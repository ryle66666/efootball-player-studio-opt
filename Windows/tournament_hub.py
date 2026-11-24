"""First-edition team choice and draw, plus the manually opened tournament records.

The game owns its event menu, match plan, match and results. Center requests
are serialized with the saved tournament state on its SQLite worker.
"""
import copy
import datetime
import json
import secrets
from pathlib import Path

import tournament_rules as rules
import tournament_team_choice as team_choice

import sys
ASSETS = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'resources/tournament_hub'
_EMBEDDED_ASSETS = None
_EMBEDDED_CATALOG = None
REVISION = 'tournament-hub-v1'
# Per gateway process. The page is only served on 127.0.0.1; writes must echo it.
TOKEN = secrets.token_hex(16)


class HubError(ValueError):
    pass


def identity(value):
    if type(value) is int:
        result=value
    elif isinstance(value,str) and value.isascii() and value.isdecimal() and len(value)<=19:
        result=int(value)
    else:
        raise HubError('球员或教练身份无效')
    if not 0<=result<2**63:raise HubError('球员或教练身份超出范围')
    return result


# ---------- state ----------
def _saved(conn, t):
    from tournament_runtime import PREFIX, get, progress_settings
    current = get(conn, PREFIX + t['uuid'])
    if current and progress_settings(current['definition']) != progress_settings(t):
        return None
    return current


def _finished(current):
    from tournament_runtime import player_eliminated
    return bool(current) and (current.get('completed') or player_eliminated(current))


def team_info(service, t, current):
    from tournament_runtime import player_eliminated
    choice = team_choice.load(service.root).get(t['uuid'])
    return {'enabled': team_choice.enabled(t),
            'changeable': team_choice.changeable(t, current, player_eliminated),
            'needs': not _finished(current) and team_choice.needs_choice(t, current, choice, player_eliminated),
            'next': _finished(current),
            'current': t['player_team_id'], 'allowed': team_choice.allowed(t),
            'chosen': bool(choice and choice.get('team') == t['player_team_id'])}


def _own(service, conn, t, current):
    """(team object, plan, source) for the user's side, without generating anything."""
    team = t['player_team_id']
    if not current:
        return None, None, None
    active = current.get('active') or {}
    if active:
        own = active.get(active.get('player_side', 'HOME').lower())
        meta = (own or {}).get('_studio_tournament_squad') or {}
        if own and meta.get('team_id', (own.get('squad_data') or {}).get('team_id')) == team:
            return own, active.get('own_plan') or own['squad_data'], 'active'
    from tournament_runtime import saved_player_plan
    saved = saved_player_plan(current,team)
    if saved:
        return saved['team'], saved['squad'], 'saved'
    return None, None, None


def _pool(service, conn, t, own):
    """Every player this team may field: own 23 + event loans + eligible club cards (service.player_pool)."""
    if own is None or not hasattr(service, 'player_pool'):
        return {}
    try:
        return service.player_pool(conn, t, {'player_side': 'HOME', 'home': own})
    except Exception:
        return {}


_CATALOG = {}


def _catalog(root):
    if _EMBEDDED_CATALOG is not None:
        return _EMBEDDED_CATALOG
    path = ASSETS.parent / 'catalog.json'
    if not _CATALOG and path.is_file():
        _CATALOG.update(json.loads(path.read_text('utf8')))
    return _CATALOG


def player_detail(root, p):
    """What the game's player detail page lists, from the card record itself."""
    cat = _catalog(root)
    params = list(p.get('parameters') or [])[:147]
    if len(params) < 147:
        return None
    abilities = [{'name': name, 'value': params[int(i)]} for i, name in (cat.get('ABILITIES') or {}).items() if int(i) < 147]
    skills = [cat['SKILL_LABELS'].get(sid, sid) for sid, index in (cat.get('PARAMETER_BY_SKILL') or {}).items()
              if index < 147 and params[index] == 1 and sid in (cat.get('SKILL_LABELS') or {})]
    styles = cat.get('PLAY_STYLES') or {}
    attack, defence = (p.get('play_style') or [0, 0])[:2] if len(p.get('play_style') or []) >= 2 else (0, 0)
    boosters = {b['id']: b for b in cat.get('boosters') or []}
    from division_squads import ROLE_NAMES
    return {'name': p.get('player_name') or p.get('name'), 'position': ROLE_NAMES[params[0]] if 0 <= params[0] < 13 else '',
            'height': params[47], 'abilities': abilities, 'skills': skills,
            'play_style': [styles.get('attack', {}).get(str(attack), ''), styles.get('defense', {}).get(str(defence), '')],
            'weak_foot': ['低', '普通', '高', '极高'][params[33]] if params[33] in range(4) else '',
            'boosters': [{'id': b, 'label': (boosters.get(b) or {}).get('label') or ('增能 #%d' % b)} for b in params[144:146] if b],
            'ai_styles': list(p.get('ai_playing_styles') or []), 'level': (p.get('level_info') or {}).get('level')}


