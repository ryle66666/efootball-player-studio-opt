import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / 'Windows') not in sys.path:
    sys.path.insert(0, str(ROOT / 'Windows'))

import tournament_rules as rules
import tournament_runtime as runtime


class TournamentMatchEnvironmentTests(unittest.TestCase):
    def test_defaults_and_validation(self):
        default = rules.normalize_match_environment(None)
        self.assertEqual(default, {
            'timezone': 'random',
            'season': 'random',
            'weather': 'random',
            'pitch_condition': 'random',
            'turf_length': 'normal',
        })
        # Turf length must be unlocked (short/normal/long) and NOT random
        for valid_turf in ('short', 'normal', 'long'):
            self.assertEqual(rules.normalize_match_environment({'turf_length': valid_turf})['turf_length'], valid_turf)
        with self.assertRaises(ValueError):
            rules.normalize_match_environment({'turf_length': 'random'})
        with self.assertRaises(ValueError):
            rules.normalize_match_environment({'weather': 'typhoon'})

    def test_resolve_random_coherence_and_determinism(self):
        # Deterministic for same seed_key
        s1 = rules.resolve_stadium_setting({}, 'run-1|fix-1')
        s2 = rules.resolve_stadium_setting({}, 'run-1|fix-1')
        self.assertEqual(s1, s2)
        self.assertEqual(s1['turf_length'], 'TURF_LENGTH_NORMAL')

        # Across 60 fixtures: varies, never snow in summer, always valid V17 enums
        seen_tz, seen_season, seen_weather, seen_pitch = set(), set(), set(), set()
        for i in range(60):
            st = rules.resolve_stadium_setting({}, f'run-1|fix-{i}')
            seen_tz.add(st['timezone'])
            seen_season.add(st['season'])
            seen_weather.add(st['weather'])
            seen_pitch.add(st['pitch_condition'])
            self.assertEqual(st['turf_length'], 'TURF_LENGTH_NORMAL')
            if st['season'] == 'SEASON_TYPE_SUMMER':
                self.assertNotEqual(st['weather'], 'WEATHER_TYPE_SNOW')
        self.assertEqual(seen_tz, {'TIME_ZONE_TYPE_DAY', 'TIME_ZONE_TYPE_NIGHT'})
        self.assertEqual(seen_season, {'SEASON_TYPE_SUMMER', 'SEASON_TYPE_WINTER'})
        self.assertTrue({'WEATHER_TYPE_FINE', 'WEATHER_TYPE_RAIN'}.issubset(seen_weather))
        self.assertTrue({'PITCH_CONDITION_NORMAL', 'PITCH_CONDITION_WET'}.issubset(seen_pitch))

    def test_fixed_overrides_and_progress_signature_preserved(self):
        base = {
            'name': 'Test Cup',
            'team_ids': [100, 101],
            'player_team_id': 100,
            'stages': [{'type': 'knockout', 'advance': 1, 'legs': 1}],
        }
        t1 = rules.normalize(base)
        t2 = rules.normalize({
            **base,
            'match_environment': {
                'timezone': 'day',
                'season': 'winter',
                'weather': 'snow',
                'pitch_condition': 'wet',
                'turf_length': 'short',
            },
        })
        # Changing match_environment must NEVER reset tournament progress
        self.assertEqual(runtime.progress_signature(t1), runtime.progress_signature(t2))

        st = rules.resolve_stadium_setting(t2, 'any-seed')
        self.assertEqual(st, {
            'timezone': 'TIME_ZONE_TYPE_DAY',
            'season': 'SEASON_TYPE_WINTER',
            'weather': 'WEATHER_TYPE_SNOW',
            'pitch_condition': 'PITCH_CONDITION_WET',
            'turf_length': 'TURF_LENGTH_SHORT',
        })

        # Merging into event match_env preserves stadium_id and ball_type untouched
        proto_env = {
            'ball_type': 42,
            'stadium_setting': {
                'stadium_id': 77,
                'timezone': 'TIME_ZONE_TYPE_NIGHT',
                'weather': 'WEATHER_TYPE_FINE',
                'season': 'SEASON_TYPE_SUMMER',
                'turf_length': 'TURF_LENGTH_NORMAL',
                'pitch_condition': 'PITCH_CONDITION_NORMAL',
            },
        }
        current = {'definition': t2, 'seed': 123, 'run_id': 'r1'}
        env_patch = runtime.match_environment(current, {'id': 'f1'})
        stadium_override = env_patch.pop('stadium_setting')
        proto_env.update(env_patch)
        proto_env['stadium_setting'].update(stadium_override)
        self.assertEqual(proto_env['ball_type'], 42)
        self.assertEqual(proto_env['stadium_setting']['stadium_id'], 77)
        self.assertEqual(proto_env['stadium_setting']['weather'], 'WEATHER_TYPE_SNOW')
        self.assertEqual(proto_env['stadium_setting']['turf_length'], 'TURF_LENGTH_SHORT')


if __name__ == '__main__':
    unittest.main()
