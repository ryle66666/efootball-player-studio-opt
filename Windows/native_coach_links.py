"""Coach Link-up conditions. IDs in coach slots 2/3 are not boosters."""
from export_errors import call as _export_data
from export_errors import export_context, exported
import copy
import json
from pathlib import Path
from studio_resources import bundled_resources
from native_cpk import unwrap,wrap

TABLE='common/etc/pesdb/CoachLink.bin'
RESOURCE=bundled_resources()
CONDITION_REVISION='explicit-position-style-only-v2'
NAME_GROUP=1142
NAME_KEY_START=64000
TEMPLATE_SELECTOR_MASK=(1<<37)|(1<<46)


def schema(resources=None):
    return _export_data("资料文件",(Path(resources) if resources else RESOURCE) / 'native-coach-links.json',"读取并解析 JSON",lambda: json.loads(((Path(resources) if resources else RESOURCE)/'native-coach-links.json').read_text(encoding='utf8')))


def rows(raw):
    if len(raw)%8:raise ValueError('心有灵犀条件表长度改变')
    values={int.from_bytes(raw[i:i+8],'little')&1023:raw[i:i+8] for i in range(0,len(raw),8)}
    if len(values)!=len(raw)//8 or 0 in values:raise ValueError('心有灵犀条件编号无效或重复')
    return values


def decode(row,info):
    value=int.from_bytes(row,'little');result={'template_id':value&1023}
    for role in ('source','target'):
        result[role+'_positions']=[int(pos) for pos,bit in info['position_bits'][role].items() if value>>bit&1]
        result[role+'_style']=(value>>(10 if role=='source' else 16))&63
    return result


def normalize(value,info=None):
    info=info or schema()
    if value is None:return None
    if not isinstance(value,dict):raise ValueError('心有灵犀设置格式无效')
    ident=value.get('template_id')
    if type(ident) is not int or str(ident) not in info['effect_by_template']:
        raise ValueError('心有灵犀只能选择当前游戏已有的类型')
    result={'template_id':ident}
    name=value.get('name','')
    if not isinstance(name,str) or any(ord(c)<32 or ord(c)==127 for c in name):
        raise ValueError('自定义心有灵犀名称需要是不含控制字符的文字')
    name=name.strip()
    if len(name)>80:raise ValueError('自定义心有灵犀名称最多 80 个字')
    if name:result['name']=name
    for role in ('source','target'):
        positions=value.get(role+'_positions')
        if not isinstance(positions,list) or not positions or any(type(p) is not int or not 0<=p<=12 for p in positions):
            raise ValueError('核心球员和关键球员必须分别选择至少一个有效位置')
        style=value.get(role+'_style')
        if type(style) is not int or not 0<=style<=35:raise ValueError('心有灵犀比赛风格编号无效')
        result[role+'_positions']=sorted(set(positions));result[role+'_style']=style
    return result


def normalize_slots(value,info=None):
    if value is None:return [None,None]
    if not isinstance(value,list) or len(value)>2:raise ValueError('原生教练最多有两项心有灵犀')
    return [normalize(v,info) for v in value]+[None]*(2-len(value))


def encode(template,config,ident,info):
    config=normalize(config,info)
    if len(template)!=8:raise ValueError('心有灵犀模板记录长度改变')
    if type(ident)is not int or not 1<=ident<1024:raise ValueError('心有灵犀条件编号无效')
    # Only the explicit position/style fields belong to the editable condition.
    # In particular, do not copy the two template-only selectors (disk bits
    # 37/46), or unknown high flags, into a personal condition.
    value=ident
    for role,shift in (('source',10),('target',16)):
        value=(value&~(63<<shift))|(config[role+'_style']<<shift)
        for position,bit in info['position_bits'][role].items():
            value=(value&~(1<<bit))|(int(int(position) in config[role+'_positions'])<<bit)
    return value.to_bytes(8,'little')


