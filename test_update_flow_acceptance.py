# -*- coding: utf-8 -*-
"""Acceptance tests for the one-click update wiring: src/update_flow.py
(decision, fetch, controller, temp-dir cleanup), the PetWindow methods that
hook it to the tray entry (borrowed onto a stand-in QObject), the six new
dialogue pools and main.py's start-up cleanup.

Nothing real happens: download / unpack / launching the updater / opening the
web page are all injected fakes; no network, no FloweyPet.exe is started.
The only real processes are sleeping python.exe copies used to make a temp
folder impossible to delete.

Run:  QT_QPA_PLATFORM=offscreen python test_update_flow_acceptance.py
"""
import atexit
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="floweypet_flow_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import QObject, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMenu  # noqa: E402

app = QApplication(sys.argv)

import app_log  # noqa: E402

app_log.LOG_PATH = TMP / "app.log"

import config as config_mod  # noqa: E402
import pet_window  # noqa: E402
import update_download as ud  # noqa: E402
import update_flow as uf  # noqa: E402
import update_unpack as uu  # noqa: E402
import updater_apply as ua  # noqa: E402
import version  # noqa: E402
from dialogue_manager import DialogueManager  # noqa: E402
from pet_window import PetWindow, State  # noqa: E402

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


def pump(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(cond())


ZIP_URL = "https://github.com/winwinking/FloweyPet/releases/download/v3.4.0/FloweyPet-v3.4.0.zip"
PAGE = "https://github.com/winwinking/FloweyPet/releases/tag/v3.4.0"
INFO = {"version": "3.4.0", "url": PAGE, "line": None, "face": None, "zip_url": ZIP_URL, "sha256": "a" * 64, "size": 1234}
APP_DIR = str(TMP / "install")
os.makedirs(APP_DIR)


def fresh_dm():
    cfg = json.loads(json.dumps(config_mod.DEFAULTS))
    dm = DialogueManager(cfg, lambda c: None, None)
    emitted = []
    dm.show_requested.connect(lambda segs, cat: emitted.append((segs, cat)))
    return dm, emitted


# ------------------------------------------------------------------ 1
print("== 1 能不能一键更新 ==")
ok_probe = lambda d: True  # noqa: E731
bad_probe = lambda d: False  # noqa: E731
check("全部满足 -> (True, ok)", uf.can_one_click_update(INFO, False, APP_DIR, False, ok_probe) == (True, "ok"))
check("源码运行 -> 不能 (dev)", uf.can_one_click_update(INFO, True, APP_DIR, False, ok_probe) == (False, "dev"))
for key in ("zip_url", "sha256", "size"):
    bad = dict(INFO, **{key: None})
    check(f"{key} 是 None -> 不能 (no_package_info)", uf.can_one_click_update(bad, False, APP_DIR, False, ok_probe) == (False, "no_package_info"))
check("信息里没有三个键 -> 不能", uf.can_one_click_update({"version": "3.4.0", "url": PAGE}, False, APP_DIR, False, ok_probe) == (False, "no_package_info"))
check("信息是 None -> 不能", uf.can_one_click_update(None, False, APP_DIR, False, ok_probe) == (False, "no_package_info"))
check("运行在临时目录 -> 不能", uf.can_one_click_update(INFO, False, APP_DIR, True, ok_probe) == (False, "running_from_temp"))
check("安装目录不可写 -> 不能", uf.can_one_click_update(INFO, False, APP_DIR, False, bad_probe) == (False, "not_writable"))
open(os.path.join(APP_DIR, ".update_pending.json"), "w").write("{}")
check("已有 .update_pending.json -> 不能", uf.can_one_click_update(INFO, False, APP_DIR, False, ok_probe) == (False, "update_pending"))
os.remove(os.path.join(APP_DIR, ".update_pending.json"))
check("标记删掉后又能", uf.can_one_click_update(INFO, False, APP_DIR, False, ok_probe)[0] is True)
check("判断顺序: dev 优先于其他原因", uf.can_one_click_update(dict(INFO, size=None), True, APP_DIR, True, bad_probe) == (False, "dev"))
check("判断顺序: 缺字段优先于临时目录", uf.can_one_click_update(dict(INFO, size=None), False, APP_DIR, True, bad_probe) == (False, "no_package_info"))
check("真实探测: 可写目录 -> 可以, 且不留文件",
      uf._probe_writable(APP_DIR) is True and not [n for n in os.listdir(APP_DIR) if n.startswith(".write_probe")])
check("真实探测: 不存在的目录 -> 不可写", uf._probe_writable(str(TMP / "nope" / "nope")) is False)
check("真实路径下 can_one_click_update 不抛异常", isinstance(uf.can_one_click_update(INFO, False, APP_DIR)[0], bool))
check("临时目录判断走 updater_apply.is_running_from_temp",
      uf.can_one_click_update(INFO, False, os.path.join(tempfile.gettempdir(), "FloweyPet-x"), None, ok_probe) == (False, "running_from_temp"))

# ------------------------------------------------------------------ 2
print("== 2 控制器: 夹具 ==")


class Rig:
    """A UpdateController with every hook recorded; worker results are queued
    and delivered to on_result() by pump_results(), like the Qt signal would."""

    def __init__(self, can=(True, "ok"), fetch=None, launcher=None, quitting=False, created=5678, use_thread=True):
        self.events = []
        self.says = []
        self.pages = []
        self.logs = []
        self.launches = []
        self.removed = []
        self.posted = []
        self.quitting = quitting
        self.fetch_calls = 0
        self._fetch = fetch
        self._launcher = launcher
        self.ctl = uf.UpdateController(
            can_update=lambda info: can,
            fetch=self._do_fetch,
            launch=self._do_launch,
            open_page=lambda url: (self.pages.append(url), self.events.append("page")),
            say=lambda c: (self.says.append(c), self.events.append("say:" + c)),
            quit_for_update=lambda: self.events.append("quit_for_update"),
            arm_deadline=lambda: self.events.append("arm_deadline"),
            is_quitting=lambda: self.quitting,
            post=self.posted.append,
            self_info=lambda: (1234, created),
            old_version="3.3.0",
            app_dir=APP_DIR,
            log=self.logs.append,
            remove_dir=self._remove,
            on_state_change=lambda: self.events.append("state"),
            use_thread=use_thread,
        )

    def _do_fetch(self, info, ev):
        self.fetch_calls += 1
        return self._fetch(info, ev)

    def _do_launch(self, exe, args, cwd):
        self.events.append("launch")
        self.launches.append((exe, list(args), cwd))
        if self._launcher:
            self._launcher(exe, args, cwd)

    def _remove(self, path):
        self.removed.append(path)
        return uf.remove_dir(path)

    def pump_results(self, n=1, timeout=10):
        assert pump(lambda: len(self.posted) >= n, timeout), "worker never posted"
        for r in list(self.posted):
            self.ctl.on_result(r)
        self.posted.clear()


def good_fetch(info, ev, ver="3.4.0"):
    tmp = tempfile.mkdtemp(prefix=uf.TEMP_PREFIX, dir=TMP)
    top = os.path.join(tmp, "unpacked", f"FloweyPet-v{ver}")
    os.makedirs(top)
    Path(top, "FloweyPet.exe").write_bytes(b"exe")
    return tmp, os.path.join(top, "FloweyPet.exe")


def fetch_with(download=None, unpack=None):
    def fake_download(url, dest, sha, size, progress_cb=None, cancel_event=None, **kw):
        if download:
            download(url, dest, sha, size, cancel_event)
        Path(dest).write_bytes(b"zip-bytes")

    def fake_unpack(zip_path, dest, ver):
        if unpack:
            unpack(zip_path, dest, ver)
        top = Path(dest) / f"FloweyPet-v{ver}"
        top.mkdir(parents=True)
        (top / "FloweyPet.exe").write_bytes(b"exe")
        return top

    return lambda info, ev: uf.fetch_package(info, ev, download=fake_download, unpack=fake_unpack,
                                             mkdtemp=lambda prefix: tempfile.mkdtemp(prefix=prefix, dir=TMP))


# ------------------------------------------------------------------ 3
print("== 3 不能一键更新: 只打开下载页 ==")
threads_before = threading.active_count()
r = Rig(can=(False, "dev"), fetch=good_fetch)
r.ctl.on_click(INFO)
check("打开下载页 (info['url'])", r.pages == [PAGE])
check("不说任何台词", r.says == [])
check("没有开线程 / 没有调用 fetch", r.fetch_calls == 0 and threading.active_count() == threads_before)
check("不进入进行中状态", r.ctl.busy is False and "state" not in r.events)
check("记了一行原因日志, 没有网址", any("dev" in l for l in r.logs) and not any("http" in l for l in r.logs), repr(r.logs))
check("不启动任何东西", r.launches == [])

# ------------------------------------------------------------------ 4
print("== 4 成功路径 ==")
r = Rig(fetch=good_fetch)
r.ctl.on_click(INFO)
check("点击后进入进行中", r.ctl.busy is True and r.ctl.version == "3.4.0")
check("说 update_downloading", r.says == ["update_downloading"], repr(r.says))
r.pump_results()
exe, args, cwd = r.launches[0]
check("启动参数逐字一致 (约定)", args == ["--apply-update", "--target", APP_DIR, "--wait-pid", "1234", "--wait-created", "5678",
                                       "--old-version", "3.3.0"], repr(args))
check("启动的是解压出来的 FloweyPet.exe, cwd=它所在目录",
      exe.endswith(os.path.join("unpacked", "FloweyPet-v3.4.0", "FloweyPet.exe")) and cwd == os.path.dirname(exe), repr((exe, cwd)))
check("顺序: 先启动更新程序, 再设兜底定时器, 再走更新专用退出",
      [e for e in r.events if e in ("launch", "arm_deadline", "quit_for_update")] == ["launch", "arm_deadline", "quit_for_update"], repr(r.events))
check("成功后临时目录保留 (更新程序要从那里运行)", os.path.isdir(os.path.dirname(os.path.dirname(os.path.dirname(exe)))) and r.removed == [])
check("成功后没有打开下载页, 没说 update_failed", r.pages == [] and "update_failed" not in r.says)
check("成功后仍是进行中 (菜单保持'正在更新')", r.ctl.busy is True)
parsed = ua._parse_args(args)
check("参数能被 updater_apply 的解析器接受 (约定闭环)",
      parsed == {"restart": True, "target": APP_DIR, "pid": 1234, "created": 5678, "old_version": "3.3.0"}, repr(parsed))
check("日志没有网址", not any("http" in l for l in r.logs), repr(r.logs))
r.ctl.cancel()
check("更新程序已启动后 cancel() 不再取消", r.ctl._task.cancel.is_set() is False)
shutil.rmtree(os.path.dirname(os.path.dirname(os.path.dirname(exe))), ignore_errors=True)

# ------------------------------------------------------------------ 5
print("== 5 各种失败 -> update_failed + 打开下载页 + 删临时目录 + 日志 ==")
cases = {
    "download:network": fetch_with(download=lambda *a: (_ for _ in ()).throw(ud.UpdateDownloadError("network", "x"))),
    "download:hash_mismatch": fetch_with(download=lambda *a: (_ for _ in ()).throw(ud.UpdateDownloadError("hash_mismatch"))),
    "download:size_mismatch": fetch_with(download=lambda *a: (_ for _ in ()).throw(ud.UpdateDownloadError("size_mismatch"))),
    "download:bad_host": fetch_with(download=lambda *a: (_ for _ in ()).throw(ud.UpdateDownloadError("bad_host"))),
    "download:no_space": fetch_with(download=lambda *a: (_ for _ in ()).throw(ud.UpdateDownloadError("no_space"))),
    "unpack:bad_path": fetch_with(unpack=lambda *a: (_ for _ in ()).throw(uu.UpdateUnpackError("bad_path", "x"))),
    "unpack:missing_exe": fetch_with(unpack=lambda *a: (_ for _ in ()).throw(uu.UpdateUnpackError("missing_exe"))),
    "unpack:wrong_version": fetch_with(unpack=lambda *a: (_ for _ in ()).throw(uu.UpdateUnpackError("wrong_version"))),
    "unexpected:RuntimeError": fetch_with(download=lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))),
}
for code, fetcher in cases.items():
    r = Rig(fetch=fetcher)
    r.ctl.on_click(INFO)
    r.pump_results()
    check(f"{code}: 说 update_failed", r.says == ["update_downloading", "update_failed"], repr(r.says))
    check(f"{code}: 打开下载页", r.pages == [PAGE])
    check(f"{code}: 临时目录已删除", len(r.removed) == 1 and not os.path.exists(r.removed[0]), repr(r.removed))
    check(f"{code}: 日志有原因代码", f"一键更新失败：{code}" in r.logs, repr(r.logs))
    check(f"{code}: 日志和台词里没有网址", not any("http" in l or "github" in l for l in r.logs))
    check(f"{code}: 没启动更新程序, 没退出", r.launches == [] and "quit_for_update" not in r.events and "arm_deadline" not in r.events)
    check(f"{code}: 不再是进行中 (可以重试)", r.ctl.busy is False)

