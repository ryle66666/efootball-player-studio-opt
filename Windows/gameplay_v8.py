"""Merge the shared V8 match data into an existing Android game archive."""
import json
from pathlib import Path
import struct
import zlib

from native_cpk import Cpk, wesys_crypt

REVISION = 'android-v8-ground-pass-20261114-1'
GROUND_PASS_REVISION = 'android-ground-pass-20261114-1'
CONSTANTS = 'assets/dt270_mobile_all.cpk'
ANIMATIONS = 'assets/dt230_mobile_all.cpk'


def configure_ground_pass(profile):
    restored = []
    for name, definition in profile['constants'].items():
        for field in definition['fields']:
            selected = (field['object'] == 'ball.o' and (
                field['field'].startswith(('boundBoundSpeed', 'boundRate[', 'grounder',
                                           'natualBound', 'rollToBound', 'boundCheckBoundAddRate['))
                or field['field'] == 'boundRollMax')) or (
                    field['object'] == 'grounderpass.o' and field['field'].startswith('angleY.'))
            if not selected:
                continue
            sources = field.setdefault('source_values', [])
            if field['after'] != field['before'] and field['after'] not in sources:
                sources.append(field['after'])
            field['after'] = field['before']
            restored.append(name + ':' + field['object'] + '/' + field['field'])
    profile['ground_pass'] = {'revision': GROUND_PASS_REVISION,
                              'release_pitch': 'native', 'release_spin': 'native',
                              'shared_ball_bounce': 'native', 'restored_fields': restored}
    return profile


def unpack(raw):
    if raw[:1] != b'\xff' or raw[3:8] != b'WESYS':
        return bytes(raw)
    if len(raw) < 16:
        raise ValueError('比赛数据封装头不完整')
    size, original = struct.unpack_from('<II', raw, 8)
    if len(raw) < 16 + size or any(raw[16 + size:]):
        raise ValueError('比赛数据封装长度无效')
    payload = raw[16:16 + size]
    if raw[2] & 2:
        payload = wesys_crypt(payload, raw[1] & 15, size, original)
    if raw[2] & 1:
        payload = zlib.decompress(payload[2:], -15)
    if len(payload) != original:
        raise ValueError('比赛数据解压长度不一致')
    return bytes(payload)


def pack(raw, template):
    if len(template) < 16 or template[:1] != b'\xff' or template[3:8] != b'WESYS':
        raise ValueError('比赛数据缺少原生封装')
    key = template[1] & 15
    encrypted = bool(template[2] & 2)
    payload = wesys_crypt(raw, key, len(raw), len(raw)) if encrypted else raw
    flags = template[2] & 0xfc | (2 if encrypted else 0)
    result = struct.pack('<BBB5sII', 255, template[1], flags, b'WESYS', len(raw), len(raw)) + payload
    if unpack(result) != raw:
        raise ValueError('比赛数据封装读回不一致')
    return result


def sections(raw):
    if len(raw) < 8:
        raise ValueError('比赛常量对象表不完整')
    count, table = struct.unpack_from('<II', raw)
    if not 0 < count < 10000 or table < 8 or table + count * 12 > len(raw):
        raise ValueError('比赛常量对象目录无效')
    result = {}
    for i in range(count):
        offset, length, name_at = struct.unpack_from('<III', raw, table + i * 12)
        if offset + length > len(raw) or name_at >= len(raw):
            raise ValueError('比赛常量对象超出文件范围')
        end = raw.find(b'\0', name_at)
        if end < 0:
            raise ValueError('比赛常量对象名称不完整')
        name = raw[name_at:end].decode('ascii')
        if not name.endswith('.o') or name in result:
            raise ValueError('比赛常量对象名称无效或重复')
        result[name] = (offset, length)
    return result


