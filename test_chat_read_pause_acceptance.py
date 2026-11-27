# -*- coding: utf-8 -*-
"""Acceptance tests for the chat reply reading pause (3.4.1): each sentence of
a chat reply carries "read_pause_ms" (base + per_char * len, clamped to
[min, max], all from config api_chat), DialogueBox uses it instead of
PAGE_PAUSE_MS for that segment, and everything else (hand-written lines,
chat_wait, error lines) is untouched. Timing is checked by recording the
delays DialogueBox hands to QTimer.singleShot (and firing them at once), so
no test waits seconds. Everything happens in a temp dir.

Run:  QT_QPA_PLATFORM=offscreen python test_chat_read_pause_acceptance.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

TMP = Path(tempfile.mkdtemp(prefix="floweypet_readpause_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import app_log  # noqa: E402
import dialogue_log  # noqa: E402
import error_log  # noqa: E402

LOGS = TMP / "logs"
app_log.LOG_PATH = LOGS / "app.log"
error_log.LOG_PATH = LOGS / "error.log"
dialogue_log.LOG_PATH = LOGS / "dialogue.log"

import api_key_store as store  # noqa: E402
import config as config_mod  # noqa: E402
import dialogue_box as db_mod  # noqa: E402
from dialogue_box import PAGE_PAUSE_MS, DialogueBox  # noqa: E402
from dialogue_manager import DialogueManager  # noqa: E402
from pet_window import PetWindow  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


class _Audio:
    def __getattr__(self, name):
        return lambda *a, **k: None


# ---- controllable timing: record every singleShot DialogueBox asks for, fire it at once
RECORD = []  # (seg_idx, callback name, delay ms)
CURRENT_BOX = [None]


class RecTimer(QTimer):
    @staticmethod
    def singleShot(ms, callback):
        box = CURRENT_BOX[0]
        RECORD.append((box._seg_idx if box is not None else None, getattr(callback, "__name__", "?"), ms))
        QTimer.singleShot(0, callback)


db_mod.QTimer = RecTimer
db_mod.TYPE_INTERVAL_MS = 1
db_mod.SLOW_TYPE_INTERVAL_MS = 1


def play(segments):
    """Plays segments through a real DialogueBox to the end; returns the
    recorded page pauses as [(seg_idx, ms)]."""
    RECORD.clear()
    box = DialogueBox(180, 1.75, _Audio(), True, pet_scale=1.0, min_font_px=15)
    CURRENT_BOX[0] = box
    done = []
    box.finished.connect(lambda: done.append(True))
    box.start(segments, lambda face: None)
    end = time.monotonic() + 10
    while not done and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.002)
    CURRENT_BOX[0] = None
    pauses = [(i, ms) for i, name, ms in RECORD if name == "_advance_page"]
    box.close()
    return (pauses if done else None)


# ===================================================================== 1
print("== 1 DialogueBox: read_pause_ms 只影响带这个键的 segment ==")
r = play([{"text": "你好。", "face": "neutral", "read_pause_ms": 2345}])
check("带 read_pause_ms 的 segment: 打完字后停这个值", r == [(0, 2345)], str(r))
r = play([{"text": "你好。", "face": "neutral"}])
check("不带的 segment: 仍然是 PAGE_PAUSE_MS", r == [(0, PAGE_PAUSE_MS)] and PAGE_PAUSE_MS == 1100, str(r))
r = play([{"text": "甲。", "face": "neutral", "read_pause_ms": 3000}, {"text": "乙。", "face": "neutral"},
          {"text": "丙。", "face": "neutral", "read_pause_ms": 1600}])
check("混合: 各 segment 各用各的(带键的用自己的, 不带的 1100)", r == [(0, 3000), (1, PAGE_PAUSE_MS), (2, 1600)], str(r))
r = play([{"text": "很长的一句话。" * 12, "face": "neutral", "read_pause_ms": 777}])
check("多页的 segment: 每一页都用这个值", r is not None and len(r) >= 2 and all(ms == 777 for _, ms in r), str(r))
for bad in (0, -5, "2000", None, True, False, float("nan")):
    r = play([{"text": "你好。", "face": "neutral", "read_pause_ms": bad}])
    check(f"read_pause_ms={bad!r} 不可用: 回退 PAGE_PAUSE_MS", r == [(0, PAGE_PAUSE_MS)], str(r))
r = play([{"text": "你好。", "face": "neutral", "read_pause_ms": 1234.6}])
check("小数取整", r == [(0, 1234)], str(r))
r = play([{"text": "……", "face": "neutral", "slow": True, "read_pause_ms": 900}])
check("slow segment 也认这个键", r == [(0, 900)], str(r))
seg_end = [ms for i, name, ms in RECORD if name == "_advance_segment"]
check("segment 之间的 SEGMENT_END_PAUSE_MS 不变", seg_end == [db_mod.SEGMENT_END_PAUSE_MS] and db_mod.SEGMENT_END_PAUSE_MS == 400, str(seg_end))

# ===================================================================== 2
print("== 2 配置默认值 ==")
D = config_mod.DEFAULTS["api_chat"]
check("DEFAULTS 里有四个键且值正确",
      (D.get("read_pause_base_ms"), D.get("read_pause_per_char_ms"), D.get("read_pause_min_ms"), D.get("read_pause_max_ms")) == (1200, 80, 1500, 4500), str(D))
old = {"api_chat": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash", "min_interval_seconds": 3, "max_per_10min": 80}}
merged = config_mod._deep_merge(config_mod.DEFAULTS, old)["api_chat"]
check("老 config(没有这四个键)读进来自动补上, 其余键保持", merged["read_pause_base_ms"] == 1200 and merged["read_pause_max_ms"] == 4500 and merged["max_per_10min"] == 80, str(merged))
dev_cfg = ROOT / "dev_data" / "config.json"
if dev_cfg.exists():
    loaded = json.loads(dev_cfg.read_text(encoding="utf-8"))
    m = config_mod._deep_merge(config_mod.DEFAULTS, loaded)["api_chat"]
    check("dev_data/config.json(只读): 合并后四个键齐全", all(k in m for k in ("read_pause_base_ms", "read_pause_per_char_ms", "read_pause_min_ms", "read_pause_max_ms")), str(m))
    print("   (dev_data/config.json 里原本有这四个键吗:", {k: k in loaded.get("api_chat", {}) for k in ("read_pause_base_ms", "read_pause_max_ms")}, ")")
else:
    print("   (没有 dev_data/config.json, 跳过)")

# ===================================================================== 3
print("== 3 PetWindow: chat_reply 每句带 read_pause_ms ==")
METHODS = (
    "_on_chat_submitted", "_get_chat_session", "_api_settings", "_chat_context", "_chat_limits", "_say_chat_line",
    "_say_chat_pool", "_say_chat_wait", "_set_chat_waiting", "_on_chat_replied", "_chat_read_pause_ms",
    "_chat_read_pause_params", "_on_chat_failed", "_on_api_probed", "is_reminder_popup_active", "_restore_chat_text",
)
store.KEY_PATH = TMP / "data" / "api_key.txt"


def rig(api_patch=None, api_value="__keep__"):
    dm = DialogueManager({"total_clicks": 0}, lambda c: None, stats=None)
    shown = []
    dm.show_requested.connect(lambda segs, cat: shown.append((cat, segs)))
    pet = MagicMock()
    pet.dm = dm
    pet.config = config_mod._default_copy()
    pet.config.update({"font_scale": 1.75, "bubble_min_font_size": 15})
    if api_value != "__keep__":
        pet.config["api_chat"] = api_value
    elif api_patch:
        pet.config["api_chat"].update(api_patch)
    pet._pending_quit = False
    pet._chat_box = None
    pet._chat_waiting = False
    pet._current_category = None
    pet.dialogue_box.isVisible.return_value = False
    for name in METHODS:
        setattr(pet, name, getattr(PetWindow, name).__get__(pet))
    return pet, shown


def reply(pet, texts):
    pet._on_chat_replied({"lines": [{"text": t, "face": "smirk"} for t in texts]})


pet, shown = rig()
reply(pet, ["一" * 10, "二" * 25, "三" * 60, "四" * 1, "五" * 200])
segs = shown[-1][1]
check("类别是 chat_reply, 每句一个 segment", shown[-1][0] == "chat_reply" and len(segs) == 5, str(shown))
check("每句都带 read_pause_ms", all("read_pause_ms" in s for s in segs), str(segs))
check("10 字 -> 2000ms", segs[0]["read_pause_ms"] == 2000, str(segs[0]))
check("25 字 -> 3200ms", segs[1]["read_pause_ms"] == 3200, str(segs[1]))
check("60 字 -> 4500ms(封顶)", segs[2]["read_pause_ms"] == 4500, str(segs[2]))
check("1 字 -> 1280 被夹到下限 1500", segs[3]["read_pause_ms"] == 1500, str(segs[3]))
check("200 字 -> 封顶 4500", segs[4]["read_pause_ms"] == 4500, str(segs[4]))
check("text 和 face 原样保留", [s["text"] for s in segs] == ["一" * 10, "二" * 25, "三" * 60, "四", "五" * 200] and all(s["face"] == "smirk" for s in segs))
check("read_pause_ms 是整数", all(isinstance(s["read_pause_ms"], int) for s in segs))
# the value survives dm._substitute and reaches the box unchanged
r = play([dict(s, text="好。") for s in segs[:2]])
check("交给 DialogueBox 播放: 各用各的停顿", r == [(0, 2000), (1, 3200)], str(r))

pet, shown = rig({"read_pause_base_ms": 100, "read_pause_per_char_ms": 10, "read_pause_min_ms": 50, "read_pause_max_ms": 500})
reply(pet, ["一" * 10, "二" * 100, "三"])
check("自定义合法配置生效: 10 字 -> 200, 100 字 -> 封顶 500, 1 字 -> 110", [s["read_pause_ms"] for s in shown[-1][1]] == [200, 500, 110], str(shown[-1][1]))

# ===================================================================== 4
print("== 4 配置不合法 -> 回退默认值 ==")
BAD = {
    "min > max": {"read_pause_min_ms": 5000, "read_pause_max_ms": 2000},
    "base = 0": {"read_pause_base_ms": 0},
    "per_char 负数": {"read_pause_per_char_ms": -80},
    "min = 0": {"read_pause_min_ms": 0},
    "max 是字符串": {"read_pause_max_ms": "4500"},
    "base 是 True": {"read_pause_base_ms": True},
    "per_char 是 None": {"read_pause_per_char_ms": None},
    "base 是 nan": {"read_pause_base_ms": float("nan")},
    "min 是列表": {"read_pause_min_ms": [1500]},
}
for label, patch in BAD.items():
    pet, shown = rig(patch)
    reply(pet, ["一" * 10, "二" * 60])
    got = [s["read_pause_ms"] for s in shown[-1][1]]
    check(f"{label}: 回退默认(10 字 2000 / 60 字 4500)", got == [2000, 4500], str(got))
for label, val in (("api_chat 不是 dict", "oops"), ("api_chat 是 None", None), ("api_chat 是空 dict", {})):
    pet, shown = rig(api_value=val)
    reply(pet, ["一" * 10])
    check(f"{label}: 用默认值", shown[-1][1][0]["read_pause_ms"] == 2000, str(shown[-1][1]))
pet, shown = rig({"read_pause_min_ms": 3000, "read_pause_max_ms": 3000})
reply(pet, ["一", "一" * 100])
check("min == max 合法: 固定 3000", [s["read_pause_ms"] for s in shown[-1][1]] == [3000, 3000], str(shown[-1][1]))
pet, shown = rig({"read_pause_base_ms": 1.5, "read_pause_per_char_ms": 0.5, "read_pause_min_ms": 1, "read_pause_max_ms": 100})
reply(pet, ["一" * 10])
check("小数配置合法, 结果取整", shown[-1][1][0]["read_pause_ms"] == 7 or shown[-1][1][0]["read_pause_ms"] == 6, str(shown[-1][1]))

# ===================================================================== 5
print("== 5 别的聊天台词不带这个键 ==")
store.delete_key()
pet, shown = rig()
pet._say_chat_wait()
check("chat_wait 不带", shown and shown[-1][0] == "chat_wait" and all("read_pause_ms" not in s for s in shown[-1][1]), str(shown))
for code in ("net", 401, 402, 400, 429, "junk"):
    pet, shown = rig()
    pet._on_chat_failed(code)
    check(f"错误台词({code}) 不带", shown and all("read_pause_ms" not in s for _, segs in shown for s in segs), str(shown))
pet, shown = rig()
pet.dm.trigger_chat_no_api()
check("chat_no_api 不带", shown and all("read_pause_ms" not in s for _, segs in shown for s in segs), str(shown))
for outcome in ("ok", "net", 401):
    pet, shown = rig()
    pet._on_api_probed(outcome)
    check(f"api 试连结果 {outcome!r}(api_saved/错误) 不带", shown and all("read_pause_ms" not in s for _, segs in shown for s in segs), str(shown))
CHAT_JSON = json.loads((ROOT / "dialogues_chat.json").read_text(encoding="utf-8"))
check("dialogues_chat.json 里所有台词都不带这个键", "read_pause_ms" not in json.dumps(CHAT_JSON))

# ===================================================================== 6
print("== 6 手写台词不带, 播放速度不变 ==")
src_all = "".join((ROOT / n).read_text(encoding="utf-8") for n in sorted(p.name for p in ROOT.glob("dialogue*.json")))
check("所有 dialogue*.json / dialogues_*.json 里都没有 read_pause_ms", "read_pause_ms" not in src_all)
pet, shown = rig()
pet.dm.trigger_menu_flavor("fight")  # a hand-written line from dialogue.json
hand = shown[-1][1] if shown else None
check("dialogue.json 的 fight 台词: segment 里没有 read_pause_ms", bool(hand) and all("read_pause_ms" not in s for s in hand), str(hand))
r = play(hand) if hand else None
check("播放这条手写台词: 停顿是 PAGE_PAUSE_MS", r is not None and len(r) >= 1 and all(ms == PAGE_PAUSE_MS for _, ms in r), str(r))
dm = DialogueManager({"total_clicks": 0}, lambda c: None, stats=None)
shown2 = []
dm.show_requested.connect(lambda segs, cat: shown2.append((cat, segs)))
dm.trigger_chat_no_api()
other = [s for _, segs in shown2 for s in segs]
check("dm 自己生成的台词也不带", other and all("read_pause_ms" not in s for s in other), str(shown2))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("聊天回复阅读停顿用例全部通过")
sys.exit(0)
