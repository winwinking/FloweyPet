# -*- coding: utf-8 -*-
"""Acceptance tests for the global lock hotkey (src/hotkey.py + the
PetWindow glue). Offscreen and fully faked: no real RegisterHotKey is called
here (that needs the real desktop -- see tools/manual_hotkey_check.py).

Run:  QT_QPA_PLATFORM=offscreen python test_hotkey_acceptance.py
"""
import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QByteArray, QCoreApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

app = QApplication(sys.argv)

import config as config_mod  # noqa: E402
import dialogue_log  # noqa: E402
import hotkey  # noqa: E402
import pet_window  # noqa: E402
from hotkey import (  # noqa: E402
    HOTKEY_ID,
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    MOD_WIN,
    WM_HOTKEY,
    HotkeyManager,
    parse_hotkey,
)
from pet_window import PetWindow  # noqa: E402

LOG_LINES = []
dialogue_log.log_event = LOG_LINES.append

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def pump():
    for _ in range(3):
        QCoreApplication.processEvents()


# ===================================================================== 1
# parse_hotkey: valid
CA = MOD_CONTROL | MOD_ALT
valid = {
    "Ctrl+Alt+L": (CA, 0x4C),
    "ctrl+alt+l": (CA, 0x4C),
    "CTRL+ALT+L": (CA, 0x4C),
    " Ctrl + Alt + L ": (CA, 0x4C),
    "Alt+Ctrl+L": (CA, 0x4C),
    "Ctrl+1": (MOD_CONTROL, 0x31),
    "Ctrl+0": (MOD_CONTROL, 0x30),
    "Shift+Alt+9": (MOD_SHIFT | MOD_ALT, 0x39),
    "Alt+F1": (MOD_ALT, 0x70),
    "Win+Shift+F12": (MOD_WIN | MOD_SHIFT, 0x7B),
    "win+f5": (MOD_WIN, 0x74),
    "Control+Windows+A": (MOD_CONTROL | MOD_WIN, 0x41),
    "Ctrl+Alt+Shift+Win+Z": (MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_WIN, 0x5A),
    "Ctrl+F": (MOD_CONTROL, 0x46),  # the letter F, not a function key
}
for spec, want in valid.items():
    check(f"parse 合法 {spec!r}", parse_hotkey(spec) == want, str(parse_hotkey(spec)))

# parse_hotkey: invalid -> None
invalid = [
    "", "   ", None, 123, ["Ctrl", "L"],
    "L", "F5", "Ctrl", "Ctrl+", "+L", "Ctrl++L", "Ctrl+Alt",
    "Ctrl+Ctrl+L", "Ctrl+Foo+L", "Foo+L", "Ctrl+LL", "Ctrl+Alt+L+K",
    "Ctrl+F13", "Ctrl+F0", "Ctrl+F01", "Ctrl+F", "Ctrl+Space", "Ctrl+;", "Ctrl+Alt+Enter",
]
invalid = [s for s in invalid if s != "Ctrl+F"]  # "Ctrl+F" is the letter F: valid, covered above
for spec in invalid:
    check(f"parse 非法 {spec!r} -> None", parse_hotkey(spec) is None, str(parse_hotkey(spec)))

# ===================================================================== 2
# HotkeyManager with a fake register function
calls = []
mgr = HotkeyManager(
    register_fn=lambda hwnd, i, m, vk: calls.append(("reg", hwnd, i, m, vk)) or True,
    unregister_fn=lambda hwnd, i: calls.append(("unreg", hwnd, i)) or True,
)
check("register 成功返回 True", mgr.register("Ctrl+Alt+L") is True)
check("register 以线程级(hWnd=None)注册并带 MOD_NOREPEAT",
      calls == [("reg", None, HOTKEY_ID, CA | MOD_NOREPEAT, 0x4C)], str(calls))
check("成功后 is_registered 为 True 且 last_error 为 None", mgr.is_registered is True and mgr.last_error is None)
mgr.unregister()
check("unregister 调用 UnregisterHotKey(None, id)", calls[-1] == ("unreg", None, HOTKEY_ID), str(calls))
n = len(calls)
mgr.unregister()
check("重复 unregister 不再调用", len(calls) == n)
check("unregister 后 is_registered 为 False", mgr.is_registered is False)

# registration refused (e.g. 1409 = already registered by another program)
fail_calls = []
mgr = HotkeyManager(
    register_fn=lambda *a: fail_calls.append(a) or False,
    unregister_fn=lambda *a: True,
    get_last_error=lambda: 1409,
)
try:
    ok = mgr.register("Ctrl+Alt+L")
    raised = None
except Exception as e:  # noqa: BLE001
    ok, raised = None, e