def legacy_constant(field, groups):
    name, obj = field['field'], field['object']
    before, after = field['before'], field['after']
    if obj == 'ball.o':
        if name.startswith(('frictionRoll', 'naturalRoll', 'natunalRoll', 'roll', 'stopSpeed')):
            group = 'roll'
        elif name.startswith(('bound', 'natualBound', 'grounder', 'overRot')):
            group = 'bounce'
        else:
            return before
    else:
        group = {
            'trap.o': 'receive', 'passget.o': 'receive', 'grounderpass.o': 'ground_pass',
            'flypass.o': 'loft_pass', 'centering.o': 'loft_pass',
        }.get(obj)
    amount = groups.get(group, 0)
    if amount == 0:
        return before
    if amount == 100:
        return after
    if field['type'] == 'bool8':
        return after if amount >= 50 else before
    fmt = '<f' if field['type'] == 'float32' else '<i'
    start, = struct.unpack(fmt, bytes.fromhex(before))
    end, = struct.unpack(fmt, bytes.fromhex(after))
    value = start + (end - start) * amount / 100
    return struct.pack(fmt, round(value) if fmt == '<i' else value).hex()


def custom_constant(field, feel):
    if feel['mode'] == 'original':
        return field['before']
    key = field['object'] + '/' + field['field']
    if key in feel['constants']:
        return feel['constants'][key]
    return legacy_constant(field, feel['legacy_groups'])


def merge_constants(raw, definition, feel=None):
    layout = sections(raw)
    if {name: list(value) for name, value in layout.items()} != definition['objects']:
        raise ValueError('此来源的比赛常量结构与 V17 不同')
    output = bytearray(raw)
    report = {'applied': [], 'already_present': [], 'retained_custom_values': []}
    occupied = set()
    override = feel is not None
    for field in definition['fields']:
        offset, size = layout[field['object']]
        relative = field['object_offset']
        before = bytes.fromhex(field['before'])
        target = custom_constant(field, feel) if override and feel['mode'] != 'workshop' else field['after']
        after = bytes.fromhex(target)
        sources = [bytes.fromhex(value) for value in field.get('source_values', [])]
        if len(before) != len(after) or not before or relative < 0 or relative + len(before) > size:
            raise ValueError('比赛常量字段范围无效')
        if any(len(value) != len(before) for value in sources):
            raise ValueError('比赛常量来源字段长度无效')
        at = offset + relative
        span = set(range(at, at + len(before)))
        if span & occupied:
            raise ValueError('比赛常量字段重复')
        occupied.update(span)
        existing = raw[at:at + len(before)]
        label = field['object'] + '/' + field['field']
        if existing == after:
            report['already_present'].append(label)
        elif override or existing == before or existing in sources:
            output[at:at + len(after)] = after
            report['applied'].append(label)
        else:
            report['retained_custom_values'].append(label)
    return bytes(output), report


def merge_references(raw, definition, restore=False):
    width = definition['record_bytes']
    if width != 12 or len(raw) < definition['minimum_bytes'] or len(raw) % width:
        raise ValueError('此来源的动作记录结构与 V17 不同')
    mask = definition['mask']
    output = bytearray(raw)
    report = {'applied': [], 'already_present': [], 'retained_custom_values': []}
    for row in definition['records']:
        at = row['record'] * width
        if not 0 <= at <= len(raw) - width:
            raise ValueError('动作记录编号超出范围')
        existing = struct.unpack_from('<I', raw, at)[0]
        current, before, after = existing & mask, row['before'] & mask, row['after'] & mask
        if restore:
            after = before
        if current == after:
            report['already_present'].append(row['record'])
        elif restore or current == before:
            struct.pack_into('<I', output, at, existing & ~mask | after)
            report['applied'].append(row['record'])
        else:
            report['retained_custom_values'].append(row['record'])
    return bytes(output), report


