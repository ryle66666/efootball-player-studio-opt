"""Declare the game's manual content-import activity and program identity."""
from pathlib import Path
import json
import struct

from android_manifest import Manifest, ANDROID, NONE

IMPORT = 'org.efootballrevival.runtimeprobe.ContentImportActivity'
GATE = 'org.efootballrevival.runtimeprobe.ProgramGateActivity'
MARKER = 'assets/player-studio/program.json'
ATTRIBUTES = {'name': 0x01010003, 'exported': 0x01010010, 'mimeType': 0x01010026}


def program_manifest(raw):
    document = Manifest(raw)
    nodes = {n: (tag, attrs) for n, tag, attrs in document.nodes()}
    result = []
    skip = 0
    for n, part in enumerate(document.chunks):
        kind = struct.unpack_from('<H', part)[0]
        if kind == 0x102:
            tag, attrs = nodes[n]
            if skip or tag == 'queries':
                skip += 1
                continue
            result.append(('start', tag, tuple(sorted((a['namespace'] or '', a['name'], a['type'], a['value']) for a in attrs))))
        elif kind == 0x103:
            if skip:
                skip -= 1
                continue
            result.append(('end', document.string(struct.unpack_from('<I', part, 20)[0])))
    return result


def _manifest(raw, previous=None):
    document = Manifest(raw)
    nodes = {n: (tag, attrs) for n, tag, attrs in document.nodes()}
    stack=[];components=[];launchers=[];application_end=None
    for n,part in enumerate(document.chunks):
        kind=struct.unpack_from('<H',part)[0]
        if kind==0x102:
            tag,attrs=nodes[n];values={a['name']:a['value']for a in attrs}
            stack.append({'tag':tag,'start':n,'values':values,'actions':set(),'categories':set()})
            if tag in('action','category'):
                parent=next((s for s in reversed(stack[:-1])if s['tag']=='intent-filter'),None)
                if parent:parent['actions'if tag=='action'else'categories'].add(values.get('name'))
        elif kind==0x103:
            node=stack.pop()
            if node['tag']=='application':application_end=n
            if node['tag']in('activity','activity-alias','provider'):components.append(dict(node,end=n))
            if node['tag']=='intent-filter'and'android.intent.action.MAIN'in node['actions']and'android.intent.category.LAUNCHER'in node['categories']:
                parent=next(s for s in reversed(stack)if s['tag']in('activity','activity-alias'))
                launchers.append((node['start'],n,parent['values']))
    if application_end is None or len(launchers)!=1:raise ValueError('游戏必须具有唯一启动入口')
    begin,end,owner=launchers[0]
    package=document.info()['package']
    def absolute(value):
        if not isinstance(value,str):return None
        return package+value if value.startswith('.')else package+'.'+value if'.'not in value else value
    entry=absolute(owner.get('targetActivity',owner.get('name')))
    if entry==GATE:entry=(previous or{}).get('entry_activity')
    launcher_filter=document.chunks[begin:end+1]
    discard=set(range(begin,end+1))
    for component in components:
        if absolute(component['values'].get('name'))in(GATE,IMPORT):
            discard.update(range(component['start'],component['end']+1))
    resource_ids = {}

    def start(tag, attrs=()):
        attrs = sorted(attrs, key=lambda row: ATTRIBUTES[row[0]])
        row = bytearray(struct.pack('<HHIII', 0x102, 16, 36 + 20 * len(attrs), 0, NONE)
                        + struct.pack('<IIHHHHHH', NONE, document.index(tag), 20, 20, len(attrs), 0, 0, 0))
        for name, value in attrs:
            key = document.index(name)
            resource_ids[key] = ATTRIBUTES[name]
            boolean = isinstance(value, bool)
            data = int(value) if boolean else document.index(value)
            row.extend(struct.pack('<IIIHBBI', document.index(ANDROID), key, NONE if boolean else data, 8, 0, 18 if boolean else 3, data))
        return row

    def close(tag):
        return bytearray(struct.pack('<HHIIIII', 0x103, 16, 24, 0, NONE, NONE, document.index(tag)))

    additions = [start('activity', [('name', GATE), ('exported', True)]), *launcher_filter, close('activity'),
                 start('activity', [('name', IMPORT), ('exported', True)])]
    for mime in ('application/x-73pack', 'application/octet-stream'):
        additions.extend([start('intent-filter'), start('action', [('name', 'android.intent.action.VIEW')]), close('action'),
                          start('category', [('name', 'android.intent.category.DEFAULT')]), close('category'),
                          start('data', [('mimeType', mime)]), close('data'), close('intent-filter')])
    additions.append(close('activity'))
    result = []
    for n, part in enumerate(document.chunks):
        if n == application_end:
            result.extend(additions)
        if n not in discard:
            result.append(part)
    document.chunks = result
    resource = next((n for n, part in enumerate(result) if struct.unpack_from('<H', part)[0] == 0x180), None)
    values = list(struct.unpack('<' + 'I' * ((len(result[resource]) - 8) // 4), result[resource][8:])) if resource is not None else []
    values.extend([0] * max(0, max(resource_ids) + 1 - len(values)))
    for key, value in resource_ids.items():
        values[key] = value
    encoded = bytearray(struct.pack('<HHI', 0x180, 8, 8 + 4 * len(values)) + struct.pack('<' + 'I' * len(values), *values))
    if resource is None:
        document.chunks.insert(document.pool + 1, encoded)
    else:
        document.chunks[resource] = encoded
    return document.bytes(), entry



def manifest(raw, previous=None):
    return _manifest(raw,previous)[0]


def changes(source, replacements, resources, package, program_id, *, player_updates=False, player_append_engine=None,
            player_append_updates=False, game_version=None, experimental_movie_overlay=False):
    previous = json.loads(source.read(MARKER)) if MARKER in source.namelist() else None
    raw = replacements.get('AndroidManifest.xml')
    if raw is None:
        raw = source.read('AndroidManifest.xml')
    raw, entry_activity = _manifest(raw,previous)
    if not entry_activity or entry_activity in(GATE,IMPORT):raise ValueError('游戏程序缺少原始启动入口')
    dex = Path(resources) / 'native-content-import.dex'
    if not dex.is_file():
        raise ValueError('缺少内容包手动导入组件，请先构建工坊资源')
    entry = previous.get('dex_entry') if previous else None
    if not entry:
        ordinal = 2
        names = set(source.namelist()) | set(replacements)
        while 'classes' + str(ordinal) + '.dex' in names:
            ordinal += 1
        entry = 'classes' + str(ordinal) + '.dex'
    descriptor = {
        'protocol': '73-studio-program-v1', 'package': package, 'program_id': program_id,
        'dex_entry': entry, 'content_import': 'manual-v1', 'entry_activity': entry_activity, 'content_required':False, 'content_file':'73efootball-content.73pack'}
    if game_version:
        descriptor['game_version'] = str(game_version)
    if player_updates:
        descriptor['player_updates'] = 1
    if player_append_engine is not None:
        descriptor.update(player_append=1, player_append_engine=player_append_engine)
        if player_append_updates:
            descriptor['player_append_updates'] = 2
    if experimental_movie_overlay:
        from player_movie_overlay import CAPABILITY
        if game_version != 'V17' or descriptor.get('player_append_updates') != 2:
            raise ValueError('动态卡面实验需要支持独立球员更新的 V17 基准')
        descriptor[CAPABILITY] = 1
    from dex_identity import rewrite
    compiled, count = rewrite(dex.read_bytes(), {}, exact_strings={
        '内容包已导入，重新启动游戏后生效。原文件可以删除。':
        '内容包已接收。启动游戏后会检查内容并显示处理结果。',
    })
    if count != 1:
        raise ValueError('未找到可更新的内容包接收提示')
    replacements.update({'AndroidManifest.xml': raw, entry: compiled, MARKER: json.dumps(descriptor, separators=(',', ':')).encode('utf8')})
    return {'dex_entry': entry}
