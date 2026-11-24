"""Filter retired tournament deals and migrate their private rental records."""
import json

REGISTRY='player_studio_tournament_chance_agents_v1'
START,STOP=1500000000,1600000000
DRAW='CMD_USE_MYCLUB_AGENT'
LIST='CMD_GET_MYCLUB_AGENTLIST'
CARDS='CMD_GET_MYCLUB_PROCURABLE_GAMEPLAYERLIST'
BOX='CMD_GET_MYCLUB_AGENT_BOX_INFO'
RESET='CMD_RESET_MYCLUB_AGENT'
COMMANDS={DRAW,CARDS,BOX,RESET}


def retired_id(conn,value):
    if type(value)not in(int,str):return False
    try:ident=int(value)
    except ValueError:return False
    if not START<=ident<STOP:return False
    row=conn.execute('select value from meta where key=?',(REGISTRY,)).fetchone()
    registry=json.loads(row[0])if row else{}
    if not isinstance(registry,dict):registry={}
    if ident in registry.values():return True
    if not conn.execute("select 1 from sqlite_master where name='agents'").fetchone():return True
    return not bool(conn.execute('select 1 from agents where agent_id=?',(ident,)).fetchone())


def decorate(service,logic,response):
    if response.get('result')!='NOERR':return response
    for name in('agent_list','agent_price_list'):
        if isinstance(response.get(name),list):
            response[name]=[row for row in response[name]
                            if not isinstance(row,dict)or not retired_id(logic.conn,row.get('agent_id'))]
    return response


def handle(service,logic,command,request,original):
    if command not in COMMANDS or not retired_id(logic.conn,request.get('agent_id')):return None
    response={'result':'NOERR','msgid':command,'rqid':request.get('rqid')}
    if command==CARDS:response.update(gameplayer_list=[],acquired_player_list=[])
    elif command==BOX:response.update(remaining_player_count=[0]*5,base_player_count=[0]*5,can_reset='NO')
    else:response['result']='ERR_INVALID_PARAM'
    return response


def retire_progress(current):
    if 'reinforcements'not in current:return False
    ledger=current.pop('reinforcements',None)
    if not isinstance(ledger,dict):return True
    rentals=ledger.get('rentals',[])
    if not isinstance(rentals,list):rentals=[]
    loans={(row['card_id'],0)for row in rentals
           if isinstance(row,dict)and type(row.get('card_id'))is int}
    if not loans:return True
    def keys(squad):
        if not isinstance(squad,dict)or not isinstance(squad.get('player_list'),list):return set()
        return {(row['gameplayer_id'].get('id'),row['gameplayer_id'].get('serial'))
                for row in squad['player_list']if isinstance(row,dict)and isinstance(row.get('gameplayer_id'),dict)}
    def affected(saved):
        if not isinstance(saved,dict):return False
        team=saved.get('team')or{}
        if not isinstance(team,dict):return False
        return bool((keys(saved.get('squad'))&loans)-keys({'player_list':team.get('player_list',[])}))
    for name in('team_plans_v1','team_plans_by_policy_v1'):
        plans=current.get(name)or{}
        if not isinstance(plans,dict):continue
        for team,saved in list(plans.items()):
            if name=='team_plans_v1':
                if affected(saved):del plans[team]
            else:
                if not isinstance(saved,dict):continue
                for policy,value in list(saved.items()):
                    if affected(value):del saved[policy]
                if not saved:del plans[team]
    active=current.get('active')or{}
    if not isinstance(active,dict):return True
    if not(active.get('started')or active.get('start_reply')):
        side=active.get('player_side','HOME').lower();own=active.get(side)or{}
        base=keys({'player_list':own.get('player_list',[])})
        invalid=((keys(active.get('own_plan'))|keys(own.get('squad_data')))&loans)-base
        if invalid:
            if keys(own.get('squad_data'))&invalid:
                current.pop('active',None)
                fixture=next((f for f in current.get('fixtures',[])if f['id']==active.get('fixture_id')),None)
                contract=(fixture or{}).get('simulation_v1')or{}
                if not contract.get('frozen'):(contract.get('teams')or{}).pop(side,None)
            else:
                for name in('own_plan','last_legal_initial_plan','start_contract','entry_installation_v1'):
                    active.pop(name,None)
    current.pop('waiting_entry_installation_v1',None)
    return True
