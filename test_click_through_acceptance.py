# -*- coding: utf-8 -*-
"""Acceptance test for win_zorder.set_click_through: the native
WS_EX_TRANSPARENT / WS_EX_LAYERED extended-style bits really flip.

Unlike the other acceptance tests this one deliberately does NOT use the
offscreen platform: under offscreen, winId() is a fake id with no real
HWND behind it, so the ex-style could never be read back. It runs on the
real "windows" platform but only ever creates a native window for a bare
QWidget that is never shown -- nothing appears on the desktop, no focus is
taken, and no FloweyPet window is touched.

Run:  python test_click_through_acceptance.py     (Windows only)
"""
import os
import sys
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "windows"  # force: tools/run_tests_isolated.py sets offscreen for children
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

app = QApplication(sys.argv)

import win_zorder as z  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"[PASS] {name}")
    else:
        FAILURES.append(name)
        print(f"[FAIL] {name} {detail}")


def style_of(hwnd):
    return z._GetWindowLongPtrW(hwnd, z.GWL_EXSTYLE)


def transparent(hwnd):
    return bool(style_of(hwnd) & z.WS_EX_TRANSPARENT)


def layered(hwnd):
    return bool(style_of(hwnd) & z.WS_EX_LAYERED)


check("平台是真实 windows 而非 offscreen", app.platformName() == "windows", app.platformName())

w = QWidget(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
hwnd = int(w.winId())  # forces the native window into existence; never shown
check("拿到真实 hwnd(IsWindow 为真)", bool(hwnd) and bool(z._user32.IsWindow(hwnd)), str(hwnd))
check("测试窗口从未显示", w.isVisible() is False)
check("初始: LAYERED=Y TRANSPARENT=N", layered(hwnd) and not transparent(hwnd), hex(style_of(hwnd)))

z.set_click_through(w, True)
check("开启后 TRANSPARENT=Y", transparent(hwnd), hex(style_of(hwnd)))
check("开启后 LAYERED 仍为 Y", layered(hwnd), hex(style_of(hwnd)))

z.set_click_through(w, False)
check("关闭后 TRANSPARENT=N", not transparent(hwnd), hex(style_of(hwnd)))
check("关闭后 LAYERED 仍为 Y(Qt 透明窗口需要)", layered(hwnd), hex(style_of(hwnd)))

for i in range(2):
    z.set_click_through(w, True)
    on_ok = transparent(hwnd) and layered(hwnd)
    z.set_click_through(w, False)
    off_ok = (not transparent(hwnd)) and layered(hwnd)
    check(f"连续切换第{i + 1}轮: 开=Y/Y 关=N/Y 状态不乱", on_ok and off_ok, hex(style_of(hwnd)))

before = style_of(hwnd)
z.set_click_through(w, True)
z.set_click_through(w, True)
check("重复开启幂等(再开一次样式不变)", style_of(hwnd) == (before | z.WS_EX_TRANSPARENT | z.WS_EX_LAYERED), hex(style_of(hwnd)))
z.set_click_through(w, False)
z.set_click_through(w, False)
check("重复关闭幂等且保留其它样式位", style_of(hwnd) == before, f"{hex(style_of(hwnd))} vs {hex(before)}")


class _NoWindow:
    def winId(self):
        return 0


class _BadWindow:
    def winId(self):
        raise RuntimeError("boom")


for label, obj in (("hwnd=0", _NoWindow()), ("winId 抛异常", _BadWindow())):
    try:
        z.set_click_through(obj, True)
        check(f"无效窗口({label}): 静默返回不报错", True)
    except Exception as e:  # noqa: BLE001
        check(f"无效窗口({label}): 静默返回不报错", False, repr(e))

w.deleteLater()
print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("click-through 用例全部通过")
sys.exit(0)
