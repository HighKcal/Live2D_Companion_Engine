"""Transparent, non-activating desktop surface; all coordinates are Qt logical pixels."""
import ctypes
from ctypes import wintypes
import json
import random
import sys
import time

import numpy as np
from OpenGL.GL import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QSize, QProcess, QTimer
from PySide6.QtGui import (QIcon, QPixmap, QPainter, QColor, QImage, QBitmap, QRegion,
                           QCursor, QTransform)
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QMenu,
                               QSystemTrayIcon, QLabel, QSlider, QWidgetAction)
from app import Canvas, Window
from prepare_model import ROOT
from pet_behavior import IdleScheduler, Relocator, StrokeDetector, smooth
from model_profiles import ProfileRegistry


SIZE_POLICY = {
    'minimum': 180,
    'maximum': 3000,
    'default': 300,
    'resize_interval_ms': 50,
    'transparent_padding_per_side_max': 512,
    'input_mask_read_pixel_limit': 2_000_000,
}


def bounded(value, first, second):
    low, high = sorted((first, second))
    return max(low, min(value, high))


def clamp_position(point, size, area):
    return QPoint(max(area.left(), min(point.x(), area.right() - size.width() + 1)),
                  max(area.top(), min(point.y(), area.bottom() - size.height() + 1)))


class PetCanvas(Canvas):
    def __init__(self, path, owner, profile):
        super().__init__(path, transparent=True, profile=profile)
        self.owner = owner
        self.setMinimumSize(1, 1)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def mousePressEvent(self, event):
        self.owner.clear_petting_input()
        if event.button() == Qt.MouseButton.LeftButton:
            if self.sleeping:
                self.wake()
                event.accept()
                return
            self.owner.begin_pointer_press(event.globalPosition(), event.position())
            event.accept()

    def mouseMoveEvent(self, event):
        if self.owner.pointer_pressed:
            self.owner.pointer_move(event.globalPosition())
        elif self.owner.dragging:
            self.owner.drag_to(event.globalPosition())
        else:
            self.owner.hover(event.position(), event.buttons())
            if self.owner.reaction_expression is None and not self.owner.menu_active:
                super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.owner.end_pointer_release(event.globalPosition(), event.position())
            event.accept()

    def contextMenuEvent(self, event):
        self.owner.open_menu(event.globalPos())

    def leaveEvent(self, event):
        self.owner.clear_petting_input()

    def resizeGL(self, w, h):
        super().resizeGL(w, h)
        self.owner.inverse_mvp = None

    def sleep(self, automatic=False):
        if self.profile.motion_asset('sleep') not in self.motion_indexes:
            return False
        self.owner.cancel_reaction()
        self.owner.cancel_idle_reaction(include_negative=True)
        if not automatic:
            self.owner.note_interaction()
        self.owner.sleep_source = 'automatic' if automatic else 'manual'
        super().sleep()
        return True

    def wake(self, *_):
        if not self.model:
            return False
        self.owner.cancel_reaction()
        self.owner.cancel_idle_reaction(include_negative=True)
        super().wake()
        self.owner.sleep_source = None
        self.owner.note_interaction()
        return True

    def apply_behavior(self):
        self.owner.apply_behavior()

    def paintGL(self):
        super().paintGL()
        if self.model and not self.error and self.owner.mask_due and not self.owner.dragging:
            self.owner.mask_due = False
            ratio = self.devicePixelRatioF()
            width, height = round(self.width()*ratio), round(self.height()*ratio)
            if width * height <= SIZE_POLICY['input_mask_read_pixel_limit']:
                pixels = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                self.owner.refresh_input_region(bytes(pixels), width, height)
            else:
                self.owner.refresh_scaled_input_region()


