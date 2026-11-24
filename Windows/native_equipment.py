"""Native boot/glove assignments and packed PlayerAppearance sleeve/sock selection."""
from pathlib import Path
import json,struct
from native_cpk import unwrap,wrap
from native_models import person_keys

BOOTS='common/etc/pesdb/Boots.bin'
ASSIGNMENTS='common/etc/appearance/BootsList.bin'
GLOVES='common/etc/pesdb/Glove.bin'
GLOVE_ASSIGNMENTS='common/etc/appearance/GloveList.bin'
# Confirmed V17 glove IDs present in Glove.bin with verified left/right textures.
VERIFIED_GLOVE_IDS=frozenset({1,101,201,301,311,601,701,901})
DEFAULT_GLOVES=(
    {'id':1,'name':'explorer grip'},
    {'id':101,'name':'Predator'},
    {'id':201,'name':'Vapor Grip3'},
    {'id':301,'name':'Future One Grip 1 NC'},
    {'id':311,'name':'ULTRA Ultimate Hybrid'},
    {'id':601,'name':'Attrakt Fusion Guardian'},
    {'id':701,'name':'Wrap Aqua Control'},
    {'id':901,'name':'Konstriktor Elite NG Hazard'},
)
SLEEVES={'keep':None,'seasonal':0,'short':1,'long':2}
SOCKS={'keep':None,'standard':0,'long':1,'short':2}
SHIRT_TAIL={'keep':None,'tucked':0,'untucked':1}
TIGHTS={'keep':None,'off':0,'short':1,'long':2}
INNER_SHIRT={'keep':None,'off':0,'standard':1,'highneck':2}
BINARY_TOGGLE={'keep':None,'off':0,'on':1}
# Wrist tape values map to (switch_bit_val, color_bits_val_or_None).
# Color table at libUE4.so 0xc78cc4: 0=team, 1=white(0xffd2d2d2), 2=black(0xff282828), 3=blue(0xffa9d0e7).
WRIST_TAPE={'keep':None,'off':(0,None),'white':(1,1),'team':(1,0),'black':(1,2),'blue':(1,3)}
# Native V17 appearance loader (6abcc08) skips the 8-byte identity. The
# character decoder at 68d2048/68d2050 takes bits 62..63 of that payload:
# season=0, short=1, long=2.
SLEEVE_BIT=126
# The same character decoder extracts payload word 1 bits 2..3 at
# 0x68d2044..0x68d2058 into the sock selector (+0x54). Native catalogue:
# 0=standard, 1=long, 2=short (e.g. Grealish/Dybala use 2). 3 is reserved.
SOCK_BIT=130
# Verified V17 accessory bits from libUE4.so (0x68d203c, 0x68d2694..0x68d27c8):
WRIST_LEFT_COLOR_BIT=120   # 2 bits
WRIST_RIGHT_COLOR_BIT=122  # 2 bits
WRIST_RIGHT_BIT=124        # 1 bit (slot 5: SK_WristTaping_R)
WRIST_LEFT_BIT=125         # 1 bit (slot 4: SK_WristTaping_L)
TIGHTS_BIT=128             # 2 bits (slot 8/9: SK_ShortTights / SK_LongTights)
INNER_SHIRT_BIT=132        # 2 bits (slot 0/1..3: SK_Inner / SK_InnerHighneck)
SHIRT_TAIL_BIT=134         # 1 bit (+0x4c: 0=tucked, 1=untucked)
ANKLE_TAPING_BIT=135       # 1 bit (slot 6: SK_AnkleTaping)
PLAYER_GLOVES_BIT=136      # 1 bit (slot 7: SK_Playerglove_, winter-only)

def validate(value):
    if not isinstance(value,dict):raise ValueError('比赛装备设置格式无效')
    if value.get('sleeves','keep') not in SLEEVES:raise ValueError('袖长请选择跟随模板、随季节、短袖或长袖')
    if value.get('socks','keep') not in SOCKS:raise ValueError('球袜长度请选择跟随模板、标准、长袜或短袜')
    if value.get('shirt_tail','keep') not in SHIRT_TAIL:raise ValueError('球衣下摆请选择跟随模板、扎进裤子或放出来')
    if value.get('tights','keep') not in TIGHTS:raise ValueError('紧身打底裤请选择跟随模板、无、短款或长款')
    if value.get('inner_shirt','keep') not in INNER_SHIRT:raise ValueError('紧身内衣请选择跟随模板、无、常规领或高领')
    if value.get('ankle_taping','keep') not in BINARY_TOGGLE:raise ValueError('脚踝绷带请选择跟随模板、无或佩戴')
    if value.get('player_gloves','keep') not in BINARY_TOGGLE:raise ValueError('冬季外场手套请选择跟随模板、无或冬季佩戴')
    if value.get('wrist_tape_left','keep') not in WRIST_TAPE:raise ValueError('左手护腕设置无效')
    if value.get('wrist_tape_right','keep') not in WRIST_TAPE:raise ValueError('右手护腕设置无效')
    boots=value.get('boots_id')
    if boots is not None and (type(boots) is not int or not 0<=boots<=65535):raise ValueError('球鞋编号无效')
    gloves=value.get('gloves_id')
    if gloves is not None and (type(gloves) is not int or not 0<=gloves<=65535):raise ValueError('门将手套编号无效')

