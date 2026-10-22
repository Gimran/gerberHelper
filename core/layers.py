"""Автоопределение медных слоёв по маскам из layer_rules.json.

Именование слоёв в Gerber - лотерея, поэтому маски вынесены в отдельный файл,
который можно править, не трогая код. Что маски не опознали, назначается руками
на вкладке «Слои».
"""
import json
import os
import re

DEFAULT_RULES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'layer_rules.json')


class LayerRules:
    def __init__(self, path=DEFAULT_RULES):
        self.path = path
        self.error = None
        data = {}
        try:
            data = json.load(open(path, encoding='utf-8'))
        except (OSError, ValueError) as e:
            self.error = f'{os.path.basename(path)}: {e}'
        self.skip = self._compile(data.get('skip'))
        self.top = self._compile(data.get('top'))
        self.bottom = self._compile(data.get('bottom'))
        self.inner = self._compile(data.get('inner'))

    @staticmethod
    def _compile(patterns):
        out = []
        for p in patterns or ():
            try:
                out.append(re.compile(p, re.I))
            except re.error:
                pass                                # кривую маску просто игнорируем
        return out

    def classify(self, *names):
        """-> 'top', 'bottom', номер слоя или None. Имена проверяются по порядку."""
        names = [n for n in names if n]
        for name in names:
            if any(r.search(name) for r in self.skip):
                return None
        for name in names:
            if any(r.search(name) for r in self.top):
                return 'top'
            if any(r.search(name) for r in self.bottom):
                return 'bottom'
            for r in self.inner:
                m = r.search(name)
                if m and m.groups():
                    try:
                        return int(m.group(1))
                    except (TypeError, ValueError):
                        pass
        return None
