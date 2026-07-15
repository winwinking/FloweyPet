# -*- coding: utf-8 -*-
"""Acceptance tests for DialogueArbiter (功能防抖/打断/连点/喷人), covering
the 验收用例 list as far as they're pure arbiter logic (cases 1-8, plus the
exit-interrupt pool lookup used by case 9). Offscreen, no real GUI windows
-- same pattern as test_chase_acceptance.py: a fake dm records every
preempt() call, a fake "pet" simulates is_reminder_popup_active()/
is_exiting()/force_end_bubble().

Cases 9 (exit-click lock), 10 (reminder-popup suppression wired through
PetWindow), and 11 (full dialogue.log chain through the real UI) need a
live PetWindow and are NOT exercised here -- see the session notes for
what was verified by code review instead.

Run:  QT_QPA_PLATFORM=offscreen python test_dialogue_arbiter_acceptance.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

app = QCoreApplication(sys.argv)

import dialogue_arbiter  # noqa: E402
from dialogue_arbiter import DialogueArbiter  # noqa: E402

# Point the arbiter at the real, hand-authored content file instead of a
# throwaway fixture -- exercises the actual transcribed pools too.
dialogue_arbiter.DIALOGUE_SPAM_PATH = (
    Path(__file__).resolve().parent / "dialogues_spam.json"
)

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


class FakeDM:
    def __init__(self):
        self.preempted = []  # (monotonic, category)

    def preempt(self, category, segments):
        assert segments, f"empty segments for {category}"
        self.preempted.append((time.monotonic(), category))
        return True

    def categories(self):
        return [c for _, c in self.preempted]


class FakePet:
    def __init__(self):
        self.reminder_popup_active = False
        self.exiting = False
        self.talking = False  # simulated "is Flowey currently mid-speech"

    def is_reminder_popup_active(self):
        return self.reminder_popup_active

    def is_exiting(self):
        return self.exiting

    def force_end_bubble(self):
        was = self.talking
        self.talking = False
        return was


def make_arbiter(**overrides):
    cfg = {
        "dialogue_spam": {
            "feature_debounce_seconds": 0.3,
            "feature_escalation_delay_steps": [[0.3, 0.4], [0.6, 0.7], [0.9, 1.0]],
            "feature_reset_seconds": 2,
            "feature_silence_seconds": 2,
            "body_click_window_seconds": 1,
            "body_click_threshold": 4,
            "interrupt_rant_threshold": 3,
            "interrupt_rant_cooldown_seconds": 2,
            **overrides,
        }
    }
    dm = FakeDM()
    pet = FakePet()
    return DialogueArbiter(cfg, dm, pet), dm, pet


# ---------------------------------------------------------------- 用例1
# 点一次功能 -> 生效(由调用方模拟)，0.3s后播一次正常台词
arb, dm, pet = make_arbiter()
calls = []
assert arb.begin_feature("pin")
arb.finish_feature("pin", lambda: calls.append("pin_on"))
check("用例1: 防抖期内不立即播", calls == [])
wait_s(0.45)
check("用例1: 0.5s(此处0.3s)后播放一次", calls == ["pin_on"])

# ---------------------------------------------------------------- 用例2
# 0.3s内狂点 -> 只有第一次被接受，台词只播一次
arb, dm, pet = make_arbiter()
accepted = [arb.begin_feature("pin") for _ in range(5)]
check("用例2: 5次点击只有第1次被接受", accepted == [True, False, False, False, False], str(accepted))
calls = []
arb.finish_feature("pin", lambda: calls.append("pin_on"))
wait_s(0.45)
check("用例2: 台词只播一次", calls == ["pin_on"])

# ---------------------------------------------------------------- 用例3
# 每次点击(点击间隔足够让上一句先播完)都严格推进恰好一级，绝不重复同一级：
# 正常->不耐烦->烦躁->愤怒(喷人)->沉默，且沉默后不再播放
arb, dm, pet = make_arbiter(feature_reset_seconds=999, feature_silence_seconds=999)
stages_seen = []
for i in range(6):
    assert arb.begin_feature("pin"), f"click {i} unexpectedly rejected"
    arb.finish_feature("pin", lambda: stages_seen.append("normal"))
    wait_s(0.35)  # 等这次的台词播完(_active_key清空)再点下一次，避免进入"排队"分支
    arb.on_bubble_finished()  # 模拟这句气泡已经播完
categories = [c for c in dm.categories() if c.startswith("spam_pin_stage")]
check(
    "用例3: 阶段严格递增且不重复(含阶段4的收尾一句)",
    categories == ["spam_pin_stage1", "spam_pin_stage2", "spam_pin_stage3", "spam_pin_stage4"],
    str(categories),
)
check("用例3: 只有第1次是正常台词", stages_seen == ["normal"])
check("用例3: 阶段4之后(第6次)真正沉默，不再播台词", len(dm.categories()) == 4, str(dm.categories()))

# --------------------------------------------------- 播放中不打断，排队+递增延时
# 同一功能项的反应正在播放时再点它：不应该被打断/重播，只排队，等这句播完后
# 按"连续第几次排队"从 feature_escalation_delay_steps 取一个递增的随机延时，
# 再触发下一级(且下一级和上一级不同，不会原地重复)。
arb, dm, pet = make_arbiter()
assert arb.begin_feature("pin")
arb.finish_feature("pin", lambda: dm.preempt("normal_marker", [{"text": "x", "face": "neutral"}]))
wait_s(0.35)  # 阶段0触发(用preempt充当正常台词，方便在dm.categories里看到)
check("播放中不打断: 阶段0已播放，active_key=pin", arb._active_key == "pin" and dm.categories()[-1] == "normal_marker")

pet.talking = True  # 模拟这条"正常台词"气泡确实还在屏幕上
n_before = len(dm.categories())
t_click_again = time.monotonic()
arb.finish_feature("pin", lambda: None)  # 播放中又点了一次同一功能
check(
    "播放中不打断: 没有新气泡/没有调用force_end_bubble",
    len(dm.categories()) == n_before and pet.talking is True,
)
check("播放中不打断: 标记为pending，等这句播完", arb._feature("pin").get("pending") is True)

arb.on_bubble_finished()  # 模拟这句正常台词播完了
check("播放中不打断: active_key已清空", arb._active_key is None)
wait_s(0.2)
check("排队延时: 第1次排队(1-2档)不会立刻触发", len(dm.categories()) == n_before)
wait_s(0.35)  # 累计约0.55s，落在配置的第1档[0.3,0.4]s范围之后
check(
    "排队延时: 第1次排队后触发了阶段1(不是阶段0，不重复)",
    dm.categories()[-1] == "spam_pin_stage1",
    str(dm.categories()[-3:]),
)
t_stage1_fired = time.monotonic()
check("排队延时: 确实等待了>=0.3s才触发", (t_stage1_fired - t_click_again) >= 0.3)

# 再来一轮，验证第2次排队的延时比第1次更长(0.6-0.7档)
pet.talking = True
arb.finish_feature("pin", lambda: None)
check("排队延时: 第2次同样只是排队", arb._feature("pin").get("pending") is True)
arb.on_bubble_finished()
wait_s(0.5)
check("排队延时: 第2次排队(0.6-0.7档)比第1次更久，0.5s时还没触发", dm.categories()[-1] == "spam_pin_stage1")
wait_s(0.35)
check(
    "排队延时: 第2次排队最终触发了阶段2(依次递增)",
    dm.categories()[-1] == "spam_pin_stage2",
    str(dm.categories()[-3:]),
)

# ---------------------------------------------------------------- 用例4
# 小花说话中点功能 -> 功能生效(由调用方模拟)，台词改为"不满"
arb, dm, pet = make_arbiter()
pet.talking = True
assert arb.begin_feature("mute")
arb.finish_feature("mute", lambda: (_ for _ in ()).throw(AssertionError("normal line must NOT play")))
wait_s(0.45)
check("用例4: 打断时不播正常台词，改播不满", dm.categories() == ["spam_interrupted_reply"], str(dm.categories()))

# ---------------------------------------------------------------- 用例5
# 累计打断3次 -> 触发喷人(rant_interrupt)，之后计数清零
arb, dm, pet = make_arbiter()
for key in ("mute", "hide", "pin"):
    pet.talking = True
    assert arb.begin_feature(key)
    arb.finish_feature(key, lambda: None)
    wait_s(0.35)
check(
    "用例5: 第3次打断触发喷人(rant_interrupt)",
    dm.categories() == ["spam_interrupted_reply", "spam_interrupted_reply", "spam_rant_interrupt"],
    str(dm.categories()),
)
check("用例5: 喷人后打断计数清零", arb._interrupt_count == 0)

# ---------------------------------------------------------------- 用例8(降级)
# 喷人冷却内再次达到阈值 -> 降级为不满，不重复喷人
for key in ("autostart", "volume", "pomodoro"):
    pet.talking = True
    assert arb.begin_feature(key)
    arb.finish_feature(key, lambda: None)
    wait_s(0.35)
check(
    "用例8: 喷人冷却内降级为不满(不重复喷人)",
    dm.categories()[-3:] == ["spam_interrupted_reply", "spam_interrupted_reply", "spam_interrupted_reply"],
    str(dm.categories()),
)
wait_s(2.1)  # 越过喷人冷却(2s)
pet.talking = True
assert arb.begin_feature("stats")
arb.finish_feature("stats", lambda: None)
wait_s(0.35)
check("用例8: 冷却结束后达到阈值可再次喷人", dm.categories()[-1] == "spam_rant_interrupt", str(dm.categories()[-1]))

# ---------------------------------------------------------------- 用例6
# 5秒(此处1秒)内连点小花本体>=4次 -> 播放"厌倦"
arb, dm, pet = make_arbiter()
results = [arb.note_body_click() for _ in range(5)]
check("用例6: 前3次不算厌倦，第4/5次判定厌倦", results == [False, False, False, True, True], str(results))
check("用例6: 厌倦台词已播放", "spam_annoyed_click" in dm.categories())

# ---------------------------------------------------------------- 用例7
# 音量连续调整(continuous) -> 期间不说话，停手后只说最后一次(可能是正常台词，
# 也可能因为连续调整次数够多而已经进入某个升级阶段 -- 两种情况都只应该恰好
# 说一句，不应该期间就说话或者说好几句)
arb, dm, pet = make_arbiter()
calls = []
n_before = len(dm.categories())
for i in range(5):
    arb.finish_feature("volume", lambda i=i: calls.append(i))
    wait_s(0.1)  # 每次都在防抖窗口内，持续把计时器往后推
check("用例7: 连续调整期间不说话", calls == [] and len(dm.categories()) == n_before)
wait_s(0.4)
total_spoken = len(calls) + (len(dm.categories()) - n_before)
check("用例7: 停手后只说一句(正常或已升级)", total_spoken == 1, f"calls={calls} dm={dm.categories()[n_before:]}")

# ---------------------------------------------------------- 提醒弹窗抑制(用例10核心)
arb, dm, pet = make_arbiter()
pet.reminder_popup_active = True
assert arb.begin_feature("pin")
arb.finish_feature("pin", lambda: calls.append("should-not-fire"))
n_before = len(dm.categories())
wait_s(0.4)
check("追讨弹窗期间：功能触发但台词被抑制", len(dm.categories()) == n_before, str(dm.categories()))

# ------------------------------------------------------------- 退出锁定拦截
arb, dm, pet = make_arbiter()
assert arb.begin_feature("pin")
arb.finish_feature("pin", lambda: calls.append("should-not-fire-either"))
pet.exiting = True
n_before = len(dm.categories())
wait_s(0.4)
check("退出锁定期间：待发的功能台词被拦截", len(dm.categories()) == n_before, str(dm.categories()))

# --------------------------------------------------- 退出被打断台词池(用例9基础)
# 整个退出过程只允许触发一次"被打断"反应(随机挑一句)，不是按次数升级的梯度，
# 所以 pick_exit_interrupt_segments 现在不带参数，多次调用只是各自独立随机挑一句。
arb, dm, pet = make_arbiter()
for _ in range(5):
    segs = arb.pick_exit_interrupt_segments()
    check("退出打断：台词池非空", bool(segs), str(segs))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败:")
    for name, detail in FAILURES:
        print(f"  FAIL {name}: {detail}")
    sys.exit(1)
print("全部验收用例通过")
sys.exit(0)
