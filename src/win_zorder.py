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
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000

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

# 64-bit safe: LONG_PTR is pointer-sized, so use c_ssize_t (a plain c_int/c_long
# would truncate the high half on x64). GetWindowLongPtrW only exists as a real
# export on 64-bit Windows; on 32-bit it's a macro over GetWindowLongW.
_GetWindowLongPtrW = getattr(_user32, "GetWindowLongPtrW", None) or _user32.GetWindowLongW
_SetWindowLongPtrW = getattr(_user32, "SetWindowLongPtrW", None) or _user32.SetWindowLongW
_GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_GetWindowLongPtrW.restype = ctypes.c_ssize_t
_SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
_SetWindowLongPtrW.restype = ctypes.c_ssize_t


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


def set_click_through(widget, enabled):
    """Mouse click-through for one widget via the native extended style:
    enabled sets WS_EX_LAYERED | WS_EX_TRANSPARENT (clicks fall to whatever
    window is underneath); disabled clears only WS_EX_TRANSPARENT and keeps
    WS_EX_LAYERED, which Qt's translucent windows need. No setWindowFlags /
    show() -- the native window isn't torn down. Silently does nothing if
    the widget has no live native window. Never raises."""
    try:
        hwnd = _hwnd_of(widget)
        if not hwnd or not _user32.IsWindow(hwnd):
            return
        style = _GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        if enabled:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        _SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass
