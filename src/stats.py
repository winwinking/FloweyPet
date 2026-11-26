# -*- coding: utf-8 -*-
"""Persisted usage statistics backing the "记录" panel. Kept in its own
file (stats.json) rather than config.json since these are accumulating
counters, not user settings -- separating them keeps config.json diffable
and makes it obvious what a fresh install resets vs. keeps.
"""
import json
from datetime import datetime, timedelta

from PyQt6.QtCore import QObject, QTimer

from error_log import log_exception, log_message
from paths import STATS_PATH
from user_data import atomic_write_json, quarantine_bad_file

AUTOSAVE_INTERVAL_MS = 5 * 60_000
COMPANION_TICK_MS = 60_000

DEFAULTS = {
    "companion_minutes": 0,
    "interaction_count": 0,
    "dialogue_trigger_count": 0,
    "pomodoro_completed": 0,
    "pomodoro_minutes": 0,
    "reminder_rounds": {"water": 0, "meal": 0, "sleep": 0},
    "reminder_responded": {"water": 0, "meal": 0, "sleep": 0},
    "reminder_perfunctory": {"water": 0, "meal": 0, "sleep": 0},  # 已敷衍 (点了知道了)
    # Bookkeeping version for the reminder counters. Schema 2 = 敷衍 no
    # longer counts as responded (see record_reminder_round / the one-time
    # migration in __init__). Default 1 so an old stats.json (which lacks
    # the key entirely) is recognized as pre-migration data.
    "reminder_stats_schema": 1,
    # Same idea for flirt_count: schema 2 = the counter was reset to 0 once
    # at 3.3.0 (see _migrate_flirt_schema). Default 1 so an old stats.json
    # without the key is treated as pre-reset data.
    "flirt_stats_schema": 1,
    "flirt_count": 0,  # persisted "调情" success counter, drives flirt_response tier selection
    "rare_seen": [],  # ids (see dialogue_manager's rare-entry id) already shown
    "latest_active_score": 0.0,  # see record_activity() for what this encodes
    "latest_active_time": None,  # cached "HH:MM" display string for the score above
    "last_active_at": None,  # epoch seconds of the last detected activity ping (any kind)
    "session_start_at": None,  # epoch seconds where the current unbroken activity chain began
    "night_owl_notified_date": None,  # logical-day date already given the once-per-night nag
    "morning_greeted_date": None,  # logical-day date already rolled for the fresh-morning greet
    "ut_first_seen": False,  # UNDERTALE launch-egg "first ever" line already shown, see game_detector.py
    "dr_first_seen": False,  # DELTARUNE launch-egg "first ever" line already shown, see game_detector.py
    "game_egg_trigger_count": 0,  # total game launch/exit egg lines triggered (not shown-count -- see game_detector._trigger)
}


def _deep_merge(defaults, loaded):
    out = dict(defaults)
    for k, v in loaded.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _default_copy():
    return json.loads(json.dumps(DEFAULTS))  # deep copy, defaults nest dicts/lists


