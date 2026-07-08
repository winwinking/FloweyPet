# -*- coding: utf-8 -*-
"""Shared Undertale-style UI kit: a borderless black/white pixel-font popup
window plus a small set of composable row widgets (toggle / action / adjust
/ separator) used by both the reminder settings panel and the pet's
right-click menu, so the two stay visually identical without duplicating
layout code.

No image assets are used anywhere here -- the border/background and the
heart cursor are drawn pixel-by-pixel with QPainter (same technique as
DialogueBox's border) and everything else is plain QLabel/QWidget styled
via QSS `color` rules.

UI follow-scale: every panel built on UndertalePopup can shrink slightly
when the pet shrinks (see pet_window.py's _ui_scale -- menus/panels only
follow *part* of the way, via ui_follow_ratio, so they never become
illegibly tiny at the pet's smallest tier). All the base pixel constants
below (PANEL_WIDTH, ROW_HEIGHT, FONT_PIXEL_SIZE, ...) are the values used
at ui_scale=1.0; UiMetrics derives the actual on-screen sizes for whatever
ui_scale a given popup was opened with, with a floor on font size so text
never becomes unreadable.
"""
from PyQt6.QtCore import QRect, QRectF, QTime, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QHBoxLayout,
    QLabel,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from fonts import pixel_font

# --------------------------------------------------------------------- style
# All colors / sizes used by the Undertale UI kit live here so they can be
# tuned in one place. The pixel-size constants are the ui_scale=1.0 baseline
# -- see UiMetrics below for how a popup derives its actual sizes.
COLOR_BG = "#000000"
COLOR_BORDER = "#FFFFFF"
COLOR_TEXT = "#FFFFFF"
COLOR_SELECTED = "#FFFF00"
COLOR_DISABLED = "#808080"
COLOR_HEART = "#FF0000"

BORDER_WIDTH = 4
PANEL_PADDING = 10
PANEL_WIDTH = 180
ROW_HEIGHT = 28
FONT_PIXEL_SIZE = 14
GAP_FROM_PET = 10
UI_MIN_FONT_PX = 10  # fallback floor when a caller doesn't pass min_font_px

# Undertale's SOUL cursor, hand-authored as a 9x8 bitmap (1 = filled).
HEART_BITMAP = [
    "011000110",
    "111101111",
    "111111111",
    "111111111",
    "011111110",
    "001111100",
    "000111000",
    "000010000",
]
HEART_PIXEL_SCALE = 2  # each bitmap cell is drawn this many screen px wide
GUTTER_MARGIN = 8  # blank space on each side of the heart inside the gutter
GUTTER_WIDTH = len(HEART_BITMAP[0]) * HEART_PIXEL_SCALE + GUTTER_MARGIN * 2
SEPARATOR_GAP = 6  # extra height (beyond the line itself) of a separator row

# Value-adjuster tuning: how long a value stays yellow after the last
# wheel/click interaction, and the step tables used by callers.
VALUE_FLASH_MS = 500
WATER_INTERVAL_CHOICES = [15, 30, 45, 60, 90, 120]
TIME_STEP_MINUTES = 15
VOLUME_TIERS = [15, 35, 50, 75, 100]


class UiMetrics:
    """Derives every scale-dependent panel dimension from a single
    ui_scale factor. min_font_px is an explicit floor (typically
    config["ui_min_font_size"]); if omitted, UI_MIN_FONT_PX is used."""

    def __init__(self, ui_scale: float = 1.0, min_font_px: int = None):
        self.ui_scale = ui_scale
        self.row_height = max(1, round(ROW_HEIGHT * ui_scale))
        font_px = round(FONT_PIXEL_SIZE * ui_scale)
        floor = min_font_px if min_font_px is not None else UI_MIN_FONT_PX
        self.font_px = max(font_px, floor)
        self.border_width = max(1, round(BORDER_WIDTH * ui_scale))
        self.panel_padding = max(0, round(PANEL_PADDING * ui_scale))
        self.heart_scale = max(1, round(HEART_PIXEL_SCALE * ui_scale))
        self.gutter_margin = max(0, round(GUTTER_MARGIN * ui_scale))
        self.gutter_width = len(HEART_BITMAP[0]) * self.heart_scale + self.gutter_margin * 2
        self.separator_gap = max(2, round(SEPARATOR_GAP * ui_scale))


DEFAULT_METRICS = UiMetrics(1.0)


def _label(text, color, font, align=Qt.AlignmentFlag.AlignVCenter):
    lbl = QLabel(text)
    lbl.setFont(font)
    lbl.setStyleSheet(f"color: {color}; background: transparent;")
    lbl.setAlignment(align)
    return lbl


