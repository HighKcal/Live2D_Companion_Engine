"""Actual Qt/OpenGL probe for fade relocation and manual-position persistence."""
import argparse
import json
import math
import sys
import time
import traceback

import live2d.v3 as live2d
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from desktop_pet import PetWindow
from model_profiles import ProfileRegistry
from prepare_model import ROOT


class Probe:
    def __init__(self, window):
        self.w = window
        self.out = ROOT / 'artifacts' / 'relocation'
        self.out.mkdir(parents=True, exist_ok=True)
        self.checks = []
        self.records = []
        self.done = False

    def capture(self, name):
        self.w.canvas.update()
        QApplication.processEvents()
        self.w.available_screen().grabWindow(
            0, self.w.x(), self.w.y(), self.w.width(), self.w.height()).save(str(self.out / f'{name}-screen.png'))
        frame = self.w.canvas.grabFramebuffer()
        assert frame.hasAlphaChannel() and frame.pixelColor(0, 0).alpha() == 0
        frame.save(str(self.out / f'{name}-framebuffer.png'))

    def full_cycle(self, name):
        w = self.w
        start = QPoint(w.pos())
        w.relocator.due = time.monotonic() - 1
        w.tick()
        assert w.relocator.state == w.relocator.FADING_OUT and w.windowOpacity() == 1
        out_duration = w.relocator.fade_out_duration
        in_duration = w.relocator.fade_in_duration
        assert .35 <= out_duration <= .45 and .45 <= in_duration <= .55

        QTest.qWait(round(out_duration * 500))
        w.tick()
        assert w.relocator.state == w.relocator.FADING_OUT
        assert 0 < w.windowOpacity() < 1 and w.pos() == start
        if name == 'cycle-0':
            self.capture('fade-out')

        QTest.qWait(round(out_duration * 600) + 10)
        w.tick()
        assert w.relocator.state == w.relocator.RELOCATING
        assert w.windowOpacity() <= .01 and w.pos() == start
        if name == 'cycle-0':
            self.capture('invisible-old-position')

        w.tick()
        destination = QPoint(w.pos())
        assert w.relocator.state == w.relocator.FADING_IN
        assert w.windowOpacity() <= .01 and destination != start
        area = w.available_screen().availableGeometry()
        assert w.visual_within_movement_area(area)
        if name == 'cycle-0':
            self.capture('invisible-new-position')

        deadline = time.monotonic() + in_duration
        while w.relocator.state == w.relocator.FADING_IN and w.windowOpacity() <= .01 and time.monotonic() < deadline:
            QTest.qWait(20)
            w.tick()
        assert w.relocator.state == w.relocator.FADING_IN and 0 < w.windowOpacity() < 1
        assert w.pos() == destination
        if name == 'cycle-0':
            self.capture('fade-in')

        QTest.qWait(round(in_duration * 600) + 10)
        w.tick()
        assert not w.relocator.active and w.windowOpacity() == 1
        assert w.pos() == destination and w.visual_within_movement_area(area)
        distance = math.hypot(destination.x() - start.x(), destination.y() - start.y())
        self.records.append({'name': name, 'from': [start.x(), start.y()],
                             'to': [destination.x(), destination.y()], 'distance': distance,
                             'fade_out': out_duration, 'fade_in': in_duration})
        return start, destination

    def start(self):
        try:
            w = self.w
            w.canvas.timer.stop()
            w.idle.next_ambient = float('inf')
            w.idle.next_major = float('inf')
            w.autonomous = True
            w.pointer_over_interactive_area = lambda: False
            w.was_blocked = False
            area = w.available_screen().availableGeometry()
            w.move_content_to(area.center() - QPoint(w._content_size.width() // 2,
                                                    w._content_size.height() // 2))
            w.manual_position = w.content_position()
            manual = QPoint(w.manual_position)
            w.save_state()

            destinations = []
            for i in range(5):
                _, destination = self.full_cycle(f'cycle-{i}')
                destinations.append((destination.x(), destination.y()))
                assert w.manual_position == manual
            assert len(set(destinations)) >= 4
            assert all(item['distance'] >= max(w.visual_bounds().width(),
                                               w.visual_bounds().height()) * .75
                       for item in self.records)
            self.checks.append('five cycles use fade-out, zero-alpha relocation, fade-in, bounds, and varied distant targets')
            self.checks.append('OpenGL framebuffer keeps transparent corners throughout fades')

            # OFF, pointer hover, menu, negative, and sleep all defer relocation.
            for label, setup, cleanup in [
                ('autonomous off', lambda: setattr(w, 'autonomous', False), lambda: setattr(w, 'autonomous', True)),
                ('pointer over hit region', lambda: setattr(w, 'pointer_over_interactive_area', lambda: True),
                 lambda: setattr(w, 'pointer_over_interactive_area', lambda: False)),
                ('menu active', lambda: setattr(w, 'menu_active', True), lambda: setattr(w, 'menu_active', False)),
            ]:
                setup()
                position = QPoint(w.pos())
                w.relocator.due = time.monotonic() - 1
                w.tick()
                assert not w.relocator.active and w.windowOpacity() == 1 and w.pos() == position, label
                cleanup()
                w.relocator.pause(time.monotonic())
            negative = w.model_profile.expression_candidates('negative')[0]['asset']
            w.start_idle_reaction(time.monotonic(), negative, None, 'negative')
            w.relocator.due = time.monotonic() - 1
            w.tick()
            assert not w.relocator.active
            w.cancel_idle_reaction(include_negative=True)
            if w.model_profile.motion_asset('sleep') in w.canvas.motion_indexes:
                assert w.canvas.sleep(automatic=False)
                w.relocator.due = time.monotonic() - 1
                w.tick()
                assert not w.relocator.active and w.windowOpacity() == 1
                w.canvas.wake()
                suffix = 'sleep blocks relocation'
            else:
                suffix = 'unsupported sleep is skipped'
            self.checks.append(f'toggle, hit hover, menu, and negative block relocation; {suffix}')

            # Actual Qt press during each fade phase must restore opacity and input.
            w.relocator.due = time.monotonic() - 1
            w.tick()
            QTest.qWait(180)
            w.tick()
            old = QPoint(w.pos())
            QTest.mousePress(w.canvas, Qt.MouseButton.LeftButton,
                             pos=QPoint(w.canvas.width() // 2, w.canvas.height() // 2))
            assert w.dragging and not w.relocator.active and w.windowOpacity() == 1 and w.pos() == old
            QTest.mouseRelease(w.canvas, Qt.MouseButton.LeftButton,
                               pos=QPoint(w.canvas.width() // 2, w.canvas.height() // 2))
            self.checks.append('Qt press cancels fade-out at the old position and restores full opacity')

            w.relocator.due = time.monotonic() - 1
            w.tick()
            QTest.qWait(500)
            w.tick()
            if w.relocator.state == w.relocator.RELOCATING:
                w.tick()
            assert w.relocator.state == w.relocator.FADING_IN
            new = QPoint(w.pos())
            QTest.qWait(150)
            w.tick()
            QTest.mousePress(w.canvas, Qt.MouseButton.LeftButton,
                             pos=QPoint(w.canvas.width() // 2, w.canvas.height() // 2))
            assert w.dragging and not w.relocator.active and w.windowOpacity() == 1 and w.pos() == new
            QTest.mouseRelease(w.canvas, Qt.MouseButton.LeftButton,
                               pos=QPoint(w.canvas.width() // 2, w.canvas.height() // 2))
            self.checks.append('Qt press cancels fade-in at the new position and restores full opacity')

            # Establish a manual position, then ensure autonomous movement never overwrites it on disk.
            w.manual_position = QPoint(area.left() + 40, area.top() + 40)
            w.save_state()
            expected = QPoint(w.manual_position)
            self.full_cycle('persistence')
            assert w.manual_position == expected
            w.save_state()
            saved = json.loads(w.state_path.read_text(encoding='utf-8'))
            assert (saved['x'], saved['y']) == (expected.x(), expected.y())
            self.checks.append('autonomous destination does not overwrite persisted manual position')
            self.finish(True)
        except Exception:
            self.finish(False, traceback.format_exc())

    def finish(self, passed, error=None):
        if self.done:
            return
        self.done = True
        report = {'passed': passed, 'checks': self.checks, 'cycles': self.records,
                  'input_method': 'actual QOpenGLWidget plus QTest mouse events'}
        if error:
            report['error'] = error
            print(error, flush=True)
        (self.out / 'probe.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('RELOCATION_VERIFY', passed, self.checks, flush=True)
        self.w.close()
        QTimer.singleShot(0, lambda: QApplication.instance().exit(0 if passed else 1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='hibana')
    args = parser.parse_args()
    registry = ProfileRegistry(ROOT)
    profile = registry.get(args.profile)
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
    window = PetWindow(model, ROOT / 'artifacts' / 'relocation' / f'{profile.id}-probe-state.json',
                       profile=profile, registry=registry)
    probe = Probe(window)
    window.canvas.ready.connect(lambda: QTimer.singleShot(300, probe.start))
    window.show()
    code = app.exec()
    window.canvas.cleanup()
    live2d.dispose()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
