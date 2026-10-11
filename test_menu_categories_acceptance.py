# -*- coding: utf-8 -*-
"""Acceptance tests for the two-layer right-click menu (pet_menu.py +
PetWindow._show_context_menu/_open_menu_category/_show_category_popup/
_back_to_main_menu): the main menu's 6 rows, each category box's content,
"< 返回"/Esc, toggles keeping the box open, 音量/大小 opening the original
panels, click-outside closing everything, 调情 wiring living on the 行动 box,
and the 战斗 placeholder line in dialogue.json.

Offscreen. Real PetContextMenu / CategoryPopup / FlirtController and the real
PetWindow methods bound onto a MagicMock stand-in (no real window).

Run:  QT_QPA_PLATFORM=offscreen python test_menu_categories_acceptance.py
"""
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import QCoreApplication, Qt  # noqa: E402
from PyQt6.QtGui import QKeyEvent  # noqa: E402
from PyQt6.QtCore import QEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import dialogue_log  # noqa: E402
import pet_menu  # noqa: E402
from flirt import FlirtController  # noqa: E402
from pet_menu import BACK_LABEL, CategoryPopup, PetContextMenu, find_category, iter_leaf_specs  # noqa: E402
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


def wait_s(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)


def row_text(row):
    lab = row.findChild(ClickableLabel)
    return lab.text() if lab is not None else None


def popup_texts(popup):
    """Row texts in order; separators (non-selectable rows) show as '---'."""
    return [row_text(r) if r in popup._selectable else "---" for r in popup._rows]


def row_for(popup, text):
    for r in popup._selectable:
        if row_text(r) == text:
            return r
    raise LookupError(text)


def make_pet(flirt=None):
    """A MagicMock pet with the real PetWindow menu-navigation methods bound."""
    pet = MagicMock()
    pet.audio = None
    pet._always_on_top = True
    pet.config = {"volume": 0.8, "panel_screen_margin": 8, "ui_min_font_size": 10}
    pet.pet_scale = 1.0
    pet._locked = False
    pet._pending_quit = False
    pet._active_popup = None
    pet.isVisible.return_value = True
    pet.x.return_value, pet.y.return_value = 400, 300
    pet.width.return_value, pet.height.return_value = 180, 212
    pet.dialogue_box.width.return_value, pet.dialogue_box.height.return_value = 220, 90
    pet._ui_scale.return_value = 1.0
    if flirt is not None:
        pet.flirt = flirt
    for name in (
        "_show_context_menu", "_open_menu_category", "_show_category_popup",
        "_back_to_main_menu", "_close_active_popup", "_place_popup", "_main_menu_height",
    ):
        setattr(pet, name, getattr(PetWindow, name).__get__(pet))
    return pet


def open_main(pet):
    pet._show_context_menu()
    pump()
    return pet._active_popup


def visible_popups():
    return [w for w in QApplication.topLevelWidgets()
            if isinstance(w, (PetContextMenu, CategoryPopup)) and w.isVisible()]


def close_all():
    for w in visible_popups():
        w.close()
    pump()


# ===================================================================== 1
# Main menu: 6 rows, names and order
pet = make_pet()
spec = pet_menu._build_menu_spec(pet)
check("规格: 主菜单 6 项(名称与顺序)", [s["label"] for s in spec] == ["锁定", "战斗", "行动", "物品", "存档点", "仁慈"],
      str([s["label"] for s in spec]))
check("规格: 锁定=toggle,战斗=action,其余 4 项=category",
      [s["type"] for s in spec] == ["toggle", "action", "category", "category", "category", "category"])
check("规格: 分类用嵌套列表(items)描述", all(isinstance(s["items"], list) and s["items"] for s in spec if s["type"] == "category"))
menu = open_main(pet)
check("主菜单: 显示的 6 行", popup_texts(menu) == ["锁定", "战斗", "行动", "物品", "存档点", "仁慈"], str(popup_texts(menu)))
check("主菜单: 是 PetContextMenu,且已打开", isinstance(menu, PetContextMenu) and menu.isVisible())
check("主菜单: 没有调情行(已移到「行动」小框)", not hasattr(menu, "flirt_row"))
close_all()

