"""The five operations, each one working on an in-memory PcbDoc.

Every operation reports what it would change and changes the document; writing
to disk is the caller's decision, so a dry run is simply "run and throw away".
"""
import collections
import os

from .gerber import GRID
import re

from .pcbdoc import (COPPER, fmt_mil, fmt_rot, kind, mil, name_of, norm_rot)
from .report import Report

EPS = 0.02          # mil, size comparison
NEAR = 2.0          # mil, how far a via may sit from its hole before we give up


# ---------------------------------------------------------------- holes
class HoleIndex:
    """All drilled holes in gerber/drill coordinates, looked up by position."""

    def __init__(self, project):
        self.by_pos = {}
        self.conflicts = []
        for d in project.drills:
            pair = project.pair_layers(d)
            for (x, y, t) in d.hits_mil():
                key = (round(x / GRID), round(y / GRID))
                if key in self.by_pos and self.by_pos[key][0] is not d:
                    self.conflicts.append((x, y))
                self.by_pos[key] = (d, t, pair, x, y)

    def at(self, x, y):
        gx, gy = round(x / GRID), round(y / GRID)
        for dx in (0, -1, 1):
            for dy in (0, -1, 1):
                hit = self.by_pos.get((gx + dx, gy + dy))
                if hit:
                    return hit, ((hit[3] - x) ** 2 + (hit[4] - y) ** 2) ** 0.5
        return None, None

    def nearest(self, x, y):
        best, bd = None, None
        for hit in self.by_pos.values():
            d = ((hit[3] - x) ** 2 + (hit[4] - y) ** 2) ** 0.5
            if bd is None or d < bd:
                best, bd = hit, d
        return best, bd

    def __len__(self):
        return len(self.by_pos)


def find_origin(doc, holes, report):
    """pcb coordinate = drill coordinate + origin. Taken from the Board record and checked.

    Probes are the first vias and the first pads that have a hole: a board without vias
    (all holes are pads) is checked just the same.
    """
    ox, oy = doc.origin()
    probes = list(doc.indices('Via')[:60])
    probes += [i for i in doc.indices('Pad') if mil(doc.get(i, 'HOLESIZE') or '0mil') > 0][:60]

    def score(off):
        n = 0
        for i in probes:
            hit, _ = holes.at(mil(doc.get(i, 'X')) - off[0], mil(doc.get(i, 'Y')) - off[1])
            n += hit is not None
        return n

    best, hits = (ox, oy), score((ox, oy))
    if probes and hits < len(probes) * 0.5:      # the Board origin is not it: vote instead
        votes = collections.Counter()
        for i in probes:
            vx, vy = mil(doc.get(i, 'X')), mil(doc.get(i, 'Y'))
            for hit in holes.by_pos.values():
                votes[(round(vx - hit[3], 2), round(vy - hit[4], 2))] += 1
        if votes:
            cand = votes.most_common(1)[0][0]
            if score(cand) > hits:
                best, hits = cand, score(cand)
                report.note(f'смещение взято подбором, а не из записи Board: {best}')
    report.note(f'смещение {best[0]}, {best[1]} mil, совпало {hits} из {len(probes)} проверенных объектов')
    return best


# ---------------------------------------------------------------- 1. drill conversion
def convert_drills(project, out_dir, apply=False):
    rep = Report('Конвертация сверловки')
    if not project.drills:
        rep.error('в папке нет файлов сверловки')
        return rep
    src = [d for d in project.drills if d.path.lower().endswith('.drl')]
    if not src:
        rep.note('файлов .drl нет, конвертировать нечего')
        return rep
    if apply:
        os.makedirs(out_dir, exist_ok=True)
    for d in src:
        dst = os.path.join(out_dir, os.path.splitext(d.name)[0] + '.TXT')
        pair = project.pair_layers(d)
        rep.detail(f'{d.name}: {pair[0] + ".." + pair[1] if pair else "пара не определена"}, '
                   f'формат {d.fmt["int"]}:{d.fmt["dec"]} '
                   f'{"INCH" if d.fmt["units"] == "ENGLISH" else "METRIC"} '
                   f'{"TZ" if d.fmt["lead_sup"] else "LZ"}')
        for t in d.order:
            qty = d.tools[t].get('qty')
            n = d.count(t)
            if qty is not None and qty != n:
                rep.error(f'{d.name}: инструмент {t}: {n} отверстий, в заголовке {qty}')
            rep.detail(f'   T{t:02d} C{d.tools[t]["dia"]} '
                       f'{"PLATED" if d.tools[t].get("plated", True) else "NON_PLATED"}: {n}')
        # every plated hole must sit on a pad on both layers of its pair
        if pair:
            plated = [(x, y) for (x, y, t) in d.hits_mil() if d.tools[t].get('plated', True)]
            for lay in pair:
                g = project.gerber(lay)
                if not g:
                    rep.note(f'нет Gerber для слоя {lay}, проверка площадок пропущена')
                    continue
                miss = [p for p in plated if not g.has_flash(*p)]
                if miss:
                    rep.note(f'{d.name}: на {lay} нет площадки под {len(miss)} отв.')
                rep.detail(f'   площадки на {lay}: {len(plated) - len(miss)}/{len(plated)}')
        rep.change(f'файл {os.path.basename(dst)}: {sum(d.count(t) for t in d.order)} отв.')
        if apply:
            d.write_altium(dst)
        rep.files.append(dst)
    return rep


