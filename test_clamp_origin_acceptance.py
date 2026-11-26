# -*- coding: utf-8 -*-
"""Acceptance tests for pet_window.clamp_origin_into_area (used for the
start-up position and for keeping the pet on screen after a size change),
plus PetWindow._apply_pet_scale actually using it.

Run:  QT_QPA_PLATFORM=offscreen python test_clamp_origin_acceptance.py
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

TMP = Path(tempfile.mkdtemp(prefix="floweypet_clamp_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QRect  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import error_log  # noqa: E402

error_log.LOG_PATH = TMP / "error.log"

import pet_window  # noqa: E402

clamp = pet_window.clamp_origin_into_area
FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


AREA = QRect(0, 0, 1000, 800)
check("完全在内部: 位置不变", clamp(100, 200, 180, 212, AREA) == (100, 200))
check("贴右边: 超出的部分被推回", clamp(950, 200, 180, 212, AREA) == (820, 200), str(clamp(950, 200, 180, 212, AREA)))
check("贴下边: 超出的部分被推回", clamp(100, 700, 180, 212, AREA) == (100, 588), str(clamp(100, 700, 180, 212, AREA)))
check("右下角同时超出", clamp(990, 790, 180, 212, AREA) == (820, 588))
check("贴左上角(负坐标): 夹到区域左上角", clamp(-50, -30, 180, 212, AREA) == (0, 0))
check("窗口比屏幕还大: 钉在区域左上角", clamp(300, 300, 1200, 900, AREA) == (0, 0))
check("可用区域不从 0,0 开始(任务栏在左/上)", clamp(0, 0, 100, 100, QRect(50, 40, 500, 400)) == (50, 40))
check("可用区域有偏移时贴右下", clamp(900, 900, 100, 100, QRect(50, 40, 500, 400)) == (450, 340))
check("恰好贴边不被移动", clamp(820, 588, 180, 212, AREA) == (820, 588))

# _apply_pet_scale uses it: grow while hugging the right/bottom edge
area = QRect(0, 0, 1000, 800)
moves = []
frame = SimpleNamespace(size=lambda: SimpleNamespace(width=lambda: 360, height=lambda: 424))
screen = SimpleNamespace(availableGeometry=lambda: area)
fake = MagicMock()
fake.stats = MagicMock()
fake.sprites = SimpleNamespace(set_pet_scale=lambda s: None, appear_frames=[frame])
fake.x = lambda: 820
fake.y = lambda: 588
fake.width = lambda: 180
fake.height = lambda: 212
fake.screen = lambda: screen
fake.move = lambda x, y: moves.append((x, y))
fake.config = {}
fake.dialogue_box = MagicMock()
fake.pomodoro_display = MagicMock()
fake._current_frame_for_state = lambda: None
try:
    pet_window.PetWindow._apply_pet_scale(fake, 2.0)
except Exception as e:  # noqa: BLE001
    print("apply raised", repr(e))
check("放大到 200% 时贴右下角的小花被夹回屏幕内", moves and moves[-1] == (640, 376), str(moves))

fake2 = MagicMock()
fake2.stats = MagicMock()
fake2.sprites = fake.sprites
fake2.x, fake2.y, fake2.width, fake2.height = fake.x, fake.y, fake.width, fake.height
fake2.screen = lambda: None
fake2.move = lambda x, y: moves.append((x, y))
fake2.config = {}
fake2.dialogue_box = MagicMock()
fake2.pomodoro_display = MagicMock()
fake2._current_frame_for_state = lambda: None
moves.clear()
try:
    pet_window.PetWindow._apply_pet_scale(fake2, 2.0)
except Exception as e:  # noqa: BLE001
    print("apply raised", repr(e))
check("screen() 为 None 时退回主屏, 不崩溃且位置在主屏内", len(moves) == 1 and moves[0][0] >= 0 and moves[0][1] >= 0, str(moves))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("夹回屏幕用例全部通过")
sys.exit(0)
