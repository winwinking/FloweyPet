# -*- coding: utf-8 -*-
"""Undertale-style right-click menu for the pet sprite itself (the system
tray's context menu is a different surface and keeps its native QMenu --
Windows renders that one itself, outside our control).

Two layers, Undertale battle-menu style. The main menu (PetContextMenu) has
a few direct items plus category rows ("行动" ...); clicking a category
closes the main menu and opens that category's small box (CategoryPopup) --
still only ONE popup at a time (PetWindow._active_popup). The box's first
row is "< 返回" (also Esc), which reopens the main menu.

Everything is described as plain lists of dicts (`_build_menu_spec`); a
category is {"type": "category", "key": ..., "label": ..., "items": [...]}
holding a nested list of the same kind of dicts, so adding a future item is
a one-line addition here, not new widget plumbing.
"""
from PyQt6.QtCore import QTimer, Qt

from undertale_ui import (
    UndertalePopup,
    make_action_row,
    make_separator_row,
    make_toggle_row,
    make_value_action_row,
)

BACK_LABEL = "< 返回"


def _build_menu_spec(pet):
    return [
        {
            "type": "toggle",
            "label": "锁定",
            "get": lambda: pet._locked,
            "set": pet._toggle_lock,
            # Unlike the other toggles (which leave their menu open so
            # several can be flipped in a row), locking makes the pet
            # click-through, so the menu must go away -- whether the lock
            # was applied, refused by the arbiter's gates, or this was an
            # unlock.
            "close_after": True,
        },
        {"type": "action", "label": "战斗", "action": pet._on_fight_clicked},
        {
            "type": "category",
            "key": "act",
            "label": "行动",
            "items": [
                {"type": "action", "label": "查看信息", "action": pet._view_info},
                {"type": "action", "label": "调情", "action": pet._on_flirt_clicked, "key": "flirt"},
            ],
        },
        {
            "type": "category",
            "key": "item",
            "label": "物品",
            "items": [
                {"type": "action", "label": "番茄钟", "action": pet._toggle_pomodoro_from_menu},
                {"type": "action", "label": "提醒设置", "action": pet._open_reminder_settings},
                {"type": "action", "label": "记录", "action": pet._open_stats_panel},
            ],
        },
        {
            "type": "category",
            "key": "save",
            "label": "存档点",
            "items": [
                {
                    "type": "toggle",
                    "label": "置顶",
                    "get": lambda: pet.config.get("always_on_top", True),
                    "set": pet._toggle_always_on_top,
                },
                {
                    "type": "value_action",
                    "label": "大小",
                    "get_value": lambda: f"{round(pet.pet_scale * 100)}%",
                    "action": pet._open_size_panel,
                },
                {
                    "type": "toggle",
                    "label": "待机动作",
                    "get": lambda: pet.config.get("idle_animation", True),
                    "set": lambda checked: pet._toggle_idle_animation(checked),  # looked up on click, not at menu build
                },
                {"type": "separator"},
                {
                    "type": "toggle",
                    "label": "静音",
                    "get": lambda: pet.config.get("muted", False),
                    "set": pet._toggle_mute,
                },
                {
                    "type": "value_action",
                    "label": "音量",
                    "get_value": lambda: f"{round(pet.config.get('volume', 0.8) * 100)}%",
                    "action": pet._open_volume_panel,
                },
                {
                    "type": "toggle",
                    "label": "界面音效",
                    "get": lambda: pet.config.get("menu_sfx_enabled", True),
                    "set": pet._toggle_menu_sfx,
                },
                {"type": "separator"},
                {
                    "type": "toggle",
                    "label": "开机自启",
                    "get": lambda: pet.config.get("autostart", False),
                    "set": pet._toggle_autostart,
                },
                {
                    "type": "toggle",
                    "label": "检查更新",
                    "get": lambda: pet.config.get("check_updates", True),
                    "set": lambda checked: pet._toggle_update_check(checked),  # looked up on click, not at menu build
                },
            ],
        },
        {
            "type": "category",
            "key": "mercy",
            "label": "仁慈",
            "items": [
                {
                    "type": "toggle",
                    "label": "隐身",
                    "get": lambda: not pet.isVisible(),
                    "set": lambda _v: pet._toggle_visible(),
                },
                {"type": "action", "label": "离开", "action": pet.request_quit},
            ],
        },
    ]