# ---------------------------------------------------------------- 2. via layers
def fix_via_layers(project, doc, holes=None, origin=None):
    rep = Report('Слои переходных отверстий')
    holes = holes if holes is not None else HoleIndex(project)
    if not len(holes):
        rep.error('нет данных сверловки')
        return rep
    off = origin or find_origin(doc, holes, rep)
    for i in doc.indices('Via'):
        x, y = mil(doc.get(i, 'X')) - off[0], mil(doc.get(i, 'Y')) - off[1]
        hit, dist = holes.at(x, y)
        if hit is None:
            hit, dist = holes.nearest(x, y)
            if hit is None or dist > NEAR:
                rep.note('via без отверстия в сверловке, не тронута')
                rep.detail(f'   via ({x:.2f}, {y:.2f}): ближайшее отверстие '
                           f'{dist:.2f} mil' if dist else '')
                continue
        d, t, pair, hx, hy = hit
        if not pair:
            rep.note(f'{d.name}: пара слоёв не определена')
            continue
        if dist > GRID * 1.5:
            rep.note('via стоит не точно на отверстии')
            rep.detail(f'   via ({x:.2f}, {y:.2f}) -> {d.name}, смещение {dist:.3f} mil')
        old = (doc.get(i, 'STARTLAYER'), doc.get(i, 'ENDLAYER'))
        if old == pair:
            rep.note(f'{d.name}: слои уже верные')
            continue
        doc.set(i, 'STARTLAYER', pair[0])
        doc.set(i, 'ENDLAYER', pair[1])
        rep.change(f'{d.name}: {old[0]}-{old[1]} -> {pair[0]}-{pair[1]}')
    return rep


# ---------------------------------------------------------------- 3. pad shapes
def fix_pad_shapes(project, doc, origin=None, holes=None):
    rep = Report('Форма и поворот площадок')
    holes = holes if holes is not None else HoleIndex(project)
    off = origin or find_origin(doc, holes, rep)
    for i in doc.indices('Pad'):
        lay = doc.get(i, 'LAYER')
        if lay not in COPPER:
            rep.note(f'пад на {lay}: не одиночный медный слой, не тронут')
            continue
        g = project.gerber(lay)
        if not g:
            rep.note(f'нет Gerber для слоя {lay}')
            continue
        x, y = mil(doc.get(i, 'X')) - off[0], mil(doc.get(i, 'Y')) - off[1]
        want = g.shape_at(x, y)
        if want is None:
            rep.note('пад без флеша в Gerber, не тронут')
            continue
        if want.kind == 'COMPLEX':
            rep.note('пад со сложной апертурой (полигон), не тронут')
            rep.detail(f'   пад X={doc.get(i, "X")} Y={doc.get(i, "Y")} на {lay}')
            continue
        rot = norm_rot(want.xsize, want.ysize, want.rotation)
        cur_shape = doc.get(i, 'SHAPE')
        cur_x, cur_y = mil(doc.get(i, 'XSIZE')), mil(doc.get(i, 'YSIZE'))
        cur_rot = norm_rot(cur_x, cur_y, float(doc.get(i, 'ROTATION')))
        if (cur_shape == want.kind and abs(cur_x - want.xsize) < EPS
                and abs(cur_y - want.ysize) < EPS and abs(cur_rot - rot) < 0.01):
            rep.note('пад уже верный')
            continue
        doc.set(i, 'XSIZE', fmt_mil(want.xsize))
        doc.set(i, 'YSIZE', fmt_mil(want.ysize))
        doc.set(i, 'SHAPE', want.kind)
        doc.set(i, 'ROTATION', fmt_rot(rot))
        rep.change(f'{cur_shape} {fmt_mil(cur_x)}x{fmt_mil(cur_y)} rot {cur_rot:g}  ->  '
                   f'{want.kind} {fmt_mil(want.xsize)}x{fmt_mil(want.ysize)} rot {rot:g}')
        rep.detail(f'   пад X={doc.get(i, "X")} Y={doc.get(i, "Y")} на {lay} '
                   f'(в Gerber {x:.2f}, {y:.2f})')
    return rep


