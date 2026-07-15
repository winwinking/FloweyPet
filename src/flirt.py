# -*- coding: utf-8 -*-
""""调情" (flirt) feature: dialogue pool loading/selection plus the
hover-hesitation "抓包" easter egg state machine.

Pool loading follows the same lazy-cached module-level pattern as roast.py.
Dialogue playback itself always goes through DialogueManager.speak(), so it
reuses the normal bubble/typewriter/face pipeline -- this module only ever
decides WHEN a line plays and WHICH pool it comes from.
"""
import json
import random
import time

from PyQt6.QtCore import QObject, QTimer

from error_log import log_exception
from paths import DIALOGUE_FLIRT_PATH

FLAT_POOLS = ("flirt_hesitate", "flirt_escape", "flirt_caught_confirm", "flirt_stall")

_data = None
_last_flat_pick = {}  # pool_name -> last entry index shown, so it never repeats immediately
_last_tier_pick = None  # (tier_index, entry_index) of the last flirt_response line shown


def _load():
    global _data
    if _data is None:
        try:
            with open(DIALOGUE_FLIRT_PATH, "r", encoding="utf-8") as f:
                _data = json.load(f)
        except (OSError, json.JSONDecodeError):
            log_exception(f"flirt: could not load {DIALOGUE_FLIRT_PATH}, 调情 will have nothing to say")
            _data = {}
    return _data


def _entry_to_segments(entry):
    """Same lines/faces -> segments conversion dialogue_manager uses for
    dialogues_info.json entries: a face slot with one tag holds for the
    whole line, 2+ tags switch partway through typing."""
    segments = []
    for line, faces in zip(entry.get("lines", []), entry.get("faces", [])):
        face = faces[0] if len(faces) == 1 else list(faces)
        segments.append({"text": line, "face": face})
    return segments


def _pick_flat(pool_name):
    pool = _load().get(pool_name) or []
    if not pool:
        return None
    indices = list(range(len(pool)))
    last = _last_flat_pick.get(pool_name)
    if last is not None and len(indices) > 1:
        indices = [i for i in indices if i != last]
    idx = random.choice(indices)
    _last_flat_pick[pool_name] = idx
    return _entry_to_segments(pool[idx])


def _find_tier(flirt_count):
    tiers = _load().get("flirt_response") or []
    for tier_idx, tier in enumerate(tiers):
        lo = tier.get("min", 1)
        hi = tier.get("max", -1)
        if flirt_count >= lo and (hi == -1 or flirt_count <= hi):
            return tier_idx, tier
    return None, None


def pick_response(flirt_count):
    global _last_tier_pick
    tier_idx, tier = _find_tier(flirt_count)
    if tier is None:
        return None
    entries = tier.get("entries") or []
    if not entries:
        return None
    indices = list(range(len(entries)))
    if _last_tier_pick and _last_tier_pick[0] == tier_idx and len(indices) > 1:
        indices = [i for i in indices if i != _last_tier_pick[1]]
    idx = random.choice(indices)
    _last_tier_pick = (tier_idx, idx)
    return _entry_to_segments(entries[idx])


def pick_hesitate():
    return _pick_flat("flirt_hesitate")


def pick_escape():
    return _pick_flat("flirt_escape")


def pick_caught_confirm():
    return _pick_flat("flirt_caught_confirm")


def pick_stall():
    return _pick_flat("flirt_stall")


# ------------------------------------------------------------- state machine
class FlirtState:
    IDLE = "idle"
    HOVERING = "hovering"  # heart is on the 调情 row, hesitate timer running
    CONFRONT = "confront"  # hesitate threshold hit, "抓包" already played


class FlirtController(QObject):
    """Owns the "调情" row's hover-hesitation state machine. Driven purely
    by two inputs from the right-click menu: on_menu_selection_changed(),
    fired every time the heart cursor lands on/leaves the 调情 row (mouse
    hover and keyboard Up/Down alike, since both route through the same
    UndertalePopup._select_index -> selection_changed signal), and
    on_menu_closed(). PetWindow owns talking to DialogueManager/Stats;
    resolve_click() just reports which branch applies and lets the caller
    build/play the actual segments, so this class stays pure state."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.state = FlirtState.IDLE
        self._last_trigger_monotonic = None
        self._stall_fired = False

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._on_hover_elapsed)

        self._stall_timer = QTimer(self)
        self._stall_timer.setSingleShot(True)
        self._stall_timer.timeout.connect(self._on_stall_elapsed)

        self.on_hesitate = lambda: None  # callbacks wired by the owner (PetWindow)
        self.on_escape = lambda: None
        self.on_stall = lambda: None

    # ----------------------------------------------------------------- config
    def _cfg(self):
        return self.config.get("flirt", {})

    def _hesitate_ms(self):
        return int(self._cfg().get("hesitate_seconds", 6) * 1000)

    def _stall_ms(self):
        return int(self._cfg().get("stall_seconds", 8) * 1000)

    def _cooldown_seconds(self):
        return self._cfg().get("cooldown_minutes", 5) * 60

    def _in_cooldown(self):
        if self._last_trigger_monotonic is None:
            return False
        return (time.monotonic() - self._last_trigger_monotonic) < self._cooldown_seconds()

    # ----------------------------------------------------------------- events
    def on_menu_selection_changed(self, is_flirt_row):
        if self.state == FlirtState.CONFRONT:
            if not is_flirt_row:
                self._resolve_escape()
            return
        if is_flirt_row:
            if self._in_cooldown():
                return
            self.state = FlirtState.HOVERING
            self._hover_timer.start(self._hesitate_ms())
        else:
            self._cancel_hover()

    def on_menu_closed(self):
        self._hover_timer.stop()
        was_confronting = self.state == FlirtState.CONFRONT
        if was_confronting:
            self._stall_timer.stop()
        self.state = FlirtState.IDLE
        if was_confronting:
            # Branch (a) covers BOTH "moved the cursor away" and "closed
            # the menu" while confronting (e.g. Escape key, or a click
            # outside the popup) -- both end the confrontation with the
            # same escape line. A confirm-click resolves the state via
            # resolve_click() before the menu's close_after() fires, so
            # this is never reached for that path (state is already IDLE
            # by then) and can't double-fire an escape after a confirm.
            self.on_escape()

    def is_confronting(self):
        return self.state == FlirtState.CONFRONT

    def resolve_click(self):
        """Call when the 调情 row is actually activated (click/Enter).
        Returns True if this click is resolving an active confrontation
        (branch b: caught_confirm + response) vs. an ordinary flirt click
        (plain response) -- caller decides what to play either way."""
        was_confronting = self.state == FlirtState.CONFRONT
        self._hover_timer.stop()
        self._stall_timer.stop()
        self.state = FlirtState.IDLE
        return was_confronting

    # ------------------------------------------------------------- internals
    def _cancel_hover(self):
        if self.state == FlirtState.HOVERING:
            self.state = FlirtState.IDLE
        self._hover_timer.stop()

    def _on_hover_elapsed(self):
        if self.state != FlirtState.HOVERING:
            return
        self.state = FlirtState.CONFRONT
        self._last_trigger_monotonic = time.monotonic()
        self._stall_fired = False
        self._stall_timer.start(self._stall_ms())
        self.on_hesitate()

    def _on_stall_elapsed(self):
        if self.state != FlirtState.CONFRONT or self._stall_fired:
            return
        self._stall_fired = True
        self.on_stall()

    def _resolve_escape(self):
        self.state = FlirtState.IDLE
        self._stall_timer.stop()
        self.on_escape()
