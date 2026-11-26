# -*- coding: utf-8 -*-
"""Acceptance tests for start-up recovery when files are in use
(updater_apply: .update_trash, empty_trash, startup_hook) and main.py's
hook-up.

Files that are "in use" are real: a copy of python.exe is placed inside the
fake install folder and run as a sleeping child process, which makes Windows
refuse to overwrite or delete it (but allow moving it). Every child is killed
and the temp folder removed at the end, also on failure.

Run:  python test_updater_recovery_acceptance.py
"""
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="floweypet_recovery_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
sys.path.insert(0, str(ROOT / "src"))

import updater_apply as ua  # noqa: E402

FAILURES = []
CHILDREN = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def cleanup():
    for p in CHILDREN:
        try:
            if p.poll() is None:
                p.kill()
            p.wait(10)
        except Exception:  # noqa: BLE001
            pass
    for _ in range(20):
        shutil.rmtree(TMP, ignore_errors=True)
        if not TMP.exists():
            break
        time.sleep(0.25)


atexit.register(cleanup)

_n = [0]


def fresh(label):
    _n[0] += 1
    d = TMP / f"{label}{_n[0]}"
    d.mkdir(parents=True)
    return d


def write(base, rel, data):
    p = Path(base).joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def snapshot(base):
    files = {}
    for dp, dn, fn in os.walk(base):
        for f in fn:
            full = os.path.join(dp, f)
            try:
                files[os.path.relpath(full, base).replace("\\", "/")] = Path(full).read_bytes()
            except OSError:
                files[os.path.relpath(full, base).replace("\\", "/")] = b"<unreadable>"
    return files


PY_DIR = Path(sys.executable).parent