# ---------------------------------------------------------------- 4. via sizes
def size_field(project, layer):
    if layer == 'TOP':
        return 'TOPLAYERSIZE'
    if layer == 'BOTTOM':
        return 'BOTTOMLAYERSIZE'
    return f'MIDLAYER{project.layer_index(layer) - 1}SIZE'


ALL_SIZE_FIELDS = (['TOPLAYERSIZE'] + [f'MIDLAYER{i}SIZE' for i in range(1, 31)]
                   + ['BOTTOMLAYERSIZE'])


def stack_sizes(doc, i):
    """Every per layer size present in a full stack via record."""
    return [mil(v) for v in (doc.get(i, f) for f in ALL_SIZE_FIELDS) if v is not None]


def fix_via_sizes(project, doc, zero_missing=False, origin=None, holes=None):
    rep = Report('Диаметры via по слоям')
    holes = holes if holes is not None else HoleIndex(project)
    off = origin or find_origin(doc, holes, rep)
    for i in doc.indices('Via'):
        x, y = mil(doc.get(i, 'X')) - off[0], mil(doc.get(i, 'Y')) - off[1]
        s = project.layer_index(doc.get(i, 'STARTLAYER'))
        e = project.layer_index(doc.get(i, 'ENDLAYER'))
        if not s or not e:
            rep.note('via со слоями вне стека, не тронута')
            continue
        full = doc.get(i, 'PADMODE') == '2' and doc.get(i, 'TOPLAYERSIZE') is not None
        sizes = {}
        for pos in range(s, e + 1):
            lay = project.layer_name(pos)
            g = project.gerber(lay)
            sh = g.shape_at(x, y) if g else None
            if sh is None:
                sizes[lay] = None                       # pad suppressed on this layer
            elif sh.kind == 'COMPLEX' or not sh.square:
                sizes[lay] = 'odd'
            else:
                sizes[lay] = sh.xsize
        found = [v for v in sizes.values() if isinstance(v, float)]
        if not found:
            rep.note('via без круглой площадки в Gerber, не тронута')
            continue
        base = min(found)                               # a via inside a component pad picks
        for lay, v in sizes.items():                    # up that pad's flash - ignore it
            if isinstance(v, float) and v > 2 * base:
                sizes[lay] = 'odd'
        found = [v for v in sizes.values() if isinstance(v, float)]

        if full:
            for lay, v in sizes.items():
                fld = size_field(project, lay)
                cur = doc.get(i, fld)
                if cur is None:
                    rep.note(f'в записи via нет поля {fld}')
                    continue
                if v == 'odd':
                    rep.note(f'слой {lay}: площадка не круглая, не тронута')
                    continue
                if v is None:
                    rep.note('слой в пределах via без площадки в Gerber' +
                             (' -> обнулён' if zero_missing else ' -> оставлен как был'))
                    if not zero_missing:
                        continue
                    v = 0.0
                if abs(mil(cur) - v) < EPS:
                    continue
                doc.set(i, fld, fmt_mil(v))
                rep.change(f'{fld}: {cur} -> {fmt_mil(v)}')
        elif len({round(v, 3) for v in found}) > 1:
            rep.note('простая via, а в Gerber площадки разного размера по слоям '
                     '(нужен Full Stack)')

        # Altium keeps DIAMETER as the largest size of a full stack via, and as
        # the only size of a simple one, where the outer layer pad is what counts
        want = max(stack_sizes(doc, i)) if full else min(found)
        cur = mil(doc.get(i, 'DIAMETER'))
        if abs(cur - want) >= EPS:
            doc.set(i, 'DIAMETER', fmt_mil(want))
            rep.change(f'DIAMETER {fmt_mil(cur)} -> {fmt_mil(want)}')
    return rep


