# -*- coding: utf-8 -*-
"""Acceptance tests for the click-through lock's emergency exit:
DialogueArbiter.reset_lock_gates() + PetWindow._force_unlock() + the tray
menu's 锁定 item. Offscreen, FakeSelf style (PetWindow's methods are bound
onto a stand-in object; no real PetWindow/tray/native window is created),
but against the REAL DialogueArbiter so "was the toggle actually refused?"
is answered by the real tantrum zone + debounce logic.

Run:  QT_QPA_PLATFORM=offscreen python test_force_unlock_acceptance.py
"""
import os
import sys
from pathlib import Path
from time import monotonic
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtGui import QAction  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMenu  # noqa: E402

app = QApplication(sys.argv)

import dialogue_log  # noqa: E402
from dialogue_arbiter import DialogueArbiter  # noqa: E402
from pet_window import PetWindow  # noqa: E402

LOG_LINES = []
dialogue_log.log_event = LOG_LINES.append  # keep this test from touching any log file

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


class FakeDM:
    def __init__(self):
        self.preempted = []
        self.flavors = []

    def preempt(self, category, segments):
        self.preempted.append(category)
        return True

    def trigger_menu_flavor(self, category):
        self.flavors.append(category)


class FakePetForArbiter:
    def is_reminder_popup_active(self):
        return False

    def is_exiting(self):
        return False

    def force_end_bubble(self):
        return False


class FakeStats:
    def __init__(self):
        self.interactions = 0

    def record_interaction(self):
        self.interactions += 1


def make_arbiter():
    cfg = {"dialogue_spam": {
        "feature_debounce_seconds": 30,       # long: a toggle leaves lock in debounce cooldown
        "lock_toggle_thresholds": [3, 6, 9],
        "lock_toggle_tantrum_seconds": 60,
    }}
    dm = FakeDM()
    return DialogueArbiter(cfg, dm, FakePetForArbiter()), dm


class FakeSelf:
    _toggle_lock = PetWindow._toggle_lock
    _force_unlock = PetWindow._force_unlock
    _on_tray_lock_triggered = PetWindow._on_tray_lock_triggered
    _build_menu = PetWindow._build_menu

    def __init__(self):
        self.arbiter, self.dm = make_arbiter()
        self.stats = FakeStats()
        self.config = {}
        self._saved = []
        self._locked = False
        self._locked_click_count = 0
        self.click_through_calls = []
        # attributes only _build_menu touches
        self._always_on_top = True
        self.pomodoro = MagicMock(is_running=False)

    def save_config(self, cfg):
        self._saved.append(dict(cfg))

    def _apply_click_through(self, on):
        self.click_through_calls.append(on)

    def isVisible(self):
        return True

    def _build_volume_action(self, menu):
        return QAction("音量", menu)

    def __getattr__(self, name):  # every other menu slot (_toggle_visible, request_quit, ...) is a no-op
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


# ===================================================================== 1
# reset_lock_gates: opens exactly the two gates, nothing else
arb, dm = make_arbiter()
arb._lock["count"] = 5
arb._lock["rant_cooldown_until"] = monotonic() + 100
arb._lock["tantrum_until"] = monotonic() + 60
arb.begin_feature("lock")      # puts "lock" into its debounce cooldown
arb.begin_feature("pin")       # another feature, must stay untouched
pin_cd_before = arb._feature("pin")["cooldown_until"]
rant_before = arb._lock["rant_cooldown_until"]
check("前提: 闹脾气冷区生效", arb.is_lock_tantrum_active() is True)
check("前提: lock 处于防抖冷却(再点被拒)", arb.begin_feature("lock") is False)
arb.reset_lock_gates()
check("reset_lock_gates: 闹脾气冷区被清", arb.is_lock_tantrum_active() is False)
check("reset_lock_gates: lock 的 cooldown_until 被清", "cooldown_until" not in arb._feature("lock"))
check("reset_lock_gates: 下一次 begin_feature('lock') 立刻被接受", arb.begin_feature("lock") is True)
check("reset_lock_gates: 不改 count", arb._lock["count"] == 5)
check("reset_lock_gates: 不改 rant_cooldown_until", arb._lock["rant_cooldown_until"] == rant_before)
check("reset_lock_gates: 不碰其他功能(pin 的冷却原样)", arb._feature("pin")["cooldown_until"] == pin_cd_before)

