"""Qt/OpenGL probe for the profile-driven greeting user motion."""
import argparse
import json
import math
import sys
import time
import traceback

import live2d.v3 as live2d
import numpy as np
from PySide6.QtCore import QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_pet import PetWindow
from model_profiles import ProfileRegistry
from prepare_model import ROOT


class GreetingProbe:
    def __init__(self, window):
        self.w = window
        self.checks = []
        self.done = False
        self.out = ROOT / 'artifacts' / 'greeting'
        self.out.mkdir(parents=True, exist_ok=True)
        self.watchdog = QTimer(window)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(
            lambda: self.finish(False, '20 second timeout'))
        self.watchdog.start(20000)

    def model_point(self, x, y):
        matrix = np.array(self.w.canvas.model._model.GetMvp()).reshape(
            (4, 4), order='F')
        clip = matrix @ np.array([x, y, 0, 1])
        return QPoint(
            round((clip[0] / clip[3] + 1) * self.w.canvas.width() / 2),
            round((1 - clip[1] / clip[3]) * self.w.canvas.height() / 2))

    def region_center(self, rect):
        left, bottom, right, top = rect
        return self.model_point((left + right) / 2, (bottom + top) / 2)

    def start(self):
        try:
            w = self.w
            supported = w.model_profile.motion_asset('greeting') in w.canvas.motion_indexes
            menu = w.build_menu()
            assert menu._greeting_action.isEnabled() == supported
            menu.close()
            if not supported:
                assert w.model_profile.motion_duration('greeting') is None
                assert not w.start_greeting()
                assert w.active_motion_kind is None
                self.checks.append('unsupported profile disables greeting and returns safely')
                self.finish(True)
                return

            assert w.model_profile.motion_asset('greeting') == 'HuiShou.motion3.json'
            assert w.model_profile.motion_duration('greeting') == 7.0
            self.checks.append('profile maps greeting to the seven-second motion')

            w.idle.next_ambient = float('inf')
            w.idle.next_major = float('inf')
            w.relocator.due = float('inf')
            w.canvas.select_expression('疑惑.exp3.json')
            expression_before = w.canvas.expression

            w.canvas.sleeping = True
            menu = w.build_menu()
            assert not menu._greeting_action.isEnabled()
            menu.close()
            w.canvas.sleeping = False
            self.checks.append('sleeping disables greeting')

            menu = w.build_menu()
            action = menu._greeting_action
            assert action.isEnabled()
            w.menu_active = True
            action.trigger()
            QApplication.processEvents()
            assert w.greeting_pending and w.active_motion_kind is None
            w.menu_active = False
            assert w.dispatch_pending_greeting()
            menu.close()
            assert not w.greeting_pending
            assert w.active_motion_kind == 'greeting'
            assert 6.9 <= w.active_motion_deadline - time.monotonic() <= 7.1
            assert w.canvas.expression == expression_before
            self.checks.append('deferred menu request starts after menu-active clears')

            menu = w.build_menu()
            assert not menu._greeting_action.isEnabled()
            menu.close()
            self.checks.append('active greeting disables a second greeting')

            idle_count = w.idle_count
            w.idle.next_ambient = time.monotonic() - 1
            w.idle.next_major = time.monotonic() - 1
            w.relocator.due = time.monotonic() - 1
            w.tick()
            assert w.idle_count == idle_count
            assert not w.relocator.active
            self.checks.append('greeting blocks idle, automatic sleep, and relocation')
            # Keep the lifecycle assertion isolated from the deliberately overdue
            # idle action above. Idle resumption is checked separately after stop.
            w.idle.next_ambient = float('inf')
            w.idle.next_major = float('inf')
            w.relocator.due = float('inf')

            before_annoyance = w.annoyance_count
            poke = w.profile.get('poke')
            if poke:
                chest = self.region_center(poke['interaction_rect_model'])
                QTest.mouseClick(w.canvas, Qt.MouseButton.LeftButton, pos=chest)
                assert w.annoyance_count == before_annoyance
            before_reactions = w.reaction_count
            left, bottom, right, top = w.profile['head_rect_model']
            for i in range(180):
                x = (left + right) / 2 + .65 * math.sin(i * .38) * (right - left) / 2
                w.hover(self.model_point(x, (bottom + top) / 2),
                        Qt.MouseButton.NoButton)
            assert w.reaction_count == before_reactions
            self.checks.append('greeting ignores chest poke and head petting input')

            stop_calls = []
            original_stop = w.canvas.stop_motion

            def tracked_stop(*args, **kwargs):
                stop_calls.append((time.monotonic(), args, kwargs))
                return original_stop(*args, **kwargs)

            w.canvas.stop_motion = tracked_stop
            pose_ids = ['Param58', 'Param59']
            pose_defaults = {
                pid: w.canvas.params[pid]['default'] for pid in pose_ids}
            QTest.qWait(6800)
            pose_before_stop = {
                pid: w.canvas.model.GetParameterValue(w.canvas.params[pid]['index'])
                for pid in pose_ids}
            QTest.qWait(700)
            pose_after_stop = {
                pid: w.canvas.model.GetParameterValue(w.canvas.params[pid]['index'])
                for pid in pose_ids}
            print('GREETING_POSE_DIAGNOSTIC', json.dumps({
                'defaults': pose_defaults,
                'before_stop': pose_before_stop,
                'after_stop': pose_after_stop,
            }, ensure_ascii=False), flush=True)
            assert w.active_motion_kind is None
            assert w.active_motion_deadline == 0.0
            assert len(stop_calls) == 1
            assert stop_calls[0][1] == (0.35,)
            assert not w.canvas.motion_cleanup_active()
            assert set(w.canvas.motion_parameter_ids[
                w.model_profile.motion_asset('greeting')]) == set(pose_ids)
            assert any(abs(pose_before_stop[pid] - pose_defaults[pid]) > .25
                       for pid in pose_ids)
            assert all(abs(pose_after_stop[pid] - pose_defaults[pid]) < .05
                       for pid in pose_ids), (pose_after_stop, pose_defaults)
            assert w.canvas.expression == expression_before
            self.checks.append(
                'Loop=True motion stops and only its pose parameters return to baseline')

            w.idle.next_major = float('inf')
            w.idle.settings['ambient']['probability'] = 1.0
            w.idle.next_ambient = time.monotonic() - 1
            w.tick()
            assert w.idle_count == idle_count + 1
            self.checks.append('normal idle scheduling resumes after greeting')
            w.cancel_idle_reaction()

            assert w.start_greeting()
            w.begin_drag(QPointF(w.pos() + QPoint(100, 100)))
            assert w.active_motion_kind is None and w.dragging
            w.end_drag()
            self.checks.append('drag stops greeting and remains functional')

            if poke:
                assert w.apply_annoyance_level(len(poke['levels']), time.monotonic())
                state = (w.annoyance_count, w.annoyance_reconciliation_index,
                         w.idle_kind, w.idle_expression)
                menu = w.build_menu()
                assert not menu._greeting_action.isEnabled()
                menu.close()
                assert not w.start_greeting()
                assert state == (w.annoyance_count, w.annoyance_reconciliation_index,
                                 w.idle_kind, w.idle_expression)
                self.checks.append(
                    'persistent NEGATIVE rejects greeting without changing reconciliation')
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
        print('GREETING_VERIFY', passed, self.checks, flush=True)
        self.w.close()
        QTimer.singleShot(0, lambda: QApplication.instance().exit(0 if passed else 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', required=True)
    args = parser.parse_args()
    registry = ProfileRegistry(ROOT)
    profile = registry.get(args.profile)
    if profile is None:
        raise SystemExit(f'unknown profile: {args.profile}')
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
        state_path=ROOT / 'artifacts/greeting' / f'{profile.id}-state.json',
        profile=profile)
    probe = GreetingProbe(window)
    window.canvas.ready.connect(lambda: QTimer.singleShot(900, probe.start))
    window.show()
    code = app.exec()
    window.canvas.cleanup()
    live2d.dispose()
    raise SystemExit(code)


if __name__ == '__main__':
    main()
