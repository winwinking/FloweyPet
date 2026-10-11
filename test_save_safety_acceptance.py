# -*- coding: utf-8 -*-
"""Acceptance tests for safe config.json / stats.json I/O: atomic writes
(user_data.atomic_write_json), quarantining unreadable files
(quarantine_bad_file), load_config/_load fallbacks, and save failures that
must not propagate. Everything runs against a temp FLOWEYPET_DATA_DIR set
BEFORE paths/config/stats are imported, and error_log's LOG_PATH is pointed
at the temp dir too -- never the real registry, user data or C:\\FloweyPet.

Run:  QT_QPA_PLATFORM=offscreen python test_save_safety_acceptance.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="floweypet_safe_"))
DATA = TMP / "data"
os.environ["FLOWEYPET_DATA_DIR"] = str(DATA)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import io  # noqa: E402
import json  # noqa: E402
from contextlib import redirect_stderr  # noqa: E402

from PyQt6.QtCore import QCoreApplication  # noqa: E402

app = QCoreApplication(sys.argv)

import error_log  # noqa: E402

error_log.LOG_PATH = TMP / "error.log"

import config  # noqa: E402
import paths  # noqa: E402
import stats  # noqa: E402
import user_data  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def clean():
    if DATA.exists():
        for p in DATA.iterdir():
            p.unlink()


def bads(name):
    return sorted(DATA.glob(name + ".bad-*"))


# Redirect explicitly instead of relying on import order: under
# tools/run_tests_isolated.py the log modules (which import paths) are loaded
# before this file runs, so paths may already be resolved to the runner's dir.
config.CONFIG_PATH = DATA / "config.json"
stats.STATS_PATH = DATA / "stats.json"
paths.CONFIG_PATH = config.CONFIG_PATH
paths.STATS_PATH = stats.STATS_PATH
check("读写的数据目录是本测试自己的临时目录", config.CONFIG_PATH.parent == DATA and stats.STATS_PATH.parent == DATA)

# ------------------------------------------------------------------ 1
# Bad JSON / non-dict JSON, config and stats alike
cases = [
    ("config", config.load_config, paths.CONFIG_PATH, config.DEFAULTS),
    ("stats", stats._load, paths.STATS_PATH, stats.DEFAULTS),
]
for label, loader, path, defaults in cases:
    for kind, raw in (("坏 JSON", b"{ this is : not json \xe4\xb8\xad"), ("非 dict 的合法 JSON", b"[1, 2]"),
                      ("无效 UTF-8", b"\xff\xfe\x00bad")):
        clean()
        DATA.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        try:
            got = loader()
            ok = True
        except Exception as e:  # noqa: BLE001
            got, ok = repr(e), False
        check(f"{label} {kind}: 不崩溃", ok, str(got))
        if ok:
            check(f"{label} {kind}: 返回默认值", got == defaults)
        found = bads(path.name)
        check(f"{label} {kind}: 原文件被改名为 .bad-时间戳", len(found) == 1, str(found))
        check(f"{label} {kind}: 改名后内容与原来一模一样", bool(found) and found[0].read_bytes() == raw)
        check(f"{label} {kind}: 原位置不再有坏文件", not path.exists())

# missing file -> defaults, nothing created
clean()
check("config 文件不存在: 返回默认值且不创建文件", config.load_config() == config.DEFAULTS and not paths.CONFIG_PATH.exists())
check("stats 文件不存在: 返回默认值且不创建文件", stats._load() == stats.DEFAULTS and not paths.STATS_PATH.exists())

# ------------------------------------------------------------------ 2
# Returned configs are deep copies of DEFAULTS
nested_keys = [k for k, v in config.DEFAULTS.items() if isinstance(v, (dict, list))]
check("DEFAULTS 里确实有嵌套结构可供测试", bool(nested_keys), "no nested defaults")
snapshot = json.dumps(config.DEFAULTS, sort_keys=True)
for source in ("文件不存在", "坏文件"):
    clean()
    if source == "坏文件":
        DATA.mkdir(parents=True, exist_ok=True)
        paths.CONFIG_PATH.write_text("{oops", encoding="utf-8")
    c = config.load_config()
    for k in nested_keys:
        v = c[k]
        if isinstance(v, dict):
            v["__poke__"] = 1
        else:
            v.append("__poke__")
    check(f"改动 load_config 返回的嵌套值({source}): DEFAULTS 不受影响",
          json.dumps(config.DEFAULTS, sort_keys=True) == snapshot)
clean()
s1 = stats._load()
for k, v in s1.items():
    if isinstance(v, dict):
        v["__poke__"] = 1
    elif isinstance(v, list):
        v.append("__poke__")
check("改动 stats._load 返回的嵌套值: stats.DEFAULTS 不受影响", "__poke__" not in json.dumps(stats.DEFAULTS))

# ------------------------------------------------------------------ 3
# atomic_write_json
target = TMP / "newdir" / "deeper" / "x.json"
user_data.atomic_write_json(target, {"a": 1, "名字": "中文"})
check("atomic_write_json: 目录不存在时自动创建", target.exists())
check("atomic_write_json: 内容正确", json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "名字": "中文"})
check("atomic_write_json: 没有 .tmp 残留", not list(target.parent.glob("*.tmp")))
check("atomic_write_json: 中文不被转义", "中文" in target.read_text(encoding="utf-8"))

orig = {"keep": "me"}
user_data.atomic_write_json(target, orig)
real_replace = os.replace


def boom(*a, **k):
    raise OSError("simulated replace failure")


os.replace = boom
try:
    raised = False
    try:
        user_data.atomic_write_json(target, {"new": "data"})
    except OSError:
        raised = True
finally:
    os.replace = real_replace
check("os.replace 失败: 异常抛出", raised)
check("os.replace 失败: 原文件内容不变", json.loads(target.read_text(encoding="utf-8")) == orig)
check("os.replace 失败: 没有 .tmp 残留", not list(target.parent.glob("*.tmp")))


class NotSerializable:
    pass


raised = False
try:
    user_data.atomic_write_json(target, {"x": NotSerializable()})
except TypeError:
    raised = True
check("序列化失败: 异常抛出、原文件不变、无 .tmp",
      raised and json.loads(target.read_text(encoding="utf-8")) == orig and not list(target.parent.glob("*.tmp")))

# quarantine_bad_file
victim = TMP / "victim.json"
victim.write_bytes(b"junk")
moved = user_data.quarantine_bad_file(victim)
check("quarantine_bad_file: 返回新路径,名字带 .bad-", moved is not None and ".bad-" in moved.name and moved.read_bytes() == b"junk")
check("quarantine_bad_file: 原文件已不在", not victim.exists())
try:
    r = user_data.quarantine_bad_file(TMP / "does_not_exist.json")
    check("quarantine_bad_file: 失败返回 None 且不抛异常", r is None)
except Exception as e:  # noqa: BLE001
    check("quarantine_bad_file: 失败返回 None 且不抛异常", False, repr(e))

# ------------------------------------------------------------------ 4
# save_config / Stats.save failure handling
clean()
os.replace = boom
try:
    try:
        with redirect_stderr(io.StringIO()):  # error_log echoes tracebacks to stderr
            config.save_config({"a": 1})
        ok = True
    except Exception as e:  # noqa: BLE001
        ok = False
        detail = repr(e)
finally:
    os.replace = real_replace
check("save_config 写入失败: 不抛异常", ok, locals().get("detail", ""))
check("save_config 写入失败: 失败被记进 error.log", error_log.LOG_PATH.exists() and "save_config" in error_log.LOG_PATH.read_text(encoding="utf-8"))
check("save_config 写入失败: 没有残留 .tmp", not list(DATA.glob("*.tmp")))

st = stats.Stats({})
os.replace = boom
try:
    try:
        with redirect_stderr(io.StringIO()):
            st.save()
        ok = True
    except Exception as e:  # noqa: BLE001
        ok = False
finally:
    os.replace = real_replace
check("Stats.save 写入失败: 不抛异常", ok)
check("Stats.save 写入失败: 失败被记进 error.log", "stats.save" in error_log.LOG_PATH.read_text(encoding="utf-8"))
st._companion_timer.stop()
st._autosave_timer.stop()

# ------------------------------------------------------------------ 5
# Round trips
clean()
cfg = config.load_config()
cfg["custom_text"] = "中文，不转义 ✓"
cfg["total_clicks"] = 4242
cfg["pomodoro"] = dict(cfg.get("pomodoro", {}), marker="m")
config.save_config(cfg)
check("config 保存后文件里中文不被转义", "中文，不转义" in paths.CONFIG_PATH.read_text(encoding="utf-8"))
back = config.load_config()
check("config 往返: 值一致", back["custom_text"] == "中文，不转义 ✓" and back["total_clicks"] == 4242 and back["pomodoro"]["marker"] == "m")
check("config 往返: 没有 .tmp 残留、没有 .bad 文件", not list(DATA.glob("*.tmp")) and not bads("config.json"))

clean()
st = stats.Stats({})
st.data["interaction_count"] = 77
st.data["note"] = "中文统计"
st.save()
st._companion_timer.stop()
st._autosave_timer.stop()
check("stats 保存后文件里中文不被转义", "中文统计" in paths.STATS_PATH.read_text(encoding="utf-8"))
back = stats._load()
check("stats 往返: 值一致", back["interaction_count"] == 77 and back["note"] == "中文统计")
check("stats 往返: 没有 .tmp 残留、没有 .bad 文件", not list(DATA.glob("*.tmp")) and not bads("stats.json"))

# ------------------------------------------------------------------ 6
# os.replace retry on PermissionError
retry_target = TMP / "retry" / "r.json"
user_data.atomic_write_json(retry_target, {"v": "old"})
pauses = []
real_sleep = user_data.time.sleep
user_data.time.sleep = lambda s: pauses.append(s)


def replace_failing(n_failures, exc):
    calls = {"n": 0}

    def fake(src, dst):
        calls["n"] += 1
        if calls["n"] <= n_failures:
            raise exc("simulated")
        return real_replace(src, dst)
    return fake, calls


try:
    fake, calls = replace_failing(2, PermissionError)
    os.replace = fake
    try:
        user_data.atomic_write_json(retry_target, {"v": "两次失败后成功"})
        ok = True
    except Exception as e:  # noqa: BLE001
        ok = False
    finally:
        os.replace = real_replace
    check("replace 前两次 PermissionError、第三次成功: 写入成功", ok)
    check("重试: 内容正确", json.loads(retry_target.read_text(encoding="utf-8")) == {"v": "两次失败后成功"})
    check("重试: 共调用 3 次 os.replace", calls["n"] == 3, str(calls))
    check("重试: 两次间隔分别为 0.05 / 0.1 秒", pauses == [0.05, 0.1], str(pauses))
    check("重试: 无 .tmp 残留", not list(retry_target.parent.glob("*.tmp")))

    pauses.clear()
    fake, calls = replace_failing(99, PermissionError)
    os.replace = fake
    try:
        raised = None
        try:
            user_data.atomic_write_json(retry_target, {"v": "never"})
        except PermissionError as e:
            raised = e
    finally:
        os.replace = real_replace
    check("replace 一直 PermissionError: 抛出 PermissionError", raised is not None)
    check("一直失败: 恰好尝试 3 次", calls["n"] == 3, str(calls))
    check("一直失败: 原文件内容不变", json.loads(retry_target.read_text(encoding="utf-8")) == {"v": "两次失败后成功"})
    check("一直失败: 无 .tmp 残留", not list(retry_target.parent.glob("*.tmp")))

    pauses.clear()
    fake, calls = replace_failing(99, OSError)
    os.replace = fake
    try:
        raised = None
        try:
            user_data.atomic_write_json(retry_target, {"v": "never"})
        except OSError as e:
            raised = e
    finally:
        os.replace = real_replace
    check("replace 抛普通 OSError: 直接抛出", raised is not None and not isinstance(raised, PermissionError))
    check("普通 OSError: 不重试(调用 1 次、不 sleep)", calls["n"] == 1 and pauses == [], f"{calls} {pauses}")
    check("普通 OSError: 原文件不变且无 .tmp",
          json.loads(retry_target.read_text(encoding="utf-8")) == {"v": "两次失败后成功"}
          and not list(retry_target.parent.glob("*.tmp")))
finally:
    user_data.time.sleep = real_sleep
    os.replace = real_replace

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("配置/统计安全读写用例全部通过")
sys.exit(0)
