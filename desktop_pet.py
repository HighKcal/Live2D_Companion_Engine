"""Transparent, non-activating desktop surface; all coordinates are Qt logical pixels."""
import ctypes
from ctypes import wintypes
import json
import random
import sys
import time

import numpy as np
from OpenGL.GL import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QRectF, QSize, QProcess, QTimer
from PySide6.QtGui import (QIcon, QPixmap, QPainter, QPen, QColor, QImage, QBitmap,
                           QRegion, QCursor, QTransform)
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


class PokeDebugOverlay(QWidget):
    """Input-transparent overlay for diagnosing profile/model hit alignment."""
    def __init__(self, canvas, owner):
        super().__init__(canvas)
        self.owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(canvas.rect())
        self.show()
        self.raise_()

    def paintEvent(self, event):
        poke = self.owner.profile.get('poke')
        if not poke or not self.owner.canvas.model:
            return
        rect = self.owner.project_model_rect(poke.get('interaction_rect_model'))
        if rect.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(255, 50, 50, 55))
        painter.setPen(QPen(QColor(255, 70, 70, 230), 2))
        painter.drawRect(rect)
        painter.setPen(QColor(255, 255, 255, 240))
        painter.drawText(rect.adjusted(4, 2, -2, -2), Qt.AlignmentFlag.AlignTop,
                         'poke_chest')
        if self.owner.debug_press_point is not None:
            painter.setBrush(QColor(60, 220, 100, 210))
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawEllipse(self.owner.debug_press_point, 6, 6)
        if self.owner.debug_release_point is not None:
            painter.setBrush(QColor(80, 160, 255, 210))
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawEllipse(self.owner.debug_release_point, 5, 5)
        painter.end()


