"""ASCII PcbDoc (Protel PCB ASCII) as a list of records that can be edited in place.

The file is one record per line, |RECORD=Via|KEY=VALUE|...  Region records come
without the leading pipe.  Everything is kept as tokens - record text and the
line separator that follows it - so a save rewrites only the fields that were
actually changed and every other byte, including CR line endings, stays put.
"""
import collections
import os
import re
import shutil

COPPER = ({'TOP', 'BOTTOM'} | {f'MID{i}' for i in range(1, 31)}
          | {f'PLANE{i}' for i in range(1, 17)})


# ---------------------------------------------------------------- field access
def field(rec, key):
    m = re.search(r'\|' + key + r'=([^|]*)', rec)
    return m.group(1) if m else None


def set_field(rec, key, value):
    return re.sub(r'\|' + key + r'=[^|]*', '|' + key + '=' + str(value), rec, count=1)


def kind(rec):
    m = re.match(r'\|?RECORD=([^|]*)', rec)
    return m.group(1) if m else None


def name_of(rec):
    """NAME is stored as comma separated character codes."""
    s = field(rec, 'NAME')
    if not s:
        return ''
    try:
        return ''.join(chr(int(c)) for c in s.split(','))
    except ValueError:
        return s


def mil(v):
    """'15.748mil' -> 15.748"""
    return float(str(v).replace('mil', ''))


def fmt_mil(v):
    s = f'{v:.4f}'.rstrip('0').rstrip('.')
    return (s or '0') + 'mil'


def fmt_rot(v):
    """Altium writes rotations as ' 1.35000000000000E+0002'."""
    return f' {v:.14E}'.replace('E+', 'E+00').replace('E-', 'E-00')


def norm_rot(xsize, ysize, rot, eps=0.02):
    """A square repeats every 90 deg, anything else every 180."""
    return round(rot % (90.0 if abs(xsize - ysize) < eps else 180.0), 4)


# ---------------------------------------------------------------- document
class PcbDoc:
    def __init__(self, path):
        self.path = path
        raw = open(path, 'rb').read().decode('latin-1')
        if not raw.lstrip().startswith(('|RECORD=', 'RECORD=')):
            raise ValueError('это не ASCII PcbDoc. В Altium: File - Save As - PCB ASCII')
        self.tok = re.split(r'(\r\n|\r|\n)', raw)
        self.rec_idx = [i for i in range(0, len(self.tok), 2) if self.tok[i].strip()]

    # -- reading
    def indices(self, rec_kind):
        return [i for i in self.rec_idx if self.tok[i] is not None and kind(self.tok[i]) == rec_kind]

    def rec(self, i):
        return self.tok[i]

    def get(self, i, key):
        return field(self.tok[i], key)

    def counts(self):
        return collections.Counter(kind(self.tok[i]) for i in self.rec_idx if self.tok[i] is not None)

    def origin(self):
        """The import offset: pcb coordinate = gerber/drill coordinate + origin."""
        b = self.indices('Board')
        if not b:
            return 0.0, 0.0
        return mil(self.get(b[0], 'ORIGINX') or '0mil'), mil(self.get(b[0], 'ORIGINY') or '0mil')

    # -- writing
    def set(self, i, key, value):
        self.tok[i] = set_field(self.tok[i], key, value)

    def delete(self, i):
        self.tok[i] = None
        if i + 1 < len(self.tok):
            self.tok[i + 1] = None          # and the separator that follows it

    def text(self):
        return ''.join(t for t in self.tok if t is not None)

    def save(self, path=None, backup=True):
        path = path or self.path
        written = []
        if backup and os.path.exists(path):
            n = 1
            while os.path.exists(f'{path}.bak{n}'):
                n += 1
            shutil.copy2(path, f'{path}.bak{n}')
            written.append(f'{path}.bak{n}')
        open(path, 'wb').write(self.text().encode('latin-1'))
        written.append(path)
        return written