check("注册失败: 不抛异常", raised is None, repr(raised))
check("注册失败: 返回 False", ok is False)
check("注册失败: 记录 GetLastError=1409", mgr.last_error == 1409, str(mgr.last_error))
check("注册失败: is_registered 为 False", mgr.is_registered is False)

# register function itself blows up
mgr = HotkeyManager(register_fn=lambda *a: (_ for _ in ()).throw(OSError("boom")), get_last_error=lambda: 5)
try:
    ok = mgr.register("Ctrl+Alt+L")
    raised = None
except Exception as e:  # noqa: BLE001
    ok, raised = None, e
check("注册函数抛异常: 不外抛且返回 False", raised is None and ok is False, repr(raised))

# unparsable spec never reaches Windows
fake_calls = []
mgr = HotkeyManager(register_fn=lambda *a: fake_calls.append(a) or True)
check("配置写错(无法解析): 返回 False", mgr.register("Ctrl+Nope") is False)
check("配置写错: 不调用 RegisterHotKey", fake_calls == [])
check("配置写错: last_error 为 None", mgr.last_error is None)

# re-registering replaces the previous registration
calls = []
mgr = HotkeyManager(
    register_fn=lambda hwnd, i, m, vk: calls.append("reg") or True,
    unregister_fn=lambda hwnd, i: calls.append("unreg") or True,
)
mgr.register("Ctrl+Alt+L")
mgr.register("Ctrl+Alt+K")
check("重复 register 先注销旧的再注册", calls == ["reg", "unreg", "reg"], str(calls))

# ===================================================================== 3
# nativeEventFilter against fake MSG memory
def make_msg(message, wparam):
    msg = wintypes.MSG()
    msg.message = message
    msg.wParam = wparam
    return msg


def run_filter(m, msg, event_type=b"windows_generic_MSG"):
    return m.nativeEventFilter(event_type, ctypes.addressof(msg))


mgr = HotkeyManager(register_fn=lambda *a: True, unregister_fn=lambda *a: True)
mgr.register("Ctrl+Alt+L")
got = []
mgr.pressed.connect(lambda: got.append(1))

res = run_filter(mgr, make_msg(WM_HOTKEY, HOTKEY_ID))
check("WM_HOTKEY 且 id 匹配: 发出 pressed", got == [1], str(got))
check("过滤器从不吞消息(返回 (False, 0))", res == (False, 0), str(res))

got.clear()
run_filter(mgr, make_msg(0x0100, HOTKEY_ID))  # WM_KEYDOWN
check("其他消息(WM_KEYDOWN)不发信号", got == [])
run_filter(mgr, make_msg(0x0002, HOTKEY_ID))  # WM_DESTROY
check("其他消息(WM_DESTROY)不发信号", got == [])
run_filter(mgr, make_msg(WM_HOTKEY, HOTKEY_ID + 1))
check("WM_HOTKEY 但 id 不匹配: 不发信号", got == [])
run_filter(mgr, make_msg(WM_HOTKEY, HOTKEY_ID), event_type=b"windows_dispatcher_MSG")
check("eventType 不是 windows_generic_MSG: 不发信号", got == [])
run_filter(mgr, make_msg(WM_HOTKEY, HOTKEY_ID), event_type=QByteArray(b"xcb_generic_event_t"))
check("eventType 为别的 QByteArray: 不发信号", got == [])
run_filter(mgr, make_msg(WM_HOTKEY, HOTKEY_ID), event_type=QByteArray(b"windows_generic_MSG"))
check("eventType 为 QByteArray(windows_generic_MSG): 发信号(真实调用的形态)", got == [1], str(got))

got.clear()
mgr.unregister()
run_filter(mgr, make_msg(WM_HOTKEY, HOTKEY_ID))
check("已注销后迟到的 WM_HOTKEY 不再发信号", got == [])

check("过滤器遇到坏指针不抛异常", HotkeyManager().nativeEventFilter(b"windows_generic_MSG", 0) == (False, 0))

# ===================================================================== 4
# PetWindow glue (FakeSelf style)
class FakeSelf:
    _setup_lock_hotkey = PetWindow._setup_lock_hotkey
    _on_lock_hotkey_register_failed = PetWindow._on_lock_hotkey_register_failed
    _on_lock_hotkey_pressed = PetWindow._on_lock_hotkey_pressed
    _run_lock_hotkey = PetWindow._run_lock_hotkey

    def __init__(self, config=None, locked=False):
        self.config = {"lock_hotkey": "Ctrl+Alt+L"} if config is None else config
        self._locked = locked
        self._tray = MagicMock()
        self.calls = []

    def _force_unlock(self):
        self.calls.append("force_unlock")

    def _toggle_lock(self, checked):
        self.calls.append(("toggle_lock", checked))


