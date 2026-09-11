"""Exercise gesture/state logic with QTest clicks and deterministic production hover callbacks."""
import argparse
import json
import sys
import time
import math
import traceback
from collections import Counter
import numpy as np
from PySide6.QtCore import QObject, QEvent, QPoint, QTimer, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import live2d.v3 as live2d
from desktop_pet import PetWindow
from prepare_model import ROOT
from model_profiles import ProfileRegistry


class EventProbe(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.counts = Counter()
        self.moves = []
        self.phase = 'initial'

    def eventFilter(self, obj, event):
        if obj in (self.w, self.w.canvas) and event.type() in (QEvent.Type.MouseMove, QEvent.Type.Enter, QEvent.Type.Leave):
            key = ('canvas' if obj is self.w.canvas else 'window') + '/' + event.type().name
            self.counts[key] += 1
            if event.type() == QEvent.Type.MouseMove:
                self.moves.append({'phase': self.phase, 'object': key, 'xy': [event.position().x(), event.position().y()],
                    'model': self.w.to_model(event.position()), 'turns_before': len(self.w.detector.turns),
                    'time': time.monotonic(), 'precise_time': time.perf_counter(), 'buttons': event.buttons().value})
        return False


def main():
    fmt = QSurfaceFormat()
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    fmt.setAlphaBufferSize(8)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='hibana')
    args = parser.parse_args()
    app = QApplication([])
    live2d.init()
    profile = ProfileRegistry(ROOT).select(args.profile)
    w = PetWindow(profile.model_path, ROOT/'artifacts/petting/probe-state.json',
                  profile=profile)
    w.resize_character(300)
    w.move(450, 180)
    probe = EventProbe(w)
    app.installEventFilter(probe)

    def run():
        w.autonomous = False
        # This probe isolates the gesture; expression priority is covered by probe_idle.py.
        w.idle.next_ambient = float('inf')
        w.idle.next_major = float('inf')
        output = {'phases': [], 'checks': [], 'input_method': 'QTest movement plus deterministic production hover callback',
                  'supported': {p: w.canvas.params[p] for p in w.reaction_values if p in w.canvas.params}}
        def head_point(fraction):
            left, bottom, right, top = w.profile['head_rect_model']
            x = (left + right) / 2 + fraction * (right - left) / 2
            y = (bottom + top) / 2
            matrix = np.array(w.canvas.model._model.GetMvp()).reshape((4, 4), order='F')
            clip = matrix @ np.array([x, y, 0, 1])
            return QPoint(round((clip[0] / clip[3] + 1) * w.canvas.width() / 2),
                          round((1 - clip[1] / clip[3]) * w.canvas.height() / 2))
        def expire_response():
            # Production holds are intentionally 3-4 seconds; shorten only this probe.
            w.clear_petting_input()
            w.reaction_until = time.monotonic() - .01
            QTest.qWait(1500)
        for phase, fractions in [('single', [-.65+i*(1.3/35) for i in range(36)]),
                                 ('stationary', [0]*35),
                                 ('repeated', [.65*math.sin(i*.22) for i in range(105)])]:
            probe.phase = phase
            before = w.reaction_count
            w.detector.reset()
            for fx in fractions:
                point = head_point(fx)
                QTest.mouseMove(w.canvas, point, 25)
                w.hover(point, Qt.MouseButton.NoButton)
                QTest.qWait(10)
            QTest.qWait(450)
            output['phases'].append({'name': phase, 'reaction_delta': w.reaction_count-before,
                                    'level': w.reaction_level, 'turns': len(w.detector.turns)})
        output['counts'] = dict(probe.counts)
        output['moves'] = probe.moves
        out = ROOT/'artifacts/petting'
        out.mkdir(parents=True, exist_ok=True)
        (out/'probe.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
        w.canvas.grabFramebuffer().save(str(out/'probe.png'))
        try:
            assert [v['reaction_delta'] for v in output['phases'][:2]] == [0,0]
            assert output['phases'][2]['reaction_delta'] >= 1
            assert output['phases'][2]['level'] > .9
            output['checks'].append('production hover route: single/stationary rejected, repeated accepted')
            for pid,target in w.reaction_values.items():
                actual = w.canvas.model.GetParameterValue(w.canvas.params[pid]['index'])
                assert abs(actual-target) < .04, (pid,actual,target)
            output['checks'].append('supported reaction values survive blink/physics')
            expire_response()
            assert w.reaction_level == 0 and not w.detector.active
            output['checks'].append('stationary after petting ends response, no hover retrigger')

            # Start with an existing expression and optional gaze tracking enabled.
            previous_expression = w.model_profile.expression_candidates('negative')[-1]['asset']
            w.canvas.select_expression(previous_expression)
            probe.phase = 'expression_and_relocation'
            w.canvas.tracking = True
            QTest.qWait(700)
            saved_count = w.reaction_count
            w.autonomous = True
            w.was_blocked = False
            w.pointer_over_interactive_area = lambda: False
            w.relocator.due = time.monotonic()-1
            w.tick()
            assert w.relocator.active
            for i in range(110):
                point = head_point(.65*math.sin(i*.22))
                QTest.mouseMove(w.canvas, point, 25)
                w.hover(point, Qt.MouseButton.NoButton)
                QTest.qWait(10)
                if i == 10:
                    assert not w.relocator.active and w.windowOpacity() == 1, 'User stroke must interrupt relocation'
            assert w.reaction_count > saved_count
            expire_response()
            assert w.reaction_level == 0 and w.reaction_expression is None and w.canvas.expression == previous_expression, (
                w.reaction_level, w.reaction_expression, w.canvas.expression)
            assert w.relocator.due > time.monotonic()+15
            output['checks'].append('stroke interrupts relocation; previous expression restored; delayed idle resume')

            before = w.reaction_count
            start = w.pos()
            point = QPoint(w.width()//2,round(w.height()*.44))
            QTest.mousePress(w.canvas,Qt.MouseButton.LeftButton,pos=point)
            assert w.pointer_pressed and not w.dragging and w.detector.last is None
            QTest.mouseMove(w.canvas, point + QPoint(15, 0), 30)
            assert w.dragging
            for dx in [15,30,10,-10,15]:
                QTest.mouseMove(w.canvas,point+QPoint(dx,0),30)
                QTest.qWait(30)
            QTest.mouseRelease(w.canvas,Qt.MouseButton.LeftButton,pos=point)
            assert not w.dragging and w.reaction_count == before and w.detector.last is None
            assert w.pos() != start
            QTest.mouseMove(w.canvas,point+QPoint(8,0),10)
            assert w.reaction_count == before
            output['checks'].append('Qt press/move/release drags without petting, including release tail')

            QTest.qWait(700)
            if w.model_profile.motion_asset('sleep'):
                assert w.canvas.sleep()
                sleep_pos = w.pos()
                for i in range(75):
                    point = head_point(.65*math.sin(i*.25))
                    QTest.mouseMove(w.canvas, point, 25)
                    w.hover(point, Qt.MouseButton.NoButton)
                    QTest.qWait(10)
                assert w.reaction_count == before and w.reaction_level == 0 and w.pos() == sleep_pos
                eye_left = w.model_profile.parameter_id('eye_open_left')
                assert w.canvas.model.GetParameterValue(w.canvas.params[eye_left]['index']) < .05
                w.canvas.wake()
                QTest.qWait(700)
                assert w.canvas.model.IsMotionFinished()
                output['checks'].append('sleep blocks native stroke events and movement, wake succeeds')
            else:
                assert not w.canvas.sleep() and not w.canvas.sleeping
                output['checks'].append('unsupported sleep is skipped without changing state')

            for height in (240,400):
                w.resize_character(height)
                w.move(400,160)
                QTest.qWait(600)
                before = w.reaction_count
                for i in range(95):
                    point = head_point(.65*math.sin(i*.24))
                    QTest.mouseMove(w.canvas, point, 25)
                    w.hover(point, Qt.MouseButton.NoButton)
                    QTest.qWait(10)
                assert w.reaction_count > before, height
                w.canvas.grabFramebuffer().save(str(out/f'reaction-{height}.png'))
                expire_response()
            output['checks'].append('production hover callback at 240px and 400px, after window relocation')
            output['passed'] = True
        except Exception:
            output['passed'] = False
            output['error'] = traceback.format_exc()
            print(output['error'],flush=True)
        output['counts'] = dict(probe.counts)
        output['moves'] = probe.moves
        (out/'probe.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in output.items() if k != 'moves'}, indent=2), flush=True)
        w.close()
        app.exit(0 if output['passed'] else 1)

    w.canvas.ready.connect(lambda: QTimer.singleShot(900, run))
    QTimer.singleShot(90000, w.close)
    w.show()
    result = app.exec()
    live2d.dispose()
    return result


if __name__ == '__main__':
    sys.exit(main())
