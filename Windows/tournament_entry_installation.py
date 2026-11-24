"""Bind a prepared match plan to a native installation acknowledgement."""
import copy
import struct
import uuid

MAGIC=0x45324931
RECORD_BYTES=512
STATUS_BYTES=64
CAPACITY=32
EXPECTED_BYTES=RECORD_BYTES*CAPACITY
CHANNEL_BYTES=EXPECTED_BYTES+STATUS_BYTES*CAPACITY+128


def prepare(current,special):
    active=current['active'];definition=current['definition']
    value={'event':definition['event_id'],'run_id':current['run_id'],'fixture_id':active['fixture_id'],
           'team':definition['player_team_id'],'use_team_set':active.get('use_team_set',definition.get('use_team_set',True)),
           'squad':copy.deepcopy(special['squad_data'])}
    old=active.get('entry_installation_v1')
    if old and all(old.get(key)==item for key,item in value.items()):return old
    value.update(nonce=uuid.uuid4().hex,status='prepared')
    encode(value)
    active['entry_installation_v1']=value
    current.pop('waiting_entry_installation_v1',None)
    return value


def prepare_waiting(current,special):
    definition=current['definition']
    value={'event':definition['event_id'],'run_id':current['run_id'],'fixture_id':None,
           'team':definition['player_team_id'],'use_team_set':definition.get('use_team_set',True),
           'squad':copy.deepcopy(special['squad_data'])}
    old=current.get('waiting_entry_installation_v1')
    if old and all(old.get(key)==item for key,item in value.items()):return old
    value.update(nonce=uuid.uuid4().hex,status='prepared')
    encode(value)
    current['waiting_entry_installation_v1']=value
    return value


def record_saved_plan(current):
    """Keep a confirmed native entry in step with its accepted menu edits."""
    active=current.get('active')or{};ticket=active.get('entry_installation_v1')
    if not ticket or ticket.get('status')!='installed'or active.get('started')or active.get('start_reply'):return False
    definition=current['definition']
    scope={'event':definition['event_id'],'run_id':current['run_id'],'fixture_id':active.get('fixture_id'),
           'team':definition['player_team_id'],'use_team_set':active.get('use_team_set',definition.get('use_team_set',True))}
    if any(ticket.get(key)!=value for key,value in scope.items()):return False
    plan=active.get('own_plan')
    if not isinstance(plan,dict):return False
    updated=dict(ticket,squad=copy.deepcopy(plan))
    encode(updated)
    ticket['squad']=updated['squad']
    return True


def encode(ticket):
    output=bytearray(RECORD_BYTES)
    if not ticket:return bytes(output)
    refs=ticket['squad'].get('player_list')or[]
    coaches=ticket['squad'].get('coach')or[]
    order=ticket['squad'].get('squad_gameplan',{}).get('match_plan_settings',{}).get('order')
    if len(refs)!=23 or len(coaches)!=1 or not isinstance(order,list)or len(order)!=40:
        raise ValueError('本场阵容缺少完整球员、教练或首发顺序')
    if sorted(i for i in order if i!=255)!=list(range(23))or any(type(i)is not int or not 0<=i<=255 for i in order):
        raise ValueError('本场阵容首发顺序无效')
    nonce=bytes.fromhex(ticket['nonce'])
    if len(nonce)!=16:raise ValueError('本场阵容安装编号无效')
    struct.pack_into('<II16sII',output,0,MAGIC,ticket['event'],nonce,ticket['team'],int(ticket['use_team_set']))
    keys=[]
    for i,row in enumerate(refs):
        key=row.get('gameplayer_id')or{}
        values=key.get('id'),key.get('serial')
        if any(type(v)is not int or not 0<=v<2**63 for v in values)or not values[0]:raise ValueError('本场阵容球员身份无效')
        keys.append(values);struct.pack_into('<QQ',output,32+i*16,*values)
    if len(set(keys))!=23:raise ValueError('本场阵容球员实例重复')
    coach=coaches[0].get('coach_id')or{}
    if any(type(coach.get(k))is not int or not 0<=coach[k]<2**63 for k in('id','serial'))or not coach['id']:
        raise ValueError('本场阵容教练身份无效')
    struct.pack_into('<QQ',output,400,coach['id'],coach['serial'])
    # The reply lists players by slot; the native plan indexes slots by player.
    native_order=[255]*40
    for slot,player in enumerate(order):
        if player!=255:native_order[player]=slot
    output[416:456]=bytes(native_order)
    return bytes(output)


def encode_rows(rows):
    output=bytearray(EXPECTED_BYTES)
    for i,(_,row)in enumerate(sorted(rows.items())):
        if i>=CAPACITY:raise ValueError('本场阵容安装目录数量超限')
        output[i*RECORD_BYTES:(i+1)*RECORD_BYTES]=encode(row.get('entry_installation'))
    return bytes(output)


def read_status(buffer,offset,fence):
    result=[]
    for i in range(CAPACITY):
        at=offset+EXPECTED_BYTES+i*STATUS_BYTES;fence()
        raw=bytes(buffer[at:at+STATUS_BYTES]);sequence,status=struct.unpack_from('<II',raw)
        fence()
        if not sequence or sequence&1 or sequence!=struct.unpack_from('<I',buffer,at)[0]:continue
        event,team,policy,reason=struct.unpack_from('<IIII',raw,24)
        if status not in(1,2)or policy not in(0,1):continue
        result.append({'status':'installed'if status==1 else'failed','nonce':raw[8:24].hex(),
                       'event':event,'team':team,'use_team_set':bool(policy),'reason':reason,'sequence':sequence})
    return sorted(result,key=lambda value:value['sequence'],reverse=True)


def synchronize(current,statuses):
    active=current.get('active')or{};ticket=active.get('entry_installation_v1')
    if not ticket:return False
    definition=current['definition']
    if any(ticket.get(k)!=v for k,v in(('event',definition['event_id']),('run_id',current['run_id']),
           ('fixture_id',active.get('fixture_id')),('team',definition['player_team_id']),
           ('use_team_set',active.get('use_team_set',definition.get('use_team_set',True))))):return False
    for status in statuses[:1]:
        if all(status.get(k)==ticket[k]for k in('nonce','event','team','use_team_set')):
            ticket['status']=status['status'];ticket['reason']=status.get('reason',0)
            return status['status']=='installed'
    return False
