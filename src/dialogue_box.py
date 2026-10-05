# -*- coding: utf-8 -*-
"""Undertale-style dialogue box: black background, white border, pixel
font, typewriter reveal. Wide and short (2.5x-3x the pet's width), wraps
to at most 3 lines per page and paginates longer segments. Follows the
pet, staying within ~10px of it at all times.
"""
import random

from PyQt6.QtCore import Qt, QRect, QRectF, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from fonts import pixel_font
from win_zorder import apply_topmost
from undertale_ui import (
    COLOR_SELECTED,
    COLOR_TEXT,
    HEART_PIXEL_SCALE,
    HeartCursor,
    clamp_rect_to_screen,
    screen_available_rect,
)

WIDTH_MULTIPLIER = 2.75
MAX_LINES_PER_PAGE = 3
PADDING_BASE = 14
BORDER_WIDTH_BASE = 3
BASE_FONT_PX = 12
LINE_SPACING_EXTRA = 6
GAP_ABOVE_PET = 8
DEFAULT_SCREEN_MARGIN = 8
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


class _ChoiceLabel(QLabel):
    """Clickable/hoverable text label -- the building block of
    ConfirmChoiceBar's [知道了]/[等等] options."""

    clicked = pyqtSignal()
    hovered = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        event.accept()

    def enterEvent(self, event):
        self.hovered.emit()
        super().enterEvent(event)


class ConfirmChoiceBar(QWidget):
    """Undertale-style choice row for the reminder/pursuit popups: a heart
    cursor sits next to whichever option is selected. Driven by mouse
    hover/click, or by DialogueBox.keyPressEvent forwarding Up/Down/
    Left/Right (move) and Enter/Z (activate) once the box has been made
    focusable (see DialogueBox._enable_choice_input). With a single option
    (sleep reminders, which have nothing to escalate to) it's just a
    heart+label with no navigation needed."""

    activated = pyqtSignal(str)  # emits the activated option's key

    def __init__(self):
        super().__init__()
        self._font = None
        self._keys = []
        self._hearts = []
        self._labels = []
        self._selected = 0
        self.on_selection_changed = None  # callable(), e.g. a menu-move sound
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(BUTTON_GAP)

    def set_font(self, font):
        self._font = font

    def set_options(self, options, heart_scale):
        """options: ordered list of (key, text) tuples, 1 or 2 entries."""
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._keys, self._hearts, self._labels = [], [], []
        for idx, (key, text) in enumerate(options):
            group = QWidget()
            g_layout = QHBoxLayout(group)
            g_layout.setContentsMargins(0, 0, 0, 0)
            g_layout.setSpacing(4)
            heart = HeartCursor(pixel_scale=heart_scale)
            heart.setVisible(False)
            label = _ChoiceLabel(text)
            label.setFont(self._font)
            label.setStyleSheet(f"color: {COLOR_TEXT}; background: transparent;")
            label.clicked.connect(lambda k=key: self.activated.emit(k))
            label.hovered.connect(lambda i=idx: self._select(i))
            g_layout.addWidget(heart)
            g_layout.addWidget(label)
            self._layout.addWidget(group)
            self._keys.append(key)
            self._hearts.append(heart)
            self._labels.append(label)
        self._selected = 0
        self._refresh()
        self.adjustSize()

    def _refresh(self):
        for i in range(len(self._keys)):
            is_sel = i == self._selected
            self._hearts[i].setVisible(is_sel)
            color = COLOR_SELECTED if is_sel else COLOR_TEXT
            self._labels[i].setStyleSheet(f"color: {color}; background: transparent;")

    def _select(self, idx):
        if idx == self._selected or not (0 <= idx < len(self._keys)):
            return
        self._selected = idx
        self._refresh()
        if self.on_selection_changed:
            self.on_selection_changed()

    def move_selection(self, direction):
        """Cycles the heart between the 1-2 options. Returns False (no-op)
        when there's nothing to move between, so a single-button [知道了]
        popup (sleep) simply ignores Up/Down/Left/Right."""
        if len(self._keys) < 2:
            return False
        self._select((self._selected + direction) % len(self._keys))
        return True

    def activate_selected(self):
        if self._keys:
            self.activated.emit(self._keys[self._selected])


