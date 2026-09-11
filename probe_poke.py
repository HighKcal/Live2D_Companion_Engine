"""Actual Qt/OpenGL probe for optional profile-driven poke annoyance."""
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
from PySide6.QtWidgets import QApplication

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
        self.watchdog.timeout.connect(lambda: self.finish(False, '45 second timeout'))
        self.watchdog.start(45000)

    def silhouette_point(self):
        bounds = self.w._silhouette_region.boundingRect()
        center = bounds.center()
        for radius in range(0, max(bounds.width(), bounds.height()) + 1, 2):
            for point in (center + QPoint(radius, 0), center + QPoint(-radius, 0),
                          center + QPoint(0, radius), center + QPoint(0, -radius)):
                if self.w.silhouette_contains(point):
                    return point
        raise AssertionError('no silhouette pixel')

    def head_point(self, phase):
        left, bottom, right, top = self.w.profile['head_rect_model']
        x = (left + right) / 2 + .65 * math.sin(phase) * (right - left) / 2
        y = (bottom + top) / 2
        matrix = np.array(self.w.canvas.model._model.GetMvp()).reshape((4, 4), order='F')
        clip = matrix @ np.array([x, y, 0, 1])
        return QPoint(round((clip[0] / clip[3] + 1) * self.w.canvas.width() / 2),
                      round((1 - clip[1] / clip[3]) * self.w.canvas.height() / 2))

    def click(self, point, button=Qt.MouseButton.LeftButton):
        QTest.mouseClick(self.w.canvas, button, pos=point, delay=25)
        QTest.qWait(20)

    def pet(self):
        before = self.w.reaction_count
        for i in range(150):
            QTest.mouseMove(self.w.canvas, self.head_point(i * .38), delay=18)
            QTest.qWait(6)
            if self.w.reaction_count > before:
                break
        assert self.w.reaction_count == before + 1

    def start(self):
        try:
            w = self.w
            assert w.profile.get('poke')
            w.idle.next_ambient = float('inf')
            w.idle.next_major = float('inf')
            w.relocator.due = float('inf')
            assert w._silhouette_region is not None
            body = self.silhouette_point()
            clear = QPoint(2, 2)
            assert w.silhouette_contains(body)
            assert not w.silhouette_contains(clear)

            before = w.annoyance_count
            self.click(clear)
            assert w.annoyance_count == before
            self.click(body, Qt.MouseButton.RightButton)
            popup = QApplication.activePopupWidget()
            if popup:
                popup.close()
            assert w.annoyance_count == before
            self.checks.append('transparent pixel and right click are not pokes')

            start_pos = QPoint(w.pos())
            QTest.mousePress(w.canvas, Qt.MouseButton.LeftButton, pos=body)
            assert w.pointer_pressed and not w.dragging
            QTest.mouseMove(w.canvas, body + QPoint(24, 0), delay=30)
            assert w.dragging
            QTest.mouseRelease(w.canvas, Qt.MouseButton.LeftButton, pos=body + QPoint(24, 0))
            assert not w.dragging and not w.pointer_pressed
            assert w.pos() != start_pos and w.annoyance_count == before
            self.checks.append('movement beyond logical threshold becomes drag, not poke')

            QTest.qWait(round((w.profile['petting']['post_drag_seconds'] + .2) * 1000))
            self.pet()
            assert w.annoyance_count == 0
            self.checks.append('button-free successful petting is not poke')
            w.cancel_reaction()

            selected = []
            original_select = w.canvas.select_expression
            def tracked(asset):
                selected.append(asset)
                return original_select(asset)
            w.canvas.select_expression = tracked

            self.click(body)
            assert w.annoyance_count == 1 and w.idle_kind is None
            self.checks.append('count 1 has no reaction')

            self.click(body)
            assert w.annoyance_count == 2 and w.idle_kind == 'poke'
            assert w.idle_expression == '疑惑.exp3.json'
            self.checks.append('count 2 selects 疑惑 once')

            calls = len(selected)
            until = w.idle_until
            self.click(body)
            assert w.annoyance_count == 3 and len(selected) == calls
            assert w.idle_expression == '疑惑.exp3.json' and w.idle_until == until
            self.checks.append('between thresholds does not restart reaction')

            self.click(body)
            assert w.annoyance_count == 4 and w.idle_expression == '白眼.exp3.json'
            self.click(body)
            assert w.annoyance_count == 5 and w.idle_expression == '白眼.exp3.json'
            self.click(body)
            assert w.annoyance_count == 6 and w.idle_expression == '生气.exp3.json'
            self.checks.append('counts 4 and 6 select 白眼 then 生气')

            w.annoyance_last_poke = time.monotonic() - w.profile['poke']['reset_seconds'] - .1
            w.tick()
            assert w.annoyance_count == 0 and w.idle_kind is None
            self.checks.append('transient annoyance resets after configured inactivity')

            for expected in range(1, 9):
                self.click(body)
                assert w.annoyance_count == expected
            assert w.idle_kind == 'negative'
            assert w.idle_expression == '脸黑.exp3.json'
            assert w.idle_until == float('inf') and w.idle.next_major == float('inf')
            w.canvas.grabFramebuffer().save(str(self.out / 'persistent-negative.png'))
            self.checks.append('count 8 enters forced persistent 脸黑 NEGATIVE')

            w.annoyance_last_poke = time.monotonic() - 60
            w.idle_until = time.monotonic() - 1
            w.tick()
            assert w.annoyance_count == 8 and w.idle_kind == 'negative'
            assert w.idle_expression == '脸黑.exp3.json'
            self.click(body)
            assert w.annoyance_count == 8 and w.idle_kind == 'negative'
            self.checks.append('time and further pokes do not clear/restart persistent NEGATIVE')

            selected.clear()
            w.detector.cooldown_until = 0.0
            self.pet()
            assert w.annoyance_count == 0
            assert w.idle_kind is None and w.reaction_expression == -1
            assert -1 not in selected
            QTest.qWait(400)
            assert w.reaction_level > .9
            w.canvas.grabFramebuffer().save(str(self.out / 'positive-recovery.png'))
            self.checks.append('petting changes NEGATIVE directly to POSITIVE and clears annoyance')
            self.finish(True)
        except Exception:
            self.finish(False, traceback.format_exc())

    def finish(self, passed, error=None):
        if self.done:
            return
        self.done = True
        self.watchdog.stop()
        result = {'passed': passed, 'checks': self.checks,
                  'input_method': 'QTest mouse events and actual Live2D/OpenGL state'}
        if error:
            result['error'] = error
            print(error, flush=True)
        (self.out / 'probe.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('POKE_VERIFY', passed, self.checks, flush=True)
        self.w.close()
        QTimer.singleShot(0, lambda: QApplication.instance().exit(0 if passed else 1))


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
    window = PetWindow(profile.model_path, state_path=ROOT / 'artifacts/poke/probe-state.json',
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
