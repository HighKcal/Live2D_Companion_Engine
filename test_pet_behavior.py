import json
import random
import math
import unittest
from copy import deepcopy
from pathlib import Path
from PySide6.QtCore import QPoint, QSize, QRect
from desktop_pet import clamp_position
from pet_behavior import IdleScheduler, Relocator, StrokeDetector, smooth
from model_profiles import ProfileRegistry

ROOT = Path(__file__).resolve().parent
PROFILE = ProfileRegistry(ROOT).select('hibana').behavior_settings()


class BehaviorTests(unittest.TestCase):
    def test_idle_scheduler_interaction_spacing_and_sleep(self):
        settings = deepcopy(PROFILE['idle'])
        settings['ambient'].update({'interval_seconds': [2, 2], 'probability': 1,
                                    'hold_seconds': [3, 3],
                                    'expressions': [{'asset': 'ambient-a', 'weight': 1},
                                                    {'asset': 'ambient-b', 'weight': 1}]})
        settings['major'].update({'delay_seconds': [10, 10],
                                  'actions': [{'kind': 'negative', 'weight': 1}]})
        settings['negative'].update({'expressions': [{'asset': 'negative-a', 'weight': 1}]})
        idle = IdleScheduler(settings, 100, random.Random(1))
        self.assertEqual(idle.next_major, 110)
        first = idle.due(102)
        self.assertEqual(first[0], 'ambient')
        self.assertEqual(idle.last_interaction, 100)
        self.assertEqual(idle.next_ambient, float('inf'))
        idle.finished('ambient', 105)
        self.assertEqual(idle.next_ambient, 107)
        second = idle.due(107)
        self.assertEqual(second[0], 'ambient')
        self.assertNotEqual(first[1]['expression'], second[1]['expression'])
        self.assertIsNone(idle.due(110, blocked=True, active_kind='ambient'))
        self.assertEqual(idle.due(110, active_kind='ambient'),
                         ('negative', {'expression': 'negative-a'}))
        idle.finished('negative', 113)
        self.assertEqual(idle.next_major, 123)
        idle.interact(115)
        self.assertEqual(idle.last_interaction, 115)
        self.assertEqual(idle.next_major, 125)

        sleep_settings = deepcopy(settings)
        sleep_settings['major']['actions'] = [{'kind': 'sleep', 'weight': 1}]
        sleeper = IdleScheduler(sleep_settings, 200, random.Random(2))
        self.assertEqual(sleeper.due(210), ('sleep', None))
        self.assertEqual(sleeper.next_major, float('inf'))

    def test_negative_major_remains_blocked_until_explicit_interaction(self):
        settings = deepcopy(PROFILE['idle'])
        settings['major'].update({'delay_seconds': [1, 1],
                                  'actions': [{'kind': 'negative', 'weight': 1}]})
        settings['negative']['expressions'] = [{'asset': 'negative-a', 'weight': 1}]
        idle = IdleScheduler(settings, 10, random.Random(3))
        self.assertEqual(idle.due(11), ('negative', {'expression': 'negative-a'}))
        self.assertEqual(idle.next_major, float('inf'))
        self.assertIsNone(idle.due(10_000, active_kind='negative'))
        self.assertEqual(idle.next_major, float('inf'))
        idle.interact(10_001)
        self.assertEqual(idle.next_major, 10_002)

    def test_transient_idle_negative_payload_is_profile_driven(self):
        settings = deepcopy(PROFILE['idle'])
        settings['major'].update({
            'delay_seconds': [1, 1],
            'actions': [{'kind': 'negative', 'weight': 1}]})
        settings['negative'] = {
            'persistent': False,
            'hold_seconds': [9, 9],
            'expressions': [{'asset': 'mild-annoyance', 'weight': 1}]}
        idle = IdleScheduler(settings, 20, random.Random(5))
        self.assertEqual(
            idle.due(21),
            ('negative', {'expression': 'mild-annoyance',
                          'persistent': False, 'hold': 9}))

    def test_virtual_monitor_arrangements(self):
        for area in [QRect(0, 0, 1920, 1040), QRect(-2560, -240, 2560, 1400), QRect(1920, 0, 1280, 720)]:
            size = QSize(408, 429)
            for point in [QPoint(-99999,-99999), QPoint(99999,99999), area.center()]:
                result = clamp_position(point, size, area)
                self.assertTrue(area.contains(QRect(result, size)))

    def test_smooth_acceleration_and_stop(self):
        self.assertEqual(smooth(0), 0)
        self.assertEqual(smooth(1), 1)
        self.assertLess(smooth(.01)/.01, .04)
        self.assertLess((1-smooth(.99))/.01, .04)

    def test_relocator_phases_distance_bounds_and_cancel(self):
        relocator = Relocator(PROFILE, 10, random.Random(4))
        self.assertTrue(30 <= relocator.due <= 70)
        now = relocator.due
        origin = (500, 400)
        self.assertTrue(relocator.start(now, origin, (0, 0, 1000, 600), (408, 429)))
        self.assertEqual(relocator.state, Relocator.FADING_OUT)
        destination = relocator.destination
        self.assertTrue(0 <= destination[0] <= 1000 and 0 <= destination[1] <= 600)
        self.assertGreaterEqual(math.dist(origin, destination), 300)
        opacity, move, done = relocator.step(now + relocator.fade_out_duration / 2)
        self.assertTrue(0 < opacity < 1 and move is None and not done)
        fade_done = now + relocator.fade_out_duration + .001
        opacity, move, done = relocator.step(fade_done)
        self.assertEqual((opacity, move, done), (0.0, None, False))
        opacity, move, done = relocator.step(fade_done + .01)
        self.assertEqual(opacity, 0.0)
        self.assertEqual(move, destination)
        self.assertEqual(relocator.state, Relocator.FADING_IN)
        end = fade_done + .011 + relocator.fade_in_duration
        self.assertEqual(relocator.step(end), (1.0, None, True))
        self.assertEqual(relocator.state, Relocator.RESTING)
        self.assertTrue(end + 20 <= relocator.due <= end + 60)
        relocator.pause(end + 1)
        self.assertFalse(relocator.active)

    def test_strokes_require_repeated_reversals(self):
        detector = StrokeDetector(PROFILE['petting'])
        head = PROFILE['head_rect_model']
        samples = [-.06,-.02,.04,.06,.02,-.04,-.06,-.02,.04,.06,.02,-.04,-.06]
        fired = [detector.sample(x,.02,i*.12,head) for i,x in enumerate(samples)]
        self.assertEqual(sum(fired), 1)
        self.assertFalse(any(detector.sample(-.06,.02,2+i*.1,head) for i in range(30)))

    def test_sleep_drag_and_outside_reject_strokes(self):
        for blocked, y in [(True,.02), (False,-.2)]:
            d = StrokeDetector(PROFILE['petting'])
            self.assertFalse(any(d.sample(.04 if i%2 else -.04,y,i*.2,PROFILE['head_rect_model'],blocked) for i in range(30)))

    def test_batched_equal_timestamps_preserve_gesture(self):
        d = StrokeDetector(PROFILE['petting'])
        hits = []
        for i in range(240):
            # Two positions can arrive in the same Windows clock tick.
            now = (i//2)*.02
            if d.sample(.06*math.sin(i*.08), .02, now, PROFILE['head_rect_model']):
                hits.append(now)
        self.assertTrue(hits, 'Event batches must not erase direction history')
        self.assertTrue(all(b-a >= PROFILE['petting']['cooldown_seconds'] for a,b in zip(hits,hits[1:])))

    def test_noise_slow_motion_and_fast_single_pass(self):
        head = PROFILE['head_rect_model']
        for positions, interval in [([.0005 if i%2 else -.0005 for i in range(300)], .02),
                                    ([.06*math.sin(i*.004) for i in range(400)], .1),
                                    ([-.08+i*.01 for i in range(17)], .008)]:
            d = StrokeDetector(PROFILE['petting'])
            self.assertFalse(any(d.sample(x,.02,i*interval,head) for i,x in enumerate(positions)))

    def test_inactivity_and_post_drag_clear_history(self):
        d = StrokeDetector(PROFILE['petting'])
        head = PROFILE['head_rect_model']
        d.sample(-.05,.02,1,head)
        d.sample(.05,.02,1.2,head)
        self.assertTrue(d.active)
        d.expire(2)
        self.assertFalse(d.active)
        self.assertIsNone(d.last)
        d.block_for(3,.4)
        self.assertFalse(any(d.sample(.04 if i%2 else -.04,.02,3+i*.05,head) for i in range(8)))
        self.assertEqual(len(d.turns),0)

    def test_natural_slowdown_at_turn_does_not_erase_strokes(self):
        d = StrokeDetector(PROFILE['petting'])
        times = 0.0
        hits = 0
        for i in range(150):
            # Flat extremum with a brief pause, followed by the return stroke.
            x = .055*math.sin(i*.14)
            times += .04 if abs(math.cos(i*.14)) > .12 else .10
            hits += d.sample(x,.02,times,PROFILE['head_rect_model'])
        self.assertGreater(hits,0)


if __name__ == '__main__':
    unittest.main()
