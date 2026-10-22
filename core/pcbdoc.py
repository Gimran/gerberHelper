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

    def delete_polygons(self, numbers):
        """Удалить полигоны с данными позициями в порядке файла, вместе с их залитыми регионами.

        Оставшиеся полигоны перенумеровываются (ID, INDEXFORSAVE), ссылки Region -> Polygon
        переписываются, регионы получают сплошную нумерацию, если среди них были удалённые.
        -> dict(children={позиция: число регионов}, regions, renumbered, rereferenced, dangling)
        """
        polys, regions = self.indices('Polygon'), self.indices('Region')
        doomed = set(numbers)
        children = collections.defaultdict(list)
        for i in regions:
            p = self.get(i, 'POLYGON')
            if p is not None:
                children[int(p)].append(i)
        doomed_regions = [i for n in doomed for i in children[n]]
        counts = {n: len(children[n]) for n in doomed}

        remap = {old: new for new, old in enumerate(n for n in range(len(polys)) if n not in doomed)}
        drop = {polys[n] for n in doomed} | set(doomed_regions)
        for i in drop:
            self.delete(i)

        renum = reref = 0
        for old, i in enumerate(polys):
            if i in drop:
                continue
            if old != remap[old]:
                self.set(i, 'INDEXFORSAVE', remap[old])
                self.set(i, 'ID', remap[old])
                renum += 1
        new_region_index = 0
        for i in regions:
            if i in drop:
                continue
            p = self.get(i, 'POLYGON')
            if p is not None and int(p) in remap and remap[int(p)] != int(p):
                self.set(i, 'POLYGON', remap[int(p)])
                reref += 1
            if doomed_regions and self.get(i, 'INDEXFORSAVE') != str(new_region_index):
                self.set(i, 'INDEXFORSAVE', new_region_index)
            new_region_index += 1

        left = {int(self.get(i, 'INDEXFORSAVE')) for i in self.indices('Polygon')}
        dangling = sorted({int(self.get(i, 'POLYGON')) for i in self.indices('Region')
                           if self.get(i, 'POLYGON') is not None
                           and int(self.get(i, 'POLYGON')) not in left})
        return dict(children=counts, regions=len(doomed_regions), renumbered=renum,
                    rereferenced=reref, dangling=dangling)

    def insert_after(self, i, records):
        """Insert new records right after record i, with the same line separator.

        Positions of everything after i shift, so the index list is rebuilt; indices
        obtained earlier for records behind i must not be reused.
        """
        sep = self.tok[i + 1] if i + 1 < len(self.tok) and self.tok[i + 1] is not None else '\r'
        block = []
        for text in records:
            block += [text, sep]
        self.tok[i + 2:i + 2] = block
        self.reindex()

    def reindex(self):
        self.rec_idx = [i for i in range(0, len(self.tok), 2)
                        if self.tok[i] is not None and self.tok[i].strip()]

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
