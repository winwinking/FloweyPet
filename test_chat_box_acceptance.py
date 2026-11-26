# -*- coding: utf-8 -*-
"""Acceptance tests for ChatBox (行动 -> 聊天 input box): Enter submits and
clears, blank input is ignored, Esc / × close it, 200-char limit, and the
window must NOT be a Qt.Popup / WindowDoesNotAcceptFocus window. Nothing is
written outside a temp dir.

Run:  QT_QPA_PLATFORM=offscreen python test_chat_box_acceptance.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="floweypet_chatbox_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import error_log  # noqa: E402

error_log.LOG_PATH = TMP / "error.log"

from chat_box import ChatBox  # noqa: E402
from dialogue_box import DialogueBox  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


class _Audio:
    def __getattr__(self, name):
        return lambda *a, **k: None


def make(pin=True):
    box = ChatBox(180, 1.75, always_on_top=pin, pet_scale=1.0, min_font_px=15)
    got, closed = [], []
    box.submitted.connect(got.append)
    box.closed.connect(lambda: closed.append(1))
    return box, got, closed


def type_text(box, text):
    box._edit.setText(text)


def press(widget, key):
    QTest.keyClick(widget, key)


# ---------------------------------------------------------------- submit
box, got, closed = make()
box.open_and_focus()
check("open_and_focus 后可见", box.isVisible())
type_text(box, "你好，小花")
press(box._edit, Qt.Key.Key_Return)
check("回车发出 submitted 且内容正确", got == ["你好，小花"], str(got))
check("回车后输入框被清空", box._edit.text() == "")
check("回车后框仍可见", box.isVisible())
check("回车后没有发 closed", closed == [])

type_text(box, "  前后有空格  ")
press(box._edit, Qt.Key.Key_Enter)
check("首尾空白被去掉", got[-1] == "前后有空格", str(got))

n = len(got)
type_text(box, "")
press(box._edit, Qt.Key.Key_Return)
check("空串不发信号", len(got) == n)
type_text(box, "     ")
press(box._edit, Qt.Key.Key_Return)
check("纯空格不发信号", len(got) == n)
check("纯空格回车后框仍可见", box.isVisible())

# ------------------------------------------------------------- max length
check("maxLength == 200", box._edit.maxLength() == 200)
type_text(box, "字" * 300)
check("输入 300 字只留 200", len(box._edit.text()) == 200, str(len(box._edit.text())))
type_text(box, "")

# ------------------------------------------------------------- close paths
press(box._edit, Qt.Key.Key_Escape)
check("输入框里按 Esc 关闭", not box.isVisible())
check("Esc 发出 closed", closed == [1], str(closed))

box.open_and_focus()
press(box, Qt.Key.Key_Escape)
check("框本身收到 Esc 也关闭", not box.isVisible() and closed == [1, 1], str(closed))

box.open_and_focus()
QTest.mouseClick(box._close_label, Qt.MouseButton.LeftButton)
check("点 × 关闭", not box.isVisible())
check("点 × 发出 closed", closed == [1, 1, 1], str(closed))
check("× 标签文字", box._close_label.text() == "×")

box.open_and_focus()
check("关闭后可以再次打开", box.isVisible())
box.close()

# ---------------------------------------------------------- window flags
check("窗口类型是 Tool", box.windowType() == Qt.WindowType.Tool, str(box.windowType()))
check("窗口类型不是 Popup", box.windowType() != Qt.WindowType.Popup)
check("没有 WindowDoesNotAcceptFocus", not (box.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus))
check("有 FramelessWindowHint", bool(box.windowFlags() & Qt.WindowType.FramelessWindowHint))
check("没有 WA_ShowWithoutActivating", not box.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
check("有 WA_TranslucentBackground", box.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
check("置顶时有 WindowStaysOnTopHint", bool(box.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
unpinned, _, _ = make(pin=False)
check("不置顶时没有 WindowStaysOnTopHint", not (unpinned.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
unpinned.set_always_on_top(True)
check("set_always_on_top 更新 is_pinned", unpinned.is_pinned() is True)

# ------------------------------------------------- size matches the bubble
for scale in (2.0, 1.0, 0.5, 0.25):
    pw = round(45 * 4 * scale)
    d = DialogueBox(pw, 1.75, _Audio(), True, pet_scale=scale, min_font_px=15)
    c = ChatBox(pw, 1.75, True, pet_scale=scale, min_font_px=15)
    check(f"挡位 {scale}: 宽度与对话框相同", c.width() == d.width(), f"{c.width()} vs {d.width()}")
    check(f"挡位 {scale}: 字号与对话框相同", c.font.pixelSize() == d.font.pixelSize())
    check(f"挡位 {scale}: 比对话框矮", c.height() < d.height())
box.reconfigure(90, 1.75, 0.5, 15)
check("reconfigure 后宽度更新", box.width() == round(90 * 2.75), str(box.width()))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("聊天框控件用例全部通过")
sys.exit(0)
