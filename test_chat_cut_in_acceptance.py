# -*- coding: utf-8 -*-
"""Acceptance tests for DialogueManager.cut_in (chat lines must not interrupt
more important speech, and must not wipe the queue) and for giving the
user's typed line back (ChatBox.restore_text) when it was not sent after
all. Everything happens in a temp dir; no network.

Run:  QT_QPA_PLATFORM=offscreen python test_chat_cut_in_acceptance.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

TMP = Path(tempfile.mkdtemp(prefix="floweypet_cutin_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import app_log  # noqa: E402
import error_log  # noqa: E402

app_log.LOG_PATH = TMP / "logs" / "app.log"
error_log.LOG_PATH = TMP / "logs" / "error.log"

import api_chat as ac  # noqa: E402
import api_key_store as store  # noqa: E402
import config as config_mod  # noqa: E402
from chat_box import ChatBox  # noqa: E402
from dialogue_manager import PRI_CHAT, PRI_CLICK, PRI_NEGLECT, PRI_POMODORO, PRI_REMINDER, DialogueManager  # noqa: E402
from pet_window import PetWindow  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def seg(text="x"):
    return [{"text": text, "face": "neutral"}]


def new_dm():
    dm = DialogueManager({"total_clicks": 0}, lambda c: None, stats=None)
    shown = []
    dm.show_requested.connect(lambda segs, cat: shown.append(cat))
    return dm, shown


def queued_cats(dm):
    return [c for _, c, _ in dm._queue]


# ===================================================================== 1
print("== 1 当前台词优先级高于聊天: 不打断, 排在后面 ==")
for label, pri in (("提醒", PRI_REMINDER), ("番茄钟", PRI_POMODORO)):
    dm, shown = new_dm()
    dm._submit("the_" + label, seg(label), pri, bypass_interval=True)
    check(f"{label}台词在显示, 记下优先级 {pri}", shown == ["the_" + label] and dm._current_priority == pri, str(dm._current_priority))
    dm.cut_in("chat_reply", seg("回复"))
    check(f"{label}台词在显示: 回复不打断(没有新的显示)", shown == ["the_" + label], str(shown))
    check(f"{label}台词在显示: 回复按聊天优先级排在队列里", queued_cats(dm) == ["chat_reply"] and dm._queue[0][0] == PRI_CHAT, str(dm._queue))
    dm.on_finished()
    check(f"{label}台词说完后回复才说出来", shown == ["the_" + label, "chat_reply"], str(shown))
    check(f"{label}: 回复显示后记下的优先级是聊天", dm._current_priority == PRI_CHAT)

dm, shown = new_dm()
dm._submit("rem", seg(), PRI_REMINDER, bypass_interval=True)
dm.cut_in("chat_api_error", seg("错误"))
check("提醒在显示: 错误台词同样排队, 不打断", shown == ["rem"] and queued_cats(dm) == ["chat_api_error"])

# ===================================================================== 2
print("== 2 回复插队时, 队列里的高优先级台词保留 ==")
dm, shown = new_dm()
dm.cut_in("chat_wait", seg("……"))
check("没有别的台词时, 「……」立刻显示", shown == ["chat_wait"] and dm._current_priority == PRI_CHAT)
dm._submit("reminder_line", seg("喝水"), PRI_REMINDER, bypass_interval=True)
dm._submit("pomodoro_line", seg("番茄"), PRI_POMODORO, bypass_interval=True)
check("提醒、番茄钟排在「……」后面", queued_cats(dm) == ["reminder_line", "pomodoro_line"], str(queued_cats(dm)))
dm.cut_in("chat_reply", seg("回复"))
check("回复插队: 立刻显示(停掉「……」)", shown == ["chat_wait", "chat_reply"], str(shown))
check("回复插队: 队列里的提醒、番茄钟台词还在", queued_cats(dm) == ["reminder_line", "pomodoro_line"], str(queued_cats(dm)))
dm.on_finished()
dm.on_finished()
check("之后提醒、番茄钟照样说出来(顺序按优先级)", shown == ["chat_wait", "chat_reply", "reminder_line", "pomodoro_line"], str(shown))

dm, shown = new_dm()
dm.preempt("x", seg())
dm._submit("rem", seg(), PRI_REMINDER, bypass_interval=True)
dm._queue.append((PRI_CHAT, "idle_chatter", seg()))
dm.cut_in("chat_reply", seg("回复"))
check("插队时只保留高于聊天优先级的条目(闲聊排队项不保留)", queued_cats(dm) == ["rem"], str(queued_cats(dm)))
dm2, shown2 = new_dm()
dm2.preempt("x", seg())
dm2._submit("rem", seg(), PRI_REMINDER, bypass_interval=True)
dm2.preempt("other", seg())
check("对照: preempt 会清空整个队列(所以聊天不再用它)", dm2._queue == [])

# ===================================================================== 3
print("== 3 排着的 chat_wait 在回复到达时被删掉 ==")
dm, shown = new_dm()
dm._submit("rem", seg("提醒"), PRI_REMINDER, bypass_interval=True)
dm.cut_in("chat_wait", seg("……"))
check("提醒在显示: 「……」排在队列里", queued_cats(dm) == ["chat_wait"], str(queued_cats(dm)))
dm.cut_in("chat_reply", seg("回复"))
check("回复到达: 排着的 chat_wait 被删掉, 回复排在队列里", queued_cats(dm) == ["chat_reply"], str(queued_cats(dm)))
dm.on_finished()
check("提醒说完后直接说回复, 「……」没有再出来", shown == ["rem", "chat_reply"], str(shown))
dm, shown = new_dm()
dm._submit("rem", seg("提醒"), PRI_REMINDER, bypass_interval=True)
dm.cut_in("chat_wait", seg("……"))
dm.cut_in("chat_api_error", seg("错误"))
check("错误台词到达时也删掉排着的 chat_wait", queued_cats(dm) == ["chat_api_error"], str(queued_cats(dm)))
dm, shown = new_dm()
dm._submit("rem", seg(), PRI_REMINDER, bypass_interval=True)
dm.cut_in("chat_wait", seg("……"))
dm.cut_in("chat_wait", seg("……"))
check("连续两个「……」本身不会互相删掉对方(只在别的聊天台词到达时删)", queued_cats(dm).count("chat_wait") == 2)

# ===================================================================== 4
print("== 4 普通闲聊 / 点击台词在显示: 照样被打断 ==")
dm, shown = new_dm()
dm._submit("idle", seg("闲聊"), PRI_CHAT, bypass_interval=True)
dm.cut_in("chat_reply", seg("回复"))
check("闲聊在显示: 被回复打断(立刻显示)", shown == ["idle", "chat_reply"], str(shown))
dm, shown = new_dm()
dm._submit("click_normal", seg("点击"), PRI_CLICK, bypass_interval=True)
check("点击台词优先级记为 PRI_CLICK", dm._current_priority == PRI_CLICK)
dm.cut_in("chat_reply", seg("回复"))
check("点击台词在显示: 被回复打断", shown == ["click_normal", "chat_reply"], str(shown))
dm, shown = new_dm()
dm._submit("neglect", seg("唠叨"), PRI_NEGLECT, bypass_interval=True)
check("PRI_NEGLECT(20) 高于聊天: 不被打断", dm._current_priority == PRI_NEGLECT and (dm.cut_in("chat_reply", seg()) or True) and shown == ["neglect"] and queued_cats(dm) == ["chat_reply"])
dm, shown = new_dm()
dm.preempt("spam_x", seg())
check("preempt 显示的按 PRI_CHAT 记", dm._current_priority == PRI_CHAT)
dm.cut_in("chat_reply", seg("回复"))
check("preempt 的台词(如功能反应)也能被回复打断", shown == ["spam_x", "chat_reply"])
dm, shown = new_dm()
check("cut_in 没有台词(None)返回 False 且不显示", dm.cut_in("chat_reply", None) is False and shown == [])
dm, shown = new_dm()
dm._submit("a", seg(), PRI_POMODORO, bypass_interval=True)
dm._submit("b", seg(), PRI_REMINDER, bypass_interval=True)
dm.on_finished()
check("队列弹出的台词也记下自己的优先级", dm._current_priority == PRI_REMINDER, str(dm._current_priority))

# ===================================================================== 5
print("== 5 ChatBox.restore_text ==")
box = ChatBox(180, 1.75, True, pet_scale=1.0, min_font_px=15)
box.open_and_focus()
box.restore_text("被还回来的字")
check("输入框是空的: 字放回去", box._edit.text() == "被还回来的字")
check("光标在末尾", box._edit.cursorPosition() == len("被还回来的字"))
box._edit.setText("新打的字")
box.restore_text("旧的字")
check("输入框里已经有新字: 不覆盖", box._edit.text() == "新打的字")
box._edit.clear()
box.restore_text("")
check("还回空串: 什么也不做", box._edit.text() == "")
box.restore_text(None)
check("还回 None: 什么也不做, 不抛异常", box._edit.text() == "")
box.close()

# ===================================================================== 6
print("== 6 PetWindow: 被限流 / 没有 key 时字还在 ==")
KEY = "sk-CUTIN-KEY-1234"
store.KEY_PATH = TMP / "data" / "api_key.txt"


class Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append(1)
        return json.dumps({"choices": [{"message": {"content": json.dumps({"text": "好。", "face": ["smirk"]})}}]}).encode("utf-8")


METHODS = (
    "_on_chat_submitted", "_get_chat_session", "_api_settings", "_chat_context", "_chat_limits", "_say_chat_line",
    "_say_chat_pool", "_say_chat_wait", "_set_chat_waiting", "_on_chat_replied", "_on_chat_failed", "_on_api_probed",
    "is_reminder_popup_active", "_open_chat", "_position_chat_box", "_restore_chat_text",
)


def rig(key=KEY):
    if key:
        store.save_key(key)
    else:
        store.delete_key()
    fake = Fake()
    dm, shown = new_dm()
    pet = MagicMock()
    pet.dm = dm
    pet.config = config_mod._default_copy()
    pet.config.update({"font_scale": 1.75, "bubble_min_font_size": 15, "bubble_screen_margin": 8})
    pet._always_on_top = True
    pet.pet_scale = 1.0
    pet.pet_display_width = 180
    pet._pending_quit = False
    pet._chat_box = None
    pet._chat_waiting = False
    pet._chat_session = None
    pet._chat_sent_times = []
    pet._current_category = None
    pet.dialogue_box.isVisible.return_value = False
    pet.x.return_value, pet.y.return_value = 310, 200
    pet.width.return_value, pet.height.return_value = 180, 212
    clock = {"t": 1000.0}
    pet._chat_clock = lambda: clock["t"]
    pet.clock = clock
    for name in METHODS:
        setattr(pet, name, getattr(PetWindow, name).__get__(pet))
    pet._make_chat_session = lambda: ac.ChatSession(
        pet._api_settings, dm._substitute, pet._chat_context, fetcher=fake, use_thread=False,
        persona_path=ROOT / "persona.md", timeout=5)
    return pet, fake, shown, dm


def send(pet, text):
    pet._chat_box._edit.setText(text)
    QTest.keyClick(pet._chat_box._edit, Qt.Key.Key_Return)
    app.processEvents()


pet, fake, shown, dm = rig()
pet._open_chat()
app.processEvents()
send(pet, "第一句")
check("对照: 第一句正常发出, 输入框清空", len(fake.calls) == 1 and pet._chat_box._edit.text() == "")
pet.clock["t"] += 1
send(pet, "太快的第二句")
check("被本地限流: 没有发请求", len(fake.calls) == 1)
check("被本地限流: 说 chat_too_frequent", shown[-1] == "chat_too_frequent", str(shown))
check("被本地限流: 用户打的字还在输入框里", pet._chat_box._edit.text() == "太快的第二句", repr(pet._chat_box._edit.text()))
check("被限流后: 输入框焦点/光标在末尾", pet._chat_box._edit.cursorPosition() == len("太快的第二句"))
pet._chat_box.close()

pet, fake, shown, dm = rig(key=None)
pet._open_chat()
app.processEvents()
send(pet, "没钥匙也想说")
check("没有 key: 说 chat_no_api, 没有请求", fake.calls == [] and shown[-1] == "chat_no_api", str(shown))
check("没有 key: 用户打的字还在输入框里", pet._chat_box._edit.text() == "没钥匙也想说", repr(pet._chat_box._edit.text()))
pet._chat_box.close()

# ===================================================================== 7
print("== 7 PetWindow: 回复不打断番茄钟 / 提醒, 队列里的提醒保留 ==")
pet, fake, shown, dm = rig()
dm._submit("pomodoro_end", seg("番茄钟到了"), PRI_POMODORO, bypass_interval=True)
pet._on_chat_submitted("番茄钟在说话时发的")
check("番茄钟台词在显示: 回复没有打断, 也没有冒「……」", shown == ["pomodoro_end"], str(shown))
check("番茄钟台词在显示: 回复排在后面", "chat_reply" in queued_cats(dm) and "chat_wait" not in queued_cats(dm), str(queued_cats(dm)))
dm.on_finished()
check("番茄钟说完后回复才出来", shown == ["pomodoro_end", "chat_reply"], str(shown))
pet, fake, shown, dm = rig()
pet._on_chat_submitted("先发")
check("先是回复(没有高优先级台词时直接显示)", shown == ["chat_wait", "chat_reply"], str(shown))
dm._submit("reminder_line", seg("喝水"), PRI_REMINDER, bypass_interval=True)
check("回复在显示时来了提醒: 提醒排在后面", queued_cats(dm) == ["reminder_line"])
pet.clock["t"] += 10
pet._chat_session._busy = False
pet._on_chat_submitted("再发")
check("再发一句: 「……」与回复插队, 但排着的提醒还在", "reminder_line" in queued_cats(dm), str(queued_cats(dm)))
dm.on_finished()
check("之后提醒照样说出来", shown[-1] == "reminder_line", str(shown))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("聊天台词 cut_in 用例全部通过")
sys.exit(0)