def detail(service, conn, t, current, payload):
    own, plan, source = _own(service, conn, t, current)
    if own is None:
        raise HubError('本届阵容尚未生成')
    key = (identity(payload.get('id')), identity(payload.get('serial')))
    pool = _pool(service, conn, t, own) if source != 'owned' else {}
    row = next((p for p in own.get('player_list', []) if (p['gameplayer_id']['id'], p['gameplayer_id']['serial']) == key), None) or pool.get(key)
    if row is None:
        raise HubError('球员不在本赛事名单中')
    out = player_detail(getattr(service, 'root', None), row)
    if out is None:
        raise HubError('球员资料不完整')
    return out


# ---------- actions ----------
def choose_team(service, conn, t, current, payload):
    from tournament_runtime import PREFIX, player_eliminated, state, start_edition, put
    team = payload.get('team')
    if type(team) is not int or team not in team_choice.allowed(t):
        raise HubError('请选择本届参赛名单中的球队')
    if not team_choice.changeable(t, current, player_eliminated):
        raise HubError('首场比赛已开球，本届控制球队已锁定')
    selected = dict(t, player_team_id=team)
    if current and _finished(current):
        put(conn, PREFIX + t['uuid'] + ':history:' + current['run_id'], current)
        current = start_edition(selected, current)
        put(conn, PREFIX + t['uuid'], current)
    elif current and team_choice.setup_pending(current) and not team_choice.user_started(current):
        from tournament_tasks import supersede
        supersede(conn,current)
        current=start_edition(selected,current)
    else:
        current = state(conn, selected)
    current['control_team_choice'] = {'team': team, 'run_id': current['run_id'], 'source': 'hub'}
    current['hub_native_entry_v1'] = {'run_id': current['run_id'], 'team': team}
    put(conn, PREFIX + t['uuid'], current)
    return {'result': 'ok', 'team': team, 'run_id': current['run_id']}


def portraits(service, conn, t, current, payload):
    """Heads and card layers of the own squad; missing pixels are decoded in the background."""
    import tournament_portraits
    own, plan, _ = _own(service, conn, t, current)
    if plan is None:
        return {'players': {}, 'pending': 0}
    ids = [r['gameplayer_id']['id'] for r in plan['player_list']]
    extra = payload.get('ids') or []
    if not isinstance(extra, list) or len(extra) > 120 or any(type(v) is not int or v <= 0 for v in extra):
        raise HubError('头像请求无效')
    return tournament_portraits.instance(service.root).describe(ids + extra)


# ---------- stage results, live round, edition end ----------
OUTCOME = {'group_qualification': '小组出线', 'league_qualification': '联赛晋级', 'playoff_qualification': '进入附加赛',
           'playoff_win': '附加赛胜出', 'knockout_next_round': '晋级下一轮', 'group_elimination': '小组赛淘汰',
           'league_elimination': '联赛淘汰', 'knockout_elimination': '本届赛事淘汰', 'champion': '夺得冠军'}


def progress_view(current):
    """Pending stage result (the same journal the game's presenter uses) and the edition's history."""
    import tournament_promotion as promotion
    if not current:
        return {'pending': None, 'history': []}
    stages = current['definition']['stages']
    def row(event):
        i = event['stage_index']
        return {'id': event['id'], 'stage': i, 'stage_name': stages[i]['name'] if i < len(stages) else '',
                'reason': event['reason'], 'title': OUTCOME.get(event['reason'], event['reason']),
                'good': event['outcome'] in ('advanced', 'champion'), 'champion': event['reason'] == 'champion',
                'status': event['status']}
    try:
        pending = promotion.pending(current)
    except ValueError:
        pending = None
    journal = current.get(promotion.KEY) or {}
    player = current['definition']['player_team_id']
    history = [row(e) for e in journal.get('events', []) if e.get('player_team_id') == player]
    return {'pending': row(pending) if pending else None, 'history': history}


