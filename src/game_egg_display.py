# -*- coding: utf-8 -*-
"""Keeps a game-launch/exit egg line (see game_detector.py) from being
silently spoken underneath a fullscreen game. Two independent pieces:

1. Occlusion detection -- is the foreground window actually the target
   game, sized to fill its monitor? Only process name + window geometry
   are read here, same "never a window title/content/screenshot" rule
   game_detector.py already follows for the process list itself.
2. TopmostGuard -- temporarily raises the pet + dialogue-box windows'
   native Z-order via SetWindowPos(HWND_TOPMOST) and restores it once the
   bubble closes. Deliberately does NOT go through Qt's
   setWindowFlags(WindowStaysOnTopHint), which tears down and recreates
   the native window (visible flicker) -- a raw Z-order change is
   invisible and has no effect on the Qt-level "已置顶" state that the
   right-click menu checkbox reflects, so the two concepts never bleed
   into each other (see PetWindow.is_pinned_on_top / DialogueBox.is_pinned,
   which TopmostGuard's caller uses to decide what to restore *to*, not
   just "always drop back to normal").
"""
import ctypes
from ctypes import wintypes

from PyQt6.QtCore import QTimer

from process_snapshot import process_name_for_pid

_user32 = ctypes.windll.user32

MONITOR_DEFAULTTONEAREST = 2
GA_ROOT = 2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.MonitorFromWindow.restype = wintypes.HANDLE
_user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
_user32.GetMonitorInfoW.restype = wintypes.BOOL
_user32.WindowFromPoint.argtypes = [_POINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
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


def _hwnd_of(widget):
    return int(widget.winId())


def _foreground_pid_and_hwnd():
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value, hwnd


def foreground_process_name():
    """Lowercase exe name of whatever window currently has focus, or None
    if it can't be determined -- used by deferred mode to notice the user
    tabbed away from the game."""
    pid, hwnd = _foreground_pid_and_hwnd()
    if not hwnd or pid is None:
        return None
    return process_name_for_pid(pid)


def _is_hwnd_fullscreen(hwnd):
    rect = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return False
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    m = info.rcMonitor
    return (rect.left, rect.top, rect.right, rect.bottom) == (m.left, m.top, m.right, m.bottom)


def is_occluded_by_process(process_name_lower):
    """True only if the foreground window belongs to `process_name_lower`
    AND fills its monitor -- a windowed/bordered instance (or one on a
    monitor the pet isn't near) is not treated as occluding anything."""
    pid, hwnd = _foreground_pid_and_hwnd()
    if not hwnd or pid is None:
        return False
    fg_name = process_name_for_pid(pid)
    if fg_name != process_name_lower:
        return False
    return _is_hwnd_fullscreen(hwnd)


def point_shows_one_of(x, y, own_hwnds):
    """Whatever window is actually topmost at screen point (x, y) -- used
    after applying TOPMOST to double check it actually won (exclusive-
    fullscreen DirectX surfaces can sit above even HWND_TOPMOST)."""
    hwnd_at_point = _user32.WindowFromPoint(_POINT(x, y))
    if not hwnd_at_point:
        return False
    if hwnd_at_point in own_hwnds:
        return True
    root = _user32.GetAncestor(hwnd_at_point, GA_ROOT)
    return bool(root) and root in own_hwnds


def _set_topmost(hwnd, topmost):
    insert_after = HWND_TOPMOST if topmost else HWND_NOTOPMOST
    _user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


class TopmostGuard:
    """Owns at most one active temporary-topmost cycle at a time. `apply`
    raises every given widget to TOPMOST immediately, then polls
    `until_hidden_check` on a QTimer; the moment it returns True (or the
    hard `max_duration_ms` safety net elapses -- covers any code path that
    hides the dialogue box without ever emitting `finished`, e.g.
    PetWindow.force_end_bubble/stop_immediately), every widget is dropped
    back to whatever z-order it was told to restore to and the timer stops.
    """

    def __init__(self, poll_interval_ms=150, max_duration_ms=60_000):
        self._poll_interval_ms = poll_interval_ms
        self._max_duration_ms = max_duration_ms
        self._timer = None

    def is_active(self):
        return self._timer is not None

    def apply(self, widgets_with_restore, until_hidden_check, on_restored=None):
        """widgets_with_restore: [(widget, restore_topmost_bool), ...] --
        restore_topmost_bool is what that widget's z-order should revert to
        once the guard ends (True if it was already user-pinned)."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

        for widget, _ in widgets_with_restore:
            _set_topmost(_hwnd_of(widget), True)

        elapsed = {"ms": 0}
        timer = QTimer()
        timer.setInterval(self._poll_interval_ms)

        def _tick():
            elapsed["ms"] += self._poll_interval_ms
            if not (until_hidden_check() or elapsed["ms"] >= self._max_duration_ms):
                return
            timer.stop()
            self._timer = None
            for widget, restore_topmost in widgets_with_restore:
                _set_topmost(_hwnd_of(widget), restore_topmost)
            if on_restored:
                on_restored()

        timer.timeout.connect(_tick)
        self._timer = timer
        timer.start()


class TopmostEggPresenter:
    """The "topmost" display-mode's whole flow: check occlusion, apply a
    TopmostGuard cycle if needed, show the line, then verify shortly after
    that the guard actually won the fight (exclusive-fullscreen games can
    sit above even HWND_TOPMOST) -- if not, hand back to the caller's
    `on_downgrade` so this one occurrence can fall back to "wait for the
    user to switch out" instead of talking into a screen nobody can see.
    Every collaborator is a constructor param so tests can swap in fakes
    without needing a real foreground game window."""

    def __init__(
        self,
        guard=None,
        occlusion_check=is_occluded_by_process,
        point_check=point_shows_one_of,
        verify_delay_ms=200,
    ):
        self._guard = guard or TopmostGuard()
        self._occlusion_check = occlusion_check
        self._point_check = point_check
        self._verify_delay_ms = verify_delay_ms

    def present(self, key, pet, dialogue_box, process_name_lower, show_now, on_downgrade, log):
        occluded = self._occlusion_check(process_name_lower)
        log(f"游戏彩蛋：[{key}] 遮挡检测={'遮挡' if occluded else '未遮挡'}")
        if not occluded:
            show_now()
            return

        pet_restore = pet.is_pinned_on_top()
        box_restore = dialogue_box.is_pinned()

        def _restored():
            log(f"游戏彩蛋：[{key}] 层级已恢复（原状态：小花={'置顶' if pet_restore else '非置顶'}）")

        self._guard.apply(
            [(pet, pet_restore), (dialogue_box, box_restore)],
            until_hidden_check=lambda: not dialogue_box.isVisible(),
            on_restored=_restored,
        )
        log(f"游戏彩蛋：[{key}] 已临时置顶")
        show_now()

        def _verify():
            if not dialogue_box.isVisible():
                return  # already closed naturally before we got to check
            own = {int(pet.winId()), int(dialogue_box.winId())}
            center = pet.geometry().center()
            if self._point_check(center.x(), center.y(), own):
                return
            log(f"游戏彩蛋：[{key}] 置顶后仍被遮挡（疑似独占全屏），自动降级为攒着等切出")
            on_downgrade()

        QTimer.singleShot(self._verify_delay_ms, _verify)
