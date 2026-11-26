# -*- coding: utf-8 -*-
"""Acceptance tests for the one-time 3.3.0 flirt_count reset
(stats.Stats._migrate_flirt_schema). stats.STATS_PATH is pointed at a temp
dir; the real dev_data / %APPDATA% are never touched.

Run:  QT_QPA_PLATFORM=offscreen python test_flirt_reset_acceptance.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="floweypet_flirtreset_"))
DATA = TMP / "data"
os.environ["FLOWEYPET_DATA_DIR"] = str(DATA)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import json  # noqa: E402

from PyQt6.QtCore import QCoreApplication  # noqa: E402

app = QCoreApplication(sys.argv)

import error_log  # noqa: E402

error_log.LOG_PATH = TMP / "error.log"

import paths  # noqa: E402
import stats  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


STATS_FILE = DATA / "stats.json"
stats.STATS_PATH = STATS_FILE
paths.STATS_PATH = STATS_FILE
check("读写的是本测试自己的临时目录", STATS_FILE.parent == DATA and str(DATA).startswith(tempfile.gettempdir()))


def fresh():
    if DATA.exists():
        for p in DATA.iterdir():
            p.unlink()
    DATA.mkdir(parents=True, exist_ok=True)


def load(raw=None):
    fresh()
    if raw is not None:
        STATS_FILE.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    s = stats.Stats({})
    s._companion_timer.stop()
    s._autosave_timer.stop()
    return s, json.loads(STATS_FILE.read_text(encoding="utf-8"))


check("DEFAULTS 里 flirt_stats_schema 默认 1", stats.DEFAULTS.get("flirt_stats_schema") == 1)

# 1. old data, no schema key
s, disk = load({"flirt_count": 37})
check("旧数据(无记号, 37): 内存里 flirt_count 为 0", s.data["flirt_count"] == 0, str(s.data["flirt_count"]))
check("旧数据: 内存里 flirt_stats_schema 为 2", s.data["flirt_stats_schema"] == 2)
check("旧数据: 文件里 flirt_count 为 0", disk.get("flirt_count") == 0, str(disk.get("flirt_count")))
check("旧数据: 文件里 flirt_stats_schema 为 2", disk.get("flirt_stats_schema") == 2)

# 1b. explicit schema 1 also resets
s, disk = load({"flirt_count": 9, "flirt_stats_schema": 1})
check("显式 schema 1 同样清零", s.data["flirt_count"] == 0 and disk["flirt_count"] == 0)

# 2. already migrated
s, disk = load({"flirt_count": 5, "flirt_stats_schema": 2})
check("已是 schema 2(5): 加载后仍为 5", s.data["flirt_count"] == 5 and disk["flirt_count"] == 5)
check("已是 schema 2: 记号仍为 2", s.data["flirt_stats_schema"] == 2 and disk["flirt_stats_schema"] == 2)

# 3. only once: after reset, counting up then reloading must not reset again
s, disk = load({"flirt_count": 37})
s.record_flirt()
s.record_flirt()
check("清零后继续调情累加", s.data["flirt_count"] == 2)
s2 = stats.Stats({})
s2._companion_timer.stop()
s2._autosave_timer.stop()
check("再次加载不再清零", s2.data["flirt_count"] == 2, str(s2.data["flirt_count"]))

# 4. other fields untouched
other = {
    "flirt_count": 37,
    "reminder_rounds": {"water": 7, "meal": 3, "sleep": 1},
    "reminder_responded": {"water": 5, "meal": 2, "sleep": 1},
    "reminder_perfunctory": {"water": 0, "meal": 0, "sleep": 0},
    "reminder_stats_schema": 2,
    "rare_seen": ["a", "b", "c"],
    "interaction_count": 123,
    "companion_minutes": 456,
    "ut_first_seen": True,
}
s, disk = load(other)
same = all(s.data[k] == v for k, v in other.items() if k != "flirt_count")
check("清零不影响 reminder_rounds / rare_seen / 其他字段(内存)", same)
check("清零不影响其他字段(文件)", all(disk[k] == v for k, v in other.items() if k != "flirt_count"))
check("同时 flirt_count 确实被清零", s.data["flirt_count"] == 0)

# 5. brand-new user
s, disk = load(None)
check("全新用户: flirt_count 为 0", s.data["flirt_count"] == 0)
check("全新用户: flirt_stats_schema 为 2", s.data["flirt_stats_schema"] == 2)
check("全新用户: 文件里也是 2 / 0", disk.get("flirt_stats_schema") == 2 and disk.get("flirt_count") == 0)

# 6. log line mentions old value
log_text = (TMP / "error.log").read_text(encoding="utf-8") if (TMP / "error.log").exists() else ""
check("日志记下了原来的数值 37", "flirt_count=37" in log_text, log_text[-300:])

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("调情次数清零用例全部通过")
sys.exit(0)
