"""Шелкография: вырезы и перевод полигонов в регионы на синтетической плате.

    python tests/test_silk.py

Полигон 100x100 с вырезом 20x20, внутри выреза островок 4x4 (он не вырез, а отдельный
объект), рядом самостоятельный полигон без вырезов. Без shapely тест пропускается.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

try:
    import shapely                           # noqa: F401
except ImportError:
    print('пропущен: нет библиотеки shapely')
    sys.exit(0)

from core import silk                        # noqa: E402
from core.pcbdoc import PcbDoc               # noqa: E402


def polygon(n, layer, x0, y0, x1, y1):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    verts = ''.join(f'|VX{k}={x}mil|VY{k}={y}mil' for k, (x, y) in enumerate(pts))
    return (f'|RECORD=Polygon|ID={n}|INDEXFORSAVE={n}|LAYER={layer}|POLYGONTYPE=Polygon{verts}'
            f'|REMOVEISLANDSBYAREA=TRUE|NAME=')


RECS = ['|RECORD=Board|ORIGINX=0mil|ORIGINY=0mil',
        polygon(0, 'TOPOVERLAY', 0, 0, 100, 100),          # внешний
        polygon(1, 'TOPOVERLAY', 40, 40, 60, 60),          # вырез
        polygon(2, 'TOPOVERLAY', 48, 48, 52, 52),          # островок внутри выреза
        polygon(3, 'TOPOVERLAY', 200, 0, 250, 50)]         # самостоятельный


def run(path, to_regions):
    open(path, 'w', newline='').write('\r'.join(RECS) + '\r')
    doc = PcbDoc(path)
    rep = silk.silk_cutouts(doc, to_regions)
    return doc, rep


def main():
    fails = []
    tmp = tempfile.mkdtemp(prefix='a2a_silk_')
    path = os.path.join(tmp, 'synthetic.PcbDoc')

    # все полигоны шелкографии становятся регионами
    doc, rep = run(path, True)
    fails += rep.errors
    if doc.indices('Polygon'):
        fails.append(f'остались полигоны: {len(doc.indices("Polygon"))}')
    regions = doc.indices('Region')
    if len(regions) != 3:
        fails.append(f'регионов {len(regions)}, ожидалось 3')
    holed = [i for i in regions if doc.get(i, 'HOLECOUNT') == '1']
    if len(holed) != 1:
        fails.append('ожидался ровно один регион с вырезом')
    else:
        r = holed[0]
        if doc.get(r, 'MAINCONTOURVERTEXCOUNT') != '4' or doc.get(r, 'H0VX0') != '400000.000000':
            fails.append(f'контур региона с вырезом не тот: H0VX0={doc.get(r, "H0VX0")}')
    if [int(doc.get(i, 'INDEXFORSAVE')) for i in regions] != [0, 1, 2]:
        fails.append('нумерация регионов не сплошная')
    doc.save(path, backup=False)
    if silk.silk_cutouts(PcbDoc(path), True).total:
        fails.append('повторный прогон (регионы) что-то изменил')

    # только полигоны с вырезами, остальные остаются полигонами без удаления островов
    doc, rep = run(path, False)
    fails += rep.errors
    polys = doc.indices('Polygon')
    if len(polys) != 2 or len(doc.indices('Region')) != 1:
        fails.append(f'[без перевода] полигонов {len(polys)}, регионов {len(doc.indices("Region"))}')
    if any(doc.get(i, 'REMOVEISLANDSBYAREA') != 'FALSE' for i in polys):
        fails.append('у оставшихся полигонов не снят «Remove Islands Less Than»')
    if [int(doc.get(i, 'INDEXFORSAVE')) for i in polys] != [0, 1]:
        fails.append('нумерация полигонов не сплошная')

    print('ок     шелкография: вырезы и регионы (синтетическая плата)' if not fails
          else 'ОШИБКА шелкография:')
    for f in fails:
        print('   ' + f)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
