"""NC drill files: read Allegro output in either flavour, write it the way Altium wants.

Handles three shapes of the same data:
  * Allegro plain     - no M48 header, tool change by M00, sizes only in the
                        ";  Holesize n. = ..." comments        (TOOL-SELECT NO)
  * Allegro enhanced  - M48 header with TnnC.008, tool change by Tnn
                        (TOOL-SELECT YES / ENHANCED_EXCELLON YES)
  * Altium            - what this tool writes: M48 with ;FILE_FORMAT, ;TYPE and
                        TnnF00S00C<dia>

Values are never converted: coordinates and hole sizes are written in the units,
resolution and zero suppression of the source.
"""
import glob
import os
import re

MIL_PER_MM = 1000.0 / 25.4


def read_nc_param(folder):
    """Coordinate format from Allegro nc_param.txt; defaults to inch 2:5."""
    p = dict(int=2, dec=5, units='ENGLISH', lead_sup=False, trail_sup=False, found=False)
    path = os.path.join(folder, 'nc_param.txt')
    if os.path.exists(path):
        p['found'] = True
        for line in open(path, encoding='latin-1'):
            k, _, v = line.strip().partition(' ')
            v = v.strip()
            if k == 'INTEGER-PLACES':
                p['int'] = int(v)
            elif k == 'DECIMAL-PLACES':
                p['dec'] = int(v)
            elif k == 'OUTPUT-UNITS':
                p['units'] = v
            elif k == 'SUPPRESS-LEAD-ZEROES':
                p['lead_sup'] = v == 'YES'
            elif k == 'SUPPRESS-TRAIL-ZEROES':
                p['trail_sup'] = v == 'YES'
    return p


def shift_decimal(num, places):
    """'11.811024' shifted left by 3 -> '0.011811024' (exact, string based)."""
    ip, _, fp = num.partition('.')
    digits = (ip + fp).lstrip('0') or '0'
    exp = len(fp) + places
    digits = digits.rjust(exp + 1, '0')
    s = (digits[:-exp] + '.' + digits[-exp:]) if exp else digits
    s = s.rstrip('0')
    return s + '0' if s.endswith('.') else s


def norm_dia(s):
    s = (s or '').strip()
    return '0' + s if s.startswith('.') else s