def glove_rows(cpk):
    if GLOVES not in cpk.files:return []
    data=unwrap(cpk.read(GLOVES))
    if len(data)%204:raise ValueError('门将手套型号表结构改变，未写入装备')
    rows=[];seen=set()
    for at in range(0,len(data),204):
        row=data[at:at+204];key=struct.unpack_from('<H',row)[0]
        if key in seen:raise ValueError('门将手套型号编号重复')
        seen.add(key)
        name=row[104:204].split(b'\0')[0].decode('utf8','strict')
        rows.append({'id':key,'name':name or '原版门将手套'})
    return rows

def catalogue(cpk):
    data=unwrap(cpk.read(BOOTS))
    if len(data)%304:raise ValueError('球鞋型号表结构改变，未写入装备')
    rows=[];seen=set()
    for at in range(0,len(data),304):
        row=data[at:at+304];key=struct.unpack_from('<H',row)[0]
        if key in seen:raise ValueError('球鞋型号编号重复')
        seen.add(key)
        color=row[4:104].split(b'\0')[0].decode('utf8','strict')
        material=row[104:204].split(b'\0')[0].decode('utf8','strict')
        name=row[204:304].split(b'\0')[0].decode('utf8','strict')
        rows.append({'id':key,'name':name or material or '原版球鞋','color':color,'material':material})
    gloves=[r for r in glove_rows(cpk) if r['id'] in VERIFIED_GLOVE_IDS]
    return {'boots':rows,'gloves':gloves or [dict(r) for r in DEFAULT_GLOVES],
            'sleeves':[{'id':k,'name':n} for k,n in zip(SLEEVES,('跟随模板','随季节','短袖','长袖'))],
            'socks':[{'id':k,'name':n} for k,n in zip(SOCKS,('跟随模板','标准长度','长袜','短袜'))]}

