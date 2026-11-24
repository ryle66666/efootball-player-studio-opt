"""Card images delivered with the Android tournament document."""
from pathlib import Path

_INSTANCES = {}


class Portraits:
    def __init__(self):
        self.entries = {}
        self.icons = {}

    def configure(self, document):
        self.entries = document.get('tournament_hub_portraits') or {}
        self.icons = document.get('tournament_hub_booster_icons') or {}

    def describe(self, ids):
        result = {}
        for pid in dict.fromkeys(ids):
            row = self.entries.get(str(pid))
            if isinstance(row, dict):
                result[str(pid)] = row
        return {'players': result, 'pending': 0}

    def booster_icons(self):
        return {int(k): v for k, v in self.icons.items() if str(k).isdigit()}


def instance(root):
    key = str(Path(root).resolve())
    if key not in _INSTANCES:
        _INSTANCES[key] = Portraits()
    return _INSTANCES[key]
