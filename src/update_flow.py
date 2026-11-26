# -*- coding: utf-8 -*-
"""One-click update: the decision and the flow behind the tray entry
"更新到 vX".

Kept out of pet_window.py so it can be tested without a window, and free of
Qt, config, paths and app_log: PetWindow hands in everything it needs (the
install folder, "is this a source run", the speak / open-page / quit hooks,
a log function). Pieces:

  can_one_click_update()   pure decision -> (ok, reason)
  fetch_package()          download + verify + unpack into a fresh temp
                           folder (runs on a worker thread)
  UpdateController         click handling, single-task guard, result
                           handling on the main thread, cancellation
  cleanup_old_update_dirs  removes leftover FloweyPet-update-* folders
  startup_update_category  which line to say after an update / rollback

No log line or spoken line produced here ever contains a URL.
"""
import os
import shutil
import tempfile
import threading

import update_download
import update_unpack
import updater_apply

TEMP_PREFIX = "FloweyPet-update-"

# can_one_click_update() reasons
OK = "ok"
REASON_DEV = "dev"
REASON_NO_PACKAGE = "no_package_info"
REASON_TEMP_DIR = "running_from_temp"
REASON_NOT_WRITABLE = "not_writable"
REASON_PENDING = "update_pending"


class UpdateFlowError(Exception):
    """A step of fetch_package failed. `code` looks like "download:hash_mismatch";
    `tmp_dir` is the temp folder to delete (or None)."""

    def __init__(self, code, tmp_dir=None):
        self.code = code
        self.tmp_dir = tmp_dir
        super().__init__(code)


# ------------------------------------------------------------- decision
def _probe_writable(app_dir):
    """Creates and deletes a throw-away file inside app_dir."""
    path = os.path.join(app_dir, f".write_probe_{os.getpid()}")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        os.remove(path)
        return True
    except OSError:
        return False


def can_one_click_update(info, is_dev, app_dir, running_from_temp=None, probe_writable=None):
    """(can, reason). `reason` is OK when allowed, else one of the REASON_*
    codes. When not allowed the tray entry just opens the download page."""
    if is_dev:
        return False, REASON_DEV
    if not isinstance(info, dict) or any(info.get(k) is None for k in ("zip_url", "sha256", "size")):
        return False, REASON_NO_PACKAGE
    in_temp = updater_apply.is_running_from_temp(app_dir) if running_from_temp is None else running_from_temp
    if in_temp:
        return False, REASON_TEMP_DIR
    if not (_probe_writable if probe_writable is None else probe_writable)(app_dir):
        return False, REASON_NOT_WRITABLE
    if os.path.lexists(os.path.join(app_dir, updater_apply.MARKER_NAME)):
        return False, REASON_PENDING
    return True, OK


# ---------------------------------------------------- download + unpack
def fetch_package(info, cancel_event=None, *, download=None, unpack=None, mkdtemp=None, progress_cb=None):
    """Downloads info["zip_url"], verifies size and SHA-256, unpacks it.
    Returns (tmp_dir, new_exe_path). Raises UpdateFlowError(code, tmp_dir);
    the temp folder is left for the caller to delete."""
    download = update_download.download if download is None else download
    unpack = update_unpack.unpack if unpack is None else unpack
    mkdtemp = tempfile.mkdtemp if mkdtemp is None else mkdtemp
    version = info["version"]
    try:
        tmp = mkdtemp(prefix=TEMP_PREFIX)
    except OSError:
        raise UpdateFlowError("tmp_dir") from None
    try:
        zip_path = os.path.join(tmp, f"FloweyPet-v{version}.zip")
        download(info["zip_url"], zip_path, info["sha256"], info["size"], progress_cb=progress_cb,
                 cancel_event=cancel_event)
        top = unpack(zip_path, os.path.join(tmp, "unpacked"), version)
        return tmp, os.path.join(os.fspath(top), updater_apply.EXE_NAME)
    except update_download.UpdateDownloadError as e:
        raise UpdateFlowError("download:" + e.code, tmp) from None
    except update_unpack.UpdateUnpackError as e:
        raise UpdateFlowError("unpack:" + e.code, tmp) from None
    except Exception as e:  # noqa: BLE001
        raise UpdateFlowError("unexpected:" + type(e).__name__, tmp) from None


def build_apply_args(target, pid, created, old_version):
    """The command line documented in updater_apply's docstring."""
    return ["--apply-update", "--target", os.fspath(target), "--wait-pid", str(int(pid)),
            "--wait-created", str(int(created)), "--old-version", str(old_version)]


def remove_dir(path):
    """Best-effort recursive delete. Never raises. True if gone."""
    if not path:
        return True
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
    return not os.path.exists(path)


def cleanup_old_update_dirs(temp_root=None, log=None):
    """Deletes <temp>/FloweyPet-update-* folders (leftovers of earlier
    updates). Only folders whose name starts with that prefix are touched;
    anything that cannot be deleted is skipped. Never raises. Returns the
    number removed."""
    removed = skipped = 0
    try:
        root = tempfile.gettempdir() if temp_root is None else os.fspath(temp_root)
        for name in os.listdir(root):
            if not name.startswith(TEMP_PREFIX):
                continue
            path = os.path.join(root, name)
            if os.path.islink(path) or not os.path.isdir(path):
                continue
            if remove_dir(path):
                removed += 1
            else:
                skipped += 1
        if (removed or skipped) and log is not None:
            log(f"清理旧的更新临时目录：删除 {removed} 个，{skipped} 个删不掉已跳过")
    except Exception:  # noqa: BLE001
        pass
    return removed