def motions(raw):
    if len(raw) < 32:
        raise ValueError('身体动画目录不完整')
    signature, count = struct.unpack_from('<II', raw)
    if signature != 0x0bffa89c or not count or 32 + count * 16 > len(raw):
        raise ValueError('身体动画目录无效')
    rows = []
    for i in range(count):
        record = raw[32 + i * 16:48 + i * 16]
        ident, offset, size = struct.unpack_from('<iII', record, 4)
        if offset < 32 + count * 16 or size == 0 or offset + size > len(raw):
            raise ValueError('身体动画片段超出文件范围')
        rows.append((ident, record, raw[offset:offset + size]))
    keys = [struct.unpack_from('<Q', row[1])[0] for row in rows]
    if keys != sorted(keys) or len(set(keys)) != count:
        raise ValueError('身体动画编号重复或未按原生顺序排列')
    return rows


def merge_motions(raw, definition, assets, restore=False):
    rows = motions(raw)
    if raw[8:32].hex() != definition['skeleton']:
        raise ValueError('身体动画骨架与通道配置不同')
    existing = {struct.unpack_from('<Q',record)[0]:(record,payload)for ident,record,payload in rows}
    additions = []
    present = []
    for item in definition['motions']:
        record = bytes.fromhex(item['record'])
        payload = (assets / item['file']).read_bytes()
        ident = item['id']
        if len(record) != 16 or struct.unpack_from('<i', record, 4)[0] != ident:
            raise ValueError('新增身体动画编号无效')
        key=struct.unpack_from('<Q',record)[0]
        if key in existing:
            old_record,old_payload=existing[key]
            if old_record[:8] != record[:8] or old_record[12:] != record[12:] or old_payload != payload:
                raise ValueError('新增身体动画编号与此来源的已有动作冲突')
            present.append(ident)
            if restore:
                del existing[key]
        elif not restore:
            additions.append((ident, record, payload))
            existing[key] = (record, payload)
    if restore:
        if not present:
            return raw, {'removed': [], 'retained_clips': len(rows)}
        count = len(existing)
        output = bytearray(raw)
        struct.pack_into('<I', output, 4, count)
        output[32:32 + 16 * len(rows)] = b''.join(record for record, _ in existing.values()) + bytes(16 * len(present))
        actual = {struct.unpack_from('<Q', record)[0]: (record, payload) for _, record, payload in motions(output)}
        if actual != existing:
            raise ValueError('身体动画恢复读回不一致')
        return bytes(output), {'removed': present, 'retained_clips': count}
    if not additions:
        return raw, {'added': [], 'already_present': present, 'retained_clips': len(rows)}
    count = len(rows) + len(additions)
    first = min(struct.unpack_from('<I', row[1], 8)[0] for row in rows)
    shift = (max(0, 32 + count * 16 - first) + 15) & ~15
    output = bytearray(raw[:first] + b'\0' * shift + raw[first:])
    records = []
    for ident, original, payload in rows:
        record = bytearray(original)
        offset = struct.unpack_from('<I', record, 8)[0]
        struct.pack_into('<I', record, 8, offset + shift)
        records.append(record)
    for ident, original, payload in additions:
        output.extend(b'\0' * (-len(output) % 16))
        record = bytearray(original)
        struct.pack_into('<II', record, 8, len(output), len(payload))
        output.extend(payload)
        records.append(record)
    records.sort(key=lambda row: struct.unpack_from('<Q', row)[0])
    struct.pack_into('<I', output, 4, count)
    for i, record in enumerate(records):
        output[32 + i * 16:48 + i * 16] = record
    actual={struct.unpack_from('<Q',record)[0]:(record,payload)for ident,record,payload in motions(output)}
    for ident, original, payload in rows + additions:
        record,recovered=actual[struct.unpack_from('<Q',original)[0]]
        if recovered != payload or record[:8] != original[:8] or record[12:] != original[12:]:
            raise ValueError('身体动画合并读回不一致')
    return bytes(output), {'added': [row[0] for row in additions], 'already_present': present,
                           'retained_clips': len(rows), 'clips': count}