r = Rig(fetch=good_fetch, launcher=lambda *a: (_ for _ in ()).throw(OSError("cannot start")))
r.ctl.on_click(INFO)
r.pump_results()
check("启动更新程序失败: update_failed + 打开下载页", r.says == ["update_downloading", "update_failed"] and r.pages == [PAGE], repr((r.says, r.pages)))
check("启动更新程序失败: 临时目录被删", len(r.removed) == 1 and not os.path.exists(r.removed[0]))
check("启动更新程序失败: 日志里有原因", any(l.startswith("一键更新失败：launch:OSError") for l in r.logs), repr(r.logs))
check("启动更新程序失败: 没有走退出", "quit_for_update" not in r.events and "arm_deadline" not in r.events)
r = Rig(fetch=good_fetch, created=None)
r.ctl.on_click(INFO)
r.pump_results()
check("取不到本进程创建时间 -> 按失败处理", r.launches == [] and r.pages == [PAGE] and "一键更新失败：self_info" in r.logs, repr(r.logs))
r = Rig(fetch=cases["download:network"])
r.ctl.on_click(INFO)
r.pump_results()
r.ctl.on_click(INFO)
check("失败后可以再点一次重新开始", r.ctl.busy is True and r.fetch_calls == 2)
r.pump_results()

