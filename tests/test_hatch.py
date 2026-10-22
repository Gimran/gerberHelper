"""Штриховка треками -> полигоны на синтетической плате (реальных данных нет).

    python tests/test_hatch.py

Заливка 400x300 mil из штрихов 10 mil с шагом 5 mil, посередине вырез 100x100 mil,
плюс отдельная короткая дорожка, которую трогать нельзя. Без shapely тест пропускается.
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

try:
    from shapely.geometry import Point, Polygon
    from shapely.ops import unary_union
except ImportError:
    print('пропущен: нет библиотеки shapely')
    sys.exit(0)

from core import hatch                       # noqa: E402
from core.pcbdoc import PcbDoc, field, mil   # noqa: E402


def track(n, net, layer, x1, y1, x2, y2, width):
    return (f'|RECORD=Track|NET={net}|INDEXFORSAVE={n}|SELECTION=FALSE|LAYER={layer}|LOCKED=FALSE'
            f'|X1={x1}mil|Y1={y1}mil|X2={x2}mil|Y2={y2}mil|WIDTH={width}mil|SUBPOLYINDEX=0')


def build():
    recs = ['|RECORD=Board|ORIGINX=0mil|ORIGINY=0mil',
            '|RECORD=Net|ID=0|INDEXFORSAVE=1|NAME=GND',
            '|RECORD=Polygon|ID=0|INDEXFORSAVE=0|LAYER=TOPOVERLAY|POLYGONTYPE=Polygon|NAME=']
    n = 0
    for y in range(0, 301, 5):
        spans = [(0, 100), (200, 400)] if 100 <= y <= 200 else [(0, 400)]
        for x1, x2 in spans:
            recs.append(track(n, 1, 'TOP', x1, y, x2, y, 10))
            n += 1
    for k in range(3):                       # настоящие дорожки: мало, без перекрытия
        recs.append(track(n, 1, 'TOP', 1000, 50 * k, 1200, 50 * k, 20))
        n += 1
    return recs


def main():
    fails = []
    tmp = tempfile.mkdtemp(prefix='a2a_hatch_')
    path = os.path.join(tmp, 'synthetic.PcbDoc')
    open(path, 'w', newline='').write('\r'.join(build()) + '\r')

    doc = PcbDoc(path)
    strokes = [i for i in doc.indices('Track') if doc.get(i, 'WIDTH') == '10mil']
    rep = hatch.hatch_to_polygons(doc, min_tracks=50, min_area=1000.0, min_void=1000.0)
    if rep.errors:
        fails += rep.errors

    tracks = doc.indices('Track')
    if len(tracks) != 3:
        fails.append(f'осталось треков {len(tracks)}, ожидалось 3 дорожки')
    if any(doc.get(i, 'WIDTH') != '20mil' for i in tracks):
        fails.append('удалена не штриховка')
    if [int(doc.get(i, 'INDEXFORSAVE')) for i in tracks] != [0, 1, 2]:
        fails.append('нумерация треков не сплошная')

    polys = doc.indices('Polygon')
    new = polys[1:]
    if len(new) < 2:
        fails.append(f'вырез должен разрезать заливку минимум на 2 части, получено {len(new)}')
    if [int(doc.get(i, 'INDEXFORSAVE')) for i in polys] != list(range(len(polys))):
        fails.append('нумерация полигонов не сплошная')
    if any(doc.get(i, 'NET') != '1' or doc.get(i, 'LAYER') != 'TOP' for i in new):
        fails.append('у новых полигонов не та цепь или слой')

    parts = []
    for i in new:
        pts, k = [], 0
        while doc.get(i, f'VX{k}') is not None:
            pts.append((mil(doc.get(i, f'VX{k}')), mil(doc.get(i, f'VY{k}'))))
            k += 1
        parts.append(Polygon(pts).buffer(0))
    union = unary_union(parts)
    if union.contains(Point(150, 150)):
        fails.append('вырез 100x100 заполнен: дыра потеряна')
    if not union.contains(Point(50, 50)):
        fails.append('сама заливка не покрыта полигоном')
    expected = 410 * 310 - 90 * 90           # штрихи шире на радиус, вырез у́же на радиус
    if abs(union.area - expected) / expected > 0.03:
        fails.append(f'площадь полигонов {union.area:.0f} при ожидаемых {expected}')

    # идемпотентность: второй прогон ничего не меняет
    doc.save(path, backup=False)
    again = hatch.hatch_to_polygons(PcbDoc(path), min_tracks=50, min_area=1000.0, min_void=1000.0)
    if again.total:
        fails.append(f'повторный прогон изменил {again.total}')

    print('ок     штриховка -> полигоны (синтетическая плата)' if not fails else 'ОШИБКА штриховка:')
    for f in fails:
        print('   ' + f)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
