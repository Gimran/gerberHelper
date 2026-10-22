"""Шелкография: вырезы в полигонах и перевод полигонов в регионы.

Если в исходных данных шелкография нарисована полигонами с вырезами (буквы «О», «А»,
«Р»), то CAMtastic импортирует вырез отдельным залитым полигоном поверх основного, и
отверстие в букве закрашено. Вырез - это полигон, лежащий целиком внутри другого
полигона того же слоя (на нечётной глубине вложенности).

Запись Polygon хранит один внешний контур, зато Region умеет внутренние контуры
(HOLECOUNT), а главное, Region не пересчитывается при Repour, и сотни полигонов
шелкографии не тормозят перезаливку. Поэтому каждый полигон шелкографии
переписывается в Region (внешние контуры с вырезами, остальные без), а полигоны-вырезы
удаляются. Внутренние контуры записываются в 1/10000 mil, как у Region от заливки
(проверено в Altium).
"""
import collections

from .pcbdoc import fmt_mil, name_of
from .report import Report

OVERLAY = ('TOPOVERLAY', 'BOTTOMOVERLAY')
ZERO = ' 0.00000000000000E+0000'


def _ring_points(doc, i):
    pts, n = [], 0
    while doc.get(i, f'VX{n}') is not None:
        pts.append((float(doc.get(i, f'VX{n}')[:-3]), float(doc.get(i, f'VY{n}')[:-3])))
        n += 1
    return pts


def _region_record(index, layer, outer, holes):
    f = ['RECORD=Region', f'INDEXFORSAVE={index}', 'SELECTION=FALSE', f'LAYER={layer}',
         'LOCKED=FALSE', 'POLYGONOUTLINE=FALSE', 'USERROUTED=TRUE', 'UNIONINDEX=0',
         'SOLDERMASKEXPANSIONMODE=None', 'PASTEMASKEXPANSIONMODE=None', 'NAME= ', 'KIND=0',
         'SUBPOLYINDEX=-1', 'ARCRESOLUTION=0.5mil', 'ISSHAPEBASED=TRUE',
         f'MAINCONTOURVERTEXCOUNT={len(outer)}']
    for n, (x, y) in enumerate(outer):
        f += [f'KIND{n}=0', f'VX{n}={fmt_mil(x)}', f'VY{n}={fmt_mil(y)}', f'CX{n}=0mil',
              f'CY{n}=0mil', f'SA{n}={ZERO}', f'EA{n}={ZERO}', f'R{n}=0mil']
    f.append(f'HOLECOUNT={len(holes)}')
    for k, ring in enumerate(holes):
        f.append(f'HOLE{k}VERTEXCOUNT={len(ring)}')
        for n, (x, y) in enumerate(ring):
            f += [f'H{k}VX{n}={x * 10000:.6f}', f'H{k}VY{n}={y * 10000:.6f}']
    return '|' + '|'.join(f)


def silk_cutouts(doc, to_regions=True):
    """to_regions: все полигоны шелкографии становятся регионами; иначе в регионы
    переписываются только полигоны с вырезами, остальные остаются полигонами, и у них
    снимается «Remove Islands Less Than» (REMOVEISLANDSBYAREA), чтобы мелкие элементы
    букв не пропадали."""
    rep = Report('Шелкография: вырезы и регионы')
    try:
        from shapely.geometry import Polygon
        from shapely.strtree import STRtree
    except ImportError:
        rep.error('для этой операции нужна библиотека shapely: pip install shapely')
        return rep

    polys = doc.indices('Polygon')
    by_layer = collections.defaultdict(list)           # слой -> [(позиция в файле, индекс записи, shapely)]
    for n, i in enumerate(polys):
        layer = doc.get(i, 'LAYER')
        if layer in OVERLAY:
            pts = _ring_points(doc, i)
            if len(pts) >= 3:
                g = Polygon(pts).buffer(0)
                if not g.is_empty and g.geom_type == 'Polygon':
                    by_layer[layer].append((n, i, g))

    plans = []                                         # (слой, внешний контур, [контуры дыр])
    doomed = set()
    total_holes = 0
    for layer, items in by_layer.items():
        geoms = [g for _, _, g in items]
        tree = STRtree(geoms)
        parent = {}
        for a, (_, _, g) in enumerate(items):
            best = None
            for b in tree.query(g):
                b = int(b)
                if b != a and geoms[b].area > g.area and geoms[b].contains(g):
                    if best is None or geoms[b].area < geoms[best].area:
                        best = b
            parent[a] = best

        def depth(a):
            d = 0
            while parent[a] is not None:
                a, d = parent[a], d + 1
            return d

        level = {a: depth(a) for a in range(len(items))}
        holes_of = collections.defaultdict(list)
        for a in range(len(items)):
            if level[a] % 2 == 1:
                holes_of[parent[a]].append(a)
                doomed.add(items[a][0])                # вырез как отдельный объект исчезает
        for a in range(len(items)):
            if level[a] % 2 == 1:
                continue
            hs = holes_of.get(a, [])
            if not hs and not to_regions:
                continue
            plans.append((layer, list(items[a][2].exterior.coords)[:-1],
                          [list(items[h][2].exterior.coords)[:-1] for h in hs]))
            doomed.add(items[a][0])
            total_holes += len(hs)

    kept = [i for n, i in enumerate(polys)
            if n not in doomed and doc.get(i, 'LAYER') in OVERLAY]
    flagged = 0
    for i in kept:                                     # остались полигонами: мелкие острова не удалять
        if doc.get(i, 'REMOVEISLANDSBYAREA') == 'TRUE':
            doc.set(i, 'REMOVEISLANDSBYAREA', 'FALSE')
            flagged += 1
    if flagged:
        rep.change('у полигонов шелкографии снят «Remove Islands Less Than»', flagged)

    if not plans:
        rep.note('полигонов шелкографии для перевода в регионы нет')
        return rep

    result = doc.delete_polygons(doomed)
    if result['dangling']:
        rep.error(f'висячие ссылки на удалённые полигоны: {result["dangling"][:10]}')
        return rep

    regions = doc.indices('Region')
    records = [_region_record(len(regions) + k, layer, outer, holes)
               for k, (layer, outer, holes) in enumerate(plans)]
    live = doc.indices('Polygon') or [i for i in doc.rec_idx if doc.tok[i] is not None]
    anchor = regions[-1] if regions else live[-1]
    doc.insert_after(anchor, records)

    with_holes = sum(1 for _, _, h in plans if h)
    rep.change(f'полигонов шелкографии переписано в Region: {len(plans)}, из них с вырезами: '
               f'{with_holes} (вырезов {total_holes})')
    rep.change('удалено полигонов (внешних и вырезов)', len(doomed))
    if result['regions']:
        rep.change('удалено залитых регионов этих полигонов', result['regions'])
    return rep
