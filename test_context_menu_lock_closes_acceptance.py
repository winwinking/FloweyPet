# -*- coding: utf-8 -*-
"""The pet's own right-click menu (PetContextMenu, pet_menu.py): clicking
the 锁定 toggle must close the menu -- queued on the event loop with
QTimer.singleShot(0, ...) so it happens after the click handler returns --
no matter whether the lock was applied, refused, or this was an unlock.
Every other toggle (置顶/隐身/静音/界面音效/开机自启) keeps its old behaviour:
the menu stays open.

Offscreen; the real PetContextMenu + real _close_active_popup, a MagicMock
standing in for the pet. (Offscreen can't prove the on-screen result -- that
needs the real desktop.)

Run:  QT_QPA_PLATFORM=offscreen python test_context_menu_lock_closes_acceptance.py
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import dialogue_log  # noqa: E402
from pet_menu import PetContextMenu  # noqa: E402
from pet_window import PetWindow  # noqa: E402
from undertale_ui import ClickableLabel  # noqa: E402

dialogue_log.log_event = lambda m: None

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def pump():
    for _ in range(3):
        QCoreApplication.processEvents()


def make_pet(locked=False, lock_accepts=True):
    pet = MagicMock()
    pet.audio = None
    pet._always_on_top = True
    pet.config = {"volume": 0.8}
    pet.pet_scale = 1.0
    pet._locked = locked
    pet.isVisible.return_value = True
    pet.toggle_lock_calls = []

    def toggle_lock(checked):
        pet.toggle_lock_calls.append(checked)
        if lock_accepts:
            pet._locked = checked

    pet._toggle_lock = toggle_lock
    pet._close_active_popup = lambda: PetWindow._close_active_popup(pet)  # the real method
    return pet


def open_menu(pet):
    menu = PetContextMenu(pet)
    pet._active_popup = menu
    menu.show()
    pump()
    return menu


def row_for(menu, label):
    for row in menu._selectable:
        lab = row.findChild(ClickableLabel)
        if lab is not None and lab.text() == label:
            return row
    raise LookupError(label)


# ===================================================================== 1
# 锁定: lock accepted
pet = make_pet(locked=False, lock_accepts=True)
menu = open_menu(pet)
check("前提: 菜单已打开", menu.isVisible() is True)
row_for(menu, "锁定").activate()
check("上锁成功: 点击处理返回时菜单还开着(关闭是排队的,不在处理里直接关)", menu.isVisible() is True)
check("上锁成功: 点击处理返回时 _active_popup 仍在(还没执行关闭)", pet._active_popup is menu)
check("上锁成功: 确实走了 _toggle_lock(True)", pet.toggle_lock_calls == [True], str(pet.toggle_lock_calls))
pump()
check("上锁成功: 事件循环转一轮后菜单已关闭", menu.isVisible() is False)
check("上锁成功: _close_active_popup 已清掉 _active_popup", pet._active_popup is None)

# ===================================================================== 2
# 锁定: lock refused (tantrum / debounce) -- state unchanged, menu still closes
pet = make_pet(locked=False, lock_accepts=False)
menu = open_menu(pet)
row_for(menu, "锁定").activate()
check("上锁被拒: 点击处理返回时菜单还开着", menu.isVisible() is True)
pump()
check("上锁被拒: 状态没变(仍未锁定)", pet._locked is False)
check("上锁被拒: 事件循环转一轮后菜单仍然关闭", menu.isVisible() is False)

# ===================================================================== 3
# 锁定: unlock
pet = make_pet(locked=True, lock_accepts=True)
menu = open_menu(pet)
row_for(menu, "锁定").activate()
check("解锁: 点击处理返回时菜单还开着", menu.isVisible() is True)
pump()
check("解锁: 走了 _toggle_lock(False)", pet.toggle_lock_calls == [False], str(pet.toggle_lock_calls))
check("解锁: 事件循环转一轮后菜单已关闭", menu.isVisible() is False)

# ===================================================================== 4
# Every other toggle keeps the menu open
for label in ("置顶", "隐身", "静音", "界面音效", "开机自启"):
    pet = make_pet()
    menu = open_menu(pet)
    row_for(menu, label).activate()
    pump()
    check(f"其他 toggle「{label}」: 点击后菜单仍然打开", menu.isVisible() is True)
    check(f"其他 toggle「{label}」: _active_popup 未被清", pet._active_popup is menu)
    menu.close()

# ===================================================================== 5
# The lock row is the only toggle spec that asks to close
import pet_menu  # noqa: E402

spec = pet_menu._build_menu_spec(make_pet())
closers = [s["label"] for s in spec if s.get("close_after")]
check("只有「锁定」一项声明 close_after", closers == ["锁定"], str(closers))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("右键菜单锁定后关闭用例全部通过")
sys.exit(0)
