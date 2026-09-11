"""Real Qt/OpenGL probe for the desktop pet's maximum-size path."""
import argparse
import json
import sys
import traceback

import live2d.v3 as live2d
from OpenGL.GL import glGetIntegerv, GL_MAX_RENDERBUFFER_SIZE, GL_MAX_VIEWPORT_DIMS
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from desktop_pet import PetWindow, SIZE_POLICY
from model_profiles import ProfileRegistry
from prepare_model import ROOT


class Probe:
    def __init__(self, window, restore=False):
        self.w = window
        self.restore = restore
        self.before_frames = 0

    def start(self):
        try:
            self.before_frames = self.w.canvas.frames
            if self.restore:
                assert self.w.character_height == SIZE_POLICY['maximum']
            else:
                assert self.w._input_region is not None
                self.w.resize_character(SIZE_POLICY['maximum'])
            QTimer.singleShot(1800, self.check)
        except Exception:
            self.finish(False, traceback.format_exc())

    def check(self):
        try:
            w = self.w
            expected_h = round(SIZE_POLICY['maximum'] *
                               w.profile['window_height_per_character_height'])
            assert w.character_height == SIZE_POLICY['maximum']
            assert w.canvas.height() == expected_h
            assert w.canvas.width() == round(expected_h * w.profile['window_aspect'])
            assert w.width() - w.canvas.width() <= 1024
            assert w.height() - w.canvas.height() <= 1024
            assert w.canvas.frames > self.before_frames and not w.canvas.error
            assert not w.mask().isEmpty()

            w.canvas.makeCurrent()
            viewport = [int(v) for v in glGetIntegerv(GL_MAX_VIEWPORT_DIMS)]
            renderbuffer = int(glGetIntegerv(GL_MAX_RENDERBUFFER_SIZE))
            ratio = w.canvas.devicePixelRatioF()
            physical = [round(w.canvas.width() * ratio), round(w.canvas.height() * ratio)]
            assert physical[0] <= viewport[0] and physical[1] <= viewport[1]
            assert max(physical) <= renderbuffer

            area = w.available_screen().availableGeometry()
            _, _, right, bottom = w.movement_limits(area)
            w.move_content_to(QPoint(right, bottom))
            assert w.visual_within_movement_area(area)
            visual = w.visual_geometry()
            assert visual.right() == area.right()
            assert visual.bottom() == area.bottom() + int(
                visual.height() * w.profile['bottom_offscreen_fraction'])
            w.manual_position = w.content_position()
            w.save_state()
            saved = json.loads(w.state_path.read_text(encoding='utf-8'))
            assert saved['height'] == SIZE_POLICY['maximum']

            report = {
                'passed': True, 'profile': w.model_profile.id,
                'restored': self.restore,
                'character_height': w.character_height,
                'canvas': [w.canvas.width(), w.canvas.height()],
                'window': [w.width(), w.height()],
                'physical_framebuffer': physical,
                'gl_max_viewport': viewport,
                'gl_max_renderbuffer': renderbuffer,
                'frames_after_resize': w.canvas.frames - self.before_frames,
            }
            self.finish(True, report=report)
        except Exception:
            self.finish(False, traceback.format_exc())

    def finish(self, passed, error=None, report=None):
        result = report or {'passed': passed, 'profile': self.w.model_profile.id, 'error': error}
        out = ROOT / 'artifacts' / 'large-size'
        out.mkdir(parents=True, exist_ok=True)
        (out / f'{self.w.model_profile.id}.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print('LARGE_SIZE_VERIFY', passed, result, flush=True)
        self.w.close()
        QApplication.instance().exit(0 if passed else 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', default='hibana')
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    fmt = QSurfaceFormat()
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    fmt.setAlphaBufferSize(8)
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv[:1])
    live2d.init()
    registry = ProfileRegistry(ROOT)
    profile = registry.get(args.profile)
    state = ROOT / 'artifacts' / 'large-size' / f'{profile.id}-state.json'
    if state.exists() and not args.restore:
        state.unlink()
    window = PetWindow(profile.model_path, state, profile=profile, registry=registry)
    probe = Probe(window, args.restore)
    window.canvas.ready.connect(lambda: QTimer.singleShot(700, probe.start))
    window.show()
    code = app.exec()
    window.canvas.cleanup()
    live2d.dispose()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
