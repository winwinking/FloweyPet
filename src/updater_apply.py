# -*- coding: utf-8 -*-
"""One-click update, step 4: "apply update" mode.

The freshly downloaded and unpacked new FloweyPet.exe is started from a
temporary folder with this command line; it waits for the old process to
exit, swaps the files of the install folder, and starts the new version.

COMMAND-LINE CONTRACT (every future version must keep honoring it, because
the *old* program builds this command line and the *new* exe parses it):

    FloweyPet.exe --apply-update --target <install dir>
                  --wait-pid <old pid> --wait-created <old process creation time>
                  --old-version <old version> [--no-restart]

  --target          install folder; must directly contain FloweyPet.exe
  --wait-pid        pid of the old process to wait for (never killed)
  --wait-created    that process's creation time as a FILETIME integer
                    (get_process_creation_time(pid)); a pid whose creation
                    time differs was reused by another process, i.e. the old
                    one is already gone
  --old-version     version string of the old program, e.g. 3.2.0
  --no-restart      swap the files but do not start anything afterwards
                    (only ever makes behavior more conservative)

There is no argument for the new files: the command-line entry (main())
always uses the folder holding the running exe. No option relaxes any check.

After success the new "<target>\\FloweyPet.exe --updated-from <old version>"
is started; after a failed (rolled back) update the old
"<target>\\FloweyPet.exe --update-failed". Exit codes: see EXIT_*.

Safety model: before touching anything a transaction marker
(<target>\\.update_pending.json) is written and every replaced/removed file
is first moved to <target>\\.update_backup\\. Any failure rolls back; if the
process is killed or the power is cut, recover_if_needed() rolls back at the
next start. config.json, stats.json, the backup/marker/manifest files and
any file that is neither in the new package nor in the previous install
manifest (a user's own files) are never touched.

Standard library only (plus the project's version.py, imported lazily).
No PyQt6/config/paths/app_log, and no print: the packaged exe has no stdout.
"""
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime

EXE_NAME = "FloweyPet.exe"
MARKER_NAME = ".update_pending.json"
BACKUP_NAME = ".update_backup"
MANIFEST_NAME = ".install_manifest.json"
PROTECTED_TOP_LEVEL = frozenset(["config.json", "stats.json", BACKUP_NAME, MARKER_NAME, MANIFEST_NAME])

WAIT_TIMEOUT_S = 30.0
POST_WAIT_S = 1.0
RETRIES = 10
RETRY_DELAY_S = 0.5
RECOVER_RETRIES = 2  # at startup, don't make the user wait for locked files
RECOVER_RETRY_DELAY_S = 0.2

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_BAD_ARGS = 2
EXIT_VALIDATION = 3
EXIT_WAIT_TIMEOUT = 4
EXIT_FAILED_ROLLED_BACK = 5
EXIT_ROLLBACK_INCOMPLETE = 6
EXIT_LAUNCH_FAILED = 7

# recover_if_needed() results
RECOVER_NONE = "none"
RECOVER_IN_PROGRESS = "in_progress"
RECOVER_RECOVERED = "recovered"
RECOVER_INCOMPLETE = "incomplete"
RECOVER_BAD_MARKER = "bad_marker"

_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*")


class ApplyError(Exception):
    """A step of the update failed; the caller rolls back."""


# ----------------------------------------------------------------- logging
def log_path():
    """<FLOWEYPET_DATA_DIR>\\logs\\update.log, else %APPDATA%\\FloweyPet\\logs\\update.log,
    else None. Same locations as paths.LOG_DIR, recomputed here because this
    module must not import paths."""
    base = os.environ.get("FLOWEYPET_DATA_DIR")
    if base:
        return os.path.join(base, "logs", "update.log")
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "FloweyPet", "logs", "update.log")
    return None


def _log(message):
    """Appends one line to update.log. Never raises."""
    try:
        path = log_path()
        if not path:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        line = "[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001 - logging must never affect the update
        pass


# --------------------------------------------------------- process helpers
_SYNCHRONIZE = 0x00100000
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_ERROR_ACCESS_DENIED = 5


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


_k32 = None


def _kernel32():
    global _k32
    if _k32 is None:
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(_FILETIME)] * 4
        k.GetProcessTimes.restype = wintypes.BOOL
        _k32 = k
    return _k32