class DrillFile:
    def __init__(self, path, params):
        self.path = path
        self.name = os.path.basename(path)
        m = re.search(r'-(\d+)-(\d+)\.(TXT|drl|txt|DRL)$', path)
        self.pair = (int(m.group(1)), int(m.group(2))) if m else None
        self.layers = None                  # layer names from the header comment
        self.tools = {}                     # number -> dict(dia str, plated, qty)
        self.hits = {}                      # number -> [(x_int, y_int)]
        self.fmt = dict(units=params['units'], lead_sup=params['lead_sup'],
                        trail_sup=params['trail_sup'], int=params['int'], dec=params['dec'])
        self._parse()

    # ------------------------------------------------------------ reading
    def _to_int(self, tok):
        neg = tok.startswith('-')
        d = tok.lstrip('+-')
        if self.fmt['trail_sup']:           # LZ: the value is left aligned
            d = d.ljust(self.fmt['int'] + self.fmt['dec'], '0')
        return -int(d) if neg else int(d)

    def _parse(self):
        order, cur, body = [], None, False
        x = y = None
        pending_type = None
        for raw in open(self.path, encoding='latin-1'):
            line = raw.strip()
            if not line:
                continue
            if not body:
                m = re.fullmatch(r'(INCH|METRIC),\s*(TZ|LZ)', line)
                if m:
                    self.fmt['units'] = 'ENGLISH' if m.group(1) == 'INCH' else 'METRIC'
                    self.fmt['lead_sup'] = m.group(2) == 'TZ'
                    self.fmt['trail_sup'] = m.group(2) == 'LZ'
                    continue
                m = re.fullmatch(r';FILE_FORMAT=(\d+):(\d+)', line)
                if m:
                    self.fmt['int'], self.fmt['dec'] = int(m.group(1)), int(m.group(2))
                    continue
                m = re.fullmatch(r';TYPE=(PLATED|NON_PLATED)', line)
                if m:
                    pending_type = m.group(1) == 'PLATED'
                    continue
                m = re.fullmatch(r'T(\d+)(?:F\d+)?(?:S\d+)?C([\d.]+)', line)
                if m:                       # tool table entry, size exactly as written
                    t = int(m.group(1))
                    d = self.tools.setdefault(t, {})
                    d['dia'] = norm_dia(m.group(2))
                    if pending_type is not None:
                        d['plated'] = pending_type
                    continue
                m = re.search(r'layers (\S+) and (\S+)', line)
                if m:
                    self.layers = (m.group(1), m.group(2))
                m = re.search(r';\s*(?:T(\d+)\s+)?Holesize\s+(\d+)\.\s*=\s*([\d.]+).*?'
                              r'\b(NON_PLATED|PLATED)\s+(MILS|MM|INCHES|INCH)\s+Quantity\s*=\s*(\d+)', line)
                if m:
                    t = int(m.group(1) or m.group(2))
                    d = self.tools.setdefault(t, {})
                    d['plated'] = m.group(4) == 'PLATED'
                    d['qty'] = int(m.group(6))
                    if 'dia' not in d:      # plain flavour: the size lives only here
                        size, unit = m.group(3), m.group(5)
                        if self.fmt['units'] == 'ENGLISH' and unit == 'MILS':
                            size = shift_decimal(size, 3)
                        elif (self.fmt['units'] == 'METRIC') != (unit == 'MM'):
                            raise ValueError(f'{self.name}: инструмент {t} задан в {unit}, '
                                             f'вывод в {self.fmt["units"]}')
                        d['dia'] = norm_dia(size)
                    if t not in order:
                        order.append(t)
                    self.hits.setdefault(t, [])
                if line == '%':
                    body = True
                    cur = order[0] if order else None
                continue
            if line in ('G90', 'G05', 'G81'):
                continue
            if line == 'M30':
                break
            m = re.fullmatch(r'T(\d+)', line)               # enhanced: explicit tool select
            if m:
                cur = int(m.group(1))
                if cur not in order:
                    order.append(cur)
                self.hits.setdefault(cur, [])
                continue
            if line == 'M00':                               # plain: next tool in order
                cur = order[order.index(cur) + 1]
                continue
            m = re.fullmatch(r'R(\d+)(?:X([+-]?\d+))?(?:Y([+-]?\d+))?', line)
            if m:                                           # repeat code
                dx = self._to_int(m.group(2)) if m.group(2) else 0
                dy = self._to_int(m.group(3)) if m.group(3) else 0
                for _ in range(int(m.group(1))):
                    x += dx
                    y += dy
                    self.hits[cur].append((x, y))
                continue
            m = re.fullmatch(r'(?:X([+-]?\d+))?(?:Y([+-]?\d+))?', line)
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    x = self._to_int(m.group(1))
                if m.group(2):
                    y = self._to_int(m.group(2))
                self.hits[cur].append((x, y))
                continue
            raise ValueError(f'{self.name}: непонятная строка {line!r}')
        self.order = [t for t in order if self.hits.get(t)] or sorted(self.hits)
        self.hits = {t: self.hits[t] for t in self.order}
        self.tools = {t: self.tools[t] for t in self.order if t in self.tools}

    # ------------------------------------------------------------ helpers
    def _scale(self):
        return (1000.0 if self.fmt['units'] == 'ENGLISH' else MIL_PER_MM) / 10 ** self.fmt['dec']

    def dia_mil(self, tool):
        d = float(self.tools[tool]['dia'])
        return d * (1000.0 if self.fmt['units'] == 'ENGLISH' else MIL_PER_MM)

    def hits_mil(self):
        """[(x_mil, y_mil, tool_number), ...] rounded to 0.01 mil"""
        s = self._scale()
        return [(round(x * s, 2), round(y * s, 2), t)
                for t, pts in self.hits.items() for (x, y) in pts]

    def count(self, tool):
        return len(self.hits.get(tool, ()))

    # ------------------------------------------------------------ writing
    def _fmt_coord(self, v):
        width = self.fmt['int'] + self.fmt['dec']
        sign = '-' if v < 0 else ''
        d = str(abs(v))
        if self.fmt['trail_sup']:
            d = d.zfill(width).rstrip('0') or '0'
        elif not self.fmt['lead_sup']:
            d = d.zfill(width)
        return sign + d

    def write_altium(self, path):
        unit_kw = 'INCH' if self.fmt['units'] == 'ENGLISH' else 'METRIC'
        zero_kw = 'TZ' if self.fmt['lead_sup'] else 'LZ'
        order = sorted(self.order, key=lambda t: (not self.tools[t].get('plated', True), t))
        with open(path, 'w', newline='\r\n') as f:
            f.write(f'M48\n;Layer_Color=9474304\n'
                    f';FILE_FORMAT={self.fmt["int"]}:{self.fmt["dec"]}\n{unit_kw},{zero_kw}\n')
            typ = None
            for t in order:
                cur = 'PLATED' if self.tools[t].get('plated', True) else 'NON_PLATED'
                if cur != typ:
                    f.write(f';TYPE={cur}\n')
                    typ = cur
                f.write(f'T{t:02d}F00S00C{self.tools[t]["dia"]}\n')
            f.write('%\n')
            for t in order:
                f.write(f'T{t:02d}\n')
                for (x, y) in self.hits[t]:
                    f.write(f'X{self._fmt_coord(x)}Y{self._fmt_coord(y)}\n')
            f.write('M30\n')
        return path


def find_drills(folder, exts=('drl', 'TXT')):
    out = []
    for e in exts:
        out += glob.glob(os.path.join(folder, '*.' + e))
    return sorted(out)