# ===================================================================== 2
# Category boxes: content and order, first row is 返回
EXPECT = {
    "act": ["查看信息", "调情"],
    "item": ["番茄钟", "提醒设置", "记录"],
    "save": ["置顶", "大小", "待机动作", "---", "静音", "音量", "界面音效", "---", "开机自启", "检查更新"],
    "mercy": ["隐身", "离开"],
}
LABEL = {"act": "行动", "item": "物品", "save": "存档点", "mercy": "仁慈"}
for key, items in EXPECT.items():
    pet = make_pet()
    pet._show_category_popup(key)
    box = pet._active_popup
    check(f"「{LABEL[key]}」小框: 内容与顺序", popup_texts(box) == [BACK_LABEL] + items, str(popup_texts(box)))
    check(f"「{LABEL[key]}」小框: 第一行是「< 返回」且红心初始落在它上面",
          box._selectable[0] is box.back_row and box._selected_index == 0)
    check(f"「{LABEL[key]}」小框: 是 CategoryPopup 且已打开", isinstance(box, CategoryPopup) and box.isVisible())
    close_all()

# ===================================================================== 3
# Main menu -> category: menu closes, box opens on the next tick, only one popup
pet = make_pet()
menu = open_main(pet)
row_for(menu, "行动").activate()
check("点分类: 主菜单立即关闭", menu.isVisible() is False)
check("点分类: 点击处理返回时小框尚未打开(排队到下一个事件循环)", pet._active_popup is menu)
pump()
box = pet._active_popup
check("点分类: 事件循环转一轮后「行动」小框打开", isinstance(box, CategoryPopup) and box.category_key == "act" and box.isVisible())
check("点分类: 同一时间只有一个弹窗", visible_popups() == [box], str(visible_popups()))
close_all()

# every category row opens its own box
for key, label in LABEL.items():
    pet = make_pet()
    menu = open_main(pet)
    row_for(menu, label).activate()
    pump()
    check(f"主菜单「{label}」打开的是 {key} 小框", pet._active_popup.category_key == key)
    close_all()

# ===================================================================== 4
# 战斗 / 锁定 on the main menu
pet = make_pet()
menu = open_main(pet)
row_for(menu, "战斗").activate()
pump()
check("战斗: 调用 _on_fight_clicked 一次", pet._on_fight_clicked.call_count == 1)
check("战斗: 点后主菜单关闭", menu.isVisible() is False)
pet = make_pet()
menu = open_main(pet)
row_for(menu, "锁定").activate()
pump()
check("锁定: 仍走 _toggle_lock(True) 且点后菜单关闭", pet._toggle_lock.call_args_list[0].args == (True,) and menu.isVisible() is False)

# ===================================================================== 5
# Toggles keep the box open; the setter is called
TOGGLES = [("save", "置顶", "_toggle_always_on_top"), ("save", "待机动作", "_toggle_idle_animation"),
           ("save", "静音", "_toggle_mute"), ("save", "界面音效", "_toggle_menu_sfx"),
           ("save", "开机自启", "_toggle_autostart"), ("mercy", "隐身", "_toggle_visible")]
for category, label, method in TOGGLES:
    pet = make_pet()
    pet._show_category_popup(category)
    box = pet._active_popup
    row_for(box, label).activate()
    pump()
    check(f"toggle「{label}」: 调用 {method}", getattr(pet, method).call_count == 1)
    check(f"toggle「{label}」: 点后小框不关闭", box.isVisible() is True and pet._active_popup is box)
    close_all()

