# -*- coding: utf-8 -*-
"""Undertale-styled reminder settings panel, opened from the pet's
right-click menu. Same black/white bordered, pixel-font, no-image look as
the context menu (see undertale_ui.py for the shared kit).

Pure UI-layer rewrite: every read/write still goes straight through the
existing Reminders object (get_water_interval/set_water_interval,
get_times/set_times, is_enabled/set_enabled), so nothing about how
reminders are scheduled or persisted changes here.
"""
from PyQt6.QtCore import QTime, QTimer
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from undertale_ui import (
    COLOR_DISABLED,
    COLOR_SELECTED,
    COLOR_TEXT,
    TIME_STEP_MINUTES,
    WATER_INTERVAL_CHOICES,
    ClickableLabel,
    MenuRow,
    PixelTimeEdit,
    UndertalePopup,
    _label,
    fit_panel_width,
    make_adjust_row,
    make_separator_row,
    make_time_row,
)

DEFAULT_NEW_MEAL_TIME = "12:00"


def _shift_time(hhmm, delta_minutes):
    h, m = map(int, hhmm.split(":"))
    total = (h * 60 + m + delta_minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _step_choice(choices, current, delta):
    try:
        idx = choices.index(current)
    except ValueError:
        idx = min(range(len(choices)), key=lambda i: abs(choices[i] - current))
    idx = (idx + delta) % len(choices)
    return choices[idx]


class _HoverButton(ClickableLabel):
    """确定/取消-style button: white text, turns yellow on hover."""

    def __init__(self, text, font):
        super().__init__(text)
        self.setFont(font)
        self.setStyleSheet(f"color: {COLOR_TEXT}; background: transparent;")

    def enterEvent(self, event):
        self.setStyleSheet(f"color: {COLOR_SELECTED}; background: transparent;")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(f"color: {COLOR_TEXT}; background: transparent;")
        super().leaveEvent(event)


def _make_switch_row(label_text, get_value, set_value, font, audio, on_change=None, metrics=None):
    """Reminder-panel row style: static white label, "[开]"/"[关]" switch at
    the line's end (yellow when on, gray when off) -- distinct from the
    context menu's whole-line-colored toggle rows."""
    row = MenuRow(font, metrics=metrics)
    label = _label(label_text, COLOR_TEXT, font)
    switch = ClickableLabel()
    switch.setFont(font)

    def refresh():
        on = get_value()
        switch.setText("[开]" if on else "[关]")
        switch.setStyleSheet(
            f"color: {COLOR_SELECTED if on else COLOR_DISABLED}; background: transparent;"
        )

    def on_click():
        if audio:
            audio.play_menu_select()
        set_value(not get_value())
        refresh()
        if on_change:
            on_change()

    switch.clicked.connect(on_click)
    refresh()
    row.row_layout.addWidget(label, 1)
    row.row_layout.addWidget(switch)
    row.activate = on_click
    return row


class ReminderPanel(UndertalePopup):
    def __init__(self, reminders, audio=None, always_on_top=True, ui_scale=1.0, min_font_px=None, parent=None):
        super().__init__(audio=audio, always_on_top=always_on_top, parent=parent, ui_scale=ui_scale, min_font_px=min_font_px)
        self.reminders = reminders

        # Same shared measurement tool as the stats panel: guards the
        # switch rows ("喝水提醒"/"吃饭提醒"/"睡觉提醒" + "[开]"/"[关]") and
        # the water-interval adjuster row against clipping at any ui_scale.
        row_specs = [
            ("喝水提醒", "[关]"),
            ("", f"{max(WATER_INTERVAL_CHOICES)}分钟", "<", ">"),
            ("吃饭提醒", "[关]"),
            ("睡觉提醒", "[关]"),
        ]
        self.setFixedWidth(fit_panel_width(self.metrics, self.font, row_specs))

        self.add_row(
            _make_switch_row(
                "喝水提醒",
                lambda: self.reminders.is_enabled("water"),
                lambda v: self.reminders.set_enabled("water", v),
                self.font,
                audio,
                metrics=self.metrics,
            )
        )
        self.add_row(
            make_adjust_row(
                "",
                lambda: f"{self.reminders.get_water_interval()}分钟",
                self._water_prev,
                self._water_next,
                self.font,
                audio=audio,
                metrics=self.metrics,
            )
        )
        self.add_row(make_separator_row(metrics=self.metrics), selectable=False)

        self.add_row(
            _make_switch_row(
                "吃饭提醒",
                lambda: self.reminders.is_enabled("meal"),
                lambda v: self.reminders.set_enabled("meal", v),
                self.font,
                audio,
                metrics=self.metrics,
            )
        )
        self._meal_section = QWidget()
        self._meal_layout = QVBoxLayout(self._meal_section)
        self._meal_layout.setContentsMargins(0, 0, 0, 0)
        self._meal_layout.setSpacing(2)
        self._layout.addWidget(self._meal_section)
        self._rebuild_meal_rows()
        self.add_row(make_separator_row(metrics=self.metrics), selectable=False)

        self.add_row(
            _make_switch_row(
                "睡觉提醒",
                lambda: self.reminders.is_enabled("sleep"),
                lambda v: self.reminders.set_enabled("sleep", v),
                self.font,
                audio,
                metrics=self.metrics,
            )
        )
        self.add_row(
            make_time_row(
                "",
                self._sleep_time_text,
                lambda hhmm: self.reminders.set_times("sleep", [hhmm]),
                self.font,
                audio=audio,
                metrics=self.metrics,
            )
        )
        self.add_row(make_separator_row(metrics=self.metrics), selectable=False)
        self.add_row(self._build_button_row())

        self.finish_layout()

    # --------------------------------------------------------------- water
    def _water_prev(self):
        cur = self.reminders.get_water_interval()
        self.reminders.set_water_interval(_step_choice(WATER_INTERVAL_CHOICES, cur, -1))

    def _water_next(self):
        cur = self.reminders.get_water_interval()
        self.reminders.set_water_interval(_step_choice(WATER_INTERVAL_CHOICES, cur, 1))

    # ---------------------------------------------------------------- meal
    def _rebuild_meal_rows(self):
        while self._meal_layout.count():
            item = self._meal_layout.takeAt(0)
            widget = item.widget()
            if widget:
                self.unregister_row(widget)
                widget.deleteLater()

        for hhmm in sorted(self.reminders.get_times("meal")):
            row = self._build_meal_time_row(hhmm)
            self.register_row(row)
            self._meal_layout.addWidget(row)

        add_row = MenuRow(self.font, metrics=self.metrics)
        add_label = ClickableLabel("+ 添加时刻")
        add_label.setFont(self.font)
        add_label.setStyleSheet(f"color: {COLOR_TEXT}; background: transparent;")
        add_label.clicked.connect(self._add_meal_time)
        add_row.row_layout.addWidget(add_label, 1)
        add_row.activate = self._add_meal_time
        self.register_row(add_row)
        self._meal_layout.addWidget(add_row)

        self.finish_layout()

    def _build_meal_time_row(self, hhmm):
        row = MenuRow(self.font, metrics=self.metrics)

        def commit(new_hhmm):
            times = [t for t in self.reminders.get_times("meal") if t != hhmm]
            if new_hhmm not in times:
                times.append(new_hhmm)
            self.reminders.set_times("meal", times)
            QTimer.singleShot(0, self._rebuild_meal_rows)

        editor = PixelTimeEdit(self.font, audio=self.audio)
        h, m = map(int, hhmm.split(":"))
        editor.blockSignals(True)
        editor.setTime(QTime(h, m))
        editor.blockSignals(False)
        editor.timeChanged.connect(lambda qt: commit(f"{qt.hour():02d}:{qt.minute():02d}"))

        remove_btn = ClickableLabel("×")
        remove_btn.setFont(self.font)
        remove_btn.setStyleSheet(f"color: {COLOR_DISABLED}; background: transparent;")
        remove_btn.clicked.connect(lambda: self._remove_meal_time(hhmm))

        row.row_layout.addStretch(1)
        row.row_layout.addWidget(editor)
        row.row_layout.addWidget(remove_btn)
        row.activate = lambda: self._remove_meal_time(hhmm)
        return row

    def _remove_meal_time(self, hhmm):
        times = [t for t in self.reminders.get_times("meal") if t != hhmm]
        self.reminders.set_times("meal", times)
        QTimer.singleShot(0, self._rebuild_meal_rows)

    def _add_meal_time(self):
        times = self.reminders.get_times("meal")
        candidate = DEFAULT_NEW_MEAL_TIME
        offset = 0
        while candidate in times:
            offset += TIME_STEP_MINUTES
            candidate = _shift_time(DEFAULT_NEW_MEAL_TIME, offset)
        times.append(candidate)
        self.reminders.set_times("meal", times)
        QTimer.singleShot(0, self._rebuild_meal_rows)

    # --------------------------------------------------------------- sleep
    def _sleep_time_text(self):
        times = self.reminders.get_times("sleep")
        return times[0] if times else "23:00"

    # -------------------------------------------------------------- buttons
    def _confirm_and_close(self):
        if self.audio:
            self.audio.play_menu_select()
        self.close()

    def _build_button_row(self):
        row = MenuRow(self.font, metrics=self.metrics)
        ok_btn = _HoverButton("确定", self.font)
        cancel_btn = _HoverButton("取消", self.font)
        ok_btn.clicked.connect(self._confirm_and_close)
        cancel_btn.clicked.connect(self._confirm_and_close)
        row.row_layout.addStretch(1)
        row.row_layout.addWidget(ok_btn)
        row.row_layout.addSpacing(16)
        row.row_layout.addWidget(cancel_btn)
        row.row_layout.addStretch(1)
        row.activate = self._confirm_and_close
        return row
