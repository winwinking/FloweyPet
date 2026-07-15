# -*- coding: utf-8 -*-
"""Acceptance tests for GameDetector (UNDERTALE/DELTARUNE launch/exit
egg), covering the spec's 验收用例 as far as they're pure detector logic
(cases 1, 2, 5, 6, 7, 8, plus the priority-suppression rule from case 3's
"reminder/chase outranks the egg" requirement). Case 9 (CPU usage) isn't
observable from a unit test and is left to the standing "watch Task
Manager" checklist; cases 3/4 (idle-chat suppression, click/right-click/
reminder staying unaffected) are wired in pet_window.py via
game_detector.is_any_game_running() and were verified by code review, not
exercised here since they need a real PetWindow (same tradeoff the
existing dialogue_arbiter/chase acceptance tests make).

Offscreen, no real GUI window, no real Windows process list touched --
process presence is injected via a swappable process_name_provider
callable, same fake-dm/fake-pet pattern as
test_dialogue_arbiter_acceptance.py/test_chase_acceptance.py.

Run:  QT_QPA_PLATFORM=offscreen python test_game_detector_acceptance.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QCoreApplication  # noqa: E402

app = QCoreApplication(sys.argv)

import game_detector as game_detector_module  # noqa: E402
from game_detector import GameDetector  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


class FakeDM:
    def __init__(self):
        self.preempted = []  # (category, segments)

    def preempt(self, category, segments):
        assert segments, f"empty segments for {category}"
        self.preempted.append((category, segments))
        return True

    def categories(self):
        return [c for c, _ in self.preempted]


class FakePet:
    def __init__(self):
        self.reminder_popup_active = False
        self.exiting = False
        self.visible = True
        self.talking = False
        self.force_end_calls = 0
        # Real PetWindow.dialogue_box has is_pinned()/isVisible()/winId()/
        # geometry() -- only ever dereferenced by the *real* TopmostEggPresenter
        # once it's decided the pet is actually occluded. Every test below
        # either keeps the real presenter's occlusion check naturally False
        # (no real fullscreen game in a CI/offscreen box) or swaps in a
        # FakePresenter that never touches this at all, so a bare placeholder
        # is enough -- it's never dereferenced.
        self.dialogue_box = object()

    def is_reminder_popup_active(self):
        return self.reminder_popup_active

    def is_exiting(self):
        return self.exiting

    def isVisible(self):
        return self.visible

    def force_end_bubble(self):
        self.force_end_calls += 1
        was = self.talking
        self.talking = False
        return was


class FakeStats:
    def __init__(self):
        self.data = {}
        self.trigger_count = 0

    def is_game_first_seen(self, key):
        return self.data.get(f"{key}_first_seen", False)

    def mark_game_first_seen(self, key):
        self.data[f"{key}_first_seen"] = True

    def record_game_egg_trigger(self):
        self.trigger_count += 1


class FakeProcessFeed:
    """Stands in for game_detector.running_process_names -- lets a test
    directly control which exe names 'exist' for a given poll, without
    touching the real Windows process list."""

    def __init__(self):
        self.names = set()

    def __call__(self):
        return set(self.names)


class FakeForegroundFeed:
    """Stands in for game_egg_display.foreground_process_name -- lets a
    test simulate the user tabbing in/out of the game without touching the
    real foreground window."""

    def __init__(self, name=None):
        self.name = name  # lowercase exe name, or None

    def __call__(self):
        return self.name


class FakePresenter:
    """Test double for game_egg_display.TopmostEggPresenter -- lets a test
    drive the 'topmost' display mode's show-now vs. downgrade-to-deferred
    branches deterministically, with no real win32 Z-order/occlusion calls
    involved. `downgrade_times` counts down: that many calls to present()
    invoke on_downgrade() instead of show_now() (simulating "still occluded
    even after topmost" for exactly that many attempts)."""

    def __init__(self, downgrade_times=0):
        self.downgrade_times = downgrade_times
        self.calls = []

    def present(self, key, pet, dialogue_box, process_name_lower, show_now, on_downgrade, log):
        self.calls.append(key)
        if self.downgrade_times > 0:
            self.downgrade_times -= 1
            on_downgrade()
        else:
            show_now()


def make_detector(config_overrides=None, presenter=None, foreground_feed=None):
    cfg = {
        "game_detect": {
            "enabled": False,  # tests drive _poll() manually, no real QTimer needed
            "poll_interval_seconds": 5,
            "process_names": {"ut": "UNDERTALE.exe", "dr": "DELTARUNE.exe"},
            "exit_confirm_polls": 2,
            "rare_launch_weight": 15,
        }
    }
    if config_overrides:
        cfg["game_detect"].update(config_overrides)
    dm = FakeDM()
    pet = FakePet()
    stats = FakeStats()
    feed = FakeProcessFeed()
    kwargs = {"foreground_name_provider": foreground_feed or FakeForegroundFeed()}
    if presenter is not None:
        kwargs["presenter"] = presenter
    gd = GameDetector(cfg, dm, pet, stats, process_name_provider=feed, **kwargs)
    return gd, dm, pet, stats, feed


# ---- case 1: first-ever launch -> the fixed "first" line, ut_first_seen --
# ---- persisted -------------------------------------------------------
gd, dm, pet, stats, feed = make_detector()
feed.names.add("undertale.exe")
gd._poll()
check("case1: launch triggers exactly one preempt", len(dm.preempted) == 1, dm.categories())
check("case1: first-ever launch uses the game_ut_first pool", dm.categories() == ["game_ut_first"], dm.categories())
check("case1: ut_first_seen persisted", stats.is_game_first_seen("ut"))
check("case1: trigger counted", stats.trigger_count == 1)
first_text = dm.preempted[0][1][0]["text"]
check("case1: first-line text matches the real pool content", first_text.startswith("这个图标"), first_text)

# ---- case 2: exit needs 2 consecutive misses; relaunch never replays -----
# ---- the first-line pool again --------------------------------------------
feed.names.discard("undertale.exe")
gd._poll()  # miss 1/2
check("case2: no exit yet after only 1 miss", dm.categories() == ["game_ut_first"], dm.categories())
gd._poll()  # miss 2/2 -> confirmed exit
check("case2: exit egg fires after 2 consecutive misses", dm.categories()[-1] == "game_ut_exit", dm.categories())
feed.names.add("undertale.exe")
gd._poll()
last_cat = dm.categories()[-1]
check(
    "case2: relaunch uses the normal/rare pool, never the first-line pool again",
    last_cat in ("game_ut_launch", "game_ut_launch_rare"),
    last_cat,
)

# ---- case 5: rapid start+stop still flows the state machine correctly ----
gd2, dm2, pet2, stats2, feed2 = make_detector()
feed2.names.add("deltarune.exe")
gd2._poll()  # launch (first-ever)
feed2.names.discard("deltarune.exe")
gd2._poll()  # miss 1/2
gd2._poll()  # miss 2/2 -> exit
check(
    "case5: quick start+stop -> exactly one launch + one exit, no dup/misorder",
    dm2.categories() == ["game_dr_first", "game_dr_exit"],
    dm2.categories(),
)
check("case5: state machine settles back to not_running", gd2._states["dr"] == "not_running")

# ---- case 6: hidden pet -> no bubble; unhiding doesn't retroactively -----
# ---- replay the missed line -----------------------------------------------
gd3, dm3, pet3, stats3, feed3 = make_detector()
pet3.visible = False
feed3.names.add("undertale.exe")
gd3._poll()
check("case6: hidden pet -> state still advances (first_seen persisted)", stats3.is_game_first_seen("ut"))
check("case6: hidden pet -> no bubble shown", dm3.categories() == [], dm3.categories())
pet3.visible = True
check("case6: unhiding afterward doesn't replay the missed line", dm3.categories() == [], dm3.categories())

# ---- case 7: one malformed entry in a pool is skipped, doesn't crash -----
bad_pool_file = Path(tempfile.gettempdir()) / "dialogues_game_bad_test.json"
bad_pool_file.write_text(
    json.dumps(
        {
            "game_ut_launch": [
                {"lines": ["ok line"], "faces": [["smirk"]]},
                {"lines": ["broken entry, faces too short"], "faces": []},
            ]
        },
        ensure_ascii=False,
    ),
    encoding="utf-8",
)
_orig_path = game_detector_module.DIALOGUE_GAME_PATH
game_detector_module.DIALOGUE_GAME_PATH = bad_pool_file
try:
    gd4, dm4, pet4, stats4, feed4 = make_detector()
    check(
        "case7: bad entry skipped, good entry survives, no crash",
        gd4._pools.get("game_ut_launch") == [{"lines": ["ok line"], "faces": [["smirk"]]}],
        gd4._pools.get("game_ut_launch"),
    )
finally:
    game_detector_module.DIALOGUE_GAME_PATH = _orig_path
bad_pool_file.unlink(missing_ok=True)

# ---- case 8: empty/missing pool at a trigger point silently skips -------
empty_pool_file = Path(tempfile.gettempdir()) / "dialogues_game_empty_test.json"
empty_pool_file.write_text(json.dumps({}), encoding="utf-8")
game_detector_module.DIALOGUE_GAME_PATH = empty_pool_file
try:
    gd5, dm5, pet5, stats5, feed5 = make_detector()
    feed5.names.add("undertale.exe")
    gd5._poll()
    check("case8: empty pool -> silent skip, no crash", dm5.categories() == [])
    check("case8: state machine still advances despite the empty pool", gd5._states["ut"] == "running")
finally:
    game_detector_module.DIALOGUE_GAME_PATH = _orig_path
empty_pool_file.unlink(missing_ok=True)

# ---- priority: a reminder/chase popup active at the trigger instant -----
# ---- silently skips the egg, and it is never queued for later ------------
gd6, dm6, pet6, stats6, feed6 = make_detector()
pet6.reminder_popup_active = True
feed6.names.add("deltarune.exe")
gd6._poll()
check("priority: reminder popup active -> game egg silently skipped", dm6.categories() == [])
check("priority: state machine still advances under suppression", gd6._states["dr"] == "running")
pet6.reminder_popup_active = False
check("priority: no queued replay once the reminder popup closes", dm6.categories() == [])

# ---- case-insensitive process-name matching --------------------------------
gd7, dm7, pet7, stats7, feed7 = make_detector()
feed7.names.add("undertale.exe")  # the provider contract returns lowercase names
gd7._poll()
check("case-insensitive match works", dm7.categories() != [])

# ---- idle chat / AFK suppression hook --------------------------------------
gd8, dm8, pet8, stats8, feed8 = make_detector()
check("is_any_game_running() False before any launch", gd8.is_any_game_running() is False)
feed8.names.add("undertale.exe")
gd8._poll()
check("is_any_game_running() True while a tracked game is running", gd8.is_any_game_running() is True)
feed8.names.discard("undertale.exe")
gd8._poll()
gd8._poll()
check("is_any_game_running() False again after confirmed exit", gd8.is_any_game_running() is False)

# ---- display-mode spec case 4: "deferred" holds the launch line back, ----
# ---- and fires it the instant the user tabs out while the game is still --
# ---- running ---------------------------------------------------------------
fg9 = FakeForegroundFeed(name=None)
gd9, dm9, pet9, stats9, feed9 = make_detector(
    config_overrides={"game_egg_display_mode": "deferred"}, foreground_feed=fg9
)
feed9.names.add("undertale.exe")
gd9._poll()  # launch while still fullscreen/foregrounded -> must not speak yet
check("displaymode_case4: deferred launch does not preempt immediately", dm9.categories() == [])
check("displaymode_case4: deferred launch is stashed for later", "ut" in gd9._pending_deferred)
fg9.name = "explorer.exe"  # user alt-tabbed away; game (per feed9) is still running
gd9._poll()
check(
    "displaymode_case4: switching out fires the stashed launch line",
    dm9.categories() == ["game_ut_first"],
    dm9.categories(),
)
check("displaymode_case4: pending cleared after firing", "ut" not in gd9._pending_deferred)

# ---- display-mode spec case 5: game exits before the user ever tabs out --
# ---- -> the stashed launch line is discarded, only the exit line plays ----
gd10, dm10, pet10, stats10, feed10 = make_detector(
    config_overrides={"game_egg_display_mode": "deferred"}
)
feed10.names.add("deltarune.exe")
gd10._poll()  # launch -> stash
check("displaymode_case5: launch stashed before exit", "dr" in gd10._pending_deferred)
feed10.names.discard("deltarune.exe")
gd10._poll()  # miss 1/2
gd10._poll()  # miss 2/2 -> confirmed exit, never having switched away
check("displaymode_case5: pending discarded on exit without switch-out", "dr" not in gd10._pending_deferred)
check(
    "displaymode_case5: only the exit line ever plays, launch line never lands",
    dm10.categories() == ["game_dr_exit"],
    dm10.categories(),
)

# ---- display-mode spec case 6: "topmost" mode, but even topmost couldn't --
# ---- beat an exclusive-fullscreen surface -> this occurrence downgrades --
# ---- itself to "wait for switch-out" instead of talking to a covered -----
# ---- screen, then actually speaks once the user does switch out ---------
fg11 = FakeForegroundFeed(name="undertale.exe")
presenter11 = FakePresenter(downgrade_times=1)
gd11, dm11, pet11, stats11, feed11 = make_detector(
    config_overrides={"game_egg_display_mode": "topmost"},
    presenter=presenter11,
    foreground_feed=fg11,
)
feed11.names.add("undertale.exe")
gd11._poll()  # launch -> presenter reports "still occluded" -> downgrade
check("displaymode_case6: downgrade does not preempt immediately", dm11.categories() == [])
check("displaymode_case6: downgrade stashes the line for later", "ut" in gd11._pending_deferred)
check("displaymode_case6: downgrade force-ends the (invisible) bubble", pet11.force_end_calls == 1)
fg11.name = "explorer.exe"  # user switches out afterward
gd11._poll()
check(
    "displaymode_case6: switching out afterward still speaks the downgraded line",
    dm11.categories() == ["game_ut_first"],
    dm11.categories(),
)
check("displaymode_case6: presenter consulted twice (initial attempt + retry)", presenter11.calls == ["ut", "ut"])

# ---- display-mode: reminder popup active at the deferred fire instant ----
# ---- still suppresses it (same priority rule as the immediate path) ------
fg12 = FakeForegroundFeed(name="undertale.exe")
gd12, dm12, pet12, stats12, feed12 = make_detector(
    config_overrides={"game_egg_display_mode": "deferred"}, foreground_feed=fg12
)
feed12.names.add("undertale.exe")
gd12._poll()  # launch -> stash
pet12.reminder_popup_active = True
fg12.name = "explorer.exe"  # switch out while a reminder popup is up
gd12._poll()
check(
    "displaymode_case_priority: deferred fire is suppressed by an active reminder popup",
    dm12.categories() == [],
)
check("displaymode_case_priority: pending consumed (not requeued) despite suppression", "ut" not in gd12._pending_deferred)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for name, detail in FAILURES:
        print(f"  - {name}: {detail}")
    sys.exit(1)
else:
    print("All checks passed.")
