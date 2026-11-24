"""CPU joint-score law v3: one win-probability tier per 50 points of team strength S.

Agreed table (2026-11-11). The stronger side's win/draw/loss chances depend
only on which 50-point band the effective S gap falls in; inside a band they
are constant. Home advantage and form still shift the gap first. Scores are
then drawn from the v2 Dixon-Coles joint distribution reweighted to these
sector masses (no post-draw rewrite, no team/name/side branch).
"""
import bisect
import json
import math
import random

import tournament_score_grid as v2
from tournament_sim_strength import REVISION as STRENGTH_VERSION

MODEL_VERSION = 'actual-squad-tier-50-v3'
CALIBRATION_VERSION = 'tier-50-20261111-v1'
TIER_WIDTH = 50
# (stronger win, draw, stronger loss) per 50-point band of |effective S gap|.
TIERS = ((.36, .28, .36), (.53, .25, .22), (.70, .18, .12), (.79, .13, .08),
         (.86, .09, .05), (.90, .07, .03), (.93, .05, .02), (.95, .04, .01))
HOME_S = v2.HOME_S
FORM_S = v2.FORM_S


def derive_seed(root, *labels):
    raw = json.dumps([MODEL_VERSION, root, *labels], ensure_ascii=False, separators=(',', ':'))
    return int.from_bytes(raw.encode('utf8'), 'big')


def tier(difference):
    return min(len(TIERS) - 1, int(abs(difference) // TIER_WIDTH))


def sector_probabilities(difference, *, minutes=90):
    try: finite = not isinstance(difference, bool) and isinstance(difference, (int, float)) and math.isfinite(difference)
    except OverflowError: finite = False
    if not finite: raise ValueError('S差必须为有限数值')
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 0 <= minutes <= 120:
        raise ValueError('比赛阶段分钟数无效')
    if minutes == 0: return (0., 1., 0.)
    win, draw, loss = TIERS[tier(difference)]
    if minutes != 90:
        # Shorter periods (extra time) keep the same shrinking rule as v2.
        fraction = minutes / 90.
        draw = draw ** fraction
        odds = (win / loss) ** math.sqrt(fraction)
        win = (1 - draw) * odds / (1 + odds); loss = 1 - draw - win
    result = (win, draw, loss)
    return result if difference >= 0 else tuple(reversed(result))


def _options(home, away, minutes, neutral, form_home, form_away):
    if type(neutral) is not bool: raise ValueError('中立场标识必须为布尔值')
    sh, sa = v2._strength(home), v2._strength(away)
    modifiers = {'home_s': 0. if neutral else HOME_S,
                 'form_home_s': v2._form_s(form_home), 'form_away_s': v2._form_s(form_away)}
    difference = sh - sa + modifiers['home_s'] + modifiers['form_home_s'] - modifiers['form_away_s']
    target = sector_probabilities(difference, minutes=minutes)
    dims = [v2.legacy._ratings(x) for x in (home, away)]
    shape = []
    for own, opp in ((dims[0], dims[1]), (dims[1], dims[0])):
        raw = (.45 * own['attack'] + .30 * own['creation'] + .10 * own['depth'] -
               .50 * opp['defence'] - .35 * opp['goalkeeping']) / 100.
        shape.append(max(-.15, min(.15, raw)))
    # Goal rates follow the tier, so a bigger gap also means wider scores.
    advantage = max(-.65, min(.65, math.copysign(tier(difference) * TIER_WIDTH, difference) / 250.))
    rates = [1.3 * math.exp(shape[0] + advantage) * minutes / 90.,
             1.3 * math.exp(shape[1] - advantage) * minutes / 90.]
    return sh, sa, difference, target, tuple(rates), modifiers


def expected_goals(home_ratings, away_ratings, *, minutes=90, neutral=False,
                   form_home=None, form_away=None, model_version=MODEL_VERSION):
    if model_version != MODEL_VERSION: raise ValueError('CPU模型版本不匹配')
    sh, sa, difference, target, rates, modifiers = _options(home_ratings, away_ratings, minutes, neutral, form_home, form_away)
    scores, cdf, xg, actual, tail = v2._joint(rates, target)
    return {'model_version': MODEL_VERSION, 'xg': list(xg), 'minutes': minutes,
            'neutral': neutral, 'strength': [sh, sa], 'effective_s_difference': difference,
            'tier': tier(difference),
            'sector_probabilities': dict(zip(('home_win', 'draw', 'away_win'), actual)),
            'base_xg': list(rates), 'modifiers': modifiers,
            'audit': {'calibration_version': CALIBRATION_VERSION, 'tier_width': TIER_WIDTH,
                      'tiers': [list(t) for t in TIERS],
                      'calibration': 'PC strength-tier probability table',
                      'strength_revision': STRENGTH_VERSION,
                      'sampler': 'one joint-score CDF draw after sector weighting',
                      'support_max': [max(x[0] for x in scores), max(x[1] for x in scores)],
                      'discarded_poisson_tail_bound': tail,
                      'identity_inputs': 'numeric S and bounded shape dimensions; no team ID/name/player side'}}


def sample_period(home_ratings, away_ratings, *, seed, minutes=90, neutral=False,
                  form_home=None, form_away=None, model_version=MODEL_VERSION):
    if type(seed) is not int: raise ValueError('CPU抽样种子必须为整数')
    result = expected_goals(home_ratings, away_ratings, minutes=minutes, neutral=neutral,
                            form_home=form_home, form_away=form_away, model_version=model_version)
    _, _, _, target, rates, _ = _options(home_ratings, away_ratings, minutes, neutral, form_home, form_away)
    scores, cdf, _, _, _ = v2._joint(rates, target)
    draw = random.Random(derive_seed(seed, 'score', minutes)).random()
    result.update(score=list(scores[bisect.bisect_left(cdf, draw)]), seed=seed)
    return result


def penalty_shootout(home_ratings, away_ratings, *, seed, model_version=MODEL_VERSION):
    if model_version != MODEL_VERSION: raise ValueError('CPU点球模型版本不匹配')
    if type(seed) is not int: raise ValueError('CPU点球抽样种子必须为整数')
    v2._strength(home_ratings); v2._strength(away_ratings)
    result = v2.legacy.penalty_shootout(home_ratings, away_ratings,
        seed=derive_seed(seed, 'penalty-takers'), model_version=v2.legacy.LEGACY_MODEL_VERSION)
    result['model_version'] = MODEL_VERSION
    result.setdefault('audit', {})['penalty_kernel'] = 'actual-roster-sequential-v1; independent v3 seed'
    return result