# ===================================================================== 2
# Control group: without the forced path, an unlock request inside the
# tantrum zone really is refused (proves the problem is real).
f = FakeSelf()
f._locked = True
f.arbiter._lock["tantrum_until"] = monotonic() + 60
f._toggle_lock(False)
check("对照: 闹脾气冷区内普通解锁被拒(_locked 仍为 True)", f._locked is True)
check("对照: 被拒时没有应用穿透", f.click_through_calls == [])

# ===================================================================== 3
# _force_unlock must succeed even with BOTH gates closed
f = FakeSelf()
f._locked = True
f._locked_click_count = 7
f.arbiter._lock["tantrum_until"] = monotonic() + 60
f.arbiter.begin_feature("lock")  # also leave lock in debounce cooldown
check("前提: 冷区中", f.arbiter.is_lock_tantrum_active() is True)
interactions_before, flavors_before, preempted_before = f.stats.interactions, list(f.dm.flavors), list(f.dm.preempted)
LOG_LINES.clear()
f._force_unlock()
check("强制解锁: _locked 为 False", f._locked is False)
check("强制解锁: 穿透最后一次参数为 False", bool(f.click_through_calls) and f.click_through_calls[-1] is False, str(f.click_through_calls))
check("强制解锁: 闹脾气冷区被清(is_lock_tantrum_active 为 False)", f.arbiter.is_lock_tantrum_active() is False)
check("强制解锁: 点击计数清零", f._locked_click_count == 0)
check("强制解锁: 不说话(无台词/无 flavor)", f.dm.flavors == flavors_before and f.dm.preempted == preempted_before)
check("强制解锁: 不记 stats", f.stats.interactions == interactions_before)
check("强制解锁: 不写配置/不落盘", f._saved == [] and "locked" not in f.config)
check("强制解锁: 记录一行日志", LOG_LINES == ["强制解锁（托盘/热键）"], str(LOG_LINES))

# ===================================================================== 4
# Right after a forced unlock, locking again must succeed immediately
# (not refused by the tantrum zone or the debounce cooldown).
f._toggle_lock(True)
check("强制解锁后 _toggle_lock(True) 立刻上锁成功", f._locked is True)
check("强制解锁后再上锁: 穿透最后一次参数为 True", f.click_through_calls[-1] is True, str(f.click_through_calls))
check("强制解锁后再上锁: 走了 arbiter 流程(计数加一)", f.arbiter._lock["count"] >= 1)


# ===================================================================== 5
# Tray 锁定 item: locked -> forced unlock; unlocked -> _toggle_lock(True)
class TraySelf(FakeSelf):
    def __init__(self):
        super().__init__()
        self.calls = []

    def _force_unlock(self):
        self.calls.append("force_unlock")
        FakeSelf._force_unlock(self)

    def _toggle_lock(self, checked):
        self.calls.append(("toggle_lock", checked))
        FakeSelf._toggle_lock(self, checked)


t = TraySelf()
t._locked = True
t.arbiter._lock["tantrum_until"] = monotonic() + 60
t._on_tray_lock_triggered(False)
QCoreApplication.processEvents()  # the handler defers the real work with QTimer.singleShot(0, ...)
check("托盘锁定项: 已锁定时走 _force_unlock(冷区内也能解)", t.calls == ["force_unlock"] and t._locked is False, str(t.calls))
t.calls.clear()
t._on_tray_lock_triggered(True)
QCoreApplication.processEvents()  # same: run the queued singleShot(0) before asserting
check("托盘锁定项: 未锁定时走 _toggle_lock(True)", t.calls == [("toggle_lock", True)] and t._locked is True, str(t.calls))

# ===================================================================== 6
# Really build the tray menu: it has a checkable 锁定 item whose check
# mark is refreshed from self._locked on aboutToShow.
m = FakeSelf()
menu = PetWindow._build_menu(m)
check("_build_menu 返回 QMenu", isinstance(menu, QMenu))
lock_actions = [a for a in menu.actions() if a.text() == "锁定"]
check("托盘菜单里有且仅有一个 '锁定' 项", len(lock_actions) == 1)
la = lock_actions[0]
check("'锁定' 项可勾选", la.isCheckable() is True)
check("初始(未锁定)未勾选", la.isChecked() is False)
m._locked = True
menu.aboutToShow.emit()
check("aboutToShow 后勾选状态刷新为 self._locked(True)", la.isChecked() is True)
m._locked = False
menu.aboutToShow.emit()
check("aboutToShow 后勾选状态刷新为 self._locked(False)", la.isChecked() is False)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("强制解锁用例全部通过")
sys.exit(0)