# ---------------------------------------------------------------- 5. polygons
def remove_nonet_polygons(project, doc, include_silk=False):
    rep = Report('Полигоны без сети')
    polys = doc.indices('Polygon')
    regions = doc.indices('Region')

    doomed = []
    for n, i in enumerate(polys):
        if doc.get(i, 'NET') is not None:
            continue
        lay = doc.get(i, 'LAYER')
        if lay not in COPPER and not include_silk:
            rep.note('полигон без сети не на медном слое, оставлен')
            rep.detail(f'   {lay}: {name_of(doc.rec(i)) or "без имени"}')
            continue
        doomed.append((n, i, lay))
    if not doomed:
        return rep
    doomed_n = {n for n, _, _ in doomed}

    children = collections.defaultdict(list)
    for i in regions:
        p = doc.get(i, 'POLYGON')
        if p is not None:
            children[int(p)].append(i)
    doomed_regions = [i for n in doomed_n for i in children[n]]

    for n, i, lay in doomed:
        rep.change(f'удалён полигон на {lay}')
        rep.detail(f'   {lay}: {name_of(doc.rec(i)) or "без имени"} '
                   f'({len(children[n])} залитых регионов)')
    if doomed_regions:
        rep.change(f'удалено залитых регионов', len(doomed_regions))

    survivors = [n for n in range(len(polys)) if n not in doomed_n]
    remap = {old: new for new, old in enumerate(survivors)}
    drop = {i for _, i, _ in doomed} | set(doomed_regions)
    for i in drop:
        doc.delete(i)

    renum = reref = 0
    for old, i in enumerate(polys):
        if i in drop:
            continue
        if old != remap[old]:
            doc.set(i, 'INDEXFORSAVE', remap[old])
            doc.set(i, 'ID', remap[old])
            renum += 1
    new_region_index = 0
    for i in regions:
        if i in drop:
            continue
        p = doc.get(i, 'POLYGON')
        if p is not None and int(p) in remap and remap[int(p)] != int(p):
            doc.set(i, 'POLYGON', remap[int(p)])
            reref += 1
        if doomed_regions and doc.get(i, 'INDEXFORSAVE') != str(new_region_index):
            doc.set(i, 'INDEXFORSAVE', new_region_index)
        new_region_index += 1
    rep.note(f'перенумеровано полигонов: {renum}, ссылок Region -> Polygon: {reref}')

    # nothing may point at a polygon that is gone
    left = {int(doc.get(i, 'INDEXFORSAVE')) for i in doc.indices('Polygon')}
    bad = [int(doc.get(i, 'POLYGON')) for i in doc.indices('Region')
           if doc.get(i, 'POLYGON') is not None and int(doc.get(i, 'POLYGON')) not in left]
    if bad:
        rep.error(f'висячие ссылки на удалённые полигоны: {sorted(set(bad))[:10]}')
    return rep


# ---------------------------------------------------------------- 6. missing holes
def _default_pad():
    """Запись Pad для случая, когда в файле нет подходящего образца."""
    f = ['RECORD=Pad', 'INDEXFORSAVE=0', 'SELECTION=FALSE', 'LAYER=MULTILAYER', 'LOCKED=FALSE',
         'POLYGONOUTLINE=FALSE', 'USERROUTED=TRUE', 'UNIONINDEX=0',
         'SOLDERMASKEXPANSIONMODE=Manual', 'SOLDERMASKEXPANSION_MANUAL=0mil',
         'PASTEMASKEXPANSIONMODE=Rule', 'NAME=1', 'X=0mil', 'Y=0mil', 'XSIZE=0mil', 'YSIZE=0mil',
         'SHAPE=ROUND', 'HOLESIZE=0mil', 'ROTATION= 0.00000000000000E+0000', 'PLATED=TRUE',
         'DAISYCHAIN=Load', 'CCSV=0', 'CPLV=0', 'CCWV=0', 'CENV=0', 'CAGV=0', 'CPEV=1', 'CSEV=2',
         'CPCV=1', 'CPRV=0', 'CPE=0mil', 'CSE=3.9499mil', 'CUSE=TRUE', 'CSB=23.1498mil',
         'CPC=20mil', 'PADMODE=0', 'SWAPID_PAD=', 'SWAPID_GATE=', '&|0', 'SWAPPEDPADNAME=',
         'GATEID=0', 'OVERRIDEWITHV6_6SHAPES=FALSE', 'DRILLTYPE=0', 'HOLETYPE=0',
         'HOLEWIDTH=0mil', 'HOLEROTATION= 0.00000000000000E+0000']
    for n in range(32):
        f += [f'PADXOFFSET{n}=0mil', f'PADYOFFSET{n}=0mil']
    f += ['PADJUMPERID=0', 'UNIQUEID=AAAAAAAA']
    return '|' + '|'.join(f)


