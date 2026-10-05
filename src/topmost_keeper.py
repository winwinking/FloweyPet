# -*- coding: utf-8 -*-
"""Keeps the pet, pomodoro display and dialogue bubble on top of other
topmost windows. Windows lets any other WS_EX_TOPMOST window that was
raised later cover ours, so the user's "置顶" checkbox alone isn't enough:
once a second, every visible window the user pinned gets re-raised with a
raw SetWindowPos (see win_zorder.py -- no Qt window teardown, no flicker).

Bubble goes last so it always lands above the pet. The whole tick is
skipped while a game-egg TopmostGuard cycle is running (it owns z-order
then) and while the app is quitting.
"""
from PyQt6.QtCore import QObject, QTimer

from win_zorder import apply_topmost

DEFAULT_INTERVAL_MS = 1000


class TopmostKeeper(QObject):
    def __init__(self, pet, guard_active, interval_ms=DEFAULT_INTERVAL_MS):
        """pet: PetWindow (exposes pomodoro_display / dialogue_box /
        is_pinned_on_top() / is_exiting()). guard_active: zero-arg callable,
        True while a TopmostGuard cycle is in progress."""
        super().__init__(pet)
        self._pet = pet
        self._guard_active = guard_active
        self._timer = QTimer(self)
        self._timer.setInterval(max(100, int(interval_ms)))
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def _tick(self):
        try:
            if self._pet.is_exiting() or self._guard_active():
                return
            pet = self._pet
            for widget, pinned in (
                (pet, pet.is_pinned_on_top),
                (pet.pomodoro_display, pet.pomodoro_display.is_pinned),
                (pet.dialogue_box, pet.dialogue_box.is_pinned),
            ):
                if widget.isVisible() and pinned():
                    apply_topmost(widget, True)
        except Exception:
            pass  # a keep-alive must never raise into the event loop
