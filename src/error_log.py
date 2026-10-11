# -*- coding: utf-8 -*-
"""Central crash/error logging. A --windowed PyInstaller build has no
console for stray `print()`s to reach, so every unhandled exception --
whether caught by the global sys.excepthook safety net installed in
main.py or by a specific try/except around a panel-opening / dialogue-
playing entry point -- must land here instead of vanishing silently.
"""
import sys
import traceback
from datetime import datetime
from pathlib import Path

from paths import LOG_DIR

LOG_PATH = LOG_DIR / "error.log"


def log_exception(context, exc_info=None):
    """Appends a timestamped traceback to LOG_PATH (and stderr, when one
    exists). Call with no `exc_info` from inside an `except:` block to log
    the exception currently being handled; pass the 3-tuple from
    sys.exc_info()/a sys.excepthook call otherwise."""
    exc_type, exc_value, exc_tb = exc_info if exc_info else sys.exc_info()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _write(f"[{timestamp}] {context}\n{body}\n")


def log_message(context):
    """Records a non-exception diagnostic (e.g. 'skipped a malformed JSON
    entry') that would otherwise only ever have been a print() nobody could
    see in a windowed build."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write(f"[{timestamp}] {context}\n")


def _write(entry):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(entry)
    except OSError:
        pass
    try:
        print(entry, file=sys.stderr)
    except Exception:
        pass


def install_global_hook():
    """Last-resort safety net for anything not already wrapped in its own
    try/except (e.g. a crash during startup, before any window exists).
    This does NOT stop PyQt6 from aborting the process once a Python
    exception escapes a Qt-invoked slot -- it only guarantees the traceback
    gets written down before that happens."""

    def _hook(exc_type, exc_value, exc_tb):
        log_exception("unhandled exception", (exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
