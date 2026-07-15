# -*- coding: utf-8 -*-
"""Unit tests for game_egg_display.py's pure logic pieces that don't
require a real fullscreen game window: TopmostGuard's apply/poll/restore/
safety-net state machine (win32 SetWindowPos is monkeypatched out here --
there's no meaningful way to assert "did the OS actually reorder these
windows" without a real on-screen game, which is exactly the one thing the
user's own acceptance checklist reserves for a manual pass) and
TopmostEggPresenter's occlusion/verify/downgrade wiring (its
occlusion_check/point_check are constructor-injected, so no real win32
calls happen here either).

Run: QT_QPA_PLATFORM=offscreen python test_game_egg_display.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QCoreApplication  # noqa: E402

app = QCoreApplication(sys.argv)

import game_egg_display  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def pump_until(predicate, timeout_s=2.0, step_s=0.01):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(step_s)
    return False


class FakeWidget:
    _next_id = 1000

    def __init__(self):
        FakeWidget._next_id += 1
        self._id = FakeWidget._next_id

    def winId(self):
        return self._id


class ZOrderSpy:
    """Monkeypatch target for game_egg_display._set_topmost -- records
    every (hwnd, topmost) call instead of touching a real window."""

    def __init__(self):
        self.calls = []

    def __call__(self, hwnd, topmost):
        self.calls.append((hwnd, topmost))


# ---- TopmostGuard: applies topmost immediately, restores on hidden ------
spy = ZOrderSpy()
_orig_set_topmost = game_egg_display._set_topmost
game_egg_display._set_topmost = spy
try:
    guard = game_egg_display.TopmostGuard(poll_interval_ms=20, max_duration_ms=2000)
    w1, w2 = FakeWidget(), FakeWidget()
    hidden = {"flag": False}
    restored = {"flag": False}
    guard.apply(
        [(w1, True), (w2, False)],
        until_hidden_check=lambda: hidden["flag"],
        on_restored=lambda: restored.__setitem__("flag", True),
    )
    check(
        "guard: applies TOPMOST to every widget immediately",
        spy.calls == [(w1._id, True), (w2._id, True)],
        spy.calls,
    )
    check("guard: is_active() True while waiting", guard.is_active())
    spy.calls.clear()
    hidden["flag"] = True
    ok = pump_until(lambda: restored["flag"])
    check("guard: restores once until_hidden_check() goes True", ok)
    check(
        "guard: restores each widget to its own remembered target (not just NOTOPMOST for all)",
        spy.calls == [(w1._id, True), (w2._id, False)],
        spy.calls,
    )
    check("guard: is_active() False after restoring", not guard.is_active())

    # ---- safety-net: restores even if until_hidden_check never fires -----
    spy.calls.clear()
    guard2 = game_egg_display.TopmostGuard(poll_interval_ms=20, max_duration_ms=80)
    w3 = FakeWidget()
    restored2 = {"flag": False}
    guard2.apply(
        [(w3, False)],
        until_hidden_check=lambda: False,
        on_restored=lambda: restored2.__setitem__("flag", True),
    )
    ok2 = pump_until(lambda: restored2["flag"], timeout_s=2.0)
    check("guard: safety-net max_duration_ms restores even if never 'hidden'", ok2)
finally:
    game_egg_display._set_topmost = _orig_set_topmost


# ---- TopmostEggPresenter: not occluded -> shows immediately, guard/point- --
# ---- check never consulted -------------------------------------------------
class FakePetForPresenter:
    def __init__(self):
        self.pinned = True

    def is_pinned_on_top(self):
        return self.pinned

    def winId(self):
        return 42

    def geometry(self):
        class _G:
            def center(self):
                class _P:
                    def x(self_inner):
                        return 100

                    def y(self_inner):
                        return 100

                return _P()

        return _G()


class FakeDialogueBoxForPresenter:
    def __init__(self):
        self.pinned = False
        self.visible = True

    def is_pinned(self):
        return self.pinned

    def isVisible(self):
        return self.visible

    def winId(self):
        return 43


class RecordingGuard:
    def __init__(self):
        self.apply_calls = []

    def apply(self, widgets_with_restore, until_hidden_check, on_restored=None):
        self.apply_calls.append(widgets_with_restore)


logs = []
pet_p = FakePetForPresenter()
box_p = FakeDialogueBoxForPresenter()

guard_p = RecordingGuard()
presenter_not_occluded = game_egg_display.TopmostEggPresenter(
    guard=guard_p, occlusion_check=lambda name: False, point_check=lambda x, y, s: True
)
shown = {"flag": False}
presenter_not_occluded.present(
    key="ut",
    pet=pet_p,
    dialogue_box=box_p,
    process_name_lower="undertale.exe",
    show_now=lambda: shown.__setitem__("flag", True),
    on_downgrade=lambda: FAILURES.append(("presenter: on_downgrade must not fire when not occluded", "")),
    log=logs.append,
)
check("presenter: not occluded -> show_now called directly", shown["flag"])
check("presenter: not occluded -> guard never applied", guard_p.apply_calls == [])

# ---- occluded, and stays covered even after topmost -> downgrades -------
guard_p2 = RecordingGuard()
presenter_downgrade = game_egg_display.TopmostEggPresenter(
    guard=guard_p2, occlusion_check=lambda name: True, point_check=lambda x, y, s: False, verify_delay_ms=10
)
shown2 = {"flag": False}
downgraded = {"flag": False}
presenter_downgrade.present(
    key="ut",
    pet=pet_p,
    dialogue_box=box_p,
    process_name_lower="undertale.exe",
    show_now=lambda: shown2.__setitem__("flag", True),
    on_downgrade=lambda: downgraded.__setitem__("flag", True),
    log=logs.append,
)
check(
    "presenter: occluded -> guard applied with each widget's correct restore target",
    guard_p2.apply_calls == [[(pet_p, True), (box_p, False)]],
)
check("presenter: occluded -> show_now still called (bubble shows, topmost applied)", shown2["flag"])
ok3 = pump_until(lambda: downgraded["flag"], timeout_s=1.0)
check("presenter: still covered after verify delay -> on_downgrade fires", ok3)

# ---- occluded, but topmost actually won -> no downgrade ------------------
guard_p3 = RecordingGuard()
presenter_ok = game_egg_display.TopmostEggPresenter(
    guard=guard_p3, occlusion_check=lambda name: True, point_check=lambda x, y, s: True, verify_delay_ms=10
)
downgraded2 = {"flag": False}
presenter_ok.present(
    key="ut",
    pet=pet_p,
    dialogue_box=box_p,
    process_name_lower="undertale.exe",
    show_now=lambda: None,
    on_downgrade=lambda: downgraded2.__setitem__("flag", True),
    log=logs.append,
)
time.sleep(0.05)
app.processEvents()
check("presenter: topmost actually won -> no downgrade", not downgraded2["flag"])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for name, detail in FAILURES:
        print(f"  - {name}: {detail}")
    sys.exit(1)
else:
    print("All checks passed.")
