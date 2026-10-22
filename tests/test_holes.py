"""Недостающие отверстия на синтетической плате (реальных данных нет).

    python tests/test_holes.py

Сверловка: три отверстия. На плате первое на месте, второго нет совсем, у третьего
диаметр не тот. Добавиться должен ровно один пад, остальное остаётся как было.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import ops                         # noqa: E402
from core.pcbdoc import PcbDoc, mil          # noqa: E402
from core.project import Project             # noqa: E402

DRILL = ('M48\n;FILE_FORMAT=2:5\nINCH,LZ\n;TYPE=PLATED\nT01F00S00C0.0800\n'
         ';TYPE=NON_PLATED\nT02F00S00C0.1200\n%\nT01\nX0100000Y0100000\nX0300000Y0100000\n'
         'T02\nX0200000Y0100000\nM30\n')


def pad(n, x, y, hole, net=1):
    return (f'|RECORD=Pad|NET={net}|INDEXFORSAVE={n}|SELECTION=FALSE|LAYER=MULTILAYER'
            f'|NAME=1|X={x}mil|Y={y}mil|XSIZE=100mil|YSIZE=100mil|SHAPE=ROUND|HOLESIZE={hole}mil'
            f'|ROTATION= 0.00000000000000E+0000|PLATED=TRUE|PADMODE=0|HOLEWIDTH=30mil'
            f'|UNIQUEID=AAAAAAAA')


def main():
    fails = []
    tmp = tempfile.mkdtemp(prefix='a2a_holes_')
    open(os.path.join(tmp, 'drill.TXT'), 'w', newline='\n').write(DRILL)
    recs = ['|RECORD=Board|ORIGINX=500mil|ORIGINY=-200mil',
            pad(0, 1500, 800, 80), pad(1, 3500, 800, 60)]          # со смещением origin
    pcb = os.path.join(tmp, 'synthetic.PcbDoc')
    open(pcb, 'w', newline='').write('\r'.join(recs) + '\r')

    project = Project(tmp)
    doc = PcbDoc(pcb)
    rep = ops.restore_holes(project, doc)
    if rep.errors:
        fails += rep.errors

    pads = doc.indices('Pad')
    if len(pads) != 3:
        fails.append(f'падов {len(pads)}, ожидалось 3 (добавить один)')
    else:
        new = pads[-1]
        checks = {'X': 2500, 'Y': 800, 'HOLESIZE': 120}
        for key, want in checks.items():
            if abs(mil(doc.get(new, key)) - want) > 0.01:
                fails.append(f'{key} нового пада {doc.get(new, key)}, ожидалось {want}')
        if doc.get(new, 'PLATED') != 'FALSE':
            fails.append('отверстие T02 неметаллизированное, а пад металлизированный')
        if doc.get(new, 'NET') is not None:
            fails.append('новый пад не должен иметь цепь')
        if doc.get(new, 'INDEXFORSAVE') != '2':
            fails.append('нумерация падов не продолжена')
    if mil(doc.get(pads[1], 'HOLESIZE')) != 60:
        fails.append('пад с неверным диаметром отверстия изменён')

    doc.save(pcb, backup=False)
    again = ops.restore_holes(project, PcbDoc(pcb))
    if again.total:
        fails.append(f'повторный прогон добавил {again.total}')

    print('ок     недостающие отверстия (синтетическая плата)' if not fails
          else 'ОШИБКА недостающие отверстия:')
    for f in fails:
        print('   ' + f)
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
