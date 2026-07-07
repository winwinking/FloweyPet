# -*- coding: utf-8 -*-
"""Water / meal / sleep reminders, plus a staged pursuit escalation if the
user ignores one: at each of reminder_pursuit_stages_minutes (default
5/10/20/30 minutes after the reminder fired) a nag popup re-shows, and if
the last stage is reached with still no response the round auto-ends as
"timed out". Sleep reminders are exempt from pursuit -- they only ever
show once, since nagging someone who's about to go to bed doesn't fit the
character and there's nothing actionable to escalate about.

A new round always supersedes whatever round came before it: if the
previous kind never got a confirm click, it's recorded as unresponded and
its pending pursuit timers become no-ops (guarded by round id, see
_on_pursuit_stage).

reminder_test_mode (config) compresses these waits so the whole flow can
be watched in under a minute: pursuit stage minutes collapse to seconds,
the hold time collapses roughly 10x, and a water reminder is forced 10
real seconds after startup.
"""
import time
from datetime import datetime

from PyQt6.QtCore import QObject, QTimer

import reminder_log

CHECK_INTERVAL_MS = 30_000
TEST_MODE_FORCED_TRIGGER_MS = 10_000
DEFAULT_PURSUIT_STAGES_MINUTES = [5, 10, 20, 30]
DEFAULT_PURSUIT_HOLD_SECONDS = 60

_KIND_LABELS = {"water": "喝水", "meal": "吃饭", "sleep": "睡觉"}


class Reminders(QObject):
    def __init__(self, config, save_config, dialogue_manager, stats=None):
        super().__init__()
        self.config = config
        self.save_config = save_config
        self.dm = dialogue_manager
        self.stats = stats
        self.test_mode = bool(config.get("reminder_test_mode", False))
        self._next_water_due = time.monotonic() + self._water_interval_s()
        self._fired_today = set()  # (kind, "HH:MM", "YYYY-MM-DD")
        self._awaiting_confirm = False
        self._awaiting_kind = None
        self._round_id = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._check)
        self._timer.start(CHECK_INTERVAL_MS)

        if self.test_mode:
            reminder_log.log_event("测试模式已启用：10秒后将强制触发一次喝水提醒")
            QTimer.singleShot(TEST_MODE_FORCED_TRIGGER_MS, lambda: self._fire("water"))

    def _water_interval_s(self):
        return self.config["reminders"]["water"].get("interval_minutes", 45) * 60

    def get_water_interval(self):
        return self.config["reminders"]["water"].get("interval_minutes", 45)

    def is_awaiting_response(self):
        """True from the moment a reminder fires until it's confirmed,
        acknowledged, or times out -- used by PetWindow to keep idle/AFK
        chatter from talking over a reminder that's still waiting on you."""
        return self._awaiting_confirm

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

    # --------------------------------------------------------- pursuit timing
    def _pursuit_stages_minutes(self):
        return self.config.get("reminder_pursuit_stages_minutes", DEFAULT_PURSUIT_STAGES_MINUTES)

    def _pursuit_stage_ms(self, minutes):
        # Test mode: "5/10/20/30 minutes" -> "5/10/20/30 seconds" (the
        # numeric minute count is reused directly as a second count).
        return int(minutes * 1000) if self.test_mode else int(minutes * 60_000)

    def confirm_hold_ms(self):
        seconds = self.config.get("reminder_pursuit_hold_seconds", DEFAULT_PURSUIT_HOLD_SECONDS)
        if self.test_mode:
            # Test mode: "60 seconds" -> "6 seconds", per the test-mode spec's
            # own example -- a flat /10, not the same ratio used for stages.
            return int(seconds * 100)
        return int(seconds * 1000)

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
        if self.stats and self._awaiting_confirm and self._awaiting_kind:
            # The previous round never got a confirm click before this new
            # one superseded it -- count it as unresponded and invalidate
            # its pending pursuit timers via the round id bump below.
            self.stats.record_reminder_round(self._awaiting_kind, responded=False)
            reminder_log.log_event(
                f"本轮终止：{_KIND_LABELS.get(self._awaiting_kind, self._awaiting_kind)}（原因：被新一轮提醒作废）"
            )
        self.dm.trigger_reminder(kind)
        self._awaiting_confirm = True
        self._awaiting_kind = kind
        self._round_id += 1
        round_id = self._round_id
        reminder_log.log_event(f"提醒弹出：{_KIND_LABELS.get(kind, kind)}（首次）")

        if kind != "sleep":
            self._schedule_pursuit(kind, round_id)

        fire_moment = time.monotonic()
        neglect_ms = self.config.get("neglect_after_minutes", 120) * 60_000
        QTimer.singleShot(neglect_ms, lambda k=kind, t=fire_moment: self._check_neglect(k, t))

    def _check_neglect(self, kind, fire_moment):
        if self.dm.last_interaction_time < fire_moment:
            self.dm.trigger_neglect(kind)

    # ------------------------------------------------------- pursuit escalation
    def _schedule_pursuit(self, kind, round_id):
        stages = self._pursuit_stages_minutes()
        total = len(stages)
        for stage_idx, minutes in enumerate(stages):
            QTimer.singleShot(
                self._pursuit_stage_ms(minutes),
                lambda k=kind, rid=round_id, i=stage_idx, mins=minutes, n=total: self._on_pursuit_stage(
                    k, rid, i, mins, n
                ),
            )

    def _on_pursuit_stage(self, kind, round_id, stage_idx, minutes, total_stages):
        if not self._awaiting_confirm or self._awaiting_kind != kind or round_id != self._round_id:
            return  # this round already ended (confirmed / superseded) -- stale timer, no-op
        reminder_log.log_event(
            f"提醒弹出：{_KIND_LABELS.get(kind, kind)} 追讨第{stage_idx + 1}次（第{minutes}分钟）"
        )
        self.dm.trigger_reminder_nag(kind, stage_idx)
        if stage_idx >= total_stages - 1:
            if self.stats:
                self.stats.record_reminder_round(kind, responded=False)
            reminder_log.log_event(f"本轮终止：{_KIND_LABELS.get(kind, kind)}（原因：超时未响应）")
            self._awaiting_confirm = False
            self._awaiting_kind = None

    # ------------------------------------------------------------- confirm
    def on_reminder_confirmed(self, perfunctory=False):
        if self._awaiting_kind:
            kind = self._awaiting_kind
            self.dm.trigger_reminder_ack(kind)
            if self.stats:
                self.stats.record_reminder_round(kind, responded=True, perfunctory=perfunctory)
            reminder_log.log_event(f"点击{'知道了' if perfunctory else '确定'}：{_KIND_LABELS.get(kind, kind)}")
            reminder_log.log_event(
                f"本轮终止：{_KIND_LABELS.get(kind, kind)}（原因：{'敷衍' if perfunctory else '响应'}）"
            )
        self._awaiting_confirm = False
        self._awaiting_kind = None
