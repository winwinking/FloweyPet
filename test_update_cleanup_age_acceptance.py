# -*- coding: utf-8 -*-
"""Acceptance tests for the age rule of update_flow.cleanup_old_update_dirs
(only FloweyPet-update-* folders untouched for min_age_seconds, default 600,
are deleted) and for its new place in main.py (after the single-instance
check, packaged build only, not when running from the temp folder).

Run:  python test_update_cleanup_age_acceptance.py
"""
import atexit
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="floweypet_age_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
sys.path.insert(0, str(ROOT / "src"))

import update_flow as uf  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


atexit.register(lambda: shutil.rmtree(TMP, ignore_errors=True))

_n = [0]


def make_update_dir(root, name="FloweyPet-update-abc", files=("x.zip", "unpacked/FloweyPet-v3.4.0/FloweyPet.exe",
                                                               "unpacked/FloweyPet-v3.4.0/assets/a.png")):
    d = Path(root) / name
    for rel in files:
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"data")
    return d


def age_everything(d, seconds):
    """Sets mtime of the folder, every sub folder and every file to `seconds` ago."""
    t = time.time() - seconds
    paths = [d]
    for dp, dn, fn in os.walk(d):
        paths += [Path(dp) / x for x in dn + fn]
    for p in paths:
        os.utime(p, (t, t))


def fresh_root():
    _n[0] += 1
    r = TMP / f"root{_n[0]}"
    r.mkdir()
    return r


MIN = 11 * 60

# ------------------------------------------------------------------ 1
print("== 1 默认 600 秒 ==")
check("默认 min_age_seconds 是 600", uf.MIN_AGE_SECONDS == 600
      and uf.cleanup_old_update_dirs.__defaults__[2] == 600)
root = fresh_root()
d = make_update_dir(root)
logs = []
n = uf.cleanup_old_update_dirs(root, log=logs.append)
check("刚建的目录不删", n == 0 and d.exists())
check("刚建的目录: 不计入删除也不计入删不掉 (不记日志)", logs == [], repr(logs))
check("刚建的目录: 里面的文件原样", (d / "unpacked" / "FloweyPet-v3.4.0" / "FloweyPet.exe").read_bytes() == b"data")
age_everything(d, 9 * 60)
check("9 分钟前的目录还不删", uf.cleanup_old_update_dirs(root) == 0 and d.exists())
age_everything(d, 599)
check("599 秒前的目录还不删", uf.cleanup_old_update_dirs(root) == 0 and d.exists())
age_everything(d, MIN)
logs.clear()
n = uf.cleanup_old_update_dirs(root, log=logs.append)
check("文件夹和里面所有东西都改到 11 分钟前 -> 删", n == 1 and not d.exists(), str(n))
check("删除后记了日志", len(logs) == 1 and "删除 1 个" in logs[0], repr(logs))

# ------------------------------------------------------------------ 2
print("== 2 只要里面有一个新的就不删 ==")
root = fresh_root()
d = make_update_dir(root)
age_everything(d, MIN)
(d / "unpacked" / "FloweyPet-v3.4.0" / "assets" / "a.png").write_bytes(b"just written")  # new file, old folders
os.utime(d, (time.time() - MIN, time.time() - MIN))
os.utime(d / "unpacked", (time.time() - MIN, time.time() - MIN))
check("前提: 文件夹本身是旧的", d.stat().st_mtime < time.time() - 600)
n = uf.cleanup_old_update_dirs(root)
check("文件夹旧但里面有一个文件是新的 -> 不删", n == 0 and d.exists() and (d / "x.zip").exists(), str(n))
root = fresh_root()
d = make_update_dir(root)
age_everything(d, MIN)
newfile = d / "unpacked" / "FloweyPet-v3.4.0" / "deep_new" / "n.txt"
newfile.parent.mkdir(parents=True)
newfile.write_bytes(b"x")
t_old = time.time() - MIN
os.utime(newfile, (t_old, t_old))
os.utime(newfile.parent, (time.time(), time.time()))  # only a sub folder is new
n = uf.cleanup_old_update_dirs(root)
check("只有某个子文件夹是新的 -> 不删", n == 0 and d.exists(), str(n))
root = fresh_root()
d = make_update_dir(root)
age_everything(d, MIN)
os.utime(d, (time.time(), time.time()))  # only the folder itself is new
check("只有文件夹本身是新的 -> 不删", uf.cleanup_old_update_dirs(root) == 0 and d.exists())
root = fresh_root()
d = make_update_dir(root)
age_everything(d, MIN)
os.utime(d / "x.zip", (time.time() + 3600, time.time() + 3600))
check("文件修改时间在未来 -> 当作新的, 不删", uf.cleanup_old_update_dirs(root) == 0 and d.exists())

# ------------------------------------------------------------------ 3
print("== 3 多个目录混合 ==")
root = fresh_root()
old1 = make_update_dir(root, "FloweyPet-update-old1")
old2 = make_update_dir(root, "FloweyPet-update-old2")
young = make_update_dir(root, "FloweyPet-update-young")
other = make_update_dir(root, "SomethingElse-update-old", files=("a.txt",))
lower = make_update_dir(root, "floweypet-update-old", files=("a.txt",))
for x in (old1, old2, other, lower):
    age_everything(x, MIN)