# ------------------------------------------------------------------ 6
print("== 6 进行中再点 ==")
release = threading.Event()


def blocking_fetch(info, ev):
    release.wait(10)
    return good_fetch(info, ev)


r = Rig(fetch=blocking_fetch)
r.ctl.on_click(INFO)
r.ctl.on_click(INFO)
r.ctl.on_click(INFO)
check("再点只说 update_busy (两次点击两句)", r.says == ["update_downloading", "update_busy", "update_busy"], repr(r.says))
time.sleep(0.2)
check("始终只有一个任务 (fetch 只被调用一次)", r.fetch_calls == 1)
check("没有重复打开下载页", r.pages == [])
release.set()
r.pump_results()
check("放行后只启动一次更新程序", len(r.launches) == 1 and r.fetch_calls == 1)
shutil.rmtree(os.path.dirname(os.path.dirname(os.path.dirname(r.launches[0][0]))), ignore_errors=True)

# ------------------------------------------------------------------ 7
print("== 7 下载中退出 / 回到主线程时已在退出 ==")
started = threading.Event()
saw_cancel = []


def cancellable_fetch(info, ev):
    tmp = tempfile.mkdtemp(prefix=uf.TEMP_PREFIX, dir=TMP)
    started.set()
    if ev.wait(10):
        saw_cancel.append(True)
    raise uf.UpdateFlowError("download:cancelled", tmp)


