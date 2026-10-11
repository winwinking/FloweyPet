# -*- coding: utf-8 -*-
"""Undertale-style right-click menu for the pet sprite itself (the system
tray's context menu is a different surface and keeps its native QMenu --
Windows renders that one itself, outside our control).

The menu is defined as a plain list of dicts (`_build_menu_spec`) so adding
a future item -- a minigame, say -- is a one-line addition here, not new
widget plumbing.
"""
from PyQt6.QtCore import QTimer

from undertale_ui import (
    UndertalePopup,
    make_action_row,
    make_separator_row,
    make_toggle_row,
    make_value_action_row,
)


def _build_menu_spec(pet):
    return [
        {
            "type": "toggle",
            "label": "置顶",
            "get": lambda: pet.config.get("always_on_top", True),
            "set": pet._toggle_always_on_top,
        },
        {
            "type": "toggle",
            "label": "隐身",
            "get": lambda: not pet.isVisible(),
            "set": lambda _v: pet._toggle_visible(),
        },
        {
            "type": "toggle",
            "label": "锁定",
            "get": lambda: pet._locked,
            "set": pet._toggle_lock,
            # Unlike the other toggles (which leave the menu open so several
            # can be flipped in a row), locking makes the pet click-through,
            # so the menu must go away -- whether the lock was applied,
            # refused by the arbiter's gates, or this was an unlock.
            "close_after": True,
        },
        {
            "type": "toggle",
            "label": "静音",
            "get": lambda: pet.config.get("muted", False),
            "set": pet._toggle_mute,
        },
        {
            "type": "toggle",
            "label": "界面音效",
            "get": lambda: pet.config.get("menu_sfx_enabled", True),
            "set": pet._toggle_menu_sfx,
        },
        {"type": "action", "label": "番茄钟", "action": pet._toggle_pomodoro_from_menu},
        {"type": "action", "label": "提醒设置", "action": pet._open_reminder_settings},
        {"type": "action", "label": "记录", "action": pet._open_stats_panel},
        {
            "type": "value_action",
            "label": "音量",
            "get_value": lambda: f"{round(pet.config.get('volume', 0.8) * 100)}%",
            "action": pet._open_volume_panel,
        },
        {
            "type": "value_action",
            "label": "大小",
            "get_value": lambda: f"{round(pet.pet_scale * 100)}%",
            "action": pet._open_size_panel,
        },
        {
            "type": "toggle",
            "label": "开机自启",
            "get": lambda: pet.config.get("autostart", False),
            "set": pet._toggle_autostart,
        },
        {"type": "action", "label": "调情", "action": pet._on_flirt_clicked, "key": "flirt"},
        {"type": "action", "label": "查看信息", "action": pet._view_info},
        {"type": "separator"},
        {"type": "action", "label": "离开", "action": pet.request_quit},
    ]


class PetContextMenu(UndertalePopup):
    def __init__(self, pet, parent=None, ui_scale=1.0, min_font_px=None):
        super().__init__(
            audio=pet.audio,
            always_on_top=pet._always_on_top,
            parent=parent,
            ui_scale=ui_scale,
            min_font_px=min_font_px,
        )
        self.flirt_row = None
        for spec in _build_menu_spec(pet):
            kind = spec["type"]
            if kind == "separator":
                self.add_row(make_separator_row(metrics=self.metrics), selectable=False)
            elif kind == "toggle":
                # Deferred with singleShot(0) so the menu closes after this
                # click handler has returned, never from inside it. `pet`'s
                # method is looked up lazily at that moment.
                on_change = (
                    (lambda: QTimer.singleShot(0, lambda: pet._close_active_popup()))
                    if spec.get("close_after")
                    else None
                )
                self.add_row(
                    make_toggle_row(
                        spec["label"],
                        spec["get"],
                        spec["set"],
                        self.font,
                        audio=pet.audio,
                        on_change=on_change,
                        metrics=self.metrics,
                    )
                )
            elif kind == "value_action":
                self.add_row(
                    make_value_action_row(
                        spec["label"],
                        spec["get_value"],
                        spec["action"],
                        self.font,
                        audio=pet.audio,
                        close_after=self.close,
                        metrics=self.metrics,
                    )
                )
            elif kind == "action":
                row = self.add_row(
                    make_action_row(
                        spec["label"],
                        spec["action"],
                        self.font,
                        audio=pet.audio,
                        close_after=self.close,
                        metrics=self.metrics,
                    )
                )
                if spec.get("key") == "flirt":
                    self.flirt_row = row
                    # The heart-selection system only reports a *new*
                    # selection (enterEvent on some other row) -- it never
                    # reports "the cursor left 调情 into blank/separator
                    # space, or off the popup entirely". Without this, the
                    # hover-hesitate state machine never learns the cursor
                    # moved away unless it happens to land on another row.
                    row.on_leave = lambda: pet.flirt.on_menu_selection_changed(False)
        self.finish_layout()