def write_catalogue(cpk,resources):
    result=catalogue(cpk)
    (Path(resources)/'native-equipment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return len(result['boots'])

def read_catalogue(resources,fallback=None):
    path=Path(resources)/'native-equipment.json'
    if not path.is_file() and fallback:path=Path(fallback)/'native-equipment.json'
    return json.loads(path.read_text(encoding='utf8')) if path.is_file() else {'boots':[],'sleeves':[]}

def patch_sleeves(row,equipment):
    # Keep the public function name for older callers; patch all independent
    # verified PlayerAppearance equipment fields together, preserving every
    # unedited bit and all native defaults.
    validate(equipment)
    edits=[]
    for bit,mask,choice in (
        (SLEEVE_BIT,3,SLEEVES[equipment.get('sleeves','keep')]),
        (SOCK_BIT,3,SOCKS[equipment.get('socks','keep')]),
        (SHIRT_TAIL_BIT,1,SHIRT_TAIL[equipment.get('shirt_tail','keep')]),
        (TIGHTS_BIT,3,TIGHTS[equipment.get('tights','keep')]),
        (INNER_SHIRT_BIT,3,INNER_SHIRT[equipment.get('inner_shirt','keep')]),
        (ANKLE_TAPING_BIT,1,BINARY_TOGGLE[equipment.get('ankle_taping','keep')]),
        (PLAYER_GLOVES_BIT,1,BINARY_TOGGLE[equipment.get('player_gloves','keep')]),
    ):
        if choice is not None:edits.append((bit,mask,choice))
    for sw_bit,col_bit,key in (
        (WRIST_LEFT_BIT,WRIST_LEFT_COLOR_BIT,'wrist_tape_left'),
        (WRIST_RIGHT_BIT,WRIST_RIGHT_COLOR_BIT,'wrist_tape_right'),
    ):
        wrist=WRIST_TAPE[equipment.get(key,'keep')]
        if wrist is not None:
            edits.append((sw_bit,1,wrist[0]))
            if wrist[1] is not None:edits.append((col_bit,3,wrist[1]))
    if not edits:return row
    if len(row)!=64:raise ValueError('原版外观记录长度改变')
    value=int.from_bytes(row,'little')
    for bit,mask,choice in edits:
        value=(value&~(mask<<bit))|(choice<<bit)
    return value.to_bytes(64,'little')

def compile_boots(cpk,projects,definitions,bindings,replace_ids=()):
    source=cpk.read(ASSIGNMENTS);data=unwrap(source)
    if len(data)%16:raise ValueError('球鞋分配表结构改变')
    rows=dict(struct.iter_unpack('<QQ',data))
    if len(rows)*16!=len(data):raise ValueError('球鞋分配表含重复编号')
    original=dict(rows);replace_ids=set(replace_ids)
    rows={k:v for k,v in rows.items() if k not in replace_ids}
    available={r['id'] for r in catalogue(cpk)['boots']};report=[]
    for p,d,b in zip(projects,definitions,bindings):
        settings=p.get('equipment') or {};validate(settings)
        donor=p.get('model',{}).get('appearance_source_id') or p['native_card']['source_id']
        inherited=next((original[int(k)] for k in person_keys(donor) if int(k) in original),None)
        selected=settings.get('boots_id')
        if selected is not None and selected not in available:raise ValueError(p['identity']['name']+'：当前 APK 不含所选球鞋型号，请重新选择')
        boots=selected if selected is not None else inherited
        if boots is None:
            report.append({'package_id':d['identity']['package_id'],'boots_id':None,'mode':'native-default'});continue
        from player_identity_binding import owned_person_aliases
        aliases={d['identity']['card_id'],*owned_person_aliases(d['identity']),int(b['asset_key'])}
        for alias in aliases:
            if alias in original and alias not in replace_ids:raise ValueError('个人球鞋编号与原版记录冲突')
            rows[alias]=boots
        report.append({'package_id':d['identity']['package_id'],'boots_id':boots,'mode':'selected' if selected is not None else 'template'})
    result=b''.join(struct.pack('<QQ',pid,rows[pid]) for pid in sorted(rows))
    assert all(rows[k]==v for k,v in original.items() if k not in replace_ids)
    return wrap(result,source),report

def compile_gloves(cpk,projects,definitions,bindings,replace_ids=()):
    source=cpk.read(GLOVE_ASSIGNMENTS);data=unwrap(source)
    if len(data)%16:raise ValueError('门将手套分配表结构改变')
    rows=dict(struct.iter_unpack('<QQ',data))
    if len(rows)*16!=len(data):raise ValueError('门将手套分配表含重复编号')
    original=dict(rows);replace_ids=set(replace_ids)
    rows={k:v for k,v in rows.items() if k not in replace_ids}
    available={r['id'] for r in glove_rows(cpk)};report=[]
    for p,d,b in zip(projects,definitions,bindings):
        settings=p.get('equipment') or {};validate(settings)
        position=str((p.get('identity') or {}).get('position') or '').upper()
        if position!='GK':
            report.append({'package_id':d['identity']['package_id'],'gloves_id':None,'mode':'non-gk-skipped'});continue
        donor=p.get('model',{}).get('appearance_source_id') or p['native_card']['source_id']
        inherited=next((original[int(k)] for k in person_keys(donor) if int(k) in original),None)
        selected=settings.get('gloves_id')
        if selected is not None and available and selected not in available:
            raise ValueError(p['identity']['name']+'：当前 APK 不含所选门将手套型号，请重新选择')
        gloves=selected if selected is not None else inherited
        if gloves is None:
            report.append({'package_id':d['identity']['package_id'],'gloves_id':None,'mode':'native-default'});continue
        from player_identity_binding import owned_person_aliases
        aliases={d['identity']['card_id'],*owned_person_aliases(d['identity']),int(b['asset_key'])}
        for alias in aliases:
            if alias in original and alias not in replace_ids:raise ValueError('个人门将手套编号与原版记录冲突')
            rows[alias]=gloves
        report.append({'package_id':d['identity']['package_id'],'gloves_id':gloves,'mode':'selected' if selected is not None else 'template'})
    if rows==original:
        return source,report
    result=b''.join(struct.pack('<QQ',pid,rows[pid]) for pid in sorted(rows))
    assert all(rows[k]==v for k,v in original.items() if k not in replace_ids)
    return wrap(result,source),report
