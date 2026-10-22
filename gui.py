"""Allegro -> Altium helper, окно.

Две половины работы разведены по вкладкам, как они идут в жизни:
  «Сверловка» - подготовка данных до импорта в CAMtastic;
  «Плата»     - лечение кривого экспорта уже после импорта, в ASCII PcbDoc;
  «Слои»      - раскладка Gerber по стеку, автоматическая и вручную.

Запуск: Allegro-Altium.pyw, allegro2altium.bat или python gui.py
"""
import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import runner                          # noqa: E402
from core.layers import DEFAULT_RULES            # noqa: E402
from core.project import NOT_COPPER, Project     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = os.path.join(HERE, 'settings.json')
MONO = ('Consolas', 12)
SMALL = ('', 11)

PCB_STEPS = [
    ('via_layers', 'Растащить via по слоям, по парам сверловки', None),
    ('pads', 'Форма, размер и поворот площадок из Gerber', None),
    ('hatch', 'Штриховку из треков превратить в полигоны (нужна shapely)', None),
    ('holes', 'Добавить отверстия из сверловки, которых нет на плате', ('text:holes_npth_over',
                                                                        'неметаллизир. от, мм:')),
    ('via_sizes', 'Диаметры via по слоям',
     ('zero_missing', 'обнулять на слоях, где в Gerber нет площадки')),
    ('polygons', 'Удалить полигоны без сети',
     ('include_silk', 'включая слои шелкографии')),
]
DEFAULT_ON = {'via_layers': True, 'pads': True, 'hatch': False, 'holes': False, 'via_sizes': False,
              'polygons': True}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('Allegro → Altium')
        self.geometry('1120x800')
        self.minsize(940, 620)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=4)      # вкладки
        self.grid_rowconfigure(2, weight=2)      # отчёт

        self.project = None
        self.queue = queue.Queue()
        self.busy = False
        self.reports = []
        self.layer_widgets = []
        self._filling = False
        self.settings = self._load_settings()

        self._build_folder_row()
        self._build_tabs()
        self._build_report()
        self._build_status()

        self.after(100, self._drain)
        folder = self.settings.get('folder')
        if folder and os.path.isdir(folder):
            self.folder_var.set(folder)
            self.scan()

    # ------------------------------------------------------------ верх
    def _build_folder_row(self):
        row = ctk.CTkFrame(self)
        row.grid(row=0, column=0, sticky='ew', padx=12, pady=(12, 6))
        row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text='Папка проекта').grid(row=0, column=0, padx=(12, 8), pady=10)
        self.folder_var = tk.StringVar()
        ctk.CTkEntry(row, textvariable=self.folder_var).grid(row=0, column=1, sticky='ew', pady=10)
        ctk.CTkButton(row, text='Обзор', width=90, command=self.pick_folder)\
            .grid(row=0, column=2, padx=8)
        ctk.CTkButton(row, text='Обновить', width=100, command=self.scan)\
            .grid(row=0, column=3, padx=(0, 12))

    def _build_tabs(self):
        self.tabs = ctk.CTkTabview(self, anchor='w')
        self.tabs.grid(row=1, column=0, sticky='nsew', padx=12, pady=6)
        for name in ('1. Сверловка', '2. Плата', 'Слои'):
            self.tabs.add(name)
            self.tabs.tab(name).grid_columnconfigure(0, weight=1)
        self._build_tab_drill(self.tabs.tab('1. Сверловка'))
        self._build_tab_pcb(self.tabs.tab('2. Плата'))
        self._build_tab_layers(self.tabs.tab('Слои'))

    # ------------------------------------------------------------ вкладка 1
    def _build_tab_drill(self, tab):
        tab.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(tab, text='Сверловка Cadence (.drl) → Excellon, который понимает Altium.\n'
                               'Единицы и координаты не пересчитываются, повторы R разворачиваются, '
                               'инструменты разделяются на металлизированные и нет.',
                     justify='left', anchor='w', font=SMALL, text_color='gray')\
            .grid(row=0, column=0, sticky='ew', padx=12, pady=(10, 6))
        self.drill_box = ctk.CTkTextbox(tab, font=MONO, height=150)
        self.drill_box.grid(row=1, column=0, sticky='nsew', padx=12)
        self.drill_box.configure(state='disabled')

        row = ctk.CTkFrame(tab, fg_color='transparent')
        row.grid(row=2, column=0, sticky='ew', padx=12, pady=10)
        ctk.CTkLabel(row, text='Класть в', font=SMALL).pack(side='left')
        self.out_var = tk.StringVar(value=self.settings.get('out_dir', 'drill_altium'))
        ctk.CTkEntry(row, textvariable=self.out_var, width=240).pack(side='left', padx=8)
        ctk.CTkLabel(row, text='подпапка проекта или полный путь', font=SMALL,
                     text_color='gray').pack(side='left')
        self.btn_convert = ctk.CTkButton(row, text='Конвертировать', width=160,
                                         command=self.convert)
        self.btn_convert.pack(side='right')

        ctk.CTkLabel(tab, text='Дальше: добавить .TXT в CAMtastic, расставить слои и пары '
                               'сверловки, извлечь нетлист, экспортировать в PCB.',
                     anchor='w', font=SMALL, text_color='gray')\
            .grid(row=3, column=0, sticky='ew', padx=12, pady=(0, 10))

    # ------------------------------------------------------------ вкладка 2
    def _build_tab_pcb(self, tab):
        ctk.CTkLabel(tab, text='Лечение экспорта из CAMtastic. Файл платы должен быть текстовым: '
                               'в Altium File → Save As → PCB ASCII.',
                     anchor='w', font=SMALL, text_color='gray')\
            .grid(row=0, column=0, sticky='ew', padx=12, pady=(10, 6))
        row = ctk.CTkFrame(tab, fg_color='transparent')
        row.grid(row=1, column=0, sticky='ew', padx=12)
        ctk.CTkLabel(row, text='Файл платы').pack(side='left', padx=(0, 8))
        self.pcb_var = tk.StringVar(value='—')
        self.pcb_menu = ctk.CTkOptionMenu(row, variable=self.pcb_var, values=['—'], width=520)
        self.pcb_menu.pack(side='left')

        self.step_vars, self.opt_vars = {}, {}
        for i, (key, title, opt) in enumerate(PCB_STEPS, start=2):
            line = ctk.CTkFrame(tab, fg_color='transparent')
            line.grid(row=i, column=0, sticky='ew', padx=12, pady=2)
            var = tk.BooleanVar(value=self.settings.get('steps', {})
                                .get(key, DEFAULT_ON.get(key, True)))
            self.step_vars[key] = var
            ctk.CTkCheckBox(line, text=title, variable=var).pack(side='left')
            if opt and opt[0].startswith('text:'):          # числовое поле вместо галочки
                oname, otext = opt[0][5:], opt[1]
                tvar = tk.StringVar(value=str(self.settings.get('opts', {}).get(oname, '3')))
                self.opt_vars[oname] = tvar
                ctk.CTkLabel(line, text=otext, font=SMALL).pack(side='left', padx=(24, 6))
                ctk.CTkEntry(line, textvariable=tvar, width=50, height=24).pack(side='left')
            elif opt:
                oname, otext = opt
                ovar = tk.BooleanVar(value=self.settings.get('opts', {}).get(oname, False))
                self.opt_vars[oname] = ovar
                ctk.CTkCheckBox(line, text=otext, variable=ovar, font=SMALL,
                                checkbox_width=18, checkbox_height=18).pack(side='left', padx=24)

        row = ctk.CTkFrame(tab, fg_color='transparent')
        row.grid(row=len(PCB_STEPS) + 2, column=0, sticky='ew', padx=12, pady=12)
        self.btn_analyze = ctk.CTkButton(row, text='Анализ', width=150, command=self.analyze)
        self.btn_analyze.pack(side='left')
        self.btn_apply = ctk.CTkButton(row, text='Применить', width=150, command=self.apply,
                                       fg_color='#b45309', hover_color='#92400e')
        self.btn_apply.pack(side='left', padx=10)
        ctk.CTkLabel(row, text='«Анализ» ничего не пишет. Перед записью создаётся копия .bakN, '
                               'Altium с этим файлом должен быть закрыт.',
                     font=SMALL, text_color='gray').pack(side='left', padx=8)

    # ------------------------------------------------------------ вкладка 3
    def _build_tab_layers(self, tab):
        tab.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(tab, text='Именование слоёв в Gerber у каждого экспорта своё, поэтому '
                               'автоопределение может промахнуться. Что не так — назначьте руками, '
                               'выбор запоминается для этой папки.',
                     anchor='w', justify='left', font=SMALL, text_color='gray')\
            .grid(row=0, column=0, sticky='ew', padx=12, pady=(10, 6))
        row = ctk.CTkFrame(tab, fg_color='transparent')
        row.grid(row=1, column=0, sticky='ew', padx=12)
        ctk.CTkLabel(row, text='Слоёв в стеке', font=SMALL).pack(side='left')
        self.stack_var = tk.StringVar(value='')
        ctk.CTkEntry(row, textvariable=self.stack_var, width=60).pack(side='left', padx=8)
        ctk.CTkButton(row, text='Применить', width=100, height=26, font=SMALL,
                      command=self.set_stack).pack(side='left')
        ctk.CTkLabel(row, text='обычно берётся из пар сверловки', font=SMALL,
                     text_color='gray').pack(side='left', padx=8)
        ctk.CTkButton(row, text='Маски автоопределения', width=180, height=26, font=SMALL,
                      command=self.open_rules).pack(side='right')
        ctk.CTkButton(row, text='Сбросить ручные', width=150, height=26, font=SMALL,
                      command=self.reset_overrides).pack(side='right', padx=8)
        self.layers_frame = ctk.CTkScrollableFrame(tab, label_text='')
        self.layers_frame.grid(row=2, column=0, sticky='nsew', padx=12, pady=10)
        self.layers_frame.grid_columnconfigure(0, weight=1)

    def _fill_layers(self):
        for w in self.layers_frame.winfo_children():
            w.destroy()
        self.layer_widgets = []
        if not self.project:
            return
        self._filling = True
        stack = self.project.stack or 0
        values = [NOT_COPPER, 'TOP'] + [f'MID{i}' for i in range(1, max(stack - 1, 0))] + ['BOTTOM']
        head = ctk.CTkFrame(self.layers_frame, fg_color='transparent')
        head.grid(row=0, column=0, sticky='ew')
        ctk.CTkLabel(head, text='файл', width=190, anchor='w', font=SMALL,
                     text_color='gray').pack(side='left')
        ctk.CTkLabel(head, text='слой в Gerber', width=230, anchor='w', font=SMALL,
                     text_color='gray').pack(side='left')
        ctk.CTkLabel(head, text='назначен', anchor='w', font=SMALL,
                     text_color='gray').pack(side='left')
        for i, r in enumerate(self.project.layer_rows(), start=1):
            line = ctk.CTkFrame(self.layers_frame, fg_color='transparent')
            line.grid(row=i, column=0, sticky='ew', pady=1)
            ctk.CTkLabel(line, text=r['name'], width=190, anchor='w', font=MONO).pack(side='left')
            ctk.CTkLabel(line, text=r['etch'] or '—', width=230, anchor='w',
                         font=SMALL).pack(side='left')
            var = tk.StringVar(value=r['layer'])
            menu = ctk.CTkOptionMenu(line, variable=var, values=values, width=130, font=SMALL,
                                     command=lambda v, name=r['name']: self.set_layer(name, v))
            menu.pack(side='left')
            ctk.CTkLabel(line, text='вручную' if r['manual'] else '', width=80, anchor='w',
                         font=SMALL, text_color='#b45309').pack(side='left', padx=8)
            self.layer_widgets.append((r['name'], var))
        self._filling = False

    def set_layer(self, file_name, value):
        if self._filling or not self.project:
            return
        self.project.set_override(file_name, value)
        self._save_settings()
        self._show_project(self.project)

    def set_stack(self):
        if not self.project:
            return
        try:
            n = int(self.stack_var.get())
        except ValueError:
            messagebox.showerror('Стек', 'Нужно число слоёв')
            return
        self.project.stack = n
        self.project.assign()
        self._show_project(self.project)

    def reset_overrides(self):
        if not self.project:
            return
        self.project.overrides.clear()
        self.project.assign()
        self._save_settings()
        self._show_project(self.project)

    def open_rules(self):
        try:
            os.startfile(DEFAULT_RULES)             # noqa: S606 - открыть в редакторе
        except OSError:
            messagebox.showinfo('Маски', DEFAULT_RULES)

    # ------------------------------------------------------------ отчёт и статус
    def _build_report(self):
        frame = ctk.CTkFrame(self)
        frame.grid(row=2, column=0, sticky='nsew', padx=12, pady=6)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(frame, fg_color='transparent')
        head.grid(row=0, column=0, sticky='ew', padx=10, pady=(8, 0))
        ctk.CTkLabel(head, text='Отчёт', font=('', 13, 'bold')).pack(side='left')
        self.verbose = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(head, text='подробности по каждому объекту', variable=self.verbose,
                        checkbox_width=18, checkbox_height=18, font=SMALL,
                        command=self._redraw_report).pack(side='left', padx=16)
        ctk.CTkButton(head, text='Сохранить отчёт', width=140, height=26, font=SMALL,
                      command=self.save_report).pack(side='right')
        self.out = ctk.CTkTextbox(frame, font=MONO)
        self.out.grid(row=1, column=0, sticky='nsew', padx=10, pady=(4, 10))

    def _build_status(self):
        self.status = ctk.CTkLabel(self, text='', anchor='w')
        self.status.grid(row=3, column=0, sticky='ew', padx=20, pady=(0, 10))

    # ------------------------------------------------------------ настройки
    def _load_settings(self):
        try:
            return json.load(open(SETTINGS, encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        folder = self.folder_var.get()
        overrides = self.settings.get('layer_overrides', {})
        if self.project:
            overrides[self.project.folder] = self.project.overrides
        data = dict(folder=folder,
                    out_dir=self.out_var.get(),
                    steps={k: v.get() for k, v in self.step_vars.items()},
                    opts={k: v.get() for k, v in self.opt_vars.items()},
                    layer_overrides=overrides)
        self.settings = data
        try:
            json.dump(data, open(SETTINGS, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        except OSError:
            pass

    # ------------------------------------------------------------ действия
    def pick_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder_var.get() or os.getcwd())
        if d:
            self.folder_var.set(os.path.normpath(d))
            self.scan()

    def scan(self):
        folder = self.folder_var.get()
        if not os.path.isdir(folder):
            messagebox.showerror('Папка', 'Такой папки нет')
            return
        self._set_busy(True, 'Читаю папку...')
        overrides = self.settings.get('layer_overrides', {}).get(os.path.abspath(folder), {})

        def work():
            try:
                self.queue.put(('project', Project(folder, overrides=overrides)))
            except Exception as e:                  # noqa: BLE001 - показываем в окне
                self.queue.put(('error', f'{type(e).__name__}: {e}'))
        threading.Thread(target=work, daemon=True).start()

    def convert(self):
        self._run(['drill'], apply=True, confirm=False)

    def analyze(self):
        self._run(self._pcb_steps(), apply=False)

    def apply(self):
        steps = self._pcb_steps()
        if not steps:
            messagebox.showinfo('Плата', 'Ни одна операция не выбрана')
            return
        if not self.reports and not messagebox.askyesno(
                'Применить', 'Сначала лучше нажать «Анализ». Всё равно писать?'):
            return
        errors = [e for r in self.reports for e in r.errors]
        if errors and not messagebox.askyesno(
                'Применить', 'В отчёте есть ошибки:\n\n' + '\n'.join(errors[:5]) +
                '\n\nВсё равно писать?'):
            return
        if not messagebox.askyesno('Применить',
                                   'Файл платы будет перезаписан, рядом останется копия .bakN.\n'
                                   'Altium с этим файлом должен быть закрыт. Писать?'):
            return
        self._run(steps, apply=True, confirm=False)

    def _pcb_steps(self):
        return [k for k, v in self.step_vars.items() if v.get()]

    def _run(self, steps, apply, confirm=True):
        if self.busy:
            return
        if not self.project:
            messagebox.showerror('Папка', 'Сначала выберите папку')
            return
        if not steps:
            messagebox.showinfo('Операции', 'Ни одна операция не выбрана')
            return
        pcb = self.pcb_var.get()
        pcb_path = os.path.join(self.project.folder, pcb) if pcb and pcb != '—' else None
        out_dir = self.out_var.get().strip() or '.'
        if not os.path.isabs(out_dir):
            out_dir = os.path.join(self.project.folder, out_dir)
        opts = dict(out_dir=out_dir,
                    zero_missing=self.opt_vars.get('zero_missing', tk.BooleanVar()).get(),
                    include_silk=self.opt_vars.get('include_silk', tk.BooleanVar()).get(),
                    holes_npth_over=self._number(self.opt_vars.get('holes_npth_over')))
        self._save_settings()
        self._set_busy(True, 'Применение...' if apply else 'Анализ...')
        self.out.delete('1.0', 'end')
        self.reports = []

        def work():
            try:
                reports, _ = runner.run(self.project, steps, opts=opts, apply=apply,
                                        pcb_path=pcb_path,
                                        log=lambda m: self.queue.put(('log', m)))
                self.queue.put(('reports', reports))
            except Exception:                       # noqa: BLE001 - показываем в окне
                import traceback
                self.queue.put(('error', traceback.format_exc()))
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _number(var):
        try:
            return float(str(var.get()).replace(',', '.')) if var else None
        except ValueError:
            return None

    def save_report(self):
        if not self.reports:
            messagebox.showinfo('Отчёт', 'Отчёта пока нет')
            return
        path = filedialog.asksaveasfilename(defaultextension='.txt',
                                            initialfile='allegro2altium.txt',
                                            initialdir=self.project.folder if self.project else '.')
        if path:
            open(path, 'w', encoding='utf-8').write(self.out.get('1.0', 'end'))
            self.status.configure(text='Отчёт сохранён')

    # ------------------------------------------------------------ обвязка
    def _set_busy(self, busy, text=''):
        self.busy = busy
        self.status.configure(text=text)
        state = 'disabled' if busy else 'normal'
        for b in (self.btn_analyze, self.btn_apply, self.btn_convert):
            b.configure(state=state)

    def _drain(self):
        try:
            while True:
                what, payload = self.queue.get_nowait()
                if what == 'project':
                    self._show_project(payload)
                    self._set_busy(False, 'Готово')
                elif what == 'log':
                    self.out.insert('end', '.. ' + payload + '\n')
                    self.out.see('end')
                elif what == 'reports':
                    self.reports = payload
                    self._redraw_report()
                    total, errors = runner.summarize(payload)
                    self._set_busy(False, f'Правок: {total}' +
                                   (f', ОШИБОК: {len(errors)}' if errors else ''))
                elif what == 'error':
                    self.out.insert('end', '\n' + payload + '\n')
                    self._set_busy(False, 'Ошибка')
                    messagebox.showerror('Ошибка', payload.strip().splitlines()[-1])
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _show_project(self, project):
        self.project = project
        self.drill_box.configure(state='normal')
        self.drill_box.delete('1.0', 'end')
        self.drill_box.insert('1.0', self._drill_text())
        self.drill_box.configure(state='disabled')
        names = [os.path.basename(p) for p in project.pcbdocs] or ['—']
        self.pcb_menu.configure(values=names)
        if self.pcb_var.get() not in names:
            self.pcb_var.set(names[0])
        self.stack_var.set(str(project.stack or ''))
        self._fill_layers()
        self._save_settings()

    def _drill_text(self):
        p = self.project
        if not p.drills:
            return ('Файлов сверловки в папке нет.\n'
                    'Нужны .drl из Allegro (или уже сконвертированные .TXT).')
        lines = [f'Стек: {p.stack or "?"} слоёв'
                 + (f'   формат {p.nc_params["int"]}:{p.nc_params["dec"]} '
                    f'{p.nc_params["units"]}' if p.nc_params.get('found') else ''), '']
        for d in p.drills:
            pair = p.pair_layers(d)
            lines.append(f'{d.name}')
            lines.append(f'    слои: {pair[0] + ".." + pair[1] if pair else "пара не определена"}'
                         f'    отверстий: {sum(d.count(t) for t in d.order)}')
            for t in d.order:
                lines.append(f'      T{t:02d}  C{d.tools[t]["dia"]}  '
                             f'{"металл." if d.tools[t].get("plated", True) else "неметалл."}'
                             f'  {d.count(t)} шт')
        for m in p.messages:
            lines.append(m)
        return '\n'.join(lines)

    def _redraw_report(self):
        if not self.reports:
            return
        self.out.delete('1.0', 'end')
        for r in self.reports:
            self.out.insert('end', (r.full() if self.verbose.get() else r.summary()) + '\n')
        total, errors = runner.summarize(self.reports)
        self.out.insert('end', f'\nвсего правок: {total}\n')
        for e in errors:
            self.out.insert('end', 'ОШИБКА: ' + e + '\n')
        self.out.see('end')


def main():
    ctk.set_appearance_mode('system')
    ctk.set_default_color_theme('blue')
    App().mainloop()


if __name__ == '__main__':
    main()