def _handle_creation_time(k, handle):
    created, a, b, c = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
    if not k.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(a), ctypes.byref(b), ctypes.byref(c)):
        return None
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


def get_process_creation_time(pid):
    """Creation time of process `pid` as a FILETIME integer, or None."""
    try:
        k = _kernel32()
        handle = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        try:
            return _handle_creation_time(k, handle)
        finally:
            k.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


def wait_for_process_exit(pid, created, timeout_s):
    """True once the process is gone, False on timeout. Never kills anything.
    A pid that cannot be opened is already gone; one whose creation time
    differs from `created` was reused by another process, so the one we meant
    is gone too."""
    k = _kernel32()
    handle = k.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return True
    try:
        if created is not None:
            actual = _handle_creation_time(k, handle)
            if actual is not None and actual != created:
                return True
        return k.WaitForSingleObject(handle, int(max(0.0, timeout_s) * 1000)) == _WAIT_OBJECT_0
    finally:
        k.CloseHandle(handle)


def is_process_alive(pid, created):
    """True only if process `pid` exists, was created at `created` and has
    not exited. Access denied counts as alive (don't meddle)."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        k = _kernel32()
        handle = k.OpenProcess(_SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            if created is not None:
                actual = _handle_creation_time(k, handle)
                if actual is not None and actual != created:
                    return False
            return k.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
        finally:
            k.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return True  # unknown: assume alive, so nothing gets rolled back under a live updater


def default_launcher(exe_path, args, cwd):
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    subprocess.Popen([exe_path] + list(args), cwd=cwd, close_fds=True, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def is_running_from_temp(exe_dir=None):
    """True if the running (frozen) program lives under the system temp
    folder, i.e. it is an unpacked update, not a real installation."""
    try:
        if exe_dir is None:
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        here = os.path.normcase(os.path.realpath(exe_dir))
        for tmp in {tempfile.gettempdir(), os.path.realpath(tempfile.gettempdir())}:
            root = os.path.normcase(os.path.realpath(tmp))
            if here == root or here.startswith(root.rstrip("\\/") + os.sep):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ------------------------------------------------------- file primitives
# Thin module-level wrappers so tests can inject failures at exactly one
# place; all of them are called through _Ctx.run (PermissionError retries).
def _replace(src, dst):
    os.replace(src, dst)


def _copy(src, dst):
    shutil.copy2(src, dst)


def _remove(path):
    os.remove(path)


def _rmtree(path):
    shutil.rmtree(path)


def _write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _Ctx:
    def __init__(self, target, retries=RETRIES, delay=RETRY_DELAY_S, sleep=time.sleep):
        self.target = target
        self.backup = os.path.join(target, BACKUP_NAME)
        self.marker = os.path.join(target, MARKER_NAME)
        self.retries = retries
        self.delay = delay
        self.sleep = sleep

    def run(self, fn, *args):
        """fn(*args), retrying on PermissionError (antivirus / locked file)."""
        for attempt in range(self.retries + 1):
            try:
                return fn(*args)
            except PermissionError:
                if attempt >= self.retries:
                    raise
                self.sleep(self.delay)

    def path(self, rel):
        return os.path.join(self.target, *rel.split("/"))

    def backup_path(self, rel):
        return os.path.join(self.backup, *rel.split("/"))


# ------------------------------------------------------------ path rules
def _safe_rel(rel):
    """Normalized relative path with '/' separators, or None if it is
    absolute, has a drive/stream colon, empty or '.'/'..' segments."""
    if not isinstance(rel, str) or not rel:
        return None
    rel = rel.replace("\\", "/")
    if rel.startswith("/") or ":" in rel:
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    if any(ord(c) < 32 for c in rel):
        return None
    return "/".join(parts)


def _is_protected(rel):
    low = rel.casefold()
    if low.split("/")[0] == BACKUP_NAME.casefold():
        return True
    if "/" in low:
        return False
    return low in {n.casefold() for n in PROTECTED_TOP_LEVEL} or low.startswith(MARKER_NAME.casefold()) \
        or low.startswith(MANIFEST_NAME.casefold())


def _scan_package(new_dir):
    """{rel: absolute path} of every file under new_dir that may be installed."""
    found = {}
    skipped = []
    for dirpath, dirnames, filenames in os.walk(new_dir):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = _safe_rel(os.path.relpath(full, new_dir))
            if rel is None or _is_protected(rel) or os.path.islink(full):
                skipped.append(rel or name)
                continue
            found[rel] = full
    return found, skipped


def _ordered(rels):
    """Everything in a stable order with FloweyPet.exe last."""
    rels = sorted(rels)
    exe = [r for r in rels if r == EXE_NAME]
    return [r for r in rels if r != EXE_NAME] + exe


def _load_manifest(target):
    path = os.path.join(target, MANIFEST_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        files = data.get("files")
        if not isinstance(files, list):
            raise ValueError("files is not a list")
    except FileNotFoundError:
        return []
    except Exception as e:  # noqa: BLE001
        _log(f"旧安装清单读不了（{type(e).__name__}），按空清单处理")
        return []
    rels = []
    for item in files:
        rel = _safe_rel(item)
        if rel is None:
            _log("旧安装清单里有不安全的路径，已忽略")
            continue
        if not _is_protected(rel):
            rels.append(rel)
    return rels


# ------------------------------------------------------------ the marker
def _new_state(target, pid, created, old_version, new_version, package_files):
    return {
        "format": 1,
        "phase": "starting",
        "pid": pid,
        "created": created,
        "old_version": old_version,
        "new_version": new_version,
        "added": [],
        "added_dirs": [],
        "package_files": list(package_files),
        "backup_dir": os.path.join(target, BACKUP_NAME),
        "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _save_marker(ctx, state):
    _write_json_atomic(ctx.marker, state)


def _set_phase(ctx, state, phase):
    state["phase"] = phase
    _save_marker(ctx, state)
    _log(f"阶段：{phase}")


# --------------------------------------------------------------- rollback
def _rollback(ctx, state):
    """Undoes a partial update using the marker's lists and the backup
    folder. Keeps going after individual errors. Returns True if everything
    was restored (marker deleted); otherwise the marker stays, with
    phase=rollback_incomplete and no live pid, so the next start retries."""
    errors = 0
    _log("开始回滚")

    for rel in state.get("package_files", []):
        rel = _safe_rel(rel)
        if rel is None:
            continue
        tmp = ctx.path(rel) + ".new"
        if os.path.lexists(tmp):
            try:
                ctx.run(_remove, tmp)
            except OSError as e:
                errors += 1
                _log(f"回滚：删除临时文件失败 {rel}.new（{type(e).__name__}）")

    for rel in state.get("added", []):
        rel = _safe_rel(rel)
        if rel is None:
            continue
        p = ctx.path(rel)
        if os.path.lexists(p):
            try:
                ctx.run(_remove, p)
            except OSError as e:
                errors += 1
                _log(f"回滚：删除新增文件失败 {rel}（{type(e).__name__}）")

    restored = 0
    if os.path.isdir(ctx.backup):
        for dirpath, dirnames, filenames in os.walk(ctx.backup):
            dirnames.sort()
            for name in sorted(filenames):
                src = os.path.join(dirpath, name)
                rel = _safe_rel(os.path.relpath(src, ctx.backup))
                if rel is None:
                    errors += 1
                    _log("回滚：备份里有不安全的路径，已跳过")
                    continue
                dst = ctx.path(rel)
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    ctx.run(_replace, src, dst)
                    restored += 1
                except OSError as e:
                    errors += 1
                    _log(f"回滚：还原失败 {rel}（{type(e).__name__}）")

    for rel in sorted((r for r in state.get("added_dirs", []) if _safe_rel(r)), key=lambda r: -r.count("/")):
        try:
            os.rmdir(ctx.path(_safe_rel(rel)))
        except OSError:
            pass

    if errors:
        state["phase"] = "rollback_incomplete"
        state["pid"] = None
        state["created"] = None
        try:
            _save_marker(ctx, state)
        except OSError as e:
            _log(f"回滚：更新事务标记失败（{type(e).__name__}）")
        _log(f"回滚未完成：还原 {restored} 个文件，{errors} 处出错；保留事务标记，下次启动再试")
        return False

    try:
        if os.path.isdir(ctx.backup):
            ctx.run(_rmtree, ctx.backup)
    except OSError as e:
        _log(f"回滚：清理备份目录失败（{type(e).__name__}）")
    try:
        ctx.run(_remove, ctx.marker)
    except FileNotFoundError:
        pass
    except OSError as e:
        _log(f"回滚：删除事务标记失败（{type(e).__name__}）")
        return False
    _log(f"回滚完成：还原 {restored} 个文件")
    return True


# ----------------------------------------------------------------- apply
def _validate(target, new_dir):
    if not os.path.isdir(target):
        return "target 不是文件夹"
    if not os.path.isfile(os.path.join(target, EXE_NAME)):
        return f"target 里没有 {EXE_NAME}"
    if not os.path.isdir(new_dir):
        return "新文件目录不是文件夹"
    if not os.path.isfile(os.path.join(new_dir, EXE_NAME)):
        return f"新文件目录里没有 {EXE_NAME}"
    a = os.path.normcase(os.path.realpath(target))
    b = os.path.normcase(os.path.realpath(new_dir))
    if a == b:
        return "target 与新文件目录相同"
    try:
        common = os.path.commonpath([a, b])
    except ValueError:
        common = None
    if common in (a, b):
        return "target 与新文件目录互相包含"
    if os.path.lexists(os.path.join(target, MARKER_NAME)):
        return "target 里已有 .update_pending.json（上次更新没有收尾）"
    return None


def _missing_dirs(target, rel):
    """Relative directories (top-most first) that `rel`'s parents still lack."""
    parts = rel.split("/")[:-1]
    missing = []
    for i in range(1, len(parts) + 1):
        d = "/".join(parts[:i])
        if not os.path.isdir(os.path.join(target, *d.split("/"))):
            missing.append(d)
    return missing


