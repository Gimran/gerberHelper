"""Регрессия: прогон на эталонных данных и побайтовое сравнение.

    python tests/regression.py

Случаи описаны в tests/cases.json:
  {"name": ..., "type": "drill",  "input": <папка с .drl>, "expected": <папка с .TXT>}
  {"name": ..., "type": "pcbdoc", "input": <папка>, "pcb": <файл>, "steps": [...],
   "expected": <файл PcbDoc>}
Пути относительно файла cases.json. Случай с отсутствующими файлами пропускается,
чтобы тест можно было держать и там, где больших плат под рукой нет.
"""
import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from core import runner                     # noqa: E402
from core.project import Project            # noqa: E402


def same(a, b):
    return open(a, 'rb').read() == open(b, 'rb').read()


def copy_files(src, dst):
    for f in os.listdir(src):
        path = os.path.join(src, f)
        if os.path.isfile(path):                    # подпапка expected не копируется
            shutil.copy2(path, dst)


def run_drill(case, work):
    copy_files(case['input_abs'], work)
    project = Project(work)
    out = os.path.join(work, 'out')
    runner.run(project, ['drill'], opts=dict(out_dir=out), apply=True)
    fails = []
    for name in sorted(os.listdir(case['expected_abs'])):
        got, want = os.path.join(out, name), os.path.join(case['expected_abs'], name)
        if not os.path.exists(got):
            fails.append(f'{name}: не создан')
        elif not same(got, want):
            fails.append(f'{name}: отличается от эталона')
    return fails


def run_pcbdoc(case, work):
    copy_files(case['input_abs'], work)
    project = Project(work)
    pcb = os.path.join(work, case['pcb'])
    runner.run(project, case['steps'], opts=case.get('opts', {}), apply=True, pcb_path=pcb)
    return [] if same(pcb, case['expected_abs']) else ['PcbDoc отличается от эталона']


def main():
    cases = json.load(open(os.path.join(HERE, 'cases.json'), encoding='utf-8'))
    bad = skipped = 0
    unpacked = []
    for case in cases:
        case['input_abs'] = os.path.join(HERE, case['input'])
        case['expected_abs'] = os.path.join(HERE, case['expected'])
        archive = case['input_abs'] + '.zip'         # большие платы лежат упакованными
        if not os.path.exists(case['input_abs']) and os.path.exists(archive):
            rel = os.path.relpath(case['expected_abs'], case['input_abs'])
            tmp = tempfile.mkdtemp(prefix='a2a_fx_')
            unpacked.append(tmp)
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
            case['input_abs'] = tmp
            case['expected_abs'] = os.path.join(tmp, rel)
        if not os.path.exists(case['input_abs']) or not os.path.exists(case['expected_abs']):
            print(f'- {case["name"]}: пропущен, нет данных ({case["input"]})')
            skipped += 1
            continue
        work = tempfile.mkdtemp(prefix='a2a_')
        try:
            fails = run_drill(case, work) if case['type'] == 'drill' else run_pcbdoc(case, work)
        except Exception as e:                          # noqa: BLE001 - it is a test runner
            fails = [f'{type(e).__name__}: {e}']
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if fails:
            bad += 1
            print(f'ОШИБКА {case["name"]}:')
            for f in fails:
                print('   ' + f)
        else:
            print(f'ок     {case["name"]}')
    print(f'\nпровалено: {bad}, пропущено: {skipped}, всего: {len(cases)}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