def name_message(ident):
    if type(ident)is not int or not 256<=ident<512:raise ValueError('独立心有灵犀名称编号无效')
    return (NAME_GROUP<<16)|(NAME_KEY_START+ident-256)


def merge_names(cpk,routes,replace_ids=()):
    """Names use personal IDs/keys; never replace a stock template message."""
    names={NAME_KEY_START+row['id']-256:row['name'] for row in routes if row.get('name')}
    if not names:return {}
    from native_strings import merge_all,read,unwrap as unwrap_text
    result={};replace_keys={NAME_KEY_START+ident-256 for ident in replace_ids}
    for path in cpk.files:
        if not path.endswith('/string/all_diff.str'):continue
        content=cpk.read(path)
        existing=next((g for g in read(unwrap_text(content))if g['id']==NAME_GROUP),None)
        if existing and (existing['items'].keys()&names.keys())-replace_keys:
            raise ValueError('自定义心有灵犀名称编号与来源游戏文本冲突，未覆盖')
        result[path]=merge_all(content,updates=[{'name':'Any3W','id':NAME_GROUP,'items':names}])
    if not result:raise ValueError('来源游戏缺少原生语言文本，无法写入心有灵犀名称')
    return result


def compile_links(cpk,coaches,registry,info=None):
    info=info or schema()
    if TABLE not in cpk.files:
        if any(any(normalize_slots(coach.get('linkups'),info))for coach in coaches):
            raise ValueError('来源游戏缺少原生心有灵犀条件表，无法写入')
        return {},{coach['uuid']:[0,0]for coach in coaches},[]
    wrapped=cpk.read(TABLE);records=rows(unwrap(wrapped))
    assigned=registry.setdefault('links',{});by_coach={};routes=[]
    prior_ids=set(records)&set(assigned.values())
    # The two verified template-only filters are disabled for ALL stock
    # effects too. Their IDs, explicit positions/styles, text, effect mapping
    # and every other field stay untouched; renaming remains personal-only.
    for ident in records:
        if str(ident)in info['effect_by_template']:
            records[ident]=(int.from_bytes(records[ident],'little')&~TEMPLATE_SELECTOR_MASK).to_bytes(8,'little')
    # Previously exported personal records can be retained by incremental mode.
    # Keep their authored positions/styles, but remove inherited selectors too.
    explicit_mask=1023|(63<<10)|(63<<16)
    for bits in info['position_bits'].values():
        for bit in bits.values():explicit_mask|=1<<bit
    for ident in assigned.values():
        if type(ident)is int and 256<=ident<512 and ident in records:
            records[ident]=(int.from_bytes(records[ident],'little')&explicit_mask).to_bytes(8,'little')
    for coach in coaches:
        with export_context('教练',coach,'编译心有灵犀触发条件'):
            selected=[]
            for slot,config in enumerate(normalize_slots(coach.get('linkups'),info)):
                if config is None:selected.append(0);continue
                template=config['template_id']
                if template not in records:raise ValueError('原 APK 缺少所选心有灵犀类型')
                key=coach['uuid']+':'+str(slot)
                ident=assigned.get(key)
                if ident is None:
                    ident=next((v for v in range(256,512) if v not in records and v not in assigned.values()),None)
                    if ident is None:raise ValueError('本次 APK 的独立心有灵犀条件编号已用完')
                    assigned[key]=ident
                if type(ident) is not int or not 256<=ident<512:raise ValueError('独立心有灵犀编号无效')
                records[ident]=encode(records[template],config,ident,info)
                route={'id':ident,'template_id':template,'condition_revision':CONDITION_REVISION}
                if config.get('name'):route.update(name=config['name'],name_message=name_message(ident))
                routes.append(route);selected.append(ident)
            by_coach[coach['uuid']]=selected
    files={TABLE:wrap(b''.join(records[k] for k in sorted(records)),wrapped)}
    files.update(merge_names(cpk,routes,prior_ids))
    return files,by_coach,routes


