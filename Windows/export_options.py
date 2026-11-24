"""Prepare the selected export without changing saved editor drafts."""
import copy
import hashlib
import json
import uuid


def selection(values, label, limit=None):
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError(label + '选择无效')
    result = list(dict.fromkeys(values))
    if limit is not None and len(result) > limit:
        raise ValueError(label + '选择数量超出范围')
    return result


def _snapshot(store, filename, key, fallback):
    from editor_drafts import listing
    with store.lock:
        path = store.root / filename
        value = json.loads(path.read_text('utf8')) if path.is_file() else copy.deepcopy(fallback)
        row = listing(store).get(key) or {}
        if not row.get('committed') and row.get('value') is not None:
            value = row['value']
        return copy.deepcopy(value)


def settings(store, overrides=None):
    from game_settings import DEFAULTS, normalize
    value = _snapshot(store, 'game-settings.json', 'settings:settings', DEFAULTS)
    if not isinstance(value, dict):
        raise ValueError('游戏设置格式无效')
    value.update(copy.deepcopy(overrides or {}))
    for key, default in DEFAULTS.items():
        section = value.get(key, {})
        if isinstance(section, dict) and section.get('enabled', default['enabled']) is False:
            value[key] = {'enabled': False}
    return normalize(value)


def progress(store, *, style_enabled=True):
    from workspace_tools import DEFAULTS, game_options, normalize_game_options
    value = _snapshot(store, 'game-options.json', 'progress:progress', DEFAULTS)
    if not isinstance(value, dict):
        raise ValueError('进度设置格式无效')
    if not style_enabled:
        value['style_mode'] = DEFAULTS['style_mode']
    if value.get('enabled', False) is False:
        value = {'enabled': False, 'style_mode': value.get('style_mode', DEFAULTS['style_mode'])}
    value = normalize_game_options(value)
    previous = game_options(store)
    if not value['enabled']:
        if previous.get('revision'):
            value['revision'] = previous['revision']
        return value
    keys = ('enabled', 'division', 'points', 'matches', 'wins', 'draws', 'losses')
    value['revision'] = (previous['revision'] if previous.get('revision') and
                         all(previous.get(key) == value[key] for key in keys) else str(uuid.uuid4()))
    return value


def world(store, selected=None, *, ignore_stadium=False):
    from world_workspace import default, normalize
    value = _snapshot(store, 'world-workspace.json', 'world:world', default())
    if not isinstance(value, dict):
        raise ValueError('球场与赛事设置格式无效')
    if ignore_stadium:
        value['stadium'] = {**default()['stadium'], 'enabled': False, 'unlock_all': False}
    stadium = value.get('stadium')
    if isinstance(stadium, dict) and stadium.get('enabled') is False:
        value['stadium'] = {**default()['stadium'], 'enabled': False,
                            'unlock_all': stadium.get('unlock_all', True)}
    wanted = set(selection(selected, '赛事')) if selected is not None else None
    rows = value.get('tournaments', [])
    if not isinstance(rows, list):
        if wanted == set():
            rows = []
        else:
            raise ValueError('锦标赛列表无效')
    value['tournaments'] = [row for row in rows if isinstance(row, dict) and
                            (row.get('enabled') if wanted is None else row.get('uuid') in wanted)]
    return normalize(value, store.root / 'assets')


def cache_metadata(raw, replacement):
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get('assets', {}), dict):
        raise ValueError('游戏 Python 资源清单格式无效')
    value.setdefault('assets', {})['app.imy'] = hashlib.sha1(replacement).hexdigest()
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf8')