r = Rig(fetch=cancellable_fetch)
r.ctl.on_click(INFO)
check("下载线程已开始", started.wait(5))
r.quitting = True
r.ctl.cancel()
r.pump_results()
check("cancel_event 被设置, 下载收到取消", saw_cancel == [True])
check("没有启动更新程序", r.launches == [])
check("正在退出: 不打开下载页, 不说 update_failed", r.pages == [] and "update_failed" not in r.says, repr((r.pages, r.says)))
_real = [x for x in r.removed if x]
check("临时目录已被删除 (由下载线程自己)", len(_real) == 1 and not os.path.exists(_real[0]), repr(r.removed))
check("任务结束", r.ctl.busy is False)

r = Rig(fetch=good_fetch)
r.ctl.on_click(INFO)
r.quitting = True  # the user chose 退出 after the download finished, before the result was handled
r.pump_results()
check("成功回来时小花已在退出: 什么都不启动", r.launches == [] and "arm_deadline" not in r.events and "quit_for_update" not in r.events)
check("成功回来时已在退出: 临时目录被清理", len(r.removed) == 1 and not os.path.exists(r.removed[0]), repr(r.removed))
check("成功回来时已在退出: 不打开下载页", r.pages == [])
r = Rig(fetch=good_fetch)
r.ctl.on_click(INFO)
r.ctl._task = None  # stale result of a task that is no longer the current one
r.pump_results()
check("过期任务的结果: 不启动, 清理临时目录", r.launches == [] and len(r.removed) == 1)
r = Rig(fetch=good_fetch)
r.ctl.cancel()
check("没有任务时 cancel() 不出错", True)

# ------------------------------------------------------------------ 8
print("== 8 接到 PetWindow: 借用真实方法的 stand-in ==")


class Harness(QObject):
    _update_result = pyqtSignal(object)

    _on_update_action = PetWindow._on_update_action
    _on_update_result = PetWindow._on_update_result
    _refresh_update_menu = PetWindow._refresh_update_menu
    _say_update_line = PetWindow._say_update_line
    _arm_update_quit_deadline = PetWindow._arm_update_quit_deadline
    _maybe_say_startup_update_line = PetWindow._maybe_say_startup_update_line
    request_quit = PetWindow.request_quit
    request_quit_for_update = PetWindow.request_quit_for_update
    _begin_quit = PetWindow._begin_quit
    _init_update_flow = PetWindow._init_update_flow

    def __init__(self, visible=True):
        super().__init__()
        self.dm, self.emitted = fresh_dm()
        self._visible = visible
        self._pending_quit = False
        self._update_info = dict(INFO)
        self._tray = MagicMock()
        self.quit_now_calls = 0
        self._exit_interrupted = False
        self._resume_exit_animation_after_interrupt = False
        self.reminders = MagicMock()
        self.dialogue_box = MagicMock()
        self.state = None
        self._current_category = None
        self.burrow_calls = 0
        self.menu_builds = 0
        self._update_quit_timer = None

    def isVisible(self):
        return self._visible

    def setVisible(self, v):
        self._visible = v

    def _close_active_popup(self):
        pass

    def _position_dialogue_box(self):
        pass

    def _on_segment_face(self, face):
        pass

    def _start_burrow_animation(self, cb):
        self.burrow_calls += 1

    def _quit_now(self):
        self.quit_now_calls += 1
        self._pending_quit = True

    def _build_menu(self):
        self.menu_builds += 1
        return QMenu()

    def _open_update_page(self, url):
        self.pages.append(url)


def make_harness(fetch=good_fetch, visible=True, can=(True, "ok"), launcher=None):
    h = Harness(visible)
    h.pages = []
    h.launches = []
    h.logs = []
    h._update_ctl = uf.UpdateController(
        can_update=lambda info: can, fetch=fetch,
        launch=lambda exe, args, cwd: (h.launches.append((exe, list(args), cwd)), launcher and launcher())[0],
        open_page=lambda url: h._open_update_page(url), say=h._say_update_line, quit_for_update=h.request_quit_for_update,
        arm_deadline=h._arm_update_quit_deadline, is_quitting=lambda: h._pending_quit, post=h._update_result.emit,
        self_info=lambda: (4321, 8765), old_version="3.3.0", app_dir=APP_DIR, log=h.logs.append,
        on_state_change=h._refresh_update_menu)
    h._update_result.connect(h._on_update_result)
    return h


