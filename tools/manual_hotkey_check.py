# -*- coding: utf-8 -*-
"""Manual check of the REAL global hotkey on the real desktop (the automated
tests only use fakes, and offscreen can't exercise RegisterHotKey).

Run from the repo root, with Flowey itself NOT running (a running Flowey that
has the same hotkey registered would make this check fail with error 1409):

    python tools/manual_hotkey_check.py                 # Ctrl+Alt+L, waits 20 s for you to press it
    python tools/manual_hotkey_check.py "Ctrl+Shift+F9"  # try another combination
    python tools/manual_hotkey_check.py --no-wait        # only the register / duplicate-register check

What it does:
  1. registers the hotkey for real (RegisterHotKey, thread level);
  2. tries to register the same combination a second time from another
     manager -- Windows must refuse it (error 1409, ERROR_HOTKEY_ALREADY_REGISTERED),
     which is exactly the "taken by another program" failure Flowey warns about;
  3. unless --no-wait, waits for you to press the combination and prints
     one line per press (try it while another window, e.g. a game, has focus);
  4. unregisters and exits. Exit code 0 only if step 1 and 2 behaved.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from hotkey import HotkeyManager, parse_hotkey  # noqa: E402

WAIT_SECONDS = 20


def main(argv):
    no_wait = "--no-wait" in argv
    args = [a for a in argv if not a.startswith("--")]
    spec = args[0] if args else "Ctrl+Alt+L"

    print(f"hotkey spec: {spec!r} -> parsed: {parse_hotkey(spec)}")
    app = QApplication(sys.argv[:1])

    mgr = HotkeyManager()
    app.installNativeEventFilter(mgr)
    presses = []
    mgr.pressed.connect(lambda: (presses.append(time.time()), print(f"  pressed #{len(presses)}  @ {time.strftime('%H:%M:%S')}")))

    ok1 = mgr.register(spec)
    print(f"1. register: {'OK' if ok1 else 'FAILED'}  (last_error={mgr.last_error})")

    other = HotkeyManager(hotkey_id=0x4C4C)  # different id, same combination
    ok2 = other.register(spec)
    print(f"2. duplicate register (must be refused): {'refused, as expected' if not ok2 else 'ACCEPTED (unexpected!)'}  (last_error={other.last_error}; 1409 = already registered)")
    other.unregister()

    good = ok1 and not ok2 and other.last_error == 1409
    if ok1 and not no_wait:
        print(f"3. press {spec} now -- waiting {WAIT_SECONDS} s ...")
        QTimer.singleShot(WAIT_SECONDS * 1000, app.quit)
        app.exec()
        print(f"   total presses seen: {len(presses)}")
        good = good and len(presses) > 0
        if not presses:
            print("   (no press seen -- if you did press it, something else may be swallowing the combination)")
    mgr.unregister()
    print("RESULT:", "OK" if good else "PROBLEM")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
