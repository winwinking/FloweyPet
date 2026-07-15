# -*- coding: utf-8 -*-
"""Undertale-styled size tier picker, opened from the pet's right-click
menu's 大小 row (see pet_menu.py's "value_action" row + PetWindow.
_open_size_panel). Structurally a clone of volume_panel.py's VolumePanel --
same black/white bordered, heart-cursor list, click-to-apply-and-close
interaction -- except the tier list comes from config["pet_size_tiers"]
(multipliers, not percentages) instead of the fixed VOLUME_TIERS.
"""
from PyQt6.QtCore import Qt

from undertale_ui import (
    COLOR_SELECTED,
    COLOR_TEXT,
    ClickableLabel,
    MenuRow,
    UndertalePopup,
    _label,
    fit_panel_width,
)

PANEL_WIDTH_BASE = 140


class SizePanel(UndertalePopup):
    """`get_value`/`set_value` mirror VolumePanel's plain-callable shape:
    get_value() returns the current pet_scale multiplier, set_value(scale)
    applies + persists it (see PetWindow._apply_pet_scale)."""

    def __init__(self, get_value, set_value, tiers, audio=None, always_on_top=True, ui_scale=1.0, min_font_px=None, parent=None):
        super().__init__(audio=audio, always_on_top=always_on_top, parent=parent, ui_scale=ui_scale, min_font_px=min_font_px)
        self._set_value = set_value

        title = _label("大小", COLOR_TEXT, self.font, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self._layout.addWidget(title)
        self._layout.addSpacing(4)

        current = get_value()
        nearest = min(range(len(tiers)), key=lambda i: abs(tiers[i] - current))
        # tiers is an editable config list (pet_size_tiers) -- a hand-edited
        # tier like "1000%" shouldn't clip just because the panel assumed a
        # fixed 3-digit-percent width, so measure the real strings instead.
        row_specs = [(f"{round(scale * 100)}%",) for scale in tiers]
        self.setFixedWidth(fit_panel_width(self.metrics, self.font, row_specs, base_width=PANEL_WIDTH_BASE))
        for scale in tiers:
            row = MenuRow(self.font, metrics=self.metrics)
            label = ClickableLabel(f"{round(scale * 100)}%")
            label.setFont(self.font)
            color = COLOR_SELECTED if scale == tiers[nearest] else COLOR_TEXT
            label.setStyleSheet(f"color: {color}; background: transparent;")
            label.clicked.connect(lambda s=scale: self._choose(s))
            row.row_layout.addWidget(label)
            row.activate = lambda s=scale: self._choose(s)
            self.add_row(row)

        self.finish_layout()
        # Land the heart on the tier already in effect instead of
        # finish_layout's usual default of the top of the list.
        if nearest != self._selected_index and 0 <= nearest < len(self._selectable):
            self._selectable[self._selected_index].set_heart(False)
            self._selected_index = nearest
            self._selectable[nearest].set_heart(True)

    def _choose(self, scale):
        self._set_value(scale)
        if self.audio:
            self.audio.play_menu_select()
        self.close()