def _install_files(ctx, state, package):
    old_manifest = _load_manifest(ctx.target)
    package_keys = {r.casefold() for r in package}
    obsolete = [r for r in old_manifest if r.casefold() not in package_keys]

    for rel in _ordered(package):
        src = package[rel]
        dst = ctx.path(rel)
        tmp = dst + ".new"
        if os.path.isdir(dst) and not os.path.islink(dst):
            raise ApplyError(f"目标位置是文件夹：{rel}")
        existed = os.path.lexists(dst)
        if not existed:
            new_dirs = _missing_dirs(ctx.target, rel)
            state["added"].append(rel)
            state["added_dirs"].extend(d for d in new_dirs if d not in state["added_dirs"])
            _save_marker(ctx, state)
            if new_dirs:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
        ctx.run(_copy, src, tmp)
        if existed:
            bk = ctx.backup_path(rel)
            os.makedirs(os.path.dirname(bk), exist_ok=True)
            ctx.run(_replace, dst, bk)
        ctx.run(_replace, tmp, dst)

    for rel in sorted(obsolete):
        p = ctx.path(rel)
        if os.path.lexists(p) and not os.path.isdir(p):
            bk = ctx.backup_path(rel)
            os.makedirs(os.path.dirname(bk), exist_ok=True)
            ctx.run(_replace, p, bk)
            _log(f"旧版独有的文件已移到备份：{rel}")
    return old_manifest


