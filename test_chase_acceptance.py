# -*- coding: utf-8 -*-
"""Acceptance tests for the ReminderChaseController state machine,
covering the 验收用例 list (test-mode compressed timing, offscreen, no
real GUI windows). Drives the controller directly with a fake dialogue
manager / fake stats; popup button clicks and timeouts are simulated by
calling on_popup_outcome() the way PetWindow does.

Run:  QT_QPA_PLATFORM=offscreen FLOWEYPET_REMINDER_LOG=<tmp> python test_chase_acceptance.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "FLOWEYPET_REMINDER_LOG", str(Path(__file__).resolve().parent / "test_acceptance_reminder.log")
)
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

app = QCoreApplication(sys.argv)

import reminder_chase  # noqa: E402
from reminder_chase import ReminderChaseController  # noqa: E402

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
        self.spoken = []  # (monotonic, category)

    def speak(self, category, segments, priority=40, bypass_interval=True):
        assert segments, f"empty segments for {category}"
        self.spoken.append((time.monotonic(), category))

    def categories(self):
        return [c for _, c in self.spoken]


class FakeStats:
    def __init__(self):
        self.rounds = []  # (kind, outcome)

    def record_reminder_round(self, kind, outcome):
        self.rounds.append((kind, outcome))


def make_ctl(test_mode=True, stages=None, hold=None):
    config = {
        "reminder_test_mode": test_mode,
        "reminder_pursuit_stages_minutes": stages or [5, 10, 20, 30],
        "reminder_pursuit_hold_seconds": 60 if hold is None else hold,
        "reminder_test_mode_hold_seconds": 3,
    }
    dm, st = FakeDM(), FakeStats()
    return ReminderChaseController(config, dm, st), dm, st


# ---------------------------------------------------------------- 用例1
# 到点弹窗 -> 停留期内点[确定] -> 本轮结束、无追讨、记 responded
ctl, dm, st = make_ctl()
ctl.start_round("water")
check("用例1: 首次弹窗类别", dm.categories() == ["chase_popup_first_water"], str(dm.categories()))
ctl.on_popup_outcome("confirm")
check("用例1: 确定后记为responded", st.rounds == [("water", "responded")], str(st.rounds))
check("用例1: 本轮结束(状态IDLE)", not ctl.is_round_active())
wait_s(7)  # 越过第5“分钟”(压缩为5秒)点位
check("用例1: 无追讨弹窗", dm.categories() == ["chase_popup_first_water", "chase_reply_water"], str(dm.categories()))

# ---------------------------------------------------------------- 用例2+5
# 不操作(全超时) -> 第5/10/20/30秒依次弹追讨 -> EXPIRED 记 unresponded
ctl, dm, st = make_ctl()
t0 = time.monotonic()
ctl.start_round("meal")
pop_times = []
for stage in range(5):  # 首次 + 4级追讨
    ctl.on_popup_outcome("timeout")
    if stage < 4:
        n_before = len([c for c in dm.categories() if c.startswith("chase_popup")])
        while len([c for c in dm.categories() if c.startswith("chase_popup")]) == n_before:
            wait_s(0.2)
            if time.monotonic() - t0 > 40:
                break
        pop_times.append(time.monotonic() - t0)
targets = [5, 10, 20, 30]
ok_times = len(pop_times) == 4 and all(abs(p - t) < 1.5 for p, t in zip(pop_times, targets))
check("用例2: 追讨在第5/10/20/30秒依次弹出", ok_times, f"实测 {[f'{p:.1f}' for p in pop_times]}")
popups = [c for c in dm.categories() if c.startswith("chase_popup")]
check(
    "用例2: 弹窗类别 首次x1 + 追讨x4",
    popups == ["chase_popup_first_meal"] + ["chase_popup_stage_meal"] * 4,
    str(popups),
)
check("用例5: 走完30分钟记 unresponded", st.rounds == [("meal", "unresponded")], str(st.rounds))
check("用例5: 本轮终止(状态IDLE)", not ctl.is_round_active())

# ---------------------------------------------------------------- 用例3
# 追讨第2级点[确定] -> RESPONDED, 后续追讨全部取消
ctl, dm, st = make_ctl()
t0 = time.monotonic()
ctl.start_round("water")
ctl.on_popup_outcome("timeout")  # 首次超时 -> 排第1级(5s)
wait_s(6)
ctl.on_popup_outcome("timeout")  # 第1级超时 -> 排第2级(10s)
wait_s(5)
popups = [c for c in dm.categories() if c.startswith("chase_popup")]
check("用例3: 已到第2级弹窗", len(popups) == 3, str(popups))
ctl.on_popup_outcome("confirm")
check("用例3: 第2级点确定记responded", st.rounds == [("water", "responded")], str(st.rounds))
n_popups = len([c for c in dm.categories() if c.startswith("chase_popup")])
wait_s(22)  # 越过第20/30“分钟”点位
check(
    "用例3: 后续追讨已全部取消",
    len([c for c in dm.categories() if c.startswith("chase_popup")]) == n_popups,
)

# ---------------------------------------------------------------- 用例4
# 追讨弹窗点[知道了] -> DISMISSED 记已敷衍(不计responded), 立即终止
ctl, dm, st = make_ctl()
ctl.start_round("meal")
ctl.on_popup_outcome("timeout")
wait_s(6)  # 第1级弹出
ctl.on_popup_outcome("dismiss")
check("用例4: 知道了记为dismissed", st.rounds == [("meal", "dismissed")], str(st.rounds))
check("用例4: 本轮立即终止", not ctl.is_round_active())
reply = [c for c in dm.categories() if c == "chase_reply_meal"]
check("用例4: 播放敷衍专属回应", len(reply) >= 2)  # 首次超时台词 + 敷衍台词
n_popups = len([c for c in dm.categories() if c.startswith("chase_popup")])
wait_s(6)
check("用例4: 无残留追讨弹窗", len([c for c in dm.categories() if c.startswith("chase_popup")]) == n_popups)

# ---------------------------------------------------------------- 用例6
# 追讨等待期间主动对话被抑制(is_round_active=True), 结束后恢复
ctl, dm, st = make_ctl()
ctl.start_round("water")
check("用例6: 弹窗中 抑制生效", ctl.is_round_active())
ctl.on_popup_outcome("timeout")
check("用例6: CHASING等待期 抑制生效", ctl.is_round_active())
wait_s(6)
ctl.on_popup_outcome("confirm")
check("用例6: 本轮结束后 抑制解除", not ctl.is_round_active())

# ---------------------------------------------------------------- 用例7
# 上一轮追讨中新一轮到点 -> 不作废旧轮, 排队等待; 旧轮追讨照常继续弹出;
# 旧轮结束后新轮才正式开始; 同一 kind 重复到点不会排队两次
ctl, dm, st = make_ctl()
ctl.start_round("water")
ctl.on_popup_outcome("timeout")  # water 进入 CHASING, 第1级5s后
ctl.start_round("meal")  # 新一轮到点 -> 应排队, 不打断 water
ctl.start_round("meal")  # 同一 kind 重复到点 -> 不应排队两次
check("用例7: 旧轮未被作废(仍在推进)", ("water", "unresponded") not in st.rounds, str(st.rounds))
check("用例7: 新轮尚未弹窗(排队中)", "chase_popup_first_meal" not in dm.categories(), str(dm.categories()))
wait_s(6)  # 越过water第5"分钟"点位 -> 追讨第1级应正常弹出(证明旧轮定时器没被打断)
check(
    "用例7: 旧轮追讨照常继续(第1级弹出)",
    dm.categories().count("chase_popup_stage_water") == 1,
    str(dm.categories()),
)
ctl.on_popup_outcome("confirm")  # 旧轮(water)结束
check("用例7: 旧轮正常结束(非作废)", ("water", "responded") in st.rounds, str(st.rounds))
check(
    "用例7: 旧轮结束后新轮(meal)立即开始",
    dm.categories()[-1] == "chase_popup_first_meal",
    str(dm.categories()[-3:]),
)
check("用例7: 排队的meal没有因为重复到点而排两次", dm.categories().count("chase_popup_first_meal") == 1)
ctl.on_popup_outcome("confirm")  # 收尾, 回到 IDLE

# ---------------------------------------------------------------- 用例8
# 睡觉提醒: 只弹一次, 超时/确定/等等都直接结束, 永不追讨
for outcome, note in (("confirm", "确定"), ("wait", "等等"), ("timeout", "超时")):
    ctl, dm, st = make_ctl()
    ctl.start_round("sleep")
    check(f"用例8: 睡觉弹窗类别({note})", dm.categories() == ["chase_popup_sleep"], str(dm.categories()))
    ctl.on_popup_outcome(outcome)
    check(f"用例8: 睡觉{note}后直接结束", not ctl.is_round_active())
    wait_s(6)
    popups = [c for c in dm.categories() if c.startswith("chase_popup")]
    check(f"用例8: 睡觉{note}后无追讨", popups == ["chase_popup_sleep"], str(popups))

# ---------------------------------------------------------------- 失效保护
# 弹窗被外部收起(不回报结果) -> failsafe 按超时流转, 轮次不悬死
ctl, dm, st = make_ctl(hold=60)  # test_mode hold=3s -> failsafe 3+8+30=41s
ctl.start_round("water")
print("……等待失效保护触发(约41秒)……")
wait_s(44)
# 压缩计时下失效保护触发时所有追讨点位均已过期，所以下一级弹窗会立刻
# 弹出、状态回到 SHOWING —— 判定标准是"链条流转了"（弹出了新一级追讨
# 弹窗），而不是停在某个特定状态。
popups_after = [c for c in dm.categories() if c.startswith("chase_popup")]
check("失效保护: 未收到结果也自动流转(已弹出下一级)", len(popups_after) >= 2, str(popups_after))
check("失效保护: 本轮仍在推进未悬死", ctl.is_round_active())

# ---------------------------------------------------------------- 用例9
# 关闭test_mode 真实时间抽查: 首次->第1级(用1分钟点位, 真实60秒)
ctl, dm, st = make_ctl(test_mode=False, stages=[1])
t0 = time.monotonic()
ctl.start_round("water")
ctl.on_popup_outcome("timeout")
print("……真实时间等待第1级(约60秒)……")
while len([c for c in dm.categories() if c.startswith("chase_popup")]) < 2:
    wait_s(0.5)
    if time.monotonic() - t0 > 80:
        break
elapsed = time.monotonic() - t0
check("用例9: 真实时间下第1级如期弹出(~60s)", 58 <= elapsed <= 75, f"{elapsed:.1f}s")
ctl.on_popup_outcome("confirm")

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败:")
    for name, detail in FAILURES:
        print(f"  FAIL {name}: {detail}")
    sys.exit(1)
print("全部验收用例通过")
sys.exit(0)