def iter_leaf_specs(spec):
    """Every non-category item of a (possibly nested) menu spec, in order."""
    for item in spec:
        if item["type"] == "category":
            yield from iter_leaf_specs(item["items"])
        else:
            yield item


def find_category(pet, key):
    for item in _build_menu_spec(pet):
        if item["type"] == "category" and item.get("key") == key:
            return item
    return None


def _add_spec_rows(popup, pet, items):
    """Builds one row per spec item into `popup`. Returns the 调情 row (the
    item with key "flirt"), or None if this list has none -- the hover/
    escape wiring for it is attached here, so it lives on whichever popup
    actually holds the row."""
    flirt_row = None
    for spec in items:
        kind = spec["type"]
        if kind == "separator":
            popup.add_row(make_separator_row(metrics=popup.metrics), selectable=False)
        elif kind == "toggle":
            # Deferred with singleShot(0) so the popup closes after this
            # click handler has returned, never from inside it. `pet`'s
            # method is looked up lazily at that moment.
            on_change = (
                (lambda: QTimer.singleShot(0, lambda: pet._close_active_popup()))
                if spec.get("close_after")
                else None
            )
            popup.add_row(
                make_toggle_row(
                    spec["label"],
                    spec["get"],
                    spec["set"],
                    popup.font,
                    audio=pet.audio,
                    on_change=on_change,
                    metrics=popup.metrics,
                )
            )
        elif kind == "value_action":
            popup.add_row(
                make_value_action_row(
                    spec["label"],
                    spec["get_value"],
                    spec["action"],
                    popup.font,
                    audio=pet.audio,
                    close_after=popup.close,
                    metrics=popup.metrics,
                )
            )
        elif kind == "category":
            key = spec["key"]
            popup.add_row(
                make_action_row(
                    spec["label"],
                    lambda key=key: pet._open_menu_category(key),
                    popup.font,
                    audio=pet.audio,
                    close_after=popup.close,
                    metrics=popup.metrics,
                )
            )
        elif kind == "action":
            row = popup.add_row(
                make_action_row(
                    spec["label"],
                    spec["action"],
                    popup.font,
                    audio=pet.audio,
                    close_after=popup.close,
                    metrics=popup.metrics,
                )
            )
            if spec.get("key") == "flirt":
                flirt_row = row
                # The heart-selection system only reports a *new*
                # selection (enterEvent on some other row) -- it never
                # reports "the cursor left 调情 into blank/separator
                # space, or off the popup entirely". Without this, the
                # hover-hesitate state machine never learns the cursor
                # moved away unless it happens to land on another row.
                row.on_leave = lambda: pet.flirt.on_menu_selection_changed(False)
    return flirt_row


class PetContextMenu(UndertalePopup):
    """First layer: direct items + category rows."""

    def __init__(self, pet, parent=None, ui_scale=1.0, min_font_px=None):
        super().__init__(
            audio=pet.audio,
            always_on_top=pet._always_on_top,
            parent=parent,
            ui_scale=ui_scale,
            min_font_px=min_font_px,
        )
        _add_spec_rows(self, pet, _build_menu_spec(pet))
        self.finish_layout()


class CategoryPopup(UndertalePopup):
    """Second layer: one category's small box. First row is "< 返回"; Esc
    (and X, like every popup) does the same. Clicking outside closes
    everything as usual (Qt.Popup) and does NOT return to the main menu."""

    def __init__(self, pet, category_spec, parent=None, ui_scale=1.0, min_font_px=None):
        super().__init__(
            audio=pet.audio,
            always_on_top=pet._always_on_top,
            parent=parent,
            ui_scale=ui_scale,
            min_font_px=min_font_px,
        )
        self._pet = pet
        self.category_key = category_spec.get("key")
        self.back_row = self.add_row(
            make_action_row(
                BACK_LABEL,
                self._go_back,
                self.font,
                audio=pet.audio,
                metrics=self.metrics,
            )
        )
        self.flirt_row = _add_spec_rows(self, pet, category_spec["items"])
        self.finish_layout()

    def _go_back(self):
        self._pet._back_to_main_menu()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_X):
            self._go_back()
            return
        super().keyPressEvent(event)