def progress_seen(service, conn, t, current, payload):
    """Confirm the shown result through promotion.begin/confirm, so the game will not show it again."""
    import tournament_promotion as promotion
    from tournament_runtime import PREFIX, put
    event = payload.get('event_id')
    pending = promotion.pending(current) if current else None
    if not pending or pending['id'] != event:
        return {'result': 'ok', 'progress': progress_view(current)}
    journal = current[promotion.KEY]
    delivery = journal.get('delivery')
    if not delivery or delivery.get('event_id') != event:
        lease = promotion.begin(current, 'hub:' + TOKEN)
        delivery = lease['delivery'] if lease else journal.get('delivery')
    if not delivery or not promotion.confirm(current, event, delivery['token']):
        raise HubError('这条赛果演出正在游戏中显示，请稍后刷新')
    with conn:
        put(conn, PREFIX + t['uuid'], current)
    return {'result': 'ok', 'progress': progress_view(current)}


def live_view(current):
    if not current:
        return {'revision': None, 'active': False, 'settled': 0}
    job = current.get('cpu_progress_v2') or {}
    played = sum(bool(f.get('played')) for f in current['fixtures'])
    revision = json.dumps([played, current.get('stage_index'), current.get('completed'),
                           job, current.get('run_id'), {k:(current.get('active')or{}).get(k)for k in ('fixture_id','started')}],
                          ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return {'revision': revision,
            'active': bool(job.get('pending')), 'settled': int(job.get('round_completed') or 0),
            'total': int(job.get('round_total') or 0), 'errors': job.get('errors') or [],
            'blocked': bool(job.get('blocked')), 'started': bool((current.get('active') or {}).get('started'))}


def live(service, conn, t, current, payload):
    if team_choice.setup_pending(current):return live_view(None)
    return live_view(current)


def open_native(service,conn,t,current,payload):
    if team_info(service,t,current).get('needs'):
        raise HubError('请先确认本届控制球队')
    from native_tournament_schedule_ui import request_native_menu
    from tournament_runtime import event_id,PREFIX,put
    if not request_native_menu(event_id(t)):
        raise HubError('游戏赛事菜单暂不可用，请返回游戏后选择本赛事')
    if current and current.pop('hub_native_entry_v1',None):put(conn,PREFIX+t['uuid'],current)
    return {'result':'ok','navigate':'open'}


def targets(service,conn,definitions):
    from tournament_runtime import event_id
    return [{'uuid':t['uuid'],'event_id':event_id(t),
             'needs_team':bool(team_info(service,t,_saved(conn,t)).get('needs'))}for t in definitions]


ACTIONS = {'open':open_native,'team': choose_team, 'portraits': portraits,
           'progress-seen': progress_seen, 'live': live, 'detail': detail}


def act(service, conn, t, action, payload):
    if action not in ACTIONS:
        raise HubError('未知操作')
    if not isinstance(payload, dict):
        raise HubError('请求格式错误')
    current=_saved(conn,t)
    if action not in ('team','live','portraits','detail','progress-seen') and team_choice.needs_choice(t,current,team_choice.load(service.root).get(t['uuid'])):
        raise HubError('请先在中心确认本届控制球队')
    return ACTIONS[action](service,conn,t,current,payload)


# ---------- draw (line-up of the stage that just started) ----------
def _group_name(stage, g):
    if stage['type'] == 'league':
        return '联赛积分榜'
    return (chr(65 + g) if g < 26 else str(g + 1)) + ' 组'


def _round_name(size):
    return {2: '决赛', 4: '半决赛', 8: '四分之一决赛'}.get(size, '%d 强' % size)


def opening_draw(t):
    """Does this tournament open with a draw? (Not when it starts with a league phase.)"""
    stages = t.get('stages') or []
    return bool(stages) and stages[0].get('type') != 'league'


def draw_plan(current):
    """What the current stage's draw produced, for the hub ceremony.

    Covers every workshop structure: several groups (pots in seed order),
    one group (matchday-1 pairings and byes), knockout / play-off
    (first-round ties with seeded byes, any team count, one or two legs) and
    the direct qualifiers who skip a play-off (``bypass``). Every team of the
    stage appears exactly once. Read-only; None when nothing is drawn.
    """
    if not current or current.get('completed'):
        return None
    k = current['stage_index']
    stage = current['definition']['stages'][k]
    if stage['type'] == 'league':
        # A league phase has no draw: it opens straight onto matchday 1.
        # Only group stages, knockouts and play-offs are drawn.
        return None
    entrants = list(current.get('entrants') or [])
    rows = [f for f in current['fixtures'] if f['stage'] == k]
    boxes, items = [], []

    def add(name, kind, teams, labels):
        index = len(boxes)
        boxes.append({'name': name, 'kind': kind, 'labels': labels})
        for slot, team in enumerate(teams):
            if team is not None:
                items.append({'box': index, 'slot': slot, 'team': team})

    groups = current.get('groups') or []
    if stage['type'] in ('groups', 'league') and len(groups) > 1:
        for g, roster in enumerate(groups):
            add(_group_name(stage, g), 'group', roster, ['POT %d' % (i + 1) for i in range(len(roster))])
        items.sort(key=lambda r: (r['slot'], r['box']))  # pot by pot across the groups
        kind = 'groups'
    elif stage['type'] in ('groups', 'league'):
        roster = groups[0] if groups else entrants
        first = sorted((f for f in rows if f['round'] == 0), key=lambda f: f['id'])
        for n, f in enumerate(first):
            add('第 1 轮 · 第 %d 场' % (n + 1), 'pair', [f['home'], f['away']], ['HOME', 'AWAY'])
        active = {x for f in first for x in (f['home'], f['away'])}
        byes = [x for x in roster if x not in active]
        if byes:
            add('第 1 轮轮空', 'bye', byes, ['BYE'] * len(byes))
        kind = 'pairs'
    else:
        ties = {}
        for f in rows:
            if f['round'] == 0 and 'tie' in f:
                ties.setdefault(f['tie'], []).append(f)
        size = 1 << max(0, (len(entrants) - 1).bit_length())
        # Final / semi / quarter names only for a knockout that decides the
        # trophy; a play-off or an early-stopping knockout is not a "final".
        if stage['type'] == 'playoff':
            prefix = stage['name']
        elif stage['output_count'] == 1:
            prefix = _round_name(size)
        else:
            prefix = '%d 强' % size
        order = sorted(ties, key=lambda key: int(str(key).rsplit(':', 1)[-1]))
        for n, key in enumerate(order):
            first = min(ties[key], key=lambda f: f.get('leg', 1))
            name = '%s · 第 %d 组' % (prefix, n + 1)
            if first.get('bye'):
                add(name, 'bye', [first['home'], None], ['SEEDED', '轮空直接晋级'])
            else:
                add(name, 'pair', [first['home'], first['away']], ['HOME', 'AWAY'])
        kind = 'ties'
    bypass = list(current.get('bypass') or [])
    if bypass:
        # Direct qualifiers who skip this play-off and rejoin its winners.
        add('直接晋级 · 等待本阶段胜者', 'direct', bypass, ['QUALIFIED'] * len(bypass))
    return {'stage': k, 'stage_name': stage['name'], 'kind': kind, 'seeded': k > 0,
            'boxes': boxes, 'items': items, 'entrants': entrants, 'bypass': bypass}


# ---------- page ----------
def _minimal(t, names, emblems):
    from tournament_hub_model import _team
    player = t['player_team_id']
    return {'version': 1, 'uuid': t['uuid'], 'run_id': None, 'name': t['name'], 'difficulty': None,
            'difficulty_name': '', 'completed': False, 'champion': None, 'player_team_id': player,
            'player_status': 'waiting', 'player_played': 0, 'stage_index': 0, 'current_round': 0,
            'next_fixture_id': None, 'leaderboards': None, 'stages': [], 'fixtures': [],
            'teams': [_team(x, names, player, emblems) for x in t['team_ids']]}


def model(service, conn, t):
    import tournament_portraits
    tournament_portraits.instance(service.root).configure(service.document())
    import tournament_hub_model as schedule_ui
    current = _saved(conn, t)
    names = service.names()
    emblems = service.document().get('tournament_emblems', {})
    team=team_info(service,t,current)
    if team['needs']:
        data=_minimal(t,names,emblems)
        data.update(run_id=current.get('run_id')if current else None,player_status='setup',
            stages=[{'index':i,'type':stage['type'],'name':stage['name'],'status':'pending'}for i,stage in enumerate(t['stages'])])
    else:
        data = schedule_ui.presentation(current, names, emblems) if current else _minimal(t, names, emblems)
    data['hub'] = {'endpoint': '/player-studio/tournaments/' + t['uuid'], 'revision': REVISION, 'token': TOKEN, 'team': team,
                   'progress': progress_view(None if team['needs']else current), 'live': live_view(None if team_choice.setup_pending(current)else current),
                   'use_team_set': t.get('use_team_set', True), 'draw': None if team['needs']else draw_plan(current), 'opening_draw': opening_draw(t),
                   'first_run':bool(current and current.get('hub_native_entry_v1')=={
                       'run_id':current['run_id'],'team':t['player_team_id']}),
                   'generated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    return data


def _asset(name):
    return _EMBEDDED_ASSETS[name] if _EMBEDDED_ASSETS is not None else (ASSETS / name).read_text('utf8')


def render(data):
    text = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    text = (text.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))
    return (_asset('index.html').replace('/*HUB_CSS*/', _asset('schedule.css') + '\n' + _asset('hub.css'))
            .replace('/*HUB_JS*/', _asset('hub.js')).replace('HUB_DATA', text)).encode('utf8')


def page(service, conn, t):
    return render(model(service, conn, t))


def list_model(service,conn,definitions):
    from tournament_hub_model import _team
    rows=[];names=service.names();emblems=service.document().get('tournament_emblems',{})
    for t,target in zip(definitions,targets(service,conn,definitions)):
        current=_saved(conn,t);player=t['player_team_id']
        row={'uuid':t['uuid'],'name':t['name'],'teams':len(t['team_ids']),
             'team':_team(player,names,player,emblems),'needs_team':target['needs_team'],
             'status':'new','status_text':'尚未开赛','played':0,'difficulty':'','stage':'','champion':None}
        if current:
            from tournament_hub_model import presentation
            data=presentation(current,names,emblems)
            stage=next((s for s in data['stages']if s['index']==data['stage_index']),None)
            labels={'champion':'本届冠军','complete':'本届已结束','eliminated':'已被淘汰',
                    'playing':'比赛进行中','waiting':'等待下一轮'}
            row.update(status=data['player_status'],status_text=labels.get(data['player_status'],''),
                       played=data['player_played'],difficulty=data['difficulty_name'],
                       stage=stage['name']if stage else '',
                       champion=_team(data['champion'],names,player,emblems)if data.get('champion')is not None else None)
        rows.append(row)
    return {'version':1,'tournaments':rows,'token':TOKEN}


def index_page(service,conn,definitions):
    text=json.dumps(list_model(service,conn,definitions),ensure_ascii=False,separators=(',',':'))
    text=text.replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    return _asset('list.html').replace('/*HUB_CSS*/',_asset('schedule.css')).replace('LIST_DATA',text).encode('utf8')


def runtime_source(source=None, assets=None, catalog=None):
    """Embed the center and its labels in the game module shipped by either editor."""
    source = Path(__file__).read_text('utf8') if source is None else source
    folder = ASSETS if assets is None else Path(assets)
    embedded = {name: (folder / name).read_text('utf8') for name in ('index.html','list.html', 'schedule.css', 'hub.css', 'hub.js')}
    labels = json.loads((Path(catalog) if catalog else folder.parent / 'catalog.json').read_text('utf8'))
    labels = {k: labels[k] for k in ('ABILITIES', 'SKILL_LABELS', 'PARAMETER_BY_SKILL', 'PLAY_STYLES', 'boosters', 'coaches') if k in labels}
    for marker, value in (('_EMBEDDED_ASSETS = None', embedded), ('_EMBEDDED_CATALOG = None', labels)):
        if source.count(marker + '\n') != 1:
            raise ValueError('锦标赛中心资源嵌入点改变')
        source = source.replace(marker + '\n', marker.split(' = ')[0] + ' = ' + repr(value) + '\n', 1)
    return source
