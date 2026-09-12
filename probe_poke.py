"""Actual Qt/OpenGL probe for profile-driven poke anger and reconciliation."""
import argparse
import json
import math
import sys
import time
import traceback

import live2d.v3 as live2d
import numpy as np
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from desktop_pet import PetWindow
from model_profiles import ProfileRegistry
from prepare_model import ROOT


class PokeProbe:
    def __init__(self, window):
        self.w = window
        self.out = ROOT / 'artifacts' / 'poke'
        self.out.mkdir(parents=True, exist_ok=True)
        self.checks = []
        self.done = False
        self.watchdog = QTimer(window)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(lambda: self.finish(False, '60 second timeout'))
        self.watchdog.start(60000)

    def model_point(self, x, y):
        matrix = np.array(self.w.canvas.model._model.GetMvp()).reshape((4, 4), order='F')
        clip = matrix @ np.array([x, y, 0, 1])
        return QPoint(round((clip[0] / clip[3] + 1) * self.w.canvas.width() / 2),
                      round((1 - clip[1] / clip[3]) * self.w.canvas.height() / 2))

    def region_center(self, rect):
        left, bottom, right, top = rect
        return self.model_point((left + right) / 2, (bottom + top) / 2)

    def head_point(self, phase=0):
        left, bottom, right, top = self.w.profile['head_rect_model']
        x = (left + right) / 2 + .65 * math.sin(phase) * (right - left) / 2
        return self.model_point(x, (bottom + top) / 2)

    def silhouette_outside_poke(self):
        bounds = self.w._silhouette_region.boundingRect()
        for y in range(bounds.bottom(), bounds.top() - 1, -4):
            for x in range(bounds.left(), bounds.right() + 1, 4):
                point = QPoint(x, y)
                if (self.w.silhouette_contains(point) and
                        not self.w.poke_region_contains(point)):
                    return point
        raise AssertionError('no silhouette point outside poke region')

    def click(self, point, button=Qt.MouseButton.LeftButton, wait=20):
        QTest.mouseClick(self.w.canvas, button, pos=point, delay=5)
        if wait:
            QTest.qWait(wait)

    def pet(self):
        # QTest does not reliably deliver button-free MouseMove on this Windows/Qt
        # stack. Drive the production hover callback deterministically; click,
        # drag, menu, and region arbitration remain actual QTest event paths.
        self.w.detector.reset()
        self.w.detector.cooldown_until = 0.0
        before = self.w.reaction_count
        for i in range(170):
            self.w.hover(self.head_point(i * .38), Qt.MouseButton.NoButton)
            QTest.qWait(10)
            if self.w.reaction_count > before:
                break
        assert self.w.reaction_count == before + 1

    def assert_level(self, level, expression, kind=None):
        assert self.w.annoyance_count == level, self.w.annoyance_count
        assert self.w.idle_expression == expression, self.w.idle_expression
        if kind is not None:
            assert self.w.idle_kind == kind, self.w.idle_kind
        assert self.w.reaction_expression is None
        assert self.w.reaction_level == 0

    def start(self):
        try:
            w = self.w
            poke = w.profile.get('poke')
            assert poke and poke['interaction_region'] == 'poke_chest'
            w.idle.next_ambient = float('inf')
            w.idle.next_major = float('inf')
            w.relocator.due = float('inf')
            assert w._silhouette_region is not None
            area = w.available_screen().availableGeometry()
            w.move_content_to(area.center() - QPoint(
                w._content_size.width() // 2, w._content_size.height() // 2))

            chest = self.region_center(poke['interaction_rect_model'])
            head = self.region_center(w.profile['head_rect_model'])
            outside = self.silhouette_outside_poke()
            clear = QPoint(2, 2)
            assert w.silhouette_contains(chest) and w.poke_region_contains(chest)
            assert w.silhouette_contains(head) and not w.poke_region_contains(head)
            assert w.silhouette_contains(outside) and not w.poke_region_contains(outside)
            assert not w.silhouette_contains(clear)

            before = w.annoyance_count
            self.click(clear)
            self.click(head)
            self.click(outside)
            self.click(chest, Qt.MouseButton.RightButton)
            popup = QApplication.activePopupWidget()
            if popup:
                popup.close()
            assert w.annoyance_count == before
            self.checks.append(
                'transparent, head, outside-chest silhouette, and right click are not pokes')

            start_pos = QPoint(w.pos())
            QTest.mousePress(w.canvas, Qt.MouseButton.LeftButton, pos=chest)
            QTest.mouseMove(w.canvas, chest + QPoint(24, 0), delay=30)
            assert w.dragging
            QTest.mouseRelease(w.canvas, Qt.MouseButton.LeftButton,
                               pos=chest + QPoint(24, 0))
            assert not w.dragging and w.annoyance_count == before
            assert w.pos() != start_pos
            self.checks.append('chest drag moves the pet without increasing annoyance')

            QTest.qWait(round((w.profile['petting']['post_drag_seconds'] + .2) * 1000))
            QTest.mousePress(w.canvas, Qt.MouseButton.LeftButton, pos=chest)
            QTest.mouseMove(w.canvas, chest + QPoint(24, 0), delay=30)
            assert w.dragging and w.pointer_pressed
            QTimer.singleShot(
                100, lambda: QApplication.activePopupWidget().close()
                if QApplication.activePopupWidget() else None)
            w.open_menu(w.mapToGlobal(chest))
            assert not w.dragging and not w.pointer_pressed
            assert QWidget.mouseGrabber() is not w.canvas
            assert w.annoyance_count == before
            self.checks.append('context menu during drag clears grab/state without poke')

            QTest.qWait(round((w.profile['petting']['post_drag_seconds'] + .2) * 1000))
            self.pet()
            assert w.annoyance_count == 0
            self.checks.append('head petting at level zero is positive, never poke')
            w.cancel_reaction()

            selected = []
            original_select = w.canvas.select_expression

            def tracked(asset):
                selected.append(asset)
                return original_select(asset)

            w.canvas.select_expression = tracked
            expected = ['疑惑.exp3.json', '生气.exp3.json', '脸黑.exp3.json']
            observed = []
            started = time.perf_counter()
            for level, expression in enumerate(expected, 1):
                self.click(chest, wait=5)
                self.assert_level(
                    level, expression, 'negative' if level == 3 else 'annoyance')
                observed.append(w.idle_expression)
            elapsed = time.perf_counter() - started
            assert observed == expected and elapsed < 1.0, (observed, elapsed)
            self.checks.append(
                'three rapid chest clicks immediately select levels 1/2/3')

            calls = len(selected)
            self.click(chest, wait=5)
            self.assert_level(3, '脸黑.exp3.json', 'negative')
            assert len(selected) == calls
            self.checks.append('fourth and later chest pokes remain capped at level 3')

            w.annoyance_last_poke = time.monotonic() - 10_000
            w.idle_until = time.monotonic() - 1
            w.tick()
            self.assert_level(3, '脸黑.exp3.json', 'negative')
        except Exception:
            self.finish(False, traceback.format_exc())
            return

        try:
            self.assert_level(3, '脸黑.exp3.json', 'negative')
            self.checks.append('inactivity never resets annoyance or level 3 NEGATIVE')
            w.autonomous = True
            w.relocator.due = time.monotonic() - 1
            w.tick()
            assert not w.relocator.active and w.windowOpacity() == 1.0
            self.checks.append('persistent 脸黑 blocks autonomous relocation')

            selected.clear()
            self.pet()
            self.assert_level(2, '生气.exp3.json', 'annoyance')
            assert w.annoyance_reconciliation_index == 0
            assert '爱心眼.exp3.json' not in selected
            w.idle_until = time.monotonic() - 1
            w.tick()
            self.assert_level(2, '生气.exp3.json', 'annoyance')
            self.checks.append('脸黑 petting selects persistent 生气 without positive')

            selected.clear()
            self.pet()
            self.assert_level(1, '舌头.exp3.json', 'annoyance')
            assert w.annoyance_reconciliation_index == 1
            assert '白眼.exp3.json' not in selected
            assert '爱心眼.exp3.json' not in selected
            w.idle_until = time.monotonic() - 1
            w.tick()
            self.assert_level(1, '舌头.exp3.json', 'annoyance')
            self.checks.append('生气 petting selects persistent 舌头 with 白眼 removed')

            selected.clear()
            self.pet()
            self.assert_level(0, '脸红.exp3.json', 'annoyance')
            assert w.annoyance_reconciliation_index == 2
            assert w.idle_until == float('inf')
            assert w.reaction_expression is None
            assert '爱心眼.exp3.json' not in selected
            w.idle_until = time.monotonic() - 1
            w.tick()
            self.assert_level(0, '脸红.exp3.json', 'annoyance')
            self.checks.append('舌头 petting selects persistent 脸红 without positive')

            selected.clear()
            baseline = w.annoyance_base_expression
            self.pet()
            assert w.annoyance_count == 0
            assert w.annoyance_reconciliation_index == 3
            assert w.idle_kind is None and w.idle_expression is None
            assert w.reaction_expression == baseline
            assert w.reaction_until == float('inf')
            heart = w.model_profile.parameter_id('heart_eyes')
            blush = w.model_profile.parameter_id('blush')
            left_eye = w.model_profile.parameter_id('eye_open_left')
            assert heart not in w.reaction_values
            assert w.reaction_values[blush] == 1.0
            assert w.reaction_values[left_eye] == 0.1
            assert '爱心眼.exp3.json' not in selected
            QTest.qWait(450)
            assert w.reaction_level > .9
            head_z = w.model_profile.parameter_id('head_angle_z')
            head_y = w.model_profile.parameter_id('head_angle_y')
            petting_pose = {
                pid: w.canvas.model.GetParameterValue(w.canvas.params[pid]['index'])
                for pid in (head_z, head_y)}
            w.tick()
            assert w.annoyance_reconciliation_index == 3
            assert w.reaction_expression == baseline
            w.relocator.due = time.monotonic() - 1
            w.tick()
            assert not w.relocator.active and w.windowOpacity() == 1.0
            self.checks.append(
                '脸红 petting starts persistent production petting parameters without heart eyes')

            selected.clear()
            self.pet()
            assert w.annoyance_count == 0 and w.annoyance_reconciliation_index is None
            assert w.idle_kind is None and w.idle_expression is None
            assert w.reaction_expression == baseline
            assert w.canvas.expression == '爱心眼.exp3.json'
            assert selected == ['爱心眼.exp3.json'], selected
            assert heart not in w.reaction_values
            assert w.reaction_level > .9
            assert w.reaction_values[head_z] == -4.0
            assert w.reaction_values[head_y] == 3.0
            QTest.qWait(50)
            completion_pose = {
                pid: w.canvas.model.GetParameterValue(w.canvas.params[pid]['index'])
                for pid in (head_z, head_y)}
            assert all(abs(completion_pose[pid] - petting_pose[pid]) < .75
                       for pid in (head_z, head_y)), (petting_pose, completion_pose)
            QTest.qWait(400)
            assert w.reaction_level > .9
            w.canvas.grabFramebuffer().save(str(self.out / 'positive-recovery.png'))
            self.checks.append('only fifth reconciliation petting selects 爱心眼')
            w.reaction_until = time.monotonic() - .01
            QTest.qWait(900)
            assert w.reaction_expression is None and w.canvas.expression == baseline
            self.checks.append('completion finishes at the original baseline without stale owner')

            w.cancel_reaction()
            w.detector.reset()
            w.idle.settings['major']['actions'] = [{'kind': 'negative', 'weight': 1}]
            w.idle.next_major = time.monotonic() - .01
            w.idle.next_ambient = float('inf')
            w.tick()
            assert w.annoyance_count == 0
            assert w.idle_kind == 'idle_negative'
            assert w.idle_expression == '生气.exp3.json'
            assert w.idle_until != float('inf')
            w.idle_until = time.monotonic() - .01
            w.tick()
            assert w.idle_kind is None and w.annoyance_count == 0
            assert w.idle.next_major > time.monotonic()
            self.checks.append(
                'random idle negative uses transient 生气 and never changes annoyance')

            self.finish(True)
        except Exception:
            self.finish(False, traceback.format_exc())

    def finish(self, passed, error=None):
        if self.done:
            return
        self.done = True
        self.watchdog.stop()
        result = {
            'passed': passed,
            'checks': self.checks,
            'poke_region_model': self.w.profile.get('poke', {}).get(
                'interaction_rect_model'),
            'input_method': (
                'QTest click/drag/menu/region events plus deterministic '
                'production hover callback and actual Live2D/OpenGL state')}
        if error:
            result['error'] = error
            print(error, flush=True)
        (self.out / 'probe.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('POKE_VERIFY', passed, self.checks, flush=True)
        self.w.close()
        QTimer.singleShot(
            0, lambda: QApplication.instance().exit(0 if passed else 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='icegirl')
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
        state_path=ROOT / 'artifacts/poke/probe-state.json',
        profile=profile)
    probe = PokeProbe(window)
    window.canvas.ready.connect(lambda: QTimer.singleShot(1000, probe.start))
    window.show()
    code = app.exec()
    window.canvas.cleanup()
    live2d.dispose()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
