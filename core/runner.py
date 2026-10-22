"""Runs the selected operations in the right order and guards the result.

Order matters: via layers must be known before per layer via sizes are read from
the gerbers.  The document is written once, after every operation has had its
say, and only if none of them reported an error.
"""
from . import hatch, ops, silk
from .pcbdoc import PcbDoc

# key, title, needs the PcbDoc
STEPS = [
    ('drill', 'Конвертация сверловки в формат Altium', False),
    ('via_layers', 'Слои переходных отверстий по парам сверловки', True),
    ('pads', 'Форма, размер и поворот площадок из Gerber', True),
    ('hatch', 'Штриховка треками в полигоны', True),
    ('holes', 'Недостающие отверстия из сверловки', True),
    ('silk', 'Шелкография: вырезы и перевод полигонов в регионы', True),
    ('via_sizes', 'Диаметры via по слоям', True),
    ('polygons', 'Удаление полигонов без сети', True),
]
STEP_TITLE = {k: t for k, t, _ in STEPS}


def run(project, selected, opts=None, apply=False, pcb_path=None, log=None):
    """-> (list of Report, PcbDoc or None).  Nothing is written unless apply."""
    opts = opts or {}
    log = log or (lambda *_: None)
    reports = []

    if 'drill' in selected:
        log('Конвертация сверловки...')
        out = opts.get('out_dir') or project.folder
        reports.append(ops.convert_drills(project, out, apply=apply))

    doc_steps = [k for k, _, needs in STEPS if needs and k in selected]
    if not doc_steps:
        return reports, None

    path = pcb_path or (project.pcbdocs[0] if project.pcbdocs else None)
    if not path:
        rep = ops.Report('PcbDoc')
        rep.error('в папке нет ASCII PcbDoc. В Altium: File - Save As - PCB ASCII')
        reports.append(rep)
        return reports, None

    log(f'Чтение {path}...')
    doc = PcbDoc(path)
    before = doc.counts()

    # привязка к сверловке и Gerber нужна только операциям, которые с ними сверяются
    holes = origin = None
    if any(k in doc_steps for k in ('via_layers', 'pads', 'via_sizes', 'holes')):
        holes = ops.HoleIndex(project)
        origin_rep = ops.Report('Привязка координат')
        origin = ops.find_origin(doc, holes, origin_rep)
        if holes.conflicts:
            origin_rep.note(f'координаты, встречающиеся в двух файлах сверловки: '
                            f'{len(holes.conflicts)}')
        reports.append(origin_rep)

    for key in doc_steps:
        log(STEP_TITLE[key] + '...')
        if key == 'via_layers':
            reports.append(ops.fix_via_layers(project, doc, holes, origin))
        elif key == 'pads':
            reports.append(ops.fix_pad_shapes(project, doc, origin, holes))
        elif key == 'hatch':
            reports.append(hatch.hatch_to_polygons(
                doc, opts.get('hatch_min_tracks', 50), opts.get('hatch_min_overlap', 1.4),
                opts.get('hatch_min_area', 20000.0), opts.get('hatch_simplify', 0.3),
                opts.get('hatch_min_void', 1000.0)))
        elif key == 'silk':
            reports.append(silk.silk_cutouts(doc, not opts.get('silk_keep_polygons', False)))
        elif key == 'holes':
            reports.append(ops.restore_holes(project, doc, origin, holes,
                                             opts.get('holes_ring', 0.0),
                                             npth_over=opts.get('holes_npth_over')))
        elif key == 'via_sizes':
            reports.append(ops.fix_via_sizes(project, doc, opts.get('zero_missing', False),
                                             origin, holes))
        elif key == 'polygons':
            reports.append(ops.remove_nonet_polygons(project, doc,
                                                     opts.get('include_silk', False)))

    # record counts: only what we deliberately delete may change
    after = doc.counts()
    allowed = {'Polygon', 'Region'} if 'polygons' in doc_steps else set()
    if 'hatch' in doc_steps:
        allowed |= {'Polygon', 'Track'}
    if 'holes' in doc_steps:
        allowed |= {'Pad'}
    if 'silk' in doc_steps:
        allowed |= {'Polygon', 'Region'}
    check = ops.Report('Проверка целостности')
    for k in sorted(set(before) | set(after)):
        if before[k] != after[k]:
            msg = f'{k}: {before[k]} -> {after[k]}'
            if k in allowed:
                check.note(msg)
            else:
                check.error('изменилось число записей ' + msg)
    if not check.errors:
        check.note('число записей всех незатронутых типов не изменилось')
    reports.append(check)

    errors = [e for r in reports for e in r.errors]
    if apply and not errors:
        log('Запись...')
        written = doc.save()
        w = ops.Report('Запись файла')
        w.files.extend(written)
        w.change('файл перезаписан')
        reports.append(w)
    elif apply and errors:
        w = ops.Report('Запись файла')
        w.error('запись отменена: есть ошибки выше')
        reports.append(w)
    return reports, doc


def summarize(reports):
    total = sum(r.total for r in reports)
    errors = [e for r in reports for e in r.errors]
    return total, errors