h = make_harness()
h._on_update_action()
check("点击 -> 说 update_downloading (经 dm 发出)", [c for _, c in h.emitted] == ["update_downloading"], repr([c for _, c in h.emitted]))
check("点击 -> 托盘菜单被刷新 (显示'正在更新')", h.menu_builds >= 1 and h._tray.setContextMenu.called)
check("下载文案是占位且没有网址", "占位" in h.emitted[0][0][0]["text"] and "http" not in h.emitted[0][0][0]["text"])
check("经真实 Qt 信号从工作线程回到主线程", pump(lambda: h.launches != [] and h.dialogue_box.start.called))
check("启动参数正确", h.launches[0][1][:2] == ["--apply-update", "--target"] and h.launches[0][1][-2:] == ["--old-version", "3.3.0"]
      and "4321" in h.launches[0][1] and "8765" in h.launches[0][1], repr(h.launches))
segs = h.dialogue_box.start.call_args[0][0]
check("告别台词来自 update_restarting 池", segs and segs[0]["text"].startswith("【占位】我先退下"), repr(segs))
check("走了退出锁 (和 request_quit 一样)", h._pending_quit is True and h.reminders.chase.lock_for_exit.called)
timer = h._update_quit_timer
check("15 秒兜底定时器存在、单次、已启动", timer is not None and timer.isSingleShot() and timer.isActive() and timer.interval() == 15000
      and pet_window.UPDATE_QUIT_DEADLINE_MS == 15000)
timer.timeout.emit()
check("兜底定时器到点直接 _quit_now", h.quit_now_calls == 1)
check("退出锁之后再点更新项不会再开任务", (h._on_update_action(), h._update_ctl.busy)[1] is True)
check("update_downloading 之外没有别的台词被 dm 发出 (退出期间被拦)", [c for _, c in h.emitted].count("update_downloading") == 1)
timer.stop()
shutil.rmtree(os.path.dirname(os.path.dirname(os.path.dirname(h.launches[0][0]))), ignore_errors=True)

h = make_harness(can=(False, "dev"))
h._on_update_action()
check("不能一键更新 -> 只打开下载页", h.pages == [PAGE] and h.emitted == [] and h.launches == [] and h.menu_builds == 0)
h = make_harness(visible=False, can=(False, "dev"))
h._update_info = None
h._on_update_action()
check("没有更新信息时点击什么都不做", h.pages == [] and h.emitted == [])
h = make_harness(fetch=cases["download:hash_mismatch"])
h._on_update_action()
pump(lambda: h.pages != [])
check("失败 -> update_failed 与打开下载页, 不退出", [c for _, c in h.emitted] == ["update_downloading"] and h.pages == [PAGE] and h._pending_quit is False)
check("失败 -> 菜单标签恢复 (再次刷新)", h.menu_builds >= 2)
h = make_harness(visible=False)
h._on_update_action()
check("小花不可见时不说话", h.emitted == [])
pump(lambda: h.launches != [])
if h._update_quit_timer:
    h._update_quit_timer.stop()
shutil.rmtree(os.path.dirname(os.path.dirname(os.path.dirname(h.launches[0][0]))), ignore_errors=True) if h.launches else None

# normal quit while downloading
started2 = threading.Event()
evs = []


def slow(info, ev):
    started2.set()
    evs.append(ev)
    ev.wait(10)
    raise uf.UpdateFlowError("download:cancelled", None)


h = make_harness(fetch=slow)
h._on_update_action()
started2.wait(5)
PetWindow.request_quit(h)
check("托盘退出/离开 -> 下载的 cancel_event 被设置", evs and evs[0].is_set())
pump(lambda: not h._update_ctl.busy)
check("下载中退出: 没有启动更新程序, 没打开下载页", h.launches == [] and h.pages == [])
check("退出走普通告别 (不是 update_restarting)", h.dialogue_box.start.called is False or "我先退下" not in h.dialogue_box.start.call_args[0][0][0]["text"])
if h._update_quit_timer:
    h._update_quit_timer.stop()

# menu label on the real _build_menu
print("== 9 托盘菜单文字 ==")


class FakeSelf:
    _build_menu = PetWindow._build_menu

    def __init__(self, ctl):
        self._update_info = dict(INFO)
        self._update_ctl = ctl
        self._always_on_top = True
        self.pomodoro = MagicMock(is_running=False)
        self.clicked = 0
        self.stats = MagicMock()
        self.config = {}
        self._locked = False

    def isVisible(self):
        return True

    def _on_update_action(self, *a):
        self.clicked += 1

    def _build_volume_action(self, menu):
        from PyQt6.QtGui import QAction
        return QAction("音量", menu)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


class CtlStub:
    def __init__(self, busy, ver):
        self.busy, self.version = busy, ver