def start_locked(dest_exe):
    """Copies python.exe (plus the runtime dlls beside it, if needed) to
    dest_exe and runs it as a sleeping process: dest_exe is now in use."""
    dest_exe = Path(dest_exe)
    dest_exe.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sys.executable, dest_exe)
    for attempt in range(2):
        p = subprocess.Popen([str(dest_exe), "-c", "import time; time.sleep(120)"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        CHILDREN.append(p)
        time.sleep(0.8)
        if p.poll() is None:
            return p
        for dll in PY_DIR.glob("*.dll"):  # not found via PATH: put them beside the copy
            try:
                shutil.copy2(dll, dest_exe.parent / dll.name)
            except OSError:
                pass
    raise RuntimeError("could not start a locked python copy")


def stop(p):
    if p.poll() is None:
        p.kill()
    p.wait(10)
    time.sleep(0.2)


PY_SIZE = Path(sys.executable).stat().st_size


def make_install():
    t = fresh("inst") / "FloweyPet"
    t.mkdir()
    write(t, "FloweyPet.exe", "OLD-EXE")
    write(t, "dialogue.json", "OLD-DIALOGUE")
    write(t, "user_notes.txt", "mine")
    write(t, "config.json", '{"keep": 1}')
    return t


def marker(t, **over):
    d = {"format": 1, "phase": "replacing", "pid": None, "created": None, "old_version": "3.2.0", "new_version": "3.3.0",
         "added": [], "added_dirs": [], "package_files": [], "backup_dir": str(t / ".update_backup")}
    d.update(over)
    write(t, ".update_pending.json", json.dumps(d))


def no_sleep(s):
    pass


# ------------------------------------------------------------------ 0
print("== 0 Windows 允许挪走正在运行的文件, 不允许覆盖/删除 (前提) ==")
t = make_install()
child = start_locked(t / "locked.exe")
other = t / "other.bin"
other.write_bytes(b"x")
for label, fn in (("覆盖在用文件", lambda: os.replace(other, t / "locked.exe")), ("删除在用文件", lambda: os.remove(t / "locked.exe"))):
    try:
        fn()
        got = "ok"
    except PermissionError:
        got = "PermissionError"
    check(f"{label} -> PermissionError", got == "PermissionError", got)
moved = t / "moved_away.exe"
try:
    os.replace(t / "locked.exe", moved)
    got = "ok"
except OSError as e:
    got = type(e).__name__
check("挪走在用文件 -> 成功", got == "ok" and moved.exists() and not (t / "locked.exe").exists(), got)
check("挪走后进程仍在运行", child.poll() is None)
stop(child)

# ------------------------------------------------------------------ 1
print("== 1 回滚还原时目标文件被占用 -> 挪进垃圾箱, 备份还原到位 ==")
t = make_install()
child = start_locked(t / "locked.exe")  # stands for a file of the NEW version that is in use
write(t, ".update_backup/locked.exe", "OLD-LOCKED-CONTENT")
write(t, ".update_backup/dialogue.json", "OLD-DIALOGUE-FROM-BACKUP")
write(t, "dialogue.json", "NEW-DIALOGUE")
marker(t)
status = ua.recover_if_needed(t, sleep=no_sleep)
check("结果是 recovered", status == "recovered", status)
check("被占用的文件被挪进 .update_trash", (t / ".update_trash" / "locked.exe").is_file()
      and (t / ".update_trash" / "locked.exe").stat().st_size == PY_SIZE)
check("备份文件已还原到原位 (被占用的那个)", (t / "locked.exe").read_bytes() == b"OLD-LOCKED-CONTENT")
check("备份文件已还原到原位 (普通的)", (t / "dialogue.json").read_bytes() == b"OLD-DIALOGUE-FROM-BACKUP")
check("事务标记已删除", not (t / ".update_pending.json").exists())
check("备份目录已清理", not (t / ".update_backup").exists())
check("用户文件和 config.json 不动", (t / "user_notes.txt").read_bytes() == b"mine" and (t / "config.json").read_bytes() == b'{"keep": 1}')
check("占用文件的进程没有被杀", child.poll() is None)
stop(child)
check("进程结束后垃圾箱里的文件可以删掉", (ua.empty_trash(t), not (t / ".update_trash").exists())[1])

# ------------------------------------------------------------------ 2
print("== 2 删除新增文件时被占用 -> 挪进垃圾箱 ==")
t = make_install()
child = start_locked(t / "bin" / "added_tool.exe")
marker(t, added=["bin/added_tool.exe"], added_dirs=["bin"])
write(t, ".update_backup/dialogue.json", "OLD-DIALOGUE")
status = ua.recover_if_needed(t, sleep=no_sleep)
check("结果是 recovered", status == "recovered", status)
check("新增文件已从原位消失", not (t / "bin" / "added_tool.exe").exists())
check("新增文件在垃圾箱里 (保持相对路径)", (t / ".update_trash" / "bin" / "added_tool.exe").is_file())
check("标记已删除", not (t / ".update_pending.json").exists())
check("备份还原了其他文件", (t / "dialogue.json").read_bytes() == b"OLD-DIALOGUE")
check("进程没被杀", child.poll() is None)
stop(child)
ua.empty_trash(t)

# .new 临时文件被占用
t = make_install()
child = start_locked(t / "dialogue.json.new")
marker(t, package_files=["dialogue.json"])
status = ua.recover_if_needed(t, sleep=no_sleep)
check(".new 临时文件被占用 -> 挪进垃圾箱, recovered", status == "recovered" and (t / ".update_trash" / "dialogue.json.new").is_file()
      and not (t / "dialogue.json.new").exists(), status)
stop(child)
ua.empty_trash(t)

# ------------------------------------------------------------------ 3
print("== 3 挪进垃圾箱也失败 -> incomplete, 标记保留 ==")
t = make_install()
child = start_locked(t / "locked.exe")
write(t, ".update_backup/locked.exe", "OLD-LOCKED-CONTENT")
write(t, ".update_backup/dialogue.json", "OLD-DIALOGUE-FROM-BACKUP")
marker(t)
orig = ua._replace


def deny_trash(src, dst):
    if ua.TRASH_NAME in dst:
        raise PermissionError("injected: cannot move into trash")
    return orig(src, dst)


ua._replace = deny_trash
try:
    status = ua.recover_if_needed(t, sleep=no_sleep)
finally:
    ua._replace = orig
check("结果是 incomplete", status == "incomplete", status)
st = json.loads((t / ".update_pending.json").read_text(encoding="utf-8"))
check("标记保留, phase=rollback_incomplete, pid 清空", st["phase"] == "rollback_incomplete" and st["pid"] is None)
check("能还原的都还原了", (t / "dialogue.json").read_bytes() == b"OLD-DIALOGUE-FROM-BACKUP")
check("备份目录保留 (里面还有没还原的文件)", (t / ".update_backup" / "locked.exe").is_file())
check("占用文件原样还在", (t / "locked.exe").stat().st_size == PY_SIZE and child.poll() is None)
stop(child)
status = ua.recover_if_needed(t, sleep=no_sleep)
check("进程结束后再恢复一次 -> recovered, 备份还原到位", status == "recovered" and (t / "locked.exe").read_bytes() == b"OLD-LOCKED-CONTENT")

# ------------------------------------------------------------------ 4
print("== 4 垃圾箱重名加后缀 ==")
t = make_install()
write(t, ".update_trash/locked.exe", "EARLIER-TRASH-0")
write(t, ".update_trash/locked.exe.1", "EARLIER-TRASH-1")
child = start_locked(t / "locked.exe")
write(t, ".update_backup/locked.exe", "OLD-LOCKED-CONTENT")
marker(t)
status = ua.recover_if_needed(t, sleep=no_sleep)
tr = t / ".update_trash"
check("结果是 recovered", status == "recovered", status)
check("已有的垃圾箱文件没被覆盖", (tr / "locked.exe").read_bytes() == b"EARLIER-TRASH-0" and (tr / "locked.exe.1").read_bytes() == b"EARLIER-TRASH-1")
check("新挪进来的用 .2 后缀", (tr / "locked.exe.2").is_file() and (tr / "locked.exe.2").stat().st_size == PY_SIZE)
check("备份已还原", (t / "locked.exe").read_bytes() == b"OLD-LOCKED-CONTENT")
stop(child)
ua.empty_trash(t)

# ------------------------------------------------------------------ 5
print("== 5 empty_trash ==")
t = make_install()
write(t, ".update_trash/a.txt", "a")
write(t, ".update_trash/sub/deep/b.txt", "b")
child = start_locked(t / ".update_trash" / "sub" / "locked.exe")
try:
    ua.empty_trash(t)
    raised = False
except BaseException:  # noqa: BLE001
    raised = True
check("有一个被占用: 不抛异常", raised is False)
check("能删的都删了", not (t / ".update_trash" / "a.txt").exists() and not (t / ".update_trash" / "sub" / "deep").exists())
check("被占用的跳过 (还在)", (t / ".update_trash" / "sub" / "locked.exe").exists())
check("安装目录里的其他东西不动", (t / "FloweyPet.exe").exists() and (t / "user_notes.txt").exists())
stop(child)
ua.empty_trash(t)
check("进程结束后再清: 整个垃圾箱没了", not (t / ".update_trash").exists())
ua.empty_trash(t)
ua.empty_trash(TMP / "does_not_exist")
check("垃圾箱不存在 / target 不存在: 什么都不发生", True)
orig_walk = ua.os.walk
ua.os.walk = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("walk exploded"))
write(t, ".update_trash/x.txt", "x")
try:
    ua.empty_trash(t)
    raised = False
except BaseException:  # noqa: BLE001
    raised = True
finally:
    ua.os.walk = orig_walk
check("内部意外错误也不抛异常", raised is False)
ua.empty_trash(t)
for bad in (None, 123, "\0bad"):
    try:
        ua.empty_trash(bad)
        raised = False
    except BaseException:  # noqa: BLE001
        raised = True
    check(f"参数是 {bad!r} 也不抛异常", raised is False)
log = (TMP / "data" / "logs" / "update.log").read_text(encoding="utf-8")
check("挪进垃圾箱和清理垃圾箱都记了日志", "被占用的文件已挪进垃圾箱" in log and "清理垃圾箱" in log)
check("日志里没有网址", "http://" not in log and "https://" not in log)

# ------------------------------------------------------------------ 6
print("== 6 .update_trash 在应用更新时受保护 ==")
check("PROTECTED_TOP_LEVEL 含 .update_trash", ".update_trash" in ua.PROTECTED_TOP_LEVEL)
for rel in (".update_trash", ".update_trash/x.txt", ".update_trash/sub/y/z.bin", ".UPDATE_TRASH/Q", ".update_backup/a",
            ".update_backup"):
    check(f"_is_protected({rel!r})", ua._is_protected(rel))
check("子目录里同名不受保护 (只管顶层)", not ua._is_protected("assets/.update_trash"))


class Fk:
    def __init__(self):
        self.launches = []

    def launcher(self, exe, args, cwd):
        self.launches.append(list(args))


t = make_install()
write(t, ".install_manifest.json", json.dumps({"version": "3.2.0", "files": [
    "FloweyPet.exe", ".update_trash/y.txt", ".UPDATE_TRASH/z.txt", ".update_trash"]}))
write(t, ".update_trash/y.txt", "old trash y")
write(t, ".update_trash/z.txt", "old trash z")
n = fresh("new") / "FloweyPet-v3.3.0"
n.mkdir()
write(n, "FloweyPet.exe", "NEW-EXE")
write(n, ".update_trash/evil.txt", "evil")
write(n, ".update_trash/sub/evil2.txt", "evil2")
f = Fk()
rc = ua.apply_update(t, n, 1, 1, "3.2.0", "3.3.0", True, waiter=lambda *a: True, launcher=f.launcher, sleep=no_sleep, self_info=(1, 1))
files = snapshot(t)
check("更新成功", rc == 0 and files["FloweyPet.exe"] == b"NEW-EXE", str(rc))
check("新包里的 .update_trash 内容没有被装进去", ".update_trash/evil.txt" not in files and ".update_trash/sub/evil2.txt" not in files)
check("旧清单里写了 .update_trash/... 也不会被移走", files.get(".update_trash/y.txt") == b"old trash y" and files.get(".update_trash/z.txt") == b"old trash z")
check("新清单里没有 .update_trash 的条目", not any(x.casefold().startswith(".update_trash") for x in json.loads(files[".install_manifest.json"])["files"]))
check("备份里没有垃圾箱的文件", not any(k.startswith(".update_backup/.update_trash") for k in files))

# ------------------------------------------------------------------ 7
print("== 7 启动钩子 startup_hook ==")


class Rec:
    def __init__(self, status):
        self.status = status
        self.calls = []
        self.launches = []

    def empty(self, target):
        self.calls.append("empty_trash")

    def recover(self, target, **kw):
        self.calls.append("recover_if_needed")
        return self.status

    def launcher(self, exe, args, cwd):
        self.launches.append((exe, list(args), cwd))


def hook(status, argv=(), launcher_raises=False):
    r = Rec(status)
    oe, orr = ua.empty_trash, ua.recover_if_needed
    ua.empty_trash, ua.recover_if_needed = r.empty, r.recover
    try:
        if launcher_raises:
            def bad(*a):
                raise OSError("cannot start")
            res = ua.startup_hook(TMP / "xx", list(argv), launcher=bad)
        else:
            res = ua.startup_hook(TMP / "xx", list(argv), launcher=r.launcher)
    finally:
        ua.empty_trash, ua.recover_if_needed = oe, orr
    return res, r


res, r = hook("in_progress")
check("in_progress -> 退出 (True), 不启动任何东西", res is True and r.launches == [])
check("empty_trash 先于 recover_if_needed", r.calls == ["empty_trash", "recover_if_needed"], repr(r.calls))
exe = os.path.join(os.path.abspath(TMP / "xx"), "FloweyPet.exe")
for st in ("recovered", "incomplete"):
    res, r = hook(st, ["--something"])
    check(f"{st} 且没有 --after-recovery -> 启动 --update-failed --after-recovery 并退出",
          res is True and r.launches == [(exe, ["--update-failed", "--after-recovery"], os.path.abspath(TMP / "xx"))], repr(r.launches))
    res, r = hook(st, ["--update-failed", "--after-recovery"])
    check(f"{st} 但已有 --after-recovery -> 不再重开, 正常启动", res is False and r.launches == [], repr(r.launches))
    res, r = hook(st, [], launcher_raises=True)
    check(f"{st} 重开失败 -> 继续用当前进程启动", res is False)
for st in ("none", "bad_marker"):
    res, r = hook(st)
    check(f"{st} -> 继续正常启动, 不启动东西", res is False and r.launches == [])
res, r = hook("none", ["--updated-from", "3.2.0"])
check("--updated-from 正常启动不受影响", res is False)
log = (TMP / "data" / "logs" / "update.log").read_text(encoding="utf-8")
check("已有 --after-recovery 时记了日志", "不再重开" in log)
check("重开时记了日志", "已重开一个新进程" in log)

# 真实目录: 恢复后确实要求重开, 第二次启动不再重开
t = make_install()
write(t, ".update_backup/dialogue.json", "OLD")
marker(t)
fk = Fk()
res = ua.startup_hook(t, [], launcher=lambda e, a, c: fk.launches.append(list(a)))
check("真实恢复 -> 请求重开一次", res is True and fk.launches == [["--update-failed", "--after-recovery"]])
check("真实恢复 -> 文件已还原, 标记已删", (t / "dialogue.json").read_bytes() == b"OLD" and not (t / ".update_pending.json").exists())
res = ua.startup_hook(t, ["--update-failed", "--after-recovery"], launcher=lambda e, a, c: fk.launches.append(list(a)))
check("重开后的第二次启动: 没有标记 -> 正常启动, 不再重开", res is False and len(fk.launches) == 1)
t = make_install()
marker(t, pid=os.getpid(), created=ua.get_process_creation_time(os.getpid()))
res = ua.startup_hook(t, [], launcher=lambda *a: None)
check("真实: 标记里的进程还活着 -> 退出 (True), 文件不动", res is True and (t / ".update_pending.json").exists())

main_src = (ROOT / "main.py").read_text(encoding="utf-8")
i_pyqt = main_src.index("from PyQt6")
i_hook = main_src.index("updater_apply.startup_hook(")
check("main.py: startup_hook 在 PyQt6 导入之前", i_hook < i_pyqt)
block = main_src[main_src.index("if getattr(sys, \"frozen\", False):"):i_pyqt]
check("main.py: 钩子整体包在 try/except Exception 里, 出错继续启动",
      re.search(r"try:\s+import updater_apply.*?startup_hook.*?except Exception:\s+pass", block, re.S) is not None)
check("main.py: 返回 True 时 sys.exit(0)", re.search(r"if _exit_now:\s+sys\.exit\(0\)", block) is not None)
check("main.py: 钩子只在打包版运行", 'if getattr(sys, "frozen", False):' in main_src[:i_hook])
check("main.py: 分流部分仍然只导入 updater_apply",
      set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", main_src[:i_pyqt], re.M)) == {"sys", "time", "pathlib", "updater_apply"},
      repr(set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", main_src[:i_pyqt], re.M))))
import inspect  # noqa: E402

hook_src = inspect.getsource(ua.startup_hook)
check("startup_hook 源码里 empty_trash 在 recover_if_needed 之前", hook_src.index("empty_trash(") < hook_src.index("recover_if_needed("))
check("startup_hook 源码里有 --after-recovery 防死循环", '"--after-recovery" in' in hook_src)
check("recover_if_needed 的返回值集合没变", {ua.RECOVER_NONE, ua.RECOVER_IN_PROGRESS, ua.RECOVER_RECOVERED, ua.RECOVER_INCOMPLETE,
                                            ua.RECOVER_BAD_MARKER} == {"none", "in_progress", "recovered", "incomplete", "bad_marker"})
r = subprocess.run([sys.executable, "-I", "-c",
                    "import sys; sys.path.insert(0,'src'); import updater_apply; "
                    "print(sorted(m for m in sys.modules if m.split('.')[0] in ('PyQt6','config','paths','app_log')))"],
                   cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
check("导入 updater_apply 仍不带入 PyQt6/config/paths/app_log", r.stdout.strip() == "[]")

# ------------------------------------------------------------------ 8
print("== 8 收尾 ==")
check("所有测试子进程都已结束", all(p.poll() is not None for p in CHILDREN) or [stop(p) for p in CHILDREN] and all(p.poll() is not None for p in CHILDREN))
cleanup()
check("临时目录已清理", not TMP.exists(), str(TMP))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("恢复时被占用文件的用例全部通过")
sys.exit(0)