class FoodOverlay(QWidget):
    """Input-transparent child overlay used for the short feeding interaction."""
    def __init__(self, canvas, owner, asset_path):
        # A direct PetWindow child is composited above QOpenGLWidget. A canvas
        # child can be hidden behind the GL composition layer on Windows.
        super().__init__(owner)
        self.owner = owner
        self.canvas = canvas
        self.pixmap = QPixmap(str(asset_path))
        self.food_rect = QRectF()
        self.path_rect = QRect()
        self.start_center = QPointF()
        self.target_center = QPointF()
        self.food_size = 1
        self.active = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(QRect(owner._content_offset, canvas.size()))
        self.show()

    def configure(self, start_center, target_center, size):
        self.start_center = QPointF(start_center)
        self.target_center = QPointF(target_center)
        self.food_size = max(1, int(size))
        half = self.food_size / 2
        start = QRectF(self.start_center.x() - half, self.start_center.y() - half,
                       self.food_size, self.food_size)
        target = QRectF(self.target_center.x() - half, self.target_center.y() - half,
                        self.food_size, self.food_size)
        self.path_rect = start.united(target).adjusted(-2, -2, 2, 2).toAlignedRect()
        self.active = True
        self.set_progress(0.0)
        self.raise_()

    def set_progress(self, progress):
        progress = smooth(max(0.0, min(1.0, progress)))
        center = self.start_center * (1.0 - progress) + self.target_center * progress
        half = self.food_size / 2
        self.food_rect = QRectF(center.x() - half, center.y() - half,
                                self.food_size, self.food_size)
        self.update()

    def hide_food(self):
        self.active = False
        self.food_rect = QRectF()
        self.path_rect = QRect()
        self.update()

    def paintEvent(self, event):
        if not self.active or self.pixmap.isNull() or self.food_rect.isEmpty():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(self.food_rect.toAlignedRect(), self.pixmap)
        painter.end()


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

    def select_expression(self, expression):
        before = self.expression
        super().select_expression(expression)
        if self.owner.poke_debug:
            requested = expression
            if isinstance(requested, int):
                requested = (self.profile.asset_identifier(self.assets['expressions'][requested])
                             if 0 <= requested < len(self.assets['expressions']) else None)
            print('POKE_DEBUG_EXPRESSION_APPLY', json.dumps({
                'requested_expression': requested,
                'expression_index': self.expression_indexes.get(requested),
                'canvas_expression_before': before,
                'canvas_expression_after': self.expression,
                'sleeping': self.sleeping,
                'idle_kind': self.owner.idle_kind,
                'idle_expression': self.owner.idle_expression,
                'reaction_expression': self.owner.reaction_expression,
            }, ensure_ascii=False), flush=True)

    def contextMenuEvent(self, event):
        self.owner.open_menu(event.globalPos())

    def leaveEvent(self, event):
        self.owner.clear_petting_input()

    def resizeGL(self, w, h):
        super().resizeGL(w, h)
        self.owner.inverse_mvp = None
        overlay = getattr(self.owner, 'poke_debug_overlay', None)
        if overlay is not None:
            overlay.setGeometry(self.rect())
            overlay.raise_()
            overlay.update()
        food = getattr(self.owner, 'food_overlay', None)
        if food is not None:
            food.setGeometry(QRect(self.owner._content_offset, self.size()))
            food.raise_()

    def sleep(self, automatic=False):
        if self.profile.motion_asset('sleep') not in self.motion_indexes:
            return False
        self.owner.cancel_feeding(restore_expression=False)
        self.owner.stop_active_motion()
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
    def __init__(self, path, state_path=None, profile=None, registry=None,
                 poke_debug=False):
        super().__init__()
        self.registry = registry or ProfileRegistry(ROOT)
        self.model_profile = profile or self.registry.select(model_path=path)
        self.profile = self.model_profile.behavior_settings()
        self.poke_debug = bool(poke_debug)
        self.debug_press_point = None
        self.debug_release_point = None
        self.poke_debug_overlay = None
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
        self.pointer_press_in_poke_region = False
        self.menu_active = False
        self.active_motion_kind = None
        self.active_motion_deadline = 0.0
        self.greeting_pending = False
        self.feeding_pending = False
        self.feeding_active = False
        self.feeding_started = 0.0
        self.feeding_stage = None
        self.feeding_mouth_pid = None
        self.feeding_mouth_baseline = 0.0
        self.feeding_mouth_value = 0.0
        self.feeding_previous_expression = None
        self.feeding_reactions = {}
        self.food_overlay = None
        self._character_input_region = QRegion()
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
        # Kept as a compact 0..N level for compatibility with existing probes.
        self.annoyance_count = 0
        self.annoyance_last_poke = 0.0
        self.annoyance_base_expression = None
        self.annoyance_reconciliation_index = None
        self._poke_debug_last_lifecycle = None
        self._poke_debug_expected_expression = None
        self.last_tick = time.monotonic()
        self.was_blocked = False
        self.mask_due = True
        self.canvas = PetCanvas(path, self, self.model_profile)
        if self.poke_debug:
            self.poke_debug_overlay = PokeDebugOverlay(self.canvas, self)
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
        self.reconciliation_reactions = []
        for item in (self.profile.get('poke') or {}).get('reconciliation', []):
            values, missing = self.model_profile.resolve_values(
                item.get('parameter_values', {}), self.canvas.params)
            self.reconciliation_reactions.append({
                'values': values,
                'expression': item.get('asset') if not values else None})
            if missing:
                print('PROFILE optional reconciliation parameters unavailable: '
                      + ', '.join(missing), flush=True)
        completion = (self.profile.get('poke') or {}).get('completion')
        self.reconciliation_completion = None
        if completion:
            values, missing = self.model_profile.resolve_values(
                completion.get('parameter_values', {}), self.canvas.params)
            self.reconciliation_completion = {
                'values': values,
                # Completion may combine its expression with pose parameters
                # carried by the preceding persistent petting response.
                'expression': completion.get('asset')}
            if missing:
                print('PROFILE optional reconciliation completion parameters unavailable: '
                      + ', '.join(missing), flush=True)
        self.reaction_values = self.positive_reactions[0]['values'] if self.positive_reactions else {}
        feeding = self.profile.get('feeding')
        if feeding:
            self.feeding_reactions = {
                role: item.get('asset') for role, item in feeding.get('reactions', {}).items()
                if isinstance(item, dict) and item.get('asset') in self.canvas.expression_indexes}
            food_path = self.model_profile.feeding_food_path()
            if food_path is not None:
                self.food_overlay = FoodOverlay(self.canvas, self, food_path)
                if self.food_overlay.pixmap.isNull():
                    self.food_overlay.deleteLater()
                    self.food_overlay = None
        self.mask_timer.start()
        if self.poke_debug_overlay is not None:
            self.poke_debug_overlay.setGeometry(self.canvas.rect())
            self.poke_debug_overlay.raise_()
            self.poke_debug_overlay.update()
            rect = self.project_model_rect(
                self.profile['poke']['interaction_rect_model'])
            print('POKE_DEBUG_REGION', json.dumps({
                'model_rect': self.profile['poke']['interaction_rect_model'],
                'canvas_rect': list(rect.getRect()),
                'canvas_size': [self.canvas.width(), self.canvas.height()],
                'window_position': [self.x(), self.y()],
            }, ensure_ascii=False), flush=True)
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

    def project_model_point(self, point):
        if not self.canvas.model or not point:
            return None
        matrix = np.array(self.canvas.model._model.GetMvp()).reshape((4, 4), order='F')
        clip = matrix @ np.array([point[0], point[1], 0, 1])
        if clip[3] == 0:
            return None
        return QPointF((clip[0] / clip[3] + 1) * self.canvas.width() / 2,
                       (1 - clip[1] / clip[3]) * self.canvas.height() / 2)

    def project_model_rect(self, rect):
        if not self.canvas.model or not rect:
            return QRect()
        left, bottom, right, top = rect
        matrix = np.array(self.canvas.model._model.GetMvp()).reshape((4, 4), order='F')
        projected = []
        for x, y in ((left, bottom), (left, top), (right, bottom), (right, top)):
            clip = matrix @ np.array([x, y, 0, 1])
            projected.append(((clip[0] / clip[3] + 1) * self.canvas.width() / 2,
                              (1 - clip[1] / clip[3]) * self.canvas.height() / 2))
        xs, ys = zip(*projected)
        return QRect(
            QPoint(int(np.floor(min(xs))), int(np.floor(min(ys)))),
            QPoint(int(np.ceil(max(xs))), int(np.ceil(max(ys))))).intersected(
                self.canvas.rect())

    def head_input_region(self):
        # Keep the small, profile-defined head area continuous for hover strokes.
        # Alpha-only HWND masks contain gaps between hair strands; crossing one
        # produces Leave events and clears a valid petting gesture.
        return QRegion(self.project_model_rect(self.profile['head_rect_model']))

    def apply_input_region(self, content_region):
        self._character_input_region = content_region.united(
            self.head_input_region()).translated(self._content_offset)
        self.refresh_feeding_input_region()

    def refresh_feeding_input_region(self):
        region = QRegion(self._character_input_region)
        if (self.feeding_active and self.food_overlay is not None and
                not self.food_overlay.path_rect.isEmpty()):
            region = region.united(QRegion(
                self.food_overlay.path_rect.translated(self._content_offset)))
        if region != self.mask():
            QTimer.singleShot(
                0, lambda r=region: self.setMask(r) if not self.closed else None)

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
            # Large restored sizes deliberately skip a full framebuffer read.
            # Match the visual-bounds fallback already used by the window mask
            # so visible interactions do not become impossible at startup.
            return self.visual_bounds().contains(point)
        source = self._silhouette_region_size
        if source.width() <= 0 or source.height() <= 0:
            return False
        mapped = QPoint(round(point.x() * source.width() / max(1, self.canvas.width())),
                        round(point.y() * source.height() / max(1, self.canvas.height())))
        return self._silhouette_region.contains(mapped)

    @staticmethod
    def model_rect_contains(model_point, rect):
        if model_point is None or not rect:
            return False
        x, y = model_point
        left, bottom, right, top = rect
        return left <= x <= right and bottom <= y <= top

    def poke_region_contains(self, point):
        poke = self.profile.get('poke')
        return bool(poke and self.model_rect_contains(
            self.to_model(point), poke.get('interaction_rect_model')))

    def begin_pointer_press(self, global_pos, local_pos):
        self.pointer_pressed = True
        self.pointer_press_global = QPointF(global_pos)
        self.pointer_press_local = QPointF(local_pos)
        self.pointer_press_started = time.monotonic()
        point = local_pos.toPoint()
        self.pointer_press_on_silhouette = self.silhouette_contains(point)
        self.pointer_press_in_poke_region = self.poke_region_contains(point)
        if self.poke_debug:
            self.debug_press_point = QPoint(point)
            self.debug_release_point = None
            model_point = self.to_model(point)
            print('POKE_DEBUG_PRESS', json.dumps({
                'local': [point.x(), point.y()],
                'global': [round(global_pos.x()), round(global_pos.y())],
                'model': list(model_point) if model_point else None,
                'chest_hit': self.pointer_press_in_poke_region,
                'silhouette_hit': self.pointer_press_on_silhouette,
                'annoyance_before': self.annoyance_count,
            }, ensure_ascii=False), flush=True)
            self.poke_debug_overlay.update()
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
        press_local = QPointF(self.pointer_press_local)
        press_global = QPointF(self.pointer_press_global)
        press_silhouette = self.pointer_press_on_silhouette
        press_chest = self.pointer_press_in_poke_region
        point = local_pos.toPoint()
        release_silhouette = self.silhouette_contains(point)
        release_chest = self.poke_region_contains(point)
        press_hit = press_silhouette and press_chest
        release_hit = release_silhouette and release_chest
        before = self.annoyance_count
        poke = self.profile.get('poke')
        max_seconds = poke.get('max_click_seconds', .5) if poke else .5
        valid_click = bool(
            not was_dragging and self.active_motion_kind is None and
            not self.feeding_active and
            press_hit and release_hit and elapsed <= max_seconds)
        attempted_expression = None
        if valid_click and poke:
            level = min(before + 1, len(poke['levels']))
            attempted_expression = self.annoyance_level(level)['asset']
        self.pointer_pressed = False
        self.pointer_press_global = None
        self.pointer_press_local = None
        self.pointer_press_on_silhouette = False
        self.pointer_press_in_poke_region = False
        registered = False
        if was_dragging:
            self.end_drag()
        else:
            self.canvas.releaseMouse()
            if valid_click:
                registered = self.register_poke(time.monotonic())
        if self.poke_debug:
            self.debug_release_point = QPoint(point)
            model_point = self.to_model(point)
            print('POKE_DEBUG_RELEASE', json.dumps({
                'press_local': [round(press_local.x()), round(press_local.y())],
                'release_local': [point.x(), point.y()],
                'press_global': [round(press_global.x()), round(press_global.y())],
                'release_global': [round(global_pos.x()), round(global_pos.y())],
                'release_model': list(model_point) if model_point else None,
                'press_chest_hit': press_chest,
                'release_chest_hit': release_chest,
                'press_silhouette_hit': press_silhouette,
                'release_silhouette_hit': release_silhouette,
                'elapsed_seconds': round(elapsed, 3),
                'drag': was_dragging,
                'valid_click': valid_click,
                'registered': bool(registered),
                'annoyance_before': before,
                'annoyance_after': self.annoyance_count,
                'attempted_expression': attempted_expression,
                'active_expression': self.idle_expression,
            }, ensure_ascii=False), flush=True)
            self.poke_debug_overlay.update()

    def begin_drag(self, global_pos):
        self.cancel_feeding()
        self.stop_active_motion()
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
        if (not self.canvas.model or self.canvas.sleeping or self.dragging or
                self.menu_active or self.active_motion_kind is not None or
                self.feeding_active):
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
        if (self.canvas.sleeping or self.dragging or self.menu_active or
                self.active_motion_kind is not None or self.feeding_active):
            return
        if (self.annoyance_count > 0 or
                self.annoyance_reconciliation_index is not None):
            self.deescalate_annoyance(now)
            return
        self.start_positive_reaction(now)

    def start_positive_reaction(self, now, previous_expression=None,
                                recovered_negative=False):
        if previous_expression is None:
            recovered_negative = self.idle_kind == 'negative'
            previous_expression = self.canvas.expression
            if self.idle_expression is not None:
                # Expression ownership transfers directly to positive. Preserve
                # the underlying baseline without rendering an intermediate frame.
                previous_expression = self.idle_previous_expression
                self.cancel_idle_reaction(
                    restore=False, reschedule=False, now=now, include_negative=True)
                self.idle.interact(now)
                self.cancel_relocation(now)
            else:
                self.note_interaction(now)
        else:
            self.idle.interact(now)
            self.cancel_relocation(now)
        if self.reaction_expression is None:
            self.reaction_expression = previous_expression
            self.canvas.model.ResetExpressions()
            if self.positive_reactions:
                selected = random.choices(
                    self.positive_reactions,
                    weights=[item['weight'] for item in self.positive_reactions], k=1)[0]
                self.reaction_values = selected['values']
                if selected['expression']:
                    self.canvas.select_expression(selected['expression'])
                else:
                    self.canvas.model.ResetExpressions()
            self.reaction_hold_seconds = random.uniform(
                *self.profile['petting']['positive_hold_seconds'])
        self.reaction_count += 1
        self.reaction_until = now + self.reaction_hold_seconds
        self.cancel_relocation(now)
        suffix = ' recovered_negative' if recovered_negative else ''
        print(f'PETTING detected={self.reaction_count}{suffix}', flush=True)

    def reset_annoyance(self):
        self.annoyance_count = 0
        self.annoyance_last_poke = 0.0
        self.annoyance_base_expression = None
        self.annoyance_reconciliation_index = None

    def annoyance_level(self, level):
        settings = self.profile.get('poke') or {}
        return next((item for item in settings.get('levels', [])
                     if item['level'] == level), None)

    def apply_annoyance_level(self, level, now):
        entry = self.annoyance_level(level)
        if entry is None or entry['asset'] not in self.canvas.expression_indexes:
            return False
        if self.annoyance_count == 0 or self.annoyance_base_expression is None:
            self.annoyance_base_expression = (
                self.idle_previous_expression
                if self.idle_expression is not None else self.canvas.expression)
        if self.idle_expression is not None:
            self.cancel_idle_reaction(
                restore=False, reschedule=False, now=now, include_negative=True)
        if self.reaction_expression is not None or self.reaction_level > 0:
            self.cancel_reaction()
        self.annoyance_count = level
        self.annoyance_reconciliation_index = None
        self.annoyance_last_poke = now
        kind = 'negative' if entry.get('persistent_negative', False) else 'annoyance'
        started = self.start_idle_reaction(
            now, entry['asset'], float('inf'), kind, allow_interaction=True)
        if started:
            self.idle_previous_expression = self.annoyance_base_expression
            if kind == 'negative':
                self.idle.next_major = float('inf')
        return started

    def apply_reconciliation_step(self, index, now):
        settings = self.profile.get('poke') or {}
        steps = settings.get('reconciliation', [])
        if not 0 <= index < len(steps):
            return False
        entry = steps[index]
        configured = (self.reconciliation_reactions[index]
                      if index < len(self.reconciliation_reactions) else None)
        if configured is None:
            return False
        if self.annoyance_base_expression is None:
            self.annoyance_base_expression = (
                self.idle_previous_expression
                if self.idle_expression is not None else self.canvas.expression)
        if self.idle_expression is not None:
            self.cancel_idle_reaction(
                restore=False, reschedule=False, now=now, include_negative=True)
        if self.reaction_expression is not None or self.reaction_level > 0:
            self.cancel_reaction(restore=False)
        self.annoyance_reconciliation_index = index
        self.annoyance_count = entry['remaining_level']
        self.annoyance_last_poke = now
        if configured['values']:
            self.reaction_expression = self.annoyance_base_expression
            self.reaction_values = configured['values']
            self.reaction_hold_seconds = float('inf')
            self.reaction_until = float('inf')
            if configured['expression']:
                self.canvas.select_expression(configured['expression'])
            else:
                self.canvas.model.ResetExpressions()
            return True
        started = self.start_idle_reaction(
            now, configured['expression'], float('inf'), 'annoyance',
            allow_interaction=True)
        if started:
            self.idle_previous_expression = self.annoyance_base_expression
        return started

    def deescalate_annoyance(self, now):
        settings = self.profile.get('poke') or {}
        steps = settings.get('reconciliation', [])
        self.idle.interact(now)
        self.cancel_relocation(now)

        if self.annoyance_reconciliation_index is None:
            next_index = next(
                (index for index, item in enumerate(steps)
                 if item['remaining_level'] == self.annoyance_count - 1),
                len(steps))
        else:
            next_index = self.annoyance_reconciliation_index + 1

        if next_index < len(steps):
            source = self.idle_expression
            self.reaction_count += 1
            if self.apply_reconciliation_step(next_index, now):
                print(f'PETTING reconcile={ascii(source)}->{ascii(steps[next_index]["asset"])}',
                      flush=True)
            return

        baseline = self.annoyance_base_expression
        self.cancel_idle_reaction(
            restore=False, reschedule=False, now=now, include_negative=True)
        continuing_parameter_reaction = self.reaction_expression is not None
        completion = self.reconciliation_completion
        if completion is None:
            if continuing_parameter_reaction or self.reaction_level > 0:
                self.cancel_reaction(restore=False)
            self.reset_annoyance()
            self.start_positive_reaction(
                now, previous_expression=baseline, recovered_negative=True)
            return
        if not continuing_parameter_reaction and self.reaction_level > 0:
            self.cancel_reaction(restore=False)
        self.reset_annoyance()
        self.reaction_expression = baseline
        self.reaction_values = completion['values']
        self.canvas.model.ResetExpressions()
        if completion['expression']:
            self.canvas.select_expression(completion['expression'])
        self.reaction_hold_seconds = random.uniform(
            *self.profile['petting']['positive_hold_seconds'])
        self.reaction_count += 1
        self.reaction_until = now + self.reaction_hold_seconds
        self.cancel_relocation(now)
        print('PETTING reconciliation=complete', flush=True)

    def register_poke(self, now):
        settings = self.profile.get('poke')
        if self.poke_debug:
            print('POKE_DEBUG_REGISTER_ENTER', json.dumps({
                'has_poke_profile': bool(settings),
                'sleeping': self.canvas.sleeping,
                'dragging': self.dragging,
                'menu_active': self.menu_active,
                'annoyance_before': self.annoyance_count,
            }, ensure_ascii=False), flush=True)
        if not settings:
            self.note_interaction(now)
            return False
        if self.canvas.sleeping or self.dragging or self.menu_active:
            return False
        if self.reaction_expression is not None or self.reaction_level > 0:
            self.cancel_reaction()
        maximum = len(settings['levels'])
        if self.annoyance_count >= maximum:
            self.annoyance_count = maximum
            self.annoyance_last_poke = now
            self.idle.interact(now)
            self.cancel_relocation(now)
            print(f'POKE level={maximum} capped', flush=True)
            return True
        level = self.annoyance_count + 1
        started = self.apply_annoyance_level(level, now)
        if started:
            self.idle.interact(now)
            self.cancel_relocation(now)
            entry = self.annoyance_level(level)
            print(f'POKE level={level} reaction={ascii(entry["asset"])}'
                  f' persistent={entry.get("persistent_negative", False)}', flush=True)
        if self.poke_debug:
            entry = self.annoyance_level(level)
            self._poke_debug_expected_expression = entry['asset'] if started and entry else None
            print('POKE_DEBUG_REGISTER_EXIT', json.dumps({
                'started': started,
                'annoyance_after': self.annoyance_count,
                'selected_expression': entry['asset'] if entry else None,
                'canvas_expression': self.canvas.expression,
                'idle_kind': self.idle_kind,
                'idle_expression': self.idle_expression,
            }, ensure_ascii=False), flush=True)
        return started

    def start_idle_reaction(self, now, expression, hold, kind,
                            allow_interaction=False):
        if (self.canvas.sleeping or self.dragging or self.menu_active or
                (self.detector.active and not allow_interaction) or
                self.reaction_expression is not None or self.reaction_level > 0 or
                (self.panel is not None and self.panel.isVisible())):
            return False
        if expression not in self.canvas.expression_indexes:
            return False
        self.idle_previous_expression = self.canvas.expression
        self.idle_expression = expression
        self.idle_kind = kind
        self.idle_until = (float('inf') if kind in ('negative', 'annoyance')
                           else now + hold)
        self.canvas.select_expression(expression)
        self.idle_count += 1
        self.cancel_relocation(now)
        print(f'IDLE_REACTION kind={kind} expression={ascii(expression)} count={self.idle_count}', flush=True)
        return True

    def cancel_idle_reaction(self, restore=True, reschedule=True, now=None,
                             include_negative=False):
        if self.idle_expression is None:
            return
        if (self.idle_kind in ('negative', 'annoyance')
                and not include_negative):
            return
        previous = self.idle_previous_expression
        kind = self.idle_kind
        self.idle_expression = None
        self.idle_kind = None
        self.idle_previous_expression = None
        self.idle_until = 0.0
        if reschedule:
            schedule_kind = 'negative' if kind == 'idle_negative' else kind
            self.idle.finished(
                schedule_kind, time.monotonic() if now is None else now)
        if restore and self.canvas.model and not self.canvas.sleeping:
            self.canvas.select_expression(previous)

    def cancel_reaction(self, restore=True):
        if self.reaction_expression is not None and self.canvas.model:
            previous = self.reaction_expression
            self.reaction_expression = None
            for pid in getattr(self, 'reaction_values', {}):
                self.canvas.set_param(pid, self.canvas.params[pid]['default'])
            if restore:
                self.canvas.select_expression(previous)
        self.reaction_level = 0.0
        self.reaction_until = 0.0
        self.reaction_hold_seconds = 0.0
        self.detector.reset()

    def tick(self):
        now = time.monotonic()
        if self.feeding_active:
            self.update_feeding(now)
        if (self.active_motion_kind is not None and
                now >= self.active_motion_deadline):
            self.stop_active_motion(now)
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
        if self.idle_kind in ('ambient', 'idle_negative') and now >= self.idle_until:
            self.cancel_idle_reaction(include_negative=True)
            self.cancel_relocation(now)
        idle_blocked = (self.dragging or self.menu_active or self.canvas.sleeping or
                        self.feeding_active or self.active_motion_kind is not None or
                        self.canvas.motion_cleanup_active() or self.detector.active or
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
                persistent = payload.get('persistent', True) if kind == 'negative' else False
                display_kind = ('negative' if persistent else 'idle_negative') if kind == 'negative' else kind
                if not self.start_idle_reaction(
                        now, payload['expression'], payload.get('hold'), display_kind):
                    if kind == 'negative':
                        self.idle.finished('negative', now)
        blocked = (not self.autonomous or self.dragging or self.menu_active or self.canvas.sleeping or
                   self.feeding_active or self.active_motion_kind is not None or
                   self.canvas.motion_cleanup_active() or
                   self.detector.active or
                   self.reaction_level > 0 or self.idle_expression is not None or
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
        if self.poke_debug:
            lifecycle = (
                self.canvas.expression, self.idle_kind, self.idle_expression,
                self.reaction_expression, self.annoyance_count, self.canvas.sleeping)
            if lifecycle != self._poke_debug_last_lifecycle:
                expected = self._poke_debug_expected_expression
                print('POKE_DEBUG_LIFECYCLE', json.dumps({
                    'canvas_expression': self.canvas.expression,
                    'idle_kind': self.idle_kind,
                    'idle_expression': self.idle_expression,
                    'reaction_expression': self.reaction_expression,
                    'annoyance_level': self.annoyance_count,
                    'sleeping': self.canvas.sleeping,
                    'expected_poke_expression': expected,
                    'poke_expression_overwritten': bool(
                        expected and (self.canvas.expression != expected or
                                      self.idle_expression != expected)),
                }, ensure_ascii=False), flush=True)
                self._poke_debug_last_lifecycle = lifecycle

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
        if self.feeding_active and self.feeding_mouth_pid in c.params:
            c.set_param(self.feeding_mouth_pid, self.feeding_mouth_value)

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


    def feeding_supported(self):
        feeding = self.profile.get('feeding')
        mouth = self.model_profile.parameter_id('mouth_open')
        return bool(
            self.canvas.model and isinstance(feeding, dict) and
            mouth in self.canvas.params and self.food_overlay is not None and
            self.project_model_point(feeding.get('mouth_target_model')) is not None)

    def feeding_enabled(self):
        return bool(
            self.feeding_supported() and not self.canvas.sleeping and
            not self.feeding_active and not self.feeding_pending and
            self.active_motion_kind is None and
            not self.canvas.motion_cleanup_active() and
            not self.persistent_negative_active() and
            self.reaction_expression is None and self.reaction_level == 0)

    def request_feeding(self):
        self.feeding_pending = True
        QTimer.singleShot(0, self.dispatch_pending_feeding)

    def dispatch_pending_feeding(self):
        if self.menu_active or not self.feeding_pending:
            return False
        self.feeding_pending = False
        return self.start_feeding()

    def set_feeding_expression(self, role):
        asset = self.feeding_reactions.get(role)
        if asset:
            self.canvas.select_expression(asset)

    def start_feeding(self, now=None):
        if not self.feeding_enabled() or self.menu_active:
            return False
        now = time.monotonic() if now is None else now
        feeding = self.profile['feeding']
        target = self.project_model_point(feeding['mouth_target_model'])
        if target is None:
            return False
        if self.idle_kind == 'ambient':
            self.cancel_idle_reaction()
        mouth = self.model_profile.parameter_id('mouth_open')
        self.feeding_previous_expression = self.canvas.expression
        self.feeding_mouth_pid = mouth
        self.feeding_mouth_baseline = self.canvas.model.GetParameterValue(
            self.canvas.params[mouth]['index'])
        self.feeding_mouth_value = self.feeding_mouth_baseline
        self.feeding_started = now
        self.feeding_stage = 'approach'
        self.feeding_active = True
        size = max(1, round(
            self.character_height * feeding['food_size_character_fraction']))
        offset = feeding['start_offset_character_fraction']
        start = target + QPointF(
            self.character_height * offset[0], self.character_height * offset[1])
        try:
            self.food_overlay.configure(start, target, size)
            self.refresh_feeding_input_region()
            self.set_feeding_expression('anticipation')
        except Exception as error:
            print(f'FEEDING start failed: {error}', flush=True)
            self.cancel_feeding(now=now)
            return False
        self.idle.interact(now)
        self.cancel_relocation(now)
        self.detector.reset()
        print(f'FEEDING started target=({target.x():.1f},{target.y():.1f}) '
              f'baseline={self.feeding_mouth_baseline:.3f}', flush=True)
        return True

    def update_feeding(self, now):
        if not self.feeding_active:
            return
        feeding = self.profile['feeding']
        approach = feeding['approach_seconds']
        close = feeding['close_seconds']
        satisfaction = feeding['satisfaction_seconds']
        elapsed = max(0.0, now - self.feeding_started)
        open_value = feeding['mouth_open_value']
        if elapsed < approach:
            progress = elapsed / approach
            blend = smooth(progress)
            self.feeding_mouth_value = (
                self.feeding_mouth_baseline * (1.0 - blend) + open_value * blend)
            self.food_overlay.set_progress(progress)
            return
        if self.feeding_stage == 'approach':
            self.feeding_stage = 'eating'
            self.food_overlay.hide_food()
            self.refresh_feeding_input_region()
            self.set_feeding_expression('eating')
        if elapsed < approach + close:
            blend = smooth((elapsed - approach) / close)
            self.feeding_mouth_value = (
                open_value * (1.0 - blend) + self.feeding_mouth_baseline * blend)
            return
        self.feeding_mouth_value = self.feeding_mouth_baseline
        if self.feeding_stage != 'satisfaction':
            self.feeding_stage = 'satisfaction'
            self.set_feeding_expression('satisfaction')
        if elapsed >= approach + close + satisfaction:
            self.cancel_feeding(now=now)

    def cancel_feeding(self, restore_expression=True, now=None):
        if not self.feeding_active and not self.feeding_pending:
            return False
        self.feeding_pending = False
        was_active = self.feeding_active
        previous = self.feeding_previous_expression
        mouth = self.feeding_mouth_pid
        baseline = self.feeding_mouth_baseline
        self.feeding_active = False
        self.feeding_started = 0.0
        self.feeding_stage = None
        if self.food_overlay is not None:
            self.food_overlay.hide_food()
        if was_active and self.canvas.model and mouth in self.canvas.params:
            self.canvas.set_param(mouth, baseline)
        if (was_active and restore_expression and self.canvas.model and
                not self.canvas.sleeping):
            self.canvas.select_expression(previous)
        self.feeding_mouth_pid = None
        self.feeding_mouth_baseline = 0.0
        self.feeding_mouth_value = 0.0
        self.feeding_previous_expression = None
        self.refresh_feeding_input_region()
        now = time.monotonic() if now is None else now
        self.idle.interact(now)
        self.relocator.pause(now)
        if was_active:
            print('FEEDING stopped', flush=True)
        return was_active

    def persistent_negative_active(self):
        if self.idle_kind == 'negative':
            return True
        poke = self.profile.get('poke') or {}
        levels = poke.get('levels', [])
        return bool(
            levels and self.annoyance_count >= len(levels) and
            levels[-1].get('persistent_negative', False))

    def greeting_supported(self):
        asset = self.model_profile.motion_asset('greeting')
        return bool(
            self.canvas.model and asset in self.canvas.motion_indexes and
            self.model_profile.motion_duration('greeting') is not None)

    def greeting_enabled(self):
        return bool(
            self.greeting_supported() and not self.canvas.sleeping and
            self.active_motion_kind is None and not self.feeding_active and
            not self.feeding_pending and
            not self.canvas.motion_cleanup_active() and
            not self.persistent_negative_active())

    def request_greeting(self):
        self.greeting_pending = True
        QTimer.singleShot(0, self.dispatch_pending_greeting)

    def dispatch_pending_greeting(self):
        if self.menu_active or not self.greeting_pending:
            return False
        self.greeting_pending = False
        return self.start_greeting()

    def start_greeting(self, now=None):
        if not self.greeting_enabled() or self.menu_active:
            return False
        now = time.monotonic() if now is None else now
        if not self.canvas.start_motion('greeting'):
            return False
        self.active_motion_kind = 'greeting'
        self.active_motion_deadline = (
            now + self.model_profile.motion_duration('greeting'))
        self.idle.interact(now)
        self.cancel_relocation(now)
        self.detector.reset()
        print(f'USER_MOTION kind=greeting started deadline={self.active_motion_deadline:.3f}',
              flush=True)
        return True

    def stop_active_motion(self, now=None):
        if self.active_motion_kind is None:
            return False
        kind = self.active_motion_kind
        cleanup = self.model_profile.motion_cleanup_duration(kind)
        self.canvas.stop_motion(cleanup)
        self.active_motion_kind = None
        self.active_motion_deadline = 0.0
        now = time.monotonic() if now is None else now
        self.relocator.pause(now)
        print(f'USER_MOTION kind={kind} stopped', flush=True)
        return True

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
        feeding = menu.addAction('간식 주기')
        feeding.setEnabled(self.feeding_enabled())
        feeding.triggered.connect(self.request_feeding)
        menu._feeding_action = feeding
        greeting = menu.addAction('인사하기')
        greeting.setEnabled(self.greeting_enabled())
        greeting.triggered.connect(self.request_greeting)
        menu._greeting_action = greeting
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

    def cancel_pointer_gesture(self):
        had_pointer = self.pointer_pressed
        self.pointer_pressed = False
        self.pointer_press_global = None
        self.pointer_press_local = None
        self.pointer_press_on_silhouette = False
        self.pointer_press_in_poke_region = False
        if self.dragging:
            self.end_drag()
        elif had_pointer:
            self.canvas.releaseMouse()
            self.interrupt()
            self.detector.block_for(
                time.perf_counter(), self.profile['petting']['post_drag_seconds'])

    def open_menu(self, position):
        # A context-menu event can arrive while the left-button grab is active.
        # End that gesture without passing through the click/poke release path.
        self.cancel_pointer_gesture()
        self.menu_active = True
        self.interrupt()
        # Menu actions that change model state (for example sleep) own their
        # cleanup. Opening the menu alone must not erase the face expression
        # that a user motion such as greeting is meant to preserve.
        menu = self.build_menu()
        try:
            menu.exec(position)
        finally:
            self.menu_active = False
            self.interrupt()
            menu.deleteLater()
            if self.greeting_pending:
                QTimer.singleShot(0, self.dispatch_pending_greeting)
            if self.feeding_pending:
                QTimer.singleShot(0, self.dispatch_pending_feeding)

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
            if msg.message == 0x84 and self.feeding_active and self.food_overlay is not None:
                # The temporary mask exposes the cookie path for painting. Cookie-only
                # pixels remain click-through while character pixels keep normal input.
                x = ctypes.c_short(msg.lParam & 0xffff).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xffff).value
                local = self.mapFromGlobal(QPoint(x, y))
                canvas_point = local - self._content_offset
                if (self.food_overlay.path_rect.contains(canvas_point) and
                        not self._character_input_region.contains(local)):
                    return True, -1  # HTTRANSPARENT
            if msg.message == 0x21:  # WM_MOUSEACTIVATE: preserve the other app's keyboard focus.
                return True, 3  # MA_NOACTIVATE, mouse event still delivered.
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event):
        self.cancel_feeding(restore_expression=False)
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