def read_catalogue(cpk,coaches,resources,routes=()):
    info=schema(resources);records=rows(unwrap(cpk.read(TABLE)))
    route_by_id={r['id']:r for r in routes if str(r.get('template_id')) in info['effect_by_template']}
    for coach in coaches:
        ids=coach.get('ability_ids',[])[2:4]
        coach['linkups']=[]
        for ident in ids:
            config=decode(records[ident],info)if ident in records else None
            if config is not None and ident in route_by_id:
                route=route_by_id[ident];config['template_id']=route['template_id']
                if route.get('name'):config['name']=route['name']
            coach['linkups'].append(config)
        coach['linkups']+=(2-len(coach['linkups']))*[None]
    labels=info.get('template_labels',{})
    return [{**decode(row,info),'label':labels.get(str(ident),'原生组合 '+str(ident)),
             'effect':info['effect_by_template'][str(ident)],
             'coaches':[c['name'] for c in coaches if ident in c.get('ability_ids',[])[2:4]]}
            for ident,row in sorted(records.items()) if str(ident) in info['effect_by_template']]


def repair_legacy(document,info=None,resources=None):
    """Repair previously imported read-only coach options without touching saves."""
    info=info or schema();known=info.get('booster_records',{});templates=info.get('templates',[])
    from native_coaches import SLOT_LAYOUT,COACH,ability_ids
    if resources and document.get('coach_slot_layout')!=SLOT_LAYOUT:
        import zipfile
        from native_cpk import Cpk
        report=Path(resources)/'import-report.json'
        try:
            from apk_sources import path_for_resources,packages_for_resources
            apk=_export_data("资料文件",report,"读取当前资料库来源",path_for_resources,resources);native={}
            from resource_set import Reader
            with Reader(apk,packages_for_resources(resources)) as archive:
                for source in ('livedata','custom-resources/legends-v10'):
                    cpk=Cpk(archive.read('assets/gateway-release/'+source+'/dt870_mobile_and.cpk',limit=None))
                    if COACH not in cpk.files:continue
                    raw=unwrap(cpk.read(COACH))
                    if len(raw)%176:raise ValueError('教练表长度改变')
                    for at in range(0,len(raw),176):native[str(int.from_bytes(raw[at:at+8],'little'))]=ability_ids(raw[at:at+176])
            for coach in document['coaches']:
                if str(coach['id']) in native:
                    coach['ability_ids']=native[str(coach['id'])]
                    coach.setdefault('raw_payload',{})['ability_id']=coach['ability_ids']
            document.pop('coach_options_revision',None);document['coach_slot_layout']=SLOT_LAYOUT
        except (OSError,KeyError,ValueError,zipfile.BadZipFile):pass
    if document.get('coach_options_revision')=='native-boosters-2-and-links-2-v1':
        for row in document.get('linkup_types',[]):row['label']=info.get('template_labels',{}).get(str(row['template_id']),row.get('label',''))
        return document
    indexed={t['template_id']:t for t in templates}
    for coach in document.get('coaches',[]):
        ids=coach.get('ability_ids') or coach.get('raw_payload',{}).get('ability_id',[])
        if not ids:continue
        effects={};unmapped=[]
        for ident in ids[:2]:
            if not ident:continue
            effect=known.get(str(ident))
            if effect:
                key=effect['parameter'];effects[key]=effects.get(key,0)+effect['amount']
            else:unmapped.append(ident)
        coach.update(effects=effects,unmapped_abilities=unmapped,
                     linkups=[copy.deepcopy(indexed.get(ident)) for ident in (ids+[0]*4)[2:4]])
    document['linkup_types']=[{**copy.deepcopy(t),'label':info.get('template_labels',{}).get(str(t['template_id']),'原生组合 '+str(t['template_id'])),
                              'effect':info['effect_by_template'][str(t['template_id'])],
                              'coaches':[c['name'] for c in document['coaches'] if t['template_id'] in (c.get('ability_ids') or c.get('raw_payload',{}).get('ability_id',[]))[2:4]]}
                             for t in templates]
    document['coach_options_revision']='native-boosters-2-and-links-2-v1'
    return document