fs = FakeSelf(CtlStub(False, None))
menu = PetWindow._build_menu(fs)
items = [a for a in menu.actions() if "更新" in a.text()]
check("空闲: 菜单项是 '更新到 v3.4.0'", [a.text() for a in items] == ["更新到 v3.4.0"], repr([a.text() for a in items]))
items[0].trigger()
check("点击菜单项调用 _on_update_action", fs.clicked == 1)
fs = FakeSelf(CtlStub(True, "3.4.0"))
menu = PetWindow._build_menu(fs)
items = [a for a in menu.actions() if "更新" in a.text()]
check("进行中: 菜单项是 '正在更新到 v3.4.0…'", [a.text() for a in items] == ["正在更新到 v3.4.0…"], repr([a.text() for a in items]))
fs._update_info = None
menu = PetWindow._build_menu(fs)
check("没有更新信息: 没有更新项", not [a for a in menu.actions() if "更新" in a.text()])
texts = ["更新到 v3.4.0", "正在更新到 v3.4.0…"]


def has_emoji(s):
    return any(0x2600 <= ord(c) <= 0x27BF or ord(c) >= 0x1F000 or 0xFE0F == ord(c) for c in s)


check("菜单文字没有 emoji", not any(has_emoji(t) for t in texts))

# ------------------------------------------------------------------ 10
print("== 10 重新打开以后 ==")
check("--updated-from -> update_done", uf.startup_update_category(["--updated-from", "3.3.0"]) == "update_done")
check("--update-failed -> update_rolled_back", uf.startup_update_category(["--update-failed"]) == "update_rolled_back")
check("两者都有 -> 只说 update_rolled_back", uf.startup_update_category(["--updated-from", "3.3.0", "--update-failed"]) == "update_rolled_back"
      and uf.startup_update_category(["--update-failed", "--updated-from", "3.3.0"]) == "update_rolled_back")
check("都没有 -> None", uf.startup_update_category([]) is None and uf.startup_update_category(["--after-recovery"]) is None)
check("--update-failed --after-recovery -> update_rolled_back", uf.startup_update_category(["--update-failed", "--after-recovery"]) == "update_rolled_back")
for argv, expected in ((["--updated-from", "3.3.0"], "update_done"), (["--update-failed"], "update_rolled_back"),
                       (["--updated-from", "3.3.0", "--update-failed"], "update_rolled_back")):
    h = Harness()
    h._startup_update_category = uf.startup_update_category(argv)
    h._maybe_say_startup_update_line()
    h._maybe_say_startup_update_line()
    h.dm.on_finished()
    h._maybe_say_startup_update_line()
    cats = [c for _, c in h.emitted]
    check(f"{argv}: 只说一次, 说的是 {expected}", cats == [expected], repr(cats))
h = Harness()
h._startup_update_category = None
h._maybe_say_startup_update_line()
check("没有参数: 不说", h.emitted == [])
h = Harness(visible=False)
h._startup_update_category = "update_done"
h._maybe_say_startup_update_line()
check("小花还不可见: 不说, 也不消耗机会", h.emitted == [] and h._startup_update_category == "update_done")
h._visible = True
h._maybe_say_startup_update_line()
check("可见之后说一次", [c for _, c in h.emitted] == ["update_done"])
h = Harness()
h._startup_update_category = "update_done"
h._maybe_say_startup_update_line()
t = h.emitted[0][0][0]["text"]
check("update_done 里 {ver} 被替换成当前版本", version.VERSION in t and "{ver}" not in t, t)
saved_argv, saved_dev, saved_app = sys.argv, pet_window.IS_DEV, pet_window.APP_DIR
try:
    sys.argv = ["FloweyPet.exe", "--updated-from", "3.3.0"]
    h = Harness()
    h._open_update_page = lambda url: None
    PetWindow._init_update_flow(h)
    check("真实 _init_update_flow 从 sys.argv 读到 update_done", h._startup_update_category == "update_done")
    check("_init_update_flow 建好了控制器和信号连接", isinstance(h._update_ctl, uf.UpdateController))
    sys.argv = ["FloweyPet.exe", "--update-failed", "--updated-from", "3.3.0"]
    h = Harness()
    h._open_update_page = lambda url: None
    PetWindow._init_update_flow(h)
    check("真实 _init_update_flow: 两者都有 -> update_rolled_back", h._startup_update_category == "update_rolled_back")
    sys.argv = ["FloweyPet.exe"]
    h = Harness()
    PetWindow._init_update_flow(h)
    check("真实 _init_update_flow: 没参数 -> None", h._startup_update_category is None)
    ok_src, _ = uf.can_one_click_update(INFO, pet_window.IS_DEV, str(pet_window.APP_DIR), False, ok_probe), None
    check("源码运行时 (IS_DEV) 真实接线判定为不能一键更新", ok_src == (False, "dev"))
finally:
    sys.argv = saved_argv
src_appear = inspect.getsource(PetWindow._advance_appear)
check("_advance_appear: 在 trigger_intro 之后说更新台词", src_appear.index("trigger_intro()") < src_appear.index("_maybe_say_startup_update_line()"))

