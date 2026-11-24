"""Apply independently installable records to the V17 native player archive."""
import csv
import io
import struct

from native_cpk import Cpk, unwrap, wrap
import native_strings

PLAYER = 'common/etc/pesdb/Player.bin'
APPEARANCE = 'common/etc/appearance/PlayerAppearance.bin'
VARIATION = 'common/etc/pesdb/PlayerVariationDetail.bin'
BOOTS = 'common/etc/appearance/BootsList.bin'
BOOSTERS = 'common/etc/pesdb/PlayerBooster.bin'
WEEKLY = 'common/etc/pesdb/PlayerWeekly.bin'
ALIASES = 'common/etc/playercard/filenameConvTable.csv'
LAYOUTS = {PLAYER: (400, 8, 8), APPEARANCE: (64, 0, 8), VARIATION: (168, 0, 8),
           BOOTS: (16, 0, 8), BOOSTERS: (40, 14, 2)}


def record_key(name, row):
    width, offset, count = LAYOUTS[name]
    if len(row) != width:
        raise ValueError('球员记录长度无效')
    value = int.from_bytes(row[offset:offset+count], 'little')
    return value & 1023 if name == BOOSTERS else value


def rows(name, raw):
    width = LAYOUTS[name][0]
    if len(raw) % width:
        raise ValueError('球员资料表长度无效')
    values = [raw[i:i+width] for i in range(0, len(raw), width)]
    result = {record_key(name, row): row for row in values}
    if len(result) != len(values):
        raise ValueError('球员资料表编号重复')
    return result


def aliases(raw):
    values = list(csv.reader(io.StringIO(raw.decode('utf8'))))
    if not values or values[0] != ['before', 'after'] or not all(len(row) == 2 for row in values):
        raise ValueError('卡面别名表格式不支持')
    result = dict(values[1:])
    if len(result) != len(values)-1:
        raise ValueError('卡面别名重复')
    return result


def weekly(raw):
    result = {}
    for card, _, grade in struct.iter_unpack('<QII', raw):
        result.setdefault(card, []).append(grade)
    return result


def alias_keys(player_ids, *, slots=False):
    keys = {f'{card}{suffix}' for card in player_ids for suffix in ('', '_', '_l', 'f')}
    if slots:
        for card in player_ids:
            style = (card >> 40) & 255
            decoration = f'{style // 16:02}{style % 16:02}'
            keys.update(f'{decoration}_{slot}_{card}_' for slot in ('f04', 'f06', 'f09'))
    return keys


def added(before, after, label):
    if any(after.get(key) != value for key, value in before.items()):
        raise ValueError('本次还改动了已有资料，需要完整 APK 导出：' + label)
    return {key: value for key, value in after.items() if key not in before}


def player_changes(before, after, allowed, label, snapshot):
    if not snapshot:
        return added(before, after, label)
    outside = lambda values: {key: value for key, value in values.items() if key not in allowed}
    if outside(before) != outside(after):
        raise ValueError('本次还改动了其它球员的资料：' + label)
    return {key: value for key, value in after.items() if key in allowed}


def changes(source, result, player_ids, booster_ids, *, snapshot=False):
    """Check that the compiler changed only the selected players' records."""
    before, after = Cpk(source), Cpk(result)
    if before.files.keys() != after.files.keys():
        raise ValueError('原生文件目录变化，需要完整 APK 导出')
    delta = {}
    for name in before.files:
        old, new = before.read(name), after.read(name)
        player_table = name in (PLAYER, APPEARANCE, VARIATION, BOOTS, ALIASES, WEEKLY)
        if old == new and not (snapshot and player_table):
            continue
        if name in LAYOUTS:
            allowed = booster_ids if name == BOOSTERS else player_ids
            values = player_changes(rows(name, unwrap(old)), rows(name, unwrap(new)),
                                    allowed, name, snapshot and player_table)
            if values.keys() - allowed:
                raise ValueError('球员资料包含其它球员的变动：' + name)
            delta[name] = [[key, value.hex()] for key, value in values.items()]
        elif name == ALIASES:
            allowed = alias_keys(player_ids, slots=snapshot)
            values = player_changes(aliases(unwrap(old)), aliases(unwrap(new)), allowed, name, snapshot)
            if values.keys() - allowed:
                raise ValueError('本次改动使用共用卡面别名，需要完整 APK 导出')
            delta[name] = list(values.items())
        elif name == WEEKLY:
            values = player_changes(weekly(unwrap(old)), weekly(unwrap(new)), player_ids, name, snapshot)
            if values.keys() - player_ids or any(len(grades) != 1 for grades in values.values()):
                raise ValueError('球员实时状态的编号不一致')
            delta[name] = [[key, grades[0]] for key, grades in values.items()]
        elif name.endswith('/string/all_diff.str'):
            old_groups = native_strings.read(native_strings.unwrap(old))
            new_groups = native_strings.read(native_strings.unwrap(new))
            old_map = {(r['name'], r['id']): r['items'] for r in old_groups}
            new_map = {(r['name'], r['id']): r['items'] for r in new_groups}
            if old_map.keys() - new_map.keys():
                raise ValueError('共用游戏文字变化，需要完整 APK 导出')
            additions = []
            for group, items in new_map.items():
                texts = added(old_map.get(group, {}), items, name)
                if texts or group not in old_map:
                    additions.append([*group, list(texts.items())])
            delta[name] = additions
        else:
            raise ValueError('本次同时改动了共用游戏资料，需要完整 APK 导出：' + name)
    return delta


