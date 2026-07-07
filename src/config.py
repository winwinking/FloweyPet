# -*- coding: utf-8 -*-
"""config.json load/save with defaults. All persisted user-facing settings
live here: window position, volume, font scale, pomodoro/reminder options,
autostart, and the small bits of state the dialogue system needs to
remember across restarts (streak days, last interaction, etc).
"""
import json
import sys
import winreg
from pathlib import Path

from paths import CONFIG_PATH

DEFAULTS = {
    "position": None,  # None => PetWindow picks a sensible on-screen default
    "volume": 0.8,
    "muted": False,
    "menu_sfx_enabled": True,  # independent of "muted": only gates menu move/select blips
    "font_scale": 1.75,
    "always_on_top": True,
    "autostart": False,
    "first_run_done": False,
    "birthday": None,          # "MM-DD" or null
    "install_date": None,      # "YYYY-MM-DD"
    "last_active_date": None,  # "YYYY-MM-DD", for streak tracking
    "streak_days": 0,
    "total_clicks": 0,
    "last_close_time": None,   # ISO timestamp
    "close_count_recent": 0,
    "greeted_streaks": [],     # which streak milestones already shown
    "greeted_birthday_date": None,
    "pomodoro": {
        "focus_minutes": 25,
        "break_minutes": 5,
    },
    "reminders": {
        "water": {"enabled": True, "interval_minutes": 45},
        "meal": {"enabled": True, "times": ["12:00", "18:00"]},
        "sleep": {"enabled": True, "times": ["23:00"]},
    },
    "pomodoro_chat_min_minutes": 5,
    "pomodoro_chat_max_minutes": 10,
    "neglect_after_minutes": 120,
    "reminder_pursuit_stages_minutes": [5, 10, 20, 30],
    "reminder_pursuit_hold_seconds": 60,
    "reminder_test_mode": False,  # dev-only: compresses reminder/pursuit timing to seconds, see reminders.py docstring
    "global_min_interval_seconds": 180,
    "reunion_after_minutes": 180,
    "click_deep_unlock_clicks": 15,
    "day_boundary_hour": 6,          # 0:00-this hour counts as "still last night" for the stats panel
    "session_gap_minutes": 90,        # activity gaps longer than this start a new session
    "morning_greet_probability": 0.2,  # odds of the "起这么早" line on a genuinely fresh early-morning boot
    "version": "1.0.0",  # shown in the "查看信息" easter egg via {ver}; edit by hand to bump it
    "info_easter_egg": {
        "weights": {"normal_intro": 40, "fourth_wall": 30, "nonsense": 20, "rare": 10},
        "anti_spam_window_seconds": 60,
        "anti_spam_trigger_count": 4,
    },
}


def _deep_merge(defaults, loaded):
    out = dict(defaults)
    for k, v in loaded.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            return _deep_merge(DEFAULTS, loaded)
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULTS)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "FloweyPet"


def set_autostart(enabled: bool):
    target = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        command = f'"{target}"'
    else:
        main_py = Path(__file__).resolve().parent.parent / "main.py"
        command = f'"{target}" "{main_py}"'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, _RUN_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        pass
