"""Вырезы в полигонах шелкографии на синтетической плате (реальных данных нет).

    python tests/test_silk.py

Полигон 100x100 с вырезом 20x20, внутри выреза островок 4x4 (он не вырез, а отдельный
полигон), рядом самостоятельный полигон без вырезов. Без shapely тест пропускается.
"""
import os
import re
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
    return f'|RECORD=Polygon|ID={n}|INDEXFORSAVE={n}|LAYER={layer}|POLYGONTYPE=Polygon{verts}|NAME='


def main():
    fails = []
    tmp = tempfile.mkdtemp(prefix='a2a_silk_')
    path = os.path.join(tmp, 'synthetic.PcbDoc')
    recs = ['|RECORD=Board|ORIGINX=0mil|ORIGINY=0mil',
            polygon(0, 'TOPOVERLAY', 0, 0, 100, 100),      # внешний
            polygon(1, 'TOPOVERLAY', 40, 40, 60, 60),      # вырез
            polygon(2, 'TOPOVERLAY', 48, 48, 52, 52),      # островок внутри выреза
            polygon(3, 'TOPOVERLAY', 200, 0, 250, 50)]     # самостоятельный
    open(path, 'w', newline='').write('\r'.join(recs) + '\r')

    for fmt in ('internal', 'mil'):
        open(path, 'w', newline='').write('\r'.join(recs) + '\r')
        doc = PcbDoc(path)
        rep = silk.silk_cutouts(doc, fmt)
        if rep.errors:
            fails += rep.errors
        polys = doc.indices('Polygon')
        if len(polys) != 2:
            fails.append(f'[{fmt}] полигонов {len(polys)}, ожидалось 2 (островок и самостоятельный)')
        if [int(doc.get(i, 'INDEXFORSAVE')) for i in polys] != [0, 1]:
            fails.append(f'[{fmt}] нумерация полигонов не сплошная')
        regions = doc.indices('Region')
        if len(regions) != 1:
            fails.append(f'[{fmt}] регионов {len(regions)}, ожидался 1')
        else:
            r = regions[0]
            if doc.get(r, 'HOLECOUNT') != '1' or doc.get(r, 'MAINCONTOURVERTEXCOUNT') != '4':
                fails.append(f'[{fmt}] у региона HOLECOUNT/контур не те')
            want = '400000.000000' if fmt == 'internal' else '40mil'
            if doc.get(r, 'H0VX0') != want:
                fails.append(f'[{fmt}] H0VX0={doc.get(r, "H0VX0")}, ожидалось {want}')
            if doc.get(r, 'LAYER') != 'TOPOVERLAY':
                fails.append(f'[{fmt}] слой региона {doc.get(r, "LAYER")}')
        doc.save(path, backup=False)
        again = silk.silk_cutouts(PcbDoc(path), fmt)
        if again.total:
            fails.append(f'[{fmt}] повторный прогон изменил {again.total}')

    print('ок     вырезы в шелкографии (синтетическая плата)' if not fails
          else 'ОШИБКА вырезы в шелкографии:')
    for f in fails:
        print('   ' + f)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
