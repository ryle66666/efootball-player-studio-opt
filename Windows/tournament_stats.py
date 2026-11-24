"""Tournament top scorers / top assists.

Player matches use the client's own report (goal_player_list, assist_player_list,
opp_goal_player_list, opp_assist_player_list; one entry per goal). CPU matches
get scorers drawn once, deterministically, from the frozen starting XI of the
settled contract: position x shooting for goals, position x passing for
assists. Shootout goals and own goals never count. A forfeit has no scorers.
Each fixture keeps its own ``player_stats_v1`` record; tables are summed from
those records, so a retried result can never be counted twice.
"""
import json
from pathlib import Path
import random
import threading

VERSION = 1
ASSIST_RATE = .72
# Native position codes: 0 GK 1 CB 2 LB 3 RB 4 DMF 5 CMF 6 LMF 7 RMF 8 AMF 9 LWF 10 RWF 11 SS 12 CF
SCORE_ROLE = {0: .02, 1: .25, 2: .35, 3: .35, 4: .45, 5: .7, 6: 1.0, 7: 1.0, 8: 1.3, 9: 1.7, 10: 1.7, 11: 1.9, 12: 2.6}
ASSIST_ROLE = {0: .05, 1: .2, 2: .8, 3: .8, 4: .6, 5: 1.0, 6: 1.3, 7: 1.3, 8: 1.7, 9: 1.5, 10: 1.5, 11: 1.3, 12: .9}
# Parameter indexes: 14 offensive awareness, 20 finishing, 21 low pass,
# 22 lofted pass, 23 heading, 28 curl, 37 kicking power.
_NAMES = {}
_LOCK = threading.Lock()


def _ability(parameters, weights):
    return sum(parameters[i] * w for i, w in weights)


def _weight(role, parameters, table, weights):
    skill = max(1., _ability(parameters, weights) - 40.)
    return table.get(role, .5) * skill * skill


def _pick(rng, rows):
    total = sum(w for _, w in rows)
    if total <= 0: return None
    point = rng.random() * total
    for item, w in rows:
        point -= w
        if point < 0: return item
    return rows[-1][0]


def player_names(root):
    """card id -> display name (Simplified Chinese slot, then English)."""
    candidates=[Path(root)/'custom-resources/legends-v10/dt870_mobile_and.cpk',Path(root)/'livedata/dt870_mobile_and.cpk']
    path=next((p for p in candidates if p.is_file()),None)
    if path is None:return {}
    stamp=(str(path),path.stat().st_size,path.stat().st_mtime_ns)
    with _LOCK:
        if _NAMES.get('stamp')==stamp:return _NAMES['value']
    from native_cpk import Cpk,unwrap
    package=Cpk(path.read_bytes())
    name = 'common/etc/pesdb/Player.bin'
    if name not in package.files: return {}
    raw = unwrap(package.read(name)); result = {}
    if len(raw) % 400 == 0:
        for at in range(0, len(raw), 400):
            row = raw[at:at + 400]; cid = int.from_bytes(row[8:16], 'little'); slots = []
            for n in range(5):
                slot = row[88 + n * 61:88 + (n + 1) * 61]
                slots.append(slot.split(b'\0', 1)[0].decode('utf8', 'replace').strip())
            result[cid] = slots[2] or slots[4] or slots[0]
    with _LOCK: _NAMES.update(stamp=stamp, value=result)
    return result


def _entry(cid, names):
    cid = int(cid)
    return {'id': cid, 'name': names.get(cid) or ('#' + str(cid))}


def _ids(values):
    out = []
    for value in values or []:
        cid = value.get('id') if isinstance(value, dict) else value
        if type(cid) is int and cid > 0: out.append(cid)
    return out


def from_native(request, player_side, fixture, names, forfeit=False):
    """The client's own goal/assist lists for the player's match."""
    mine = 'home' if player_side == 'HOME' else 'away'; other = 'away' if mine == 'home' else 'home'
    record = {'version': VERSION, 'source': 'native', 'teams': {'home': fixture['home'], 'away': fixture['away']},
              'goals': {'home': [], 'away': []}, 'assists': {'home': [], 'away': []}}
    if forfeit: return dict(record, forfeit=True)
    record['goals'][mine] = [_entry(c, names) for c in _ids(request.get('goal_player_list'))]
    record['assists'][mine] = [_entry(c, names) for c in _ids(request.get('assist_player_list'))]
    record['goals'][other] = [_entry(c, names) for c in _ids(request.get('opp_goal_player_list'))]
    record['assists'][other] = [_entry(c, names) for c in _ids(request.get('opp_assist_player_list'))]
    return record


