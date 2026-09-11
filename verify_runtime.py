"""Integration checks against the actual widget, native model and framebuffer."""
import json
import platform
import traceback
import time
from PySide6.QtCore import QTimer, QPointF, QEvent, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from prepare_model import ROOT


class Verifier:
    def __init__(self, window):
        self.w = window
        self.c = window.canvas
        self.out = ROOT / 'artifacts'
        self.out.mkdir(exist_ok=True)
        self.report = {'python': platform.python_version(), 'checks': []}
        self.steps = []
        self.finished = False
        self.watchdog = QTimer(window)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(self.timeout)
        self.watchdog.start(90000)
        self.c.failed.connect(self.failed)

    def failed(self, error):
        self.report['error'] = error
        self.finish(False)

    def timeout(self):
        self.failed('Verification timed out (90 seconds)')

    def snapshot(self, name):
        image = self.c.grabFramebuffer()
        assert not image.isNull(), 'Empty framebuffer'
        assert image.save(str(self.out / (name + '.png')))
        self.w.grab().save(str(self.out / (name + '_window.png')))
        from PIL import Image
        im = Image.open(self.out / (name + '.png')).convert('RGB')
        assert len(im.resize((100, 100)).getcolors(10001)) > 100, 'Blank model rendering'

    def check(self, name, callback):
        callback()
        assert not self.c.error, self.c.error
        self.report['checks'].append(name)

    def pid(self, semantic):
        parameter_id = self.c.profile.parameter_id(semantic)
        assert parameter_id in self.c.params, (semantic, parameter_id)
        return parameter_id

    def start(self):
        self.report['gl'] = self.c.gl_info
        self.report['parameters'] = self.c.params
        for box in self.w.checks.values():
            box.setChecked(False)
        self.steps.append((800, 'initial visible model', lambda: self.snapshot('idle')))
        physics_outputs = self.physics_output_ids()
        skipped = [pid for pid in self.w.spins if pid in physics_outputs]
        if skipped:
            self.report['physics_owned_controls_skipped'] = skipped
        for pid in self.w.spins:
            if pid in physics_outputs:
                continue
            for bound in ('min', 'max'):
                self.steps.append((30, f'set {pid} {bound}', lambda p=pid, b=bound: self.w.spins[p].setValue(self.c.params[p][b])))
                self.steps.append((100, f'verify {pid} {bound}', lambda p=pid, b=bound: self.assert_param(p, self.c.params[p][b])))
            self.steps.append((30, f'reset {pid}', lambda p=pid: self.w.spins[p].setValue(self.c.params[p]['default'])))
        mouth = self.pid('mouth_open')
        head_x = self.pid('head_angle_x')
        self.steps += [(50, 'mouth open', lambda: self.w.spins[mouth].setValue(1)),
                       (200, 'mouth framebuffer', lambda: self.snapshot('mouth')),
                       (50, 'restore mouth', lambda: self.w.spins[mouth].setValue(0))]
        physics_id = self.c.profile.parameter_id('physics_head_bounce')
        if physics_id in self.c.params:
            self.steps += [(30, 'physics baseline', self.physics_baseline),
                           (30, 'physics head impulse', lambda: self.w.spins[head_x].setValue(30)),
                           (180, 'physics responds to head input', self.physics_check)]
        else:
            self.report['physics_probe'] = {'skipped': 'profile has no physics output mapping'}
        self.steps += [(100, 'head framebuffer', lambda: self.snapshot('head')),
                       (30, 'restore head', lambda: self.w.spins[head_x].setValue(0)),
                       (30, 'blink close phase', self.blink_close),
                       (40, 'blink closes actual eyes', self.blink_check),
                       (30, 'disable test blink', lambda: self.w.checks['blink'].setChecked(False))]
        for i in range(len(self.c.assets['expressions'])):
            self.steps.append((30, f'select expression {i}', lambda n=i: self.w.expression.setCurrentIndex(n+1)))
            self.steps.append((650, f'render expression {i}', lambda n=i: self.snapshot(f'expression_{n:02}')))
        self.steps += [(30, 'clear expression', lambda: self.w.expression.setCurrentIndex(0)),
                       (700, 'expression state cleared', self.assert_expression_reset),
                       (30, 'resize wide', lambda: self.w.resize(1350, 700)),
                       (300, 'resize framebuffer', lambda: self.snapshot('resized')),
                       (30, 'enable tracking', lambda: self.w.checks['tracking'].setChecked(True)),
                       (30, 'mouse tracking changes values', self.tracking),
                       (30, 'enable blink', lambda: self.w.checks['blink'].setChecked(True))]
        sleep_asset = self.c.profile.motion_asset('sleep')
        if sleep_asset and self.c.profile.runtime_file(sleep_asset, 'motion'):
            self.steps += [(30, 'start sleep', self.w.sleep_button.click),
                           (1500, 'sleep framebuffer', lambda: self.snapshot('sleep_1')),
                           (3000, 'sleep loop and input lock', self.sleep_check),
                           (30, 'stop sleep', self.w.wake_button.click),
                           (30, 'disable blink for reset check', lambda: self.w.checks['blink'].setChecked(False)),
                           (300, 'wake idle reset', self.wake_check)]
        else:
            self.report['sleep_probe'] = {'skipped': 'profile has no sleep motion'}
        self.next()

    def physics_output_ids(self):
        model_data = json.loads(self.c.path.read_text(encoding='utf-8-sig'))
        relative = model_data.get('FileReferences', {}).get('Physics')
        if not relative:
            return set()
        physics_path = self.c.path.parent / relative
        physics = json.loads(physics_path.read_text(encoding='utf-8-sig'))
        return {
            output.get('Destination', {}).get('Id')
            for setting in physics.get('PhysicsSettings', [])
            for output in setting.get('Output', [])
            if output.get('Destination', {}).get('Target') == 'Parameter'
        }

    def assert_param(self, pid, value):
        actual = self.c.model.GetParameterValue(self.c.params[pid]['index'])
        assert abs(actual - value) < 0.02, (pid, actual, value)

    def physics_baseline(self):
        parameter = self.pid('physics_head_bounce')
        self.physics_before = self.c.model.GetParameterValue(self.c.params[parameter]['index'])

    def physics_check(self):
        parameter = self.pid('physics_head_bounce')
        value = self.c.model.GetParameterValue(self.c.params[parameter]['index'])
        self.report['physics_probe'] = {'parameter': parameter, 'before': self.physics_before, 'after': value}
        assert abs(value - self.physics_before) > .01, 'Physics output did not respond'

    def blink_close(self):
        self.w.checks['blink'].setChecked(True)
        self.c.started = time.monotonic() - .10

    def blink_check(self):
        parameter = self.pid('eye_open_left')
        value = self.c.model.GetParameterValue(self.c.params[parameter]['index'])
        assert value < .65, value
        self.snapshot('blink')

    def assert_expression_reset(self):
        for pid in self.c.expression_params:
            if pid in self.c.params:
                self.assert_param(pid, self.c.params[pid]['default'])

    def tracking(self):
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(self.c.width() * .9, self.c.height() * .1), QPointF(), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.c.mouseMoveEvent(event)
        assert self.c.values[self.pid('eye_gaze_x')] > 0

    def sleep_check(self):
        assert self.c.sleeping and not self.w.controls.isEnabled()
        self.assert_param(self.pid('eye_open_left'), 0)
        self.assert_param(self.pid('eye_open_right'), 0)
        head_z = self.pid('head_angle_z')
        assert abs(self.c.model.GetParameterValue(self.c.params[head_z]['index'])) > 1
        previous = dict(self.c.values)
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(0, 0), QPointF(), Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        self.c.mouseMoveEvent(event)
        assert self.c.values == previous
        self.snapshot('sleep_2')

    def wake_check(self):
        assert not self.c.sleeping and self.c.model.IsMotionFinished()
        assert self.w.controls.isEnabled()
        for pid in self.w.spins:
            self.assert_param(pid, self.c.params[pid]['default'])
        self.assert_expression_reset()
        asset = self.c.profile.motion_asset('sleep')
        runtime_file = self.c.profile.runtime_file(asset, 'motion')
        data = json.loads((self.c.path.parent / runtime_file).read_text(encoding='utf-8-sig'))
        for curve in data['Curves']:
            pid = curve['Id']
            if pid in self.c.params:
                self.assert_param(pid, self.c.params[pid]['default'])
        self.snapshot('wake')

    def next(self):
        if not self.steps:
            self.finish(True)
            return
        delay, name, callback = self.steps.pop(0)
        def run():
            try:
                self.check(name, callback)
                self.next()
            except Exception:
                self.report['error'] = traceback.format_exc()
                print(self.report['error'], flush=True)
                self.finish(False)
        QTimer.singleShot(delay, run)

    def finish(self, passed):
        if self.finished:
            return
        self.finished = True
        self.watchdog.stop()
        self.report['passed'] = passed
        self.report['frames'] = self.c.frames
        (self.out / 'verification.json').write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'VERIFICATION passed={passed} frames={self.c.frames}', flush=True)
        self.w.close()
        QApplication.instance().exit(0 if passed else 1)
