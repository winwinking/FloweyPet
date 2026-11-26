# -*- coding: utf-8 -*-
"""Acceptance tests for the anti-cheat shadow value following legitimate
clicks (SaveTamperGuard.note_click, called from
DialogueManager.record_interaction) and the one-time catch-up migration
anticheat.migrate_shadow_sync (config key shadow_sync_schema).

The shadow value is always a fake in-memory store injected into the code
under test; the real registry is never touched (and a tripwire on winreg
proves it).

Run:  QT_QPA_PLATFORM=offscreen python test_shadow_sync_acceptance.py
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="floweypet_shadow_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT / "src"))

import winreg  # noqa: E402

REGISTRY_CALLS = []
_orig_create, _orig_set = winreg.CreateKey, winreg.SetValueEx


def _trip_create(*a, **k):
    REGISTRY_CALLS.append("CreateKey")
    raise OSError("tripwire: tests must not write the real registry")


def _trip_set(*a, **k):
    REGISTRY_CALLS.append("SetValueEx")
    raise OSError("tripwire: tests must not write the real registry")


winreg.CreateKey, winreg.SetValueEx = _trip_create, _trip_set

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import anticheat  # noqa: E402
import config as config_mod  # noqa: E402
from dialogue_manager import DialogueManager  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


class Store:
    """In-memory stand-in for the registry value."""

    def __init__(self, value=None, fail_write=None, silent_drop=False):
        self.value = value
        self.writes = []
        self.fail_write = fail_write  # exception class to raise
        self.silent_drop = silent_drop  # write "succeeds" but nothing is stored (registry refused quietly)

    def read(self):
        return self.value

    def write(self, v):
        self.writes.append(v)
        if self.fail_write:
            raise self.fail_write("injected")
        if not self.silent_drop:
            self.value = int(v)


def make_dm(total, store):
    cfg = json.loads(json.dumps(config_mod.DEFAULTS))
    cfg["total_clicks"] = total
    saved = []
    dm = DialogueManager(cfg, lambda c: saved.append(c["total_clicks"]), None)
    dm.tamper_guard = anticheat.SaveTamperGuard(store.read, store.write)
    return dm, cfg, saved


# ------------------------------------------------------------------ 1
print("== 1 每次点击同步影子值 ==")
store = Store(10)
dm, cfg, saved = make_dm(10, store)
dm.record_interaction()
check("点击一次: total_clicks=11", cfg["total_clicks"] == 11)
check("点击一次后影子值等于新的 total_clicks", store.value == 11 and store.writes == [11], repr(store.writes))
check("先保存 config 再写影子值 (config 已保存为 11)", saved and saved[-1] == 11)
for _ in range(1499):
    dm.record_interaction()
check("共点 1500 次: total_clicks=1510", cfg["total_clicks"] == 1510)
check("影子值始终跟着: 1510", store.value == 1510 and len(store.writes) == 1500)
guard = anticheat.SaveTamperGuard(store.read, store.write)
check("连点 1500 次之后启动检查: 不判定篡改", guard.check(cfg) is None)
check("检查本身没有再写影子值", len(store.writes) == 1500)
stale = Store(10)  # what the old behaviour left behind
check("对照: 老行为 (影子值停在 10) 的同一份存档会被判篡改 (mod_a)",
      (lambda v: v is not None and v.kind == "mod_a")(anticheat.SaveTamperGuard(stale.read, stale.write).check(dict(cfg))))
store = Store(None)
dm, cfg, saved = make_dm(0, store)
dm.record_interaction()
check("影子值不存在时点击也会写入它", store.value == 1)
store = Store(5)
dm, cfg, saved = make_dm(5, store)
for _ in range(3):
    dm.record_interaction()
check("note_click 写的是 int", all(isinstance(w, int) for w in store.writes) and store.writes == [6, 7, 8])
g = anticheat.SaveTamperGuard(store.read, store.write)
g.note_click(100)
check("SaveTamperGuard.note_click 直接调用写入", store.value == 100)

# ------------------------------------------------------------------ 2
print("== 2 写影子值失败不影响点击 ==")
for exc in (OSError, PermissionError, RuntimeError, ValueError):
    store = Store(10, fail_write=exc)
    dm, cfg, saved = make_dm(10, store)
    try:
        dm.record_interaction()
        dm.record_interaction()
        raised = False
    except BaseException:  # noqa: BLE001
        raised = True
    check(f"写影子值抛 {exc.__name__}: 点击不抛异常", raised is False)
    check(f"写影子值抛 {exc.__name__}: 点击照常计数并保存", cfg["total_clicks"] == 12 and saved[-1] == 12 and len(store.writes) == 2)
store = Store(10, silent_drop=True)
dm, cfg, saved = make_dm(10, store)
dm.record_interaction()
check("写入被静默丢弃: 点击照常计数", cfg["total_clicks"] == 11 and store.value == 10)
check("真实 write_shadow_clicks 在注册表被拒时也不抛 (OSError 被吞)",
      config_mod.write_shadow_clicks(5) is None)

# ------------------------------------------------------------------ 3
print("== 3 一次性迁移 ==")
M = anticheat.migrate_shadow_sync


def cfg_with(total, schema=None):
    c = {"total_clicks": total}
    if schema is not None:
        c["shadow_sync_schema"] = schema
    return c


for label, c0 in (("没有记号", cfg_with(321)), ("记号 1", cfg_with(321, 1))):
    store = Store(84)
    c = dict(c0)
    saves, logs = [], []
    r = M(c, store.read, store.write, save=lambda cc: saves.append(dict(cc)), log=logs.append)
    check(f"影子值 84、点击 321、{label} -> 影子值变 321", store.value == 321 and r == "raised", f"{r} {store.value}")
    check(f"{label}: 记号变 2 并保存一次", c["shadow_sync_schema"] == 2 and len(saves) == 1 and saves[0]["shadow_sync_schema"] == 2)
    check(f"{label}: 点击数没动", c["total_clicks"] == 321)
    check(f"{label}: 记一行日志写明旧影子值和点击数", len(logs) == 1 and "84" in logs[0] and "321" in logs[0], repr(logs))
    r2 = M(c, store.read, store.write, save=lambda cc: saves.append(dict(cc)), log=logs.append)
    check(f"{label}: 再启动一次不再动", r2 == "skipped" and store.writes == [321] and len(saves) == 1 and len(logs) == 1)
store = Store(None)
c = cfg_with(321)
saves, logs = [], []
r = M(c, store.read, store.write, save=saves.append, log=logs.append)
check("影子值不存在 -> 不动, 不创建", store.value is None and store.writes == [] and r == "kept")
check("影子值不存在: 记号 2, 保存一次, 不写日志", c["shadow_sync_schema"] == 2 and len(saves) == 1 and logs == [])
store = Store(500)
c = cfg_with(321)
saves = []
r = M(c, store.read, store.write, save=saves.append)
check("影子值 500、点击 321 -> 不动影子值 (交给原有篡改判定)", store.value == 500 and store.writes == [] and r == "kept")
check("影子值 500: 记号 2", c["shadow_sync_schema"] == 2 and len(saves) == 1)
store = Store(321)
c = cfg_with(321)
r = M(c, store.read, store.write)
check("影子值等于点击数 -> 不动, 记号 2", store.writes == [] and c["shadow_sync_schema"] == 2 and r == "kept")
store = Store(0)
c = cfg_with(0)
r = M(c, store.read, store.write)
check("点击 0、影子值 0 -> 不动", store.writes == [] and c["shadow_sync_schema"] == 2)
store = Store(84, silent_drop=True)
c = cfg_with(321)
saves, logs = [], []
r = M(c, store.read, store.write, save=saves.append, log=logs.append)
check("写入没成功 (读回对不上) -> 不盖章, 下次再试", r == "write_failed" and "shadow_sync_schema" not in c and saves == [], f"{r} {c}")
check("写入没成功: 日志说明", logs and "没有成功" in logs[0], repr(logs))
store = Store(84, fail_write=OSError)
c = cfg_with(321)
r = M(c, store.read, store.write)
check("写入抛异常 -> 不抛出, 不盖章", r == "write_failed" and "shadow_sync_schema" not in c)
store = Store(84)
store.fail_write = None
c = cfg_with(321)
r = M(c, store.read, store.write)
check("下次启动 (写入恢复) 重试成功", r == "raised" and store.value == 321 and c["shadow_sync_schema"] == 2)
for bad in (True, "321", None, 3.5):
    store = Store(84)
    c = {"total_clicks": bad}
    r = M(c, store.read, store.write)
    check(f"total_clicks={bad!r} 不是整数 -> 不动影子值", store.writes == [] and r == "invalid")
for bad_shadow in (True, "84"):
    store = Store(bad_shadow)
    c = cfg_with(321)
    r = M(c, store.read, store.write)
    check(f"影子值={bad_shadow!r} 不是整数 -> 不动", store.writes == [] and r == "kept")
c = cfg_with(321, 5)
store = Store(1)
check("记号大于 2 也当作已完成", M(c, store.read, store.write) == "skipped" and store.writes == [])
check("save/log 都可以不传", M(cfg_with(5), Store(1).read, Store(1).write) in ("raised", "write_failed"))
check("DEFAULTS 里 shadow_sync_schema 默认 1", config_mod.DEFAULTS["shadow_sync_schema"] == 1)
loaded = config_mod._deep_merge(config_mod.DEFAULTS, {"total_clicks": 321})
check("老 config (没有记号) 加载后记号为 1 -> 会执行迁移", loaded["shadow_sync_schema"] == 1)

# ------------------------------------------------------------------ 4
print("== 4 迁移在 check 之前 ==")
calls = []


class Spy(Store):
    def read(self):
        calls.append("read")
        return super().read()

    def write(self, v):
        calls.append(f"write:{v}")
        super().write(v)


store = Spy(84)
c = cfg_with(321)
M(c, store.read, store.write)
calls.append("-- startup finished, pet appears --")
guard = anticheat.SaveTamperGuard(store.read, store.write)
verdict = guard.check(c)
check("注入记录: 迁移的读写发生在 check 之前", calls.index("write:321") < calls.index("-- startup finished, pet appears --"), repr(calls))
check("迁移之后 check 看到的是同步后的影子值: 不判篡改", verdict is None)
store = Store(84)
c = cfg_with(321)
verdict_without = anticheat.SaveTamperGuard(store.read, store.write).check(dict(c))
check("对照: 没有迁移直接 check, 差值 237 < 1000 也不判篡改 (迁移是为了以后)", verdict_without is None)
src = (ROOT / "main.py").read_text(encoding="utf-8")
i_mig = src.index("migrate_shadow_sync(config")
check("main.py: 迁移在旧数据迁移 (migrate_legacy_data) 之后", i_mig > src.index("result = migrate_legacy_data("))
check("main.py: 迁移在 apply_size_settings 之后、_session_start 之前", src.index("apply_size_settings(config)") < i_mig < src.index('config["_session_start"]'))
check("main.py: 迁移在创建 DialogueManager 之前 (check 由它在开场时执行)", i_mig < src.index("dm = DialogueManager("))
check("main.py: 迁移在创建窗口之前", i_mig < src.index("window = PetWindow("))
dm_src = (ROOT / "src" / "dialogue_manager.py").read_text(encoding="utf-8")
init_src = dm_src[dm_src.index("def __init__"):dm_src.index("def _pool")]
check("DialogueManager.__init__ 里没有调用 check (check 只在开场 trigger_intro 里)", "tamper_guard.check(" not in init_src
      and dm_src.count("tamper_guard.check(") == 1)
check("main.py: 临时目录里运行时跳过 (不碰共用的注册表)", "if not running_from_temp:" in src[i_mig - 200:i_mig])
check("main.py: 没有多用 raise _SkipStep (仍然正好两处)", src.count("raise _SkipStep") == 2)
check("main.py: 迁移放在 try/except 里, 出错不影响启动", re.search(r"migrate_shadow_sync\(config[^\n]*\n\s+except Exception:\s+log_exception\(\"migrate_shadow_sync\"\)", src) is not None)
check("main.py: 用的是真实的 read/write_shadow_clicks + save_config + log_message",
      "migrate_shadow_sync(config, read_shadow_clicks, write_shadow_clicks, save=save_config, log=log_message)" in src)

# ------------------------------------------------------------------ 5
print("== 5 篡改判定照常 ==")
store = Store(84)
c = cfg_with(321)
M(c, store.read, store.write)
check("前提: 迁移后影子值=321", store.value == 321)
c["total_clicks"] = 321 + 5000
guard = anticheat.SaveTamperGuard(store.read, store.write)
v = guard.check(c)
check("迁移完成后手动把 total_clicks 改大 5000 再启动 -> 判定篡改 (mod_a)", v is not None and v.kind == "mod_a" and not v.exempt, repr(v))
check("判定里的真实值是 321", v is not None and v.real_value == 321 and v.tampered_value == 5321)
guard.resolve(c, v)
check("resolve 把点击数纠正回 321, 影子值仍是 321", c["total_clicks"] == 321 and store.value == 321)
for label, tampered, kind in (("改成 0", 0, "mod_f"), ("改小", 150, "mod_d"), ("改成负数", -1, "neg3"), ("改成超长", 10 ** 11, "long1")):
    store = Store(321)
    c = cfg_with(tampered, 2)
    v = anticheat.SaveTamperGuard(store.read, store.write).check(c)
    check(f"迁移后 {label} -> {kind}", v is not None and v.kind == kind, repr(v))
store = Store(321)
c = cfg_with(321 + 999, 2)
check("差值不到 1000 仍不当作篡改", anticheat.SaveTamperGuard(store.read, store.write).check(c) is None)
# full cycle through the DialogueManager: migrate, click, restart -> consistent
store = Store(84)
cfg = json.loads(json.dumps(config_mod.DEFAULTS))
cfg["total_clicks"] = 321
M(cfg, store.read, store.write)
dm = DialogueManager(cfg, lambda c: None, None)
dm.tamper_guard = anticheat.SaveTamperGuard(store.read, store.write)
for _ in range(2500):
    dm.record_interaction()
check("整套流程: 迁移 -> 点击 2500 次 -> 重启检查 -> 不判篡改", anticheat.SaveTamperGuard(store.read, store.write).check(cfg) is None
      and store.value == cfg["total_clicks"] == 2821)
cfg["total_clicks"] += 5000
check("整套流程: 之后手动加 5000 -> 判篡改", anticheat.SaveTamperGuard(store.read, store.write).check(cfg) is not None)

# ------------------------------------------------------------------ 6
print("== 6 其他会改 total_clicks 的地方 / 安全 ==")
check("全程没有碰真实注册表 (tripwire 只响了第 2 节故意调用真实 write_shadow_clicks 的那一次, 且被拦住了)", REGISTRY_CALLS == ["CreateKey"], repr(REGISTRY_CALLS))
winreg.CreateKey, winreg.SetValueEx = _orig_create, _orig_set
check("record_interaction 是唯一 +1 的地方", len(re.findall(r'\["total_clicks"\]\s*=\s*self\.config\.get\("total_clicks", 0\)\s*\+\s*1', dm_src)) == 1)
check("anticheat.py 没有导入 error_log/config 等 (保持纯逻辑)", not re.search(r"^(?:from|import)\s+(?:error_log|config|paths|app_log)", (ROOT / "src" / "anticheat.py").read_text(encoding="utf-8"), re.M))
check("config.py 里过时的注释已更新", "Only the app's own legitimate click-increment path writes this" not in (ROOT / "src" / "config.py").read_text(encoding="utf-8"))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("影子值同步用例全部通过")
sys.exit(0)
