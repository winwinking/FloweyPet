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

# ------------------------------------------------------------------ 9
# Interrupted import -> "importing" marker -> resumed
import shutil  # noqa: E402
from user_data import autostart_exe_dir, pick_legacy_source  # noqa: E402


def failing_copy2(fail_name):
    real = shutil.copy2

    def fake(src, dst, *a, **kw):
        if Path(dst).name == fail_name and Path(dst).parent.name == "data":
            raise OSError("simulated disk error")
        return real(src, dst, *a, **kw)
    return real, fake


data, lc, ls, root = sandbox()
lc.write_text('{"total_clicks": 5}', encoding="utf-8")
ls.write_text('{"interaction_count": 3}', encoding="utf-8")
real, fake = failing_copy2("stats.json")
shutil.copy2 = fake
try:
    raised = False
    try:
        run(data, lc, ls)
    except OSError:
        raised = True
finally:
    shutil.copy2 = real
check("搬到一半出错: 异常向外抛出", raised)
check("搬到一半出错: 标记是 importing", marker(data)["state"] == "importing")
r = run(data, lc, ls)
check("恢复后重跑: 返回 resumed_import", r == "resumed_import", r)
check("恢复后重跑: 两个文件都和旧文件一致",
      (data / "config.json").read_bytes() == lc.read_bytes() and (data / "stats.json").read_bytes() == ls.read_bytes())
check("恢复后重跑: 标记 imported", marker(data)["state"] == "imported")
check("恢复后重跑: 没有备份目录", not list(data.glob("backup_before_import_*")))

# importing + legacy files gone -> keep data, close marker
data, lc, ls, root = sandbox()
lc.write_text('{"total_clicks": 5}', encoding="utf-8")
shutil.copy2 = fake_ = failing_copy2("config.json")[1]
try:
    try:
        run(data, lc, ls)
    except OSError:
        pass
finally:
    shutil.copy2 = real
(data / "config.json").write_text('{"progress": "mine"}', encoding="utf-8")
lc.unlink()
r = run(data, lc, ls)
check("importing 且旧文件已消失: 返回 none", r == "none", r)
check("importing 且旧文件已消失: 数据不变", read_json(data / "config.json") == {"progress": "mine"})
check("importing 且旧文件已消失: 标记变 imported", marker(data)["state"] == "imported")
lc.write_text('{"total_clicks": 777}', encoding="utf-8")
r = run(data, lc, ls)
check("之后旧文件重新出现: 返回 none,不覆盖",
      r == "none" and read_json(data / "config.json") == {"progress": "mine"})

# Case B interrupted, then rerun
data, lc, ls, root = sandbox()
run(data, lc, ls)
(data / "config.json").write_text('{"new": true}', encoding="utf-8")
(data / "stats.json").write_text('{"s": "new"}', encoding="utf-8")
lc.write_text('{"total_clicks": 99}', encoding="utf-8")
ls.write_text('{"s": "old"}', encoding="utf-8")
shutil.copy2 = failing_copy2("stats.json")[1]
try:
    raised = False
    try:
        run(data, lc, ls)
    except OSError:
        raised = True
finally:
    shutil.copy2 = real
check("B 搬到一半出错: 异常抛出且标记 importing", raised and marker(data)["state"] == "importing")
r = run(data, lc, ls)
check("B 出错后重跑: 返回 resumed_import", r == "resumed_import", r)
check("B 出错后重跑: 备份目录只有一个", len(list(data.glob("backup_before_import_*"))) == 1)
check("B 出错后重跑: 新数据是旧文件内容",
      read_json(data / "config.json") == {"total_clicks": 99} and read_json(data / "stats.json") == {"s": "old"})
check("B 出错后重跑: 标记 imported", marker(data)["state"] == "imported")

# ------------------------------------------------------------------ 10
# autostart_exe_dir
check("autostart: 带引号含空格的 exe", autostart_exe_dir('"C:\\Program Files\\Flowey Pet\\FloweyPet.exe"') == Path("C:\\Program Files\\Flowey Pet"))
check("autostart: 带引号 exe 后跟参数", autostart_exe_dir('"C:\\A B\\FloweyPet.exe" --x') == Path("C:\\A B"))
check("autostart: 不带引号的 exe", autostart_exe_dir("C:\\Games\\FloweyPet.exe") == Path("C:\\Games"))
check("autostart: 不带引号 exe 后跟参数", autostart_exe_dir("C:\\Games\\FloweyPet.EXE --x") == Path("C:\\Games"))
check("autostart: 源码版 python.exe + main.py 取 python.exe 所在文件夹",
      autostart_exe_dir('"C:\\Python39\\python.exe" "E:\\src\\main.py"') == Path("C:\\Python39"))
for label, val in (("None", None), ("空字符串", ""), ("abc", "abc"), ("只有一个引号", '"'), ("引号未闭合", '"C:\\a\\b.exe'), ("非字符串", 123)):
    try:
        got = autostart_exe_dir(val)
        ok = got is None
    except Exception as e:  # noqa: BLE001
        got, ok = repr(e), False
    check(f"autostart: {label} -> None,不抛异常", ok, str(got))

# ------------------------------------------------------------------ 11
# pick_legacy_source
def mkdir_with(root, name, files=("config.json",)):
    d = root / name
    d.mkdir(parents=True)
    for f in files:
        (d / f).write_text("{}", encoding="utf-8")
    return d


root = Path(tempfile.mkdtemp(prefix="floweypet_pick_"))
prim = mkdir_with(root, "prim")
fb = mkdir_with(root, "fb")
c, s_, src = pick_legacy_source(prim, [fb])
check("pick: primary 有文件 -> 用 primary", src == prim and c == prim / "config.json" and s_ == prim / "stats.json")
prim2 = mkdir_with(root, "prim2", files=())
c, s_, src = pick_legacy_source(prim2, [fb])
check("pick: primary 没有、fallback 有 -> 用 fallback", src == fb and c == fb / "config.json")
fb_only_stats = mkdir_with(root, "fb_stats", files=("stats.json",))
c, s_, src = pick_legacy_source(prim2, [fb_only_stats])
check("pick: fallback 只有 stats.json 也算", src == fb_only_stats)
c, s_, src = pick_legacy_source(prim2, [prim2, fb])
check("pick: fallback 和 primary 是同一个文件夹 -> 跳过", src == fb)
c, s_, src = pick_legacy_source(prim2, [prim2 / ".." / "prim2", fb])
check("pick: 用 resolve 比较(带 .. 的同一文件夹也跳过)", src == fb)
c, s_, src = pick_legacy_source(prim2, [root / "nope", None, fb])
check("pick: fallback 不存在/None -> 跳过", src == fb)
empty_fb = mkdir_with(root, "empty_fb", files=())
c, s_, src = pick_legacy_source(prim2, [empty_fb, root / "nope", None])
check("pick: 都没有 -> 返回 primary 的路径",
      src == prim2 and c == prim2 / "config.json" and s_ == prim2 / "stats.json")
c, s_, src = pick_legacy_source(prim2, [])
check("pick: 空候选列表 -> primary", src == prim2)
py_dir = mkdir_with(root, "pydir", files=())
c, s_, src = pick_legacy_source(prim2, [autostart_exe_dir('"%s" "main.py"' % (py_dir / "python.exe"))])
check("pick: 源码版自启命令的文件夹没有数据文件 -> 跳过", src == prim2)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("用户数据目录迁移用例全部通过")
sys.exit(0)