logs = []
n = uf.cleanup_old_update_dirs(root, log=logs.append)
check("只删旧的 FloweyPet-update-*: 2 个", n == 2 and not old1.exists() and not old2.exists(), str(n))
check("新的保留", young.exists())
check("其他名字的旧文件夹不碰 (即使很旧)", other.exists() and lower.exists())
check("日志只算删掉的 (没有把新的算进 '删不掉')", len(logs) == 1 and "删除 2 个" in logs[0] and "0 个删不掉" in logs[0], repr(logs))
f = root / "FloweyPet-update-file"
f.write_text("a file")
os.utime(f, (time.time() - MIN, time.time() - MIN))
check("前缀同名的普通文件不碰", uf.cleanup_old_update_dirs(root) == 0 and f.exists())

# ------------------------------------------------------------------ 4
print("== 4 min_age_seconds=0 与以前一样 ==")
root = fresh_root()
fresh1 = make_update_dir(root, "FloweyPet-update-a1")
fresh2 = make_update_dir(root, "FloweyPet-update-a2")
other = make_update_dir(root, "NotOurs", files=("a.txt",))
n = uf.cleanup_old_update_dirs(root, min_age_seconds=0)
check("min_age_seconds=0: 刚建的也删", n == 2 and not fresh1.exists() and not fresh2.exists(), str(n))
check("min_age_seconds=0: 其他名字的仍不碰", other.exists())
root = fresh_root()
d = make_update_dir(root)
age_everything(d, 2)  # 2 s ago: immune to filesystem timestamps that run slightly ahead of time.time()
check("min_age_seconds=0 不用等", uf.cleanup_old_update_dirs(root, min_age_seconds=0) == 1 and not d.exists())
root = fresh_root()
d = make_update_dir(root)
check("自定义 min_age_seconds=5, 刚建的目录不删", uf.cleanup_old_update_dirs(root, min_age_seconds=5) == 0 and d.exists())
age_everything(d, 6)
check("自定义 min_age_seconds=5, 6 秒前的删", uf.cleanup_old_update_dirs(root, min_age_seconds=5) == 1 and not d.exists())

# ------------------------------------------------------------------ 5
print("== 5 取修改时间出错 / 其他行为不变 ==")
root = fresh_root()
d = make_update_dir(root)
age_everything(d, MIN)
orig = uf._newest_mtime


def boom(path):
    raise OSError("cannot stat")


uf._newest_mtime = boom
try:
    try:
        n = uf.cleanup_old_update_dirs(root)
        raised = False
    except BaseException:  # noqa: BLE001
        n, raised = None, True
finally:
    uf._newest_mtime = orig
check("取修改时间出错 -> 不抛异常", raised is False)
check("取修改时间出错 -> 当作新的, 跳过不删", n == 0 and d.exists(), str(n))
n = uf.cleanup_old_update_dirs(root)
check("恢复后同一个旧目录正常被删", n == 1 and not d.exists())
check("目录不存在不抛异常", uf.cleanup_old_update_dirs(TMP / "no_such_dir") == 0)
orig_listdir = uf.os.listdir
uf.os.listdir = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    uf.cleanup_old_update_dirs(root)
    raised = False
except BaseException:  # noqa: BLE001
    raised = True
finally:
    uf.os.listdir = orig_listdir
check("内部意外错误不抛异常", raised is False)
root = fresh_root()
d = make_update_dir(root)
age_everything(d, MIN)
orig_remove = uf.remove_dir
uf.remove_dir = lambda p: False
logs = []
try:
    n = uf.cleanup_old_update_dirs(root, log=logs.append)
finally:
    uf.remove_dir = orig_remove
check("旧但删不掉: 不计入删除, 记 1 个删不掉", n == 0 and logs and "1 个删不掉" in logs[0], repr((n, logs)))

# ------------------------------------------------------------------ 6
print("== 6 main.py 的位置 ==")
src = (ROOT / "main.py").read_text(encoding="utf-8")
i_claim = src.index("guard.try_claim()")
i_clean = src.index("update_flow.cleanup_old_update_dirs(")
check("cleanup_old_update_dirs 只出现一次", src.count("cleanup_old_update_dirs(") == 1)
check("cleanup_old_update_dirs 在 guard.try_claim() 之后", i_clean > i_claim)
check("在 sys.exit(0) 判重退出之后 (被拦下的第二个进程根本到不了这里)",
      src.index("sys.exit(0)", i_claim) < i_clean)
cond = src[src.rfind("\n", 0, src.rfind("if ", 0, i_clean)):i_clean]
check("仍在 打包版 且 非临时目录 的条件里", re.search(r"if not IS_DEV and not running_from_temp:", cond) is not None, repr(cond))
check("clean 紧跟在判重之后, 在数据迁移之前", i_clean < src.index("migrate_legacy_data(USER_DATA_DIR"))
check("没有留下判重之前的旧调用", "elif not IS_DEV:" not in src[:i_claim])
check("记日志用 app_log.log_event", "cleanup_old_update_dirs(log=app_log.log_event)" in src)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("清理旧更新临时目录(按年龄)用例全部通过")
sys.exit(0)
