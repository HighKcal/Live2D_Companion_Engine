"""Real Live2D/Qt probe for ambient, major idle, direct recovery, and wake-up."""
import argparse
import json
import math
import random
import sys
import time
import traceback
from copy import deepcopy

import live2d.v3 as live2d
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_pet import PetWindow
from pet_behavior import IdleScheduler
from prepare_model import ROOT


class Probe:
    def __init__(self, window):
        self.w = window
        self.out = ROOT / 'artifacts' / 'idle'
        self.out.mkdir(parents=True, exist_ok=True)
        self.checks = []
        self.done = False
        self.watchdog = QTimer(window)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(lambda: self.finish(False, '45 second timeout'))
        self.watchdog.start(45000)

    def start(self):
        try:
            settings = deepcopy(self.w.profile['idle'])
            settings['ambient']['probability'] = 1.0
            settings['major']['actions'] = [{'kind': 'negative', 'weight': 1}]
            now = time.monotonic()
            self.w.idle = IdleScheduler(settings, now, random.Random(4))
            self.baseline_interaction = self.w.idle.last_interaction
            self.w.idle.next_major = float('inf')
            self.w.idle.next_ambient = now - 1
            self.w.tick()
            hold = self.w.idle_until - now
            assert self.w.idle_kind == 'ambient' and 8 <= hold <= 12
            assert self.w.idle.last_interaction == self.baseline_interaction
            self.first_ambient = self.w.idle_expression
            self.w.canvas.grabFramebuffer().save(str(self.out / 'ambient.png'))
            self.checks.append('ambient uses configured 8-12 second hold without interaction reset')
            self.w.idle_until = time.monotonic() - .01
            self.w.tick()
            now = time.monotonic()
            assert self.w.idle_expression is None
            assert self.w.idle.next_ambient - now >= 9.8
            self.checks.append('ambient ends at baseline and schedules its interval from completion')

            self.w.idle.next_ambient = now - 1
            self.w.tick()
            assert self.w.idle_kind == 'ambient'
            ambient_count = len(self.w.model_profile.expression_candidates('ambient'))
            if ambient_count > 1:
                assert self.w.idle_expression != self.first_ambient
                self.checks.append('multiple ambient candidates avoid an immediate repeat')
            else:
                assert self.w.idle_expression == self.first_ambient
                self.checks.append('single ambient candidate remains valid')

            self.w.idle.next_major = time.monotonic() - 1
            self.w.tick()
            assert self.w.idle_kind == 'negative'
            assert self.w.idle_until == float('inf')
            expression = self.w.idle_expression
            self.w.idle_until = time.monotonic() - 1
            self.w.tick()
            assert self.w.idle_kind == 'negative' and self.w.idle_expression == expression
            assert self.w.idle.next_major == float('inf')
            self.checks.append('major negative persists past an expired timestamp and blocks another major action')

            self.w.relocator.due = time.monotonic() - 1
            self.w.tick()
            assert not self.w.relocator.active
            self.checks.append('persistent negative blocks autonomous relocation')

            point = QPoint(self.w.canvas.width()//2, round(self.w.canvas.height()*.65))
            QTest.mousePress(self.w.canvas, Qt.MouseButton.LeftButton, pos=point)
            assert self.w.pointer_pressed and not self.w.dragging
            QTest.mouseMove(self.w.canvas, point + QPoint(20, 0), delay=30)
            assert self.w.dragging and self.w.idle_kind == 'negative'
            QTest.mouseRelease(self.w.canvas, Qt.MouseButton.LeftButton,
                               pos=point + QPoint(20, 0))
            assert not self.w.dragging and self.w.idle_kind == 'negative'
            self.checks.append('drag interaction does not clear persistent negative')
            # The preceding drag deliberately blocks petting input briefly.
            # Start this separate hover/petting check after that production cooldown.
            delay = round((self.w.profile['petting']['post_drag_seconds'] + .15) * 1000)
            QTimer.singleShot(delay, self.negative_hover)
        except Exception:
            self.finish(False, traceback.format_exc())

    def head_point(self, x_fraction):
        import numpy as np
        left, bottom, right, top = self.w.profile['head_rect_model']
        x = (left + right) / 2 + x_fraction * (right - left) / 2
        y = (bottom + top) / 2
        matrix = np.array(self.w.canvas.model._model.GetMvp()).reshape((4, 4), order='F')
        clip = matrix @ np.array([x, y, 0, 1])
        return QPoint(round((clip[0] / clip[3] + 1) * self.w.canvas.width() / 2),
                      round((1 - clip[1] / clip[3]) * self.w.canvas.height() / 2))

    def move_head(self, phase):
        QTest.mouseMove(self.w.canvas, self.head_point(.65 * math.sin(phase)), delay=25)
        QTest.qWait(10)

    def negative_hover(self):
        try:
            before = self.w.reaction_count
            QTest.mouseMove(self.w.canvas, self.head_point(0), delay=30)
            assert self.w.idle_kind == 'negative' and self.w.reaction_count == before
            self.checks.append('plain head hover keeps negative expression')

            self.select_calls = []
            original = self.w.canvas.select_expression
            def tracked(index):
                self.select_calls.append(index)
                return original(index)
            self.w.canvas.select_expression = tracked
            for i in range(25):
                self.move_head(i * .22)
            assert self.w.reaction_count == before and self.w.idle_kind == 'negative'
            assert self.w.detector.active and not self.select_calls
            self.checks.append('in-progress petting candidate keeps negative expression')

            for i in range(25, 130):
                self.move_head(24 * .22 + (i - 24) * .42)
                if self.w.reaction_count > before:
                    break
            assert self.w.reaction_count == before + 1
            assert self.w.idle_kind is None and self.w.reaction_expression == -1
            assert -1 not in self.select_calls
            remaining = self.w.reaction_until - time.monotonic()
            assert 2.8 <= remaining <= 4
            assert self.w.idle.next_major - self.w.idle.last_interaction >= 60
            self.checks.append('successful petting changes negative directly to 3-4 second positive')
            QTimer.singleShot(450, self.positive_visible)
        except Exception:
            self.finish(False, traceback.format_exc())

    def positive_visible(self):
        try:
            assert self.w.reaction_level > .9 and self.w.idle_expression is None
            self.w.canvas.grabFramebuffer().save(str(self.out / 'positive-recovery.png'))
            self.w.reaction_until = time.monotonic() - .01
            QTimer.singleShot(850, self.positive_restored)
        except Exception:
            self.finish(False, traceback.format_exc())

    def positive_restored(self):
        try:
            assert self.w.reaction_expression is None and self.w.reaction_level == 0
            assert self.w.canvas.expression == -1
            self.checks.append('positive fades back to baseline')
            self.auto_sleep()
        except Exception:
            self.finish(False, traceback.format_exc())

    def auto_sleep(self):
        try:
            if self.w.model_profile.motion_asset('sleep') not in self.w.canvas.motion_indexes:
                assert all(action.get('kind') != 'sleep'
                           for action in self.w.profile['idle']['major']['actions'])
                assert not self.w.canvas.sleep(automatic=True)
                menu = self.w.build_menu()
                sleep = next(action for action in menu.actions() if action.text() == '수면 시작')
                assert not sleep.isEnabled()
                menu.deleteLater()
                self.checks.append('missing sleep capability is filtered, disabled, and never invoked')
                self.finish(True)
                return
            settings = deepcopy(self.w.profile['idle'])
            settings['major']['actions'] = [{'kind': 'sleep', 'weight': 1}]
            now = time.monotonic()
            self.w.idle = IdleScheduler(settings, now, random.Random(8))
            self.w.idle.next_major = now - 1
            self.w.tick()
            assert self.w.canvas.sleeping and self.w.sleep_source == 'automatic'
            position = self.w.pos()
            count = self.w.idle_count
            self.w.idle.next_ambient = now - 1
            self.w.idle.next_major = now - 1
            self.w.relocator.due = now - 1
            self.w.tick()
            assert self.w.canvas.sleeping and self.w.idle_count == count
            assert not self.w.relocator.active and self.w.windowOpacity() == 1 and self.w.pos() == position
            QTimer.singleShot(1000, self.wake_auto_from_menu)
        except Exception:
            self.finish(False, traceback.format_exc())

    def wake_auto_from_menu(self):
        try:
            for semantic in ('eye_open_left', 'eye_open_right'):
                pid = self.w.model_profile.parameter_id(semantic)
                value = self.w.canvas.model.GetParameterValue(self.w.canvas.params[pid]['index'])
                assert value < .1, (pid, value)
            menu = self.w.build_menu()
            wake = next(action for action in menu.actions() if action.text() == '수면 해제')
            before = time.monotonic()
            wake.trigger()
            QTest.qWait(100)
            menu.deleteLater()
            assert not self.w.canvas.sleeping and self.w.sleep_source is None
            assert self.w.canvas.model.IsMotionFinished()
            assert self.w.idle.last_interaction >= before
            assert self.w.idle.next_major - self.w.idle.last_interaction >= 60
            self.checks.append('profile sleep motion blocks behaviors and menu wake fully resets state')

            assert self.w.canvas.sleep(automatic=False)
            assert self.w.canvas.sleeping and self.w.sleep_source == 'manual'
            QTest.qWait(100)
            QTest.mouseClick(self.w.canvas, Qt.MouseButton.LeftButton,
                             pos=QPoint(self.w.canvas.width()//2, self.w.canvas.height()//2))
            assert not self.w.canvas.sleeping and self.w.sleep_source is None
            assert not self.w.dragging and self.w.canvas.model.IsMotionFinished()
            self.checks.append('manual sleep uses same wake path and left click wakes without drag')
            self.finish(True)
        except Exception:
            self.finish(False, traceback.format_exc())

    def finish(self, passed, error=None):
        if self.done:
            return
        self.done = True
        self.watchdog.stop()
        result = {'passed': passed, 'checks': self.checks,
                  'input_method': 'QTest events and actual Live2D/OpenGL state',
                  'major_paths': ['forced negative weight', 'forced sleep weight']}
        if error:
            result['error'] = error
            print(error, flush=True)
        (self.out / 'probe.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('IDLE_VERIFY', passed, self.checks, flush=True)
        self.w.close()
        QTimer.singleShot(0, lambda: QApplication.instance().exit(0 if passed else 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='hibana')
    args = parser.parse_args()
    from model_profiles import ProfileRegistry
    profile = ProfileRegistry(ROOT).select(args.profile)
    model = profile.model_path
    fmt = QSurfaceFormat()
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv[:1])
    live2d.init()
    window = PetWindow(model, state_path=ROOT / 'artifacts' / 'idle' / f'{profile.id}-probe-state.json',
                       profile=profile)
    probe = Probe(window)
    window.canvas.ready.connect(probe.start)
    window.show()
    code = app.exec()
    window.canvas.cleanup()
    live2d.dispose()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
