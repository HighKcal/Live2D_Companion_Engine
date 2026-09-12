import json
import tempfile
import unittest
from pathlib import Path

from model_profiles import ModelProfile, ProfileError, ProfileRegistry


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'profiles').mkdir()
        runtime = self.root / 'models' / 'sample' / 'runtime'
        runtime.mkdir(parents=True)
        (runtime / 'model.model3.json').write_text('{}', encoding='utf-8')
        (runtime / 'happy.exp3.json').write_text('{}', encoding='utf-8')
        (runtime / 'sleep.motion3.json').write_text('{}', encoding='utf-8')
        (runtime / 'assets.json').write_text(json.dumps({
            'expressions': [{'name': 'source happy', 'file': 'happy.exp3.json',
                             'source': 'Expressions/happy.exp3.json'}],
            'motions': [{'name': 'source sleep', 'file': 'sleep.motion3.json',
                         'source': 'Motions/sleep.motion3.json'}]
        }), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def profile_data(self):
        return {
            'schema_version': 1, 'id': 'sample', 'display_name': 'Sample',
            'model': {'path': 'models/sample/runtime/model.model3.json'},
            'presentation': {'scale': 1, 'window_aspect': 1,
                             'window_height_per_character_height': 1},
            'hit_areas': {'head': {'type': 'rect', 'rect': [-1, 0, 1, 1]}},
            'parameters': {'eye_open_left': 'EyeL', 'optional_heart': 'MissingHeart'},
            'controls': [{'parameter': 'eye_open_left', 'label': 'Eye'}],
            'expressions': {
                'ambient': [{'asset': 'Expressions/happy.exp3.json'}],
                'negative': [],
                'positive': [{'asset': 'Expressions/happy.exp3.json',
                              'parameter_values': {'eye_open_left': 0, 'optional_heart': 1}}]
            },
            'motions': {'sleep': {'asset': 'Motions/sleep.motion3.json'}},
            'behavior': {
                'rest_seconds': [1, 2], 'relocation': {},
                'idle': {'ambient': {'interval_seconds': [1, 2], 'probability': 1,
                                     'hold_seconds': [1, 2]},
                         'major': {'delay_seconds': [2, 3], 'actions': []},
                         'negative': {}},
                'petting': {}
            }
        }

    def write_profile(self, name='sample.json', data=None):
        path = self.root / 'profiles' / name
        path.write_text(json.dumps(data or self.profile_data()), encoding='utf-8')
        return path

    def test_parsing_stable_asset_resolution_and_optional_parameter(self):
        profile = ModelProfile(self.write_profile(), self.root)
        self.assertEqual(profile.behavior_settings()['render_window_scale'], 1.0)
        self.assertEqual(profile.behavior_settings()['bottom_offscreen_fraction'], 0.65)
        self.assertEqual(profile.expression_index('Expressions/happy.exp3.json'), 0)
        self.assertEqual(profile.motion_asset('sleep'), 'Motions/sleep.motion3.json')
        values, missing = profile.resolve_values(
            {'eye_open_left': 0, 'optional_heart': 1}, {'EyeL': object()})
        self.assertEqual(values, {'EyeL': 0})
        self.assertEqual(missing, ['optional_heart'])

    def test_discovery_invalid_profile_and_active_selection_persistence(self):
        self.write_profile()
        (self.root / 'profiles' / 'broken.json').write_text('{', encoding='utf-8')
        state = self.root / 'local' / 'app-state.json'
        registry = ProfileRegistry(self.root, state)
        self.assertEqual(list(registry.profiles), ['sample'])
        self.assertIn('broken.json', registry.errors)
        self.assertEqual(registry.menu_entries(), [('sample', 'Sample')])
        registry.set_active('sample')
        self.assertEqual(ProfileRegistry(self.root, state).active_id(), 'sample')

    def test_real_profiles_discover_optional_sleep_and_icegirl(self):
        registry = ProfileRegistry(Path(__file__).resolve().parent)
        self.assertIn('hibana', registry.profiles)
        self.assertIn('tsubaki', registry.profiles)
        self.assertIn('icegirl', registry.profiles)
        self.assertEqual(registry.errors, {})
        profile = registry.get('tsubaki')
        self.assertEqual(profile.expression_index('EXP3/爱心眼.exp3.json'), 1)
        self.assertEqual(profile.runtime_file('motions/idle.motion3.json', 'motion'),
                         'motion_00.motion3.json')
        self.assertIsNone(profile.motion_asset('sleep'))
        self.assertEqual(profile.behavior_settings()['idle']['major']['actions'],
                         [{'kind': 'negative', 'weight': 1}])
        self.assertEqual(profile.hit_areas['head']['rect'], [-0.17, 0.58, 0.17, 0.96])
        self.assertEqual(profile.behavior_settings()['render_window_scale'], 2.0)
        self.assertEqual(profile.behavior_settings()['bottom_offscreen_fraction'], 0.65)
        self.assertEqual(registry.get('hibana').behavior_settings()['render_window_scale'], 2.0)
        self.assertEqual(registry.get('hibana').behavior_settings()['bottom_offscreen_fraction'], 0.65)
        icegirl = registry.get('icegirl')
        self.assertEqual(
            registry.menu_entries(),
            [('hibana', '스파키'), ('icegirl', '슈아'), ('tsubaki', '카멜리아')])
        self.assertEqual(icegirl.display_name, '슈아')
        self.assertEqual(len(icegirl.assets['expressions']), 20)
        self.assertEqual(len(icegirl.assets['motions']), 3)
        self.assertEqual(icegirl.expression_index('疑惑.exp3.json'), 11)
        self.assertEqual(icegirl.runtime_file('MeiYan.motion3.json', 'motion'),
                         'motion_02.motion3.json')
        self.assertIsNone(icegirl.motion_asset('sleep'))
        self.assertEqual(icegirl.motion_asset('greeting'), 'HuiShou.motion3.json')
        self.assertEqual(icegirl.motion_duration('greeting'), 7.0)
        self.assertEqual(icegirl.motion_cleanup_duration('greeting'), 0.35)
        self.assertIsNone(registry.get('hibana').motion_asset('greeting'))
        self.assertIsNone(registry.get('tsubaki').motion_asset('greeting'))
        self.assertEqual(icegirl.behavior_settings()['idle']['major']['actions'],
                         [{'kind': 'negative', 'weight': 1}])
        self.assertEqual(icegirl.behavior_settings()['head_rect_model'],
                         [-0.13, 0.34, 0.13, 0.64])
        self.assertEqual(icegirl.behavior_settings()['poke']['interaction_rect_model'],
                         [-0.1, 0.18, 0.1, 0.34])
        positive = icegirl.expression_candidates('positive')[0]['parameter_values']
        self.assertEqual((positive['blush'], positive['heart_eyes']), (1.0, 1.0))
        self.assertEqual(
            [(item['level'], item['asset'])
             for item in icegirl.behavior['poke']['levels']],
            [(1, '疑惑.exp3.json'), (2, '生气.exp3.json'),
             (3, '脸黑.exp3.json')])
        self.assertEqual(
            [(item['remaining_level'], item['asset'])
             for item in icegirl.behavior['poke']['reconciliation']],
            [(2, '生气.exp3.json'), (1, '舌头.exp3.json'),
             (0, '脸红.exp3.json'), (0, '脸红.exp3.json')])
        self.assertEqual(
            icegirl.behavior['poke']['reconciliation'][-1]['role'],
            'accepting_petting')
        self.assertNotIn(
            'heart_eyes',
            icegirl.behavior['poke']['reconciliation'][-1]['parameter_values'])
        self.assertEqual(
            icegirl.behavior['poke']['completion']['asset'],
            '爱心眼.exp3.json')
        self.assertEqual(
            icegirl.behavior['poke']['completion']['parameter_values'],
            {'head_angle_z': -4.0, 'head_angle_y': 3.0})
        self.assertEqual(
            [item['asset'] for item in icegirl.expression_candidates('negative')],
            ['生气.exp3.json'])
        self.assertFalse(icegirl.behavior_settings()['idle']['negative']['persistent'])
        self.assertNotIn('poke', registry.get('hibana').behavior)
        self.assertNotIn('poke', registry.get('tsubaki').behavior)

    def test_bottom_offscreen_fraction_is_validated(self):
        data = self.profile_data()
        data['presentation']['bottom_offscreen_fraction'] = 1.01
        with self.assertRaisesRegex(ProfileError, 'bottom_offscreen_fraction'):
            ModelProfile(self.write_profile(data=data), self.root)

    def test_unknown_asset_is_validation_error(self):
        data = self.profile_data()
        data['expressions']['negative'] = [{'asset': 'missing.exp3.json'}]
        with self.assertRaisesRegex(ProfileError, 'unknown expression asset'):
            ModelProfile(self.write_profile(data=data), self.root)

    def test_unknown_semantic_parameter_is_validation_error(self):
        data = self.profile_data()
        data['expressions']['positive'][0]['parameter_values']['invented'] = 1
        with self.assertRaisesRegex(ProfileError, 'unknown semantic parameter'):
            ModelProfile(self.write_profile(data=data), self.root)

    def test_optional_poke_levels_region_and_reconciliation_are_validated(self):
        data = self.profile_data()
        data['hit_areas']['poke_chest'] = {
            'type': 'rect', 'space': 'model', 'rect': [-.2, 0, .2, .4]}
        data['behavior']['poke'] = {
            'interaction_region': 'poke_chest',
            'max_click_seconds': .5,
            'drag_threshold_pixels': 8,
            'levels': [
                {'level': 1, 'asset': 'Expressions/happy.exp3.json'},
                {'level': 2, 'asset': 'Expressions/happy.exp3.json',
                 'persistent_negative': True},
            ],
            'reconciliation': [
                {'asset': 'Expressions/happy.exp3.json', 'remaining_level': 1},
                {'asset': 'Expressions/happy.exp3.json', 'remaining_level': 0,
                 'parameter_values': {'eye_open_left': 0, 'optional_heart': 1}}],
            'completion': {'asset': 'Expressions/happy.exp3.json'}}
        profile = ModelProfile(self.write_profile(data=data), self.root)
        self.assertEqual(profile.behavior_settings()['poke']['interaction_rect_model'],
                         [-.2, 0, .2, .4])
        values, missing = profile.resolve_values(
            profile.behavior['poke']['reconciliation'][1]['parameter_values'],
            {'EyeL': object()})
        self.assertEqual(values, {'EyeL': 0})
        self.assertEqual(missing, ['optional_heart'])
        data['behavior']['poke']['levels'][1]['level'] = 3
        with self.assertRaisesRegex(ProfileError, 'sequential'):
            ModelProfile(self.write_profile(data=data), self.root)
        data['behavior']['poke']['levels'][1]['level'] = 2
        data['behavior']['poke']['interaction_region'] = 'missing'
        with self.assertRaisesRegex(ProfileError, 'interaction_region'):
            ModelProfile(self.write_profile(data=data), self.root)
        data['behavior']['poke']['interaction_region'] = 'poke_chest'
        data['behavior']['poke']['reconciliation'][0]['asset'] = 'missing.exp3.json'
        with self.assertRaisesRegex(ProfileError, 'unknown poke reconciliation'):
            ModelProfile(self.write_profile(data=data), self.root)

    def test_motion_duration_is_validated(self):
        data = self.profile_data()
        data['motions']['greeting'] = {
            'asset': 'Motions/sleep.motion3.json', 'duration_seconds': 0}
        with self.assertRaisesRegex(ProfileError, 'duration_seconds'):
            ModelProfile(self.write_profile(data=data), self.root)
        data['motions']['greeting']['duration_seconds'] = 1
        data['motions']['greeting']['cleanup_seconds'] = -0.1
        with self.assertRaisesRegex(ProfileError, 'cleanup_seconds'):
            ModelProfile(self.write_profile(data=data), self.root)

    def test_missing_capabilities_are_valid(self):
        data = self.profile_data()
        data['expressions']['negative'] = []
        data['expressions']['positive'] = []
        data['motions'] = {}
        profile = ModelProfile(self.write_profile(data=data), self.root)
        self.assertEqual(profile.expression_candidates('negative'), [])
        self.assertIsNone(profile.motion_asset('sleep'))
        self.assertEqual(profile.behavior_settings()['idle']['major']['actions'], [])


if __name__ == '__main__':
    unittest.main()
