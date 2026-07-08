# -*- coding: utf-8 -*-
"""Undertale-style dialogue box: black background, white border, pixel
font, typewriter reveal. Wide and short (2.5x-3x the pet's width), wraps
to at most 3 lines per page and paginates longer segments. Follows the
pet, staying within ~10px of it at all times.
"""
import random

from PyQt6.QtCore import Qt, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QPushButton, QWidget

from fonts import pixel_font

WIDTH_MULTIPLIER = 2.75
MAX_LINES_PER_PAGE = 3
PADDING_BASE = 14
BORDER_WIDTH_BASE = 3
BASE_FONT_PX = 12
LINE_SPACING_EXTRA = 6
GAP_ABOVE_PET = 8
TYPE_INTERVAL_MS = 38
SLOW_TYPE_INTERVAL_MS = 130
PAGE_PAUSE_MS = 1100
SEGMENT_END_PAUSE_MS = 400
DEFAULT_CONFIRM_HOLD_MS = 60_000
BUTTON_GAP = 12
GLITCH_FLICKER_MS = 90
GLITCH_TOTAL_MS = 500
GLITCH_FACES = ("crazed", "drag_face", "evil_color", "creepy")

EVIL_LAUGH_FACES = {"evil", "creepy"}


class DialogueBox(QWidget):
    finished = pyqtSignal()

    def __init__(
        self,
        pet_width_px: int,
        font_scale: float,
        audio,
        always_on_top: bool = True,
        confirm_hold_ms: int = DEFAULT_CONFIRM_HOLD_MS,
        test_mode: bool = False,
        pet_scale: float = 1.0,
        min_font_px: int = None,
    ):
        self._pin = always_on_top
        self._confirm_hold_ms = confirm_hold_ms
        self.test_mode = test_mode
        super().__init__(None, self._window_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.audio = audio
        self.reconfigure(pet_width_px, font_scale, pet_scale, min_font_px)

        self._segments = []
        self._seg_idx = 0
        self._pages = []
        self._page_idx = 0
        self._full_page_text = ""
        self._revealed = 0
        self._silent_segment = False
        self._face_schedule = None  # list of face tags for the current segment, if 2+
        self._face_switch_points = []  # [(char_threshold, face_tag), ...] remaining

        self._typing_timer = QTimer(self)
        self._typing_timer.timeout.connect(self._tick)
        self._type_interval = TYPE_INTERVAL_MS

        self._glitch_timer = QTimer(self)
        self._glitch_timer.timeout.connect(self._glitch_tick)
        self._glitch_elapsed_ms = 0

        self.on_segment_face = None  # callable(face_key)

        self._require_confirm = False
        self.response_outcome = None  # None | "confirmed" | "acknowledged"
        self._confirm_btn = QPushButton("确定", self)
        self._confirm_btn.hide()
        self._confirm_btn.clicked.connect(self._on_confirm_clicked)
        self._ack_btn = QPushButton("知道了", self)
        self._ack_btn.hide()
        self._ack_btn.clicked.connect(self._on_ack_clicked)

    # --------------------------------------------------------------- window
    def _window_flags(self):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self._pin:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def set_always_on_top(self, pin: bool):
        if pin == self._pin:
            return
        self._pin = pin
        was_visible = self.isVisible()
        self.setWindowFlags(self._window_flags())
        if was_visible:
            self.show()

    # --------------------------------------------------------------- sizing
    def reconfigure(self, pet_width_px: int, font_scale: float, pet_scale: float = 1.0, min_font_px: int = None):
        """(Re)computes every scale-dependent dimension -- box size, font,
        border, padding -- from the pet's current display width and size
        tier. Called once from __init__ and again whenever the pet's size
        tier changes, so a live bubble picks up the new scale without
        needing to be torn down and recreated."""
        self.box_width = round(pet_width_px * WIDTH_MULTIPLIER)
        font_px = round(BASE_FONT_PX * font_scale * pet_scale)
        if min_font_px is not None:
            font_px = max(font_px, min_font_px)
        self.font = pixel_font(font_px)
        self._metrics = QFontMetrics(self.font)
        self._padding = max(1, round(PADDING_BASE * pet_scale))
        self._border_width = max(1, round(BORDER_WIDTH_BASE * pet_scale))
        line_h = self._metrics.height() + LINE_SPACING_EXTRA
        self._line_h = line_h
        self.box_height = self._padding * 2 + line_h * MAX_LINES_PER_PAGE
        self.setFixedSize(self.box_width, self.box_height)

    # ------------------------------------------------------------- lifecycle
    def start(self, segments, on_segment_face, require_confirm=False):
        self.on_segment_face = on_segment_face
        self._segments = segments
        self._seg_idx = -1
        self._require_confirm = require_confirm
        self.response_outcome = None
        self._confirm_btn.hide()
        self._ack_btn.hide()
        self._advance_segment()
        self.show()

    def stop_immediately(self):
        self._typing_timer.stop()
        self._glitch_timer.stop()
        self._confirm_btn.hide()
        self._ack_btn.hide()
        self.hide()

    # --------------------------------------------------------------- layout
    def _wrap(self, text):
        content_w = self.box_width - self._padding * 2
        lines = []
        current = ""
        for ch in text:
            if ch == "\n":
                lines.append(current)
                current = ""
                continue
            trial = current + ch
            if self._metrics.horizontalAdvance(trial) > content_w and current:
                lines.append(current)
                current = ch
            else:
                current = trial
        lines.append(current)
        return lines

    def _paginate(self, lines):
        return [
            lines[i : i + MAX_LINES_PER_PAGE]
            for i in range(0, len(lines), MAX_LINES_PER_PAGE)
        ] or [[""]]

    # ------------------------------------------------------------ sequencing
    def _advance_segment(self):
        self._seg_idx += 1
        if self._seg_idx >= len(self._segments):
            self._typing_timer.stop()
            if self._require_confirm:
                self._show_confirm_button()
                return
            self.hide()
            self.finished.emit()
            return
        seg = self._segments[self._seg_idx]
        face = seg.get("face", "neutral")
        if isinstance(face, list) and face:
            self._face_schedule = face
            face = face[0]
        else:
            self._face_schedule = None
        self._type_interval = SLOW_TYPE_INTERVAL_MS if seg.get("slow") else TYPE_INTERVAL_MS

        if seg.get("effect") == "glitch":
            self._start_glitch(face, seg)
            return

        hold_seconds = seg.get("hold_seconds")
        if hold_seconds:
            if self.on_segment_face:
                self.on_segment_face(face)
            self._full_page_text = ""
            self._revealed = 0
            self.update()
            self.show()
            QTimer.singleShot(int(hold_seconds * 1000), self._advance_segment)
            return

        if self.on_segment_face:
            self.on_segment_face(face)
        self._silent_segment = face in EVIL_LAUGH_FACES
        if self._silent_segment:
            self.audio.play_laugh()
        lines = self._wrap(seg.get("text", ""))
        self._pages = self._paginate(lines)
        self._page_idx = -1
        self._advance_page()

    def _start_glitch(self, settle_face, seg):
        self._glitch_elapsed_ms = 0
        self._glitch_settle_face = settle_face
        self._glitch_seg = seg
        self.show()
        self._glitch_tick()

    def _glitch_tick(self):
        if self._glitch_elapsed_ms >= GLITCH_TOTAL_MS:
            self._glitch_timer.stop()
            seg = self._glitch_seg
            if self.on_segment_face:
                self.on_segment_face(self._glitch_settle_face)
            self._silent_segment = self._glitch_settle_face in EVIL_LAUGH_FACES
            if not seg.get("text"):
                QTimer.singleShot(SEGMENT_END_PAUSE_MS, self._advance_segment)
                return
            lines = self._wrap(seg.get("text", ""))
            self._pages = self._paginate(lines)
            self._page_idx = -1
            self._advance_page()
            return
        if self.on_segment_face:
            self.on_segment_face(random.choice(GLITCH_FACES))
        self._glitch_elapsed_ms += GLITCH_FLICKER_MS
        self._glitch_timer.start(GLITCH_FLICKER_MS)

    def _advance_page(self):
        self._page_idx += 1
        if self._page_idx >= len(self._pages):
            QTimer.singleShot(SEGMENT_END_PAUSE_MS, self._advance_segment)
            return
        self._full_page_text = "\n".join(self._pages[self._page_idx])
        self._revealed = 0
        self._face_switch_points = self._build_face_switch_points()
        self.update()
        self._typing_timer.start(self._type_interval)

    def _build_face_switch_points(self):
        """Evenly spread the schedule's later face tags across the page's
        character count, so a multi-tag segment switches expression partway
        through typing instead of only at the very start."""
        if not self._face_schedule or len(self._face_schedule) < 2:
            return []
        total = len(self._full_page_text)
        n = len(self._face_schedule)
        points = []
        for i in range(1, n):
            threshold = max(1, round(total * i / n))
            points.append((threshold, self._face_schedule[i]))
        return points

    def _tick(self):
        if self._revealed >= len(self._full_page_text):
            self._typing_timer.stop()
            QTimer.singleShot(PAGE_PAUSE_MS, self._advance_page)
            return
        ch = self._full_page_text[self._revealed]
        self._revealed += 1
        while self._face_switch_points and self._revealed >= self._face_switch_points[0][0]:
            _, next_face = self._face_switch_points.pop(0)
            if self.on_segment_face:
                self.on_segment_face(next_face)
        if not self._silent_segment:
            self.audio.play_blip_for_char(ch)
        self.update()

    # ---------------------------------------------------------------- confirm
    def _show_confirm_button(self):
        self._confirm_btn.adjustSize()
        self._ack_btn.adjustSize()
        cbw, cbh = self._confirm_btn.width(), self._confirm_btn.height()
        abw, abh = self._ack_btn.width(), self._ack_btn.height()
        y = self.height() - max(cbh, abh) - self._padding // 2
        self._confirm_btn.move(self.width() - cbw - self._padding // 2, y)
        self._ack_btn.move(self.width() - cbw - BUTTON_GAP - abw - self._padding // 2, y)
        self._confirm_btn.show()
        self._ack_btn.show()
        self._confirm_btn.raise_()
        self._ack_btn.raise_()
        QTimer.singleShot(self._confirm_hold_ms, self._auto_close_after_confirm_window)

    def _on_confirm_clicked(self):
        if not self.isVisible():
            return
        self.response_outcome = "confirmed"
        self._confirm_btn.hide()
        self._ack_btn.hide()
        self.hide()
        self.finished.emit()

    def _on_ack_clicked(self):
        if not self.isVisible():
            return
        self.response_outcome = "acknowledged"
        self._confirm_btn.hide()
        self._ack_btn.hide()
        self.hide()
        self.finished.emit()

    def _auto_close_after_confirm_window(self):
        if not self.isVisible() or not self._confirm_btn.isVisible():
            return  # already closed via a button click
        self._confirm_btn.hide()
        self._ack_btn.hide()
        self.hide()
        self.finished.emit()

    # ---------------------------------------------------------------- paint
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(QColor(0, 0, 0, 235))
        painter.setPen(QPen(QColor(255, 255, 255), self._border_width))
        half = self._border_width / 2
        painter.drawRect(QRectF(half, half, self.width() - self._border_width, self.height() - self._border_width))

        painter.setFont(self.font)
        painter.setPen(QColor(255, 255, 255))
        shown = self._full_page_text[: self._revealed]
        y = self._padding
        for line in shown.split("\n"):
            painter.drawText(self._padding, y + self._metrics.ascent(), line)
            y += self._line_h

        if self.test_mode:
            painter.setPen(QColor(255, 60, 60))
            tag = "TEST"
            tw = self._metrics.horizontalAdvance(tag)
            painter.drawText(self.width() - tw - 6, self._metrics.ascent() + 4, tag)

    # ------------------------------------------------------------ placement
    def follow(self, pet_x, pet_y, pet_w):
        x = pet_x + (pet_w - self.width()) // 2
        y = pet_y - self.height() - GAP_ABOVE_PET
        self.move(x, y)
