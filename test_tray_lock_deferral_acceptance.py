# -*- coding: utf-8 -*-
"""The tray menu's 锁定 click handler must not do the real lock/unlock (or
touch the tray icon) inside the menu's own click dispatch: it decides from
the lock state at click time and queues the work on the event loop, to run
after the menu has finished closing. Offscreen, FakeSelf style.

NOTE: offscreen cannot reproduce the real tray menu hanging on screen; this
only pins the structural guarantee (handler returns before anything native
happens). Whether it actually cures the hang needs the real desktop.

Run:  QT_QPA_PLATFORM=offscreen python test_tray_lock_deferral_acceptance.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtGui import QAction  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

app = QApplication(sys.argv)

import dialogue_log  # noqa: E402
from pet_window import PetWindow  # noqa: E402

dialogue_log.log_event = lambda m: None

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def pump():
    """Run everything queued with QTimer.singleShot(0, ...)."""
    for _ in range(3):
        QCoreApplication.processEvents()


class FakeWindow:
    def winId(self):
        return 0  # set_click_through() returns early on hwnd 0


class FakeSelf:
    _on_tray_lock_triggered = PetWindow._on_tray_lock_triggered
    _apply_click_through = PetWindow._apply_click_through
    _build_menu = PetWindow._build_menu

    def __init__(self, locked):
        self._locked = locked
        self.calls = []              # order of real lock/unlock executions
        self.click_through_calls = []
        self.tooltip_refreshes = 0
        self.dialogue_box = FakeWindow()
        self.pomodoro_display = FakeWindow()
        # only _build_menu touches these
        self._always_on_top = True
        self.config = {}
        self.pomodoro = MagicMock(is_running=False)

    def _force_unlock(self):
        self.calls.append("force_unlock")
        self._locked = False

    def _toggle_lock(self, checked):
        self.calls.append(("toggle_lock", checked))
        self._locked = checked

    def _refresh_tray_tooltip(self):
        self.tooltip_refreshes += 1

    def isVisible(self):
        return True

    def _build_volume_action(self, menu):
        return QAction("音量", menu)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


# ===================================================================== 1
# Locked: click -> nothing happens inside the handler, force_unlock is queued
f = FakeSelf(locked=True)
f._on_tray_lock_triggered(False)
check("已锁定: 点击处理返回时尚未调用 _force_unlock(没在调用栈里执行)", f.calls == [], str(f.calls))
check("已锁定: 点击处理返回时 _locked 尚未改变", f._locked is True)
pump()
check("已锁定: 事件循环转一轮后 _force_unlock 恰好执行一次", f.calls == ["force_unlock"], str(f.calls))

# ===================================================================== 2
# Unlocked: click -> _toggle_lock(True) queued, not run inside the handler
f = FakeSelf(locked=False)
f._on_tray_lock_triggered(True)
check("未锁定: 点击处理返回时尚未调用 _toggle_lock", f.calls == [], str(f.calls))
pump()
check("未锁定: 事件循环转一轮后 _toggle_lock(True) 恰好执行一次", f.calls == [("toggle_lock", True)], str(f.calls))

# ===================================================================== 3
# The decision uses the state AT CLICK TIME, even if it changes before the
# queued call runs.
f = FakeSelf(locked=True)
f._on_tray_lock_triggered(False)
f._locked = False  # state flips between the click and the queued call
pump()
check("决定取自点击时的状态(点击时已锁定 -> 仍走强制解锁)", f.calls == ["force_unlock"], str(f.calls))

# ===================================================================== 4
# The click handler never calls _apply_click_through itself -- with the
# REAL _force_unlock wired in, click-through only changes on the next turn.
class FakeSelfReal(FakeSelf):
    _force_unlock = PetWindow._force_unlock

    def __init__(self, locked):
        super().__init__(locked)
        self.arbiter = MagicMock()
        self._locked_click_count = 5

    def _apply_click_through(self, on):  # recorder in place of the real one
        self.click_through_calls.append(on)


f = FakeSelfReal(locked=True)
f._on_tray_lock_triggered(False)
check("点击处理本身不直接调用 _apply_click_through", f.click_through_calls == [], str(f.click_through_calls))
check("点击处理返回时 arbiter.reset_lock_gates 也尚未调用", f.arbiter.reset_lock_gates.call_count == 0)
pump()
check("事件循环转一轮后才 _apply_click_through(False)(排进了事件循环)", f.click_through_calls == [False], str(f.click_through_calls))
check("事件循环转一轮后 _locked 为 False 且 reset_lock_gates 调用一次",
      f._locked is False and f.arbiter.reset_lock_gates.call_count == 1)

# ===================================================================== 5
# The real _apply_click_through defers the tray tooltip refresh
f = FakeSelf(locked=True)
f._apply_click_through(False)
check("_apply_click_through 返回时托盘 tooltip 尚未刷新(没在调用栈里碰托盘)", f.tooltip_refreshes == 0, str(f.tooltip_refreshes))
pump()
check("事件循环转一轮后 tooltip 恰好刷新一次", f.tooltip_refreshes == 1, str(f.tooltip_refreshes))

# ===================================================================== 6
# Real tray menu: clicking the 锁定 item never calls setChecked from our
# code (the check mark is re-synced only by aboutToShow).
m = FakeSelf(locked=False)
menu = PetWindow._build_menu(m)
la = [a for a in menu.actions() if a.text() == "锁定"][0]
set_checked_calls = []
_orig = la.setChecked
la.setChecked = lambda v: (set_checked_calls.append(v), _orig(v))[1]
la.trigger()
check("点击'锁定'项: 处理函数没有调用 setChecked", set_checked_calls == [], str(set_checked_calls))
check("点击'锁定'项: 返回时尚未上锁", m.calls == [], str(m.calls))
pump()
check("点击'锁定'项: 事件循环转一轮后才执行上锁", m.calls == [("toggle_lock", True)], str(m.calls))
m._locked = False  # e.g. the lock was refused
menu.aboutToShow.emit()
check("勾选只靠 aboutToShow 刷新(此时 setChecked 来自 aboutToShow)", set_checked_calls == [False], str(set_checked_calls))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("托盘锁定延后执行用例全部通过")
sys.exit(0)
