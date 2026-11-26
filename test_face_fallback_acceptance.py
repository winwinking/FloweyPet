# -*- coding: utf-8 -*-
"""Acceptance tests for SpriteBank's unknown-expression fallback: any face
name that is not a string or not in the manifest must draw the neutral face
(same pixels, same frame count), never a face-less stem. Nothing is written
outside a temp dir.

Run:  QT_QPA_PLATFORM=offscreen python test_face_fallback_acceptance.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="floweypet_facefb_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import error_log  # noqa: E402

error_log.LOG_PATH = TMP / "error.log"

from compositor import SpriteBank  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def img(bank, expr, idx=0):
    return bank.get_face(expr, idx).toImage()


bank = SpriteBank(1.0)
neutral = img(bank, "neutral")
check("对照: neutral 不是空图", not neutral.isNull() and neutral.width() > 0)

BAD = [("乱名 no_such_face_xyz", "no_such_face_xyz"), ("空字符串", ""), ("None", None)]
for label, expr in BAD:
    try:
        got = img(bank, expr)
        raised = None
    except Exception as e:  # noqa: BLE001
        got, raised = None, e
    check(f"{label}: 不抛异常", raised is None, repr(raised))
    check(f"{label}: get_face 与 neutral 的图像完全相同", got is not None and got == neutral)
    try:
        n = bank.face_frame_count(expr)
        raised = None
    except Exception as e:  # noqa: BLE001
        n, raised = None, e
    check(f"{label}: face_frame_count 不抛异常且等于 neutral 的", raised is None and n == bank.face_frame_count("neutral"), repr(raised or n))

# a different frame index of a bad name must also match neutral's same index
check("乱名 frame_idx=1 与 neutral frame_idx=1 相同", img(bank, "no_such_face_xyz", 1) == img(bank, "neutral", 1))

smirk = img(bank, "smirk")
check("正常表情 smirk 不是空图", not smirk.isNull() and smirk.width() > 0)
check("正常表情 smirk 与 neutral 不同", smirk != neutral)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("表情回退用例全部通过")
sys.exit(0)