def startup_update_category(argv):
    """Pool to speak at the first appearance after an update, or None.
    --update-failed wins when both flags are present."""
    argv = list(argv)
    if "--update-failed" in argv:
        return "update_rolled_back"
    if "--updated-from" in argv:
        return "update_done"
    return None


# ------------------------------------------------------------ controller
class _Task:
    def __init__(self, info):
        self.info = info
        self.cancel = threading.Event()
        self.applying = False


class UpdateController:
    """Everything between the click and the quit. All hooks are injected:

    can_update(info) -> (bool, reason)        fetch(info, cancel_event) -> (tmp_dir, exe)
    launch(exe, args, cwd)                    open_page(url)
    say(category)                             quit_for_update()
    arm_deadline()                            is_quitting() -> bool
    post(result)  called on the worker thread; must deliver the result to
                  on_result() on the main thread
    self_info() -> (pid, creation_time)       remove_dir(path)    log(text)
    on_state_change()                         refresh the tray menu label
    """

    def __init__(self, *, can_update, fetch, launch, open_page, say, quit_for_update, arm_deadline, is_quitting,
                 post, self_info, old_version, app_dir, log=None, remove_dir=remove_dir, on_state_change=None,
                 use_thread=True):
        self._can_update = can_update
        self._fetch = fetch
        self._launch = launch
        self._open_page = open_page
        self._say = say
        self._quit_for_update = quit_for_update
        self._arm_deadline = arm_deadline
        self._is_quitting = is_quitting
        self._post = post
        self._self_info = self_info
        self._old_version = old_version
        self._app_dir = app_dir
        self._log = log or (lambda text: None)
        self._remove_dir = remove_dir
        self._on_state_change = on_state_change or (lambda: None)
        self._use_thread = use_thread
        self._task = None

    @property
    def busy(self):
        return self._task is not None

    @property
    def version(self):
        return self._task.info["version"] if self._task else None

    # ------------------------------------------------------------ click
    def on_click(self, info):
        if self._task is not None:
            self._say("update_busy")
            return
        ok, reason = self._can_update(info)
        if not ok:
            self._log(f"一键更新不可用（{reason}），改为打开下载页")
            self._open_page(info["url"])
            return
        task = _Task(info)
        self._task = task
        self._on_state_change()
        self._say("update_downloading")
        self._log(f"一键更新：开始下载 v{info['version']}")
        if self._use_thread:
            threading.Thread(target=self._work, args=(task,), daemon=True).start()
        else:
            self._work(task)

    def _work(self, task):
        """Worker thread: no UI access, only fetch() and post()."""
        try:
            tmp, exe = self._fetch(task.info, task.cancel)
            result = {"task": task, "ok": True, "tmp_dir": tmp, "exe": exe}
        except UpdateFlowError as e:
            result = {"task": task, "ok": False, "code": e.code, "tmp_dir": e.tmp_dir}
        except Exception as e:  # noqa: BLE001
            result = {"task": task, "ok": False, "code": "unexpected:" + type(e).__name__, "tmp_dir": None}
        if not result["ok"] and task.cancel.is_set():
            self._remove_dir(result.get("tmp_dir"))  # the app is leaving; nobody else will
            result["tmp_dir"] = None
        self._post(result)

    # ----------------------------------------------------------- result
    def on_result(self, result):
        """Main thread."""
        task = result["task"]
        tmp = result.get("tmp_dir")
        if task is not self._task:
            self._remove_dir(tmp)
            return
        if self._is_quitting():
            self._task = None
            self._remove_dir(tmp)
            self._log("一键更新：小花正在退出，不再继续" + ("" if result["ok"] else f"（{result['code']}）"))
            return
        if not result["ok"]:
            self._fail(task, result["code"], tmp)
            return
        try:
            pid, created = self._self_info()
            if created is None:
                raise OSError("no creation time")
            args = build_apply_args(self._app_dir, pid, created, self._old_version)
        except Exception:  # noqa: BLE001
            self._fail(task, "self_info", tmp)
            return
        try:
            self._launch(result["exe"], args, os.path.dirname(result["exe"]))
        except Exception as e:  # noqa: BLE001
            self._fail(task, "launch:" + type(e).__name__, tmp)
            return
        task.applying = True
        self._log("一键更新：更新程序已启动，小花退出")
        self._arm_deadline()
        self._quit_for_update()

    def _fail(self, task, code, tmp):
        self._task = None
        self._remove_dir(tmp)
        self._log(f"一键更新失败：{code}")
        self._say("update_failed")
        self._open_page(task.info["url"])
        self._on_state_change()

    # ----------------------------------------------------------- cancel
    def cancel(self):
        """The user is leaving: stop a running download. A no-op once the
        updater has been launched."""
        task = self._task
        if task is not None and not task.applying:
            task.cancel.set()