# ------------------------------------------------------------------ 11
print("== 11 清理旧的更新临时目录 ==")
root = TMP / "tempsim"
root.mkdir()
for name in ("FloweyPet-update-aaaa", "FloweyPet-update-bbbb"):
    (root / name / "unpacked" / "FloweyPet-v3.3.0").mkdir(parents=True)
    (root / name / "unpacked" / "FloweyPet-v3.3.0" / "FloweyPet.exe").write_bytes(b"x")
    (root / name / "x.zip").write_bytes(b"zip")
keep_dirs = ["FloweyPet-update", "FloweyPet-updates", "Other-FloweyPet-update-x", "floweypet-update-lower", "FloweyPet", "random"]
for name in keep_dirs:
    (root / name).mkdir()
    (root / name / "keep.txt").write_text("keep")
(root / "FloweyPet-update-file").write_text("a file with the prefix, not a folder")


def age_tree(path, seconds=11 * 60):
    """Sets mtime of the folder, all sub folders and all files to `seconds` ago
    (cleanup only deletes folders untouched for 10 minutes)."""
    t = time.time() - seconds
    items = [path]
    for dp, dn, fn in os.walk(path):
        items += [os.path.join(dp, x) for x in dn + fn]
    for x in items:
        os.utime(x, (t, t))


for name in ("FloweyPet-update-aaaa", "FloweyPet-update-bbbb"):
    age_tree(root / name)
