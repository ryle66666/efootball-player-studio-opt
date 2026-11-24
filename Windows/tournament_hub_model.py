"""Saved tournament data for the Hub UI."""
import copy,base64,json,secrets
from collections import OrderedDict
import tournament_rules as rules

_REVISIONS=OrderedDict()
def _revision(current):
    key=json.dumps(current,sort_keys=True,ensure_ascii=False,separators=(",",":"))
    value=_REVISIONS.get(key)
    if value is None:
        value=secrets.token_hex(12);_REVISIONS[key]=value
        while len(_REVISIONS)>64:_REVISIONS.popitem(last=False)
    else:_REVISIONS.move_to_end(key)
    return value

def _emblem(entry):
    if not isinstance(entry,dict):
        return None
    uri=entry.get('uri','')
    if not isinstance(uri,str) or not uri.startswith('data:image/png;base64,') or len(uri)>100000:
        return None
    try:raw=base64.b64decode(uri.split(',',1)[1],validate=True)
    except ValueError:return None
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return None
    return {k:entry[k] for k in ('uri','source','kind') if k in entry}

def _team(team, names, player, emblems):
    return {'id': team, 'name': str(names.get(str(team), names.get(team, '球队 ' + str(team)))),
            'own': team == player, 'emblem':_emblem(emblems.get(str(team),emblems.get(team)))} if team is not None else None

def _bracket(stage, fixtures, stage_index):
    """Include ungenerated rounds, but never predict a team or a result.

    The runtime sorts tie IDs as strings and pairs opposite ends. Preserve that
    exact graph (also at 128 teams); adjacent-card assumptions give wrong links.
    """
    size = 1 << (stage['input_count'] - 1).bit_length()
    rounds, previous = [], []
    r = 0
    while size > stage['output_count']:
        actual = {}
        for f in fixtures:
            if f['round'] == r:
                actual.setdefault(f['tie'], []).append(f)
        nodes = []
        ordered = sorted(previous, key=lambda n: n['id'])
        for pair in range(size // 2):
            key = f'{stage_index}:{r}:{pair}'
            legs = sorted(actual.get(key, []), key=lambda f: f['leg'])
            first = legs[0] if legs else None
            sources = [ordered[pair]['id'], ordered[-1-pair]['id']] if ordered else []
            nodes.append({'id': key, 'round': r, 'sources': sources,
                          'teams': [first['home'], first['away']] if first else [None, None],
                          'fixtures': [f['id'] for f in legs],
                          'winner': rules.tie_winner(legs) if legs else None,
                          'bye': bool(first and first.get('bye')),
                          'generated': bool(legs),
                          'rules': rules.round_rules(stage, size)})
        rounds.append({'index': r, 'size': size, 'name': {2: '决赛', 4: '半决赛', 8: '四分之一决赛'}.get(size, str(size) + ' 强'),
                       'generated': bool(actual), 'nodes': nodes})
        previous = nodes
        size //= 2
        r += 1
    return rounds

def _display_fixture(fixture, tournament_stats):
    """Only what the page shows: the settled simulation record (~32 KB per
    match: frozen ratings, seeds, contract blob) never leaves the save."""
    stats = tournament_stats.fixture_stats(fixture)
    sim = fixture.get('simulation_v1') or {}
    f = {k: copy.deepcopy(v) for k, v in fixture.items() if k not in ('simulation_v1', 'player_stats_v1')}
    if fixture.get('forfeit_team') is not None:f['forfeiting_team']=fixture['forfeit_team']
    if fixture.get('not_played'):f['cancelled_by_forfeit']=True
    if stats:
        f['events'] = {'goals': copy.deepcopy(stats['goals']), 'assists': copy.deepcopy(stats['assists'])}
    xg = sim.get('xg') if isinstance(sim, dict) else None
    if isinstance(xg, list) and len(xg) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in xg):
        f['xg'] = [round(float(v), 2) for v in xg]
    return f

