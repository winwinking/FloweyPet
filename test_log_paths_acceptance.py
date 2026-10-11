# -*- coding: utf-8 -*-
"""Acceptance tests for the log files living under paths.LOG_DIR
(<user data dir>/logs) and for the five log modules' fault tolerance
(invalid UTF-8 in an existing log, prune of expired lines, missing log dir).
Temp dirs only -- never the real user data dir or the old C:\\FloweyPet.

Run:  python test_log_paths_acceptance.py
"""
import io
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="floweypet_logs_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")  # before importing any src module
sys.path.insert(0, str(ROOT / "src"))

import app_log  # noqa: E402
import audio_log  # noqa: E402
import dialogue_log  # noqa: E402
import error_log  # noqa: E402
import reminder_log  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


# ------------------------------------------------------------------ 1
# Paths, evaluated in subprocesses (module-level constants)
def log_paths(data_dir, reminder_env=None):
    env = dict(os.environ)
    env.pop("FLOWEYPET_REMINDER_LOG", None)
    env["FLOWEYPET_DATA_DIR"] = str(data_dir)
    if reminder_env:
        env["FLOWEYPET_REMINDER_LOG"] = reminder_env
    code = ("import sys; sys.path.insert(0, r'%s');"
            "import app_log, audio_log, dialogue_log, error_log, reminder_log;"
            "[print(m.LOG_PATH) for m in (app_log, audio_log, dialogue_log, error_log, reminder_log)]"
            % (ROOT / "src"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env).stdout.splitlines()
    return [Path(x) for x in out[:5]]


d = TMP / "pathsdata"
got = log_paths(d)
names = ["app.log", "audio.log", "dialogue.log", "error.log", "reminder.log"]
for p, n in zip(got, names):
    check(f"{n}: LOG_PATH 落在 <数据目录>/logs/ 下", p == d / "logs" / n, str(p))
override = str(TMP / "custom" / "my_reminder.log")
got2 = log_paths(d, reminder_env=override)
check("reminder_log: 设置了 FLOWEYPET_REMINDER_LOG 就用它", got2[4] == Path(override), str(got2[4]))
check("reminder_log: 环境变量不影响其他四个", got2[:4] == got[:4])
check("导入日志模块不创建 logs 目录", not (d / "logs").exists())

# ------------------------------------------------------------------ 2
# Sources no longer mention the old fixed folder
OLD = "C:" + "\\" + "FloweyPet"
for n in ("app_log", "audio_log", "dialogue_log", "error_log", "reminder_log"):
    text = (ROOT / "src" / f"{n}.py").read_text(encoding="utf-8")
    check(f"{n}.py 源码不再包含旧的固定路径", OLD not in text and "C:/FloweyPet" not in text)

# ------------------------------------------------------------------ 3
# Tolerance: invalid UTF-8 in the existing log + an expired line
old_ts = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
for mod in (app_log, audio_log, reminder_log, dialogue_log):
    name = mod.__name__
    path = TMP / f"{name}_tolerance" / "x.log"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe broken bytes\n" + f"[{old_ts}] expired line\n".encode("utf-8"))
    mod.LOG_PATH = path
    try:
        mod.log_event("NEW-LINE-MARKER")
        ok, detail = True, ""
    except Exception as e:  # noqa: BLE001
        ok, detail = False, repr(e)
    check(f"{name}: 含无效 UTF-8 的旧日志,log_event 不抛异常", ok, detail)
    text = path.read_text(encoding="utf-8", errors="replace")
    check(f"{name}: 新的一行被追加进去", "NEW-LINE-MARKER" in text)
    check(f"{name}: 过期行被删掉", "expired line" not in text)

# ------------------------------------------------------------------ 4
# error_log creates a missing logs dir
logs_dir = TMP / "fresh_logs_dir" / "logs"
error_log.LOG_PATH = logs_dir / "error.log"
with redirect_stderr(io.StringIO()):
    error_log.log_message("hello from test")
check("error_log: logs 目录被创建", logs_dir.is_dir())
check("error_log: 日志文件被创建且含内容", error_log.LOG_PATH.exists() and "hello from test" in error_log.LOG_PATH.read_text(encoding="utf-8"))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("日志路径与容错用例全部通过")
sys.exit(0)
