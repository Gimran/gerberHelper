"""RS-274X reader: coordinate format, apertures (including %AM macros) and flashes.

Only what is needed to know what copper sits under a pad or via: the position of
every D03 flash and the shape of the aperture used for it.  Positions are kept
in mil, rounded to 0.01 mil, which is the resolution both Allegro gerbers and
Altium PcbDoc files use.
"""
import re

MIL_PER_MM = 1000.0 / 25.4
GRID = 0.01


class Shape:
    """A pad shape as Altium stores it: ROUND (round or oval), RECTANGLE, or COMPLEX."""

    def __init__(self, kind, xsize, ysize, rotation=0.0):
        self.kind = kind
        self.xsize = xsize
        self.ysize = ysize
        self.rotation = rotation

    @property
    def square(self):
        return abs(self.xsize - self.ysize) < 0.02

    def __repr__(self):
        return f'{self.kind} {self.xsize:g}x{self.ysize:g} rot {self.rotation:g}'


def aperture_shape(text, macros):
    """Aperture definition -> Shape, or None if it cannot be read."""
    a = (text or '').strip()
    m = re.fullmatch(r'C,([\d.]+)(?:X.*)?', a)
    if m:
        d = float(m.group(1)) * 1000
        return Shape('ROUND', d, d)
    m = re.fullmatch(r'R,([\d.]+)X([\d.]+)(?:X.*)?', a)
    if m:
        return Shape('RECTANGLE', float(m.group(1)) * 1000, float(m.group(2)) * 1000)
    m = re.fullmatch(r'O,([\d.]+)X([\d.]+)(?:X.*)?', a)
    if m:
        return Shape('ROUND', float(m.group(1)) * 1000, float(m.group(2)) * 1000)
    name = re.match(r'([A-Za-z_$][\w$]*)', a)
    if name and name.group(1) in macros:
        prim = [p.strip() for p in macros[name.group(1)].strip().split('*') if p.strip()]
        if any(p.startswith('4,') for p in prim):           # free-form outline
            return Shape('COMPLEX', 0, 0)
        rects = [p for p in prim if p.startswith('21,')]    # centre line = rectangle
        circs = [p for p in prim if p.startswith('1,')]
        if len(rects) == 1:
            t = [s.strip() for s in rects[0].split(',')]
            w, h = float(t[2]) * 1000, float(t[3]) * 1000
            rot = float(t[6]) if len(t) > 6 and t[6] else 0.0
            if len(circs) == 2:                             # rectangle + round caps = oval
                cap = float(circs[0].split(',')[2].strip()) * 1000
                if h >= w:                                  # long axis was Y, Altium wants it on X
                    return Shape('ROUND', h + cap, cap, rot + 90)
                return Shape('ROUND', w + cap, cap, rot)
            return Shape('RECTANGLE', w, h, rot)
        return Shape('COMPLEX', 0, 0)
    return None


class Gerber:
    def __init__(self, path):
        self.path = path
        txt = open(path, encoding='latin-1').read()
        m = re.search(r'%FSLAX(\d)(\d)Y\d\d\*MO(IN|MM)\*%', txt)
        if not m:
            raise ValueError(f'{path}: не найден заголовок %FSLAX..*MO..*%')
        self.dec = int(m.group(2))
        self.unit = m.group(3)
        scale = (1000.0 if self.unit == 'IN' else MIL_PER_MM) / 10 ** self.dec
        self.apertures = {int(a): b for a, b in re.findall(r'%ADD(\d+)([^*]*)\*%', txt)}
        self.macros = dict(re.findall(r'%AM([^*]+)\*(.*?)%', txt, re.S))
        self.etch_layers = re.findall(r'G04 Layer:\s+ETCH/(.+?)\*', txt)
        self.film = (re.search(r'G04 Film Name:\s+(\S+)\*', txt) or [None, ''])[1]

        x = y = 0
        cur = None
        self.flashes = {}
        # Расширенные команды (%...%) уже разобраны выше. Вырезаем их целиком до разбиения
        # по '*': иначе закрывающий '%' склеивается со следующей обычной командой
        # ('%\nD10') и выбор апертуры теряется
        body = re.sub(r'%[^%]*%', '', txt)
        for blk in body.split('*'):
            blk = blk.strip()
            if not blk or blk.startswith('G04'):
                continue
            m = re.search(r'D(\d{2,})$', blk)
            if m and int(m.group(1)) >= 10:
                cur = int(m.group(1))
            m = re.search(r'X([+-]?\d+)', blk)
            if m:
                x = int(m.group(1))
            m = re.search(r'Y([+-]?\d+)', blk)
            if m:
                y = int(m.group(1))
            if blk.endswith('D03'):
                self.flashes[(round(x * scale, 2), round(y * scale, 2))] = cur
        self._shapes = {}

    def shape_at(self, x, y):
        """Shape of the flash at (x, y) in mil, within two grid steps, else None.

        Two steps, because the pcb coordinate went through an origin shift and a
        rounding to 0.01 mil on the way here.
        """
        for dx in (0, -GRID, GRID, -2 * GRID, 2 * GRID):
            for dy in (0, -GRID, GRID, -2 * GRID, 2 * GRID):
                d = self.flashes.get((round(x + dx, 2), round(y + dy, 2)))
                if d is not None:
                    if d not in self._shapes:
                        self._shapes[d] = aperture_shape(self.apertures.get(d), self.macros)
                    return self._shapes[d]
        return None

    def has_flash(self, x, y):
        return self.shape_at(x, y) is not None