real_manager = pet_window.HotkeyManager
try:
    # 4a: registration refused -> warning path, no exception
    pet_window.HotkeyManager = lambda: HotkeyManager(
        register_fn=lambda *a: False, unregister_fn=lambda *a: True, get_last_error=lambda: 1409
    )
    LOG_LINES.clear()
    f = FakeSelf()
    try:
        f._setup_lock_hotkey()
        raised = None
    except Exception as e:  # noqa: BLE001
        raised = e
    check("PetWindow: 热键注册失败不抛异常、不影响启动", raised is None, repr(raised))
    check("PetWindow: 注册失败写了一行 dialogue_log", len(LOG_LINES) == 1 and "注册失败" in LOG_LINES[0] and "1409" in LOG_LINES[0], str(LOG_LINES))
    check("PetWindow: 注册失败弹了一条托盘提示", f._tray.showMessage.call_count == 1)
    args = f._tray.showMessage.call_args[0]
    check("PetWindow: 提示文字正确", args[1] == "锁定热键 Ctrl+Alt+L 注册失败，可能被其他软件占用", str(args))
    check("PetWindow: 提示用 Warning 图标", args[2] == QSystemTrayIcon.MessageIcon.Warning)

    # 4b: spec in config is garbage -> same warning path
    LOG_LINES.clear()
    f = FakeSelf(config={"lock_hotkey": "Ctrl+Nope"})
    f._setup_lock_hotkey()
    check("PetWindow: 配置写错也走提示分支且文字带上配置值",
          f._tray.showMessage.call_count == 1 and "Ctrl+Nope" in f._tray.showMessage.call_args[0][1])

    # 4c: success -> no warning
    pet_window.HotkeyManager = lambda: HotkeyManager(register_fn=lambda *a: True, unregister_fn=lambda *a: True)
    LOG_LINES.clear()
    f = FakeSelf()
    f._setup_lock_hotkey()
    check("PetWindow: 注册成功不弹提示、不写日志", f._tray.showMessage.call_count == 0 and LOG_LINES == [], str(LOG_LINES))
    check("PetWindow: 注册成功后管理器处于已注册状态", f._lock_hotkey.is_registered is True)

    # 4d: constructing the manager blows up -> still no exception
    def boom():
        raise RuntimeError("no user32")

    pet_window.HotkeyManager = boom
    f = FakeSelf()
    try:
        f._setup_lock_hotkey()
        raised = None
    except Exception as e:  # noqa: BLE001
        raised = e
    check("PetWindow: 管理器构造失败也不抛异常", raised is None, repr(raised))
    check("PetWindow: 管理器构造失败也走提示分支", f._tray.showMessage.call_count == 1)
finally:
    pet_window.HotkeyManager = real_manager

# 4e: pressed -> work is deferred to the event loop, then locked -> force unlock, unlocked -> lock
f = FakeSelf(locked=True)
f._on_lock_hotkey_pressed()
check("热键按下: 信号处理里不直接执行(已排进事件循环)", f.calls == [], str(f.calls))
pump()
check("热键按下(已锁定): 事件循环后执行 _force_unlock", f.calls == ["force_unlock"], str(f.calls))

f = FakeSelf(locked=False)
f._on_lock_hotkey_pressed()
check("热键按下: 未锁定时也先不直接执行", f.calls == [], str(f.calls))
pump()
check("热键按下(未锁定): 事件循环后执行 _toggle_lock(True)", f.calls == [("toggle_lock", True)], str(f.calls))

# ===================================================================== 5
# defaults + "no keyboard hook" guard
check("config 默认 lock_hotkey 为 Ctrl+Alt+L", config_mod.DEFAULTS.get("lock_hotkey") == "Ctrl+Alt+L")
check("默认热键能被解析", parse_hotkey(config_mod.DEFAULTS["lock_hotkey"]) == (CA, 0x4C))

import ast  # noqa: E402

tree = ast.parse(Path(hotkey.__file__).read_text(encoding="utf-8"))
used = set()
for node in ast.walk(tree):  # imports and called/attribute names only -- docstring prose doesn't count
    if isinstance(node, ast.Import):
        used.update(a.name for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        used.add(node.module or "")
        used.update(a.name for a in node.names)
    elif isinstance(node, ast.Attribute):
        used.add(node.attr)
    elif isinstance(node, ast.Name):
        used.add(node.id)
for banned in ("SetWindowsHookEx", "SetWindowsHookExW", "pynput", "keyboard", "GetAsyncKeyState", "GetKeyState"):
    check(f"hotkey.py 代码里没有 {banned}", banned not in used)
check("hotkey.py 只用 RegisterHotKey/UnregisterHotKey", {"RegisterHotKey", "UnregisterHotKey"} <= used)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("全局热键用例全部通过")
sys.exit(0)