def _verify(ctx, package, new_dir):
    for rel, src in package.items():
        dst = ctx.path(rel)
        if not os.path.isfile(dst):
            raise ApplyError(f"校验失败：缺少 {rel}")
        if os.path.getsize(dst) != os.path.getsize(src):
            raise ApplyError(f"校验失败：大小不一致 {rel}")
    if _sha256_of(ctx.path(EXE_NAME)) != _sha256_of(os.path.join(new_dir, EXE_NAME)):
        raise ApplyError(f"校验失败：{EXE_NAME} 的 SHA256 不一致")


def _finalize(ctx, state, package, new_version):
    """Writes the new install manifest inside the transaction (the old one is
    moved to the backup first, so a rollback restores it too)."""
    mpath = os.path.join(ctx.target, MANIFEST_NAME)
    if os.path.lexists(mpath):
        bk = ctx.backup_path(MANIFEST_NAME)
        os.makedirs(os.path.dirname(bk), exist_ok=True)
        ctx.run(_replace, mpath, bk)
    else:
        state["added"].append(MANIFEST_NAME)
        _save_marker(ctx, state)
    _write_json_atomic(mpath, {"version": new_version, "files": _ordered(package)})


def apply_update(target, new_dir, wait_pid, wait_created, old_version, new_version=None, restart=True, *,
                 waiter=None, launcher=None, sleep=time.sleep, self_info=None, wait_timeout=WAIT_TIMEOUT_S,
                 post_wait=POST_WAIT_S, retries=RETRIES, retry_delay=RETRY_DELAY_S):
    """Applies the update; returns an EXIT_* code. waiter/launcher/sleep/
    self_info exist so tests can run this without real processes."""
    target = os.path.abspath(os.fspath(target))
    new_dir = os.path.abspath(os.fspath(new_dir))
    waiter = wait_for_process_exit if waiter is None else waiter
    launcher = default_launcher if launcher is None else launcher
    if new_version is None:
        try:
            import version
            new_version = version.VERSION
        except Exception:  # noqa: BLE001
            new_version = "unknown"
    _log(f"应用更新：{old_version} -> {new_version}，目标 {target}")

    def launch(args, what):
        if not restart:
            _log(f"--no-restart：不启动{what}")
            return True
        try:
            launcher(os.path.join(target, EXE_NAME), args, target)
            _log(f"已启动{what}：{' '.join(args)}")
            return True
        except Exception as e:  # noqa: BLE001
            _log(f"启动{what}失败（{type(e).__name__}）")
            return False

    problem = _validate(target, new_dir)
    if problem:
        _log(f"校验失败：{problem}")
        if os.path.isfile(os.path.join(target, EXE_NAME)):
            launch(["--update-failed"], "旧版")
        return EXIT_VALIDATION

    _log(f"等待旧进程 {wait_pid} 退出（最多 {wait_timeout:g} 秒）")
    try:
        exited = waiter(wait_pid, wait_created, wait_timeout)
    except Exception as e:  # noqa: BLE001
        _log(f"等待旧进程时出错（{type(e).__name__}）")
        return EXIT_WAIT_TIMEOUT
    if not exited:
        _log("旧进程仍未退出，放弃更新（不会强杀）")
        return EXIT_WAIT_TIMEOUT
    sleep(post_wait)

    ctx = _Ctx(target, retries, retry_delay, sleep)
    state = None
    try:
        package, skipped = _scan_package(new_dir)
        if EXE_NAME not in package:
            raise ApplyError(f"新文件里没有 {EXE_NAME}")
        if skipped:
            _log(f"新包里有 {len(skipped)} 个文件被跳过（受保护或不安全）")
        if os.path.lexists(ctx.backup):
            _log("删除上一次更新留下的备份目录")
            ctx.run(_rmtree, ctx.backup)
        pid, created = self_info if self_info is not None else (os.getpid(), get_process_creation_time(os.getpid()))
        state = _new_state(target, pid, created, old_version, new_version, _ordered(package))
        _save_marker(ctx, state)
    except Exception as e:  # noqa: BLE001 - nothing in the install folder has been touched yet
        _log(f"准备阶段失败，未改动任何文件：{type(e).__name__}: {e}")
        if state is not None:
            try:
                os.remove(ctx.marker)
            except OSError:
                pass
        launch(["--update-failed"], "旧版")
        return EXIT_FAILED_ROLLED_BACK

    try:
        os.makedirs(ctx.backup, exist_ok=True)
        _set_phase(ctx, state, "replacing")
        _install_files(ctx, state, package)
        _set_phase(ctx, state, "verifying")
        _verify(ctx, package, new_dir)
        _set_phase(ctx, state, "finalizing")
        _finalize(ctx, state, package, new_version)
        ctx.run(_remove, ctx.marker)
    except Exception as e:  # noqa: BLE001 - BaseException (a killed process) deliberately skips this: recover_if_needed() handles it
        _log(f"更新失败：{type(e).__name__}: {e}")
        complete = _rollback(ctx, state)
        launch(["--update-failed"], "旧版")
        return EXIT_FAILED_ROLLED_BACK if complete else EXIT_ROLLBACK_INCOMPLETE

    _log("文件替换完成并通过校验")
    if not launch(["--updated-from", old_version], "新版"):
        return EXIT_LAUNCH_FAILED
    return EXIT_OK


