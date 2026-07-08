# -*- coding: utf-8 -*-
"""Bucket-matching logic for the "记录" panel's bottom Flowey commentary
line. Bucket boundaries are program logic and live here; the wording/face
for each bucket lives in stats_roast.json so it can be edited or extended
without touching code (see that file's "_readme").

Selection order: the 2.9-style "combo" conditions (multiple stats at
once) are checked first and win outright if any matches; otherwise one of
the eight single-metric categories is chosen at random (skipping any
whose bucket doesn't apply to the current numbers) and its matching line
is shown. This keeps the panel varied across opens instead of always
leading with the same metric.
"""
import json
import random

from error_log import log_exception
from paths import STATS_ROAST_PATH

_data = None


def _load():
    global _data
    if _data is None:
        try:
            with open(STATS_ROAST_PATH, "r", encoding="utf-8") as f:
                _data = json.load(f)
        except (OSError, json.JSONDecodeError):
            log_exception(f"roast: could not load {STATS_ROAST_PATH}, commentary will be skipped")
            _data = {}
    return _data


def _pick(pool_key, bucket_key):
    pool = _load().get(pool_key, {}).get(bucket_key, [])
    return random.choice(pool) if pool else None


def _pick_flat(pool_key):
    pool = _load().get(pool_key, [])
    return random.choice(pool) if pool else None


def _substitute(entry, metrics):
    text = entry["text"]
    if "{clicks}" in text:
        text = text.replace("{clicks}", str(metrics.get("clicks", 0)))
    if "{time}" in text:
        text = text.replace("{time}", metrics.get("latest_time") or "??:??")
    return text, entry["face"]


# ------------------------------------------------------------- bucket rules
def _combo_bucket(m):
    water, meal = m.get("water_rate"), m.get("meal_rate")
    if m["hours"] >= 500 and m["clicks"] >= 5000 and m["pomodoro_count"] >= 100:
        return "high_all"
    if m["days"] >= 100 and (water or 0) >= 90 and (meal or 0) >= 90:
        return "loyal_all"
    if (
        m["hours"] >= 100
        and ((water is not None and water < 30) or (meal is not None and meal < 30))
        and m["clicks"] >= 3000
    ):
        return "click_farm"
    if m["days"] >= 60 and m["clicks"] < 100 and m["interactions"] < 50:
        return "neglected"
    if (
        m["pomodoro_count"] >= 50
        and water is not None and water < 40
        and meal is not None and meal < 40
    ):
        return "tool_only"
    return None


def _days_bucket(days):
    if days >= 1000:
        return "day_1000_plus"
    if days >= 365:
        return "day_365_plus"
    if 180 <= days <= 364:
        return "day_180_364"
    if days == 100:
        return "day_100"
    if 30 <= days <= 59:
        return "day_30_59"
    if 2 <= days <= 7:
        return "day_2_7"
    if days == 1:
        return "day_1"
    return None


def _hours_bucket(hours):
    if hours >= 1000:
        return "h_1000_plus"
    if hours >= 500:
        return "h_500_plus"
    if 100 <= hours < 500:
        return "h_100_499"
    if 20 <= hours < 100:
        return "h_20_99"
    if hours < 5:
        return "h_lt5"
    return None


def _clicks_bucket(clicks):
    if clicks >= 50000:
        return "c_50000_plus"
    if clicks >= 10000:
        return "c_10000_plus"
    if 1000 <= clicks < 5000:
        return "c_1000_4999"
    if 200 <= clicks < 1000:
        return "c_200_999"
    if clicks < 50:
        return "c_lt50"
    return None


def _pomodoro_bucket(count, days):
    if count >= 500:
        return "p_500_plus"
    if count >= 100:
        return "p_100_plus"
    if 20 <= count < 100:
        return "p_20_99"
    if 1 <= count < 20:
        return "p_1_19"
    if count == 0 and days >= 7:
        return "p_zero"
    return None


def _rate_bucket(rate):
    if rate is None:
        return None
    if rate >= 100:
        return "r_100"
    if 80 <= rate < 100:
        return "r_80_99"
    if 50 <= rate < 80:
        return "r_50_79"
    if 30 <= rate < 50:
        return "r_30_49"
    return "r_lt30"


def _egg_bucket(pct):
    if pct >= 100:
        return "e_100"
    if 80 <= pct < 100:
        return "e_80_99"
    if 50 <= pct < 80:
        return "e_50_79"
    if 20 <= pct < 50:
        return "e_20_49"
    return "e_lt20"


def _flirt_bucket(count, threshold):
    """Reserved condition slot for a dedicated flirt-count roast line: only
    fires once flirt_count has reached the configurable threshold (see
    config.json's flirt.roast_threshold_count). Wording lives under
    stats_roast.json's "flirt_count" -> "reached" bucket, left empty until
    the user writes it."""
    if threshold and count >= threshold:
        return "reached"
    return None


def _latest_active_bucket(score):
    if score is None:
        return None
    if score > 29:
        return "a_gt5"
    if score > 27:
        return "a_3_5"
    if score > 25:
        return "a_1_3"
    if score > 23:
        return "a_23_1"
    return "a_le23"


def pick_roast_line(metrics):
    """metrics keys: days, hours, clicks, pomodoro_count, interactions,
    water_rate, meal_rate (0-100 or None), egg_pct (0-100), latest_time
    ("HH:MM" or None), latest_score (float or None)."""
    combo_bucket = _combo_bucket(metrics)
    if combo_bucket:
        entry = _pick("combo", combo_bucket)
        if entry:
            return _substitute(entry, metrics)

    categories = [
        ("companion_days", _days_bucket(metrics["days"])),
        ("companion_hours", _hours_bucket(metrics["hours"])),
        ("click_count", _clicks_bucket(metrics["clicks"])),
        ("pomodoro_count", _pomodoro_bucket(metrics["pomodoro_count"], metrics["days"])),
        ("water_rate", _rate_bucket(metrics.get("water_rate"))),
        ("meal_rate", _rate_bucket(metrics.get("meal_rate"))),
        ("egg_collection", _egg_bucket(metrics["egg_pct"])),
        ("latest_active", _latest_active_bucket(metrics.get("latest_score"))),
        ("flirt_count", _flirt_bucket(metrics.get("flirt_count", 0), metrics.get("flirt_roast_threshold"))),
    ]
    valid = [(pool, bucket) for pool, bucket in categories if bucket]
    random.shuffle(valid)
    for pool, bucket in valid:
        entry = _pick(pool, bucket)
        if entry:
            return _substitute(entry, metrics)

    entry = _pick_flat("default")
    if entry:
        return _substitute(entry, metrics)
    return "数据不会骗人。", "neutral"


def open_line():
    entry = _pick_flat("panel_open")
    return (entry["text"], entry["face"]) if entry else (None, None)


def close_line():
    entry = _pick_flat("panel_close")
    return (entry["text"], entry["face"]) if entry else (None, None)
