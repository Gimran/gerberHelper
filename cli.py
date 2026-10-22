"""Command line front end.

  python cli.py info                      что найдено в папке
  python cli.py run --all                 анализ без записи
  python cli.py run --all --apply         с записью (перед записью делается бэкап)
  python cli.py run drill via_layers      только выбранные шаги

Папка по умолчанию - текущая; другую задать через --folder.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import runner                     # noqa: E402
from core.project import Project            # noqa: E402

KEYS = [k for k, _, _ in runner.STEPS]


def main(argv=None):
    ap = argparse.ArgumentParser(description='Allegro -> Altium: сверловка, via, пады, полигоны')
    ap.add_argument('command', choices=['info', 'run'])
    ap.add_argument('steps', nargs='*', help='шаги: ' + ', '.join(KEYS))
    ap.add_argument('--folder', default='.')
    ap.add_argument('--pcb', help='путь к ASCII PcbDoc, если в папке их несколько')
    ap.add_argument('--out', help='куда класть сконвертированную сверловку')
    ap.add_argument('--all', action='store_true', help='все шаги')
    ap.add_argument('--apply', action='store_true', help='записать изменения')
    ap.add_argument('--zero-missing', action='store_true',
                    help='обнулять размер via на слоях, где в Gerber нет площадки')
    ap.add_argument('--holes-ring', type=float, default=0.0, metavar='MIL',
                    help='поясок меди вокруг добавляемых отверстий, mil (по умолчанию 0: пад равен отверстию)')
    ap.add_argument('--holes-npth-over', type=float, default=None, metavar='MM',
                    help='добавляемые отверстия от этого диаметра и крупнее делать неметаллизированными')
    ap.add_argument('--include-silk', action='store_true',
                    help='удалять полигоны без сети и на слоях шелкографии')
    ap.add_argument('-v', '--verbose', action='store_true', help='подробности по каждому объекту')
    a = ap.parse_args(argv)

    project = Project(a.folder)
    print(project.describe())
    if a.command == 'info':
        return 0

    steps = KEYS if a.all else [s for s in a.steps if s in KEYS]
    bad = [s for s in a.steps if s not in KEYS]
    if bad:
        ap.error('неизвестные шаги: ' + ', '.join(bad))
    if not steps:
        ap.error('не выбрано ни одного шага, используйте --all или перечислите: ' + ', '.join(KEYS))

    print()
    reports, _ = runner.run(project, steps,
                            opts=dict(out_dir=a.out, zero_missing=a.zero_missing,
                                      include_silk=a.include_silk,
                                      holes_ring=a.holes_ring, holes_npth_over=a.holes_npth_over),
                            apply=a.apply, pcb_path=a.pcb, log=lambda m: print('..', m))
    print()
    for r in reports:
        print(r.full() if a.verbose else r.summary())
    total, errors = runner.summarize(reports)
    print(f'\nвсего правок: {total}')
    if errors:
        print('ОШИБКИ:')
        for e in errors:
            print('  ' + e)
        return 1
    if not a.apply:
        print('это анализ, файл не менялся. Для записи добавьте --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())