# ===================================================================== 6
# 音量 / 大小: close the box and open the original panel
for label, method in (("音量", "_open_volume_panel"), ("大小", "_open_size_panel")):
    pet = make_pet()
    pet._show_category_popup("save")
    box = pet._active_popup
    row_for(box, label).activate()
    pump()
    check(f"「{label}」: 打开原面板(调用 {method})", getattr(pet, method).call_count == 1)
    check(f"「{label}」: 小框关闭", box.isVisible() is False)
    close_all()

# ===================================================================== 7
# Plain actions close the box
for category, label, method in (("act", "查看信息", "_view_info"), ("act", "调情", "_on_flirt_clicked"),
                                ("item", "番茄钟", "_toggle_pomodoro_from_menu"),
                                ("item", "提醒设置", "_open_reminder_settings"), ("item", "记录", "_open_stats_panel"),
                                ("mercy", "离开", "request_quit")):
    pet = make_pet()
    pet._show_category_popup(category)
    box = pet._active_popup
    row_for(box, label).activate()
    pump()
    check(f"action「{label}」: 调用 {method}", getattr(pet, method).call_count == 1)
    check(f"action「{label}」: 点后小框关闭", box.isVisible() is False)
    close_all()

# ===================================================================== 8
# 返回: click and Esc both close the box and reopen the main menu
pet = make_pet()
pet._show_category_popup("save")
box = pet._active_popup
box.back_row.activate()
check("点「< 返回」: 小框立即关闭", box.isVisible() is False)
pump()
check("点「< 返回」: 事件循环转一轮后主菜单重新打开", isinstance(pet._active_popup, PetContextMenu) and pet._active_popup.isVisible())
check("点「< 返回」: 同一时间只有一个弹窗", len(visible_popups()) == 1)
close_all()

pet = make_pet()
pet._show_category_popup("item")
box = pet._active_popup
box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
check("按 Esc: 小框关闭", box.isVisible() is False)
pump()
check("按 Esc: 主菜单重新打开", isinstance(pet._active_popup, PetContextMenu) and pet._active_popup.isVisible())
close_all()

# Esc on the main menu itself still just closes it
pet = make_pet()
menu = open_main(pet)
menu.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
pump()
check("主菜单按 Esc: 只是关闭,不弹别的", menu.isVisible() is False and visible_popups() == [])

# ===================================================================== 9
# Click outside = Qt.Popup closes the box by itself; must not reopen the main menu
pet = make_pet()
pet._show_category_popup("save")
box = pet._active_popup
box.close()  # exactly what Qt.Popup does on an outside click
wait_s(0.1)
check("点小框外面: 全部关闭,不回主菜单", visible_popups() == [], str(visible_popups()))

# ===================================================================== 10
# 调情 wiring lives on the 行动 box
def make_flirt_pet():
    events = []
    ctl = FlirtController({"flirt": {"hesitate_seconds": 0.1, "stall_seconds": 5, "cooldown_minutes": 0}})
    ctl.on_hesitate = lambda: events.append("hesitate")
    ctl.on_escape = lambda: events.append("escape")
    ctl.on_stall = lambda: events.append("stall")
    return make_pet(flirt=ctl), ctl, events


pet, ctl, events = make_flirt_pet()
menu = open_main(pet)
row_for(menu, "行动").activate()
pump()
check("从主菜单点进「行动」: 不触发逃跑/抓包台词", events == [] and ctl.state == "idle", f"{events} {ctl.state}")
box = pet._active_popup
check("「行动」小框上有 flirt_row", box.flirt_row is not None and row_text(box.flirt_row) == "调情")
box.selection_changed.emit(box.flirt_row)
check("在「行动」小框里悬停调情行: 进入 hovering", ctl.state == "hovering")
wait_s(0.3)
check("悬停满时间: 抓包", events == ["hesitate"], str(events))
box.flirt_row.on_leave()  # what MenuRow.leaveEvent calls
check("离开调情行: 逃跑台词", events == ["hesitate", "escape"], str(events))
close_all()

pet, ctl, events = make_flirt_pet()
pet._show_category_popup("act")
box = pet._active_popup
box.selection_changed.emit(box.flirt_row)
wait_s(0.3)
box.close()
pump()
check("抓包后关闭「行动」小框: 逃跑台词", events == ["hesitate", "escape"], str(events))
close_all()

