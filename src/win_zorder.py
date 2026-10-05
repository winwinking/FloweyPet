# -*- coding: utf-8 -*-
"""Native Z-order helpers shared by game_egg_display.TopmostGuard and
topmost_keeper.TopmostKeeper. A raw SetWindowPos(HWND_TOPMOST/NOTOPMOST)
change is invisible (no native window teardown/flicker, unlike Qt's
setWindowFlags(WindowStaysOnTopHint)) and does not touch the Qt-level
"已置顶" state the right-click menu checkbox reflects.
"""
import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32

SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

_user32.SetWindowPos.argtypes = [
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [ctypes.c_void_p]
_user32.IsWindow.restype = ctypes.c_int


def _hwnd_of(widget):
    return int(widget.winId())


def _set_topmost(hwnd, topmost):
    insert_after = HWND_TOPMOST if topmost else HWND_NOTOPMOST
    _user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def apply_topmost(widget, topmost):
    """Best-effort _set_topmost for one widget: silently does nothing if
    the widget has no live native window (e.g. already destroyed, or the
    offscreen test platform's fake winId). Never raises."""
    try:
        hwnd = _hwnd_of(widget)
        if hwnd and _user32.IsWindow(hwnd):
            _set_topmost(hwnd, topmost)
    except Exception:
        pass
