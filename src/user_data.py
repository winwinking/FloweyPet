# -*- coding: utf-8 -*-
"""One-time migration of config.json / stats.json from the old location
(next to the executable) into the per-user data directory.

Everything is passed in by the caller (no paths.py / registry access here)
so it can be tested against temp dirs. The legacy files are only ever read.
The marker file (migration.json) is written LAST: if anything raises midway
no marker is written and the next launch simply starts over.

Results returned:
    "imported"     first run in this data dir, legacy file(s) copied over
    "fresh"        first run in this data dir, nothing to import
    "late_import"  data dir had a "fresh" start but legacy files showed up
                   later; existing data was backed up, then overwritten
    "none"         nothing to do
"""
import json
import os
import shutil
from datetime import datetime

NAMES = ("config.json", "stats.json")
MARKER = "migration.json"


def _write_marker(data_dir, state):
    payload = {"state": state, "time": datetime.now().isoformat(timespec="seconds")}
    tmp = data_dir / (MARKER + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, data_dir / MARKER)


def _read_marker_state(data_dir):
    try:
        with open(data_dir / MARKER, "r", encoding="utf-8") as f:
            state = json.load(f).get("state")
        return state if isinstance(state, str) else None
    except (OSError, ValueError, AttributeError):
        return None


def _align_shadow(config_path, read_shadow):
    """Raise total_clicks to the registry shadow value if the copied file is
    below it (a stale/older config must not read as tampering later)."""
    shadow = read_shadow()
    if shadow is None:
        return
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(cfg, dict):
        return
    clicks = cfg.get("total_clicks")
    if isinstance(clicks, bool) or not isinstance(clicks, int) or clicks >= shadow:
        return
    cfg["total_clicks"] = shadow
    tmp = config_path.with_name(config_path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, config_path)


def migrate_legacy_data(data_dir, legacy_config, legacy_stats, read_shadow):
    data_dir.mkdir(parents=True, exist_ok=True)
    legacy = {"config.json": legacy_config, "stats.json": legacy_stats}
    legacy_present = [n for n in NAMES if legacy[n].exists()]
    has_data = any((data_dir / n).exists() for n in NAMES)

    if not has_data:
        if legacy_present:
            for n in legacy_present:
                shutil.copy2(legacy[n], data_dir / n)
            if "config.json" in legacy_present:
                _align_shadow(data_dir / "config.json", read_shadow)
            _write_marker(data_dir, "imported")
            return "imported"
        _write_marker(data_dir, "fresh")
        return "fresh"

    if _read_marker_state(data_dir) == "fresh" and legacy_present:
        backup = data_dir / ("backup_before_import_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        backup.mkdir(parents=True, exist_ok=True)
        for n in NAMES:
            if (data_dir / n).exists():
                shutil.copy2(data_dir / n, backup / n)
        for n in legacy_present:
            shutil.copy2(legacy[n], data_dir / n)
        if "config.json" in legacy_present:
            _align_shadow(data_dir / "config.json", read_shadow)
        _write_marker(data_dir, "imported")
        return "late_import"

    return "none"
