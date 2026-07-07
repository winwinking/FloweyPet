# -*- coding: utf-8 -*-
"""Pomodoro timer: one focus phase, one break phase, then stops. While
running (either phase), random "pomodoro_chat" lines are sprinkled in and
ordinary idle chatter is expected to be suppressed by the caller (see
PetWindow.set_pomodoro_active).
"""
import random

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class Pomodoro(QObject):
    tick = pyqtSignal()
    active_changed = pyqtSignal(bool)

    def __init__(self, config, save_config, dialogue_manager, stats=None):
        super().__init__()
        self.config = config
        self.save_config = save_config
        self.dm = dialogue_manager
        self.stats = stats
        self.state = "idle"  # idle | focus | break
        self.remaining = 0
        self._focus_s = 0
        self._break_s = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_tick)
        self._chat_timer = QTimer(self)
        self._chat_timer.setSingleShot(True)
        self._chat_timer.timeout.connect(self._on_chat_tick)

    @property
    def is_running(self):
        return self.state != "idle"

    def start(self, focus_minutes=None, break_minutes=None):
        focus_minutes = focus_minutes or self.config["pomodoro"]["focus_minutes"]
        break_minutes = break_minutes or self.config["pomodoro"]["break_minutes"]
        self._focus_s = focus_minutes * 60
        self._break_s = break_minutes * 60
        self.state = "focus"
        self.remaining = self._focus_s
        self._timer.start(1000)
        self._schedule_chat()
        self.dm.trigger_pomodoro_status("start")
        self.active_changed.emit(True)
        self.tick.emit()

    def cancel(self):
        was_running = self.is_running
        self.state = "idle"
        self._timer.stop()
        self._chat_timer.stop()
        self.tick.emit()
        if was_running:
            self.active_changed.emit(False)

    def _on_tick(self):
        self.remaining -= 1
        self.tick.emit()
        if self.remaining <= 0:
            if self.state == "focus":
                self.state = "break"
                self.remaining = self._break_s
                self.dm.trigger_pomodoro_status("end")
            else:
                if self.stats:
                    minutes_used = (self._focus_s + self._break_s) // 60
                    self.stats.record_pomodoro_completed(minutes_used)
                self.cancel()

    def _schedule_chat(self):
        mn = self.config.get("pomodoro_chat_min_minutes", 5)
        mx = self.config.get("pomodoro_chat_max_minutes", 10)
        delay_ms = random.randint(mn, max(mn, mx)) * 60_000
        self._chat_timer.start(delay_ms)

    def _on_chat_tick(self):
        if self.is_running:
            self.dm.trigger_pomodoro_chat()
            self._schedule_chat()

    def status_text(self):
        if self.state == "idle":
            return None
        m, s = divmod(max(self.remaining, 0), 60)
        label = "专注" if self.state == "focus" else "休息"
        return f"{label} {m:02d}:{s:02d}"