class HoverButton(QLabel):
    """确定/取消-style clickable text button: white, turns yellow on hover.
    Shared by ReminderPanel, StatsPanel, and the reminder pursuit popup so
    all three "plain text button" surfaces look and behave identically."""

    clicked = pyqtSignal()

    def __init__(self, text, font):
        super().__init__(text)
        self.setFont(font)
        self.setStyleSheet(f"color: {COLOR_TEXT}; background: transparent;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        event.accept()

    def enterEvent(self, event):
        self.setStyleSheet(f"color: {COLOR_SELECTED}; background: transparent;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(f"color: {COLOR_TEXT}; background: transparent;")
        super().leaveEvent(event)


class ClickableLabel(QLabel):
    """A QLabel that emits `clicked` on left-click and eats the event so it
    doesn't also bubble up to a parent row's click handler."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        event.accept()


class HeartCursor(QWidget):
    """The red SOUL heart, drawn pixel-by-pixel from HEART_BITMAP -- no
    image asset, no antialiasing, hard 1:1 blocky edges. pixel_scale lets a
    shrunk-down panel shrink its cursor to match (see UiMetrics)."""

    def __init__(self, pixel_scale: int = HEART_PIXEL_SCALE):
        super().__init__()
        self._pixel_scale = pixel_scale
        w = len(HEART_BITMAP[0]) * pixel_scale
        h = len(HEART_BITMAP) * pixel_scale
        self.setFixedSize(w, h)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLOR_HEART))
        s = self._pixel_scale
        for row, bits in enumerate(HEART_BITMAP):
            for col, bit in enumerate(bits):
                if bit == "1":
                    painter.drawRect(col * s, row * s, s, s)


# ------------------------------------------------------------------- popup
class UndertalePopup(QWidget):
    """Frameless black-background / white-border popup. Uses Qt.Popup so it
    closes itself on an outside click, matching native menu behavior without
    any manual event-filter plumbing.

    Owns a single "selected row" index shared by mouse hover and keyboard
    Up/Down navigation, so exactly one heart is ever shown. Enter/Z activates
    the selected row; Esc/X closes the popup.

    ui_scale (see UiMetrics) lets a popup opened while the pet is shrunk
    follow along partway -- self.metrics/self.font reflect the derived
    sizes and should be threaded into any row this popup builds."""

    closed = pyqtSignal()
    selection_changed = pyqtSignal(object)  # emits the newly-selected row, mouse hover or keyboard alike

    def __init__(self, audio=None, always_on_top=True, parent=None, ui_scale=1.0, min_font_px=None):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Popup | Qt.WindowType.Tool
        if always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(parent, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.audio = audio
        self.ui_scale = ui_scale
        self.metrics = UiMetrics(ui_scale, min_font_px)
        self.font = pixel_font(self.metrics.font_px)

        self.setFixedWidth(round(PANEL_WIDTH * ui_scale))
        margin = self.metrics.border_width + self.metrics.panel_padding
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(margin, margin, margin, margin)
        self._layout.setSpacing(2)

        self._rows = []  # all rows, including non-selectable ones (separators)
        self._selectable = []  # rows with select_index() capability
        self._selected_index = -1

    # ------------------------------------------------------------- rows
    def register_row(self, row, selectable=True):
        """Hook a row into heart/keyboard selection without placing it in
        the top-level layout -- for rows that live inside a nested container
        (e.g. a dynamically rebuilt list of meal-time rows)."""
        row.set_hover_callback(self._on_row_hover)
        self._rows.append(row)
        if selectable:
            self._selectable.append(row)
        return row

    def add_row(self, row, selectable=True):
        self.register_row(row, selectable=selectable)
        self._layout.addWidget(row)
        return row

    def unregister_row(self, row):
        if row in self._rows:
            self._rows.remove(row)
        if row in self._selectable:
            idx = self._selectable.index(row)
            self._selectable.remove(row)
            if self._selected_index >= idx:
                self._selected_index = max(-1, self._selected_index - 1)

    def finish_layout(self):
        self.adjustSize()
        if self._selected_index == -1 and self._selectable:
            self._select_index(0)

    def _on_row_hover(self, row):
        if row in self._selectable:
            self._select_index(self._selectable.index(row))

    def _select_index(self, idx):
        if not self._selectable:
            return
        idx = max(0, min(len(self._selectable) - 1, idx))
        if idx == self._selected_index:
            return
        if 0 <= self._selected_index < len(self._selectable):
            self._selectable[self._selected_index].set_heart(False)
        self._selected_index = idx
        self._selectable[idx].set_heart(True)
        if self.audio:
            self.audio.play_menu_move()
        self.selection_changed.emit(self._selectable[idx])

    def leaveEvent(self, event):
        super().leaveEvent(event)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

    # ------------------------------------------------------------ paint
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(QColor(COLOR_BG))
        bw = self.metrics.border_width
        painter.setPen(QPen(QColor(COLOR_BORDER), bw))
        half = bw / 2
        painter.drawRect(QRectF(half, half, self.width() - bw, self.height() - bw))

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Up:
            self._select_index(self._selected_index - 1)
        elif key == Qt.Key.Key_Down:
            self._select_index(self._selected_index + 1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Z):
            if 0 <= self._selected_index < len(self._selectable):
                self._selectable[self._selected_index].activate()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            if 0 <= self._selected_index < len(self._selectable):
                direction = -1 if key == Qt.Key.Key_Left else 1
                self._selectable[self._selected_index].step(direction)
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_X):
            self.close()
        else:
            super().keyPressEvent(event)


# --------------------------------------------------------------------- rows
class MenuRow(QWidget):
    """Base row: reserves a fixed-width cursor gutter on the left (heart
    centered in it, clear margin on either side), content after it.
    Subclasses/builders fill in the content widget(s) via `row_layout`.
    `metrics` (a UiMetrics) drives row height / gutter width / heart size;
    omit it to get the ui_scale=1.0 defaults."""

    def __init__(self, font, metrics: UiMetrics = None):
        super().__init__()
        self.metrics = metrics or DEFAULT_METRICS
        self.setFixedHeight(self.metrics.row_height)
        self._hover_cb = None
        self.activate = lambda: None  # builders override with their handler
        self.step = lambda direction: None  # Left/Right; builders override if they have a value to step

        self.gutter = QWidget()
        self.gutter.setFixedWidth(self.metrics.gutter_width)
        gutter_layout = QHBoxLayout(self.gutter)
        gutter_layout.setContentsMargins(self.metrics.gutter_margin, 0, self.metrics.gutter_margin, 0)
        self.heart = HeartCursor(pixel_scale=self.metrics.heart_scale)
        self.heart.setVisible(False)
        gutter_layout.addWidget(self.heart)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self.gutter)

        self._content = QWidget()
        self.row_layout = QHBoxLayout(self._content)
        self.row_layout.setContentsMargins(0, 0, 0, 0)
        self.row_layout.setSpacing(4)
        outer.addWidget(self._content, 1)

    def set_hover_callback(self, cb):
        self._hover_cb = cb

    def set_heart(self, visible):
        self.heart.setVisible(visible)

    def enterEvent(self, event):
        if self._hover_cb:
            self._hover_cb(self)
        super().enterEvent(event)


def make_separator_row(metrics: UiMetrics = None):
    metrics = metrics or DEFAULT_METRICS
    row = QWidget()
    row.setFixedHeight(metrics.border_width + metrics.separator_gap)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(metrics.gutter_width, 3, 0, 3)
    line = QWidget()
    line.setFixedHeight(max(1, round(2 * metrics.ui_scale)))
    line.setStyleSheet(f"background-color: {COLOR_BORDER};")
    layout.addWidget(line)
    row.set_hover_callback = lambda cb: None
    row.set_heart = lambda visible: None
    row.activate = lambda: None
    return row


def make_toggle_row(label_text, get_value, set_value, font, audio=None, on_change=None, metrics: UiMetrics = None):
    """Full-row-clickable line whose text is yellow when on, gray when off."""
    row = MenuRow(font, metrics=metrics)
    text_label = ClickableLabel(label_text)
    text_label.setFont(font)

    def refresh():
        color = COLOR_SELECTED if get_value() else COLOR_DISABLED
        text_label.setStyleSheet(f"color: {color}; background: transparent;")

    def on_click():
        if audio:
            audio.play_menu_select()
        set_value(not get_value())
        refresh()
        if on_change:
            on_change()

    text_label.clicked.connect(on_click)
    refresh()
    row.row_layout.addWidget(text_label, 1)
    row.activate = on_click
    row._toggle_refresh = refresh
    return row


def make_info_row(label_text, value_text, font, metrics: UiMetrics = None):
    """`label   value`-style read-only row -- no click/hover behavior, just
    aligned display, for panels like the stats panel that only show numbers."""
    row = MenuRow(font, metrics=metrics)
    label = _label(label_text, COLOR_TEXT, font)
    value = _label(value_text, COLOR_SELECTED, font, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    row.row_layout.addWidget(label)
    row.row_layout.addStretch(1)
    row.row_layout.addWidget(value)
    row.activate = lambda: None
    return row


def make_action_row(label_text, on_click, font, audio=None, close_after=None, metrics: UiMetrics = None):
    row = MenuRow(font, metrics=metrics)
    text_label = ClickableLabel(label_text)
    text_label.setFont(font)
    text_label.setStyleSheet(f"color: {COLOR_TEXT}; background: transparent;")

    def handler():
        if audio:
            audio.play_menu_select()
        on_click()
        if close_after:
            close_after()

    text_label.clicked.connect(handler)
    row.row_layout.addWidget(text_label, 1)
    row.activate = handler
    return row


class ValueAdjuster(QWidget):
    """`< value >` control shared by every adjustable row: mouse wheel and
    the arrow labels both step the value, flashing it yellow and reverting
    to white after VALUE_FLASH_MS of inactivity. `on_wheel_step(steps,
    modifiers)` lets callers (e.g. a time-of-day row) interpret notches and
    Shift differently than plain +1/-1; if omitted, each notch/click just
    calls on_prev/on_next once per step."""

    def __init__(self, get_text, on_prev, on_next, font, audio=None, on_wheel_step=None):
        super().__init__()
        self.get_text = get_text
        self.on_prev = on_prev
        self.on_next = on_next
        self.audio = audio
        self.on_wheel_step = on_wheel_step

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._unflash)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.prev_btn = ClickableLabel("<")
        self.prev_btn.setFont(font)
        self.prev_btn.setStyleSheet(f"color: {COLOR_TEXT}; background: transparent;")
        self.value_label = _label(get_text(), COLOR_TEXT, font, Qt.AlignmentFlag.AlignCenter)
        self.next_btn = ClickableLabel(">")
        self.next_btn.setFont(font)
        self.next_btn.setStyleSheet(f"color: {COLOR_TEXT}; background: transparent;")

        self.prev_btn.clicked.connect(lambda: self._step_click(-1))
        self.next_btn.clicked.connect(lambda: self._step_click(1))

        layout.addWidget(self.prev_btn)
        layout.addWidget(self.value_label)
        layout.addWidget(self.next_btn)

    def refresh(self):
        self.value_label.setText(self.get_text())

    def _flash(self):
        self.value_label.setStyleSheet(f"color: {COLOR_SELECTED}; background: transparent;")
        self._flash_timer.start(VALUE_FLASH_MS)

    def _unflash(self):
        self.value_label.setStyleSheet(f"color: {COLOR_TEXT}; background: transparent;")

    def _step_click(self, direction):
        (self.on_prev if direction < 0 else self.on_next)()
        self.refresh()
        self._flash()
        if self.audio:
            self.audio.play_menu_move()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() // 120
        if steps == 0:
            return
        if self.on_wheel_step:
            self.on_wheel_step(steps, event.modifiers())
        else:
            direction = 1 if steps > 0 else -1
            for _ in range(abs(steps)):
                (self.on_next if direction > 0 else self.on_prev)()
        self.refresh()
        self._flash()
        if self.audio:
            self.audio.play_menu_move()
        event.accept()


def make_adjust_row(label_text, get_text, on_prev, on_next, font, audio=None, on_wheel_step=None, metrics: UiMetrics = None):
    """`label   < value >`-style row built on ValueAdjuster."""
    row = MenuRow(font, metrics=metrics)
    label = _label(label_text, COLOR_TEXT, font)
    adjuster = ValueAdjuster(get_text, on_prev, on_next, font, audio=audio, on_wheel_step=on_wheel_step)

    row.row_layout.addWidget(label)
    row.row_layout.addStretch(1)
    row.row_layout.addWidget(adjuster)
    row._adjuster = adjuster
    row.activate = lambda: None
    return row


def make_value_action_row(label_text, get_value_text, on_click, font, audio=None, close_after=None, metrics: UiMetrics = None):
    """`label ... value`-style row (e.g. "音量   50%") where clicking
    anywhere on the row -- label or value -- opens a related picker instead
    of adjusting anything in place. The value half is always drawn in the
    "selected" yellow so it reads as the current setting at a glance."""
    row = MenuRow(font, metrics=metrics)
    label = ClickableLabel(label_text)
    label.setFont(font)
    label.setStyleSheet(f"color: {COLOR_TEXT}; background: transparent;")
    value = ClickableLabel(get_value_text())
    value.setFont(font)
    value.setStyleSheet(f"color: {COLOR_SELECTED}; background: transparent;")

    def handler():
        if audio:
            audio.play_menu_select()
        on_click()
        if close_after:
            close_after()

    label.clicked.connect(handler)
    value.clicked.connect(handler)
    row.row_layout.addWidget(label)
    row.row_layout.addStretch(1)
    row.row_layout.addWidget(value)
    row.activate = handler
    return row


class PixelTimeEdit(QTimeEdit):
    """HH:mm editor styled to match the popup: black background, pixel
    font, no spinner buttons. Built on Qt's own QTimeEdit rather than a
    hand-rolled clone, so typing digits, Left/Right to switch the hour/minute
    section, and per-section mouse-wheel adjustment all come for free and
    already work correctly (wrap-around, validation, etc)."""

    def __init__(self, font, audio=None):
        super().__init__()
        self.audio = audio
        self.setDisplayFormat("HH:mm")
        self.setWrapping(True)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setFont(font)
        self.setFrame(False)
        self.setStyleSheet(
            f"QTimeEdit {{ color: {COLOR_TEXT}; background: transparent; }}"
            f"QTimeEdit:focus {{ color: {COLOR_SELECTED}; }}"
        )

    def wheelEvent(self, event):
        super().wheelEvent(event)
        if self.audio:
            self.audio.play_menu_move()


def make_time_row(label_text, get_hhmm, on_change, font, audio=None, metrics: UiMetrics = None):
    """`label   [HH:mm]`-style row: click a section then type digits or
    scroll the wheel over it -- both routes land on the same QTimeEdit."""
    row = MenuRow(font, metrics=metrics)
    editor = PixelTimeEdit(font, audio=audio)
    h, m = map(int, get_hhmm().split(":"))
    editor.blockSignals(True)
    editor.setTime(QTime(h, m))
    editor.blockSignals(False)
    editor.timeChanged.connect(lambda qt: on_change(f"{qt.hour():02d}:{qt.minute():02d}"))

    if label_text:
        row.row_layout.addWidget(_label(label_text, COLOR_TEXT, font))
    row.row_layout.addStretch(1)
    row.row_layout.addWidget(editor)
    row._time_editor = editor
    row.activate = editor.setFocus
    return row


# ------------------------------------------------------------------ layout
def _clamp_rect(rect, screen):
    x = max(screen.x(), min(rect.x(), screen.x() + screen.width() - rect.width()))
    y = max(screen.y(), min(rect.y(), screen.y() + screen.height() - rect.height()))
    return QRect(x, y, rect.width(), rect.height())


def _bubble_rect(pet_x, pet_y, pet_w, dialogue_box):
    from dialogue_box import GAP_ABOVE_PET

    bw, bh = dialogue_box.width(), dialogue_box.height()
    x = pet_x + (pet_w - bw) // 2
    y = pet_y - bh - GAP_ABOVE_PET
    return QRect(x, y, bw, bh)


def position_avoid_pet_and_bubble(popup, pet_x, pet_y, pet_w, pet_h, dialogue_box, gap=GAP_FROM_PET):
    """Tries beside the pet (right, then left, then below) in turn, taking
    the first candidate that both fits on screen and doesn't overlap the pet
    body or the dialogue bubble's display area. If none fit cleanly, falls
    back to a screen-clamped position nudged clear of the bubble -- covering
    the bubble is the one thing this never allows."""
    popup.finish_layout()
    screen = QApplication.primaryScreen().availableGeometry()
    w, h = popup.width(), popup.height()

    pet_rect = QRect(pet_x, pet_y, pet_w, pet_h)
    bubble_rect = _bubble_rect(pet_x, pet_y, pet_w, dialogue_box)
    forbidden = pet_rect.united(bubble_rect)

    y_centered = pet_y + (pet_h - h) // 2
    candidates = [
        QRect(pet_x + pet_w + gap, y_centered, w, h),  # right
        QRect(pet_x - gap - w, y_centered, w, h),  # left
        QRect(pet_x + (pet_w - w) // 2, pet_y + pet_h + gap, w, h),  # below
    ]

    for rect in candidates:
        clamped = _clamp_rect(rect, screen)
        fits_on_screen = clamped == rect
        if fits_on_screen and not clamped.intersects(forbidden):
            popup.move(clamped.x(), clamped.y())
            return

    fallback = _clamp_rect(candidates[0], screen)
    if fallback.intersects(bubble_rect):
        new_y = min(screen.y() + screen.height() - h, bubble_rect.y() + bubble_rect.height() + gap)
        fallback = QRect(fallback.x(), new_y, w, h)
    popup.move(fallback.x(), fallback.y())
