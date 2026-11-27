# -*- coding: utf-8 -*-
"""Regression test: callbacks scheduled by an OLD line (page pause, segment
pause, hold_seconds, glitch end) must not fire into a NEW line that replaced
it via stop_immediately() + start(). Real symptom: an API chat reply's first
sentence was flipped to the second one before it finished typing, because the
"……" shown while waiting had left a page/segment callback behind.

Real DialogueBox and real QTimers; only the module's timing constants are
shrunk so the whole file runs in about a second.

Run:  QT_QPA_PLATFORM=offscreen python test_dialogue_box_stale_timers.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import dialogue_box as db_mod  # noqa: E402
from dialogue_box import DialogueBox  # noqa: E402

db_mod.TYPE_INTERVAL_MS = 6
db_mod.SLOW_TYPE_INTERVAL_MS = 6
db_mod.PAGE_PAUSE_MS = 90
db_mod.SEGMENT_END_PAUSE_MS = 90
db_mod.GLITCH_TOTAL_MS = 20
db_mod.GLITCH_FLICKER_MS = 5

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


class _Audio:
    def __getattr__(self, name):
        return lambda *a, **k: None


def pump_until(cond, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.002)
    return cond()


NEW_TEXTS = ["第一句要打好一阵子才能打完的话，字数足够多。", "第二句同样要打好一阵子的话，字数也足够多。", "第三句。"]
NEW = [{"text": t, "face": f"n{i + 1}"} for i, t in enumerate(NEW_TEXTS)]


def scenario(label, old_segments, old_pending):
    """Start `old_segments`, wait until `old_pending(box)` says an old callback
    is now scheduled, cut over to NEW, and watch the new line play."""
    box = DialogueBox(180, 1.75, _Audio(), True, pet_scale=1.0, min_font_px=15)
    for seg in NEW:
        pages = box._paginate(box._wrap(seg["text"]))
        assert len(pages) == 1, (seg["text"], pages)  # one page each, so "fully typed" is unambiguous
    finished = []
    box.finished.connect(lambda: finished.append(True))
    box.start(old_segments, lambda face: None)
    reached = pump_until(lambda: old_pending(box), 3)
    check(f"[{label}] 旧台词已经排下了回调(前置条件)", reached)
    box.stop_immediately()
    finished.clear()

    seen = []  # (face, previous segment fully typed?)

    def on_face(face):
        seen.append((face, box._revealed >= len(box._full_page_text) and box._page_idx >= len(box._pages)))

    box.start(NEW, on_face)
    pump_until(lambda: bool(finished), 10)
    pump_until(lambda: False, 0.35)  # leave room for any late stale callback to misbehave
    faces = [f for f, _ in seen]
    check(f"[{label}] 新台词按顺序完整播完 n1,n2,n3", faces == ["n1", "n2", "n3"], str(faces))
    check(f"[{label}] 第二句、第三句出现时, 前一句都已打完", all(ok for f, ok in seen if f != "n1"), str(seen))
    check(f"[{label}] 只结束一次(finished 恰好 1 次)", len(finished) == 1, str(finished))
    box.close()


# 1) page pause: "……" typed out, the page-end singleShot(PAGE_PAUSE_MS, _advance_page) is pending
scenario("页停顿", [{"text": "……", "face": "o"}],
         lambda b: b._revealed >= len(b._full_page_text) > 0 and not b._typing_timer.isActive())
# 2) segment pause: page already advanced, singleShot(SEGMENT_END_PAUSE_MS, _advance_segment) is pending
scenario("段停顿", [{"text": "……", "face": "o"}, {"text": "后面还有一句。", "face": "o2"}],
         lambda b: b._seg_idx == 0 and b._page_idx >= len(b._pages))
# 3) hold_seconds segment: singleShot(hold, _advance_segment) is pending
scenario("hold_seconds", [{"hold_seconds": 0.25, "face": "o"}, {"text": "之后。", "face": "o2"}],
         lambda b: b._seg_idx == 0 and not b._typing_timer.isActive())
# 4) glitch without text: singleShot(SEGMENT_END_PAUSE_MS, _advance_segment) after the glitch is pending
scenario("glitch 无字", [{"effect": "glitch", "face": "o", "text": ""}, {"text": "之后。", "face": "o2"}],
         lambda b: b._glitch_elapsed_ms >= db_mod.GLITCH_TOTAL_MS and not b._glitch_timer.isActive())

# 5) plain playback is unchanged: three segments straight through
box = DialogueBox(180, 1.75, _Audio(), True, pet_scale=1.0, min_font_px=15)
done, faces = [], []
box.finished.connect(lambda: done.append(True))
t0 = time.monotonic()
box.start(NEW, lambda f: faces.append(f))
pump_until(lambda: bool(done), 10)
check("正常播放(不换台词): 顺序完整、结束一次", faces == ["n1", "n2", "n3"] and len(done) == 1, f"{faces} {done}")
# 6) a callback left over from the OLD line must not close/advance after a normal finish either
pump_until(lambda: False, 0.3)
check("播完之后没有多余回调再触发 finished", len(done) == 1, str(done))
box.close()

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("旧计时器作废用例全部通过")
sys.exit(0)
