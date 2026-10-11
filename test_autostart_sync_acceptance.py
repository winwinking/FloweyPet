# -*- coding: utf-8 -*-
"""Acceptance tests for config.sync_autostart / _autostart_command /
_RUN_NAME. Every registry access is an injected fake -- the real registry is
never read or written.

Run:  python test_autostart_sync_acceptance.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("FLOWEYPET_DATA_DIR", tempfile.mkdtemp(prefix="floweypet_as_"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import config  # noqa: E402

FAILURES = []
EXPECTED = '"C:\\Games\\Flowey\\FloweyPet.exe"'


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


class Writer:
    def __init__(self, result=True, exc=None):
        self.calls = []
        self.result = result
        self.exc = exc

    def __call__(self, enabled):
        self.calls.append(enabled)
        if self.exc:
            raise self.exc
        return self.result


def sync(cfg, current, writer, is_dev=False, expected=EXPECTED, read_exc=None):
    def read():
        if read_exc:
            raise read_exc
        return current
    return config.sync_autostart(cfg, read_command=read, write=writer, expected=expected, is_dev=is_dev)


# ------------------------------------------------------------------ 1
w = Writer()
check("is_dev=True: 返回 skipped_dev", sync({"autostart": True}, None, w, is_dev=True) == "skipped_dev")
check("is_dev=True: write 没被调用", w.calls == [])

# ------------------------------------------------------------------ 2
for label, cfg in (("autostart 为 False", {"autostart": False}), ("autostart 缺失", {}),
                   ("autostart 为 None", {"autostart": None}), ("autostart 为非 True 的真值", {"autostart": "yes"})):
    for reg_label, reg in (("注册表为空", None), ("注册表里有旧路径", '"D:\\old\\FloweyPet.exe"')):
        w = Writer()
        r = sync(cfg, reg, w)
        check(f"{label}、{reg_label}: 返回 off 且 write 没被调用", r == "off" and w.calls == [], f"{r} {w.calls}")

# ------------------------------------------------------------------ 3
w = Writer()
r = sync({"autostart": True}, None, w)
check("autostart 为 True、注册表为 None: 返回 updated", r == "updated", r)
check("autostart 为 True、注册表为 None: write 调用一次且参数为 True", w.calls == [True], str(w.calls))

w = Writer()
r = sync({"autostart": True}, '"D:\\Elsewhere\\FloweyPet.exe"', w)
check("注册表指向另一个文件夹的 exe: 返回 updated 且 write(True)", r == "updated" and w.calls == [True], f"{r} {w.calls}")

w = Writer()
r = sync({"autostart": True}, "", w)
check("注册表为空字符串: 返回 updated", r == "updated" and w.calls == [True], r)

# ------------------------------------------------------------------ 4
for label, reg in (("大小写不同", EXPECTED.upper()), ("首尾有空格", "  " + EXPECTED + "\t "),
                   ("大小写和空格都不同", " " + EXPECTED.lower() + " "), ("完全相同", EXPECTED)):
    w = Writer()
    r = sync({"autostart": True}, reg, w)
    check(f"注册表与 expected {label}: 返回 ok 且 write 没被调用", r == "ok" and w.calls == [], f"{r} {w.calls}")
w = Writer()
r = sync({"autostart": True}, EXPECTED, w, expected="  " + EXPECTED.upper() + "  ")
check("expected 自带空白/大小写差异也算 ok", r == "ok" and w.calls == [], r)

# ------------------------------------------------------------------ 5
w = Writer(result=False)
check("write 返回 False: 返回 failed", sync({"autostart": True}, None, w) == "failed" and w.calls == [True])
w = Writer(exc=RuntimeError("boom"))
try:
    r, raised = sync({"autostart": True}, None, w), False
except Exception:  # noqa: BLE001
    r, raised = None, True
check("write 抛异常: 返回 failed 且不向外抛", r == "failed" and not raised, f"{r} {raised}")
w = Writer()
try:
    r, raised = sync({"autostart": True}, None, w, read_exc=OSError("denied")), False
except Exception:  # noqa: BLE001
    r, raised = None, True
check("read_command 抛异常: 返回 failed 且不向外抛", r == "failed" and not raised, f"{r} {raised}")
check("read_command 抛异常: write 没被调用", w.calls == [])
try:
    r = config.sync_autostart(None, read_command=lambda: None, write=Writer(), expected=EXPECTED, is_dev=False)
    check("config 参数异常(None): 不抛异常,返回 failed", r == "failed", r)
except Exception as e:  # noqa: BLE001
    check("config 参数异常(None): 不抛异常,返回 failed", False, repr(e))

# ------------------------------------------------------------------ 6
check("源码运行时 _RUN_NAME == FloweyPetDev", config._RUN_NAME == "FloweyPetDev", config._RUN_NAME)
cmd = config._autostart_command()
py = str(Path(sys.executable).resolve())
main_py = str((Path(config.__file__).resolve().parent.parent / "main.py"))
check("_autostart_command: 含 python 可执行文件路径且带引号", f'"{py}"' in cmd, cmd)
check("_autostart_command: 含 main.py 路径且带引号", f'"{main_py}"' in cmd, cmd)
check("_autostart_command: 就是 '\"python\" \"main.py\"' 两段", cmd == f'"{py}" "{main_py}"', cmd)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("开机自启同步用例全部通过")
sys.exit(0)
