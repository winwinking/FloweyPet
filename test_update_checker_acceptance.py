# -*- coding: utf-8 -*-
"""Acceptance tests for the update check: update_checker's pure functions
(parse_version / is_newer / parse_manifest / fetch / should_announce), the
UpdateChecker QObject (fake fetchers only -- nothing here touches the
network), DialogueManager.trigger_update_announce, and PetWindow's
_on_update_available / _toggle_update_check on a stand-in object.

Run:  QT_QPA_PLATFORM=offscreen python test_update_checker_acceptance.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

TMP = Path(tempfile.mkdtemp(prefix="floweypet_upd_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import app_log  # noqa: E402

app_log.LOG_PATH = TMP / "app.log"

import config as config_mod  # noqa: E402
import update_checker as uc  # noqa: E402
import version  # noqa: E402
from dialogue_manager import DialogueManager  # noqa: E402
from pet_window import PetWindow  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


# ------------------------------------------------------------------ 1
check("parse_version 正常", uc.parse_version("3.2.0") == (3, 2, 0))
check("parse_version 单段", uc.parse_version("7") == (7,))
for bad in ("v3.2.0", "3.2.x", "3..2", "", " 3.2.0", "3.2.0 ", "3.2.0-beta", "abc", None, 3, ["3"], "３.２"):
    check(f"parse_version 非法 {bad!r} -> None", uc.parse_version(bad) is None)
check("is_newer: 更新", uc.is_newer("3.2.0", "3.1.1"))
check("is_newer: 相等 False", not uc.is_newer("3.1.1", "3.1.1"))
check("is_newer: 更旧 False", not uc.is_newer("3.0.9", "3.1.1"))
check("is_newer: 位数不同相等 (3.2 vs 3.2.0) False", not uc.is_newer("3.2", "3.2.0") and not uc.is_newer("3.2.0", "3.2"))
check("is_newer: 位数不同更新 (3.2.1 vs 3.2)", uc.is_newer("3.2.1", "3.2") and uc.is_newer("4", "3.9.9"))
check("is_newer: 数值比较而不是字符串 (3.10 > 3.9)", uc.is_newer("3.10", "3.9"))
check("is_newer: 非数字 False", not uc.is_newer("abc", "3.1.1") and not uc.is_newer("3.2.0", "abc"))
check("is_newer: None False", not uc.is_newer(None, "3.1.1") and not uc.is_newer("3.2.0", None))

# ------------------------------------------------------------------ 2
def manifest(**kw):
    base = {"version": "3.2.0"}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False).encode("utf-8")


for label, raw in (("坏 JSON", b"{not json"), ("非对象(数组)", b"[1,2]"), ("非对象(字符串)", b'"3.2.0"'),
                   ("空字节", b""), ("无效 UTF-8", b"\xff\xfe\x00"), ("缺 version", b'{"url": "x"}'),
                   ("version 非法", b'{"version": "latest"}'), ("version 非字符串", b'{"version": 3}')):
    try:
        r = uc.parse_manifest(raw)
        ok = r is None
    except Exception as e:  # noqa: BLE001
        r, ok = repr(e), False
    check(f"parse_manifest {label} -> None 且不抛异常", ok, str(r))
check("parse_manifest None 输入也不抛", uc.parse_manifest(None) is None)

m = uc.parse_manifest(manifest(url=uc.ALLOWED_URL_PREFIX + "releases/tag/v3.2.0"))
check("parse_manifest: 允许前缀内的 url 保留", m["url"] == uc.ALLOWED_URL_PREFIX + "releases/tag/v3.2.0")
for label, url in (("别的域名", "https://evil.example.com/FloweyPet/"), ("http://", "http://github.com/winwinking/FloweyPet/x"),
                   ("相似前缀", "https://github.com/winwinking/FloweyPet-evil/x"), ("非字符串", 123),
                   ("file://", "file:///C:/x")):
    m = uc.parse_manifest(manifest(url=url))
    check(f"parse_manifest url {label} -> 默认 Releases 页", m["url"] == uc.DEFAULT_RELEASE_URL, str(m))
m = uc.parse_manifest(manifest())
check("parse_manifest 无 url -> 默认 Releases 页", m["url"] == uc.DEFAULT_RELEASE_URL)
check("parse_manifest 无 line/face -> None", m["line"] is None and m["face"] is None)
for blank in ("", "   ", "\n\t", None, 5):
    check(f"parse_manifest line {blank!r} -> None", uc.parse_manifest(manifest(line=blank))["line"] is None)
check("parse_manifest line 去首尾空白", uc.parse_manifest(manifest(line="  你好  "))["line"] == "你好")
check("parse_manifest line 超长截断到 200", len(uc.parse_manifest(manifest(line="长" * 500))["line"]) == 200)
m = uc.parse_manifest(manifest(line="新版本来啦，快去看看～", face="wink"))
check("parse_manifest 中文 line 与 face 正常", m["line"] == "新版本来啦，快去看看～" and m["face"] == "wink" and m["version"] == "3.2.0")
check("parse_manifest face 空串/非字符串 -> None",
      uc.parse_manifest(manifest(face=""))["face"] is None and uc.parse_manifest(manifest(face=7))["face"] is None)
check("parse_manifest 接受 utf-8 带中文的原始 bytes", uc.parse_manifest('{"version":"3.2.0","line":"好"}'.encode("utf-8"))["line"] == "好")

# ------------------------------------------------------------------ 3
for bad_url in ("ftp://example.com/version.json", "http://example.com/version.json", "javascript:alert(1)", "C:\\x.json", "", None):
    try:
        uc.fetch(bad_url)
        ok = False
    except ValueError:
        ok = True
    except Exception as e:  # noqa: BLE001
        ok = False
    check(f"fetch 拒绝 {bad_url!r}(ValueError)", ok)
check("测试在源码模式运行(IS_DEV)", uc.IS_DEV is True)
local = TMP / "version.json"
local.write_bytes(manifest(line="本地验证"))
check("fetch 源码运行时 file:// 能读本地临时文件", uc.parse_manifest(uc.fetch(local.as_uri()))["line"] == "本地验证")
big = TMP / "big.json"
big.write_bytes(b"x" * (200 * 1024))
check("fetch 最多读 64KB", len(uc.fetch(big.as_uri())) == 64 * 1024)
uc.IS_DEV = False
try:
    try:
        uc.fetch(local.as_uri())
        ok = False
    except ValueError:
        ok = True
finally:
    uc.IS_DEV = True
check("fetch 非源码运行时 file:// 被拒绝", ok)
seen_ua = {}
import urllib.request  # noqa: E402


class FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        return b"{}"


class FakeOpener:
    def open(self, req, timeout=None):
        seen_ua["ua"] = req.get_header("User-agent")
        seen_ua["method"] = req.get_method()
        seen_ua["data"] = req.data
        seen_ua["timeout"] = timeout
        return FakeResp()


real_build = urllib.request.build_opener
urllib.request.build_opener = lambda *h: FakeOpener()
try:
    uc.fetch("https://example.com/v.json", timeout=7)
finally:
    urllib.request.build_opener = real_build
check("fetch: User-Agent 为 FloweyPet/<版本>", seen_ua.get("ua") == f"FloweyPet/{version.VERSION}", str(seen_ua))
check("fetch: 是不带数据的 GET,超时按参数", seen_ua.get("method") == "GET" and seen_ua.get("data") is None and seen_ua.get("timeout") == 7)

# ------------------------------------------------------------------ 4
class Fetcher:
    def __init__(self, result=None, exc=None):
        self.result, self.exc, self.calls = result, exc, []

    def __call__(self, url, *a, **k):
        self.calls.append(url)
        if self.exc:
            raise self.exc
        return self.result


def cfg(**kw):
    c = {"check_updates": True, "update_url": "https://example.invalid/version.json",
         "update_check_hours": 3, "update_first_delay_seconds": 8}
    c.update(kw)
    return c


def run_checker(fetcher, config=None):
    ck = uc.UpdateChecker(config or cfg(), fetcher=fetcher, use_thread=False)
    got = []
    ck.update_available.connect(got.append)
    ck.check_now()
    return ck, got


f = Fetcher(exc=OSError("network down"))
try:
    ck, got = run_checker(f)
    raised = False
except Exception:  # noqa: BLE001
    raised, got = True, []
check("fetcher 抛异常: 不抛、不发信号", not raised and got == [] and ck.available is None)
check("fetcher 抛异常: app.log 记了一行", app_log.LOG_PATH.exists() and "检查更新失败" in app_log.LOG_PATH.read_text(encoding="utf-8"))
ck.check_now()
check("失败后可以再次检查(不会卡在检查中)", len(f.calls) == 2)

for label, raw in (("坏 JSON", b"{oops"), ("缺 version", b"{}"), ("空", b"")):
    ck, got = run_checker(Fetcher(result=raw))
    check(f"fetcher 返回{label}: 不发信号", got == [] and ck.available is None)

ck, got = run_checker(Fetcher(result=manifest(version="99.0.0", line="来了", face="wink")))
check("版本更新: 发出一次信号", len(got) == 1, str(got))
check("版本更新: 信号内容与 available 正确",
      got and got[0]["version"] == "99.0.0" and got[0]["line"] == "来了" and got[0]["face"] == "wink"
      and got[0]["url"] == uc.DEFAULT_RELEASE_URL and ck.available == got[0])
for label, ver in (("相同", version.VERSION), ("更旧", "0.0.1")):
    ck, got = run_checker(Fetcher(result=manifest(version=ver)))
    check(f"版本{label}: 不发信号", got == [] and ck.available is None)

f = Fetcher(result=manifest(version="99.0.0"))
ck, got = run_checker(f, cfg(update_url=""))
check("update_url 为空: 不调用 fetcher", f.calls == [] and got == [])

# overlapping checks don't stack
calls = []
ck = uc.UpdateChecker(cfg(), fetcher=lambda url: calls.append(url) or manifest(version="99.0.0"), use_thread=False)
ck._checking = True
ck.check_now()
check("已有检查在进行中: check_now 直接返回", calls == [])

# timers
f = Fetcher(result=manifest(version="99.0.0"))
ck = uc.UpdateChecker(cfg(check_updates=False), fetcher=f, use_thread=False)
ck.start()
check("check_updates=False: start() 后 fetcher 从未被调用", f.calls == [])
check("check_updates=False: start() 没有启动任何定时器", not ck._first_timer.isActive() and not ck._periodic_timer.isActive())
ck = uc.UpdateChecker(cfg(update_first_delay_seconds=8, update_check_hours=3), fetcher=f, use_thread=False)
ck.start()
check("check_updates=True: start() 启动首检与周期定时器", ck._first_timer.isActive() and ck._periodic_timer.isActive())
check("首检间隔 = update_first_delay_seconds", ck._first_timer.interval() == 8000, str(ck._first_timer.interval()))
check("周期间隔 = update_check_hours", ck._periodic_timer.interval() == 3 * 3600 * 1000, str(ck._periodic_timer.interval()))
check("start() 本身不立刻联网", f.calls == [])
ck.set_enabled(False)
check("set_enabled(False): 所有定时器停止", not ck._first_timer.isActive() and not ck._periodic_timer.isActive())
ck.set_enabled(True)
check("set_enabled(True): 5 秒后检查并恢复周期", ck._first_timer.isActive() and ck._first_timer.interval() == 5000 and ck._periodic_timer.isActive())
ck.set_enabled(False)
ck = uc.UpdateChecker(cfg(update_check_hours="x", update_first_delay_seconds=-1), fetcher=f, use_thread=False)
ck.start()
check("配置值非法: 回退到默认 8 秒 / 3 小时", ck._first_timer.interval() == 8000 and ck._periodic_timer.interval() == 3 * 3600 * 1000)
ck.set_enabled(False)

# real background thread
ck = uc.UpdateChecker(cfg(), fetcher=Fetcher(result=manifest(version="99.0.0")), use_thread=True)
got = []
ck.update_available.connect(got.append)
ck.check_now()
deadline = time.time() + 5
while not got and time.time() < deadline:
    app.processEvents()
    time.sleep(0.01)
check("后台线程: 结果通过信号回到主线程", len(got) == 1 and got[0]["version"] == "99.0.0")
check("后台线程: 检查结束后 _checking 复位", ck._checking is False)

# ------------------------------------------------------------------ 5
check("should_announce: 同版本 False", uc.should_announce({"last_announced_version": "3.2.0"}, "3.2.0") is False)
check("should_announce: 新版本 True", uc.should_announce({"last_announced_version": "3.2.0"}, "3.3.0") is True)
check("should_announce: 从未播报(None/缺失) True",
      uc.should_announce({"last_announced_version": None}, "3.2.0") and uc.should_announce({}, "3.2.0"))

# ------------------------------------------------------------------ 6
test_cfg = json.loads(json.dumps(config_mod.DEFAULTS))
dm = DialogueManager(test_cfg, lambda c: None, None)
emitted = []
dm.show_requested.connect(lambda segs, cat: emitted.append((segs, cat)))


def announce(*a, **k):
    emitted.clear()
    dm.busy = False
    dm.trigger_update_announce(*a, **k)
    return emitted[-1] if emitted else None


r = announce("9.9.9", "新版本 {new_ver} 来了", "wink")
check("announce: 有 line 时发出 update_available", r is not None and r[1] == "update_available")
check("announce: {new_ver} 被替换", r and r[0][0]["text"] == "新版本 9.9.9 来了", str(r))
check("announce: 合法 face 保留", r and r[0][0]["face"] == "wink")
r = announce("9.9.9", "hi", "no_such_face_folder")
check("announce: 非法 face 回退 neutral", r and r[0][0]["face"] == "neutral")
r = announce("9.9.9", "hi", None)
check("announce: face 为空回退 neutral", r and r[0][0]["face"] == "neutral")
r = announce("9.9.9", "hi", "../faces")
for evil_face in ("../faces", "..", ".", "neutral/../neutral", "a\b"):
    r = announce("9.9.9", "hi", evil_face)
    check(f"announce: 带路径的 face {evil_face!r} 不被接受,回退 neutral", r and r[0][0]["face"] == "neutral", str(r))
pool_texts = [e["text"] for e in dm.data["update_available"]]
FACES_DIR_T = Path(__file__).resolve().parent / "assets" / "faces"


def _faces_valid(face):
    names = [face] if isinstance(face, str) else face
    return isinstance(names, list) and bool(names) and all(
        isinstance(n, str) and (FACES_DIR_T / n).is_dir() for n in names)


check("dialogues_system.json 的 update_available 池恰好 8 条,无占位,face 都合法",
      len(pool_texts) == 8 and not any("占位" in t for t in pool_texts)
      and all(_faces_valid(e.get("face")) for e in dm.data["update_available"]))
seen_texts = set()
for _ in range(12):
    r = announce("4.5.6", None, None)
    check_ok = r is not None and r[1] == "update_available"
    if not check_ok:
        break
    seen_texts.add(r[0][0]["text"])
check("announce: line 为空时走 update_available 池", check_ok and seen_texts and all(t in [p.replace("{new_ver}", "4.5.6").replace("{ver}", version.VERSION) for p in pool_texts] for t in seen_texts), str(seen_texts))
check("announce: 池里的 {new_ver} 与 {ver} 都被替换", all("{" not in t for t in seen_texts) and any("4.5.6" in t for t in seen_texts))
check("announce: 池条目 face 合法", bool(r) and _faces_valid(r[0][0]["face"]))
dm.busy = True
emitted.clear()
dm.trigger_update_announce("9.9.9", "排队")
queued = [c for (_, c, _) in dm._queue]
check("announce: 正在说话时排队而不是丢弃", "update_available" in queued and emitted == [])
dm.busy = False
dm._queue.clear()
dm.data.pop("update_available")
try:
    emitted.clear()
    dm.trigger_update_announce("9.9.9")
    ok = emitted == []
except Exception:  # noqa: BLE001
    ok = False
check("announce: 池缺失且没有 line 时静默不报错", ok)

# ------------------------------------------------------------------ 7
class FakeWin:
    _on_update_available = PetWindow._on_update_available
    _toggle_update_check = PetWindow._toggle_update_check
    attach_update_checker = PetWindow.attach_update_checker

    def __init__(self, visible=True, quitting=False, last=None):
        self._visible, self._pending_quit = visible, quitting
        self.config = {"last_announced_version": last, "check_updates": True}
        self.saved = []
        self.dm = MagicMock()
        self.stats = MagicMock()
        self._tray = MagicMock()
        self._update_info = None
        self._update_checker = None

    def isVisible(self):
        return self._visible

    def _build_menu(self):
        return ("menu", self._update_info)

    def save_config(self, c):
        self.saved.append(dict(c))


info = {"version": "9.9.9", "url": uc.DEFAULT_RELEASE_URL, "line": "新", "face": "wink"}
w = FakeWin()
w._on_update_available(info)
check("_on_update_available: 保存 info 并重建托盘菜单", w._update_info == info and w._tray.setContextMenu.call_args[0][0] == ("menu", info))
check("_on_update_available: 可见时播报,参数来自 info",
      w.dm.trigger_update_announce.call_args == (("9.9.9", "新", "wink"),))
check("_on_update_available: 记录并保存 last_announced_version",
      w.config["last_announced_version"] == "9.9.9" and w.saved and w.saved[-1]["last_announced_version"] == "9.9.9")
w = FakeWin(last="9.9.9")
w._on_update_available(info)
check("_on_update_available: 同版本已播报过则不再说,但菜单项仍在",
      not w.dm.trigger_update_announce.called and w._update_info == info and w._tray.setContextMenu.called)
for label, kw in (("不可见", {"visible": False}), ("正在退出", {"quitting": True})):
    w = FakeWin(**kw)
    w._on_update_available(info)
    check(f"_on_update_available: {label}时不说也不记录",
          not w.dm.trigger_update_announce.called and w.config["last_announced_version"] is None and w.saved == [])
    check(f"_on_update_available: {label}时托盘菜单仍更新", w._update_info == info and w._tray.setContextMenu.called)

w = FakeWin()
stub = MagicMock()
w.attach_update_checker(stub)
check("attach_update_checker: 保存引用并连接信号", w._update_checker is stub and stub.update_available.connect.called)
w._toggle_update_check(False)
check("_toggle_update_check: 记录互动、写 config、保存、通知 checker",
      w.stats.record_interaction.called and w.config["check_updates"] is False and w.saved[-1]["check_updates"] is False
      and stub.set_enabled.call_args == ((False,),))
w._toggle_update_check(True)
check("_toggle_update_check(True)", w.config["check_updates"] is True and stub.set_enabled.call_args == ((True,),))
w2 = FakeWin()
try:
    w2._toggle_update_check(False)
    ok = w2.config["check_updates"] is False
except Exception:  # noqa: BLE001
    ok = False
check("_toggle_update_check: 还没挂 checker 时不报错", ok)

# ------------------------------------------------------------------ 8
d = config_mod.DEFAULTS
check("DEFAULTS: check_updates 默认 True", d.get("check_updates") is True)
check("DEFAULTS: update_url 是 raw.githubusercontent 的 version.json",
      d.get("update_url") == "https://raw.githubusercontent.com/winwinking/FloweyPet/master/version.json")
check("DEFAULTS: 间隔 3 小时 / 首检延迟 8 秒 / last_announced_version 为 None",
      d.get("update_check_hours") == 3 and d.get("update_first_delay_seconds") == 8 and "last_announced_version" in d
      and d["last_announced_version"] is None)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("检查更新用例全部通过")
sys.exit(0)
