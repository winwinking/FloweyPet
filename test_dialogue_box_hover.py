# -*- coding: utf-8 -*-
"""Repro/regression test for the "popup vanishes while user is trying to
click 确定/等等" bug: hovering into the dialogue box (as the user's mouse
does on the way to a button) then leaving it again (very easy to do given
how small the box is at low pet_scale) must NOT make the box disappear
while its choice bar is still waiting on a response.

Root cause: DialogueBox._disable_choice_input() called setWindowFlags()
(which hides a QWidget as a side effect in Qt) without a follow-up show(),
unlike its counterpart _enable_choice_input(). Any leaveEvent while the
choice bar was up therefore hid the whole popup silently, DialogueBox never
emitted `finished`, so PetWindow/DialogueManager.busy got stuck True
forever -- explains both "popup disappears before I can click" and "the
pet stops responding to anything afterwards" (busy never clears without
that finished signal), reported by the user 2026-07-12.

Run:  QT_QPA_PLATFORM=offscreen python test_dialogue_box_hover.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QEvent, QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

from dialogue_box import DialogueBox  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def wait_ms(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class FakeAudio:
    def play_blip_for_char(self, ch):
        pass

    def play_laugh(self):
        pass

    def play_menu_move(self):
        pass

    def play_menu_select(self):
        pass


finished_calls = []
box = DialogueBox(120, 1.0, FakeAudio(), confirm_hold_ms=60_000)
box.finished.connect(lambda: finished_calls.append(True))

segments = [{"text": "该喝水啦。", "face": "neutral"}]
options = [("confirm", "确定"), ("wait", "等等")]
box.start(segments, lambda face: None, choice_options=options, hold_ms=60_000)

# let the typing finish and the choice bar appear
for _ in range(200):
    if box._button_bar.isVisible():
        break
    wait_ms(20)
check("choice bar appeared", box._button_bar.isVisible())
check("box visible right after choice bar appears", box.isVisible())

# Simulate the mouse moving into the box (user aiming for a button), then
# leaving it again mid-decision -- exactly what happens when the box is
# small (low pet_scale) and the cursor overshoots/undershoots the button.
# Calling the underlying handlers directly (rather than synthesizing real
# QEnterEvent/QEvent objects) exercises exactly the methods responsible
# without fighting Qt's constructor requirements for those event types.
box._enable_choice_input()
check("box visible after enterEvent", box.isVisible())
box._disable_choice_input()
check(
    "BUG: box must stay visible after leaveEvent while choice bar is still up",
    box.isVisible(),
    f"isVisible={box.isVisible()} accept_focus={box._accept_focus}",
)
check(
    "choice bar itself must still be visible/clickable after leaveEvent",
    box._button_bar.isVisible(),
)
check("no premature finished signal from the hover/leave alone", not finished_calls)

# Now the user actually manages to click 确定 -- must still resolve normally.
box._button_bar.activate_selected()
wait_ms(20)
check("finished emitted after a real click post-leaveEvent", finished_calls == [True], str(finished_calls))
check("response_outcome recorded", box.response_outcome == "confirm", str(box.response_outcome))
check("box hidden after a real confirm", not box.isVisible())

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败:")
    for name, detail in FAILURES:
        print(f"  FAIL {name}: {detail}")
    sys.exit(1)
print("全部通过")
sys.exit(0)
