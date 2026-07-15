# -*- coding: utf-8 -*-
"""Regression test for the reported bug: after the 6s hover-hesitate
"抓包" fires, moving the mouse away from the "调情" row did not play the
flirt_escape line. Root cause: the heart-selection system
(UndertalePopup._select_index) only fires on `enterEvent` of some OTHER
row -- there was no signal at all for "the cursor left 调情 into blank/
separator space, or off the popup entirely", which is the natural way a
user actually "moves the mouse away". Fixed via MenuRow.on_leave (new
opt-in hook, no-op for every row except 调情) plus making
FlirtController.on_menu_closed() also fire on_escape() when the menu
closes mid-confrontation (spec item 7a covers both cases).

Offscreen, no real GUI interaction -- same pattern as the other
test_*_acceptance.py files in this project.

Run:  QT_QPA_PLATFORM=offscreen python test_flirt_hover_escape.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QEvent, QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from flirt import FlirtController  # noqa: E402
from fonts import pixel_font  # noqa: E402
from pet_menu import PetContextMenu  # noqa: E402
from undertale_ui import MenuRow  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def wait_s(seconds):
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def make_controller(hesitate=0.2, stall=0.3, cooldown_minutes=None, cooldown_seconds=0.4):
    cfg = {
        "flirt": {
            "hesitate_seconds": hesitate,
            "stall_seconds": stall,
            "cooldown_minutes": (cooldown_minutes if cooldown_minutes is not None else cooldown_seconds / 60),
        }
    }
    ctl = FlirtController(cfg)
    events = []
    ctl.on_hesitate = lambda: events.append("hesitate")
    ctl.on_escape = lambda: events.append("escape")
    ctl.on_stall = lambda: events.append("stall")
    return ctl, events


# ===================================================================== 1
# Moving away BEFORE hesitate elapses cancels cleanly, no hesitate fires.
ctl, events = make_controller()
ctl.on_menu_selection_changed(True)
wait_s(0.05)
ctl.on_menu_selection_changed(False)
wait_s(0.3)
check("悬停中途移开: 不触发抓包", events == [], str(events))
check("悬停中途移开: 状态回到 idle", ctl.state == "idle")

# ===================================================================== 2
# Full hesitate -> hesitate fires exactly once.
ctl, events = make_controller()
ctl.on_menu_selection_changed(True)
wait_s(0.3)
check("悬停满6s(测试用0.2s): 触发抓包", events == ["hesitate"], str(events))
check("抓包后进入对峙状态", ctl.is_confronting())

# ----- branch (a1): cursor moves to a DIFFERENT selectable row -----
ctl.on_menu_selection_changed(False)
check("对峙中移到其他选项: 触发逃跑台词", events == ["hesitate", "escape"], str(events))
check("逃跑后状态回到 idle", not ctl.is_confronting())

# ===================================================================== 3
# THE ACTUAL REPORTED BUG: cursor leaves 调情 into blank/separator space
# or off the popup entirely -- no other row's enterEvent ever fires, so
# the only signal is MenuRow.on_leave -> on_menu_selection_changed(False).
# This simulates exactly that path (see pet_menu.py's wiring).
ctl, events = make_controller()
ctl.on_menu_selection_changed(True)
wait_s(0.3)
check("(重复确认)悬停满后触发抓包", events == ["hesitate"])
ctl.on_menu_selection_changed(False)  # what MenuRow.on_leave now calls
check("对峙中鼠标移到空白/菜单外: 触发逃跑台词(此前的bug)", events == ["hesitate", "escape"], str(events))

# ===================================================================== 4
# branch (a2): closing the menu entirely while confronting (Escape key /
# click outside the popup) must ALSO play the escape line -- this is the
# on_menu_closed() fix (previously it silently reset state with no line).
ctl, events = make_controller()
ctl.on_menu_selection_changed(True)
wait_s(0.3)
ctl.on_menu_closed()
check("对峙中直接关闭菜单: 也触发逃跑台词", events == ["hesitate", "escape"], str(events))
check("关闭菜单后状态回到 idle", not ctl.is_confronting())

# closing the menu while NOT confronting (ordinary case) must not
# fabricate an escape line.
ctl, events = make_controller()
ctl.on_menu_closed()
check("未处于对峙时关闭菜单: 不触发逃跑台词", events == [], str(events))

# a confirm-click resolves state to idle BEFORE the menu's close_after()
# runs -- on_menu_closed() must not double-fire escape after a confirm.
ctl, events = make_controller()
ctl.on_menu_selection_changed(True)
wait_s(0.3)
was_confronting = ctl.resolve_click()
check("点击调情确认分支: resolve_click 返回 True", was_confronting is True)
ctl.on_menu_closed()  # simulates make_action_row's close_after() firing right after
check("确认分支后菜单关闭: 不会额外补播逃跑台词", events == ["hesitate"], str(events))

# ===================================================================== 5
# branch (c): staying on 调情 past stall_seconds plays the stall line
# exactly once, then the confrontation continues (still resolvable by
# a/b afterward).
ctl, events = make_controller(hesitate=0.15, stall=0.15)
ctl.on_menu_selection_changed(True)
wait_s(0.3)
check("赖着分支: 抓包已触发", events == ["hesitate"], str(events))
wait_s(0.3)
check("赖着分支: 达到stall时长后触发一次赖着台词", events == ["hesitate", "stall"], str(events))
wait_s(0.3)
check("赖着分支: 台词只播一次,不循环", events == ["hesitate", "stall"], str(events))
check("赖着后仍处于对峙状态", ctl.is_confronting())
ctl.on_menu_selection_changed(False)
check("赖着后仍可通过移开触发逃跑", events == ["hesitate", "stall", "escape"], str(events))

# ===================================================================== 6
# Cooldown: re-hovering within cooldown_minutes must not re-trigger, even
# after waiting past hesitate_seconds again; past cooldown, it can.
ctl, events = make_controller(hesitate=0.15, stall=5, cooldown_seconds=0.4)
ctl.on_menu_selection_changed(True)
wait_s(0.3)
ctl.on_menu_selection_changed(False)
check("冷却前置: 抓包+逃跑各一次", events == ["hesitate", "escape"], str(events))
ctl.on_menu_selection_changed(True)
wait_s(0.3)
check("冷却期内再次悬停: 不重新触发抓包", events == ["hesitate", "escape"], str(events))
# Simulate the cooldown having elapsed.
ctl._last_trigger_monotonic = time.monotonic() - 9999
ctl.on_menu_selection_changed(False)
ctl.on_menu_selection_changed(True)
wait_s(0.3)
check("冷却过后再次悬停: 可以再次触发抓包", events == ["hesitate", "escape", "hesitate"], str(events))

# ===================================================================== 7
# MenuRow.on_leave: default no-op never crashes; a row that sets it gets
# called on a real Qt leaveEvent.
plain_row = MenuRow(pixel_font(14))
try:
    plain_row.leaveEvent(QEvent(QEvent.Type.Leave))
    check("普通行: 未设置on_leave时leaveEvent不报错", True)
except Exception as e:  # noqa: BLE001
    check("普通行: 未设置on_leave时leaveEvent不报错", False, repr(e))

hook_calls = []
flirt_row = MenuRow(pixel_font(14))
flirt_row.on_leave = lambda: hook_calls.append(1)
flirt_row.leaveEvent(QEvent(QEvent.Type.Leave))
check("调情行: leaveEvent触发on_leave钩子", hook_calls == [1], str(hook_calls))

# ===================================================================== 8
# Integration: PetContextMenu actually wires flirt_row.on_leave the way
# pet_menu.py intends -- proves the fix is really connected, not just
# correct in isolation.
class FakePet:
    def __init__(self):
        self.audio = None
        self._always_on_top = True
        self.config = {"volume": 0.8}
        self.pet_scale = 1.0
        self.flirt, self._flirt_events = make_controller()

        def _noop(*a, **k):
            pass

        for name in (
            "_toggle_always_on_top", "_toggle_visible", "_toggle_lock", "_toggle_mute",
            "_toggle_menu_sfx", "_toggle_pomodoro_from_menu", "_open_reminder_settings",
            "_open_stats_panel", "_open_volume_panel", "_open_size_panel",
            "_toggle_autostart", "_on_flirt_clicked", "_view_info", "request_quit",
        ):
            setattr(self, name, _noop)

    def isVisible(self):
        return True


fake_pet = FakePet()
menu = PetContextMenu(fake_pet)
check("菜单集成: flirt_row 被正确记录", menu.flirt_row is not None)
fake_pet.flirt.on_menu_selection_changed(True)
wait_s(0.3)
check("菜单集成: 悬停调情行满6s触发抓包", fake_pet._flirt_events == ["hesitate"], str(fake_pet._flirt_events))
menu.flirt_row.on_leave()  # exactly what Qt calls via MenuRow.leaveEvent
check(
    "菜单集成: flirt_row.on_leave() 正确调用 on_menu_selection_changed(False) 触发逃跑",
    fake_pet._flirt_events == ["hesitate", "escape"],
    str(fake_pet._flirt_events),
)
menu.close()

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("调情悬停/逃跑用例全部通过")
sys.exit(0)
