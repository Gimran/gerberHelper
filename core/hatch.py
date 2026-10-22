"""Штриховка треками -> полигоны.

Gerber и экспорты вроде CAM350 не хранят заливку объектом: полигон превращается в
сотни перекрывающихся штрихов, и после импорта в Altium это тысячи треков вместо
одного полигона. Здесь штрихи объединяются в контур, который записывается как
Polygon той же цепи; сами штрихи удаляются. Заливку (Repour) Altium делает сам.

Штриховку от настоящих дорожек отличает перекрытие: сумма площадей штрихов заметно
больше площади их объединения (у штриховки 1.4-2.0, у дорожек около 1.0).

Дыры внутри заливки (вокруг падов и дорожек других цепей) в контур не входят:
запись Polygon хранит один внешний контур, а зазоры при Repour восстанавливаются
по правилам.
"""
import collections

from .pcbdoc import COPPER, field, fmt_mil, mil
from .report import Report

ZERO = ' 0.00000000000000E+0000'


def _layer_title(layer):
    if layer == 'TOP':
        return 'Top Layer'
    if layer == 'BOTTOM':
        return 'Bottom Layer'
    return f'Mid-Layer {layer[3:]}' if layer.startswith('MID') else layer


def _encode_name(text):
    """NAME хранится списком кодов символов через запятую."""
    return ','.join(str(ord(c)) for c in text)


def _polygon_record(index, net, layer, name, ring):
    head = ['RECORD=Polygon', f'ID={index}', f'NET={net}', f'INDEXFORSAVE={index}',
            'SELECTION=FALSE', f'LAYER={layer}', 'LOCKED=FALSE', 'POLYGONOUTLINE=FALSE',
            'USERROUTED=TRUE', 'UNIONINDEX=0', 'PRIMITIVELOCK=TRUE', 'POLYGONTYPE=Polygon',
            'POUROVER=TRUE', 'REMOVEDEAD=FALSE', 'GRIDSIZE=20mil', 'TRACKWIDTH=8mil',
            'HATCHSTYLE=Solid', 'USEOCTAGONS=FALSE', 'MINPRIMLENGTH=3mil']
    verts = []
    for n, (x, y) in enumerate(ring):
        verts += [f'KIND{n}=0', f'VX{n}={fmt_mil(x)}', f'VY{n}={fmt_mil(y)}',
                  f'CX{n}=0mil', f'CY{n}=0mil', f'SA{n}={ZERO}', f'EA{n}={ZERO}', f'R{n}=0mil']
    tail = ['SHELVED=FALSE', 'RESTORELAYER=UNKNOWN', 'RESTORENET=',
            'REMOVEISLANDSBYAREA=FALSE',            # ничего не удалять: заливка только по контуру
            'REMOVENECKS=FALSE', 'AREATHRESHOLD=10000000000.000000', 'ARCRESOLUTION=0.5mil',
            'NECKWIDTHTHRESHOLD=5mil', 'POUROVERSTYLE=1', f'NAME={_encode_name(name)}',
            'POURINDEX=-1', 'IGNOREVIOLATIONS=FALSE', 'COPPERINVALIDATE=TRUE']
    return '|' + '|'.join(head + verts + tail)


