# -*- coding: utf-8 -*-
"""Persisted usage statistics backing the "记录" panel. Kept in its own
file (stats.json) rather than config.json since these are accumulating
counters, not user settings -- separating them keeps config.json diffable
and makes it obvious what a fresh install resets vs. keeps.
"""
import json
from datetime import datetime, timedelta

from PyQt6.QtCore import QObject, QTimer

from paths import STATS_PATH

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
    "reminder_perfunctory": {"water": 0, "meal": 0, "sleep": 0},
    "rare_seen": [],  # ids (see dialogue_manager's rare-entry id) already shown
    "latest_active_score": 0.0,  # see record_activity() for what this encodes
    "latest_active_time": None,  # cached "HH:MM" display string for the score above
    "last_active_at": None,  # epoch seconds of the last detected activity ping (any kind)
    "session_start_at": None,  # epoch seconds where the current unbroken activity chain began
    "night_owl_notified_date": None,  # logical-day date already given the once-per-night nag
    "morning_greeted_date": None,  # logical-day date already rolled for the fresh-morning greet
}


def _deep_merge(defaults, loaded):
    out = dict(defaults)
    for k, v in loaded.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load():
    if STATS_PATH.exists():
        try:
            with open(STATS_PATH, "r", encoding="utf-8") as f:
                return _deep_merge(DEFAULTS, json.load(f))
        except (OSError, json.JSONDecodeError):
            pass
    return json.loads(json.dumps(DEFAULTS))  # deep copy, defaults nest dicts/lists


class Stats(QObject):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.data = _load()
        self._migrate_bogus_latest_active()
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
            with open(STATS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

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

    def record_reminder_round(self, kind, responded, perfunctory=False):
        """responded covers both response buttons ("确定" and "知道了") for
        the purposes of response_rate; perfunctory separately marks the
        "知道了" (acknowledged-but-not-really-responded) outcome so it can be
        told apart from a genuine "确定" later."""
        rounds = self.data.setdefault("reminder_rounds", {"water": 0, "meal": 0, "sleep": 0})
        rounds[kind] = rounds.get(kind, 0) + 1
        if responded:
            responded_map = self.data.setdefault(
                "reminder_responded", {"water": 0, "meal": 0, "sleep": 0}
            )
            responded_map[kind] = responded_map.get(kind, 0) + 1
            if perfunctory:
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

    def mark_rare_seen(self, rare_id):
        seen = self.data.setdefault("rare_seen", [])
        if rare_id not in seen:
            seen.append(rare_id)
            self.save()

    def rare_progress(self, total_count):
        seen_count = min(len(self.data.get("rare_seen", [])), total_count)
        pct = (seen_count / total_count * 100) if total_count else 0.0
        return seen_count, total_count, pct

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
