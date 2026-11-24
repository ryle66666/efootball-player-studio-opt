"""V17 native possession, independent Lone Wolf and owned shooting skills."""
import copy
import json
import struct
from studio_resources import bundled_resources

REVISION='v17-native-possession-lone-wolf-v17'


def hooks(engine,ordinary_enabled=True,require_v8=False,*,include_v8=True):
    data=json.loads((bundled_resources()/'native-ai-cut-in-shots.json').read_text(encoding='utf8'))
    if data.get('revision')!=REVISION or not data.get('production_ready'):
        raise ValueError('AI 技能适配资料版本不匹配')
    v8_only=not ordinary_enabled and bool(data.get('v8_bundles'))
    result=copy.deepcopy(data['v8_bundles']if v8_only else data['bundles'])
    if require_v8 and not all(bundle.get('v8_native')for bundle in result):
        raise ValueError('V8 比赛适配资料不完整，请更新工坊后重新生成')
    if not v8_only:
        body=bytearray.fromhex(result[0]['body_hex']);offset=data['ordinary_switch_offset']
        if struct.unpack_from('<I',body,offset)[0]!=0x58414931:raise ValueError('AI 机会比较配置接口无效')
        struct.pack_into('<I',body,offset,int(bool(ordinary_enabled)))
        result[0]['body_hex']=body.hex()
    if not include_v8:
        if v8_only:
            raise ValueError('当前比赛手感只支持独狼与独立射门适配')
        for bundle in result:
            bundle['extra_entries']=[entry for entry in bundle['extra_entries'] if not entry['name'].startswith('studio_v8_')]
            bundle.pop('v8_native',None)
    entries=[entry for bundle in result for entry in
        [bundle]+bundle.get('extra_entries',[])+bundle.get('v8_native',{}).get('imports',[])]+data['probes']
    for entry in entries:
        engine.seek(entry['file_offset']);expected=bytes.fromhex(entry['expected_hex'])
        if engine.read(len(expected))!=expected:
            raise ValueError('此版本的 AI 技能接口不同，请检查游戏版本后再生成')
    for bundle in result:
        for key in ('assembly','code','functions','constants','helper_entries','deferred_shared_refs','writable','evidence'):
            bundle.pop(key,None)
    return result