def hatch_to_polygons(doc, min_tracks=50, min_overlap=1.4, min_area=20000.0, simplify=0.3,
                      min_void=1000.0):
    """min_tracks: меньше треков в группе (слой, цепь, ширина) - не штриховка;
    min_overlap: порог отношения сумма/объединение; min_area (mil^2): куски объединения
    меньше этой площади остаются треками; simplify (mil): упрощение контура;
    min_void (mil^2): дыры в заливке больше этой площади сохраняются разрезом, меньшие
    (щели между штрихами, зазоры вокруг падов) игнорируются, Repour воссоздаст их сам."""
    rep = Report('Штриховка треками -> полигоны')
    try:
        from shapely.geometry import LineString, Point, Polygon
        from shapely.ops import split, unary_union
        from shapely.prepared import prep
    except ImportError:
        rep.error('для этой операции нужна библиотека shapely: pip install shapely')
        return rep

    def parts_without_voids(piece):
        """Polygon хранит один внешний контур, дыр не бывает. Каждую значимую дыру
        превращаем в выемку: режем кусок линией через центр дыры, пока дыр не останется."""
        todo, out = [piece], []
        while todo:
            p = todo.pop()
            voids = [h for h in p.interiors if Polygon(h).area >= min_void]
            if not voids:
                out.append(Polygon(p.exterior))
                continue
            p = Polygon(p.exterior, voids)                  # мелкие дыры отбрасываем
            c = Polygon(voids[0]).centroid
            x0, y0, x1, y1 = p.bounds
            todo += [g for g in split(p, LineString([(x0 - 1, c.y), (x1 + 1, c.y)])).geoms
                     if g.area > 0]
        return out

    net_names = {doc.get(i, 'INDEXFORSAVE'): doc.get(i, 'NAME') for i in doc.indices('Net')}

    groups = collections.defaultdict(list)
    for i in doc.indices('Track'):
        layer, net = doc.get(i, 'LAYER'), doc.get(i, 'NET')
        if layer in COPPER and net is not None:
            groups[(layer, net, doc.get(i, 'WIDTH'))].append(i)

    new_records, doomed = [], []
    used_names = collections.Counter()
    next_index = len(doc.indices('Polygon'))

    for (layer, net, width), idxs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(idxs) < min_tracks:
            continue
        w = mil(width)
        shapes, mids = [], []
        for i in idxs:
            a = (mil(doc.get(i, 'X1')), mil(doc.get(i, 'Y1')))
            b = (mil(doc.get(i, 'X2')), mil(doc.get(i, 'Y2')))
            line = LineString([a, b]) if a != b else Point(a)
            shapes.append(line.buffer(w / 2, cap_style='round', quad_segs=4))
            mids.append(Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))
        union = unary_union(shapes)
        overlap = sum(s.area for s in shapes) / union.area if union.area else 0
        label = f'{layer} цепь {net_names.get(net, net)}, штрих {width}'
        if overlap < min_overlap:
            rep.note('группа треков не похожа на штриховку (перекрытие мало), не тронута')
            rep.detail(f'   {label}: {len(idxs)} треков, перекрытие {overlap:.2f}')
            continue

        pieces = list(union.geoms) if hasattr(union, 'geoms') else [union]
        made = skipped = cuts = 0
        for piece in pieces:
            if piece.area < min_area:
                skipped += 1
                continue
            inside = prep(piece)
            own = [i for i, mid in zip(idxs, mids) if inside.contains(mid)]
            doomed += own
            voids = [h for h in piece.interiors if Polygon(h).area >= min_void]
            parts = parts_without_voids(piece)
            cuts += len(voids)
            title = f'{_layer_title(layer)}-{net_names.get(net, net)}'
            for part in parts:
                ring = list(part.simplify(simplify, preserve_topology=True).exterior.coords)
                if len(ring) < 4:
                    continue
                n = used_names[title]
                used_names[title] += 1
                name = title if n == 0 else f'{title} {n}'
                new_records.append(_polygon_record(next_index, net, layer, name, ring))
                next_index += 1
                made += 1
                rep.detail(f'   {label}: полигон {name}: {len(ring) - 1} вершин, '
                           f'{part.area:,.0f} mil²')
            rep.detail(f'   {label}: кусок {piece.area:,.0f} mil², треков {len(own)}'
                       + (f', дыр сохранено разрезом: {len(voids)} '
                          f'({sum(Polygon(h).area for h in voids):,.0f} mil²)' if voids else ''))
        if made:
            rep.change(f'{label}: {len(idxs)} треков, перекрытие {overlap:.2f} -> '
                       f'{made} полигонов' + (f' (дыр сохранено: {cuts})' if cuts else ''))
        if skipped:
            rep.note(f'куски штриховки меньше {min_area:,.0f} mil² оставлены треками', skipped)

    if not new_records:
        return rep

    # контур в файл, штрихи долой, нумерация треков без дыр
    polys = doc.indices('Polygon')
    anchor = polys[-1] if polys else doc.indices('Track')[-1]
    doomed_set = set(doomed)
    for i in doomed_set:                # удаление позиции не сдвигает, вставка сдвигает: сначала удаляем
        doc.delete(i)
    doc.insert_after(anchor, new_records)
    for new, i in enumerate(doc.indices('Track')):
        if doc.get(i, 'INDEXFORSAVE') != str(new):
            doc.set(i, 'INDEXFORSAVE', new)
    rep.change('удалено штрихов-треков', len(doomed_set))
    rep.note('после открытия в Altium выполните Tools - Polygon Pours - Repour All: '
             'полигоны записаны контуром, без залитых регионов')
    return rep