class DialogueBox(QWidget):
    finished = pyqtSignal()
    # Emitted the moment the choice bar appears (typing done, popup now just
    # sitting there waiting on a click/timeout). PetWindow uses this to drop
    # back into the idle bobbing animation instead of freezing in the talk
    # pose for the whole 30-60s hold.
    hold_started = pyqtSignal()

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
        screen_margin: int = DEFAULT_SCREEN_MARGIN,
    ):
        self._pin = always_on_top
        self._confirm_hold_ms = confirm_hold_ms
        self.test_mode = test_mode
        self._screen_margin = screen_margin
        self._accept_focus = False  # only True while the choice bar is up, see _enable_choice_input
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

        self._choice_options = None  # list[(key, label)] while a choice popup is active
        self.response_outcome = None  # None | "confirm" | "wait" | "dismiss" | "timeout"
        self._button_bar = ConfirmChoiceBar()
        self._button_bar.setParent(self)
        self._button_bar.hide()
        self._button_bar.activated.connect(self._on_choice_activated)
        self._button_bar.on_selection_changed = self.audio.play_menu_move

        # Owned, stoppable hold timer (NOT a bare singleShot): a stale
        # singleShot from an earlier popup could fire while a LATER popup's
        # bar was up and close it early as a bogus timeout -- one of the
        # chase system's original root causes (see reminder_chase.py).
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._auto_close_after_confirm_window)
        self._active_hold_ms = confirm_hold_ms

    # --------------------------------------------------------------- window
    def _window_flags(self):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if not self._accept_focus:
            flags |= Qt.WindowType.WindowDoesNotAcceptFocus
        if self._pin:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def set_always_on_top(self, pin: bool):
        if pin == self._pin:
            return
        self._pin = pin
        apply_topmost(self, pin)

    def is_pinned(self):
        """Public hook for GameDetector/TopmostGuard, mirrors
        PetWindow.is_pinned_on_top()."""
        return self._pin

    # --------------------------------------------------------------- sizing
    def reconfigure(self, pet_width_px: int, font_scale: float, pet_scale: float = 1.0, min_font_px: int = None):
        """(Re)computes every scale-dependent dimension -- box size, font,
        border, padding -- from the pet's current display width and size
        tier. Called once from __init__ and again whenever the pet's size
        tier changes, so a live bubble picks up the new scale without
        needing to be torn down and recreated."""
        self.box_width = round(pet_width_px * WIDTH_MULTIPLIER)
        self._pet_scale = pet_scale
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
    def start(self, segments, on_segment_face, choice_options=None, hold_ms=None):
        """choice_options: ordered list of (outcome_key, label) tuples --
        e.g. [("confirm", "确定"), ("等等" …)] -- shown as an Undertale
        choice bar after the last segment; None means a plain bubble that
        just closes itself. hold_ms overrides the constructor-default hold
        for this popup only (首次提醒 vs 追讨弹窗 have different holds)."""
        self.on_segment_face = on_segment_face
        self._segments = segments
        self._seg_idx = -1
        self._choice_options = list(choice_options) if choice_options else None
        self._active_hold_ms = hold_ms if hold_ms else self._confirm_hold_ms
        self.response_outcome = None
        self._hold_timer.stop()
        self._hide_confirm_bar()
        self._advance_segment()
        self.show()

    def stop_immediately(self):
        self._typing_timer.stop()
        self._glitch_timer.stop()
        self._hold_timer.stop()
        self._hide_confirm_bar()
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
            if self._choice_options:
                self._show_confirm_bar(self._choice_options)
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
        # Check every face this segment will switch through, not just the
        # first one -- a multi-face segment that only reaches "creepy"/
        # "evil" partway through (e.g. ["gritted_teeth", "creepy"]) must
        # still get the laugh and go silent for its whole duration.
        faces_in_segment = self._face_schedule if self._face_schedule else [face]
        self._silent_segment = any(f in EVIL_LAUGH_FACES for f in faces_in_segment)
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
    def _show_confirm_bar(self, options):
        heart_scale = max(1, round(HEART_PIXEL_SCALE * self._pet_scale))
        self._button_bar.set_font(self.font)
        self._button_bar.set_options(options, heart_scale)
        bw, bh = self._button_bar.width(), self._button_bar.height()
        x = self.width() - bw - self._padding // 2
        y = self.height() - bh - self._padding // 2
        self._button_bar.move(x, y)
        self._button_bar.show()
        self._button_bar.raise_()
        # Deliberately NOT calling _enable_choice_input() here -- see its
        # docstring. Keyboard nav only turns on once the mouse is actually
        # hovering the box (enterEvent below); mouse clicks on the bar's
        # labels work regardless, since Qt delivers mouse input to a
        # WindowDoesNotAcceptFocus widget without requiring activation.
        self._hold_timer.start(self._active_hold_ms)
        self.hold_started.emit()

    def _on_choice_activated(self, key):
        if not self.isVisible():
            return
        self.audio.play_menu_select()
        self.response_outcome = key  # "confirm" | "wait" | "dismiss"
        self._hold_timer.stop()
        self._hide_confirm_bar()
        self.hide()
        self.finished.emit()

    def _auto_close_after_confirm_window(self):
        if not self.isVisible() or not self._button_bar.isVisible():
            return  # already closed via a button click
        self.response_outcome = "timeout"
        self._hide_confirm_bar()
        self.hide()
        self.finished.emit()

    def _hide_confirm_bar(self):
        self._button_bar.hide()
        self._disable_choice_input()

    # ----------------------------------------------------- choice keyboard nav
    def _enable_choice_input(self):
        """The box is normally WindowDoesNotAcceptFocus (so ambient chatter
        never steals focus from whatever app the user is using), and that
        must stay true even while the [知道了]/[等等] bar is up -- reminders
        fire on a timer, completely unrelated to whatever the user happens
        to be typing into elsewhere, so unconditionally activating this
        window the instant a popup appears would yank OS keyboard focus
        away from that other app on every single reminder. Real keyboard
        nav is only granted while the mouse is actually hovering the box
        (see enterEvent/leaveEvent below) -- at that point the user has
        deliberately moved their attention here, so taking focus is safe.
        Mouse clicks on the bar's labels work with no focus change at all,
        since Qt still delivers mouse input to a WindowDoesNotAcceptFocus
        widget without activating it."""
        if self._accept_focus or not self._button_bar.isVisible():
            return
        self._accept_focus = True
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setWindowFlags(self._window_flags())
        self.show()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.activateWindow()

    def _disable_choice_input(self):
        if not self._accept_focus:
            return
        # Capture BEFORE setWindowFlags(): once it hides this top-level
        # window as a Qt side effect, the child button bar's own
        # isVisible() immediately reads False too (a child is only
        # "visible" while every ancestor is), so checking it afterward
        # could never tell a real close apart from this hide side effect.
        bar_was_up = self._button_bar.isVisible()
        self._accept_focus = False
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowFlags(self._window_flags())
        # If the choice bar was still up (ordinary mouse-leaves-the-box
        # case, e.g. the cursor overshooting the small button while aiming
        # for it), the popup must stay on screen -- re-show it.
        # _hide_confirm_bar() already hid the button bar before calling us
        # when a real close is in progress, so this correctly no-ops there
        # instead of flashing the box back up right before self.hide().
        if bar_was_up:
            self.show()

    def enterEvent(self, event):
        self._enable_choice_input()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._disable_choice_input()
        super().leaveEvent(event)

    def keyPressEvent(self, event):
        if not self._button_bar.isVisible():
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Left):
            self._button_bar.move_selection(-1)
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_Right):
            self._button_bar.move_selection(1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Z):
            self._button_bar.activate_selected()
        else:
            super().keyPressEvent(event)

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
    def follow(self, pet_x, anchor_top, pet_w, pet_bottom):
        """Positions the bubble centered above `anchor_top` (the pet's own
        top, or the pomodoro badge's top when that's showing above the
        pet) -- unless that would push it off the top of whichever screen
        the pet is actually on, in which case it flips to below the pet
        instead (`pet_bottom`). If neither the above nor the below spot
        fits cleanly, falls back to clamping the above position onto
        screen with a safety margin, same as the popup panels. Never
        settles on a spot that overlaps the pet's own body if a
        non-overlapping option was available."""
        bw, bh = self.width(), self.height()
        pet_h = max(1, pet_bottom - anchor_top)
        screen = screen_available_rect(pet_x, anchor_top, pet_w, pet_h)
        margin = self._screen_margin

        x = pet_x + (pet_w - bw) // 2
        y_above = anchor_top - bh - GAP_ABOVE_PET
        y_below = pet_bottom + GAP_ABOVE_PET

        if y_above >= screen.y() + margin:
            y = y_above
        elif y_below + bh <= screen.y() + screen.height() - margin:
            y = y_below
        else:
            y = y_above  # neither fits cleanly; the clamp below does its best

        rect = clamp_rect_to_screen(QRect(x, y, bw, bh), screen, margin)

        pet_rect = QRect(pet_x, anchor_top, pet_w, pet_h)
        if rect.intersects(pet_rect):
            below = clamp_rect_to_screen(QRect(x, y_below, bw, bh), screen, margin)
            if not below.intersects(pet_rect):
                rect = below

        self.move(rect.x(), rect.y())
