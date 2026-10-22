"""Result of one operation: what changed, what was skipped, what went wrong."""
import collections


class Report:
    def __init__(self, title):
        self.title = title
        self.changes = collections.Counter()    # description -> count of edits made
        self.notes = collections.Counter()      # description -> count, nothing changed
        self.details = []                       # per object lines, for the details tab
        self.errors = []                        # anything that must stop a write
        self.files = []                         # files written or that would be written

    def change(self, what, n=1):
        self.changes[what] += n

    def note(self, what, n=1):
        self.notes[what] += n

    def detail(self, line):
        self.details.append(line)

    def error(self, line):
        self.errors.append(line)

    @property
    def total(self):
        return sum(self.changes.values())

    @property
    def ok(self):
        return not self.errors

    def summary(self):
        out = [f'== {self.title}: {self.total} правок' + (', ОШИБКИ' if self.errors else '')]
        for k, n in self.changes.most_common():
            out.append(f'   {n:6d}  {k}')
        for k, n in self.notes.most_common():
            out.append(f'   {n:6d}  - {k}')
        for e in self.errors:
            out.append(f'   ОШИБКА: {e}')
        for f in self.files:
            out.append(f'   файл: {f}')
        return '\n'.join(out)

    def full(self):
        return '\n'.join([self.summary()] + ['   ' + d for d in self.details])
