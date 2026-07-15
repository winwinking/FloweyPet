# -*- coding: utf-8 -*-
"""Reminder SCHEDULING only: decides when a water/meal/sleep reminder
comes due (interval clock for water, wall-clock times for meal/sleep) and
hands the round off to ReminderChaseController, which owns everything
that happens after the popup appears -- the staged 追讨 escalation, its
timers, stats, and logging. See reminder_chase.py's docstring for the
state machine and for the root-cause history of why the escalation logic
was moved out of here into a controller with its own absolute-schedule
timers (short version: the old hop-by-hop scheduling died whenever a
popup was closed without emitting `finished`, e.g. by the hide/quit
burrow animation, stranding the round forever).

reminder_test_mode (config) compresses the chase schedule to 1分钟=1秒
and forces a water reminder 10 real seconds after startup so the whole
chain can be exercised quickly; see ReminderChaseController.hold_ms for
the compressed popup hold.
"""
import time
from datetime import datetime

from PyQt6.QtCore import QObject, QTimer

import reminder_log
from reminder_chase import ReminderChaseController

CHECK_INTERVAL_MS = 30_000
TEST_MODE_FORCED_TRIGGER_MS = 10_000


class Reminders(QObject):
    def __init__(self, config, save_config, dialogue_manager, stats=None):
        super().__init__()
        self.config = config
        self.save_config = save_config
        self.dm = dialogue_manager
        self.stats = stats
        self.test_mode = bool(config.get("reminder_test_mode", False))
        self.chase = ReminderChaseController(config, dialogue_manager, stats)
        self._next_water_due = time.monotonic() + self._water_interval_s()
        self._fired_today = set()  # (kind, "HH:MM", "YYYY-MM-DD")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check)
        self._timer.start(CHECK_INTERVAL_MS)

        if self.test_mode:
            reminder_log.log_event("测试模式已启用：10秒后将强制触发一次喝水提醒（1分钟=1秒压缩计时）")
            QTimer.singleShot(TEST_MODE_FORCED_TRIGGER_MS, lambda: self._fire("water"))

    def _water_interval_s(self):
        return self.config["reminders"]["water"].get("interval_minutes", 45) * 60

    def get_water_interval(self):
        return self.config["reminders"]["water"].get("interval_minutes", 45)

    def is_awaiting_response(self):
        """True from the moment a reminder fires until the round is fully
        resolved (含 CHASING 等待期) -- used by PetWindow to keep idle/AFK
        chatter from talking over an active round."""
        return self.chase.is_round_active()

    def confirm_hold_ms(self):
        return self.chase.hold_ms()

    def is_enabled(self, kind):
        return self.config["reminders"][kind].get("enabled", True)

    def set_enabled(self, kind, enabled):
        self.config["reminders"][kind]["enabled"] = enabled
        self.save_config(self.config)

    def set_water_interval(self, minutes):
        self.config["reminders"]["water"]["interval_minutes"] = minutes
        self.save_config(self.config)
        self._next_water_due = time.monotonic() + minutes * 60

    def get_times(self, kind):
        return list(self.config["reminders"][kind].get("times", []))

    def set_times(self, kind, times):
        self.config["reminders"][kind]["times"] = sorted(times)
        self.save_config(self.config)

    # ------------------------------------------------------------------ check
    def _check(self):
        now_wall = datetime.now()
        if self.is_enabled("water") and time.monotonic() >= self._next_water_due:
            self._fire("water")
            self._next_water_due = time.monotonic() + self._water_interval_s()

        hhmm = now_wall.strftime("%H:%M")
        today = now_wall.strftime("%Y-%m-%d")
        for kind in ("meal", "sleep"):
            if not self.is_enabled(kind):
                continue
            times = self.config["reminders"][kind].get("times", [])
            if hhmm in times:
                key = (kind, hhmm, today)
                if key not in self._fired_today:
                    self._fired_today.add(key)
                    self._fire(kind)

    # -------------------------------------------------------------- firing
    def _fire(self, kind):
        self.chase.start_round(kind)

        fire_moment = time.monotonic()
        neglect_ms = self.config.get("neglect_after_minutes", 120) * 60_000
        QTimer.singleShot(neglect_ms, lambda k=kind, t=fire_moment: self._check_neglect(k, t))

    def _check_neglect(self, kind, fire_moment):
        if self.dm.last_interaction_time < fire_moment:
            self.dm.trigger_neglect(kind)