logs = []
n = uf.cleanup_old_update_dirs(root, log=logs.append)
left = sorted(os.listdir(root))
check("返回删除个数 2", n == 2, str(n))
check("FloweyPet-update-* 文件夹被删", not (root / "FloweyPet-update-aaaa").exists() and not (root / "FloweyPet-update-bbbb").exists())
check("其他名字的文件夹一个都没碰", all((root / k / "keep.txt").read_text() == "keep" for k in keep_dirs))
check("前缀同名的普通文件不碰", (root / "FloweyPet-update-file").is_file())
check("记了一行日志", len(logs) == 1 and "删除 2 个" in logs[0], repr(logs))
check("没东西可删时不记日志也不出错", uf.cleanup_old_update_dirs(root, log=logs.append) == 0 and len(logs) == 1)
check("目录不存在不抛异常", uf.cleanup_old_update_dirs(TMP / "no_such_dir") == 0)
locked_dir = root / "FloweyPet-update-locked"
(locked_dir / "unpacked").mkdir(parents=True)
(locked_dir / "unpacked" / "other.txt").write_text("other")
(root / "FloweyPet-update-gone").mkdir()
lock_exe = locked_dir / "unpacked" / "FloweyPet.exe"
shutil.copy2(sys.executable, lock_exe)
p = subprocess.Popen([str(lock_exe), "-c", "import time; time.sleep(120)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL)
CHILDREN.append(p)
time.sleep(0.8)
if p.poll() is not None:
    for dll in Path(sys.executable).parent.glob("*.dll"):
        try:
            shutil.copy2(dll, lock_exe.parent / dll.name)
        except OSError:
            pass
    p = subprocess.Popen([str(lock_exe), "-c", "import time; time.sleep(120)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)
    CHILDREN.append(p)
    time.sleep(0.8)
age_tree(locked_dir)
age_tree(root / "FloweyPet-update-gone")
logs.clear()
try:
    n = uf.cleanup_old_update_dirs(root, log=logs.append)
    raised = False
except BaseException:  # noqa: BLE001
    raised = True
check("有一个被占用删不掉: 不抛异常", raised is False)
check("被占用的跳过 (还在), 其他照删", lock_exe.exists() and not (root / "FloweyPet-update-gone").exists() and n == 1, f"n={n}")
check("日志里写了跳过数量", logs and "1 个删不掉" in logs[0], repr(logs))
check("其他名字的文件夹仍然没碰", all((root / k / "keep.txt").exists() for k in keep_dirs))
p.kill()
p.wait(10)
time.sleep(0.3)
age_tree(locked_dir)  # the previous cleanup deleted a file in it, which refreshed its mtime
check("进程结束后能清掉", uf.cleanup_old_update_dirs(root) == 1 and not locked_dir.exists())
orig_listdir = uf.os.listdir
uf.os.listdir = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    uf.cleanup_old_update_dirs(root)
    raised = False
except BaseException:  # noqa: BLE001
    raised = True
finally:
    uf.os.listdir = orig_listdir
check("内部出现意外错误也不抛异常", raised is False)
check("remove_dir: 不存在的路径 / None 不出错", uf.remove_dir(str(TMP / "none_such")) is True and uf.remove_dir(None) is True)
main_src = (ROOT / "main.py").read_text(encoding="utf-8")
check("main.py: 清理在 guard.try_claim() 之后, 且只在 打包版且不在临时目录 的条件里",
      main_src.index("update_flow.cleanup_old_update_dirs(") > main_src.index("guard.try_claim()")
      and re.search(r"if not IS_DEV and not running_from_temp:\s+update_flow\.cleanup_old_update_dirs\(log=app_log\.log_event\)", main_src) is not None)

# ------------------------------------------------------------------ 12
print("== 12 六个新台词池 ==")
NEW = ["update_downloading", "update_busy", "update_restarting", "update_failed", "update_done", "update_rolled_back"]
raw = (ROOT / "dialogues_system.json").read_bytes()
data = json.loads(raw.decode("utf-8"))
old = json.loads(subprocess.run(["git", "show", "HEAD:dialogues_system.json"], cwd=str(ROOT), capture_output=True).stdout.decode("utf-8"))
for name in NEW:
    check(f"{name}: 池存在, 1 条, 带【占位】, 表情 neutral",
          isinstance(data.get(name), list) and len(data[name]) == 1 and "【占位】" in data[name][0]["text"]
          and data[name][0]["face"] == "neutral", repr(data.get(name)))
    check(f"{name}: 没有 emoji 也没有网址", not has_emoji(data[name][0]["text"]) and "http" not in data[name][0]["text"])
check("update_done 带 {ver}", "{ver}" in data["update_done"][0]["text"])
check("其他池条目数不变 (duplicate_launch 13 / update_available 8 / no_update 4)",
      {k: len(data[k]) for k in ("duplicate_launch", "update_available", "no_update")} == {"duplicate_launch": 13, "update_available": 8, "no_update": 4})
check("其他池内容和 HEAD 完全一样", all(data[k] == old[k] for k in old if k != "_readme"))
check("池名集合 = 原有 + 六个新的", set(data) - {"_readme"} == (set(old) - {"_readme"}) | set(NEW))
check("_readme 保留原文并补了说明", data["_readme"].startswith(old["_readme"]) and all(n in data["_readme"] for n in NEW))
check("_readme 说明了各池什么时候说", "--updated-from" in data["_readme"] and "--update-failed" in data["_readme"] and "告别台词" in data["_readme"])
check("换行风格不变: 全是 CRLF, 末尾有换行", raw.count(b"\r\n") == raw.count(b"\n") and raw.endswith(b"}\r\n"))
check("缩进仍是 2 空格", b'\r\n  "update_busy": [' in raw)
check("文件以 UTF-8 无 BOM 保存", not raw.startswith(b"\xef\xbb\xbf"))
for name in NEW:
    dm, emitted = fresh_dm()
    dm.trigger_update_line(name)
    check(f"{name}: 能抽到并发出", len(emitted) == 1 and emitted[0][1] == name and "【占位】" in emitted[0][0][0]["text"], repr(emitted))
dm, emitted = fresh_dm()
segs = dm.pick_update_segments("update_done")
check("pick_update_segments 替换 {ver}", segs and version.VERSION in segs[0]["text"] and "{ver}" not in segs[0]["text"])
check("pick_update_segments: 没有这个池 -> None", dm.pick_update_segments("no_such_pool") is None)
req = (ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
check("打包必需文件里已有 dialogues_system.json (无需改 build_release.py)", '"dialogues_system.json"' in req)
check("台词池只在 dialogues_system.json 里 (没有硬编码到代码里)",
      "【占位】" not in (ROOT / "src" / "update_flow.py").read_text(encoding="utf-8")
      and "【占位】" not in (ROOT / "src" / "pet_window.py").read_text(encoding="utf-8"))

# ------------------------------------------------------------------ 13
print("== 13 模块纯度 / 其他 ==")
r = subprocess.run([sys.executable, "-I", "-c",
                    "import sys; sys.path.insert(0,'src'); import update_flow; "
                    "print(sorted(m for m in sys.modules if m.split('.')[0] in ('PyQt6','config','paths','app_log')))"],
                   cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
check("导入 update_flow 不带入 PyQt6/config/paths/app_log", r.stdout.strip() == "[]", repr((r.stdout, r.stderr[-200:])))
flow_src = (ROOT / "src" / "update_flow.py").read_text(encoding="utf-8")
check("update_flow 没有 print(", "print(" not in flow_src)
check("pet_window 的更新入口: 菜单连到 _on_update_action 而不是直接开网页", "self._on_update_action" in inspect.getsource(PetWindow._build_menu)
      and "_open_update_page(update_info" not in inspect.getsource(PetWindow._build_menu))
check("request_quit 仍然不带参数 (QAction.triggered 会传 checked)", list(inspect.signature(PetWindow.request_quit).parameters) == ["self"])
check("UPDATE_QUIT_DEADLINE_MS == 15000 且 EXIT_FAILSAFE_MS 未变", pet_window.UPDATE_QUIT_DEADLINE_MS == 15000 and pet_window.EXIT_FAILSAFE_MS == 15000)
check("日志文件里没有网址 (本测试期间 app.log)", not (TMP / "app.log").exists() or "http" not in (TMP / "app.log").read_text(encoding="utf-8"))

print("== 14 收尾 ==")
for c in CHILDREN:
    if c.poll() is None:
        c.kill()
        c.wait(10)
check("所有测试子进程都已结束", all(c.poll() is not None for c in CHILDREN))
cleanup()
check("临时目录已清理", not TMP.exists(), str(TMP))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("一键更新接线用例全部通过")
sys.exit(0)
