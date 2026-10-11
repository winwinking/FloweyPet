# -*- coding: utf-8 -*-
"""Pomodoro countdown readout: a small always-on-top-aware badge that sits
directly above the pet's head and follows it around. Rendered with the
DSEG7 seven-segment font (assets/font/), sized so "25:00" spans roughly the
pet's on-screen width. Falls back to the pixel font if DSEG7 isn't present.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from fonts import dseg7_font
from win_zorder import apply_topmost, set_click_through

REFERENCE_TEXT = "25:00"
PROBE_PIXEL_SIZE = 48
PADDING_X = 10
PADDING_Y = 6
GAP_ABOVE_PET = 8
DIGIT_COLOR = QColor(90, 235, 140)
BORDER_COLOR = QColor(255, 255, 255)
BG_COLOR = QColor(0, 0, 0, 220)


def _fit_pixel_size(pet_width_px: int) -> int:
    probe_metrics = QFontMetrics(dseg7_font(PROBE_PIXEL_SIZE))
    probe_width = probe_metrics.horizontalAdvance(REFERENCE_TEXT)
    if probe_width <= 0:
        return PROBE_PIXEL_SIZE
    return max(8, round(PROBE_PIXEL_SIZE * pet_width_px / probe_width))


class PomodoroDisplay(QWidget):
    def __init__(self, pet_width_px: int, always_on_top: bool = True, is_locked=None):
        self._pin = always_on_top
        self._is_locked = is_locked or (lambda: False)  # injected by PetWindow; avoids importing it
        super().__init__(None, self._window_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._text = REFERENCE_TEXT
        self.reconfigure(pet_width_px)

    # ------------------------------------------------------------- sizing
    def reconfigure(self, pet_width_px: int):
        """Recomputes the digit size / box dimensions from the pet's
        current display width. Called from __init__ and again whenever the
        pet's size tier changes."""
        size = _fit_pixel_size(pet_width_px)
        self.font = dseg7_font(size)
        metrics = QFontMetrics(self.font)
        text_w = metrics.horizontalAdvance(REFERENCE_TEXT)
        text_h = metrics.height()
        self.box_width = text_w + PADDING_X * 2
        self.box_height = text_h + PADDING_Y * 2
        self.setFixedSize(self.box_width, self.box_height)

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

    def showEvent(self, event):
        super().showEvent(event)
        set_click_through(self, self._is_locked())

    def set_always_on_top(self, pin: bool):
        if pin == self._pin:
            return
        self._pin = pin
        apply_topmost(self, pin)

    def is_pinned(self):
        return self._pin

    # ------------------------------------------------------------- display
    def set_time(self, minutes: int, seconds: int):
        self._text = f"{minutes:02d}:{seconds:02d}"
        self.update()
        if not self.isVisible():
            self.show()

    def hide_display(self):
        self.hide()

    # ------------------------------------------------------------ placement
    def follow(self, pet_x, pet_y, pet_w):
        x = pet_x + (pet_w - self.width()) // 2
        y = pet_y - self.height() - GAP_ABOVE_PET
        self.move(x, y)

    # ---------------------------------------------------------------- paint
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(BG_COLOR)
        painter.setPen(QPen(BORDER_COLOR, 2))
        painter.drawRoundedRect(1, 1, self.width() - 2, self.height() - 2, 5, 5)

        painter.setFont(self.font)
        painter.setPen(DIGIT_COLOR)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)
