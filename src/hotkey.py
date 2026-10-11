# -*- coding: utf-8 -*-
"""Global hotkey via RegisterHotKey -- NO keyboard hook of any kind
(no SetWindowsHookEx, no pynput/keyboard library). Windows itself watches
for the key combination and posts WM_HOTKEY to the registering thread's
message queue, so nothing ever sees (or can log) any other keystroke.

parse_hotkey("Ctrl+Alt+L") -> (modifiers, vk), or None if it can't be
used. HotkeyManager registers one thread-level hotkey (hWnd=NULL) and
emits `pressed` when its WM_HOTKEY comes through Qt's native event filter.
Register from the GUI thread: the WM_HOTKEY is posted to that thread's
queue, which is the one Qt's event loop pumps.
"""
import ctypes
from ctypes import wintypes

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000  # holding the keys down fires only once

WM_HOTKEY = 0x0312
VK_F1 = 0x70

# Thread-level hotkey ids must be in 0x0000..0xBFFF.
HOTKEY_ID = 0x4C4B

_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "meta": MOD_WIN,
}


def parse_hotkey(spec):
    """"Ctrl+Alt+L" -> (MOD_CONTROL | MOD_ALT, 0x4C). Modifier names are
    Ctrl/Alt/Shift/Win (case-insensitive, spaces around '+' ignored), the key
    is one letter, one digit, or F1-F12. At least one modifier is required (a
    bare global key would hijack it system-wide). Anything else -> None."""
    if not isinstance(spec, str):
        return None
    parts = [p.strip() for p in spec.split("+")]
    if len(parts) < 2 or any(p == "" for p in parts):
        return None
    *mod_names, key = parts
    modifiers = 0
    for name in mod_names:
        flag = _MODIFIERS.get(name.lower())
        if flag is None or modifiers & flag:  # unknown or repeated modifier
            return None
        modifiers |= flag
    k = key.upper()
    if len(k) == 1 and ("A" <= k <= "Z" or "0" <= k <= "9"):
        vk = ord(k)
    elif k.startswith("F") and k[1:].isdigit() and not k[1:].startswith("0") and 1 <= int(k[1:]) <= 12:
        vk = VK_F1 + int(k[1:]) - 1
    else:
        return None
    return modifiers, vk


_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL


def _real_register(hwnd, hotkey_id, modifiers, vk):
    return bool(_user32.RegisterHotKey(hwnd, hotkey_id, modifiers, vk))


def _real_unregister(hwnd, hotkey_id):
    return bool(_user32.UnregisterHotKey(hwnd, hotkey_id))


class HotkeyManager(QObject, QAbstractNativeEventFilter):
    pressed = pyqtSignal()

    def __init__(self, register_fn=None, unregister_fn=None, get_last_error=None, hotkey_id=HOTKEY_ID):
        """register_fn / unregister_fn / get_last_error are injectable so
        tests can drive this without touching the real RegisterHotKey."""
        super().__init__()
        self._register = register_fn or _real_register
        self._unregister = unregister_fn or _real_unregister
        self._get_last_error = get_last_error or ctypes.get_last_error
        self._id = hotkey_id
        self._registered = False
        self.spec = None
        self.last_error = None  # GetLastError after a failed register(); None otherwise

    @property
    def is_registered(self):
        return self._registered

    def register(self, spec):
        """True if the hotkey is now active. False (never raises) if spec is
        unusable or Windows refused it (typically ERROR_HOTKEY_ALREADY_
        REGISTERED = 1409, i.e. another program owns the combination); the
        reason is left in self.last_error (None for an unparsable spec)."""
        self.unregister()
        self.spec = spec
        self.last_error = None
        parsed = parse_hotkey(spec)
        if parsed is None:
            return False
        modifiers, vk = parsed
        try:
            ok = self._register(None, self._id, modifiers | MOD_NOREPEAT, vk)
        except Exception:
            ok = False
        if not ok:
            try:
                self.last_error = self._get_last_error()
            except Exception:
                self.last_error = -1
            return False
        self._registered = True
        return True

    def unregister(self):
        if not self._registered:
            return
        self._registered = False
        try:
            self._unregister(None, self._id)
        except Exception:
            pass

    def nativeEventFilter(self, eventType, message):
        """Only recognises our own WM_HOTKEY and turns it into the `pressed`
        signal; never consumes the message and does no real work here."""
        try:
            if bytes(eventType) == b"windows_generic_MSG" and self._registered:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == self._id:
                    self.pressed.emit()
        except Exception:
            pass
        return False, 0
