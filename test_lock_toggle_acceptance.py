# -*- coding: utf-8 -*-
"""Acceptance tests for 锁定(lock) toggle's dedicated 3-tier escalation
(正常/无语/不耐烦/爆发) added to DialogueArbiter -- see dialogue_arbiter.py's
_wrap_lock_provider. Offscreen, drives the real DialogueArbiter directly
against the real dialogues_spam.json content (lock_toggle_tired/annoyed/
rant pools), same FakeDM/FakePet pattern as
test_dialogue_arbiter_acceptance.py. PetWindow._toggle_lock's own gating
(tantrum-blocks-toggle, begin_feature wiring) is covered separately in
test_lock_and_layout_acceptance.py; this file is scoped to the arbiter's
tier-selection logic itself.

Run:  QT_QPA_PLATFORM=offscreen python test_lock_toggle_acceptance.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

app = QCoreApplication(sys.argv)

from dialogue_arbiter import DialogueArbiter  # noqa: E402

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
        self.preempted = []  # (category, segments)
        self.flavors = []  # normal-tier lock_on/lock_off calls

    def preempt(self, category, segments):
        assert segments, f"empty segments for {category}"
        self.preempted.append((category, segments))
        return True

    def trigger_menu_flavor(self, category):
        self.flavors.append(category)

    def categories(self):
        return [c for c, _ in self.preempted]


class FakePet:
    def __init__(self):
        self.reminder_popup_active = False
        self.exiting = False
        self.talking = False

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
            "feature_debounce_seconds": 0.01,
            "feature_escalation_delay_steps": [[0.05, 0.06]],
            "feature_reset_seconds": 1,
            "feature_silence_seconds": 1,
            "body_click_window_seconds": 1,
            "body_click_threshold": 4,
            "interrupt_rant_threshold": 3,
            "interrupt_rant_cooldown_seconds": 1.5,
            "lock_toggle_thresholds": [3, 6, 9],
            "lock_toggle_tantrum_seconds": 0.5,
            **overrides,
        }
    }
    dm = FakeDM()
    pet = FakePet()
    return DialogueArbiter(cfg, dm, pet), dm, pet


def toggle(arb, checked):
    """Mirrors the call shape PetWindow._toggle_lock uses against the
    arbiter (minus the tantrum/begin_feature gate itself, which is
    PetWindow's own responsibility, tested elsewhere)."""
    assert arb.begin_feature("lock")
    arb.finish_feature("lock", lambda: arb.dm.trigger_menu_flavor("lock_on" if checked else "lock_off"))


def settle(arb):
    """Simulate the toggle's reaction bubble having finished playing before
    the next toggle arrives, same convention test_dialogue_arbiter_
    acceptance.py's 用例3 loop uses -- otherwise a second toggle while
    _active_key is still "lock" would fall into finish_feature's
    still-playing "just queue" branch instead of resolving immediately."""
    wait_s(0.03)
    arb.on_bubble_finished()


# ---- case 1: slow toggles (further apart than the reset interval) always -
# ---- stay normal, count never accumulates ---------------------------------
arb, dm, pet = make_arbiter()
for i in range(5):
    toggle(arb, i % 2 == 0)
    settle(arb)
    wait_s(1.1)  # exceed feature_reset_seconds=1 between each toggle
check(
    "case1: 慢速切换全部走正常lock_on/lock_off，不升级",
    dm.flavors == ["lock_on", "lock_off", "lock_on", "lock_off", "lock_on"] and dm.categories() == [],
    f"flavors={dm.flavors} preempted={dm.categories()}",
)
check("case1: 计数不累积(每次间隔超时都会重置)", arb._lock["count"] == 1, arb._lock["count"])

# ---- case 2: rapid consecutive toggles: 1-3 normal, 4-6 tired, 7-9 -------
# ---- annoyed ---------------------------------------------------------------
arb, dm, pet = make_arbiter()
for i in range(9):
    toggle(arb, i % 2 == 0)
    settle(arb)
check("case2: 前3次仍是正常lock_on/lock_off", dm.flavors == ["lock_on", "lock_off", "lock_on"], dm.flavors)
tired_cats = [c for c in dm.categories() if c == "spam_lock_tired"]
annoyed_cats = [c for c in dm.categories() if c == "spam_lock_annoyed"]
check("case2: 第4~6次播放了3次无语(tired)，不再有新的正常台词", len(tired_cats) == 3 and len(dm.flavors) == 3, dm.categories())
check("case2: 第7~9次播放了3次不耐烦(annoyed)", len(annoyed_cats) == 3, dm.categories())

# ---- case 3: 10th toggle -> rant (fixed 1 line), count resets, optional --
# ---- tantrum window opens and always recovers ------------------------------
toggle(arb, False)  # 10th toggle overall in this arbiter's session
settle(arb)
check("case3: 第10次播放爆发台词", dm.categories()[-1] == "spam_lock_rant", dm.categories())
check("case3: 爆发后计数清零", arb._lock["count"] == 0)
check("case3: 爆发后进入闹脾气冷区", arb.is_lock_tantrum_active())
wait_s(0.6)  # exceed lock_toggle_tantrum_seconds=0.5
check("case3: 冷区结束后自动恢复，绝不会永久锁死", not arb.is_lock_tantrum_active())

# ---- case 4: within the rant cooldown, reaching the 10th toggle again ----
# ---- stays at annoyed, does not repeat the outburst -----------------------
arb2, dm2, pet2 = make_arbiter()
for i in range(10):
    toggle(arb2, i % 2 == 0)
    settle(arb2)
check("case4 前置: 第一轮第10次先正常爆发一次", dm2.categories()[-1] == "spam_lock_rant", dm2.categories())
rant_count_before = len([c for c in dm2.categories() if c == "spam_lock_rant"])
for i in range(10):
    toggle(arb2, i % 2 == 0)
    settle(arb2)
rant_count_after = len([c for c in dm2.categories() if c == "spam_lock_rant"])
check(
    "case4: 5分钟(此处1.5s)冷却内再刷到第10次不重复爆发",
    rant_count_after == rant_count_before,
    f"before={rant_count_before} after={rant_count_after} cats={dm2.categories()}",
)
check("case4: 冷却内第二轮第10次降级为annoyed", dm2.categories()[-1] == "spam_lock_annoyed", dm2.categories()[-1])

# ---- case 5: the toggle itself is decoupled from dialogue tier -- every ---
# ---- resolved tier still carries a real functional toggle upstream (this
# ---- file only tests the arbiter's tier decision; PetWindow._toggle_lock
# ---- always applies self._locked/config["locked"] before ever consulting
# ---- the arbiter, verified in test_lock_and_layout_acceptance.py) --------
check(
    "case5: 每一级(tired/annoyed/rant)都真实产生了一次台词决定，功能生效与台词档位互不阻塞",
    len(tired_cats) + len(annoyed_cats) + 1 == 7,
    (tired_cats, annoyed_cats),
)

# ---- bad/missing pool degrades to normal, never crashes -------------------
arb3, dm3, pet3 = make_arbiter()
arb3._pools["lock_toggle_tired"] = []  # simulate a missing/empty pool
for i in range(4):
    toggle(arb3, i % 2 == 0)
    settle(arb3)
check(
    "台词池缺失兜底: 第4次(本应是tired)台词池为空时降级为正常lock_on/lock_off，不崩溃不静默",
    dm3.flavors == ["lock_on", "lock_off", "lock_on", "lock_off"],
    dm3.flavors,
)

# ---- config threshold override is honored, not hardcoded ------------------
arb4, dm4, pet4 = make_arbiter(lock_toggle_thresholds=[1, 2, 3])
for i in range(4):
    toggle(arb4, i % 2 == 0)
    settle(arb4)
check(
    "阈值可配置: [1,2,3]档位下第4次(t3+1=4)即触发爆发，而不是固定的第10次",
    dm4.categories()[-1] == "spam_lock_rant",
    dm4.categories(),
)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for name, detail in FAILURES:
        print(f"  - {name}: {detail}")
    sys.exit(1)
print("All checks passed.")