def add_values(current, additions, label):
    for key, value in additions.items():
        if key in current and current[key] != value:
            raise ValueError('已安装资料与小包冲突：' + label + ' / ' + str(key))
    changed = additions.keys() - current.keys()
    current.update(additions)
    return changed


def merge_values(current, incoming, label, replace):
    changed = add_values(current, {k: v for k, v in incoming.items() if k not in replace}, label)
    for key in current.keys() & replace:
        if key not in incoming:
            del current[key]
            changed.add(key)
    for key in incoming.keys() & replace:
        if current.get(key) != incoming[key]:
            current[key] = incoming[key]
            changed.add(key)
    return changed


def apply(raw, delta, player_ids, booster_ids, *, layout=None, replace_ids=frozenset(), snapshot=False):
    cpk = Cpk(raw)
    replaced = {}
    for name, operations in delta.items():
        if name not in cpk.files or not isinstance(operations, list):
            raise ValueError('小包的原生资料表无效')
        original = cpk.read(name)
        if name in LAYOUTS:
            additions = {key: bytes.fromhex(row) for key, row in operations}
            allowed = booster_ids if name == BOOSTERS else player_ids
            if (len(additions) != len(operations) or additions.keys() - allowed
                    or any(type(key) is not int or record_key(name, value) != key for key, value in additions.items())):
                raise ValueError('小包球员资料编号无效')
            values = rows(name, unwrap(original))
            replace = frozenset() if name == BOOSTERS else replace_ids
            if merge_values(values, additions, name, replace):
                order = values if name == VARIATION else sorted(values)
                replaced[name] = wrap(b''.join(values[key] for key in order), original)
        elif name == ALIASES:
            additions = dict(operations)
            allowed = alias_keys(player_ids, slots=snapshot)
            if len(additions) != len(operations) or additions.keys() - allowed:
                raise ValueError('小包卡面别名无效')
            values = aliases(unwrap(original))
            replace = alias_keys(replace_ids, slots=snapshot)
            if merge_values(values, additions, name, replace):
                output = io.StringIO(newline='')
                writer = csv.writer(output, lineterminator='\r\n')
                writer.writerow(['before', 'after'])
                writer.writerows(sorted(values.items()))
                replaced[name] = wrap(output.getvalue().encode(), original)
        elif name == WEEKLY:
            additions = {key: [grade] for key, grade in operations}
            if (len(additions) != len(operations) or additions.keys() - player_ids
                    or any(type(value[0]) is not int or not 0 <= value[0] <= 4 for value in additions.values())):
                raise ValueError('小包球员状态档位无效')
            original_data = unwrap(original)
            values = weekly(original_data)
            changed = merge_values(values, additions, name, replace_ids)
            sequence = max((number for _, number, _ in struct.iter_unpack('<QII', original_data)), default=0)
            if changed:
                data = b''.join(struct.pack('<QII', *row) for row in struct.iter_unpack('<QII', original_data)
                                if row[0] not in changed)
                data += b''.join(struct.pack('<QII', key, number, values[key][0])
                                 for number, key in enumerate(sorted(changed & values.keys()), sequence+1))
                replaced[name] = wrap(data, original)
        elif name.endswith('/string/all_diff.str'):
            groups = native_strings.read(native_strings.unwrap(original))
            indexed = {(row['name'], row['id']): row['items'] for row in groups}
            modified = False
            for label, ident, entries in operations:
                items = dict(entries)
                if len(items) != len(entries):
                    raise ValueError('小包游戏文字重复')
                from custom_boosters import text_key
                allowed = {text_key(bid)+offset for bid in booster_ids for offset in (0, 1)}
                if (label, ident) != ('Any3W', 1142) or items.keys() - allowed:
                    raise ValueError('小包增能文字编号无效')
                if (label, ident) not in indexed:
                    values = {}; indexed[label, ident] = values
                    groups.append({'name': label, 'id': ident, 'items': values})
                modified |= bool(add_values(indexed[label, ident], items, name))
            if modified:
                replaced[name] = native_strings.wrap(native_strings.write(groups))
        else:
            raise ValueError('小包含有不支持的共用游戏资料')
    if not replaced:
        return raw
    if layout is None:
        return cpk.replace(replaced)
    # Rebuild from the same signed layout for every install. Appending a
    # larger table to the previous archive would retain obsolete copies.
    base = Cpk(layout)
    if base.files.keys() != cpk.files.keys():
        raise ValueError('已安装球员资料目录与基准 APK 不同')
    for name in cpk.files.keys() - replaced.keys():
        current = cpk.read(name)
        if current != base.read(name):
            replaced[name] = current
    return base.replace(replaced)