def from_cpu(contract, fixture, names):
    """Deterministic scorers from the settled contract's frozen starting XI."""
    from tournament_sim_strength import _ordered_roster, _formations
    result = contract.get('result') or {}
    score = result.get('final_score')
    if not isinstance(score, list) or len(score) != 2: raise ValueError('CPU赛果缺少最终比分，无法分配进球')
    seeds = contract.get('seeds') or {}
    root = json.dumps(['player-stats', VERSION, seeds.get('regulation'), fixture['id'], fixture['home'], fixture['away']],
                      separators=(',', ':'))
    rng = random.Random(int.from_bytes(root.encode('utf8'), 'big'))
    record = {'version': VERSION, 'source': 'cpu-model', 'teams': {'home': fixture['home'], 'away': fixture['away']},
              'goals': {'home': [], 'away': []}, 'assists': {'home': [], 'away': []}}
    for index, side in enumerate(('home', 'away')):
        goals = int(score[index])
        if goals <= 0: continue
        team = contract['teams'][side]
        roster, _ = _ordered_roster(team); attack, _, _ = _formations(team)
        starters = [(int(p['gameplayer_id']['id']), attack[i]['role'], p['parameters']) for i, p in enumerate(roster[:11])]
        shooters = [(cid, _weight(role, params, SCORE_ROLE, ((20, .55), (14, .25), (23, .1), (37, .1))))
                    for cid, role, params in starters]
        for _ in range(goals):
            scorer = _pick(rng, shooters)
            if scorer is None: continue
            record['goals'][side].append(_entry(scorer, names))
            if rng.random() < ASSIST_RATE:
                passers = [(cid, _weight(role, params, ASSIST_ROLE, ((21, .35), (22, .25), (28, .15), (14, .25))))
                           for cid, role, params in starters if cid != scorer]
                assist = _pick(rng, passers)
                if assist is not None: record['assists'][side].append(_entry(assist, names))
    return record


def fixture_stats(fixture):
    if fixture.get('cancelled_by_forfeit') or fixture.get('bye') or not fixture.get('played'): return None
    value = fixture.get('player_stats_v1') or (fixture.get('simulation_v1') or {}).get('player_stats_v1')
    return value if isinstance(value, dict) and value.get('version') == VERSION and 'goals' in value else None


def recorded(fixture):
    """True once a record (or a recorded reason it is unavailable) exists."""
    value = fixture.get('player_stats_v1') or (fixture.get('simulation_v1') or {}).get('player_stats_v1')
    return isinstance(value, dict) and value.get('version') == VERSION


def leaderboards(current, limit=20):
    totals = {}; counted = 0
    played = [f for f in current.get('fixtures', []) if f.get('played') and not f.get('bye') and not f.get('cancelled_by_forfeit')]
    for fixture in played:
        stats = fixture_stats(fixture)
        if stats is None: continue
        counted += 1
        for kind in ('goals', 'assists'):
            for side in ('home', 'away'):
                for entry in stats[kind][side]:
                    # One row per player per team: the same card can play
                    # for the user's team and for a CPU squad in one edition.
                    team = stats['teams'][side]
                    row = totals.setdefault((entry['id'], team), {'id': entry['id'], 'name': entry['name'],
                                                                  'team': team, 'goals': 0, 'assists': 0})
                    row[kind] += 1
    rows = list(totals.values())
    scorers = sorted((r for r in rows if r['goals']), key=lambda r: (-r['goals'], -r['assists'], r['name']))[:limit]
    assists = sorted((r for r in rows if r['assists']), key=lambda r: (-r['assists'], -r['goals'], r['name']))[:limit]
    return {'version': VERSION, 'scorers': scorers, 'assists': assists,
            'matches_counted': counted, 'matches_played': len(played)}


def summary_lines(current, names=None, top=3):
    board = leaderboards(current, top); names = names or {}
    def label(r): return r['name'] + '（' + str(names.get(str(r['team']), '球队 ' + str(r['team']))) + '）'
    lines = []
    if board['scorers']: lines.append('射手榜：' + '，'.join(label(r) + ' ' + str(r['goals']) + ' 球' for r in board['scorers']))
    if board['assists']: lines.append('助攻榜：' + '，'.join(label(r) + ' ' + str(r['assists']) + ' 次' for r in board['assists']))
    return lines
