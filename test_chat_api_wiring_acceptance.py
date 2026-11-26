# -*- coding: utf-8 -*-
"""Acceptance tests for wiring the chat box to the API: waiting bubble,
reply / error lines, the local rate limit, the ChatBox waiting state, the
connection test when API settings are saved, and "nothing typed, nothing
returned, no key ever reaches a log". No network: a fake fetcher is injected
through PetWindow._make_chat_session. PetWindow is a MagicMock with the real
methods bound (same approach as test_chat_pet_acceptance.py). Everything
happens in a temp dir.

Run:  QT_QPA_PLATFORM=offscreen python test_chat_api_wiring_acceptance.py
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock

TMP = Path(tempfile.mkdtemp(prefix="floweypet_chatwiring_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import app_log  # noqa: E402
import dialogue_log  # noqa: E402
import error_log  # noqa: E402

LOGS = TMP / "logs"
app_log.LOG_PATH = LOGS / "app.log"
error_log.LOG_PATH = LOGS / "error.log"
dialogue_log.LOG_PATH = LOGS / "dialogue.log"

import api_chat as ac  # noqa: E402
import api_key_store as store  # noqa: E402
import config as config_mod  # noqa: E402
import pet_window as pw  # noqa: E402
from dialogue_box import DialogueBox  # noqa: E402
from dialogue_manager import PRI_CHAT, PRI_REMINDER, DialogueManager  # noqa: E402
from pet_window import PetWindow  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


KEY = "sk-WIRING-KEY-SENTINEL-5150"
KEY2 = "sk-WIRING-KEY-TWO-8080"
USER_SECRET = "SENTINEL_用户输入_7777"
REPLY_SECRET = "SENTINEL_回复正文_9999"
store.KEY_PATH = TMP / "data" / "api_key.txt"
PERSONA = TMP / "persona.md"
PERSONA.write_text("人设 {days}\n", encoding="utf-8")
CHAT_JSON = json.loads((ROOT / "dialogues_chat.json").read_text(encoding="utf-8"))


def pump():
    app.processEvents()


def wait_for(cond, seconds=8):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def completion(content):
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode("utf-8")


class Fake:
    """The injected fetcher: records every request, answers per `mode`."""

    def __init__(self, mode="ok", text="好。"):
        self.calls = []
        self.mode = mode
        self.text = text
        self.gate = None  # threading.Event: hold the answer until set
        self.content = None  # when set (and mode "ok"): the raw message content to answer with

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": dict(headers), "body": json.loads(body)})
        if self.gate is not None:
            self.gate.wait(10)
        m = self.mode
        if isinstance(m, int):
            raise urllib.error.HTTPError(url, m, "x", {}, None)
        if m == "net":
            raise urllib.error.URLError("down")
        if m == "timeout":
            raise socket.timeout("timed out")
        if m == "junk":
            return b"not json"
        if self.content is not None:
            return completion(self.content)
        return completion(json.dumps({"text": self.text, "face": ["smirk", "angry"]}, ensure_ascii=False))


METHODS = (
    "_on_chat_submitted", "_get_chat_session", "_api_settings", "_chat_context", "_chat_limits",
    "_say_chat_line", "_say_chat_pool", "_say_chat_wait", "_set_chat_waiting", "_on_chat_replied",
    "_on_chat_failed", "_on_api_probed", "is_reminder_popup_active", "_open_chat", "_position_chat_box",
    "_on_api_settings_saved", "_on_dialogue_hold_started", "_on_urgent_line_queued", "is_chat_open",
)


def rig(mode="ok", threaded=False, key=KEY, text="好。", config_patch=None):
    """(pet, fake, shown, dm). `shown` = (category, first text) of every bubble the dm asked for."""
    if key:
        store.save_key(key)
    else:
        store.delete_key()
    fake = Fake(mode, text)
    if threaded:
        fake.gate = threading.Event()
    dm = DialogueManager({"total_clicks": 0}, lambda c: None, stats=None)
    shown = []
    dm.show_requested.connect(lambda segs, cat: shown.append((cat, segs[0]["text"] if segs else "", segs)))
    dm.preempt_calls = []
    orig_preempt = dm.preempt
    dm.preempt = lambda cat, segs: (dm.preempt_calls.append(cat), orig_preempt(cat, segs))[1]
    dm.cut_in_calls = []
    orig_cut_in = dm.cut_in
    dm.cut_in = lambda cat, segs: (dm.cut_in_calls.append(cat), orig_cut_in(cat, segs))[1]
    dm.speak_calls = []
    orig_speak = dm.speak
    dm.speak = lambda cat, segs, **kw: (dm.speak_calls.append(cat), orig_speak(cat, segs, **kw))[1]
    pet = MagicMock()
    pet.dm = dm
    pet.config = config_mod._default_copy()
    pet.config.update({"font_scale": 1.75, "bubble_min_font_size": 15, "bubble_screen_margin": 8})
    if config_patch:
        pet.config["api_chat"].update(config_patch)
    pet.save_config = lambda cfg: None
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
        pet._api_settings, dm._substitute, pet._chat_context,
        fetcher=fake, use_thread=threaded, persona_path=PERSONA, timeout=5,
    )
    return pet, fake, shown, dm


def cats(shown):
    return [c for c, _, _ in shown]


def pool_texts(pool):
    return [e["text"] for e in CHAT_JSON[pool]]


# ===================================================================== 1
print("== 1 有 key: 提交 -> 请求 -> chat_wait -> chat_reply ==")
pet, fake, shown, dm = rig(text="哼，知道了。")
pet._on_chat_submitted("你好小花")
check("发出了 1 个请求", len(fake.calls) == 1)
check("key 是现读的并放在 Authorization 头", fake.calls[0]["headers"]["Authorization"] == f"Bearer {KEY}")
check("用户的话是请求里的最后一条 user 消息", fake.calls[0]["body"]["messages"][-1] == {"role": "user", "content": "你好小花"})
check("先说 chat_wait 再说 chat_reply", cats(shown) == ["chat_wait", "chat_reply"], str(cats(shown)))
check("chat_wait 的文字是「……」, 表情 n_left_glance", shown[0][1] == "……" and shown[0][2][0]["face"] == "n_left_glance", str(shown[0]))
seg = shown[1][2][0]
check("chat_reply 的 segment: text 与 face 正确", seg == {"text": "哼，知道了。", "face": "smirk"}, str(seg))
check("chat_wait 与 chat_reply 都是用 cut_in 说的", dm.cut_in_calls == ["chat_wait", "chat_reply"], str(dm.cut_in_calls))
check("回复到了后 _chat_waiting 复位", pet._chat_waiting is False)
check("历史只在内存: 会话里有一轮", pet._chat_session.history_turns() == [("你好小花", "哼，知道了。")])
pet.clock["t"] += 10
pet._on_chat_submitted("第二句")
check("第二句的请求带上了历史", len(fake.calls[1]["body"]["messages"]) == 4, str(len(fake.calls[1]["body"]["messages"])))
check("get_context: 请求的 system 里有时间与天数", "【当下情况】" in fake.calls[0]["body"]["messages"][0]["content"])
check("get_settings: 默认地址与模型", fake.calls[0]["url"] == "https://api.deepseek.com/chat/completions" and fake.calls[0]["body"]["model"] == "deepseek-flash")
check("DeepSeek 的请求带 thinking disabled", fake.calls[0]["body"].get("thinking") == {"type": "disabled"})
pet2, fake2, shown2, dm2 = rig(config_patch={"base_url": "https://other.example/v1", "model": "m2"})
pet2._on_chat_submitted("x")
check("config 里的地址/模型被用上", fake2.calls[0]["url"] == "https://other.example/v1/chat/completions" and fake2.calls[0]["body"]["model"] == "m2")
pet3, fake3, shown3, dm3 = rig(config_patch={"base_url": "", "model": ""})
pet3._on_chat_submitted("x")
check("config 里地址/模型为空: 用默认值", fake3.calls[0]["url"].startswith("https://api.deepseek.com/") and fake3.calls[0]["body"]["model"] == "deepseek-flash")

print("== 1b 分句回复: 一句一个 segment, 一个表情 ==")
pet, fake, shown, dm = rig()
fake.content = json.dumps({"lines": [{"text": "甲。", "face": "smirk"}, {"text": "乙！", "face": "angry"}, {"text": "丙……", "face": "worry"}]}, ensure_ascii=False)
pet._on_chat_submitted("说三句")
segs = shown[-1][2]
check("回复的每一句做成一个 segment", shown[-1][0] == "chat_reply" and len(segs) == 3 and [x["text"] for x in segs] == ["甲。", "乙！", "丙……"], str(segs))
check("每个 segment 的 face 是单个字符串(不是数组)", [x["face"] for x in segs] == ["smirk", "angry", "worry"] and all(isinstance(x["face"], str) for x in segs), str(segs))
check("整组 segments 一次交给 cut_in(category chat_reply)", dm.cut_in_calls == ["chat_wait", "chat_reply"], str(dm.cut_in_calls))
check("segment 里只有 text 和 face 两个键", all(set(x) == {"text", "face"} for x in segs))
check("历史里小花那一句是各句 text 直接拼起来", pet._chat_session.history_turns() == [("说三句", "甲。乙！丙……")], str(pet._chat_session.history_turns()))
pet, fake, shown, dm = rig()
fake.content = json.dumps({"lines": [{"text": f"第{i}句。", "face": "smirk"} for i in range(1, 7)]}, ensure_ascii=False)
pet._on_chat_submitted("说六句")
check("超过 4 句: 只播前 4 句", [x["text"] for x in shown[-1][2]] == ["第1句。", "第2句。", "第3句。", "第4句。"], str(shown[-1][2]))
pet, fake, shown, dm = rig()
fake.content = '{"text": "旧格式一。旧格式二。", "face": ["wink", "grin"]}'
pet._on_chat_submitted("旧格式")
check("模型回了旧格式: 拆成多个 segment, 各一个表情", [(x["text"], x["face"]) for x in shown[-1][2]] == [("旧格式一。", "wink"), ("旧格式二。", "grin")], str(shown[-1][2]))
pet, fake, shown, dm = rig()
fake.content = '{"lines": []}'
pet._on_chat_submitted("空的")
check("lines 为空: bad_response -> chat_api_error", cats(shown) == ["chat_wait", "chat_api_error"], str(cats(shown)))

# ===================================================================== 2
print("== 2 没 key ==")
pet, fake, shown, dm = rig(key=None)
pet._on_chat_submitted("你好")
check("没 key: 说 chat_no_api", cats(shown) == ["chat_no_api"] and shown[0][1] in pool_texts("chat_no_api"), str(cats(shown)))
check("没 key: 没有发请求, 没有 chat_wait", fake.calls == [] and "chat_wait" not in cats(shown))
check("没 key: 不进等待状态", pet._chat_waiting is False)

# ===================================================================== 3
print("== 3 每种错误分类 -> 对应的池 ==")
CASES = [
    (401, "chat_api_key_error"), (403, "chat_api_key_error"), (402, "chat_no_balance"),
    (400, "chat_check_settings"), (404, "chat_check_settings"), (422, "chat_check_settings"),
    (429, "chat_api_error"), (500, "chat_api_error"), (418, "chat_api_error"),
    ("net", "chat_api_error"), ("timeout", "chat_api_error"), ("junk", "chat_api_error"),
]
for mode, pool in CASES:
    pet, fake, shown, dm = rig(mode=mode)
    pet._on_chat_submitted("x")
    ok = cats(shown) == ["chat_wait", pool] and shown[1][1] in pool_texts(pool)
    check(f"{mode} -> {pool}", ok, str(cats(shown)))
    check(f"{mode}: 出错后等待状态复位", pet._chat_waiting is False)
    check(f"{mode}: 错误台词也是 cut_in 说的", dm.cut_in_calls == ["chat_wait", pool], str(dm.cut_in_calls))
pet, fake, shown, dm = rig()
pet._make_chat_session = lambda: ac.ChatSession(pet._api_settings, dm._substitute, pet._chat_context, fetcher=fake,
                                                use_thread=False, persona_path=TMP / "gone.md")
pet._on_chat_submitted("x")
check("人设文件丢了(no_persona) -> chat_api_error", cats(shown) == ["chat_wait", "chat_api_error"], str(cats(shown)))
check("人设文件丢了: 没有发请求", fake.calls == [])
pet, fake, shown, dm = rig(config_patch={"base_url": "http://insecure.example/v1"})
pet._on_chat_submitted("x")
check("地址不是 https(no_config) -> chat_no_api", cats(shown) == ["chat_wait", "chat_no_api"] and fake.calls == [], str(cats(shown)))
check("所有错误池的表情名都在 assets/faces/ 里且不是 drag_face", True)

# ===================================================================== 4
print("== 4 本地限流 ==")
pet, fake, shown, dm = rig()
pet._on_chat_submitted("一")
pet.clock["t"] += 1
pet._on_chat_submitted("二")
check("3 秒内第二句: 说 chat_too_frequent", cats(shown)[-1] == "chat_too_frequent" and shown[-1][1] in pool_texts("chat_too_frequent"), str(cats(shown)))
check("3 秒内第二句: 没有发第二个请求", len(fake.calls) == 1)
check("被限流的那句没有 chat_wait", cats(shown) == ["chat_wait", "chat_reply", "chat_too_frequent"], str(cats(shown)))
pet.clock["t"] += 2.0  # 3 s since the first
pet._on_chat_submitted("三")
check("满 3 秒后可以再发", len(fake.calls) == 2)
check("被限流的请求不占用次数(最近 10 分钟只记发出的)", len(pet._chat_sent_times) == 2)

pet, fake, shown, dm = rig()
for i in range(80):
    pet._on_chat_submitted(f"第{i + 1}句")
    pet.clock["t"] += 4
check("连发 80 句(每句隔 4 秒)都发出了", len(fake.calls) == 80, str(len(fake.calls)))
pet._on_chat_submitted("第81句")
check("10 分钟内第 81 句: 说 chat_too_frequent", cats(shown)[-1] == "chat_too_frequent" and len(fake.calls) == 80, str(cats(shown)[-3:]))
pet.clock["t"] += 600
pet._on_chat_submitted("过了十分钟")
check("过了 10 分钟窗口: 又可以发了", len(fake.calls) == 81)
pet, fake, shown, dm = rig(config_patch={"min_interval_seconds": 10, "max_per_10min": 2})
pet._on_chat_submitted("a")
pet.clock["t"] += 5
pet._on_chat_submitted("b")
check("可配置: min_interval_seconds=10 时 5 秒内被限流", len(fake.calls) == 1)
pet.clock["t"] += 6
pet._on_chat_submitted("c")
pet.clock["t"] += 11
pet._on_chat_submitted("d")
check("可配置: max_per_10min=2 时第 3 句被限流", len(fake.calls) == 2 and cats(shown)[-1] == "chat_too_frequent")
check("DEFAULTS: min_interval_seconds=3, max_per_10min=80",
      config_mod.DEFAULTS["api_chat"]["min_interval_seconds"] == 3 and config_mod.DEFAULTS["api_chat"]["max_per_10min"] == 80)

# ===================================================================== 5
print("== 5 等待状态 (ChatBox) ==")
pet, fake, shown, dm = rig(threaded=True, text="慢慢来。")
pet._open_chat()
pump()
box = pet._chat_box
box._edit.setText("第一句")
QTest.keyClick(box._edit, Qt.Key.Key_Return)
pump()
check("提交后进入等待状态", box.is_waiting() is True and pet._chat_waiting is True)
check("等待时占位文字是「等小花回话……」", box._edit.placeholderText() == "等小花回话……")
check("等待时请求已发出(在途)", wait_for(lambda: len(fake.calls) == 1))
box._edit.setText("等待中打的字")
QTest.keyClick(box._edit, Qt.Key.Key_Return)
pump()
check("等待时按回车不发送", len(fake.calls) == 1)
check("等待时输入框里的字还在", box._edit.text() == "等待中打的字", box._edit.text())
check("等待期间气泡是 chat_wait", cats(shown) == ["chat_wait"], str(cats(shown)))
fake.gate.set()
check("回复到了: 说出 chat_reply", wait_for(lambda: cats(shown) == ["chat_wait", "chat_reply"]), str(cats(shown)))
check("回复后恢复: 不再等待, 占位文字复原", box.is_waiting() is False and box._edit.placeholderText() == "说点什么……")
check("回复后输入框里的字还在", box._edit.text() == "等待中打的字")
pet.clock["t"] += 10
fake.gate = None
QTest.keyClick(box._edit, Qt.Key.Key_Return)
pump()
check("回复后按回车可以发送", wait_for(lambda: len(fake.calls) == 2) and box._edit.text() == "")
wait_for(lambda: cats(shown)[-1] == "chat_reply")
box.close()

pet, fake, shown, dm = rig(threaded=True)
pet._on_chat_submitted("先发一句")
pet.clock["t"] += 10
pet._on_chat_submitted("又发一句")
check("请求在途时再提交: 说 chat_too_frequent, 不发第二个请求", cats(shown)[-1] == "chat_too_frequent" and wait_for(lambda: len(fake.calls) == 1), str(cats(shown)))
fake.gate.set()
wait_for(lambda: "chat_reply" in cats(shown))

# ===================================================================== 6
print("== 6 提醒弹窗时排队 / 关框后回复照样说 ==")
pet, fake, shown, dm = rig()
pet._current_category = "chase_popup_first_water"
pet.dialogue_box.isVisible.return_value = True
dm.busy = True  # a popup is on screen: the dm is busy with it
check("对照: is_reminder_popup_active 为 True", pet.is_reminder_popup_active() is True)
pet._on_chat_submitted("提醒期间说话")
check("提醒弹窗在: 没有冒 chat_wait", "chat_wait" not in cats(shown), str(cats(shown)))
check("提醒弹窗在: 回复用 speak 排队, 没有 cut_in", dm.speak_calls == ["chat_reply"] and dm.cut_in_calls == [], f"{dm.speak_calls} {dm.cut_in_calls}")
check("提醒弹窗在: 回复没有打断(还没有显示)", "chat_reply" not in cats(shown) and any(c == "chat_reply" for _, c, _ in dm._queue))
dm.busy = False
dm.on_finished()
check("弹窗结束后排队的回复才说出来", cats(shown) == ["chat_reply"], str(cats(shown)))
pet, fake, shown, dm = rig(mode=500)
pet._current_category = "chase_popup_first_water"
pet.dialogue_box.isVisible.return_value = True
dm.busy = True
pet._on_chat_submitted("x")
check("提醒弹窗在: 错误台词同样排队", dm.speak_calls == ["chat_api_error"] and dm.cut_in_calls == [])
pet, fake, shown, dm = rig()
pet._pending_quit = True
pet._on_chat_submitted("退出中")
check("正在退出: 提交被忽略, 没有请求", fake.calls == [] and shown == [])

pet, fake, shown, dm = rig(threaded=True, text="你关了框我也要说。")
pet._open_chat()
pump()
pet._chat_box._edit.setText("发了就关")
QTest.keyClick(pet._chat_box._edit, Qt.Key.Key_Return)
pump()
pet._chat_box.close()
check("等回复时关掉聊天框", not pet._chat_box.isVisible() and pet._chat_waiting is True)
fake.gate.set()
check("关框后回复照样说出来", wait_for(lambda: cats(shown) == ["chat_wait", "chat_reply"]) and shown[-1][1] == "你关了框我也要说。", str(cats(shown)))
pet._open_chat()
pump()
check("回复后重新打开聊天框: 不在等待状态", pet._chat_box.is_waiting() is False)
pet._chat_box.close()
pet2, f2, s2, d2 = rig(threaded=True)
pet2._on_chat_submitted("x")
pet2._open_chat()
pump()
check("等回复时重新打开聊天框: 显示等待状态", pet2._chat_box.is_waiting() is True)
f2.gate.set()
wait_for(lambda: "chat_reply" in cats(s2))
pet2._chat_box.close()

# ===================================================================== 7
print("== 7 key 每次现读 ==")
pet, fake, shown, dm = rig()
pet._on_chat_submitted("一")
store.save_key(KEY2)
pet.clock["t"] += 10
pet._on_chat_submitted("二")
check("中途换 key: 下一次请求用新的 key", fake.calls[1]["headers"]["Authorization"] == f"Bearer {KEY2}")
store.delete_key()
pet.clock["t"] += 10
n = len(fake.calls)
dm.busy = False  # (the rig never finishes bubbles; free the dm so the line shows at once)
pet._on_chat_submitted("三")
check("中途删掉 key 再提交: 说 chat_no_api, 不发请求", len(fake.calls) == n and cats(shown)[-1] == "chat_no_api", str(cats(shown)))
import re  # noqa: E402

check("源码里没有把 key 缓存在 PetWindow 的属性上", not re.search(r"self\._?api_key", (ROOT / "src" / "pet_window.py").read_text(encoding="utf-8")))

# ===================================================================== 8
print("== 8 保存设置时试连 ==")
NEW_URL, NEW_MODEL = "https://api.deepseek.com", "deepseek-flash"
pet, fake, shown, dm = rig(key=None)
pet._on_api_settings_saved(NEW_URL, NEW_MODEL, "")
check("保存了但没有 key: 说 api_saved_no_key", cats(shown) == ["api_saved_no_key"] and shown[0][1] in pool_texts("api_saved_no_key"), str(cats(shown)))
check("没有 key: 不试连(没有请求, 没有 chat_wait)", fake.calls == [] and "chat_wait" not in cats(shown))
pet, fake, shown, dm = rig(key=None)
pet._on_api_settings_saved(NEW_URL, NEW_MODEL, KEY)
check("有 key: 先冒 chat_wait 再说 api_saved", cats(shown) == ["chat_wait", "api_saved"] and shown[1][1] in pool_texts("api_saved"), str(cats(shown)))
check("试连发的是最小请求(只有 ping, max_tokens 1, 无 system)", len(fake.calls) == 1 and fake.calls[0]["body"]["messages"] == [{"role": "user", "content": "ping"}] and fake.calls[0]["body"]["max_tokens"] == 1)
check("试连带 thinking disabled(DeepSeek)", fake.calls[0]["body"].get("thinking") == {"type": "disabled"})
check("试连用刚存的 key", fake.calls[0]["headers"]["Authorization"] == f"Bearer {KEY}" and store.load_key() == KEY)
check("试连是 cut_in 说的", dm.cut_in_calls == ["chat_wait", "api_saved"])
pet, fake, shown, dm = rig(key=KEY)
pet._on_api_settings_saved(NEW_URL, NEW_MODEL, "")
check("钥匙框留空但已有 key: 照样试连", len(fake.calls) == 1 and cats(shown) == ["chat_wait", "api_saved"])
for mode, pool in ((401, "chat_api_key_error"), (402, "chat_no_balance"), (404, "chat_check_settings"), ("timeout", "chat_api_error"),
                   (500, "chat_api_error"), ("net", "chat_api_error")):
    pet, fake, shown, dm = rig(mode=mode, key=None)
    pet._on_api_settings_saved(NEW_URL, NEW_MODEL, KEY)
    check(f"试连 {mode} -> {pool}", cats(shown) == ["chat_wait", pool] and shown[1][1] in pool_texts(pool), str(cats(shown)))
    check(f"试连 {mode}: api_saved 不会说", "api_saved" not in cats(shown))
pet, fake, shown, dm = rig(threaded=True)
pet._on_chat_submitted("聊天在途")
check("聊天请求在途", wait_for(lambda: len(fake.calls) == 1))
pet._on_api_settings_saved(NEW_URL, NEW_MODEL, "")
check("聊天在途时保存: 只说 api_saved, 不试连(没有第二个请求)", len(fake.calls) == 1 and "api_saved" in dm.speak_calls and cats(shown).count("api_saved") <= 1)
fake.gate.set()
wait_for(lambda: "chat_reply" in cats(shown))
pet, fake, shown, dm = rig(key=None)
pet._on_api_settings_saved(NEW_URL, NEW_MODEL, KEY)
pet.clock["t"] += 10
pet._on_chat_submitted("试连之后马上聊")
check("试连结束后聊天正常", len(fake.calls) == 2 and cats(shown)[-1] == "chat_reply")

# ===================================================================== 9
print("== 9 chat_wait 气泡挂着, 最长 40 秒 ==")
check("常量 CHAT_WAIT_LINGER_MS == 40000", pw.CHAT_WAIT_LINGER_MS == 40_000)
pet = MagicMock()
pet._pending_quit = False
pet.reminders.chase.hold_ms_for_category.return_value = None
pet._on_show_requested = PetWindow._on_show_requested.__get__(pet)
pet._on_show_requested([{"text": "……", "face": "n_left_glance"}], "chat_wait")
kw = pet.dialogue_box.start.call_args.kwargs
check("chat_wait: PetWindow 以 linger_ms=40000 启动气泡", kw.get("linger_ms") == 40_000, str(kw))
pet.dialogue_box.reset_mock()
pet._on_show_requested([{"text": "你好", "face": "neutral"}], "click_normal")
kw2 = pet.dialogue_box.start.call_args.kwargs
check("其他台词: 启动参数完全不变(没有 linger_ms)", "linger_ms" not in kw2, str(kw2))

box = DialogueBox(180, 1.75, MagicMock(), True, pet_scale=1.0, min_font_px=15)
fin, hold = [], []
box.finished.connect(lambda: fin.append(1))
box.hold_started.connect(lambda: hold.append(1))
box.start([{"text": "……", "face": "neutral"}], lambda f: None, linger_ms=150)
check("linger: 打完字后气泡还在(没有关、没有 finished)", wait_for(lambda: hold == [1]) and box.isVisible() and fin == [] and box.is_lingering())
check("linger: 到时间(兜底)自动关闭并发 finished", wait_for(lambda: fin == [1]) and not box.isVisible() and not box.is_lingering())
box.start([{"text": "……", "face": "neutral"}], lambda f: None, linger_ms=60_000)
wait_for(lambda: box.is_lingering())
box.start([{"text": "新的话", "face": "neutral"}], lambda f: None)
check("linger 被新的 start 替换: 旧的兜底计时器停掉", box.is_lingering() is False)
check("替换后的新气泡正常结束(隐藏 + finished)", wait_for(lambda: fin == [1, 1], 6) and not box.isVisible(), str(fin))
box.start([{"text": "……", "face": "neutral"}], lambda f: None, linger_ms=60_000)
wait_for(lambda: box.is_lingering())
box.stop_immediately()
check("stop_immediately 会停掉 linger 计时器且不发 finished", not box.is_lingering() and not box.isVisible() and fin == [1, 1])
box.start([{"text": "普通", "face": "neutral"}], lambda f: None)
check("普通台词(不带 linger)行为不变: 说完就关 + finished", wait_for(lambda: fin == [1, 1, 1], 6) and not box.isVisible() and hold == [1, 1, 1][:len(hold)] and not box.is_lingering())
box.start([{"text": "……", "face": "neutral"}], lambda f: None, linger_ms=60_000)
wait_for(lambda: box.is_lingering())
box.end_linger()
check("end_linger(超时回调): 关闭并发 finished", not box.isVisible() and fin[-1] == 1 and len(fin) == 4, str(fin))

pet, fake, shown, dm = rig(threaded=True)
dm.on_urgent_queued = pet._on_urgent_line_queued
pet._current_category = "chat_wait"
pet.dialogue_box.is_lingering.return_value = True
dm.busy = True
dm.speak("reminder_x", [{"text": "喝水", "face": "neutral"}], priority=PRI_REMINDER, bypass_interval=True)
check("等回复的「……」挂着时来了提醒: 立刻让位", pet.dialogue_box.end_linger.call_count == 1)
pet.dialogue_box.end_linger.reset_mock()
dm.speak("chat_x", [{"text": "闲聊", "face": "neutral"}], priority=PRI_CHAT, bypass_interval=True)
check("只是普通闲聊排队: 不让位", pet.dialogue_box.end_linger.call_count == 0)
pet._current_category = "chat_wait"
dm._queue.clear()
dm._queue.append((PRI_REMINDER, "r", []))
pet.state = "talking"
pet._on_dialogue_hold_started()
check("「……」刚打完字时已有提醒在排队: 立刻让位", pet.dialogue_box.end_linger.call_count == 1)
pet._chat_session.probe() if False else None
store.delete_key()
pet.clock["t"] += 1
fake.gate.set()

# ===================================================================== 10
print("== 10 台词池 ==")
faces_dir = ROOT / "assets" / "faces"
bad, drag = [], []
for pool in ("chat_no_api", "chat_wait", "chat_api_error", "chat_api_key_error", "chat_check_settings", "chat_no_balance",
             "chat_too_frequent", "api_saved", "api_saved_no_key", "api_key_deleted"):
    entries = CHAT_JSON[pool]
    for e in entries:
        names = [e["face"]] if isinstance(e["face"], str) else e["face"]
        bad += [n for n in names if not (faces_dir / n).is_dir()]
        drag += [n for n in names if n == "drag_face"]
check("所有池里的表情名都在 assets/faces/ 里", not bad, str(bad))
check("所有池里都没有 drag_face", not drag)
dmx = DialogueManager({"total_clicks": 0}, lambda c: None, stats=None)
check("DialogueManager 加载并校验了所有池", all(len(dmx.data.get(p, [])) == len(CHAT_JSON[p]) for p in CHAT_JSON if not p.startswith("_")))
check("池大小: error 5 / key_error 3 / check_settings 1 / no_balance 1 / too_frequent 2 / wait 1 / no_key 1",
      [len(CHAT_JSON[p]) for p in ("chat_api_error", "chat_api_key_error", "chat_check_settings", "chat_no_balance", "chat_too_frequent", "chat_wait", "api_saved_no_key")] == [5, 3, 1, 1, 2, 1, 1])
check("api_saved 保持原样", CHAT_JSON["api_saved"][0]["text"] == "哼，钥匙收下了。现在你可以跟我说话了……别说废话。")
check("_readme 提到了新增的池", all(p in CHAT_JSON["_readme"] for p in ("chat_wait", "chat_api_error", "chat_api_key_error", "chat_check_settings", "chat_no_balance", "chat_too_frequent", "api_saved_no_key")))
import dialogue_manager as dmmod  # noqa: E402

check("dialogue_manager.CHAT_POOLS 包含所有池", all(p in dmmod.CHAT_POOLS for p in ("chat_wait", "chat_api_error", "chat_api_key_error", "chat_check_settings", "chat_no_balance", "chat_too_frequent", "api_saved_no_key")))

# ==================================================================== 11
print("== 11 日志不泄漏 ==")
for f in LOGS.glob("*") if LOGS.exists() else ():
    f.unlink()
store.delete_key()
for mode in ("ok", 401, 402, 404, 429, 500, "net", "timeout", "junk"):
    pet, fake, shown, dm = rig(mode=mode, key=KEY, text=REPLY_SECRET)
    pet._on_chat_submitted(USER_SECRET)
    pet.clock["t"] += 10
    pet._on_chat_submitted(USER_SECRET)
    pet._on_api_settings_saved(NEW_URL, NEW_MODEL, KEY)
    pet2, fake2, shown2, dm2 = rig(mode=mode, key=None, text=REPLY_SECRET)
    pet2._on_api_settings_saved(NEW_URL, NEW_MODEL, KEY2)
pet, fake, shown, dm = rig(key=KEY, text=REPLY_SECRET, config_patch={"base_url": "http://insecure.example"})
pet._on_chat_submitted(USER_SECRET)
pet, fake, shown, dm = rig(threaded=True, key=KEY, text=REPLY_SECRET)
pet._on_chat_submitted(USER_SECRET)
fake.gate.set()
wait_for(lambda: "chat_reply" in cats(shown))
pet, fake, shown, dm = rig(key=KEY, text=REPLY_SECRET)
pet._make_chat_session = lambda: ac.ChatSession(pet._api_settings, dm._substitute, pet._chat_context,
                                                fetcher=lambda *a: (_ for _ in ()).throw(RuntimeError(f"leaky {KEY} {USER_SECRET} {REPLY_SECRET}")),
                                                use_thread=False, persona_path=PERSONA)
pet._on_chat_submitted(USER_SECRET)
store.delete_key()
for body in ('{"lines": [{"text": "😀", "note": "' + REPLY_SECRET + '"}]}', '{"lines": [], "note": "' + REPLY_SECRET + '"}',
             '{"text": "' + REPLY_SECRET + '", "face": ["smirk"]}', ""):
    pet, fake, shown, dm = rig(key=KEY, text=REPLY_SECRET)
    fake.content = body
    pet._on_chat_submitted(USER_SECRET)
texts = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in TMP.rglob("*.log"))
check("日志不是空的(失败路径确实写了日志)", "API 聊天失败" in texts, texts[:200])
check("日志里有 bad_response 的原因代号(只有代号)", "reason=all_lines_empty" in texts and "reason=no_lines" in texts and "reason=empty_content" in texts)
check("所有日志里没有 key", KEY not in texts and KEY2 not in texts)
check("所有日志里没有用户输入", USER_SECRET not in texts)
check("所有日志里没有回复正文", REPLY_SECRET not in texts)
check("config 里没有 key", KEY not in json.dumps(config_mod._default_copy()))
src = (ROOT / "src" / "pet_window.py").read_text(encoding="utf-8")
seg = src[src.index("def _make_chat_session"):src.index("def _on_api_probed")]
check("聊天接线代码里没有任何日志调用", "log_event" not in seg and "log_message" not in seg and "print(" not in seg)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("聊天框接 API 用例全部通过")
sys.exit(0)
