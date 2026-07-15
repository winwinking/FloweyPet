# -*- coding: utf-8 -*-
"""Undertale-styled volume tier picker, opened from the pet's right-click
menu's 音量 row (see pet_menu.py's "value_action" row + PetWindow.
_open_volume_panel). Replaces the old inline horizontal tier row that used
to live directly inside the context menu -- picking a tier here applies +
persists the volume immediately, plays one menu_select.wav at the *new*
volume as an audition, then closes itself.
"""
from PyQt6.QtCore import Qt

from undertale_ui import (
    COLOR_SELECTED,
    COLOR_TEXT,
    VOLUME_TIERS,
    ClickableLabel,
    MenuRow,
    UndertalePopup,
    _label,
    fit_panel_width,
)

PANEL_WIDTH_BASE = 140


class VolumePanel(UndertalePopup):
    """`get_value`/`set_value` mirror the plain int-percent callables the
    pet's context menu already used for volume, so this panel doesn't need
    to know anything about config/audio plumbing itself."""

    def __init__(self, get_value, set_value, audio=None, always_on_top=True, ui_scale=1.0, min_font_px=None, parent=None):
        super().__init__(audio=audio, always_on_top=always_on_top, parent=parent, ui_scale=ui_scale, min_font_px=min_font_px)
        # Same shared measurement tool as the stats panel -- currently the
        # fixed VOLUME_TIERS are all short (15-100%) so this rarely changes
        # anything, but it guards against the row ever clipping instead of
        # each panel hand-rolling its own width guess.
        row_specs = [(f"{pct}%",) for pct in VOLUME_TIERS]
        self.setFixedWidth(fit_panel_width(self.metrics, self.font, row_specs, base_width=PANEL_WIDTH_BASE))
        self._set_value = set_value

        title = _label("音量", COLOR_TEXT, self.font, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self._layout.addWidget(title)
        self._layout.addSpacing(4)

        current = get_value()
        nearest = min(range(len(VOLUME_TIERS)), key=lambda i: abs(VOLUME_TIERS[i] - current))
        for pct in VOLUME_TIERS:
            row = MenuRow(self.font, metrics=self.metrics)
            label = ClickableLabel(f"{pct}%")
            label.setFont(self.font)
            color = COLOR_SELECTED if pct == VOLUME_TIERS[nearest] else COLOR_TEXT
            label.setStyleSheet(f"color: {color}; background: transparent;")
            label.clicked.connect(lambda p=pct: self._choose(p))
            row.row_layout.addWidget(label)
            row.activate = lambda p=pct: self._choose(p)
            self.add_row(row)

        self.finish_layout()
        # Land the heart on the tier already in effect instead of
        # finish_layout's usual default of the top of the list.
        if nearest != self._selected_index and 0 <= nearest < len(self._selectable):
            self._selectable[self._selected_index].set_heart(False)
            self._selected_index = nearest
            self._selectable[nearest].set_heart(True)

    def _choose(self, pct):
        self._set_value(pct)
        if self.audio:
            self.audio.play_menu_select()
        self.close()
