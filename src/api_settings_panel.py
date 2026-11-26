# -*- coding: utf-8 -*-
"""存档点 -> API 设置: a small window with three text lines (address, model,
key) and [保存] [删除钥匙] [取消].

Built like ChatBox, not like the menu panels: NOT a Qt.Popup (a Popup closes
on any outside click, and clicking an IME candidate window can count as
"outside"), it takes keyboard focus, and it stays open until saved,
cancelled, × or Esc.

The panel itself neither touches the disk nor the network and never judges
whether the key is right. It validates the address, then reports through
signals; PetWindow does the saving. Nothing typed here -- the key above all --
is ever logged.
"""
import urllib.parse

from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPalette, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from api_chat import DEFAULT_BASE_URL, DEFAULT_MODEL
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

TITLE = "API 设置"
ERR_NOT_HTTPS = "地址必须以 https:// 开头"
ERR_NO_HOST = "地址不完整"
ERROR_COLOR = "#ff4040"
HINT_COLOR = "#8a8a8a"
PLACEHOLDER_KEY_SAVED = "已保存（留空则不改）"
PLACEHOLDER_KEY_NONE = "粘贴你的 API key"
MAX_FIELD_CHARS = 300


class _ClickLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        event.accept()


class _FieldEdit(QLineEdit):
    escape_pressed = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.escape_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ApiSettingsPanel(QWidget):
    saved = pyqtSignal(str, str, str)  # base_url, model, key ("" = leave the stored key alone)
    key_deleted = pyqtSignal()
    closed = pyqtSignal()

    def __init__(
        self,
        base_url,
        model,
        has_key,
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
        self.has_key = bool(has_key)

        self._title = QLabel(TITLE)
        self._title.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self._close_label = _ClickLabel("×")
        self._close_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_label.clicked.connect(self.close)
        self._title_spacer = QLabel("")

        self._url_edit = self._make_edit()
        self._url_edit.setText(base_url if isinstance(base_url, str) else "")
        self._model_edit = self._make_edit()
        self._model_edit.setText(model if isinstance(model, str) else "")
        self._key_edit = self._make_edit()
        self._key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_edit.setPlaceholderText(PLACEHOLDER_KEY_SAVED if self.has_key else PLACEHOLDER_KEY_NONE)
        self._url_edit.setMaxLength(MAX_FIELD_CHARS)
        self._model_edit.setMaxLength(MAX_FIELD_CHARS)
        self._key_edit.setMaxLength(MAX_FIELD_CHARS)
        self.setTabOrder(self._url_edit, self._model_edit)
        self.setTabOrder(self._model_edit, self._key_edit)

        self._row_labels = [QLabel("地址"), QLabel("模型"), QLabel("钥匙")]
        self._error = QLabel("")
        self._error.setWordWrap(False)

        self._save_label = _ClickLabel("[保存]")
        self._delete_label = _ClickLabel("[删除钥匙]")
        self._cancel_label = _ClickLabel("[取消]")
        for lab in (self._save_label, self._delete_label, self._cancel_label):
            lab.setCursor(Qt.CursorShape.PointingHandCursor)
        self._save_label.clicked.connect(self._save)
        self._delete_label.clicked.connect(self._delete_key)
        self._cancel_label.clicked.connect(self.close)
        self._delete_label.setVisible(self.has_key)

        for edit in (self._url_edit, self._model_edit, self._key_edit):
            edit.returnPressed.connect(self._save)
            edit.escape_pressed.connect(self.close)
            edit.textEdited.connect(self._clear_error)

        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(0)
        title_row.addWidget(self._title_spacer)
        title_row.addWidget(self._title, 1)
        title_row.addWidget(self._close_label)
        self._layout.addLayout(title_row)
        for lab, edit in zip(self._row_labels, (self._url_edit, self._model_edit, self._key_edit)):
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(lab)
            row.addWidget(edit, 1)
            self._layout.addLayout(row)
        self._layout.addWidget(self._error)
        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.addWidget(self._save_label)
        actions.addWidget(self._delete_label)
        actions.addWidget(self._cancel_label)
        actions.addStretch(1)
        self._layout.addLayout(actions)

        self.reconfigure(pet_width_px, font_scale, pet_scale, min_font_px)

    def _make_edit(self):
        edit = _FieldEdit(self)
        edit.setFrame(False)
        return edit

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
        """Public hook for TopmostKeeper, mirrors ChatBox.is_pinned()."""
        return self._pin

    # --------------------------------------------------------------- sizing
    def reconfigure(self, pet_width_px: int, font_scale: float, pet_scale: float = 1.0, min_font_px: int = None):
        """Same width/font/padding/border numbers as ChatBox and
        DialogueBox.reconfigure; the height follows the content."""
        self.box_width = round(pet_width_px * WIDTH_MULTIPLIER)
        font_px = round(BASE_FONT_PX * font_scale * pet_scale)
        if min_font_px is not None:
            font_px = max(font_px, min_font_px)
        self.font = pixel_font(font_px)
        self._metrics = QFontMetrics(self.font)
        self._padding = max(1, round(PADDING_BASE * pet_scale))
        self._border_width = max(1, round(BORDER_WIDTH_BASE * pet_scale))
        line_h = self._metrics.height() + LINE_SPACING_EXTRA

        self._layout.setContentsMargins(self._padding, self._padding, self._padding, self._padding)
        label_w = max(self._metrics.horizontalAdvance(lab.text()) for lab in self._row_labels) + 4
        plain = f"QLabel {{ color: {COLOR_TEXT}; background: transparent; }}"
        for w in (self._title, self._title_spacer, *self._row_labels):
            w.setFont(self.font)
            w.setStyleSheet(plain)
        for lab in self._row_labels:
            lab.setFixedWidth(label_w)
        self._close_label.setFont(self.font)
        self._close_label.setStyleSheet(plain + f"QLabel:hover {{ color: {COLOR_SELECTED}; }}")
        self._title_spacer.setFixedWidth(self._metrics.horizontalAdvance(self._close_label.text()))
        for lab in (self._save_label, self._delete_label, self._cancel_label):
            lab.setFont(self.font)
            lab.setStyleSheet(plain + f"QLabel:hover {{ color: {COLOR_SELECTED}; }}")
        self._error.setFont(self.font)
        self._error.setStyleSheet(f"QLabel {{ color: {ERROR_COLOR}; background: transparent; }}")
        self._error.setFixedHeight(line_h)
        self._title.setFixedHeight(line_h)
        self._close_label.setFixedHeight(line_h)

        edit_css = (
            f"QLineEdit {{ color: {COLOR_TEXT}; background: #101010; border: 1px solid #707070;"
            f" padding: 0px 3px; selection-background-color: #5a5a5a; selection-color: {COLOR_TEXT}; }}"
            f"QLineEdit:focus {{ border: 1px solid {COLOR_SELECTED}; }}"
        )
        for edit in (self._url_edit, self._model_edit, self._key_edit):
            edit.setFont(self.font)
            edit.setFixedHeight(line_h)
            edit.setStyleSheet(edit_css)
            palette = edit.palette()
            palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(HINT_COLOR))
            edit.setPalette(palette)

        self.setFixedWidth(self.box_width)
        self._layout.activate()
        self.setFixedHeight(self._layout.sizeHint().height())
        self.box_height = self.height()

    def finish_layout(self):
        """Lets PetWindow._place_popup treat this like any other popup."""

    # ------------------------------------------------------------ lifecycle
    def open_and_focus(self):
        """Show and take keyboard focus on the first empty line (normally
        the key line). Only for a deliberate user click."""
        self.show()
        self.raise_()
        self.activateWindow()
        target = self._url_edit
        for edit in (self._url_edit, self._model_edit, self._key_edit):
            if not edit.text():
                target = edit
                break
        target.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def closeEvent(self, event):
        self._key_edit.clear()  # never leave the key sitting in a hidden widget
        super().closeEvent(event)
        if event.isAccepted():
            self.closed.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    # -------------------------------------------------------------- actions
    def _clear_error(self, *_):
        if self._error.text():
            self._error.setText("")

    def error_text(self):
        return self._error.text()

    def _save(self):
        url = self._url_edit.text().strip() or DEFAULT_BASE_URL
        model = self._model_edit.text().strip() or DEFAULT_MODEL
        if not url.lower().startswith("https://"):
            self._error.setText(ERR_NOT_HTTPS)
            return
        try:
            host = urllib.parse.urlsplit(url).hostname
        except ValueError:
            host = None
        if not host:
            self._error.setText(ERR_NO_HOST)
            return
        key = self._key_edit.text().strip()
        self._key_edit.clear()
        self.saved.emit(url, model, key)
        self.close()

    def _delete_key(self):
        self.key_deleted.emit()
        self.close()

    # ---------------------------------------------------------------- paint
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setBrush(QColor(0, 0, 0, 235))
        painter.setPen(QPen(QColor(255, 255, 255), self._border_width))
        half = self._border_width / 2
        painter.drawRect(QRectF(half, half, self.width() - self._border_width, self.height() - self._border_width))
