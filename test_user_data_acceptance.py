# -*- coding: utf-8 -*-
"""Acceptance tests for the user-data-dir move: paths.USER_DATA_DIR
resolution (in subprocesses) and user_data.migrate_legacy_data's cases.
Only temp dirs and an in-memory fake read_shadow -- never the real registry,
APPDATA, or the repo-root config.json.

Run:  python test_user_data_acceptance.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from user_data import migrate_legacy_data  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def sandbox():
    root = Path(tempfile.mkdtemp(prefix="floweypet_ud_"))
    legacy = root / "legacy"
    legacy.mkdir()
    return root / "data", legacy / "config.json", legacy / "stats.json", root


def marker(data):
    return json.loads((data / "migration.json").read_text(encoding="utf-8"))


def run(data, lc, ls, shadow=None):
    return migrate_legacy_data(data, lc, ls, lambda: shadow)


def read_json(p):
    return json.loads(p.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 1
# paths.py in a subprocess (module-level constants)
def paths_in_subprocess(env_extra, remove=()):
    env = dict(os.environ)
    for k in remove:
        env.pop(k, None)
    env.update(env_extra)
    code = ("import sys; sys.path.insert(0, r'%s'); import paths; "
            "print(paths.CONFIG_PATH); print(paths.STATS_PATH); print(paths.USER_DATA_DIR); "
            "print(paths.LEGACY_CONFIG_PATH); print(paths.LEGACY_STATS_PATH); print(paths.APP_DIR)"
            % (ROOT / "src"))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env).stdout.splitlines()
    return [Path(x) for x in out[:6]]


d = Path(tempfile.mkdtemp(prefix="floweypet_ud_env_")) / "mydata"
cfg, st, udd, lcfg, lst, app = paths_in_subprocess({"FLOWEYPET_DATA_DIR": str(d)})
check("FLOWEYPET_DATA_DIR: CONFIG_PATH 落在该目录", cfg == d / "config.json", str(cfg))
check("FLOWEYPET_DATA_DIR: STATS_PATH 落在该目录", st == d / "stats.json", str(st))
check("LEGACY 路径仍在 APP_DIR", lcfg == app / "config.json" and lst == app / "stats.json")
check("paths.py 只定义路径,不创建目录", not d.exists())
cfg, st, udd, lcfg, lst, app = paths_in_subprocess({}, remove=("FLOWEYPET_DATA_DIR",))
check("源码运行且无环境变量: 用 APP_DIR/dev_data", udd == app / "dev_data", str(udd))

# ------------------------------------------------------------------ 2
# Case A with legacy files
data, lc, ls, root = sandbox()
lc.write_text('{"total_clicks": 5, "x": "中文"}', encoding="utf-8")
ls.write_text('{"interaction_count": 3}', encoding="utf-8")
before = (lc.read_bytes(), ls.read_bytes())
r = run(data, lc, ls)
check("A 有旧文件: 返回 imported", r == "imported", r)
check("A 有旧文件: config 内容一致", (data / "config.json").read_bytes() == before[0])
check("A 有旧文件: stats 内容一致", (data / "stats.json").read_bytes() == before[1])
check("A 有旧文件: 标记为 imported 且带时间", marker(data)["state"] == "imported" and bool(marker(data)["time"]))
check("A 有旧文件: 旧文件仍在且未变", lc.exists() and ls.exists() and (lc.read_bytes(), ls.read_bytes()) == before)

data, lc, ls, root = sandbox()
ls.write_text('{"a": 1}', encoding="utf-8")
r = run(data, lc, ls)
check("A 只有旧 stats: 只复制存在的那个",
      r == "imported" and (data / "stats.json").exists() and not (data / "config.json").exists())

# ------------------------------------------------------------------ 3
# Case A without legacy files
data, lc, ls, root = sandbox()
r = run(data, lc, ls)
check("A 没旧文件: 返回 fresh,标记 fresh", r == "fresh" and marker(data)["state"] == "fresh")
check("A 没旧文件: 没有凭空生成 config/stats",
      not (data / "config.json").exists() and not (data / "stats.json").exists())

# ------------------------------------------------------------------ 4
# Case B: fresh start, then legacy files appear
data, lc, ls, root = sandbox()
run(data, lc, ls)
(data / "config.json").write_text('{"total_clicks": 1, "new": true}', encoding="utf-8")
(data / "stats.json").write_text('{"s": "new"}', encoding="utf-8")
lc.write_text('{"total_clicks": 99}', encoding="utf-8")
ls.write_text('{"s": "old"}', encoding="utf-8")
r = run(data, lc, ls)
check("B: 返回 late_import", r == "late_import", r)
backups = list(data.glob("backup_before_import_*"))
check("B: 生成一个备份目录", len(backups) == 1, str(backups))
if backups:
    check("B: 备份里是导入前的数据",
          read_json(backups[0] / "config.json").get("new") is True
          and read_json(backups[0] / "stats.json") == {"s": "new"})
check("B: 新数据是旧文件内容",
      read_json(data / "config.json") == {"total_clicks": 99} and read_json(data / "stats.json") == {"s": "old"})
check("B: 标记变 imported", marker(data)["state"] == "imported")
check("B: 旧文件仍在未变", lc.read_text(encoding="utf-8") == '{"total_clicks": 99}')

# ------------------------------------------------------------------ 5
# After an import, newer legacy files must not overwrite again
lc.write_text('{"total_clicks": 12345}', encoding="utf-8")
r = run(data, lc, ls)
check("导入过后再放旧文件: 返回 none", r == "none", r)
check("导入过后再放旧文件: 不覆盖", read_json(data / "config.json") == {"total_clicks": 99})
check("导入过后再放旧文件: 没有新备份", len(list(data.glob("backup_before_import_*"))) == 1)

# ------------------------------------------------------------------ 6
# Data but no marker
data, lc, ls, root = sandbox()
data.mkdir()
(data / "config.json").write_text('{"keep": 1}', encoding="utf-8")
lc.write_text('{"total_clicks": 7}', encoding="utf-8")
r = run(data, lc, ls)
check("已有数据但没有标记: 返回 none", r == "none", r)
check("已有数据但没有标记: 不动数据也不写标记",
      read_json(data / "config.json") == {"keep": 1} and not (data / "migration.json").exists())


# ------------------------------------------------------------------ 7
# Shadow alignment
def aligned(file_clicks, shadow):
    data, lc, ls, root = sandbox()
    lc.write_text(json.dumps({"total_clicks": file_clicks, "k": "v"}), encoding="utf-8")
    run(data, lc, ls, shadow=shadow)
    return read_json(data / "config.json"), lc


got, lc_ = aligned(10, 500)
check("影子值对齐: shadow 大于文件值 -> 改成 shadow", got["total_clicks"] == 500 and got["k"] == "v", str(got))
check("影子值对齐: 旧文件未被改", read_json(lc_)["total_clicks"] == 10)
got, _ = aligned(900, 500)
check("影子值对齐: shadow 小于文件值 -> 不动", got["total_clicks"] == 900, str(got))
got, _ = aligned(500, 500)
check("影子值对齐: 相等 -> 不动", got["total_clicks"] == 500, str(got))
got, _ = aligned(10, None)
check("影子值对齐: shadow 为 None -> 不动", got["total_clicks"] == 10, str(got))
data, lc, ls, root = sandbox()
lc.write_text(json.dumps({"total_clicks": "abc"}), encoding="utf-8")
run(data, lc, ls, shadow=500)
check("影子值对齐: total_clicks 不是整数 -> 不动", read_json(data / "config.json")["total_clicks"] == "abc")
check("影子值对齐: 不留 .tmp 文件", not list(data.glob("*.tmp")))

data, lc, ls, root = sandbox()
run(data, lc, ls, shadow=500)
(data / "config.json").write_text("{}", encoding="utf-8")
lc.write_text('{"total_clicks": 3}', encoding="utf-8")
r = run(data, lc, ls, shadow=500)
check("late_import 也做影子值对齐", r == "late_import" and read_json(data / "config.json")["total_clicks"] == 500)

# ------------------------------------------------------------------ 8
# Broken legacy JSON
data, lc, ls, root = sandbox()
lc.write_bytes(b"{not json at all")
try:
    r = run(data, lc, ls, shadow=500)
    ok = True
except Exception as e:  # noqa: BLE001
    r, ok = repr(e), False
check("旧 config 是坏 JSON: 不抛异常", ok, r)
check("旧 config 是坏 JSON: 照样复制", (data / "config.json").read_bytes() == b"{not json at all" and r == "imported")

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("用户数据目录迁移用例全部通过")
sys.exit(0)