def presentation(current, names=None, emblems=None):
    """Return JSON-compatible display data without mutating the save or indexes."""
    names = names or {}
    emblems = emblems or {}
    definition = current['definition']
    player = definition['player_team_id']
    import tournament_stats as tournament_stats
    fixtures = [_display_fixture(f, tournament_stats) for f in current['fixtures']]
    by_stage = {}
    for f in fixtures:
        # The runtime overloads "penalties": True means rule enabled before
        # play; [h,a] means an actual shootout after settlement. Separate them.
        f['penalties_allowed'] = definition['stages'][f['stage']]['type'] in ('knockout', 'playoff')
        if not isinstance(f.get('penalties'), list):
            f['penalties'] = None
        by_stage.setdefault(f['stage'], []).append(f)
    stages = []
    history = {h['stage']: h['tables'] for h in current.get('stage_history', [])}
    for i, s in enumerate(definition['stages']):
        rows = by_stage.get(i, [])
        status = 'complete' if current.get('completed') or i < current['stage_index'] else 'current' if i == current['stage_index'] else 'pending'
        tables = copy.deepcopy(history.get(i, []))
        if i == current['stage_index'] and s['type'] in ('groups', 'league') and not tables:
            tables = [rules.standings(current, g) for g in range(len(current.get('groups', [])))]
        groups = []
        for g, table in enumerate(tables):
            groups.append({'index': g, 'name': '联赛积分榜' if s['type'] == 'league' else (chr(65 + g) if g < 26 else str(g + 1)) + ' 组',
                           'teams': [row['team'] for row in table], 'table': table})
        # Original group order matters for the seed tiebreaker. Historical tables
        # already contain the original seed; no inferred draw is needed.
        byes = []
        if s['type'] in ('groups', 'league') and rows:
            for group in groups:
                roster = set(group['teams'])
                for r in range(s['rounds']):
                    active = {f[k] for f in rows if f.get('group') == group['index'] and f['round'] == r for k in ('home', 'away')}
                    byes.extend({'round': r, 'group': group['index'], 'team': t} for t in sorted(roster - active))
        stages.append({'index': i, 'name': s['name'], 'type': s['type'], 'status': status,
                       'rules': copy.deepcopy(s), 'groups': groups, 'byes': byes,
                       'fixture_ids': [f['id'] for f in rows],
                       'bracket': _bracket(s, rows, i) if s['type'] in ('knockout', 'playoff') and rows else []})
    pending = sorted((f for f in fixtures if f['stage'] == current['stage_index'] and not f['played']),
                     key=lambda f: (f['round'], f.get('leg', 1), f['id']))
    next_own = next((f for f in pending if player in (f['home'], f['away'])), None)
    own_played = sum(f['played'] and not f.get('bye') and not f.get('cancelled_by_forfeit') and player in (f['home'], f['away']) for f in fixtures)
    own_pending = any(player in (f['home'], f['away']) for f in pending)
    # State.entrants is the active stage's saved roster, including direct
    # qualifiers who bypass its playoff. Do not call next_fixture/due/advance.
    active = set(current.get('entrants', [])) | set(current.get('bypass', []))
    own_ties = [f for f in fixtures if f['stage'] == current['stage_index'] and 'tie' in f and player in (f['home'], f['away'])]
    eliminated = False
    if own_ties:
        last = max(own_ties, key=lambda f: (f['round'], f['leg']))
        same = [f for f in own_ties if f['tie'] == last['tie']]
        winner = rules.tie_winner(same)
        eliminated = winner is not None and winner != player
    own_status = 'champion' if current.get('champion') == player else 'complete' if current.get('completed') else 'eliminated' if eliminated else 'playing' if own_pending else 'waiting' if player in active else 'eliminated'
    difficulty = rules.difficulty(current)
    fingerprint = _revision([definition,current['run_id'],fixtures,stages,current.get('completed'),current.get('champion')])
    leaders = tournament_stats.leaderboards(current)
    return {'version': 1, 'uuid': definition['uuid'], 'run_id': current['run_id'], 'save_revision': fingerprint,
            'leaderboards': leaders,
            'name': definition['name'], 'difficulty': difficulty, 'difficulty_name': rules.DIFFICULTIES[difficulty],
            'completed': current.get('completed', False),
            'champion': current.get('champion'), 'player_team_id': player, 'player_status': own_status,
            'player_played': own_played, 'stage_index': current['stage_index'],
            'current_round': pending[0]['round'] if pending else current.get('round', 0),
            'next_fixture_id': next_own['id'] if next_own else None,
            'teams': [_team(t, names, player, emblems) for t in definition['team_ids']], 'stages': stages, 'fixtures': fixtures}
