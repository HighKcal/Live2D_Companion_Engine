"""Windows Live2D prototype: native Cubism rendering in a QOpenGLWidget."""
import argparse
import gc
import json
import math
from pathlib import Path
import sys
import time
import traceback

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton, QScrollArea,
    QSlider, QVBoxLayout, QWidget)
from OpenGL.GL import glGetString, GL_VERSION, GL_RENDERER, glViewport
import live2d.v3 as live2d
from prepare_model import ROOT, prepare
from model_profiles import ProfileError, ProfileRegistry


class Canvas(QOpenGLWidget):
    ready = Signal()
    failed = Signal(str)
    state_changed = Signal()

    def __init__(self, path, transparent=False, profile=None):
        super().__init__()
        self.path = path
        self.profile = profile or ProfileRegistry(ROOT).select(model_path=path)
        self.transparent = transparent
        self.assets = self.profile.assets
        self.expression_indexes = {}
        self.motion_indexes = {}
        self.model = None
        self.params = {}
        self.values = {}
        self.expression_params = set()
        self.expression = -1
        self.sleeping = False
        self.blink = True
        self.tracking = False
        self.breath = True
        self.frames = 0
        self.error = None
        self.gl_info = {}
        self.started = time.monotonic()
        self.setMouseTracking(True)
        self.setMinimumSize(400, 500)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.update)

    def initializeGL(self):
        try:
            live2d.glInit()
            self.gl_info = {name: glGetString(token).decode() for name, token in [('version', GL_VERSION), ('renderer', GL_RENDERER)]}
            self.model = live2d.LAppModel()
            self.model.LoadModelJson(str(self.path))
            self.model.SetAutoBlinkEnable(False)
            self.model.SetAutoBreathEnable(False)
            for i, pid in enumerate(self.model.GetParamIds()):
                p = self.model.GetParameter(i)
                self.params[pid] = {'index': i, 'min': p.min, 'max': p.max, 'default': p.default}
            if not self.params:
                raise RuntimeError('Model loaded without parameters')
            control_ids = [self.profile.parameter_id(item.get('parameter'))
                           for item in self.profile.controls]
            self.values = {p: self.params[p]['default'] for p in control_ids if p in self.params}
            for i, e in enumerate(self.assets['expressions']):
                p = self.path.parent / e['file']
                self.model.LoadExtraExpression(f'exp{i}', str(p))
                self.expression_indexes[self.profile.asset_identifier(e)] = i
                self.expression_params.update(v['Id'] for v in json.loads(p.read_text(encoding='utf-8-sig'))['Parameters'])
            for motion in self.assets['motions']:
                result = self.model.LoadExtraMotion('ProfileMotions', str(self.path.parent / motion['file']))
                if result < 0:
                    raise RuntimeError(f'Motion load failed: {motion["file"]}')
                self.motion_indexes[self.profile.asset_identifier(motion)] = result
            self.model.Resize(self.width(), self.height())
            self.context().aboutToBeDestroyed.connect(self.cleanup)
            self.timer.start()
            self.ready.emit()
        except Exception:
            self.error = traceback.format_exc()
            print(self.error, flush=True)
            self.failed.emit(self.error)

    def set_param(self, pid, value):
        if pid in self.params:
            p = self.params[pid]
            self.model.SetParameterValue(pid, max(p['min'], min(p['max'], value)))

    def reset_parameters(self):
        self.model.ResetParameters()
        for pid, p in self.params.items():
            self.set_param(pid, p['default'])

    def select_expression(self, expression):
        if not self.model or self.sleeping:
            return
        self.model.ResetExpressions()
        for pid in self.expression_params:
            if pid in self.params:
                self.set_param(pid, self.params[pid]['default'])
        if isinstance(expression, int):
            expression = (self.profile.asset_identifier(self.assets['expressions'][expression])
                          if 0 <= expression < len(self.assets['expressions']) else None)
        self.expression = expression if expression else -1
        index = self.expression_indexes.get(expression)
        if index is not None:
            self.model.SetExpression(f'exp{index}')

    def sleep(self):
        if not self.model:
            return False
        asset = self.profile.motion_asset('sleep')
        index = self.motion_indexes.get(asset)
        if index is None:
            return False
        self.model.StopAllMotions()
        self.model.ResetExpressions()
        self.reset_parameters()
        self.sleeping = True
        priority = self.profile.motion_actions['sleep'].get('priority', 3)
        self.model.StartMotion('ProfileMotions', index, priority)
        self.state_changed.emit()
        return True

    def wake(self):
        if not self.model:
            return
        self.model.StopAllMotions()
        self.model.ResetExpressions()
        self.reset_parameters()
        self.sleeping = False
        self.expression = -1
        control_ids = [self.profile.parameter_id(item.get('parameter'))
                       for item in self.profile.controls]
        self.values = {p: self.params[p]['default'] for p in control_ids if p in self.params}
        self.state_changed.emit()

    def resizeGL(self, w, h):
        if self.model:
            self.model.Resize(max(1, w), max(1, h))

    def paintGL(self):
        live2d.clearBuffer(*( (0, 0, 0, 0) if self.transparent else (0.12, 0.14, 0.19, 1.0)))
        if not self.model or self.error:
            return
        try:
            ratio = self.devicePixelRatioF()
            glViewport(0, 0, round(self.width() * ratio), round(self.height() * ratio))
            if not self.sleeping:
                # Reset the union, not only the new expression: Add 0 cannot undo old state.
                for pid in self.expression_params:
                    if pid in self.params:
                        self.set_param(pid, self.params[pid]['default'])
                for pid, value in self.values.items():
                    self.set_param(pid, value)
                elapsed = time.monotonic() - self.started
                if self.blink:
                    phase = elapsed % 3.7
                    openness = min(1.0, abs(phase - 0.15) / 0.15) if phase < 0.30 else 1.0
                    for semantic in ('eye_open_left', 'eye_open_right'):
                        pid = self.profile.parameter_id(semantic)
                        if pid in self.params:
                            p = self.params[pid]
                            self.set_param(pid, p['min'] + openness * (p['default'] - p['min']))
                breath = self.profile.parameter_id('breath')
                if self.breath and breath in self.params:
                    p = self.params[breath]
                    self.set_param(breath, p['min'] + (0.5 + 0.5 * math.sin(elapsed * 1.7)) * (p['max'] - p['min']))
            self.apply_behavior()
            # Native Update evaluates motions, expressions, physics and pose, then mesh vertices.
            self.model.Update()
            self.model.Draw()
            self.frames += 1
        except Exception:
            self.error = traceback.format_exc()
            self.timer.stop()
            self.failed.emit(self.error)

    def apply_behavior(self):
        """Optional absolute parameter layer, before physics and mesh update."""

    def mouseMoveEvent(self, event):
        if self.tracking and not self.sleeping:
            for semantic, v in [('eye_gaze_x', 2 * event.position().x() / self.width() - 1),
                                ('eye_gaze_y', 1 - 2 * event.position().y() / self.height())]:
                pid = self.profile.parameter_id(semantic)
                if pid in self.params:
                    p = self.params[pid]
                    self.values[pid] = p['default'] + v * (p['max'] - p['default'] if v >= 0 else p['default'] - p['min'])

    def cleanup(self):
        self.timer.stop()
        if self.model:
            self.makeCurrent()
            self.model.DestroyRenderer()
            self.model = None
            gc.collect()
            live2d.glRelease()
            self.doneCurrent()


