# -*- coding: utf-8 -*-
"""Acceptance tests for anticheat.py (AutoClickerDetector + SaveTamperGuard),
covering the verification list from the implementation plan: CPS 6-stage
escalation/reset/repeat-episode-skips-to-3/cooldown, tamper classification
for every category and boundary, the {real_clicks}/{tampered_clicks}
？？？？ display cutoff, tamper_detect_count persistence across simulated
restarts, permanent lock at >5, milestone/mod_f exemption from both the
counter and the lock, and the once-per-run limit on the locked "……"/"……滚"
reply. Pure Python, no Qt/GUI needed -- neither class touches PyQt6.

Run:  python test_anticheat_acceptance.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from anticheat import (  # noqa: E402
    AutoClickerDetector,
    SaveTamperGuard,
    digit_count,
    format_number_for_display,
)

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


# ------------------------------------------------------------- fake clock
class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def click_burst(detector, clock, count, dt):
    """Fires `count` clicks spaced `dt` seconds apart, returning the list
    of non-None stages emitted along the way."""
    stages = []
    for _ in range(count):
        clock.advance(dt)
        s = detector.on_click()
        if s is not None:
            stages.append(s)
    return stages


# =============================================================== AutoClicker

# ------------------------------------------------------------------ 用例1
# 正常人类速度(1-8次/秒)不会进入任何阶段
clock = FakeClock()
det = AutoClickerDetector(now_fn=clock)
stages = click_burst(det, clock, 30, 0.2)  # 5次/秒，持续6秒
check("正常速度：不触发任何阶段", stages == [], str(stages))
check("正常速度：不进入冷却", not det.is_cooling_down())

# ------------------------------------------------------------------ 用例2
# 20次/秒持续到30秒+ -> 依次触发阶段1~6，且阶段6后进入冷却
clock = FakeClock()
det = AutoClickerDetector(now_fn=clock)
stages = click_burst(det, clock, int(32 / 0.05), 0.05)  # 20次/秒，32秒
check(
    "连点器持续：阶段1~6按顺序各触发一次",
    stages == [1, 2, 3, 4, 5, 6],
    str(stages),
)
check("阶段6后进入30秒冷却", det.is_cooling_down())
clock.advance(10)
check("冷却期(点击)不响应，返回None", det.on_click() is None)
check("冷却10秒后仍在冷却", det.is_cooling_down())

# ------------------------------------------------------------------ 用例3
# 冷却结束后再次连点 -> 直接从阶段3开始(跳过1/2)
clock.advance(21)  # 累计冷却31秒，超过30秒冷却窗口
check("冷却30秒后结束", not det.is_cooling_down())
stages = click_burst(det, clock, int(32 / 0.05), 0.05)
check(
    "冷却结束后第二次发作：从阶段3开始，跳过1/2",
    stages == [3, 4, 5, 6],
    str(stages),
)

# ------------------------------------------------------------------ 用例4
# 速率掉回正常并维持 >= RECOVERY_GRACE_SECONDS -> 序列清零，下次重新从阶段1开始
clock = FakeClock()
det = AutoClickerDetector(now_fn=clock)
# episode_start 从"速率首次达到可疑门槛"那一刻算起(约0.5s后)，不是从第0秒算
# 起，所以要多打一点余量才能确保elapsed(从episode_start算)真的越过10秒。
stages = click_burst(det, clock, 260, 0.05)  # 20次/秒，13秒 -> 应该到阶段3
check("清零测试前置：已进入阶段3", 3 in stages, str(stages))
check("清零测试前置：还没到过阶段6", not det._has_completed_once)
# 停顿超过 RECOVERY_GRACE_SECONDS(2s)，速率跌回0
clock.advance(3.0)
det.on_click()  # 单次点击不足以判定为连点器，且触发清零判定
stages_after = click_burst(det, clock, 100, 0.05)  # 重新20次/秒，留足够余量越过新一轮的0.5s探测延迟+3s门槛
check(
    "停顿后清零：重新发作从阶段1开始(不是继续阶段3+)",
    1 in stages_after,
    str(stages_after),
)

# =============================================================== TamperGuard

def make_guard():
    shadow = {}

    def read():
        return shadow.get("v")

    def write(v):
        shadow["v"] = v

    return SaveTamperGuard(read, write), shadow


# ------------------------------------------------------------------ 用例5
# 全新安装(从未建立影子值) -> 不判定为篡改，只是建立基准
guard, shadow = make_guard()
cfg = {"total_clicks": 42}
verdict = guard.check(cfg)
check("全新安装：不判定篡改", verdict is None)
check("全新安装：影子值已建立", shadow["v"] == 42)

# ------------------------------------------------------------------ 用例6
# 数值一致 -> 不判定篡改
verdict = guard.check(cfg)
check("数值一致：不判定篡改", verdict is None)

# ------------------------------------------------------------------ 用例7
# 差值分类边界：<1000 不算篡改，1000~9999 -> mod_a，>=10000 -> mod_b，变小 -> mod_d
guard, shadow = make_guard()
shadow["v"] = 100
cfg = {"total_clicks": 100 + 999}
check("差值999：不判定篡改(低于门槛)", guard.check(cfg) is None)
cfg = {"total_clicks": 100 + 1000}
v = guard.check(cfg)
check("差值1000：mod_a", v is not None and v.kind == "mod_a", v and v.kind)
guard, shadow = make_guard()
shadow["v"] = 100
cfg = {"total_clicks": 100 + 10000}
v = guard.check(cfg)
check("差值10000：mod_b", v.kind == "mod_b", v.kind)
guard, shadow = make_guard()
shadow["v"] = 5000
cfg = {"total_clicks": 50}  # 50不是里程碑数字，避免和100/500/...撞车
v = guard.check(cfg)
check("数值变小：mod_d", v.kind == "mod_d", v.kind)

# ------------------------------------------------------------------ 用例8
# 特殊整数(123456/666666/888888/999999) -> mod_c
guard, shadow = make_guard()
shadow["v"] = 10
cfg = {"total_clicks": 666666}
v = guard.check(cfg)
check("特殊整数666666：mod_c", v.kind == "mod_c", v.kind)

# ------------------------------------------------------------------ 用例9
# 归零 -> mod_f，exempt，不计入tamper_detect_count
guard, shadow = make_guard()
shadow["v"] = 500
cfg = {"total_clicks": 0, "tamper_detect_count": 0}
v = guard.check(cfg)
check("归零：mod_f", v.kind == "mod_f")
check("归零：exempt(彩蛋)", v.exempt is True)
check("归零：不计入tamper_detect_count", cfg.get("tamper_detect_count", 0) == 0)

# ------------------------------------------------------------------ 用例10
# 既有里程碑数字(如500/1000000) -> existing_milestone，exempt
guard, shadow = make_guard()
shadow["v"] = 10
cfg = {"total_clicks": 500, "tamper_detect_count": 0}
v = guard.check(cfg)
check("里程碑数字500：existing_milestone", v.kind == "existing_milestone")
check("里程碑数字：exempt", v.exempt is True)
check("里程碑数字：不计入tamper_detect_count", cfg.get("tamper_detect_count", 0) == 0)
guard, shadow = make_guard()
shadow["v"] = 10
cfg = {"total_clicks": 2_500_000, "tamper_detect_count": 0}
v = guard.check(cfg)
check("超过1000000：existing_milestone(作弊彩蛋)", v.kind == "existing_milestone")

# ------------------------------------------------------------------ 用例11
# 负数分类：-1精确匹配neg3；超大负数(>=8位)neg4；否则首次neg1/再次neg2
guard, shadow = make_guard()
shadow["v"] = 100
cfg = {"total_clicks": -1, "tamper_detect_count": 0}
v = guard.check(cfg)
check("恰好-1：neg3", v.kind == "neg3", v.kind)
guard, shadow = make_guard()
shadow["v"] = 100
cfg = {"total_clicks": -99999999, "tamper_detect_count": 0}
v = guard.check(cfg)
check("超大负数(8位)：neg4", v.kind == "neg4", v.kind)
guard, shadow = make_guard()
shadow["v"] = 100
cfg = {"total_clicks": -50, "tamper_detect_count": 0, "neg_tamper_count": 0}
v = guard.check(cfg)
check("首次一般负数：neg1", v.kind == "neg1", v.kind)
cfg["total_clicks"] = -60  # 同一个cfg(同一个"安装")上再来一次，模拟重启后又被改了一次
v2 = guard.check(cfg)
check("再次一般负数：neg2", v2.kind == "neg2", v2.kind)

# ------------------------------------------------------------------ 用例12
# 超长数字位数分档：10-12/13-15/16-20/20-29/30+
guard, shadow = make_guard()
shadow["v"] = 1
for digits, expected in ((11, "long1"), (14, "long2"), (18, "long3"), (25, "long4"), (35, "long5")):
    cfg = {"total_clicks": int("1" * digits), "tamper_detect_count": 0}
    v = guard.check(cfg)
    check(f"{digits}位数字：{expected}", v.kind == expected, f"got {v.kind}")

# ------------------------------------------------------------------ 用例13
# 负数+超长组合 -> combo1
guard, shadow = make_guard()
shadow["v"] = 1
cfg = {"total_clicks": -int("9" * 15), "tamper_detect_count": 0}
v = guard.check(cfg)
check("负数+超长：combo1", v.kind == "combo1", v.kind)

# ------------------------------------------------------------------ 用例14
# 反复篡改：第3次起(A/B/C/D类)改判mod_e；跨"重启"持久化(复用同一个cfg dict)
guard, shadow = make_guard()
shadow["v"] = 100
cfg = {"total_clicks": 200, "tamper_detect_count": 0}  # 差值100+1000=mod_a
kinds = []
for i in range(3):
    cfg["total_clicks"] = 100 + 1500  # 每次都是mod_a量级的差值
    v = guard.check(cfg)
    kinds.append(v.kind)
    guard.resolve(cfg, v)  # 纠正回真实值，模拟"关闭重开"前的落盘
check("反复篡改：第1/2次mod_a，第3次起改判mod_e", kinds == ["mod_a", "mod_a", "mod_e"], str(kinds))
check("反复篡改：计数已到3", cfg["tamper_detect_count"] == 3)

# 继续到第6次 -> tamper_locked变True且不会变回False
for i in range(3):
    cfg["total_clicks"] = 100 + 1500
    v = guard.check(cfg)
    guard.resolve(cfg, v)
check("超过5次：tamper_locked=True", cfg.get("tamper_locked") is True, str(cfg))
check("计数达到6", cfg["tamper_detect_count"] == 6)

# ------------------------------------------------------------------ 用例15
# resolve()无条件把total_clicks纠正回真实值
guard, shadow = make_guard()
shadow["v"] = 77
cfg = {"total_clicks": 999999999999999, "tamper_detect_count": 0}
v = guard.check(cfg)
guard.resolve(cfg, v)
check("resolve后total_clicks纠正回真实值", cfg["total_clicks"] == 77, cfg["total_clicks"])
check("resolve后影子值同步", shadow["v"] == 77)

# ------------------------------------------------------------------ 用例16
# locked_reply_allowed_this_run: 每次运行(guard实例)最多True一次
guard, shadow = make_guard()
check("locked_reply第一次允许", guard.locked_reply_allowed_this_run() is True)
check("locked_reply第二次不再允许(同一次运行)", guard.locked_reply_allowed_this_run() is False)
check("locked_reply第三次仍不允许", guard.locked_reply_allowed_this_run() is False)

# =============================================================== 数字显示

check("12位以内正常显示", format_number_for_display(123456789012) == "123456789012")
check("13位起显示？？？？", format_number_for_display(1234567890123) == "？？？？")
check("负数按绝对值位数判断", format_number_for_display(-1234567890123) == "？？？？")
check("digit_count对负数取绝对值", digit_count(-42) == 2)
check("digit_count可以处理巨长数字而不报错", digit_count(int("7" * 500)) == 500)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败:")
    for name, detail in FAILURES:
        print(f"  FAIL {name}: {detail}")
    sys.exit(1)
print("全部验收用例通过")
sys.exit(0)