# --------------------------------------------------------------- recovery
def recover_if_needed(target, *, is_alive=None, retries=RECOVER_RETRIES, retry_delay=RECOVER_RETRY_DELAY_S,
                      sleep=time.sleep):
    """For program start-up. Returns one of the RECOVER_* strings:
    none         no marker, nothing done
    in_progress  the marker's apply process is still alive: leave everything
    recovered    interrupted update rolled back completely
    incomplete   rollback could not restore everything (marker kept)
    bad_marker   marker unreadable: renamed to .update_pending.json.bad-<time>,
                 no other file touched"""
    target = os.path.abspath(os.fspath(target))
    ctx = _Ctx(target, retries, retry_delay, sleep)
    if not os.path.lexists(ctx.marker):
        return RECOVER_NONE
    is_alive = is_process_alive if is_alive is None else is_alive

    try:
        with open(ctx.marker, "r", encoding="utf-8") as f:
            state = json.load(f)
        if not isinstance(state, dict) or not isinstance(state.get("phase"), str):
            raise ValueError("not a transaction marker")
        for key in ("added", "added_dirs", "package_files"):
            if not isinstance(state.get(key, []), list):
                raise ValueError(f"{key} is not a list")
        pid, created = state.get("pid"), state.get("created")
        if pid is not None and (isinstance(pid, bool) or not isinstance(pid, int)):
            raise ValueError("pid is not an int")
        if created is not None and (isinstance(created, bool) or not isinstance(created, int)):
            raise ValueError("created is not an int")
    except Exception as e:  # noqa: BLE001
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bad = ctx.marker + ".bad-" + stamp
        try:
            os.replace(ctx.marker, bad)
            _log(f"事务标记读不了（{type(e).__name__}），已改名为 {os.path.basename(bad)}，没有改动任何其他文件")
        except OSError as e2:
            _log(f"事务标记读不了且改名失败（{type(e2).__name__}）")
        return RECOVER_BAD_MARKER

    if pid is not None and is_alive(pid, created):
        _log("发现事务标记，但负责更新的进程还在运行：不处理")
        return RECOVER_IN_PROGRESS

    _log(f"发现被中断的更新（阶段 {state['phase']}，{state.get('old_version')} -> {state.get('new_version')}），开始恢复")
    complete = _rollback(ctx, state)
    return RECOVER_RECOVERED if complete else RECOVER_INCOMPLETE