class Window(QMainWindow):
    def __init__(self, path, shared_canvas=None, profile=None):
        super().__init__()
        self.profile = shared_canvas.profile if shared_canvas is not None else profile
        self.setWindowTitle(f'{self.profile.display_name} · Live2D 모델 실험실')
        self.resize(1120, 820)
        self.shared_canvas = shared_canvas is not None
        self.canvas = shared_canvas or Canvas(path, profile=self.profile)
        root = QWidget()
        layout = QHBoxLayout(root)
        if not self.shared_canvas:
            layout.addWidget(self.canvas, 1)
        panel = QWidget()
        panel.setFixedWidth(350)
        self.form = QVBoxLayout(panel)
        self.status = QLabel('OpenGL 및 모델 로딩 중…')
        self.status.setWordWrap(True)
        self.form.addWidget(self.status)
        self.controls = QWidget()
        self.fields = QFormLayout(self.controls)
        self.form.addWidget(self.controls)
        self.spins = {}
        self.checks = {}
        for attr, label, checked in [('blink', '자동 눈 깜빡임', True), ('tracking', '모델 영역에서 마우스로 시선 추적', False), ('breath', '자동 호흡', True)]:
            box = QCheckBox(label)
            box.setChecked(checked)
            box.toggled.connect(lambda value, a=attr: setattr(self.canvas, a, value))
            self.fields.addRow(box)
            self.checks[attr] = box
        self.expression = QComboBox()
        self.expression.addItem('기본 표정', None)
        for e in self.canvas.assets['expressions']:
            self.expression.addItem(e['name'], self.profile.asset_identifier(e))
        self.expression.currentIndexChanged.connect(
            lambda i: self.canvas.select_expression(self.expression.itemData(i)))
        self.fields.addRow('표정', self.expression)
        self.sleep_button = QPushButton('수면 모션 재생')
        self.sleep_button.clicked.connect(self.canvas.sleep)
        self.form.addWidget(self.sleep_button)
        self.wake_button = QPushButton('중지 · 정상 대기로 초기화')
        self.wake_button.clicked.connect(self.canvas.wake)
        self.form.addWidget(self.wake_button)
        zoom = QSlider(Qt.Orientation.Horizontal)
        zoom.setRange(40, 180)
        zoom.setValue(100)
        zoom.valueChanged.connect(lambda v: self.canvas.model and self.canvas.model.SetScale(v / 100))
        zoom.setEnabled(not self.shared_canvas)
        self.form.addWidget(QLabel('모델 확대 / 축소'))
        self.form.addWidget(zoom)
        note = QLabel('눈 수동 조절: 자동 깜빡임 해제\n시선 수동 조절: 마우스 추적 해제\n표정은 얼굴 수동값 위에 적용됩니다.\n수면 중 얼굴 제어와 자동 동작을 잠급니다.')
        note.setWordWrap(True)
        self.form.addWidget(note)
        self.form.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(panel)
        scroll.setFixedWidth(375)
        layout.addWidget(scroll)
        self.setCentralWidget(root)
        self.controls.setEnabled(False)
        self.sleep_button.setEnabled(False)
        self.wake_button.setEnabled(False)
        self.canvas.ready.connect(self.populate)
        self.canvas.failed.connect(self.on_error)
        self.canvas.state_changed.connect(self.sync)
        if self.shared_canvas and self.canvas.model:
            self.resize(400, 800)
            self.populate()

    def on_error(self, error):
        self.status.setText('실행 오류:\n' + error)

    def populate(self):
        for item in self.profile.controls:
            pid = self.profile.parameter_id(item.get('parameter'))
            label = item.get('label', item.get('parameter', 'parameter'))
            if pid not in self.canvas.params:
                self.fields.addRow(label, QLabel('이 모델에 없음'))
                continue
            p = self.canvas.params[pid]
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(p['min'], p['max'])
            spin.setSingleStep((p['max'] - p['min']) / 100)
            spin.setValue(p['default'])
            spin.setToolTip(f"{pid}\n런타임 범위 {p['min']} … {p['max']} / 기본 {p['default']}")
            spin.valueChanged.connect(lambda v, key=pid: self.canvas.values.__setitem__(key, v))
            self.spins[pid] = spin
            self.fields.addRow(label, spin)
        self.controls.setEnabled(True)
        self.sleep_button.setEnabled(self.profile.motion_asset('sleep') in self.canvas.motion_indexes)
        self.wake_button.setEnabled(True)
        self.sync()

    def sync(self):
        c = self.canvas
        self.controls.setEnabled(not c.sleeping)
        self.sleep_button.setEnabled(
            not c.sleeping and self.profile.motion_asset('sleep') in c.motion_indexes)
        if not c.sleeping:
            self.expression.setCurrentIndex(
                0 if c.expression == -1 else max(0, self.expression.findData(c.expression)))
            for pid, spin in self.spins.items():
                spin.setValue(c.values[pid])
        self.status.setText(('수면 재생 중' if c.sleeping else '정상 대기') + f' · 파라미터 {len(c.params)}개\n' + c.gl_info.get('renderer', ''))

    def closeEvent(self, event):
        if not self.shared_canvas:
            self.canvas.cleanup()
        super().closeEvent(event)


