"""Actual Qt/OpenGL probe for profile-driven Feeding v1."""
import argparse
import json
import sys
import time
import traceback

import live2d.v3 as live2d
from PySide6.QtCore import QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_pet import PetWindow
from model_profiles import ProfileRegistry
from prepare_model import ROOT


class FeedingProbe:
    def __init__(self, window):
        self.w = window
        self.out = ROOT / 'artifacts' / 'feeding'
        self.out.mkdir(parents=True, exist_ok=True)
        self.checks = []
        self.done = False
        self.watchdog = QTimer(window)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(
            lambda: self.finish(False, '45 second timeout'))
        self.watchdog.start(45000)

    def mouth_value(self):
        pid = self.w.model_profile.parameter_id('mouth_open')
        return self.w.canvas.model.GetParameterValue(
            self.w.canvas.params[pid]['index'])

    def wait_until(self, predicate, timeout_ms=5000):
        elapsed = 0
        while elapsed < timeout_ms and not predicate():
            QTest.qWait(25)
            elapsed += 25
        assert predicate(), f'condition timed out after {timeout_ms}ms'

    def start(self):
        try:
            w = self.w
            config = w.profile['feeding']
            assert config['close_seconds'] == 1.65
            w.idle.next_ambient = float('inf')
            w.idle.next_major = float('inf')
            w.relocator.due = float('inf')
            area = w.available_screen().availableGeometry()
            w.move_content_to(area.center() - QPoint(
                w._content_size.width() // 2, w._content_size.height() // 2))

            assert w.feeding_supported() and w.feeding_enabled()
            menu = w.build_menu()
            assert menu._feeding_action.isEnabled()
            menu.close()
            assert w.food_overlay.testAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            target = w.project_model_point(config['mouth_target_model'])
            assert target is not None and w.canvas.rect().contains(target.toPoint())
            self.checks.append('profile capability, menu, overlay input transparency, mouth projection')

            previous_expression = w.canvas.expression
            baseline = self.mouth_value()
            started = time.monotonic()
            assert w.start_feeding(started)
            assert w.feeding_active and w.feeding_stage == 'approach'
            assert w.food_overlay.active and not w.food_overlay.path_rect.isEmpty()
            expected_size = round(
                w.character_height * config['food_size_character_fraction'])
            assert config['food_size_character_fraction'] == 0.04
            assert w.food_overlay.food_size == expected_size
            assert w.food_overlay.food_rect.size().toSize().width() == expected_size
            assert w.food_overlay.food_rect.size().toSize().height() == expected_size
            start_center = w.food_overlay.start_center
            assert start_center != w.food_overlay.target_center
            QTest.qWait(40)
            assert w.mask().contains(
                w.food_overlay.path_rect.center() + w._content_offset)
            self.checks.append(
                'food overlay is one-quarter size and its full path is exposed by the window mask')

            QTest.qWait(360)
            mid_rect = w.food_overlay.food_rect
            assert w.feeding_active and mid_rect.center() != start_center
            assert w.feeding_mouth_value > baseline + .15
            assert self.mouth_value() > baseline + .08
            w.food_overlay.grab().save(
                str(self.out / f'{w.model_profile.id}-overlay.png'))
            self.checks.append('cookie moves while the actual mouth parameter opens')

            QTest.qWait(190)
            idle_count = w.idle_count
            w.idle.next_ambient = time.monotonic() - 1
            w.idle.next_major = time.monotonic() - 1
            w.relocator.due = time.monotonic() - 1
            w.tick()
            assert w.idle_count == idle_count
            assert not w.relocator.active and not w.canvas.sleeping
            self.checks.append('feeding blocks idle, automatic sleep, and relocation')

            self.wait_until(lambda: w.feeding_stage == 'eating')
            assert not w.food_overlay.active
            self.wait_until(lambda: w.feeding_stage == 'satisfaction')
            assert abs(w.feeding_mouth_value - baseline) < .03
            QTest.qWait(80)
            assert abs(self.mouth_value() - baseline) < .12
            expected = w.feeding_reactions.get('satisfaction')
            if expected:
                assert w.canvas.expression == expected
            self.checks.append('bite hides the cookie, closes the mouth, and selects satisfaction')

            self.wait_until(lambda: not w.feeding_active, 4000)
            QTest.qWait(80)
            assert not w.food_overlay.active and w.food_overlay.path_rect.isEmpty()
            assert w.feeding_mouth_pid is None
            assert abs(self.mouth_value() - baseline) < .12
            assert w.canvas.expression == previous_expression
            elapsed = time.monotonic() - started
            assert 4.2 < elapsed < 5.3
            self.checks.append('feeding finishes in range and restores mouth/expression baseline')

            baseline = self.mouth_value()
            assert w.start_feeding()
            QTest.qWait(180)
            w.begin_drag(QPointF(w.mapToGlobal(QPoint(100, 100))))
            assert w.dragging and not w.feeding_active
            assert not w.food_overlay.active and w.feeding_mouth_pid is None
            QTest.qWait(50)
            assert abs(self.mouth_value() - baseline) < .12
            w.end_drag()
            self.checks.append('drag cancels feeding and restores the owned mouth parameter')

            if w.model_profile.motion_asset('sleep') in w.canvas.motion_indexes:
                assert w.canvas.sleep()
                assert not w.feeding_enabled() and not w.start_feeding()
                w.canvas.wake()
                self.checks.append('sleep rejects feeding and wake remains functional')

            if w.greeting_supported():
                assert w.start_greeting()
                assert not w.feeding_enabled() and not w.start_feeding()
                w.stop_active_motion()
                self.checks.append('active greeting rejects feeding')

            if w.profile.get('poke'):
                poke = w.profile['poke']
                assert w.apply_annoyance_level(len(poke['levels']), time.monotonic())
                state = (w.annoyance_count, w.annoyance_reconciliation_index,
                         w.idle_kind, w.idle_expression)
                menu = w.build_menu()
                assert not menu._feeding_action.isEnabled()
                menu.close()
                assert not w.start_feeding()
                assert state == (w.annoyance_count, w.annoyance_reconciliation_index,
                                 w.idle_kind, w.idle_expression)
                self.checks.append('persistent NEGATIVE rejects feeding without state changes')

            overlay = w.food_overlay
            w.food_overlay = None
            assert not w.feeding_supported() and not w.start_feeding()
            w.food_overlay = overlay
            self.checks.append('unsupported capability is a safe no-op')
            self.finish(True)
        except Exception:
            self.finish(False, traceback.format_exc())

    def finish(self, passed, error=None):
        if self.done:
            return
        self.done = True
        self.watchdog.stop()
        result = {'passed': passed, 'profile': self.w.model_profile.id,
                  'checks': self.checks}
        if error:
            result['error'] = error
            print(error, flush=True)
        (self.out / f'{self.w.model_profile.id}.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('FEEDING_VERIFY', passed, self.checks, flush=True)
        QTimer.singleShot(0, lambda: QApplication.instance().exit(0 if passed else 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    args = parser.parse_args()
    profile = ProfileRegistry(ROOT).select(args.profile)
    fmt = QSurfaceFormat()
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv[:1])
    live2d.init()
    window = PetWindow(
        profile.model_path,
        state_path=ROOT / 'artifacts' / 'feeding' / f'{profile.id}-state.json',
        profile=profile)
    probe = FeedingProbe(window)
    window.canvas.ready.connect(lambda: QTimer.singleShot(900, probe.start))
    window.show()
    code = app.exec()
    window.canvas.cleanup()
    live2d.dispose()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
