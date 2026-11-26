# -*- coding: utf-8 -*-
"""Acceptance tests for wiring the chat box into the pet (行动 -> 聊天):
single instance, placement, no ambient chatter while it is open, auto-close
on lock / hide / quit, size + pin follow, the "no API yet" reply, and the
dialogues_chat.json pool. PetWindow itself is a MagicMock with the real
methods bound (same approach as test_menu_categories_acceptance.py). Nothing
is written outside a temp dir.

Run:  QT_QPA_PLATFORM=offscreen python test_chat_pet_acceptance.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

TMP = Path(tempfile.mkdtemp(prefix="floweypet_chatpet_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import QRect  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import error_log  # noqa: E402

error_log.LOG_PATH = TMP / "error.log"

import pet_menu  # noqa: E402
from chat_box import ChatBox  # noqa: E402
from dialogue_box import DialogueBox  # noqa: E402
from dialogue_manager import PRI_CHAT, DialogueManager  # noqa: E402
from pet_window import PetWindow  # noqa: E402
from undertale_ui import screen_available_rect  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


class _Audio:
    def __getattr__(self, name):
        return lambda *a, **k: None


METHODS = (
    "_open_chat", "_position_chat_box", "_on_chat_submitted", "is_chat_open",
    "_position_dialogue_box", "_on_idle_chat_tick", "_trigger_afk_unless_awaiting",
    "_apply_click_through", "_toggle_always_on_top", "_apply_pet_scale",
    "mouseMoveEvent",
)


def make_pet(x=310, y=200, w=180, h=212):  # x=310: centred on the 800px-wide offscreen screen
    pet = MagicMock()
    pet.config = {"font_scale": 1.75, "bubble_min_font_size": 15, "bubble_screen_margin": 8}
    pet._always_on_top = True
    pet.pet_scale = 1.0
    pet.pet_display_width = w
    pet._pending_quit = False
    pet._chat_box = None
    pet._chat_waiting = False  # a plain attribute on the real PetWindow (a MagicMock one would read as "waiting")
    pet._pomodoro_active = False
    pet._dragging = False
    pet.state = "idle"
    pet.x.return_value, pet.y.return_value = x, y
    pet.width.return_value, pet.height.return_value = w, h
    pet.dialogue_box = DialogueBox(w, 1.75, _Audio(), True, pet_scale=1.0, min_font_px=15)
    pet.reminders.is_awaiting_response.return_value = False
    pet.flirt.is_confronting.return_value = False
    pet.game_detector.is_any_game_running.return_value = False
    for name in METHODS:
        setattr(pet, name, getattr(PetWindow, name).__get__(pet))
    return pet


def pump():
    app.processEvents()


def screen_of(pet):
    return screen_available_rect(pet.x(), pet.y(), pet.width(), pet.height())


# ------------------------------------------------------------------ menu
spec = pet_menu._build_menu_spec(make_pet())
act = next(s for s in spec if s.get("key") == "act")
check("「行动」子项: 查看信息 / 调情 / 聊天", [i["label"] for i in act["items"]] == ["查看信息", "调情", "聊天"],
      str([i["label"] for i in act["items"]]))
chat_item = act["items"][2]
check("「聊天」项是 action 类型", chat_item["type"] == "action")

# --------------------------------------------------------- open / single
pet = make_pet()
pet._open_chat()
pump()
box = pet._chat_box
check("_open_chat 后框存在且可见", isinstance(box, ChatBox) and box.isVisible())
check("_open_chat 关掉了当前菜单弹窗", pet._close_active_popup.called)
check("is_chat_open 为 True", pet.is_chat_open() is True)
pet._open_chat()
pump()
check("再开一次不会出现第二个(同一个对象)", pet._chat_box is box)
tops = [w for w in QApplication.topLevelWidgets() if isinstance(w, ChatBox) and w.isVisible()]
check("屏幕上只有一个聊天框", tops == [box], str(len(tops)))

# -------------------------------------------------------------- placement
scr = screen_of(pet)
pet_rect = QRect(pet.x(), pet.y(), pet.width(), pet.height())
g = box.geometry()
check("居中在小花正下方", g.y() >= pet_rect.bottom() and abs((g.x() + g.width() / 2) - (pet_rect.x() + pet_rect.width() / 2)) <= 1,
      f"{g} vs {pet_rect}")
check("不与小花重叠", not g.intersects(pet_rect))
margin = pet.config["bubble_screen_margin"]
check("在屏幕内(含 margin)", scr.adjusted(margin, margin, -margin, -margin).contains(g), f"{g} in {scr}")

# near the bottom edge: below does not fit -> beside the pet, still on screen, not overlapping
pet2 = make_pet(x=scr.x() + 600, y=scr.y() + scr.height() - 212 - 4)
pet2._open_chat()
pump()
g2 = pet2._chat_box.geometry()
pr2 = QRect(pet2.x(), pet2.y(), pet2.width(), pet2.height())
check("贴屏幕底边: 下方放不下时改放侧边", g2.y() < pr2.bottom() + 8 and not g2.intersects(pr2), f"{g2} vs {pr2}")
check("贴屏幕底边: 仍在屏幕内", scr.adjusted(margin, margin, -margin, -margin).contains(g2), f"{g2} in {scr}")
pet2._chat_box.close()

# near the right edge: horizontally clamped, still on screen
pet3 = make_pet(x=scr.x() + scr.width() - 180 - 2, y=scr.y() + 200)
pet3._open_chat()
pump()
g3 = pet3._chat_box.geometry()
check("贴屏幕右边: 框仍在屏幕内", scr.adjusted(margin, margin, -margin, -margin).contains(g3), f"{g3} in {scr}")
pet3._chat_box.close()

# bubble below the pet (pet at the top): chat box must not sit on top of it
pet4 = make_pet(x=scr.x() + 600, y=scr.y() + 2)
pet4._position_dialogue_box = PetWindow._position_dialogue_box.__get__(pet4)
pet4.pomodoro_display.isVisible.return_value = False
pet4._open_chat()
pump()
pet4._position_dialogue_box()  # bubble flips below the pet; chat box re-places itself
bub = pet4.dialogue_box.geometry()
check("小花在屏幕顶端: 聊天框避开气泡", not pet4._chat_box.geometry().intersects(bub), f"{pet4._chat_box.geometry()} vs {bub}")
pet4._chat_box.close()

# ------------------------------------------------ follow: drag / scale / pin
pet._open_chat()
pump()
before = box.pos()
pet.x.return_value, pet.y.return_value = 300, 250
pet.dialogue_box.hide()
pet._dragging = True
pet.mouseMoveEvent = PetWindow.mouseMoveEvent.__get__(pet)
ev = MagicMock()
ev.globalPosition.return_value.toPoint.return_value = pet.x.return_value and __import__("PyQt6.QtCore", fromlist=["QPoint"]).QPoint(300, 250)
pet._drag_offset = __import__("PyQt6.QtCore", fromlist=["QPoint"]).QPoint(0, 0)
pet._drag_moved = True
pet.pomodoro_display.isVisible.return_value = False
pet.mouseMoveEvent(ev)
check("拖动小花时聊天框跟着走", box.pos() != before)
pr = QRect(pet.x(), pet.y(), pet.width(), pet.height())
check("拖动后仍在小花下方", box.geometry().y() >= pr.bottom())

w_before = box.width()
pet.sprites.appear_frames = [MagicMock()]
new_size = MagicMock()
new_size.width.return_value, new_size.height.return_value = 90, 106
pet.sprites.appear_frames[0].size.return_value = new_size
pet.sprites.get_face.return_value = MagicMock()
pet.screen.return_value = None
pet._set_base_pixmap = MagicMock()
pet._current_frame_for_state = MagicMock()
pet.width.return_value, pet.height.return_value = 90, 106
pet.setFixedSize = MagicMock()
pet.move = MagicMock()
pet.dialogue_box.reconfigure = MagicMock()
pet.pomodoro_display.isVisible.return_value = False
try:
    pet._apply_pet_scale(0.5)
    scaled_ok = True
except Exception as e:  # noqa: BLE001
    scaled_ok = False
    print("apply_pet_scale error:", repr(e))
check("_apply_pet_scale 能跑完(含聊天框)", scaled_ok)
check("换大小挡位: 聊天框宽度按新档位重算", box.width() == round(90 * 2.75) and box.width() < w_before, f"{box.width()} vs {w_before}")

pet._always_on_top = True
pet.arbiter.begin_feature.return_value = True
pet._toggle_always_on_top(False)
check("切换置顶: 聊天框 is_pinned 同步为 False", box.is_pinned() is False)
pet._toggle_always_on_top(True)
check("切换置顶: 聊天框 is_pinned 同步为 True", box.is_pinned() is True)
box.close()

# ---------------------------------------------- no ambient chatter while open
pet = make_pet()
pet._on_idle_chat_tick()
check("对照: 框没开时随机冒泡会触发", pet.dm.trigger_idle_random.call_count == 1)
pet._trigger_afk_unless_awaiting(5)
check("对照: 框没开时 AFK 会触发", pet.dm.trigger_afk.call_count == 1)
pet.dm.reset_mock()
pet._open_chat()
pump()
pet._on_idle_chat_tick()
check("聊天框开着: 随机冒泡不触发", pet.dm.trigger_idle_random.call_count == 0)
check("聊天框开着: 冒泡计时器仍会重排", pet._schedule_idle_chat.called)
pet._trigger_afk_unless_awaiting(5)
check("聊天框开着: AFK 不触发", pet.dm.trigger_afk.call_count == 0)
pet._chat_box.close()
pet._on_idle_chat_tick()
pet._trigger_afk_unless_awaiting(5)
check("关掉聊天框后冒泡恢复", pet.dm.trigger_idle_random.call_count == 1)
check("关掉聊天框后 AFK 恢复", pet.dm.trigger_afk.call_count == 1)

# ---------------------------------------------------------- auto close
pet = make_pet()
pet._open_chat()
pump()
pet._locked = False
pet._apply_click_through(True)
check("锁定(_apply_click_through(True))会关闭聊天框", not pet._chat_box.isVisible())
pet._open_chat()
pump()
pet._apply_click_through(False)
check("解锁不会关闭聊天框", pet._chat_box.isVisible())
pet._chat_box.close()

import pet_window as pw  # noqa: E402

pet = make_pet()
pet._open_chat()
pump()
pw._close_chat(pet)
check("隐身/退出用的 _close_chat 会关掉聊天框", not pet._chat_box.isVisible())
pw._close_chat(object())
check("_close_chat 对没有聊天框的对象无害", True)
src_text = (ROOT / "src" / "pet_window.py").read_text(encoding="utf-8")
i_tv = src_text.index("def _toggle_visible")
i_hf = src_text.index("def _hide_with_flavor")
check("_toggle_visible 里关聊天框(隐身)", "_close_chat(self)" in src_text[i_tv:i_hf])
i_bq = src_text.index("def _begin_quit")
i_iq = src_text.index("def is_exiting")
check("_begin_quit 里关聊天框(退出)", "_close_chat(self)" in src_text[i_bq:i_iq])
pet = make_pet()
pet._pending_quit = True
pet._open_chat()
check("退出中 _open_chat 不开框", pet._chat_box is None)

# --------------------------------------------------- submit -> chat_no_api
pet = make_pet()
pet._open_chat()
pump()
pet._chat_box._edit.setText("你好")
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402

QTest.keyClick(pet._chat_box._edit, Qt.Key.Key_Return)
check("提交文字后 dm 收到 chat_no_api", pet.dm.trigger_chat_no_api.call_count == 1)
check("提交后聊天框仍开着", pet._chat_box.isVisible())

# real DialogueManager: category, priority, bypass
dm = DialogueManager({"total_clicks": 0}, lambda cfg: None, stats=None)
check("dm 加载了 chat_no_api 池", len(dm.data.get("chat_no_api", [])) == 1)
calls = []
dm.speak = lambda category, segments, priority=None, bypass_interval=None: calls.append((category, segments, priority, bypass_interval))
dm.trigger_chat_no_api()
check("trigger_chat_no_api 调 speak(category=chat_no_api, PRI_CHAT, bypass_interval=True)",
      len(calls) == 1 and calls[0][0] == "chat_no_api" and calls[0][2] == PRI_CHAT and calls[0][3] is True, str(calls))
seg = calls[0][1]
check("台词是 3 个表情的数组", isinstance(seg[0]["face"], list) and seg[0]["face"] == ["frown", "smirk", "serious"], str(seg))

# end to end through the real queue: the bubble request carries category chat_no_api
dm2 = DialogueManager({"total_clicks": 0}, lambda cfg: None, stats=None)
got = []
dm2.show_requested.connect(lambda segments, category: got.append((category, segments)))
dm2.trigger_chat_no_api()
check("真实 dm: 发出 show_requested(category=chat_no_api)", len(got) == 1 and got[0][0] == "chat_no_api", str(got))
dm2.busy = True
dm2.trigger_chat_no_api()
check("dm 正在说话时: 排队而不是丢弃", len(got) == 1 and len(dm2._queue) == 1 and dm2._queue[0][1] == "chat_no_api")

# ---------------------------------------------------- the json file itself
raw = json.loads((ROOT / "dialogues_chat.json").read_text(encoding="utf-8"))
check("dialogues_chat.json 有 _readme", isinstance(raw.get("_readme"), str) and raw["_readme"])
check("dialogues_chat.json: chat_no_api 是非空数组", isinstance(raw.get("chat_no_api"), list) and raw["chat_no_api"])
faces_dir = ROOT / "assets" / "faces"
bad = []
for entry in raw["chat_no_api"]:
    names = [entry["face"]] if isinstance(entry["face"], str) else entry["face"]
    bad += [n for n in names if not (faces_dir / n).is_dir()]
check("chat_no_api 里的表情名都在 assets/faces/ 里", not bad, str(bad))
check("chat_no_api 文案不含占位符花括号", all("{" not in e["text"] for e in raw["chat_no_api"]))

spec_text = (ROOT / "FloweyPet.spec").read_text(encoding="utf-8")
check("FloweyPet.spec 的 datas 含 dialogues_chat.json", "'dialogues_chat.json'" in spec_text)
rel_text = (ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
check("build_release.py 的 REQUIRED 含 dialogues_chat.json", '"dialogues_chat.json"' in rel_text)


# ------------------------------------------------ TopmostKeeper + chat box
import topmost_keeper  # noqa: E402
import win_zorder  # noqa: E402

from PyQt6.QtCore import QObject  # noqa: E402


class _QPet(QObject):
    """TopmostKeeper parents itself to the pet, so it needs a real QObject."""

    def __init__(self, inner):
        super().__init__()
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)


applied = []
orig_apply = topmost_keeper.apply_topmost
topmost_keeper.apply_topmost = lambda widget, on: applied.append((widget, on))
try:
    kp = make_pet()
    kp.is_exiting.return_value = False
    kp.is_pinned_on_top.return_value = False
    kp.isVisible.return_value = False
    kp.pomodoro_display.isVisible.return_value = False
    kp.dialogue_box.hide()
    keeper = topmost_keeper.TopmostKeeper(_QPet(kp), guard_active=lambda: False)
    kp._open_chat()
    pump()
    keeper._tick()
    check("置顶开着且框可见: 保持器会把聊天框置顶", (kp._chat_box, True) in applied)
    applied.clear()
    kp._toggle_always_on_top(False)
    keeper._tick()
    check("关掉「置顶」后: 保持器不会强行置顶聊天框", all(w is not kp._chat_box for w, _ in applied), str(applied))
    kp._toggle_always_on_top(True)
    kp._chat_box.close()
    applied.clear()
    keeper._tick()
    check("聊天框关着时: 保持器跳过聊天框", all(w is not kp._chat_box for w, _ in applied), str(applied))
    kp2 = make_pet()
    kp2.is_exiting.return_value = False
    keeper2 = topmost_keeper.TopmostKeeper(_QPet(kp2), guard_active=lambda: False)
    applied.clear()
    keeper2._tick()
    check("从没开过聊天框: 保持器不报错", True)
finally:
    topmost_keeper.apply_topmost = orig_apply
check("apply_topmost 带 SWP_NOACTIVATE(不抢别的应用的键盘焦点)",
      "SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE" in (ROOT / "src" / "win_zorder.py").read_text(encoding="utf-8")
      and win_zorder.SWP_NOACTIVATE == 0x0010)

# no user text in any log: send a sentinel through the whole path
SENT = "SENTINEL_秘密_12345"
import app_log  # noqa: E402
import dialogue_log  # noqa: E402

app_log.LOG_PATH = TMP / "app.log"
dialogue_log.LOG_PATH = TMP / "dialogue.log"
pet = make_pet()
pet._open_chat()
pump()
pet._chat_box._edit.setText(SENT)
QTest.keyClick(pet._chat_box._edit, Qt.Key.Key_Return)
leaks = [p.name for p in TMP.rglob("*.log") if SENT in p.read_text(encoding="utf-8", errors="replace")]
check("用户输入的内容没有出现在任何日志文件里", not leaks, str(leaks))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("聊天框接入用例全部通过")
sys.exit(0)