# ----------------------------------------------------------- command line
def _parse_args(argv):
    """Returns a dict or None if the command line does not follow the contract."""
    opts = {"restart": True}
    seen_apply = False
    i = 0
    values = {"--target": "target", "--wait-pid": "pid", "--wait-created": "created", "--old-version": "old_version"}
    while i < len(argv):
        arg = argv[i]
        if arg == "--apply-update":
            seen_apply = True
        elif arg == "--no-restart":
            opts["restart"] = False
        elif arg in values:
            if i + 1 >= len(argv):
                return None
            opts[values[arg]] = argv[i + 1]
            i += 1
        else:
            return None
        i += 1
    if not seen_apply or not all(k in opts for k in ("target", "pid", "created", "old_version")):
        return None
    try:
        opts["pid"] = int(opts["pid"])
        opts["created"] = int(opts["created"])
    except ValueError:
        return None
    if opts["pid"] <= 0 or opts["created"] < 0 or not opts["target"]:
        return None
    if not _VERSION_RE.fullmatch(opts["old_version"]):
        return None
    return opts


def main(argv):
    """Command-line entry. argv excludes the program name. Never raises;
    returns an EXIT_* code."""
    try:
        opts = _parse_args(list(argv))
        if opts is None:
            _log("命令行参数不符合约定，未做任何事")
            return EXIT_BAD_ARGS
        new_dir = os.path.dirname(os.path.abspath(sys.executable))
        return apply_update(opts["target"], new_dir, opts["pid"], opts["created"], opts["old_version"],
                            restart=opts["restart"])
    except Exception as e:  # noqa: BLE001
        _log(f"应用更新出现意外错误：{type(e).__name__}: {e}")
        return EXIT_UNEXPECTED