pet, ctl, events = make_flirt_pet()
pet._show_category_popup("act")
box = pet._active_popup
box.selection_changed.emit(box.flirt_row)
wait_s(0.3)
box.back_row.activate()
pump()
check("抓包后点「< 返回」: 逃跑台词(恰好一次)", events == ["hesitate", "escape"], str(events))
close_all()

pet, ctl, events = make_flirt_pet()
menu = open_main(pet)
menu.selection_changed.emit(menu._selectable[1])
menu.close()
pump()
check("主菜单上的选中/关闭不再接到调情状态机", ctl.state == "idle" and events == [], f"{ctl.state} {events}")

# ===================================================================== 11
# 战斗 placeholder line
raw = json.load(open(ROOT / "dialogue.json", encoding="utf-8"))
fight = raw.get("fight")
check("dialogue.json 有 \"fight\" 键(列表)", isinstance(fight, list) and len(fight) == 1)
check("fight 占位台词的文字与表情", fight[0] == {"text": "你想和我打？……哈哈哈。还没到时候呢。", "face": "evil"}, str(fight))
manifest = json.load(open(ROOT / "assets" / "faces_manifest.json", encoding="utf-8"))
check("fight 用的表情 evil 在 faces_manifest 里存在", fight[0]["face"] in manifest)

# nested spec flattening keeps every leaf once
leaves = [s.get("label") for s in iter_leaf_specs(pet_menu._build_menu_spec(make_pet())) if s["type"] != "separator"]
check("展开后的叶子项: 17 项且无重复", len(leaves) == 17 and len(set(leaves)) == 17, str(leaves))

# ===================================================================== 12
# Placement: one rule for every popup -- right of the pet, top aligned with the
# pet's top; left only if the right side doesn't fit the screen; then below.
import undertale_ui  # noqa: E402
from PyQt6.QtCore import QRect  # noqa: E402

SCREEN = QRect(0, 0, 1920, 1040)
PW, PH, GAP, MARGIN = 180, 212, undertale_ui.GAP_FROM_PET, 8
_orig_screen = undertale_ui.screen_available_rect
_orig_bubble_rect = undertale_ui._bubble_rect
undertale_ui.screen_available_rect = lambda *a, **k: SCREEN


def make_bubble(visible):
    b = MagicMock()
    b.width.return_value, b.height.return_value = 495, 112  # the real bubble size at 100% (2.75 x pet width)
    b.isVisible.return_value = visible
    return b


_main_probe = PetContextMenu(make_pet())
_main_probe.finish_layout()
MAIN_H = _main_probe.height()  # the yardstick every popup uses (see PetWindow._main_menu_height)
_main_probe.close()


def placed(popup, px, py, bubble_visible=False):
    undertale_ui.position_avoid_pet_and_bubble(popup, px, py, PW, PH, make_bubble(bubble_visible), margin=MARGIN, ref_h=MAIN_H)
    return popup.x(), popup.y()


def all_popups():
    pet_ = make_pet()
    yield "主菜单", PetContextMenu(pet_)
    for key, label in LABEL.items():
        yield f"「{label}」小框", CategoryPopup(pet_, find_category(pet_, key))