def restore_holes(project, doc, origin=None, holes=None, ring=0.0, tol=3.0, npth_over=None):
    """Отверстие есть в сверловке, а на плате под ним нет ни пада, ни via: добавляем Pad.

    ring - ширина медного пояска вокруг отверстия, mil; 0 даёт пад размером с отверстие.
    Металлизация берётся из файла сверловки, но отверстия диаметром от npth_over мм и
    крупнее (крепёж) делаются неметаллизированными. Pad создаётся без цепи.
    """
    import random
    from .pcbdoc import set_field
    rep = Report('Недостающие отверстия')
    holes = holes if holes is not None else HoleIndex(project)
    if not len(holes):
        rep.error('нет данных сверловки')
        return rep
    off = origin or find_origin(doc, holes, rep)

    grid = collections.defaultdict(list)
    for kind_name in ('Pad', 'Via'):
        for i in doc.indices(kind_name):
            x, y = mil(doc.get(i, 'X')), mil(doc.get(i, 'Y'))
            grid[(round(x / tol), round(y / tol))].append(
                (x, y, mil(doc.get(i, 'HOLESIZE') or '0mil')))

    def objects_at(x, y):
        gx, gy = round(x / tol), round(y / tol)
        return [o for dx in (-1, 0, 1) for dy in (-1, 0, 1) for o in grid.get((gx + dx, gy + dy), ())
                if abs(o[0] - x) <= tol and abs(o[1] - y) <= tol]

    missing = []
    for d, t, _, hx, hy in holes.by_pos.values():
        x, y = hx + off[0], hy + off[1]
        found = objects_at(x, y)
        dia = d.dia_mil(t)
        if not found:
            plated = d.tools[t].get('plated', True)
            if npth_over and dia * 0.0254 >= npth_over:
                plated = False
            missing.append((x, y, dia, plated, t))
            continue
        drilled = max(o[2] for o in found)
        if drilled <= 0:
            rep.note('на месте отверстия стоит объект без отверстия, не тронут')
            rep.detail(f'   X={fmt_mil(x)} Y={fmt_mil(y)}: в сверловке {dia * 0.0254:.3f} мм')
        elif abs(drilled - dia) > 1.0:
            rep.note('диаметр отверстия на плате не совпадает со сверловкой, не тронут')
            rep.detail(f'   X={fmt_mil(x)} Y={fmt_mil(y)}: на плате {drilled * 0.0254:.3f} мм, '
                       f'в сверловке {dia * 0.0254:.3f} мм')
        else:
            rep.note('отверстие на месте')
    if not missing:
        return rep

    pads = doc.indices('Pad')
    template = next((doc.rec(i) for i in pads
                     if doc.get(i, 'LAYER') == 'MULTILAYER' and doc.get(i, 'PADMODE') == '0'
                     and doc.get(i, 'SHAPE') == 'ROUND'
                     and mil(doc.get(i, 'HOLESIZE') or '0mil') > 0), None) or _default_pad()
    template = re.sub(r'\|NET=[^|]*', '', template, count=1)           # новый пад без цепи
    used = {doc.get(i, 'UNIQUEID') for i in pads}
    rnd = random.Random(len(pads))
    letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    records, per_tool = [], collections.Counter()
    for n, (x, y, dia, plated, t) in enumerate(sorted(missing, key=lambda m: (m[4], m[0], m[1]))):
        uid = ''.join(rnd.choice(letters) for _ in range(8))
        while uid in used:
            uid = ''.join(rnd.choice(letters) for _ in range(8))
        used.add(uid)
        rec = template
        for key, value in (('INDEXFORSAVE', len(pads) + n), ('X', fmt_mil(x)), ('Y', fmt_mil(y)),
                           ('XSIZE', fmt_mil(dia + 2 * ring)), ('YSIZE', fmt_mil(dia + 2 * ring)),
                           ('SHAPE', 'ROUND'), ('HOLESIZE', fmt_mil(dia)), ('HOLEWIDTH', fmt_mil(dia)),
                           ('PLATED', 'TRUE' if plated else 'FALSE'), ('NAME', f'H{n + 1}'),
                           ('UNIQUEID', uid), ('ROTATION', fmt_rot(0.0))):
            rec = set_field(rec, key, value)
        records.append(rec)
        per_tool[(t, round(dia * 0.0254, 4), plated)] += 1
        rep.detail(f'   добавлен пад H{n + 1}: X={fmt_mil(x)} Y={fmt_mil(y)}, отверстие '
                   f'{dia * 0.0254:.4f} мм, {"металлизированное" if plated else "неметаллизированное"}')

    anchor = pads[-1] if pads else doc.rec_idx[-1]
    doc.insert_after(anchor, records)
    for (t, mm, plated), n in sorted(per_tool.items()):
        rep.change(f'T{t:02d} {mm:g} мм, {"металлизированное" if plated else "неметаллизированное"}: '
                   f'добавлен пад', n)
    rep.note('пады добавлены без цепи и без контактной площадки сверх отверстия '
             '(размер пада равен отверстию)')
    return rep
