# -*- coding: utf-8 -*-
"""Dedicated event log for DialogueArbiter's decisions (dialogue.log) --
every functional-dialogue request's source, priority verdict (played /
dropped / rerouted-to-another-pool), counter changes, and rant/exit-lock
events, so "why didn't it talk / why did it say that" is always
reconstructable after the fact. Same pattern as app_log.py/reminder_log.py.
"""
from datetime import datetime, timedelta
from pathlib import Path

LOG_PATH = Path(r"C:\FloweyPet\dialogue.log")
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