try:
    # pet in the middle of the screen: right, top level with the pet, for every popup,
    # bubble hidden or showing alike
    px, py = 870, 414
    right_x = px + PW + GAP
    for name, popup in all_popups():
        for shown in (False, True):
            x, y = placed(popup, px, py, bubble_visible=shown)
            check(f"屏幕中间(气泡{'显示' if shown else '隐藏'}): {name}在右边", x == right_x, f"x={x}, 期望 {right_x}")
            check(f"屏幕中间(气泡{'显示' if shown else '隐藏'}): {name}顶部与小花顶部对齐", y == py, f"y={y}, 期望 {py}")
        popup.close()

    # same left edge for the menu and every box => no jumping when going back / opening a box
    xs = {name: placed(popup, px, py)[0] for name, popup in all_popups()}
    check("主菜单和四个小框的左边缘一致(返回/进入不跳位)", len(set(xs.values())) == 1, str(xs))

    # pet against the right edge: right doesn't fit -> left, top aligned
    px2, py2 = 1920 - PW - 40, 500  # the default start position's x
    for name, popup in all_popups():
        x, y = placed(popup, px2, py2)
        check(f"靠右边缘: {name}在左边", x + popup.width() + GAP == px2, f"x={x} w={popup.width()}")
        check(f"靠右边缘: {name}顶部与小花顶部对齐", y == py2, f"y={y}")
        popup.close()

    # a tall box near the bottom is nudged up to stay on screen, and still sits on the right
    # (old: "高的小框仍在右边,只是向上夹紧" at pet y=800. New rule: judged by the MAIN MENU's
    # height -- at y=800 the main menu still fits beside the pet, so nothing hugs the edge and the
    # tall box stays on the right, nudged up; with the pet really on the bottom edge (y=828) the
    # main menu would be squeezed, so the tall box goes directly above the pet.)
    tall = CategoryPopup(make_pet(), find_category(make_pet(), "save"))
    px3, py3 = 870, 800
    x, y = placed(tall, px3, py3)
    check("靠近屏幕底部但主菜单仍放得下: 高的小框仍在右边,只是向上夹紧",
          x == px3 + PW + GAP and y < py3 and y + tall.height() <= SCREEN.bottom() + 1 - MARGIN, f"x={x} y={y} h={tall.height()}")
    py4 = SCREEN.height() - PH  # pet on the bottom edge: the main menu would be squeezed
    x, y = placed(tall, px3, py4)
    check("贴下沿: 高的小框放在小花正上方", x == px3 + (PW - tall.width()) // 2 and y + tall.height() + GAP == py4, f"x={x} y={y} h={tall.height()}")
    tall.close()

    # the bubble area only counts while the bubble is showing
    menu_ = PetContextMenu(make_pet())
    blocker = QRect(px + PW, py - 20, 600, PH + 40)  # a "bubble" area sitting right where the right candidate would go
    undertale_ui._bubble_rect = lambda *a, **k: blocker
    x_hidden, _ = placed(menu_, px, py, bubble_visible=False)
    x_shown, _ = placed(menu_, px, py, bubble_visible=True)
    check("气泡隐藏: 即使它的区域挡在右边,也不影响右边判定", x_hidden == right_x, f"x={x_hidden}")
    check("气泡显示: 它的区域挡住右边时,右边被让出", x_shown != right_x, f"x={x_shown}")
    menu_.close()
finally:
    undertale_ui.screen_available_rect = _orig_screen
    undertale_ui._bubble_rect = _orig_bubble_rect

# ===================================================================== 13
# Placement, vertical rule: squeezed against the top edge -> directly below the
# pet; against the bottom edge -> directly above; otherwise unchanged.
undertale_ui.screen_available_rect = lambda *a, **k: SCREEN
try:
    cases = {
        "屏幕中间": ((870, 414), "right"),
        "贴右边缘": ((1920 - PW - 40, 500), "left"),
        "贴上沿": ((870, 0), "below"),
        "贴下沿": ((870, SCREEN.height() - PH), "above"),
        "贴上沿又贴右边缘": ((1920 - PW - 40, 0), "below"),
        "贴下沿又贴右边缘": ((1920 - PW - 40, SCREEN.height() - PH), "above"),
    }
    for case, ((cx, cy), want) in cases.items():
        for name, popup in all_popups():
            x, y = placed(popup, cx, cy, bubble_visible=False)
            w_, h_ = popup.width(), popup.height()
            want_here = want  # every popup follows the main menu's verdict, whatever its own height
            got = ("right" if x == cx + PW + GAP else
                   "left" if x + w_ + GAP == cx else
                   "below" if (y == cy + PH + GAP and x == cx + (PW - w_) // 2) else
                   "above" if (y + h_ + GAP == cy and x == cx + (PW - w_) // 2) else f"其它({x},{y})")
            check(f"{case}: {name}在小花{ {'right': '右', 'left': '左', 'below': '下', 'above': '上'}[want_here] }方", got == want_here, f"实际 {got}; x={x} y={y} {w_}x{h_}")
            check(f"{case}: {name}完全在屏幕可用区内(留 {MARGIN}px 边距)",
                  QRect(x, y, w_, h_) == undertale_ui.clamp_rect_to_screen(QRect(x, y, w_, h_), SCREEN, MARGIN))
            check(f"{case}: {name}不压在小花身上", not QRect(x, y, w_, h_).intersects(QRect(cx, cy, PW, PH)))
            popup.close()

    # at the top edge the bubble is off-screen/above anyway: below works with it showing, too
    for name, popup in all_popups():
        x, y = placed(popup, 870, 0, bubble_visible=True)
        check(f"贴上沿(气泡显示): {name}仍在小花下方", y == 0 + PH + GAP, f"y={y}")
        popup.close()

    # at the bottom edge "above" is where the visible bubble sits: it must not be
    # chosen over the bubble; whatever is picked stays on screen and off the pet
    for name, popup in all_popups():
        x, y = placed(popup, 870, SCREEN.height() - PH, bubble_visible=True)
        r = QRect(x, y, popup.width(), popup.height())
        bubble = undertale_ui._bubble_rect(870, SCREEN.height() - PH, PW, make_bubble(True))
        check(f"贴下沿(气泡显示): {name}不放在小花正上方(那是气泡的位置)", not (y + popup.height() + GAP == SCREEN.height() - PH and x == 870 + (PW - popup.width()) // 2) or not r.intersects(bubble))
        check(f"贴下沿(气泡显示): {name}不压在小花身上", not r.intersects(QRect(870, SCREEN.height() - PH, PW, PH)))
        popup.close()
finally:
    undertale_ui.screen_available_rect = _orig_screen

# ===================================================================== 14
# The real PetWindow path (_show_context_menu / _show_category_popup / back):
# the pet hugging the top or bottom edge puts the menu AND every box on the same
# side, so forward / back never jumps.
undertale_ui.screen_available_rect = lambda *a, **k: SCREEN
try:
    for case, (cx, cy), side in (("贴上沿", (870, 0), "below"), ("贴下沿", (870, SCREEN.height() - PH), "above"),
                                 ("屏幕中间", (870, 414), "right")):
        pet = make_pet()
        pet.x.return_value, pet.y.return_value = cx, cy
        pet.dialogue_box.isVisible.return_value = False  # no bubble showing
        pet._show_context_menu()
        main = pet._active_popup
        spots = {"主菜单": (main.x(), main.y(), main.width(), main.height())}
        main.close()
        for key, label in LABEL.items():
            pet._show_category_popup(key)
            box = pet._active_popup
            spots[f"「{label}」小框"] = (box.x(), box.y(), box.width(), box.height())
            box.close()
        for name, (x, y, w_, h_) in spots.items():
            got = ("right" if x == cx + PW + GAP else "below" if y == cy + PH + GAP else
                   "above" if y + h_ + GAP == cy else f"其它({x},{y})")
            check(f"真实流程·{case}: {name}在小花{side}", got == side, f"实际 {got}")
        if side == "right":
            check(f"真实流程·{case}: 主菜单与小框左边缘一致", len({v[0] for v in spots.values()}) == 1, str(spots))
        else:
            tops = {v[0] + v[2] // 2 for v in spots.values()}
            check(f"真实流程·{case}: 主菜单与小框水平居中在小花上", tops == {cx + PW // 2}, str(spots))
        close_all()
finally:
    undertale_ui.screen_available_rect = _orig_screen

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("两层右键菜单用例全部通过")
sys.exit(0)
