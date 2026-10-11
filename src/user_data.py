# -*- coding: utf-8 -*-
"""One-time migration of config.json / stats.json from the old location
(next to the executable) into the per-user data directory.

Everything is passed in by the caller (no paths.py / registry access here)
so it can be tested against temp dirs. The legacy files are only ever read.

Marker file (migration.json) states: "fresh" (nothing was imported),
"importing" (copy started, not finished), "imported" (done). "importing" is
written BEFORE any copying and replaced by "imported" only after all copies
and the shadow alignment are done, so a copy that dies midway leaves
"importing" behind and the next launch finishes the job.

Results returned:
    "imported"        first run in this data dir, legacy file(s) copied over
    "fresh"           first run in this data dir, nothing to import
    "late_import"     data dir had a "fresh" start but legacy files showed up
                      later; existing data was backed up, then overwritten
    "resumed_import"  a previous import died midway ("importing" marker);
                      the legacy files were copied over again
    "none"            nothing to do
"""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

def atomic_write_json(path, data):
    """Writes `data` as JSON to `path` via a sibling .tmp file + os.replace,
    so a crash mid-write can never leave a half-written file behind. On any
    error the .tmp is removed and the original exception is re-raised."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def quarantine_bad_file(path):
    """Renames an unreadable file to <name>.bad-YYYYmmdd_HHMMSS next to it
    (kept for inspection, never deleted). Returns the new path, or None if
    the rename failed. Never raises."""
    try:
        target = path.with_name(path.name + ".bad-" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        os.replace(path, target)
        return target
    except Exception:  # noqa: BLE001
        return None


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


def autostart_exe_dir(command):
    """Folder of the exe named by an autostart registry command string
    (e.g. '"C:\\Games\\Flowey Pet\\FloweyPet.exe"'), or None. Never raises."""
    try:
        if not isinstance(command, str):
            return None
        text = command.strip()
        if not text:
            return None
        if text.startswith('"'):
            end = text.find('"', 1)
            if end == -1:
                return None
            first = text[1:end]
        else:
            first = text.split(" ", 1)[0]
        first = first.strip()
        if not first.lower().endswith(".exe"):
            return None
        return Path(first).parent
    except Exception:  # noqa: BLE001 - this must never break startup
        return None


def _has_legacy_file(folder):
    return (folder / "config.json").exists() or (folder / "stats.json").exists()


def pick_legacy_source(primary_dir, fallback_dirs):
    """(config_path, stats_path, source_dir): primary_dir if it holds either
    file, else the first usable fallback folder that does, else primary."""
    primary_dir = Path(primary_dir)
    if _has_legacy_file(primary_dir):
        return primary_dir / "config.json", primary_dir / "stats.json", primary_dir
    try:
        primary_resolved = primary_dir.resolve()
    except OSError:
        primary_resolved = primary_dir
    for folder in fallback_dirs:
        if folder is None:
            continue
        folder = Path(folder)
        try:
            if not folder.is_dir():
                continue
            if folder.resolve() == primary_resolved:
                continue
        except OSError:
            continue
        if _has_legacy_file(folder):
            return folder / "config.json", folder / "stats.json", folder
    return primary_dir / "config.json", primary_dir / "stats.json", primary_dir


def migrate_legacy_data(data_dir, legacy_config, legacy_stats, read_shadow):
    data_dir.mkdir(parents=True, exist_ok=True)
    legacy = {"config.json": legacy_config, "stats.json": legacy_stats}
    legacy_present = [n for n in NAMES if legacy[n].exists()]
    has_data = any((data_dir / n).exists() for n in NAMES)

    def copy_and_finish():
        for n in legacy_present:
            shutil.copy2(legacy[n], data_dir / n)
        if "config.json" in legacy_present:
            _align_shadow(data_dir / "config.json", read_shadow)
        _write_marker(data_dir, "imported")

    if not has_data:
        if legacy_present:
            _write_marker(data_dir, "importing")
            copy_and_finish()
            return "imported"
        _write_marker(data_dir, "fresh")
        return "fresh"

    state = _read_marker_state(data_dir)

    if state == "fresh" and legacy_present:
        backup = data_dir / ("backup_before_import_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        backup.mkdir(parents=True, exist_ok=True)
        for n in NAMES:
            if (data_dir / n).exists():
                shutil.copy2(data_dir / n, backup / n)
        _write_marker(data_dir, "importing")
        copy_and_finish()
        return "late_import"

    if state == "importing":
        if legacy_present:
            copy_and_finish()
            return "resumed_import"
        # Legacy files are gone: keep whatever is in data_dir and close the
        # marker, so legacy files reappearing later can't overwrite progress.
        _write_marker(data_dir, "imported")
        return "none"

    return "none"
