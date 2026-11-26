# -*- coding: utf-8 -*-
"""Where the API chat key lives: USER_DATA_DIR/api_key.txt, one line of
UTF-8 plain text. Deliberately NOT in config.json -- config.json is copied
by the legacy-data migration, rewritten wholesale on every save and is the
file people attach to bug reports.

Nothing here ever logs a key: on a failure only a fixed sentence and the
exception class name go to app.log (exception *messages* are never logged --
a Windows error message can carry a path, and a careless one could carry
data).

The location is read from KEY_PATH at call time, so tests can point it at a
temp dir.
"""
import app_log
from paths import API_KEY_PATH
from user_data import atomic_write_text

KEY_PATH = API_KEY_PATH


def _log(what, exc):
    try:
        app_log.log_event(f"API 钥匙{what}失败：{type(exc).__name__}")
    except Exception:  # noqa: BLE001 - logging must never break the caller
        pass


def load_key():
    """The stored key, or "" if there is none / it can't be read / it is
    empty. Never raises."""
    try:
        with open(KEY_PATH, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as e:  # ValueError: UnicodeDecodeError
        _log("读取", e)
        return ""
    lines = text.strip().splitlines()
    return lines[0].strip() if lines else ""


def has_key():
    return bool(load_key())


def save_key(key):
    """Stores `key` (surrounding whitespace removed). An empty key deletes
    the file. True on success, False if the disk refused. Never raises."""
    key = key.strip() if isinstance(key, str) else ""
    if not key:
        return delete_key()
    try:
        atomic_write_text(KEY_PATH, key)
        return True
    except Exception as e:  # noqa: BLE001
        _log("保存", e)
        return False


def delete_key():
    """Removes the key file. True if it is gone afterwards. Never raises."""
    try:
        KEY_PATH.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        _log("保存", e)
        return False
    return True
