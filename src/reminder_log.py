# -*- coding: utf-8 -*-
"""Dedicated event log for the reminder/pursuit system (reminder.log).
Kept separate from error.log since these are expected lifecycle events,
not failures -- the point is that someone can tail this file later and
confirm the pursuit escalation actually ran, without having to watch the
screen live. Pruned to the last RETENTION_DAYS on every write so it never
grows unbounded.
"""
import os
from datetime import datetime, timedelta
from pathlib import Path

# FLOWEYPET_REMINDER_LOG lets a test run redirect this file away from the
# real instance's log, so an offscreen self-test never interleaves its
# events with (or prunes) the log the user actually reads.
LOG_PATH = Path(os.environ.get("FLOWEYPET_REMINDER_LOG", r"C:\FloweyPet\reminder.log"))
RETENTION_DAYS = 7


def log_event(message):
    now = datetime.now()
    line = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _prune(now)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def _prune(now):
    if not LOG_PATH.exists():
        return
    cutoff = now - timedelta(days=RETENTION_DAYS)
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    kept = [ln for ln in lines if _within_retention(ln, cutoff)]
    if len(kept) != len(lines):
        try:
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(kept)
        except OSError:
            pass


def _within_retention(line, cutoff):
    try:
        ts = datetime.strptime(line[1:20], "%Y-%m-%d %H:%M:%S")
        return ts >= cutoff
    except (ValueError, IndexError):
        return True  # malformed line -- keep it rather than risk losing data
