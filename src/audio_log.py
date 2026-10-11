# -*- coding: utf-8 -*-
"""Dedicated event log for the audio subsystem (audio.log). Separate from
error.log/reminder.log since audio events (play attempts, failures,
rebuilds) can be high-volume when audio_debug is on -- keeping them in
their own file means turning on verbose logging never floods the crash
log. Pruned to the last RETENTION_DAYS on every write so it never grows
unbounded.
"""
from datetime import datetime, timedelta
from pathlib import Path

from paths import LOG_DIR

LOG_PATH = LOG_DIR / "audio.log"
RETENTION_DAYS = 3


def log_event(message):
    now = datetime.now()
    line = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _prune(now)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except (OSError, ValueError):
        pass


def _prune(now):
    if not LOG_PATH.exists():
        return
    cutoff = now - timedelta(days=RETENTION_DAYS)
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except (OSError, ValueError):
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
