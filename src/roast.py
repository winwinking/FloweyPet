# -*- coding: utf-8 -*-
"""Bucket-matching + combo-condition logic for the "记录" panel's bottom
Flowey commentary line. Bucket boundaries for the eight single-metric
categories are still program logic (this file) with wording in
stats_roast.json; the "combo" pool (multiple stats considered together)
is a fully data-driven conditions engine instead: every entry's
"conditions" (field/op/value triples, all AND'd together) lives entirely
in the JSON, so new combo roasts can be added by editing stats_roast.json
alone -- no code changes needed.

Selection order: combo entries are checked first. Every entry whose
conditions all pass is a candidate; one is chosen by weighted random
among candidates (default weight 1) so multiple simultaneous matches stay
varied across panel opens instead of always leading with the same one.
If no combo entry matches, one of the eight single-metric categories is
chosen at random (skipping any whose bucket doesn't apply to the current
numbers) as before.
"""
import json
import random

from error_log import log_exception, log_message
from paths import STATS_ROAST_PATH

_data = None

_OPS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}

# Fields usable inside a combo entry's "conditions" and as {placeholder}s in
# its "lines" -- see stats_roast.json's "_readme". "egg_rate" is a derived
# 0-100 percentage (eggs_seen/eggs_total*100), the documented stand-in for a
# direct eggs_seen==eggs_total field-to-field comparison.
_CONDITION_FIELDS = (
    "days", "hours", "clicks", "interactions", "dialogues", "pomodoro",
    "flirts", "water_rate", "food_rate", "eggs_seen", "eggs_total",
    "egg_rate", "latest_hour",
)

# Placeholders whose raw value shouldn't just be str()'d.
_PLACEHOLDER_FORMAT = {
    "hours": lambda v: f"{v:.1f}",
    "water_rate": lambda v: f"{v:.0f}",
    "food_rate": lambda v: f"{v:.0f}",
    "egg_rate": lambda v: f"{v:.0f}",
}


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


# --------------------------------------------------------- combo (conditions)
def _condition_fields(metrics):
    """Maps the internal metrics dict (see pet_window._collect_stats_metrics)
    onto the field names combo conditions/placeholders are written against.
    A rate/count that has no data yet stays None so _entry_matches treats
    any condition on it as a non-match instead of crashing or guessing."""
    latest_hour = None
    latest_time = metrics.get("latest_time")
    if latest_time:
        try:
            latest_hour = int(str(latest_time).split(":")[0])
        except (ValueError, IndexError):
            latest_hour = None

    eggs_total = metrics.get("egg_total") or 0
    egg_rate = metrics.get("egg_pct")
    if egg_rate is None and eggs_total:
        egg_rate = metrics.get("egg_seen", 0) / eggs_total * 100

    return {
        "days": metrics.get("days", 0),
        "hours": metrics.get("hours", 0),
        "clicks": metrics.get("clicks", 0),
        "interactions": metrics.get("interactions", 0),
        "dialogues": metrics.get("dialogues", 0),
        "pomodoro": metrics.get("pomodoro_count", 0),
        "flirts": metrics.get("flirt_count", 0),
        "water_rate": metrics.get("water_rate"),
        "food_rate": metrics.get("meal_rate"),
        "eggs_seen": metrics.get("egg_seen", 0),
        "eggs_total": eggs_total,
        "egg_rate": egg_rate,
        "latest_hour": latest_hour,
    }


def _validate_condition(cond):
    if not isinstance(cond, dict):
        return False
    field = cond.get("field")
    op = cond.get("op")
    value = cond.get("value")
    if field not in _CONDITION_FIELDS:
        return False
    if op not in _OPS:
        return False
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return True


def _validate_combo_entry(entry):
    """Returns (ok, reason). A malformed hand-edited entry is skipped and
    logged, never raised -- see the module docstring."""
    if not isinstance(entry, dict):
        return False, "entry is not a JSON object"
    conditions = entry.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return False, "'conditions' must be a non-empty array"
    for cond in conditions:
        if not _validate_condition(cond):
            return False, f"malformed condition {cond!r}"
    lines = entry.get("lines")
    faces = entry.get("faces")
    if not isinstance(lines, list) or not lines or not all(isinstance(t, str) and t for t in lines):
        return False, "'lines' must be a non-empty array of non-empty strings"
    if not isinstance(faces, list) or len(faces) != len(lines):
        return False, "'faces' must be an array the same length as 'lines'"
    for slot in faces:
        if not isinstance(slot, list) or not slot or not all(isinstance(tag, str) and tag for tag in slot):
            return False, "each 'faces' slot must be a non-empty array of face-tag strings"
    weight = entry.get("weight", 1)
    if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
        return False, "'weight' must be a positive number"
    return True, ""


def _combo_entries():
    raw = _load().get("combo", [])
    if not isinstance(raw, list):
        log_message("roast: 'combo' must be an array, skipping the whole pool")
        return []
    valid = []
    for i, entry in enumerate(raw):
        ok, reason = _validate_combo_entry(entry)
        if not ok:
            log_message(f"roast: skipping malformed combo entry [{i}]: {reason}")
            continue
        valid.append(entry)
    return valid


def _entry_matches(conditions, fields):
    for cond in conditions:
        actual = fields.get(cond["field"])
        if actual is None:
            return False  # missing/no-data-yet metric never satisfies a condition
        if not _OPS[cond["op"]](actual, cond["value"]):
            return False
    return True


def _substitute_combo_text(text, fields):
    for field in _CONDITION_FIELDS:
        token = "{" + field + "}"
        if token not in text:
            continue
        value = fields.get(field)
        if value is None:
            continue  # leave unknown/no-data placeholders exactly as-is
        fmt = _PLACEHOLDER_FORMAT.get(field)
        text = text.replace(token, fmt(value) if fmt else str(value))
    return text


def _combo_segments(entry, fields):
    segments = []
    for line, faces in zip(entry["lines"], entry["faces"]):
        face = faces[0] if len(faces) == 1 else list(faces)
        segments.append({"text": _substitute_combo_text(line, fields), "face": face})
    return segments


def _pick_combo(metrics):
    fields = _condition_fields(metrics)
    matched = [e for e in _combo_entries() if _entry_matches(e["conditions"], fields)]
    if not matched:
        return None
    weights = [e.get("weight", 1) for e in matched]
    entry = random.choices(matched, weights=weights, k=1)[0]
    return _combo_segments(entry, fields)


# ------------------------------------------------------------- bucket rules
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
    dialogues, water_rate, meal_rate (0-100 or None), egg_pct (0-100),
    latest_time ("HH:MM" or None), latest_score (float or None).

    Returns a list of {"text","face"} segments (never empty), ready to be
    appended straight onto a dialogue box's segment list."""
    combo_segments = _pick_combo(metrics)
    if combo_segments:
        return combo_segments

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
            text, face = _substitute(entry, metrics)
            return [{"text": text, "face": face}]

    entry = _pick_flat("default")
    if entry:
        text, face = _substitute(entry, metrics)
        return [{"text": text, "face": face}]
    return [{"text": "数据不会骗人。", "face": "neutral"}]


def open_line():
    entry = _pick_flat("panel_open")
    return (entry["text"], entry["face"]) if entry else (None, None)


def close_line():
    entry = _pick_flat("panel_close")
    return (entry["text"], entry["face"]) if entry else (None, None)
