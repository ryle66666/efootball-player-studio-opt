"""Control-team selection before the first player match of an edition."""
import copy
import datetime
import json
import os
import threading
from pathlib import Path

FILE = 'tournament-team-choice.json'
_LOCK = threading.Lock()
_CACHE = {}


def _path(root):
    root = Path(root)
    return root / FILE


def load(root):
    if root is None: return {}
    path = _path(root)
    try: stamp = (path.stat().st_size, path.stat().st_mtime_ns)
    except OSError: return {}
    with _LOCK:
        if _CACHE.get(str(path), (None,))[0] == stamp: return _CACHE[str(path)][1]
    try: value = json.loads(path.read_text(encoding='utf8'))
    except (OSError, ValueError): value = {}
    value = {k: v for k, v in value.items() if isinstance(v, dict) and type(v.get('team')) is int} if isinstance(value, dict) else {}
    with _LOCK: _CACHE[str(path)] = (stamp, value)
    return value


def _write(root, value):
    path = _path(root); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.' + str(os.getpid()) + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf8'); os.replace(temp, path)
    with _LOCK: _CACHE.pop(str(path), None)


def confirm(root, uuid, run_id, team):
    """The native picker was answered for this edition: show the normal entry next time."""
    value = dict(load(root)); row = dict(value.get(uuid) or {'team': team, 'previous': None})
    row.update(team=team, run_id=run_id, confirmed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    value[uuid] = row; _write(root, value)


def mark_installed(root,uuid,run_id,fixture_id,nonce,team):
    value=dict(load(root));row=dict(value.get(uuid)or{})
    if row.get('team')!=team or row.get('run_id')!=run_id:return
    installed={'run_id':run_id,'fixture_id':fixture_id,'nonce':nonce,'team':team}
    if row.get('installed_plan')==installed:return
    row['installed_plan']=installed;value[uuid]=row;_write(root,value)


def save(root, uuid, team, previous=None):
    path = _path(root); path.parent.mkdir(parents=True, exist_ok=True)
    value = dict(load(root))
    value[uuid] = {'team': team, 'previous': previous,
                   'chosen_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    temp = path.with_suffix('.' + str(os.getpid()) + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf8'); os.replace(temp, path)
    with _LOCK: _CACHE.pop(str(path), None)


def enabled(definition):
    return definition.get('team_select', True) is not False and len(definition.get('team_ids') or []) >= 2


def allowed(definition):
    return [t for t in definition.get('team_ids') or [] if type(t) is int and t > 0]


def overlay(root, definitions):
    """Definitions with the stored choice applied (inputs are not modified)."""
    choices = load(root); out = []
    for t in definitions:
        choice = choices.get(t.get('uuid')) if enabled(t) else None
        if choice and choice['team'] in allowed(t) and choice['team'] != t.get('player_team_id'):
            t = copy.deepcopy(t); t['player_team_id'] = choice['team']
        out.append(t)
    return out


def user_started(current):
    if not current: return False
    active = current.get('active') or {}
    if active.get('started') or active.get('start_reply'): return True
    return any(f.get('reported_result') or (f.get('simulation_v1') or {}).get('source') == 'native'
               for f in current.get('fixtures', []) if f.get('played'))


def setup_pending(current):
    """Computer fixtures wait for this edition's team confirmation and draw."""
    if not current or user_started(current):return False
    definition=current['definition']
    if not enabled(definition):return False
    identity={'run_id':current['run_id'],'team':definition['player_team_id']}
    choice=current.get('control_team_choice')or{}
    if choice.get('source')!='hub' or any(choice.get(key)!=value for key,value in identity.items()):return True
    return current.get('hub_native_entry_v1')==identity


def is_open(definition, current, eliminated=None, choice=None):
    if not enabled(definition): return False
    if not current: return True
    if current.get('completed') or (eliminated is not None and eliminated(current)): return True
    # Already picked for this edition: the entry page shows the chosen squad.
    if choice and choice.get('run_id') == current.get('run_id'): return False
    return not user_started(current)



def changeable(definition,current,eliminated=None):
    if not enabled(definition):return False
    if not current:return True
    if current.get('completed')or(eliminated is not None and eliminated(current)):return True
    return not user_started(current)


def needs_choice(definition,current,choice,eliminated=None):
    if not changeable(definition,current,eliminated):return False
    return not(choice and choice.get('source')=='hub'and choice.get('team')in allowed(definition)
               and current and choice.get('run_id')==current.get('run_id')
               and not current.get('completed')and not(eliminated is not None and eliminated(current)))


def choose(root,uid,team,previous,run_id):
    value=dict(load(root))
    value[uid]={'team':team,'previous':previous,'run_id':run_id,'source':'hub',
                'chosen_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    _write(root,value)

def native_picker(request):
    """CMD_SET_EVENT_ENTER sent from the native preset-team picker."""
    return requested_team(request) is not None and isinstance(request.get('difference_squad_data'), list)


def native_squad(special,request=None):
    """Preset-team response data for the selected tournament match plan."""
    squad=special['squad_data'];team=squad['team_id']
    members=copy.deepcopy(squad['player_list'])
    coaches=squad.get('coach')or[]
    if len(coaches)!=1 or not members:raise ValueError('锦标赛比赛计划缺少球员或教练')
    previous=next((r for r in(request or{}).get('difference_squad_data',[])
                   if isinstance(r,dict)and r.get('team_id')==team),{})
    return {'squad_id':previous.get('squad_id',squad.get('squad_id',1)),'team_id':team,
            'coach_id':copy.deepcopy(coaches[0]['coach_id']),
            'gameplayer_id_list':[copy.deepcopy(r['gameplayer_id'])for r in members],
            'member_list':members,'squad_gameplan':copy.deepcopy(squad['squad_gameplan']),
            'tactics_kind':squad.get('tactics_kind',previous.get('tactics_kind',0)),
            'uniform_list':copy.deepcopy(squad.get('uniform_list',[]))}


def requested_team(request):
    value = request.get('select_team_id')
    return value if type(value) is int and value > 0 else None