class PetWindow(QWidget):
    def __init__(self, path, state_path=None, profile=None, registry=None):
        super().__init__()
        self.registry = registry or ProfileRegistry(ROOT)
        self.model_profile = profile or self.registry.select(model_path=path)
        self.profile = self.model_profile.behavior_settings()
        self.state_path = state_path or ROOT / 'local' / 'pet-state.json'
        self.setWindowTitle(f'{self.model_profile.display_name} 데스크톱 펫')
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.Window | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.dragging = False
        self.pointer_pressed = False
        self.pointer_press_global = None
        self.pointer_press_local = None
        self.pointer_press_started = 0.0
        self.pointer_press_on_silhouette = False
        self.menu_active = False
        self.autonomous = True
        self.character_height = SIZE_POLICY['default']
        self.panel = None
        self.closed = False
        self.relocator = Relocator(self.profile, time.monotonic())
        self.idle = IdleScheduler(self.profile['idle'], time.monotonic())
        self.detector = StrokeDetector(self.profile['petting'])
        self.inverse_mvp = None
        self.reaction_level = 0.0
        self.reaction_until = 0.0
        self.reaction_hold_seconds = 0.0
        self.reaction_expression = None
        self.reaction_count = 0
        self.idle_expression = None
        self.idle_kind = None
        self.idle_previous_expression = None
        self.idle_until = 0.0
        self.idle_count = 0
        self.sleep_source = None
        self.manual_position = None
        # The persisted/manual position is the original-size central render area,
        # while the real QWidget may extend beyond it with transparent padding.
        self._content_size = QSize(1, 1)
        self._content_offset = QPoint(0, 0)
        self._visual_bounds = None
        self._input_region = None
        self._input_region_size = QSize(1, 1)
        self._silhouette_region = None
        self._silhouette_region_size = QSize(1, 1)
        self.annoyance_count = 0
        self.annoyance_last_poke = 0.0
        self.last_tick = time.monotonic()
        self.was_blocked = False
        self.mask_due = True
        self.canvas = PetCanvas(path, self, self.model_profile)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self.content_layout = layout
        self.canvas.ready.connect(self.loaded)
        self.canvas.failed.connect(self.on_error)
        self.canvas.state_changed.connect(self.state_changed)
        self.canvas.timer.timeout.connect(self.tick)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(400)
        self.save_timer.timeout.connect(self.save_state)
        self.mask_timer = QTimer(self)
        self.mask_timer.setInterval(200)
        self.mask_timer.timeout.connect(lambda: setattr(self, 'mask_due', True))
        self.pending_character_height = None
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.setInterval(SIZE_POLICY['resize_interval_ms'])
        self.resize_timer.timeout.connect(self.apply_pending_character_height)
        self.load_state()
        self.create_tray()
        app = QApplication.instance()
        app.screenAdded.connect(self.connect_screen)
        app.screenRemoved.connect(lambda _: QTimer.singleShot(0, self.recover_geometry))
        for screen in app.screens():
            self.connect_screen(screen)

    def available_screen(self, point=None):
        app = QApplication.instance()
        point = point or self.visual_geometry().center()
        return app.screenAt(point) or min(app.screens(), key=lambda s: (s.availableGeometry().center()-point).manhattanLength())

    def connect_screen(self, screen):
        screen.availableGeometryChanged.connect(self.recover_geometry)
        screen.geometryChanged.connect(self.recover_geometry)
        QTimer.singleShot(0, self.recover_geometry)

    def content_position(self):
        return self.pos() + self._content_offset

    def content_geometry(self):
        return QRect(self.content_position(), self._content_size)

    def visual_bounds(self):
        return self._visual_bounds or QRect(QPoint(0, 0), self._content_size)

    def visual_geometry(self):
        bounds = self.visual_bounds()
        return QRect(self.content_position() + bounds.topLeft(), bounds.size())

    def movement_limits(self, area):
        bounds = self.visual_bounds()
        bottom_overflow = int(bounds.height() * self.profile['bottom_offscreen_fraction'])
        return (area.left() - bounds.left(), area.top() - bounds.top(),
                area.right() - bounds.right(),
                area.bottom() - bounds.bottom() + bottom_overflow)

    def visual_within_movement_area(self, area):
        left, top, right, bottom = self.movement_limits(area)
        position = self.content_position()
        return (min(left, right) <= position.x() <= max(left, right) and
                min(top, bottom) <= position.y() <= max(top, bottom))

    def move_content_to(self, point):
        self.move(point - self._content_offset)

    def clamp_content_position(self, point, area):
        left, top, right, bottom = self.movement_limits(area)
        return QPoint(bounded(point.x(), left, right), bounded(point.y(), top, bottom))

    @staticmethod
    def normalize_character_height(height):
        return bounded(int(height), SIZE_POLICY['minimum'], SIZE_POLICY['maximum'])

    def recover_geometry(self, *_):
        if self.closed:
            return
        content_position = self.content_position()
        area = self.available_screen().availableGeometry()
        old_size = QSize(self._content_size)
        old_visual = QRect(self._visual_bounds) if self._visual_bounds is not None else None
        desired_h = round(self.character_height * self.profile['window_height_per_character_height'])
        content_h = max(1, desired_h)
        self._content_size = QSize(round(content_h * self.profile['window_aspect']), content_h)
        if old_visual is not None and old_size.width() > 0 and old_size.height() > 0:
            sx = self._content_size.width() / old_size.width()
            sy = self._content_size.height() / old_size.height()
            self._visual_bounds = QRect(round(old_visual.x() * sx), round(old_visual.y() * sy),
                                        max(1, round(old_visual.width() * sx)),
                                        max(1, round(old_visual.height() * sy)))
        window_scale = self.profile['render_window_scale']
        padding_cap = SIZE_POLICY['transparent_padding_per_side_max']
        extra_width = min(padding_cap * 2, max(0, round(
            self._content_size.width() * (window_scale - 1))))
        extra_height = min(padding_cap * 2, max(0, round(
            self._content_size.height() * (window_scale - 1))))
        window_size = QSize(self._content_size.width() + extra_width,
                            self._content_size.height() + extra_height)
        self._content_offset = QPoint((window_size.width() - self._content_size.width()) // 2,
                                      (window_size.height() - self._content_size.height()) // 2)
        self.content_layout.setContentsMargins(
            self._content_offset.x(), self._content_offset.y(),
            window_size.width() - self._content_size.width() - self._content_offset.x(),
            window_size.height() - self._content_size.height() - self._content_offset.y())
        self.resize(window_size)
        self.move_content_to(self.clamp_content_position(content_position, area))
        if self.manual_position is not None:
            manual_center = self.manual_position + self.visual_bounds().center()
            manual_area = self.available_screen(manual_center).availableGeometry()
            self.manual_position = self.clamp_content_position(self.manual_position, manual_area)
        self.interrupt()
        self.save_timer.start()

    def load_state(self):
        data = {}
        try:
            data = json.loads(self.state_path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('State must be an object')
            self.character_height = self.normalize_character_height(
                data.get('height', SIZE_POLICY['default']))
            self.autonomous = bool(data.get('autonomous', True))
            saved_size = data.get('content_size')
            saved_bounds = data.get('visual_bounds')
            if (isinstance(saved_size, list) and len(saved_size) == 2 and
                    isinstance(saved_bounds, list) and len(saved_bounds) == 4):
                self._content_size = QSize(int(saved_size[0]), int(saved_size[1]))
                self._visual_bounds = QRect(*map(int, saved_bounds))
            self.move(int(data['x']), int(data['y']))
        except (OSError, ValueError, TypeError, KeyError):
            area = QApplication.primaryScreen().availableGeometry()
            self.move(area.right() - 440, area.bottom() - 440)
        self.manual_position = self.content_position()
        self.recover_geometry()

    def save_state(self):
        position = self.manual_position or self.content_position()
        bounds = self.visual_bounds()
        center = position + bounds.center()
        data = {'x': position.x(), 'y': position.y(), 'height': self.character_height,
                'autonomous': self.autonomous, 'screen': self.available_screen(center).name(),
                'profile_id': self.model_profile.id,
                'content_size': [self._content_size.width(), self._content_size.height()],
                'visual_bounds': list(bounds.getRect())}
        temporary = self.state_path.with_suffix('.tmp')
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
            temporary.replace(self.state_path)
        except OSError as error:
            print(f'Position save failed: {error}', flush=True)

    def loaded(self):
        self.canvas.model.SetScale(self.profile['scale'])
        configured = self.profile['petting'].get('positive_reactions', [])
        self.positive_reactions = []
        for item in configured:
            values, missing = self.model_profile.resolve_values(
                item.get('parameter_values', {}), self.canvas.params)
            self.positive_reactions.append({
                'name': item.get('name', 'positive'), 'weight': item.get('weight', 1),
                'values': values, 'expression': item.get('asset') if not values else None})
            if missing:
                print(f'PROFILE optional positive parameters unavailable: {", ".join(missing)}', flush=True)
        self.reaction_values = self.positive_reactions[0]['values'] if self.positive_reactions else {}
        self.mask_timer.start()
        print('PET_READY', self.geometry().getRect(), 'DPR', self.devicePixelRatioF(),
              'MVP', self.canvas.model._model.GetMvp(), flush=True)

    def refresh_input_region(self, pixels, width, height):
        if not self.canvas.model or self.closed or self.dragging:
            return
        # QOpenGLWidget on Windows composes with alpha but does not always give the
        # HWND per-pixel hit testing. A bounded 5 Hz silhouette region fixes that.
        # Four logical pixels of dilation prevent animated hair tips being clipped.
        from PIL import Image, ImageFilter
        rgba = Image.frombytes('RGBA', (width, height), pixels).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        alpha = rgba.getchannel('A').resize((self.canvas.width(), self.canvas.height()))
        alpha = alpha.point([0] + [255]*255)
        visible = alpha.getbbox()
        if not visible:
            return
        self._visual_bounds = QRect(visible[0], visible[1], visible[2] - visible[0],
                                    visible[3] - visible[1])
        strict_rgba = Image.merge('RGBA', (alpha, alpha, alpha, alpha))
        strict_data = strict_rgba.tobytes()
        strict_image = QImage(strict_data, self.canvas.width(), self.canvas.height(),
                              self.canvas.width()*4, QImage.Format.Format_RGBA8888)
        self._silhouette_region = QRegion(QBitmap.fromImage(strict_image.createAlphaMask()))
        self._silhouette_region_size = QSize(self.canvas.size())
        alpha = alpha.filter(ImageFilter.MaxFilter(9))
        mask_rgba = Image.merge('RGBA', (alpha, alpha, alpha, alpha))
        data = mask_rgba.tobytes()
        mask_image = QImage(data, self.canvas.width(), self.canvas.height(), self.canvas.width()*4,
                            QImage.Format.Format_RGBA8888)
        region = QRegion(QBitmap.fromImage(mask_image.createAlphaMask()))
        self._input_region = QRegion(region)
        self._input_region_size = QSize(self.canvas.size())
        self.apply_input_region(region)

    def head_input_region(self):
        # Keep the small, profile-defined head area continuous for hover strokes.
        # Alpha-only HWND masks contain gaps between hair strands; crossing one
        # produces Leave events and clears a valid petting gesture.
        left, bottom, right, top = self.profile['head_rect_model']
        matrix = np.array(self.canvas.model._model.GetMvp()).reshape((4, 4), order='F')
        projected = []
        for x, y in ((left, bottom), (left, top), (right, bottom), (right, top)):
            clip = matrix @ np.array([x, y, 0, 1])
            projected.append(((clip[0] / clip[3] + 1) * self.canvas.width() / 2,
                              (1 - clip[1] / clip[3]) * self.canvas.height() / 2))
        xs, ys = zip(*projected)
        head_rect = QRect(QPoint(int(np.floor(min(xs))), int(np.floor(min(ys)))),
                          QPoint(int(np.ceil(max(xs))), int(np.ceil(max(ys))))).intersected(
                              self.canvas.rect())
        return QRegion(head_rect)

    def apply_input_region(self, content_region):
        region = content_region.united(self.head_input_region()).translated(self._content_offset)
        if region != self.mask():
            QTimer.singleShot(0, lambda r=region: self.setMask(r) if not self.closed else None)

    def refresh_scaled_input_region(self):
        if self._input_region is not None:
            transform = QTransform()
            transform.scale(self.canvas.width() / self._input_region_size.width(),
                            self.canvas.height() / self._input_region_size.height())
            region = transform.map(self._input_region)
        else:
            region = QRegion(self.visual_bounds())
        self.apply_input_region(region)

    def silhouette_contains(self, point):
        if self._silhouette_region is None:
            return False
        source = self._silhouette_region_size
        if source.width() <= 0 or source.height() <= 0:
            return False
        mapped = QPoint(round(point.x() * source.width() / max(1, self.canvas.width())),
                        round(point.y() * source.height() / max(1, self.canvas.height())))
        return self._silhouette_region.contains(mapped)

    def begin_pointer_press(self, global_pos, local_pos):
        self.pointer_pressed = True
        self.pointer_press_global = QPointF(global_pos)
        self.pointer_press_local = QPointF(local_pos)
        self.pointer_press_started = time.monotonic()
        self.pointer_press_on_silhouette = self.silhouette_contains(local_pos.toPoint())
        self.cancel_relocation(self.pointer_press_started)
        self.detector.reset()
        self.canvas.grabMouse()

    def pointer_move(self, global_pos):
        if not self.pointer_pressed:
            return
        delta = global_pos - self.pointer_press_global
        poke = self.profile.get('poke')
        threshold = poke.get('drag_threshold_pixels', 8.0) if poke else 8.0
        if not self.dragging and np.hypot(delta.x(), delta.y()) >= threshold:
            self.begin_drag(self.pointer_press_global)
        if self.dragging:
            self.drag_to(global_pos)

    def end_pointer_release(self, global_pos, local_pos):
        if not self.pointer_pressed and not self.dragging:
            return
        was_dragging = self.dragging
        elapsed = time.monotonic() - self.pointer_press_started
        press_hit = self.pointer_press_on_silhouette
        release_hit = self.silhouette_contains(local_pos.toPoint())
        self.pointer_pressed = False
        self.pointer_press_global = None
        self.pointer_press_local = None
        self.pointer_press_on_silhouette = False
        if was_dragging:
            self.end_drag()
            return
        self.canvas.releaseMouse()
        poke = self.profile.get('poke')
        max_seconds = poke.get('max_click_seconds', .5) if poke else .5
        if press_hit and release_hit and elapsed <= max_seconds:
            self.register_poke(time.monotonic())

    def begin_drag(self, global_pos):
        self.note_interaction()
        self.dragging = True
        self.drag_offset = global_pos - QPointF(self.pos())
        self.interrupt()
        self.cancel_reaction()
        self.detector.block_for(time.perf_counter(), self.profile['petting']['post_drag_seconds'])
        self.canvas.grabMouse()

    def drag_to(self, global_pos):
        if self.dragging:
            point = (global_pos - self.drag_offset).toPoint()
            area = self.available_screen(global_pos.toPoint()).availableGeometry()
            content = self.clamp_content_position(point + self._content_offset, area)
            self.move_content_to(content)

    def end_drag(self):
        if not self.dragging:
            return
        self.dragging = False
        self.canvas.releaseMouse()
        self.interrupt()
        self.recover_geometry()
        self.manual_position = self.content_position()
        self.save_timer.start()
        self.detector.block_for(time.perf_counter(), self.profile['petting']['post_drag_seconds'])

    def interrupt(self):
        self.cancel_relocation(time.monotonic())
        self.detector.reset()

    def note_interaction(self, now=None):
        now = time.monotonic() if now is None else now
        self.cancel_idle_reaction()
        self.idle.interact(now)
        self.cancel_relocation(now)

    def cancel_relocation(self, now=None):
        now = time.monotonic() if now is None else now
        self.relocator.pause(now)
        if self.windowOpacity() != 1.0:
            self.setWindowOpacity(1.0)
            self.update()

    def pointer_over_interactive_area(self):
        point = self.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(point):
            return False
        region = self.mask()
        return region.isEmpty() or region.contains(point)

    def clear_petting_input(self):
        was_active = self.detector.active
        self.detector.reset()
        if was_active:
            self.cancel_relocation(time.monotonic())

    def hover(self, position, buttons):
        if self.relocator.active:
            self.cancel_relocation(time.monotonic())
        if not self.canvas.model or self.canvas.sleeping or self.dragging or self.menu_active:
            self.detector.reset()
            return
        model_point = self.to_model(position)
        if model_point is None:
            return
        now = time.monotonic()
        was_active = self.detector.active
        hit = self.detector.sample(*model_point, time.perf_counter(), self.profile['head_rect_model'],
                                   buttons != Qt.MouseButton.NoButton)
        # Pausing relocation must not clear an in-progress stroke sequence.
        if self.detector.active or was_active:
            self.cancel_relocation(now)
            if self.idle_kind == 'ambient':
                self.cancel_idle_reaction()
        if self.detector.active and self.reaction_expression is not None:
            self.reaction_until = now + self.reaction_hold_seconds
        if hit:
            self.start_reaction(now)

    def to_model(self, point):
        if self.inverse_mvp is None:
            matrix = np.array(self.canvas.model._model.GetMvp()).reshape((4, 4), order='F')
            try:
                self.inverse_mvp = np.linalg.inv(matrix)
            except np.linalg.LinAlgError:
                return None
        vec = self.inverse_mvp @ np.array([2 * point.x() / self.canvas.width() - 1,
                                          1 - 2 * point.y() / self.canvas.height(), 0, 1])
        return float(vec[0] / vec[3]), float(vec[1] / vec[3])

    def start_reaction(self, now):
        if self.canvas.sleeping or self.dragging or self.menu_active:
            return
        self.reset_annoyance()
        recovered_negative = self.idle_kind == 'negative'
        previous_expression = self.canvas.expression
        if recovered_negative:
            previous_expression = self.idle_previous_expression
            self.cancel_idle_reaction(restore=False, reschedule=False, now=now,
                                      include_negative=True)
            self.idle.interact(now)
            self.cancel_relocation(now)
        else:
            self.note_interaction(now)
        if self.reaction_expression is None:
            self.reaction_expression = previous_expression
            self.canvas.model.ResetExpressions()
            if self.positive_reactions:
                selected = random.choices(self.positive_reactions,
                                          weights=[item['weight'] for item in self.positive_reactions], k=1)[0]
                self.reaction_values = selected['values']
                if selected['expression']:
                    self.canvas.select_expression(selected['expression'])
                else:
                    self.canvas.model.ResetExpressions()
            self.reaction_hold_seconds = random.uniform(*self.profile['petting']['positive_hold_seconds'])
        self.reaction_count += 1
        self.reaction_until = now + self.reaction_hold_seconds
        self.cancel_relocation(now)
        suffix = ' recovered_negative' if recovered_negative else ''
        print(f'PETTING detected={self.reaction_count}{suffix}', flush=True)

    def reset_annoyance(self):
        self.annoyance_count = 0
        self.annoyance_last_poke = 0.0

    def register_poke(self, now):
        settings = self.profile.get('poke')
        if not settings:
            self.note_interaction(now)
            return False
        if self.canvas.sleeping or self.dragging or self.menu_active:
            return False
        if self.idle_kind == 'negative':
            self.idle.interact(now)
            self.cancel_relocation(now)
            return False
        if self.reaction_expression is not None or self.reaction_level > 0:
            self.idle.interact(now)
            self.cancel_relocation(now)
            return False
        if (self.annoyance_last_poke and
                now - self.annoyance_last_poke > settings['reset_seconds']):
            self.reset_annoyance()
        self.annoyance_count += 1
        self.annoyance_last_poke = now
        if self.idle_kind == 'ambient':
            self.cancel_idle_reaction(now=now)
        self.idle.interact(now)
        self.cancel_relocation(now)
        reaction = next((item for item in settings['reactions']
                         if item['threshold'] == self.annoyance_count), None)
        if reaction is None:
            print(f'POKE count={self.annoyance_count}', flush=True)
            return True

        previous = self.canvas.expression
        if self.idle_kind == 'poke':
            previous = self.idle_previous_expression
            self.cancel_idle_reaction(restore=False, reschedule=False, now=now)
        persistent = reaction.get('persistent_negative', False)
        kind = 'negative' if persistent else 'poke'
        hold = float('inf') if persistent else random.uniform(*reaction['hold_seconds'])
        started = self.start_idle_reaction(now, reaction['asset'], hold, kind)
        if started:
            self.idle_previous_expression = previous
            if persistent:
                self.idle.next_major = float('inf')
            print(f'POKE count={self.annoyance_count} reaction={ascii(reaction["asset"])}'
                  f' persistent={persistent}', flush=True)
        return started

    def start_idle_reaction(self, now, expression, hold, kind):
        if (self.canvas.sleeping or self.dragging or self.menu_active or self.detector.active or
                self.reaction_expression is not None or self.reaction_level > 0 or
                (self.panel is not None and self.panel.isVisible())):
            return False
        if expression not in self.canvas.expression_indexes:
            return False
        self.idle_previous_expression = self.canvas.expression
        self.idle_expression = expression
        self.idle_kind = kind
        self.idle_until = float('inf') if kind == 'negative' else now + hold
        self.canvas.select_expression(expression)
        self.idle_count += 1
        self.cancel_relocation(now)
        print(f'IDLE_REACTION kind={kind} expression={ascii(expression)} count={self.idle_count}', flush=True)
        return True

    def cancel_idle_reaction(self, restore=True, reschedule=True, now=None,
                             include_negative=False):
        if self.idle_expression is None:
            return
        if self.idle_kind == 'negative' and not include_negative:
            return
        previous = self.idle_previous_expression
        kind = self.idle_kind
        self.idle_expression = None
        self.idle_kind = None
        self.idle_previous_expression = None
        self.idle_until = 0.0
        if reschedule:
            self.idle.finished(kind, time.monotonic() if now is None else now)
        if restore and self.canvas.model and not self.canvas.sleeping:
            self.canvas.select_expression(previous)

    def cancel_reaction(self):
        if self.reaction_expression is not None and self.canvas.model:
            previous = self.reaction_expression
            self.reaction_expression = None
            for pid in getattr(self, 'reaction_values', {}):
                self.canvas.set_param(pid, self.canvas.params[pid]['default'])
            self.canvas.select_expression(previous)
        self.reaction_level = 0.0
        self.reaction_until = 0.0
        self.reaction_hold_seconds = 0.0
        self.detector.reset()

    def tick(self):
        now = time.monotonic()
        dt = min(.1, now - self.last_tick)
        self.last_tick = now
        was_petting = self.detector.active
        self.detector.expire(time.perf_counter())
        if was_petting and not self.detector.active:
            self.cancel_relocation(now)
        if self.canvas.sleeping:
            self.reaction_level = 0
        else:
            target = 1.0 if now < self.reaction_until else 0.0
            step = dt / (self.profile['petting']['attack_seconds'] if target else self.profile['petting']['fade_seconds'])
            self.reaction_level = max(0, min(1, self.reaction_level + (step if target else -step)))
            if self.reaction_level == 0 and self.reaction_expression is not None:
                self.cancel_reaction()
                self.cancel_relocation(now)
        if self.idle_kind in ('ambient', 'poke') and now >= self.idle_until:
            self.cancel_idle_reaction()
            self.cancel_relocation(now)
        poke = self.profile.get('poke')
        if (poke and self.idle_kind != 'negative' and self.annoyance_last_poke and
                now - self.annoyance_last_poke > poke['reset_seconds']):
            if self.idle_kind == 'poke':
                self.cancel_idle_reaction(now=now)
            self.reset_annoyance()
        idle_blocked = (self.dragging or self.menu_active or self.canvas.sleeping or self.detector.active or
                        self.reaction_expression is not None or self.reaction_level > 0 or
                        self.idle_expression is not None or self.relocator.active or
                        (self.panel is not None and self.panel.isVisible()))
        idle_action = self.idle.due(now, idle_blocked and self.idle_expression is None,
                                    active_kind=self.idle_kind)
        if idle_action:
            kind, payload = idle_action
            if kind == 'sleep':
                if not self.canvas.sleep(automatic=True):
                    self.idle.finished('negative', now)
            elif kind in ('ambient', 'negative'):
                if kind == 'negative' and self.idle_kind == 'ambient':
                    self.cancel_idle_reaction()
                if not self.start_idle_reaction(now, payload['expression'], payload.get('hold'), kind):
                    if kind == 'negative':
                        self.idle.finished('negative', now)
        blocked = (not self.autonomous or self.dragging or self.menu_active or self.canvas.sleeping or
                   self.detector.active or self.reaction_level > 0 or self.idle_expression is not None or
                   self.pointer_over_interactive_area() or
                   (self.panel is not None and self.panel.isVisible()))
        if blocked:
            if self.relocator.active or not self.was_blocked:
                self.cancel_relocation(now)
        else:
            if self.was_blocked and not self.relocator.active:
                self.relocator.pause(now)
            area = self.available_screen().availableGeometry()
            limits = self.movement_limits(area)
            content = self.content_position()
            self.relocator.start(now, (content.x(), content.y()), limits,
                                 (self.visual_bounds().width(), self.visual_bounds().height()))
            if self.relocator.active:
                opacity, destination, _ = self.relocator.step(now)
                self.setWindowOpacity(opacity)
                if destination is not None:
                    # RELOCATING follows a full zero-opacity tick, so no translated frame is visible.
                    self.move_content_to(QPoint(round(destination[0]), round(destination[1])))
                    self.mask_due = True
        self.was_blocked = blocked

    def apply_behavior(self):
        if self.canvas.sleeping:
            return
        c = self.canvas
        blend = smooth(self.reaction_level)
        if self.reaction_expression is not None:
            # Native expressions are suspended. Each value starts from this frame's base,
            # so neither Add expressions nor previous response frames accumulate.
            for pid, target in self.reaction_values.items():
                base = c.model.GetParameterValue(c.params[pid]['index']) if pid in c.values or pid in c.expression_params else c.params[pid]['default']
                c.set_param(pid, base * (1 - blend) + target * blend)

    def state_changed(self):
        self.interrupt()

    def resize_character(self, height):
        height = self.normalize_character_height(height)
        if height == self.character_height:
            return
        self.note_interaction()
        bottom = self.content_geometry().bottomLeft()
        manual_bottom = None
        if self.manual_position is not None:
            manual_bottom = self.manual_position + QPoint(0, self._content_size.height() - 1)
        self.character_height = height
        self.recover_geometry()
        candidate = QPoint(self.content_position().x(), bottom.y() - self._content_size.height() + 1)
        self.move_content_to(self.clamp_content_position(
            candidate, self.available_screen().availableGeometry()))
        if manual_bottom is not None:
            candidate = QPoint(manual_bottom.x(), manual_bottom.y() - self._content_size.height() + 1)
            screen = self.available_screen(candidate + QPoint(self._content_size.width() // 2,
                                                               self._content_size.height() // 2))
            self.manual_position = self.clamp_content_position(candidate, screen.availableGeometry())
        self.save_timer.start()

    def queue_character_resize(self, height):
        self.pending_character_height = self.normalize_character_height(height)
        if not self.resize_timer.isActive():
            self.resize_timer.start()

    def apply_pending_character_height(self):
        if self.pending_character_height is None:
            return
        height = self.pending_character_height
        self.pending_character_height = None
        self.resize_character(height)

    def build_menu(self):
        menu = QMenu(self)
        characters = menu.addMenu('캐릭터')
        menu._character_menu = characters
        for profile_id, display_name in self.registry.menu_entries():
            action = characters.addAction(display_name)
            action.setCheckable(True)
            action.setChecked(profile_id == self.model_profile.id)
            action.setEnabled(profile_id != self.model_profile.id)
            action.triggered.connect(lambda checked=False, pid=profile_id: self.select_profile(pid))
        menu.addSeparator()
        move = menu.addAction('자율 이동')
        move.setCheckable(True)
        move.setChecked(self.autonomous)
        move.toggled.connect(self.set_autonomous)
        sizes = menu.addMenu('크기')
        menu._sizes_menu = sizes
        size_panel = QWidget(sizes)
        size_layout = QHBoxLayout(size_panel)
        size_layout.setContentsMargins(10, 4, 10, 4)
        size_slider = QSlider(Qt.Orientation.Horizontal, size_panel)
        size_slider.setRange(SIZE_POLICY['minimum'], SIZE_POLICY['maximum'])
        size_slider.setSingleStep(10)
        size_slider.setPageStep(100)
        size_slider.setValue(self.character_height)
        size_slider.setMinimumWidth(220)
        size_label = QLabel(f'{self.character_height} px', size_panel)
        size_label.setMinimumWidth(58)
        size_slider.valueChanged.connect(lambda value: size_label.setText(f'{value} px'))
        size_slider.valueChanged.connect(self.queue_character_resize)
        size_slider.sliderReleased.connect(self.apply_pending_character_height)
        size_layout.addWidget(size_slider)
        size_layout.addWidget(size_label)
        size_action = QWidgetAction(sizes)
        size_action.setDefaultWidget(size_panel)
        sizes.addAction(size_action)
        menu._size_slider = size_slider
        menu._size_label = size_label
        sleep = menu.addAction('수면 해제' if self.canvas.sleeping else '수면 시작')
        sleep.setEnabled(self.canvas.model is not None and
                         self.model_profile.motion_asset('sleep') in self.canvas.motion_indexes)
        sleep.triggered.connect(self.canvas.wake if self.canvas.sleeping else self.canvas.sleep)
        menu.addAction('개발용 제어판', self.open_panel)
        menu.addSeparator()
        menu.addAction('종료', self.close)
        return menu

    def select_profile(self, profile_id):
        if profile_id == self.model_profile.id:
            return
        self.registry.set_active(profile_id)
        self.save_state()
        result = QProcess.startDetached(
            sys.executable, [str(ROOT / 'app.py'), '--profile', profile_id])
        started = result[0] if isinstance(result, tuple) else bool(result)
        if started:
            self.close()
        else:
            self.registry.set_active(self.model_profile.id)
            self.tray.showMessage('캐릭터 전환 실패', '새 프로세스를 시작하지 못했습니다.',
                                  QSystemTrayIcon.MessageIcon.Warning)

    def set_autonomous(self, enabled):
        self.note_interaction()
        self.autonomous = enabled
        self.interrupt()
        self.save_timer.start()

    def open_menu(self, position):
        self.menu_active = True
        self.interrupt()
        self.cancel_reaction()
        self.cancel_idle_reaction()
        menu = self.build_menu()
        try:
            menu.exec(position)
        finally:
            self.menu_active = False
            self.interrupt()
            menu.deleteLater()

    def create_tray(self):
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setBrush(QColor('#f49ab6'))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(3, 3, 26, 26)
        painter.end()
        self.tray = QSystemTrayIcon(QIcon(pixmap), self)
        self.tray.setToolTip(f'{self.model_profile.display_name} · 우클릭하여 종료 또는 제어')
        # Tray menu is always accessible even if model initialization fails.
        self.tray_menu = QMenu()
        self.tray_menu.addAction('화면 안으로 불러오기', self.recover_from_tray)
        self.tray_menu.addAction('개발용 제어판', self.open_panel)
        self.tray_menu.addAction('종료', self.close)
        self.tray_menu.aboutToShow.connect(lambda: self.tray_interaction(True))
        self.tray_menu.aboutToHide.connect(lambda: self.tray_interaction(False))
        self.tray.setContextMenu(self.tray_menu)
        self.tray.show()

    def tray_interaction(self, active):
        self.menu_active = active
        self.interrupt()
        if active:
            self.cancel_reaction()
            self.cancel_idle_reaction()

    def recover_from_tray(self):
        self.note_interaction()
        self.recover_geometry()

    def open_panel(self):
        # Reuse laboratory controls, bound to the SAME renderer; no second model/GL context.
        self.note_interaction()
        self.interrupt()
        self.cancel_reaction()
        if self.panel is None:
            self.panel = Window(self.canvas.path, shared_canvas=self.canvas,
                                profile=self.model_profile)
            self.panel.destroyed.connect(lambda: setattr(self, 'panel', None))
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()

    def on_error(self, error):
        self.tray.showMessage(f'{self.model_profile.display_name} 실행 오류',
                              str(error)[-300:], QSystemTrayIcon.MessageIcon.Critical)
        print(error, flush=True)

    def nativeEvent(self, event_type, message):
        if event_type == b'windows_generic_MSG':
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x21:  # WM_MOUSEACTIVATE: preserve the other app's keyboard focus.
                return True, 3  # MA_NOACTIVATE, mouse event still delivered.
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event):
        self.closed = True
        self.mask_timer.stop()
        self.save_timer.stop()
        self.save_state()
        if self.panel:
            self.panel.close()
        self.tray.hide()
        self.canvas.cleanup()
        event.accept()
        QApplication.instance().quit()