def replace_file(archive, replacements, target):
    """Write one archive with bounded copies, preserving every unselected member."""
    if archive.header.rows[0].get('EnableTocCrc') or archive.header.rows[0].get('EnableFileCrc'):
        raise ValueError('比赛数据容器使用了不支持的目录验证格式')
    original_rows = {name: dict(archive.toc.rows[index]) for name, index in archive.files.items()}

    def stored(container, row):
        start = container.base + row['FileOffset']
        end = start + row['FileSize']
        if start < 0 or end < start or end > len(container.raw):
            raise ValueError('比赛数据容器成员超出文件范围')
        return memoryview(container.raw)[start:end]

    with target.open('wb') as out:
        view = memoryview(archive.raw)
        for at in range(0, len(view), 1024 * 1024):
            out.write(view[at:at + 1024 * 1024])
    with target.open('r+b') as out:
        for name, value in replacements.items():
            index = archive.files[name]
            row = archive.toc.rows[index]
            if len(value) <= row['FileSize']:
                out.seek(archive.base + row['FileOffset'])
                out.write(value)
                out.write(b'\0' * (row['FileSize'] - len(value)))
            else:
                out.seek(0, 2)
                out.write(b'\0' * (-out.tell() % archive.align))
                at = out.tell()
                out.write(value)
                out.write(b'\0' * (-out.tell() % archive.align))
                archive.toc.set(index, 'FileOffset', at - archive.base)
            archive.toc.set(index, 'FileSize', len(value))
            archive.toc.set(index, 'ExtractSize', len(value))
        out.seek(0, 2)
        archive.header.set(0, 'ContentSize', out.tell() - archive.header.rows[0]['ContentOffset'])
        out.seek(archive.header_at)
        out.write(archive.header.bytes())
        out.seek(archive.toc_at)
        out.write(archive.toc.bytes())
    check = Cpk(target.read_bytes())
    if set(check.files) != set(archive.files):
        raise ValueError('比赛数据容器目录改变')
    for name in archive.files:
        if name in replacements:
            equal = check.read(name) == replacements[name]
        else:
            original = original_rows[name]
            current = check.toc.rows[check.files[name]]
            equal = current == original and stored(check, current) == stored(archive, original)
        if not equal:
            raise ValueError('比赛数据容器成员读回不一致：' + name)


def prepare(source, resources, work, progress=None, *, feel=None):
    folder = Path(resources) / 'gameplay_v8'
    profile = json.loads((folder / 'profile.json').read_text('utf8'))
    if profile['revision'] != REVISION:
        raise ValueError('游戏性资源版本不匹配')
    replacements = {}
    report = {'revision': REVISION, 'constants': {}, 'animations': {}, 'face_sequences': 'preserved',
              'ground_pass': profile['ground_pass']}
    from export_payload import FilePayload
    restore = feel is not None and feel['mode'] != 'workshop'
    for name, definitions, label in ((CONSTANTS, profile['constants'], '写入比赛手感常量'),
                                      (ANIMATIONS, profile['animations'], '核对动作取消时机与身体动画')):
        if progress:
            progress(message=label)
        archive = Cpk(source.read(name, limit=None))
        changed = {}
        for member, definition in definitions.items():
            wrapped = archive.read(member)
            raw = unpack(wrapped)
            if name == CONSTANTS:
                result, detail = merge_constants(raw, definition, feel)
            elif definition['kind'] == 'references':
                result, detail = merge_references(raw, definition, restore)
            else:
                result, detail = merge_motions(raw, definition, folder, restore)
            report['constants' if name == CONSTANTS else 'animations'][member] = detail
            if result != raw:
                changed[member] = pack(result, wrapped)
        if changed:
            target = Path(work) / Path(name).name
            replace_file(archive, changed, target)
            replacements[name] = FilePayload.from_path(target)
        del archive, changed
    return replacements, report