def _load():
    if not STATS_PATH.exists():
        return _default_copy()
    try:
        with open(STATS_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except OSError as e:
        log_message(f"stats load: 读取 {STATS_PATH} 失败（{e!r}），本次使用默认值，文件未改动")
        return _default_copy()
    except ValueError as e:  # json.JSONDecodeError / UnicodeDecodeError
        moved = quarantine_bad_file(STATS_PATH)
        log_message(f"stats load: {STATS_PATH} 不是合法 JSON（{e!r}），已改名为 {moved}，使用默认值")
        return _default_copy()
    if not isinstance(loaded, dict):
        moved = quarantine_bad_file(STATS_PATH)
        log_message(f"stats load: {STATS_PATH} 内容不是 JSON 对象，已改名为 {moved}，使用默认值")
        return _default_copy()
    return _deep_merge(DEFAULTS, loaded)


class Stats(QObject):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.data = _load()
        self._migrate_bogus_latest_active()
        self._migrate_reminder_schema()
        self._migrate_flirt_schema()
        # _load()'s _deep_merge() already backfills any field missing from
        # an old/partial stats.json in memory; write it straight back out so
        # the file itself stops being incomplete instead of relying on some
        # later save() to eventually catch it up.
        self.save()

        self._companion_timer = QTimer(self)
        self._companion_timer.timeout.connect(self._tick_companion_minute)
        self._companion_timer.start(COMPANION_TICK_MS)

        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self.save)
        self._autosave_timer.start(AUTOSAVE_INTERVAL_MS)

    def save(self):
        try:
            atomic_write_json(STATS_PATH, self.data)
        except OSError:
            log_exception("stats.save")

    def _tick_companion_minute(self):
        self.data["companion_minutes"] += 1

    # ------------------------------------------------------------- counters
    def record_interaction(self):
        """One full interaction session: a click that triggered dialogue,
        or using a right-click menu function."""
        self.data["interaction_count"] += 1

    def record_dialogue_triggered(self):
        """Every dialogue bubble segment sequence Flowey plays, including
        proactive chatter -- called once per DialogueManager._show()."""
        self.data["dialogue_trigger_count"] += 1

    def record_pomodoro_completed(self, minutes_used):
        self.data["pomodoro_completed"] += 1
        self.data["pomodoro_minutes"] += minutes_used
        self.save()

    def record_pomodoro_minutes(self, minutes_used):
        self.data["pomodoro_minutes"] += minutes_used

    def record_reminder_round(self, kind, outcome):
        """One finished reminder round. outcome: "responded" (点确定),
        "dismissed" (点知道了 -> 已敷衍: counts toward rounds but NOT toward
        responded), or "unresponded" (超时走完追讨 / 被新一轮作废). 响应率 =
        responded / rounds, per kind."""
        rounds = self.data.setdefault("reminder_rounds", {"water": 0, "meal": 0, "sleep": 0})
        rounds[kind] = rounds.get(kind, 0) + 1
        if outcome == "responded":
            responded_map = self.data.setdefault(
                "reminder_responded", {"water": 0, "meal": 0, "sleep": 0}
            )
            responded_map[kind] = responded_map.get(kind, 0) + 1
        elif outcome == "dismissed":
            perfunctory_map = self.data.setdefault(
                "reminder_perfunctory", {"water": 0, "meal": 0, "sleep": 0}
            )
            perfunctory_map[kind] = perfunctory_map.get(kind, 0) + 1
        self.save()

    def response_rate(self, kind):
        """Returns a 0-100 float, or None if there's no data yet for kind."""
        total = self.data.get("reminder_rounds", {}).get(kind, 0)
        if total == 0:
            return None
        responded = self.data.get("reminder_responded", {}).get(kind, 0)
        return responded / total * 100

    def dismissed_count(self, kind):
        return self.data.get("reminder_perfunctory", {}).get(kind, 0)

    def _migrate_reminder_schema(self):
        """Schema 1 counted a 敷衍 ("知道了") click inside reminder_responded
        as well; schema 2 keeps the two disjoint. Subtract once so old data
        matches the new 口径, then stamp the version -- missing keys were
        already backfilled by _deep_merge, so the stats panel can never see
        an absent field either way."""
        if self.data.get("reminder_stats_schema", 1) >= 2:
            return
        responded = self.data.setdefault("reminder_responded", {"water": 0, "meal": 0, "sleep": 0})
        perfunctory = self.data.get("reminder_perfunctory", {})
        for kind, dismissed in perfunctory.items():
            if kind in responded:
                responded[kind] = max(0, responded.get(kind, 0) - dismissed)
        self.data["reminder_stats_schema"] = 2
        self.save()

    def _migrate_flirt_schema(self):
        """3.3.0 re-tiered the flirt lines, so every user's flirt_count is
        reset to 0 exactly once; the schema stamp keeps it from ever
        happening again. Touches only flirt_count and flirt_stats_schema."""
        if self.data.get("flirt_stats_schema", 1) >= 2:
            return
        old = self.data.get("flirt_count", 0)
        log_message(f"stats migrate: 3.3.0 调情次数清零一次，原来的 flirt_count={old!r}")
        self.data["flirt_count"] = 0
        self.data["flirt_stats_schema"] = 2
        self.save()

    def record_flirt(self):
        """One successful "调情" trigger (basic click or the confront-confirm
        branch). Returns the updated count so callers can immediately use it
        for tier selection without a second read."""
        self.data["flirt_count"] = self.data.get("flirt_count", 0) + 1
        self.save()
        return self.data["flirt_count"]

    def mark_rare_seen(self, rare_id):
        seen = self.data.setdefault("rare_seen", [])
        if rare_id not in seen:
            seen.append(rare_id)
            self.save()

    def rare_progress(self, total_count):
        seen_count = min(len(self.data.get("rare_seen", [])), total_count)
        pct = (seen_count / total_count * 100) if total_count else 0.0
        return seen_count, total_count, pct

    def is_game_first_seen(self, key):
        return self.data.get(f"{key}_first_seen", False)

    def mark_game_first_seen(self, key):
        field = f"{key}_first_seen"
        if not self.data.get(field, False):
            self.data[field] = True
            self.save()

    def record_game_egg_trigger(self):
        self.data["game_egg_trigger_count"] = self.data.get("game_egg_trigger_count", 0) + 1
        self.save()

    def _migrate_bogus_latest_active(self):
        """One-time cleanup for data written by the old (pre-day-boundary)
        version of record_activity(), which mistook ordinary morning use
        (6:00-11:59) for staying up all night. Any stored time in that
        range can only be leftover bad data, since the current logic never
        writes one -- clear it so the record starts fresh."""
        t = self.data.get("latest_active_time")
        if not t:
            return
        try:
            hour = int(t.split(":")[0])
        except (ValueError, IndexError):
            return
        if 6 <= hour < 12:
            self.data["latest_active_score"] = 0.0
            self.data["latest_active_time"] = None
            self.save()

    def _logical_date_str(self, dt):
        """0:00 through day_boundary_hour belongs to the PREVIOUS calendar
        day's logical date, so a late-night session reads as one continuous
        "day" instead of splitting at midnight."""
        boundary = self.config.get("day_boundary_hour", 6)
        d = dt.date()
        if dt.hour < boundary:
            d -= timedelta(days=1)
        return d.isoformat()

    def current_logical_date(self):
        return self._logical_date_str(datetime.now())

    def note_activity(self, ts):
        """Update the session-continuity clock. A gap longer than
        session_gap_minutes since the last ping starts a new chain;
        otherwise the current chain (session_start_at) just extends."""
        gap_minutes = self.config.get("session_gap_minutes", 90)
        last = self.data.get("last_active_at")
        if last is None or (ts - last) > gap_minutes * 60:
            self.data["session_start_at"] = ts
        self.data["last_active_at"] = ts

    def is_ongoing_night_session(self, now=None):
        """True if the current unbroken activity chain began during normal
        daytime/evening hours (>= day_boundary_hour) on an earlier calendar
        date than `now` -- i.e. this really is a continuation of last
        night's use, not a session that itself started fresh inside the
        0:00-day_boundary_hour window (which has no evening predecessor to
        "carry over" from) or a normal same-day session that hasn't crossed
        midnight at all."""
        now = now or datetime.now()
        start_ts = self.data.get("session_start_at")
        if start_ts is None:
            return False
        start_dt = datetime.fromtimestamp(start_ts)
        boundary = self.config.get("day_boundary_hour", 6)
        start_hour = start_dt.hour + start_dt.minute / 60
        return start_dt.date() != now.date() and start_hour >= boundary

    def record_activity(self):
        """Tracks the historical latest "how deep into the night" moment
        the user has continuously stayed up, ever. Only activity inside
        0:00-day_boundary_hour that's part of an unbroken chain reaching
        back to normal evening use updates the record; a fresh boot inside
        that window (e.g. an early riser) or any activity at/after
        day_boundary_hour never does, however late the clock briefly reads."""
        now = datetime.now()
        ts = now.timestamp()
        hour = now.hour + now.minute / 60
        boundary = self.config.get("day_boundary_hour", 6)
        self.note_activity(ts)
        if hour < boundary and self.is_ongoing_night_session(now):
            score = hour + 24
            if score > self.data.get("latest_active_score", 0.0):
                self.data["latest_active_score"] = score
                self.data["latest_active_time"] = now.strftime("%H:%M")

    def companion_hours(self):
        return self.data.get("companion_minutes", 0) / 60
