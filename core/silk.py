"""Вырезы в полигонах шелкографии.

Если в исходных данных шелкография нарисована полигонами с вырезами (буквы «О», «А»,
«Р»), то CAMtastic импортирует вырез отдельным залитым полигоном поверх основного, и
отверстие в букве закрашено. Вырез - это полигон, лежащий целиком внутри другого
полигона того же слоя (на нечётной глубине вложенности).

Запись Polygon хранит один внешний контур, зато Region умеет внутренние контуры
(HOLECOUNT). Поэтому внешний полигон с вырезами переписывается в Region, а
полигоны-вырезы удаляются. Полигоны без вырезов остаются как были.

Формат внутренних контуров вручную нарисованного Region в файлах, которые я видел,
не встречался: у Region от заливки они в единицах 1/10000 mil, а контур Region
фигуры (ISSHAPEBASED) записан в mil. Поэтому единицы дыр выбираются параметром:
hole_format 'internal' (1/10000 mil, как у заливки) или 'mil'.
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


def _region_record(index, layer, outer, holes, hole_format):
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
            if hole_format == 'mil':
                f += [f'H{k}VX{n}={fmt_mil(x)}', f'H{k}VY{n}={fmt_mil(y)}']
            else:
                f += [f'H{k}VX{n}={x * 10000:.6f}', f'H{k}VY{n}={y * 10000:.6f}']
    return '|' + '|'.join(f)


def silk_cutouts(doc, hole_format='internal'):
    rep = Report('Вырезы в полигонах шелкографии')
    try:
        from shapely.geometry import Polygon
        from shapely.strtree import STRtree
    except ImportError:
        rep.error('для этой операции нужна библиотека shapely: pip install shapely')
        return rep
    if hole_format not in ('internal', 'mil'):
        rep.error(f'неизвестный формат дыр: {hole_format}')
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

        holes_of = collections.defaultdict(list)
        for a in range(len(items)):
            if parent[a] is not None and depth(a) % 2 == 1:
                holes_of[parent[a]].append(a)
        for a, hs in holes_of.items():
            outer_item = items[a]
            plans.append((layer, list(outer_item[2].exterior.coords)[:-1],
                          [list(items[h][2].exterior.coords)[:-1] for h in hs]))
            doomed.add(outer_item[0])
            doomed.update(items[h][0] for h in hs)
            rep.detail(f'   {layer}: {name_of(doc.rec(outer_item[1])) or "полигон"} '
                       f'{outer_item[2].area:,.0f} mil², вырезов {len(hs)}')

    if not plans:
        rep.note('вложенных полигонов шелкографии нет')
        return rep

    result = doc.delete_polygons(doomed)
    if result['dangling']:
        rep.error(f'висячие ссылки на удалённые полигоны: {result["dangling"][:10]}')
        return rep

    regions = doc.indices('Region')
    records = [_region_record(len(regions) + k, layer, outer, holes, hole_format)
               for k, (layer, outer, holes) in enumerate(plans)]
    anchor = regions[-1] if regions else doc.indices('Polygon')[-1]
    doc.insert_after(anchor, records)

    total_holes = sum(len(h) for _, _, h in plans)
    rep.change(f'полигонов с вырезами переписано в Region: {len(plans)}, вырезов: {total_holes}')
    rep.change('удалено полигонов (внешних и вырезов)', len(doomed))
    if result['regions']:
        rep.change('удалено залитых регионов этих полигонов', result['regions'])
    rep.note(f'единицы внутренних контуров: '
             f'{"1/10000 mil, как у заливки" if hole_format == "internal" else "mil"}; '
             f'если Altium откроет буквы без вырезов, сохраните вариант с другим форматом')
    return rep
