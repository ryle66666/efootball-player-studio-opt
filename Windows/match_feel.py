"""Per-export match feel preferences for the V17 adapter."""

import json
import math
import struct
from functools import cache

from content_pack import atomic_json
from gameplay_v8 import legacy_constant
from studio_resources import bundled_resources

LEGACY_GROUPS = ('receive', 'ground_pass', 'loft_pass', 'roll', 'bounce')
STOCK_SPIN = (1.50, 0.70)
WORKSHOP_SPIN = (1.75, 0.50)
NUMBERS = {
    'spin_factor': (1.00, 1.75, 0.05, STOCK_SPIN[0]),
    'spin_threshold': (0.50, 1.00, 0.05, STOCK_SPIN[1]),
}
FIELDS = {
    'receive_reaction': ('trap.o/reactionTrapBall.reactionOwnBall.groundBall.paraMin.reactionTime', 0.05, 0.65),
    'stop_input': ('trap.o/stopTrap.inputTime', 0.15, 0.85),
    'trap_cancel_frame': ('trap.o/trapCutFrame.default', 2, 14),
    'open_body': ('passget.o/openBodyMaxValue', 20, 75),
    'ground_speed_min': ('grounderpass.o/paraMinSpeedRate', 0.65, 1.35),
    'ground_speed_max': ('grounderpass.o/paraMaxSpeedRate', 0.65, 1.35),
    'loft_gauge_distance': ('flypass.o/manual.distForGageMax', 35, 90),
    'cross_speed_adjustment': ('centering.o/adjustSpeed', 0, 30),
    'magnus': ('ball.o/magnusRate', 0.020, 0.045),
    'natural_roll': ('ball.o/naturalRoll', 0.8, 4.0),
    'stop_speed': ('ball.o/stopSpeed', 0.05, 1.5),
    'bounce_rate_0': ('ball.o/boundRate[0]', 0.35, 0.95),
    'bounce_friction_speed': ('ball.o/boundFrictionSpeedMax', 15, 40),
}
SWITCHES = ('response', 'ai')


@cache
def field_definitions():
    path = bundled_resources() / 'gameplay_v8' / 'profile.json'
    profile = json.loads(path.read_text('utf8'))
    definitions = {
        field['object'] + '/' + field['field']: field
        for member in profile['constants'].values() for field in member['fields']
    }
    return {name: definitions[path] for name, (path, _, _) in FIELDS.items()}


def field_format(field):
    return '<i' if field['type'] == 'int32' else '<f'


def number_from_hex(field, raw):
    number, = struct.unpack(field_format(field), bytes.fromhex(raw))
    return float(f'{number:.7g}') if type(number) is float else number


def field_value(custom, name, field):
    if name in custom:
        return custom[name]
    return number_from_hex(field, legacy_constant(field, custom.get('legacy_groups', {})))


def normalize(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('比赛手感设置格式无效')
    mode = value.get('mode', 'original')
    if mode not in ('original', 'workshop', 'custom'):
        raise ValueError('比赛手感档位无效')
    custom = value.get('custom', {})
    if not isinstance(custom, dict):
        raise ValueError('自定义手感设置格式无效')
    result = {}
    for name, (minimum, maximum, step, default) in NUMBERS.items():
        number = custom.get(name, default)
        if type(number) not in (int, float) or not minimum <= number <= maximum:
            raise ValueError('比赛手感数值超出范围')
        rounded = round(number / step) * step
        if not math.isclose(rounded, number, rel_tol=0, abs_tol=1e-9):
            raise ValueError('比赛手感数值步长无效')
        result[name] = int(rounded) if step == 1 else round(rounded, 3)
    for name in SWITCHES:
        result[name] = custom.get(name, False)
        if type(result[name]) is not bool:
            raise ValueError('比赛手感开关无效')
    legacy = custom.get('legacy_groups', custom)
    if not isinstance(legacy, dict):
        raise ValueError('旧版比赛手感设置格式无效')
    for name in LEGACY_GROUPS:
        amount = legacy.get(name, 0)
        if type(amount) not in (int, float) or not 0 <= amount <= 100 or amount != int(amount):
            raise ValueError('旧版比赛手感比例无效')
        if amount:
            result.setdefault('legacy_groups', {})[name] = int(amount)
    definitions = field_definitions()
    for name, (_, minimum, maximum) in FIELDS.items():
        if name not in custom:
            continue
        number = custom[name]
        if type(number) not in (int, float) or not minimum <= number <= maximum:
            raise ValueError('比赛手感参数超出可调范围：' + name)
        if definitions[name]['type'] == 'int32':
            if number != int(number):
                raise ValueError('停球动作取消帧须为整数')
            number = int(number)
        result[name] = number
    if field_value(result, 'ground_speed_min', definitions['ground_speed_min']) > field_value(
            result, 'ground_speed_max', definitions['ground_speed_max']):
        raise ValueError('地面传球速度下限须小于或等于上限')
    return {'mode': mode, 'custom': result}


def settings(store, value=None):
    path = store.root / 'match-feel.json'
    with store.lock:
        if value is None:
            return normalize(json.loads(path.read_text('utf8')) if path.is_file() else None)
        result = normalize(value)
        atomic_json(path, result)
        return result


def effective(value):
    mode, custom = value['mode'], value['custom']
    if mode == 'custom':
        definitions = field_definitions()
        constants = {
            path: struct.pack(field_format(definitions[name]), custom[name]).hex()
            for name, (path, _, _) in FIELDS.items() if name in custom
        }
        return {
            **custom, 'mode': mode, 'constants': constants,
            'legacy_groups': custom.get('legacy_groups', {}),
            'magnus': field_value(custom, 'magnus', definitions['magnus']),
        }
    factor, threshold = STOCK_SPIN if mode == 'original' else WORKSHOP_SPIN
    return {
        'mode': mode, 'spin_factor': factor, 'spin_threshold': threshold,
        'response': mode == 'workshop', 'ai': mode == 'workshop',
        'magnus': 0.035 if mode == 'original' else 0.020,
    }


def fields_for_ui(value):
    custom = value['custom']
    rows = {}
    for name, field in field_definitions().items():
        _, minimum, maximum = FIELDS[name]
        rows[name] = {
            'min': minimum, 'max': maximum, 'step': 1 if field['type'] == 'int32' else 'any',
            'original': number_from_hex(field, field['before']),
            'workshop': number_from_hex(field, field['after']),
            'current': field_value(custom, name, field),
        }
    for (name, (minimum, maximum, step, default)), reference in zip(NUMBERS.items(), WORKSHOP_SPIN):
        rows[name] = {'min': minimum, 'max': maximum, 'step': step,
                      'original': default, 'workshop': reference, 'current': custom[name]}
    return rows
