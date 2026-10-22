"""Что лежит в папке: Gerber, сверловка, ASCII PcbDoc, и как слои ложатся на стек.

Слои определяются масками из layer_rules.json, а то, что маски не опознали или
опознали неверно, назначается вручную: overrides = {имя файла: 'TOP' | 'MID3' |
'BOTTOM' | None}, где None значит «не медь».
"""
import glob
import os
import re

from . import excellon
from .gerber import Gerber
from .layers import LayerRules

GERBER_EXT = ('art', 'gbr', 'ger', 'gdo', 'pho', 'gtl', 'gbl', 'g1', 'g2', 'g3', 'g4')
NOT_COPPER = 'не медь'


def layer_name(i, n):
    """Позиция в стеке -> имя слоя Altium."""
    return 'TOP' if i == 1 else 'BOTTOM' if i == n else f'MID{i - 1}'


class Project:
    def __init__(self, folder, overrides=None, rules=None):
        self.folder = os.path.abspath(folder)
        self.rules = rules or LayerRules()
        self.overrides = {k: v for k, v in (overrides or {}).items()}
        self.messages = []
        self.gerbers = []        # [{path, name, etch, auto, pos, manual}]
        self.copper = {}         # позиция -> path
        self.stack = 0
        self.drills = []
        self.pcbdocs = []
        self._cache = {}
        self.scan()

    # ------------------------------------------------------------ поиск файлов
    def _candidates(self, folder):
        out = []
        for e in GERBER_EXT:
            out += glob.glob(os.path.join(folder, '*.' + e))
        return sorted(out)

    def scan(self):
        if self.rules.error:
            self.messages.append(self.rules.error)
        parent = os.path.dirname(self.folder)

        files = self._candidates(self.folder)
        if not any(self._etch(p) for p in files):
            up = self._candidates(parent)
            if any(self._etch(p) for p in up):
                files = up
                self.messages.append(f'Gerber взяты из папки выше: {parent}')
        self.gerbers = [dict(path=p, name=os.path.basename(p), etch=self._etch(p),
                             auto=None, pos=None, manual=False) for p in files]

        params = excellon.read_nc_param(self.folder)
        if not params['found']:
            params = excellon.read_nc_param(parent)
        self.nc_params = params
        drl = excellon.find_drills(self.folder, ('drl',)) or excellon.find_drills(parent, ('drl',))
        txt = excellon.find_drills(self.folder, ('TXT',))
        self.drills = []
        for path in (drl or txt):
            try:
                self.drills.append(excellon.DrillFile(path, params))
            except Exception as e:                  # noqa: BLE001 - показываем пользователю
                self.messages.append(f'{os.path.basename(path)}: {e}')

        self.stack = max([max(d.pair) for d in self.drills if d.pair] or [0]) or None
        self.assign()

        self.pcbdocs = []
        for path in glob.glob(os.path.join(self.folder, '*.PcbDoc')):
            try:
                if open(path, 'rb').read(200).decode('latin-1').lstrip()\
                        .startswith(('|RECORD=', 'RECORD=')):
                    self.pcbdocs.append(path)
            except OSError:
                pass
        self.pcbdocs.sort(key=os.path.getmtime, reverse=True)

    @staticmethod
    def _etch(path):
        try:
            head = open(path, encoding='latin-1').read(4000)
        except OSError:
            return None
        m = re.search(r'G04 Layer:\s+ETCH/(.+?)\*', head)
        return m.group(1).strip() if m else None

    # ------------------------------------------------------------ раскладка слоёв
    def assign(self):
        """Маски + ручные назначения -> self.copper и поля pos в self.gerbers."""
        for g in self.gerbers:
            g['auto'] = self.rules.classify(g['etch'], os.path.splitext(g['name'])[0])
            if g['auto'] is None and g['etch']:
                g['auto'] = 'copper'             # запись ETCH есть, но номер не вычитался
        n_copper = sum(1 for g in self.gerbers
                       if g['auto'] is not None or g['name'] in self.overrides)
        stack = self.stack or n_copper or 0

        taken, unplaced = {}, []
        for g in self.gerbers:
            g['manual'] = g['name'] in self.overrides
            if g['manual']:
                want = self.overrides[g['name']]
                if not want or want == NOT_COPPER:
                    g['pos'] = None
                    continue
                stack = max(stack, self._index_of(want, stack) or 0)
            kind = self.overrides.get(g['name']) if g['manual'] else g['auto']
            g['pos'] = None
            if kind is None:
                continue
            if isinstance(kind, str) and kind not in ('copper',):
                pos = self._index_of(kind, stack)
            elif kind == 'top':
                pos = 1
            elif kind == 'bottom':
                pos = stack
            elif isinstance(kind, int):
                pos = kind
            else:
                pos = None
            if pos and pos not in taken:
                taken[pos] = g
                g['pos'] = pos
            else:
                unplaced.append(g)

        self.stack = max([stack] + list(taken)) if (taken or stack) else 0
        for g in unplaced:                        # остатки занимают свободные места по порядку
            for pos in range(1, self.stack + 1):
                if pos not in taken:
                    taken[pos] = g
                    g['pos'] = pos
                    break
        self.copper = {pos: g['path'] for pos, g in sorted(taken.items())}

    def _index_of(self, name, stack):
        name = str(name).upper()
        if name == 'TOP':
            return 1
        if name == 'BOTTOM':
            return stack or None
        m = re.fullmatch(r'MID(\d+)', name)
        return int(m.group(1)) + 1 if m else None

    def set_override(self, file_name, value):
        """value: 'TOP' | 'MID3' | 'BOTTOM' | NOT_COPPER | None (снять ручное назначение)"""
        if value is None:
            self.overrides.pop(file_name, None)
        else:
            self.overrides[file_name] = value
        self.assign()

    # ------------------------------------------------------------ доступ
    def layer_name(self, i):
        return layer_name(i, self.stack)

    def layer_index(self, name):
        for i in range(1, (self.stack or 0) + 1):
            if self.layer_name(i) == name:
                return i
        return None

    def gerber(self, layer):
        i = self.layer_index(layer) if isinstance(layer, str) else layer
        path = self.copper.get(i)
        if not path:
            return None
        if path not in self._cache:
            self._cache[path] = Gerber(path)
        return self._cache[path]

    def pair_layers(self, drill):
        if not drill.pair or not self.stack:
            return None
        a, b = drill.pair
        return self.layer_name(a), self.layer_name(b)

    def layer_rows(self):
        """Для вкладки «Слои»: по строке на каждый найденный Gerber."""
        rows = []
        for g in sorted(self.gerbers, key=lambda x: (x['pos'] is None, x['pos'] or 0, x['name'])):
            rows.append(dict(name=g['name'], etch=g['etch'] or '',
                             layer=self.layer_name(g['pos']) if g['pos'] else NOT_COPPER,
                             manual=g['manual']))
        return rows

    def describe(self):
        out = [f'Папка: {self.folder}',
               f'Стек: {self.stack} слоёв' if self.stack else 'Стек: не определён']
        for i, path in self.copper.items():
            g = next(x for x in self.gerbers if x['path'] == path)
            out.append(f'   {self.layer_name(i):7s} <- {os.path.basename(path)}'
                       + (f' ({g["etch"]})' if g['etch'] else '')
                       + ('  [вручную]' if g['manual'] else ''))
        for d in self.drills:
            pair = self.pair_layers(d)
            tools = ', '.join(f'{d.tools[t]["dia"]}x{d.count(t)}' for t in d.order)
            out.append(f'   сверловка {d.name}: '
                       f'{pair[0] + ".." + pair[1] if pair else "пара не определена"}  [{tools}]')
        for p in self.pcbdocs:
            out.append(f'   PcbDoc ASCII: {os.path.basename(p)} '
                       f'({os.path.getsize(p) / 1e6:.1f} МБ)')
        out += ['   ' + m for m in self.messages]
        return '\n'.join(out)