def main():
    # pythonw has no console streams; retain diagnostics without a console window.
    if sys.stdout is None or sys.stderr is None:
        (ROOT / 'local').mkdir(exist_ok=True)
        log = (ROOT / 'local' / 'pet.log').open('w', encoding='utf-8', buffering=1)
        sys.stdout = sys.stderr = log
    parser = argparse.ArgumentParser()
    parser.add_argument('--zip', type=Path)
    parser.add_argument('--model', type=Path)
    parser.add_argument('--profile', help='Profile id from profiles/*.json')
    parser.add_argument('--verify', action='store_true', help='Run automated real-GL checks and exit')
    parser.add_argument('--lab', action='store_true', help='Open the original model laboratory')
    parser.add_argument('--verify-pet', choices=['foundation', 'full', 'restore'])
    parser.add_argument('--state', type=Path, help='Optional local state path for isolated verification')
    args = parser.parse_args()
    registry = ProfileRegistry(ROOT)
    for filename, error in registry.errors.items():
        print(f'PROFILE ignored {filename}: {error}', flush=True)
    try:
        path = prepare(args.zip) if args.zip else args.model
        profile = registry.select(args.profile, model_path=path)
        path = profile.model_path
        if args.profile:
            registry.set_active(profile.id)
    except ProfileError as error:
        parser.error(str(error))
    fmt = QSurfaceFormat()
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setAlphaBufferSize(8)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv[:1])
    live2d.init()
    if args.lab or args.verify:
        win = Window(path.resolve(), profile=profile)
    else:
        from desktop_pet import PetWindow
        win = PetWindow(path.resolve(), state_path=args.state, profile=profile, registry=registry)
    if args.verify:
        from verify_runtime import Verifier
        verifier = Verifier(win)
        win.canvas.ready.connect(verifier.start)
    if args.verify_pet:
        from verify_pet import PetVerifier
        verifier = PetVerifier(win, args.verify_pet)
        win.canvas.ready.connect(verifier.start)
    win.show()
    code = app.exec()
    win.canvas.cleanup()
    live2d.dispose()
    return code


if __name__ == '__main__':
    sys.exit(main())
