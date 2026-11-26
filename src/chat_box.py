# -*- coding: utf-8 -*-
"""Chat input box (行动 -> 聊天): a small frameless window with one text
line and an × button, styled like the dialogue bubble (black fill, white
border, pixel font, same width, same size algorithm as
DialogueBox.reconfigure).

Deliberately NOT an UndertalePopup / Qt.Popup: a Popup closes on any click
outside it (a click on an IME candidate window may count as "outside"), and
this box has to stay open until the user clicks × or presses Esc. It also
must be able to take keyboard focus, so -- unlike DialogueBox -- it carries
neither WindowDoesNotAcceptFocus nor WA_ShowWithoutActivating. The user
opened it on purpose, so grabbing focus when it opens is fine.

This module knows nothing about PetWindow; PetWindow owns placement,
follow-the-pet, and auto-close.
"""
from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QWidget

from dialogue_box import (
    BASE_FONT_PX,
    BORDER_WIDTH_BASE,
    LINE_SPACING_EXTRA,
    PADDING_BASE,
    WIDTH_MULTIPLIER,
)
from fonts import pixel_font
from undertale_ui import COLOR_SELECTED, COLOR_TEXT
from win_zorder import apply_topmost

MAX_INPUT_CHARS = 200
PLACEHOLDER_TEXT = "说点什么……"
PLACEHOLDER_COLOR = "#8a8a8a"
CLOSE_TEXT = "×"


class _CloseLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        event.accept()


class _ChatLineEdit(QLineEdit):
    escape_pressed = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ChatBox(QWidget):
    submitted = pyqtSignal(str)  # stripped, non-empty text the user entered
    closed = pyqtSignal()

    def __init__(
        self,
        pet_width_px: int,
        font_scale: float,
        always_on_top: bool = True,
        pet_scale: float = 1.0,
        min_font_px: int = None,
    ):
        self._pin = always_on_top
        super().__init__(None, self._window_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._edit = _ChatLineEdit(self)
        self._edit.setMaxLength(MAX_INPUT_CHARS)
        self._edit.setPlaceholderText(PLACEHOLDER_TEXT)
        self._edit.setFrame(False)
        self._edit.returnPressed.connect(self._on_return)
        self._edit.escape_pressed.connect(self.close)

        self._close_label = _CloseLabel(CLOSE_TEXT, self)
        self._close_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_label.clicked.connect(self.close)

        self._layout = QHBoxLayout(self)
        self._layout.setSpacing(6)
        self._layout.addWidget(self._edit, 1)
        self._layout.addWidget(self._close_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.reconfigure(pet_width_px, font_scale, pet_scale, min_font_px)

    # --------------------------------------------------------------- window
    def _window_flags(self):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self._pin:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def set_always_on_top(self, pin: bool):
        if pin == self._pin:
            return
        self._pin = pin
        apply_topmost(self, pin)

    def is_pinned(self):
        """Public hook for TopmostKeeper, mirrors DialogueBox.is_pinned()."""
        return self._pin

    # --------------------------------------------------------------- sizing
    def reconfigure(self, pet_width_px: int, font_scale: float, pet_scale: float = 1.0, min_font_px: int = None):
        """Same numbers as DialogueBox.reconfigure (width, font, padding,
        border); the height is one text line instead of three."""
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
        self.box_height = self._padding * 2 + line_h

        self._layout.setContentsMargins(self._padding, self._padding, self._padding, self._padding)
        self._edit.setFont(self.font)
        self._edit.setFixedHeight(line_h)
        self._edit.setTextMargins(0, 0, 0, 0)
        self._edit.setStyleSheet(
            f"QLineEdit {{ color: {COLOR_TEXT}; background: transparent; border: none; padding: 0px;"
            f" selection-background-color: #5a5a5a; selection-color: {COLOR_TEXT}; }}"
        )
        palette = self._edit.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(PLACEHOLDER_COLOR))
        self._edit.setPalette(palette)
        self._close_label.setFont(self.font)
        self._close_label.setStyleSheet(
            f"QLabel {{ color: {COLOR_TEXT}; background: transparent; }}"
            f"QLabel:hover {{ color: {COLOR_SELECTED}; }}"
        )
        self.setFixedSize(self.box_width, self.box_height)

    # ------------------------------------------------------------ lifecycle
    def open_and_focus(self):
        """Show and take keyboard focus. Only call this for a deliberate
        user action (menu click) -- it steals focus from whatever app the
        user was in."""
        self.show()
        self.raise_()
        self.activateWindow()
        self._edit.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def closeEvent(self, event):
        super().closeEvent(event)
        if event.isAccepted():
            self.closed.emit()

    def _on_return(self):
        text = self._edit.text().strip()
        if not text:
            return
        self._edit.clear()
        self.submitted.emit(text)
        self._edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    # ---------------------------------------------------------------- paint
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(QColor(0, 0, 0, 235))
        painter.setPen(QPen(QColor(255, 255, 255), self._border_width))
        half = self._border_width / 2
        painter.drawRect(QRectF(half, half, self.width() - self._border_width, self.height() - self._border_width))
