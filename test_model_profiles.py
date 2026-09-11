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
        self.assertEqual(icegirl.display_name, 'IceGirl')
        self.assertEqual(len(icegirl.assets['expressions']), 20)
        self.assertEqual(len(icegirl.assets['motions']), 3)
        self.assertEqual(icegirl.expression_index('疑惑.exp3.json'), 11)
        self.assertEqual(icegirl.runtime_file('MeiYan.motion3.json', 'motion'),
                         'motion_02.motion3.json')
        self.assertIsNone(icegirl.motion_asset('sleep'))
        self.assertEqual(icegirl.behavior_settings()['idle']['major']['actions'],
                         [{'kind': 'negative', 'weight': 1}])
        self.assertEqual(icegirl.behavior_settings()['head_rect_model'],
                         [-0.13, 0.34, 0.13, 0.64])
        positive = icegirl.expression_candidates('positive')[0]['parameter_values']
        self.assertEqual((positive['blush'], positive['heart_eyes']), (1.0, 1.0))
        self.assertEqual(
            [(item['threshold'], item['asset'])
             for item in icegirl.behavior['poke']['reactions']],
            [(2, '疑惑.exp3.json'), (4, '白眼.exp3.json'),
             (6, '生气.exp3.json'), (8, '脸黑.exp3.json')])
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

    def test_optional_poke_assets_and_thresholds_are_validated(self):
        data = self.profile_data()
        data['behavior']['poke'] = {
            'reset_seconds': 15, 'max_click_seconds': .5,
            'drag_threshold_pixels': 8,
            'reactions': [
                {'threshold': 2, 'asset': 'Expressions/happy.exp3.json',
                 'hold_seconds': [1, 2]},
                {'threshold': 4, 'asset': 'Expressions/happy.exp3.json',
                 'persistent_negative': True},
            ]}
        profile = ModelProfile(self.write_profile(data=data), self.root)
        self.assertEqual(profile.behavior['poke']['reactions'][-1]['threshold'], 4)
        data['behavior']['poke']['reactions'][1]['threshold'] = 2
        with self.assertRaisesRegex(ProfileError, 'thresholds'):
            ModelProfile(self.write_profile(data=data), self.root)
        data['behavior']['poke']['reactions'][1]['threshold'] = 4
        data['behavior']['poke']['reactions'][0]['asset'] = 'missing.exp3.json'
        with self.assertRaisesRegex(ProfileError, 'unknown poke expression'):
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
