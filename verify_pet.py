"""Actual OpenGL integration checks; synthetic Qt inputs are labelled as such."""
import json
import time
import traceback
import ctypes
from ctypes import wintypes
from PySide6.QtCore import QPoint, QPointF, QRect, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PIL import Image
from prepare_model import ROOT


class PetVerifier:
    def __init__(self, window, stage):
        self.w = window
        self.stage = stage
        self.report = {'stage': stage, 'checks': [], 'input_method': 'programmatic Qt callbacks'}
        self.out = ROOT / 'artifacts' / 'pet'
        self.out.mkdir(parents=True, exist_ok=True)
        self.steps = []
        self.done = False
        self.foreground_before = int(ctypes.windll.user32.GetForegroundWindow())
        self.restored_geometry = window.geometry().getRect()
        self.watchdog = QTimer(window)
        self.watchdog.setSingleShot(True)
        self.watchdog.timeout.connect(lambda: self.fail('90 second timeout'))
        self.watchdog.start(90000)
        window.canvas.failed.connect(self.fail)

    def start(self):
        # Keep legacy regression deterministic; probe_idle.py owns timed-expression checks.
        self.w.idle.next_ambient = float('inf')
        self.w.idle.next_major = float('inf')
        if self.stage != 'restore':
            self.w.resize_character(300)
            area = self.w.available_screen().availableGeometry()
            self.w.move(area.center() - QPoint(self.w.width()//2, self.w.height()//2))
        self.report['gl'] = self.w.canvas.gl_info
        self.report['screens'] = [{'name': s.name(), 'available': s.availableGeometry().getRect(), 'dpr': s.devicePixelRatio()} for s in QApplication.screens()]
        self.steps = [(900, 'transparent framebuffer and full character bounds', self.transparency),
                      (50, 'native alpha click-through and character input', self.native_input),
                      (50, 'drag displacement and clamping', self.drag),
                      (50, 'state persisted', self.persistence),
                      (50, 'offscreen recovery', self.recovery),
                      (50, 'discovered character menu', self.character_menu),
                      (100, 'developer panel shares live model', self.panel)]
        if self.stage == 'full':
            reaction_restore_ms = max(1200, round(
                (self.w.profile['petting']['fade_seconds'] + 1.5) * 1000))
            self.steps += [(50, 'fade relocation and user intervention', self.relocation),
                           (50, 'petting ignores stationary pointer and single pass', self.no_false_pet),
                           (50, 'start existing expression', self.start_existing_expression)]
            for x in [-.65, -.2, .4, .65, .2, -.4, -.65, -.2, .4, .65, .2, -.4, -.65]:
                self.steps.append((120, 'hover stroke sample', lambda v=x: self.hover(v)))
            self.steps += [(400, 'petting reaction owns actual parameters', self.reaction),
                           (reaction_restore_ms, 'reaction fades and restores previous expression',
                            self.reaction_restored)]
            if self.w.model_profile.motion_asset('sleep') in self.w.canvas.motion_indexes:
                self.steps += [(50, 'sleep starts during interaction', self.sleep_start),
                               (1800, 'sleep prevents relocation and petting', self.sleep_check),
                               (50, 'wake', self.w.canvas.wake),
                               (300, 'wake returns to idle with cooldown', self.wake_check)]
            else:
                self.steps.append((50, 'unsupported sleep capability is disabled', self.no_sleep))
            self.steps.append((50, 'save resized position for next process', self.save_restore))
        if self.stage == 'restore':
            self.steps = [(900, 'new process restores saved size and position', self.restore)]
        self.next()

    def transparency(self):
        path = self.out / (self.stage + '.png')
        image = self.w.grab().toImage()
        assert image.hasAlphaChannel()
        image.save(str(path))
        im = Image.open(path).convert('RGBA')
        alpha = im.getchannel('A')
        bounds = alpha.getbbox()
        assert bounds and alpha.getpixel((0, 0)) == 0
        assert bounds[0] > 2 and bounds[1] > 2 and bounds[2] < im.width - 2 and bounds[3] < im.height - 2, bounds
        height = (bounds[3] - bounds[1]) / self.w.devicePixelRatioF()
        self.report['visible_character_height'] = height
        self.report['alpha_bounds'] = bounds
        assert 270 <= height <= 330, height
        content = self.w._content_size
        scale = self.w.profile['render_window_scale']
        self.report['window_geometry'] = self.w.geometry().getRect()
        self.report['content_geometry'] = self.w.content_geometry().getRect()
        self.report['visual_geometry'] = self.w.visual_geometry().getRect()
        self.report['transparent_padding'] = [self.w._content_offset.x(),
                                              self.w._content_offset.y()]
        assert abs(self.w.width() / content.width() - scale) < .01
        assert abs(self.w.height() / content.height() - scale) < .01
        assert abs(self.w._content_offset.x() * 2 - (self.w.width() - content.width())) <= 1
        assert abs(self.w._content_offset.y() * 2 - (self.w.height() - content.height())) <= 1
        self.report['native_mvp'] = self.w.canvas.model._model.GetMvp()

    def drag(self):
        start = self.w.pos()
        g = QPointF(start + QPoint(150, 200))
        self.w.begin_drag(g)
        self.w.drag_to(g + QPointF(-70, -60))
        self.w.end_drag()
        assert self.w.pos() == start + QPoint(-70, -60), (start, self.w.pos())
        assert not self.w.dragging

    def native_input(self):
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        rect = wintypes.RECT()
        user32.GetWindowRect(wintypes.HWND(int(self.w.winId())), ctypes.byref(rect))
        def root_at(x, y):
            handle = user32.WindowFromPoint(wintypes.POINT(rect.left+x, rect.top+y))
            return int(user32.GetAncestor(handle, 2) or 0)
        ratio = self.w.devicePixelRatioF()
        opaque = root_at(round(self.w.width() * .5 * ratio), round(self.w.height() * .57 * ratio))
        clear = root_at(5, 5)
        own = int(self.w.winId())
        self.report['native_input'] = {'own': own, 'opaque_root': opaque, 'transparent_root': clear,
                                      'foreground_before': self.foreground_before,
                                      'foreground_after': int(user32.GetForegroundWindow())}
        assert opaque == own, self.report['native_input']
        assert clear != own, self.report['native_input']
        assert int(user32.GetForegroundWindow()) != own

    def relocation(self):
        w = self.w
        w.canvas.timer.stop()
        area = w.available_screen().availableGeometry()
        w.move_content_to(area.center() - QPoint(w._content_size.width()//2,
                                                w._content_size.height()//2))
        w.manual_position = w.content_position()
        w.was_blocked = False
        w.autonomous = True
        w.pointer_over_interactive_area = lambda: False
        original = QPoint(w.pos())
        manual = QPoint(w.manual_position)

        # Fade-out input cancellation restores full opacity without moving.
        w.relocator.due = time.monotonic()-1
        w.tick()
        assert w.relocator.state == w.relocator.FADING_OUT
        QTest.qWait(150)
        w.tick()
        assert 0 < w.windowOpacity() < 1 and w.pos() == original
        w.begin_drag(QPointF(w.pos()+QPoint(150, 200)))
        assert not w.relocator.active and w.windowOpacity() == 1 and w.pos() == original
        w.end_drag()

        # A second run reaches zero opacity for a full tick before the only move.
        w.relocator.due = time.monotonic()-1
        w.tick()
        deadline = time.monotonic() + 1
        seen_positions = {(w.x(), w.y())}
        while w.relocator.state != w.relocator.RELOCATING and time.monotonic() < deadline:
            QTest.qWait(20)
            w.tick()
            seen_positions.add((w.x(), w.y()))
        assert w.relocator.state == w.relocator.RELOCATING
        assert w.windowOpacity() <= .01 and w.pos() == original
        w.tick()
        relocated = QPoint(w.pos())
        seen_positions.add((relocated.x(), relocated.y()))
        assert w.relocator.state == w.relocator.FADING_IN and relocated != original
        assert w.windowOpacity() <= .01 and w.visual_within_movement_area(area)
        assert seen_positions == {(original.x(), original.y()), (relocated.x(), relocated.y())}
        assert w.manual_position == manual
        QTest.qWait(150)
        w.tick()
        assert 0 < w.windowOpacity() < 1
        w.begin_drag(QPointF(w.pos()+QPoint(150, 200)))
        assert not w.relocator.active and w.windowOpacity() == 1 and w.pos() == relocated
        w.end_drag()
        assert w.relocator.due - time.monotonic() >= 19
        w.canvas.timer.start()

    def hover(self, x_fraction):
        m = self.w.canvas.model._model.GetMvp()
        # Profile model-space head coordinate -> exact current screen projection.
        left, bottom, right, top = self.w.profile['head_rect_model']
        x = (left + right) / 2 + x_fraction * (right - left) / 2
        y = (bottom + top) / 2
        import numpy as np
        clip = np.array(m).reshape(4,4,order='F') @ np.array([x, y, 0, 1])
        p = QPointF((clip[0]/clip[3]+1)*self.w.canvas.width()/2,
                    (1-clip[1]/clip[3])*self.w.canvas.height()/2)
        self.w.hover(p, Qt.MouseButton.NoButton)

    def no_false_pet(self):
        from pet_behavior import StrokeDetector
        d = StrokeDetector(self.w.profile['petting'])
        head = self.w.profile['head_rect_model']
        left, bottom, right, top = head
        center_x, center_y = (left + right) / 2, (bottom + top) / 2
        width = right - left
        assert not any(d.sample(center_x, center_y, i*.1, head) for i in range(40))
        d.reset()
        assert not any(d.sample(left + width*i/39, center_y, i*.04, head) for i in range(40))
        d.reset()
        assert not any(d.sample(center_x + width*(.3 if i%2 else -.3), center_y,
                                i*.2, head, True) for i in range(20))

    def reaction(self):
        w = self.w
        assert w.reaction_count > 0 and w.reaction_level > .9, (w.reaction_count, w.reaction_level)
        for pid, target in w.reaction_values.items():
            actual = w.canvas.model.GetParameterValue(w.canvas.params[pid]['index'])
            assert abs(actual-target) < .03, (pid, actual, target)
        w.canvas.grabFramebuffer().save(str(self.out/'petting.png'))
        # Keep production's 3-4 second hold; shorten only this regression stage.
        w.clear_petting_input()
        w.reaction_until = time.monotonic() - .01

    def reaction_restored(self):
        assert self.w.reaction_level == 0 and self.w.reaction_expression is None
        assert self.w.canvas.expression == self.previous_expression

    def start_existing_expression(self):
        self.previous_expression = self.w.model_profile.expression_candidates('negative')[-1]['asset']
        self.w.canvas.select_expression(self.previous_expression)

    def sleep_start(self):
        self.w.start_reaction(time.monotonic())
        self.w.canvas.sleep()
        self.sleep_position = self.w.pos()

    def sleep_check(self):
        w = self.w
        before = w.reaction_count
        self.hover(0)
        w.start_reaction(time.monotonic())
        w.relocator.due = time.monotonic()-1
        w.tick()
        assert w.canvas.sleeping and w.reaction_count == before
        assert w.pos() == self.sleep_position and not w.relocator.active and w.windowOpacity() == 1
        for semantic in ('eye_open_left', 'eye_open_right'):
            pid = w.model_profile.parameter_id(semantic)
            value = w.canvas.model.GetParameterValue(w.canvas.params[pid]['index'])
            assert value < .1, (semantic, value)
        w.canvas.grabFramebuffer().save(str(self.out/'sleep.png'))

    def no_sleep(self):
        assert not self.w.canvas.sleep()
        assert not self.w.canvas.sleeping
        assert all(action.get('kind') != 'sleep'
                   for action in self.w.profile['idle']['major']['actions'])
        menu = self.w.build_menu()
        sleep = next(action for action in menu.actions() if action.text() == '수면 시작')
        assert not sleep.isEnabled()
        menu.deleteLater()

    def wake_check(self):
        assert not self.w.canvas.sleeping and self.w.canvas.model.IsMotionFinished()
        assert self.w.relocator.due > time.monotonic()+18

    def save_restore(self):
        self.w.resize_character(400)
        self.w.save_state()
        expected = self.w.geometry().getRect()
        content = self.w.manual_position
        expected_path = self.out / f'expected-restore-{self.w.model_profile.id}.json'
        expected_path.write_text(json.dumps({
            'geometry': expected, 'content_position': [content.x(), content.y()], 'height': 400
        }), encoding='utf-8')

    def restore(self):
        expected_path = self.out / f'expected-restore-{self.w.model_profile.id}.json'
        expected = json.loads(expected_path.read_text(encoding='utf-8'))
        assert list(self.restored_geometry) == expected['geometry'], (self.restored_geometry, expected)
        assert self.w.character_height == expected['height']
        assert [self.w.manual_position.x(), self.w.manual_position.y()] == expected['content_position']
        self.w.canvas.grabFramebuffer().save(str(self.out/'restored.png'))

    def persistence(self):
        self.w.save_state()
        data = json.loads(self.w.state_path.read_text(encoding='utf-8'))
        assert (data['x'], data['y']) == (self.w.manual_position.x(), self.w.manual_position.y())
        assert self.w.manual_position == self.w.content_position()
        assert data['height'] == self.w.character_height

    def recovery(self):
        self.w.move(-40000, 40000)
        self.w.recover_geometry()
        area = self.w.available_screen().availableGeometry()
        assert self.w.visual_within_movement_area(area)
        _, _, edge_x, edge_y = self.w.movement_limits(area)
        edge = QPoint(edge_x, edge_y)
        self.w.move_content_to(edge)
        self.w.recover_geometry()
        visual = self.w.visual_geometry()
        assert visual.right() == area.right()
        allowed = int(visual.height() * self.w.profile['bottom_offscreen_fraction'])
        assert visual.bottom() == area.bottom() + allowed
        assert self.w.geometry().right() > area.right()
        assert self.w.geometry().bottom() > area.bottom()

    def panel(self):
        self.w.open_panel()
        assert self.w.panel.canvas is self.w.canvas
        self.w.panel.close()
        assert self.w.canvas.model is not None

    def character_menu(self):
        menu = self.w.build_menu()
        character = menu._character_menu
        labels = [action.text() for action in character.actions()]
        assert labels == [name for _, name in self.w.registry.menu_entries()]
        checked = [action.text() for action in character.actions() if action.isChecked()]
        assert checked == [self.w.model_profile.display_name]
        assert self.w.profile['bottom_offscreen_fraction'] == .65
        assert menu._size_slider.minimum() == 180 and menu._size_slider.maximum() == 3000
        assert len(menu._sizes_menu.actions()) == 1
        original_height = self.w.character_height
        preview_height = min(3000, original_height + 10)
        menu._size_slider.setValue(preview_height)
        assert self.w.pending_character_height == preview_height
        menu._size_slider.setValue(original_height)
        self.w.apply_pending_character_height()
        assert self.w.character_height == original_height
        menu.deleteLater()

    def next(self):
        if not self.steps:
            self.finish(True)
            return
        delay, name, callback = self.steps.pop(0)
        def run():
            try:
                callback()
                assert not self.w.canvas.error, self.w.canvas.error
                self.report['checks'].append(name)
                self.next()
            except Exception:
                self.fail(traceback.format_exc())
        QTimer.singleShot(delay, run)

    def fail(self, error):
        self.report['error'] = error
        print(error, flush=True)
        self.finish(False)

    def finish(self, passed):
        if self.done:
            return
        self.done = True
        self.watchdog.stop()
        self.report['passed'] = passed
        self.report['frames'] = self.w.canvas.frames
        (self.out / (self.stage + '.json')).write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('PET_VERIFY', passed, self.report['checks'], flush=True)
        self.w.close()
        QApplication.instance().exit(0 if passed else 1)
