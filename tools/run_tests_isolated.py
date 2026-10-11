# -*- coding: utf-8 -*-
"""Runs the acceptance tests serially, each in its own process, with every
log file redirected into a throwaway temp dir -- so a test run can never
touch (or corrupt) the real logs in <user data dir>\logs (paths.LOG_DIR), the
log directory of a real FloweyPet install. No src/ or test file is modified: the redirect is done by
setting each log module's LOG_PATH before the test script starts.

Redirected log writers (src/):
    app_log.LOG_PATH        LOG_DIR\app.log
    audio_log.LOG_PATH      LOG_DIR\audio.log
    dialogue_log.LOG_PATH   LOG_DIR\dialogue.log
    error_log.LOG_PATH      LOG_DIR\error.log
    reminder_log.LOG_PATH   LOG_DIR\reminder.log (also via env FLOWEYPET_REMINDER_LOG,
                            which test_chase_acceptance.py otherwise points at
                            <repo>/test_acceptance_reminder.log)
config.json / stats.json (paths.CONFIG_PATH / STATS_PATH) are redirected too, via
the FLOWEYPET_DATA_DIR env var pointing at a <temp>/data subdirectory.

Usage (from the repo root):
    python tools/run_tests_isolated.py [--out DIR]     # all tests, chase last
"""
import os
import re
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_MODULES = ("app_log", "audio_log", "dialogue_log", "error_log", "reminder_log")


def _child(test_file, log_dir):
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT))
    import importlib

    for name in LOG_MODULES:
        mod = importlib.import_module(name)
        mod.LOG_PATH = Path(log_dir) / f"{name[:-4]}.log"
    sys.argv = [test_file]
    runpy.run_path(str(ROOT / test_file), run_name="__main__")


def _main(out_dir):
    tests = sorted(p.name for p in ROOT.glob("test_*.py"))
    chase = "test_chase_acceptance.py"
    tests = [t for t in tests if t != chase] + ([chase] if chase in tests else [])
    log_dir = tempfile.mkdtemp(prefix="floweypet_isolated_")
    out = Path(out_dir) if out_dir else Path(log_dir)
    out.mkdir(parents=True, exist_ok=True)
    data_dir = Path(log_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8",
               FLOWEYPET_REMINDER_LOG=str(Path(log_dir) / "reminder.log"),
               FLOWEYPET_DATA_DIR=str(data_dir))
    print(f"isolated log dir: {log_dir}")
    rows = []
    for t in tests:
        proc = subprocess.run(
            [sys.executable, __file__, "--child", t, log_dir],
            cwd=str(ROOT), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        text = proc.stdout + proc.stderr
        (out / f"isolated_{t[:-3]}.log").write_text(text, encoding="utf-8")
        n_pass = len(re.findall(r"\[PASS\]", text))
        n_fail = len(re.findall(r"\[FAIL\]|Traceback", text))
        rows.append((t, proc.returncode, n_pass, n_fail))
        print(f"{t:<42} exit={proc.returncode} PASS={n_pass} FAIL={n_fail}", flush=True)
    return 0 if all(r[1] == 0 for r in rows) else 1


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--child":
        _child(sys.argv[2], sys.argv[3])
    else:
        out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
        sys.exit(_main(out))
