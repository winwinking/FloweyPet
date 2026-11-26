# -*- coding: utf-8 -*-
"""Acceptance tests for 存档点 -> API 设置: api_key_store (the key lives in
its own file, never in config.json), the ApiSettingsPanel window, its menu
entry, the lines Flowey says afterwards, and "nothing typed ever reaches a
log". PetWindow is a MagicMock with the real methods bound (same approach as
test_chat_pet_acceptance.py). Everything happens in a temp dir.

Run:  QT_QPA_PLATFORM=offscreen python test_api_settings_acceptance.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

TMP = Path(tempfile.mkdtemp(prefix="floweypet_apisettings_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLineEdit  # noqa: E402

app = QApplication(sys.argv)

import app_log  # noqa: E402
import error_log  # noqa: E402

LOGS = TMP / "logs"
app_log.LOG_PATH = LOGS / "app.log"
error_log.LOG_PATH = LOGS / "error.log"

import api_chat  # noqa: E402
import api_key_store as store  # noqa: E402
import config as config_mod  # noqa: E402
import pet_menu  # noqa: E402
import pet_window as pw  # noqa: E402
from api_settings_panel import ApiSettingsPanel  # noqa: E402
from dialogue_manager import PRI_CHAT, DialogueManager  # noqa: E402
from pet_window import PetWindow  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


SENT = "sk-SENTINEL-API-KEY-31337"
SENT2 = "sk-SECOND-SENTINEL-8675309"
KEYFILE = TMP / "data" / "api_key.txt"
store.KEY_PATH = KEYFILE
CONFIG_FILE = TMP / "data" / "config.json"
config_mod.CONFIG_PATH = CONFIG_FILE


def pump():
    app.processEvents()


# ====================================================================== 1
print("== 1 api_key_store ==")
check("没有文件: load_key() == ''", store.load_key() == "" and store.has_key() is False)
check("save_key 成功返回 True", store.save_key(SENT) is True)
check("保存后读回一致", store.load_key() == SENT and store.has_key() is True)
check("文件是 UTF-8 纯文本、只有一行、没有多余内容", KEYFILE.read_bytes() == SENT.encode("utf-8"))
store.save_key("   " + SENT2 + " \r\n\t")
check("首尾空白被去掉", store.load_key() == SENT2 and KEYFILE.read_text(encoding="utf-8") == SENT2)
store.save_key("")
check("空串等于删除文件", not KEYFILE.exists() and store.load_key() == "")
store.save_key(SENT)
store.save_key("   \n ")
check("纯空白也等于删除", not KEYFILE.exists())
store.save_key(None)
check("None 不抛异常", not KEYFILE.exists())
store.save_key(SENT)
check("delete_key 返回 True 并删掉文件", store.delete_key() is True and not KEYFILE.exists())
check("删除后 load_key() == ''", store.load_key() == "")
check("删除不存在的文件也返回 True、不抛异常", store.delete_key() is True)
KEYFILE.write_text("", encoding="utf-8")
check("文件内容为空: load_key() == ''", store.load_key() == "")
KEYFILE.write_text("  \n\n", encoding="utf-8")
check("文件只有空白: load_key() == ''", store.load_key() == "")
KEYFILE.write_bytes(b"\xff\xfe\x00bad-bytes")
check("文件读不了(坏编码): 返回 '' 不抛异常", store.load_key() == "")
KEYFILE.unlink()
check("paths.API_KEY_PATH 在用户数据目录里, 文件名 api_key.txt", __import__("paths").API_KEY_PATH.name == "api_key.txt"
      and __import__("paths").API_KEY_PATH.parent == __import__("paths").USER_DATA_DIR)

# write failure: the key path is a directory -> the replace is refused
BADDIR = TMP / "data" / "keydir"
BADDIR.mkdir(parents=True, exist_ok=True)
store.KEY_PATH = BADDIR
try:
    ok = store.save_key(SENT)
    raised = None
except Exception as e:  # noqa: BLE001
    ok, raised = None, e
check("写入失败: 不抛异常且返回 False", raised is None and ok is False, repr(raised))
try:
    ok = store.delete_key()
    raised = None
except Exception as e:  # noqa: BLE001
    ok, raised = None, e
check("删除失败: 不抛异常且返回 False", raised is None and ok is False, repr(raised))
check("写入失败后没有残留 .tmp 文件", not list((TMP / "data").glob("*.tmp")))
store.KEY_PATH = KEYFILE
log_text = app_log.LOG_PATH.read_text(encoding="utf-8") if app_log.LOG_PATH.exists() else ""
check("失败只记固定句子和异常类型名", "API 钥匙保存失败：" in log_text and ("OSError" in log_text or "PermissionError" in log_text), log_text)
check("失败的日志里没有 key", SENT not in log_text and SENT2 not in log_text)

# ====================================================================== 2
print("== 2 config 默认值 ==")
check("config.DEFAULTS['api_chat'] 与 api_chat 的默认常量一致",
      config_mod.DEFAULTS["api_chat"] == {"base_url": api_chat.DEFAULT_BASE_URL, "model": api_chat.DEFAULT_MODEL},
      str(config_mod.DEFAULTS["api_chat"]))
check("config.DEFAULTS 里任何地方都没有 key 字段", "api_key" not in json.dumps(config_mod.DEFAULTS))
check("config.py 没有 import api_chat(main.py 要在导入 PyQt 前读 config)",
      not any(l.strip().startswith(("import api_chat", "from api_chat")) for l in (ROOT / "src" / "config.py").read_text(encoding="utf-8").splitlines()))
merged = config_mod._deep_merge(config_mod.DEFAULTS, {"api_chat": {"model": "x"}})
check("老 config 只有 model: 合并后地址补默认值", merged["api_chat"] == {"base_url": api_chat.DEFAULT_BASE_URL, "model": "x"})

# ====================================================================== 3
print("== 3 面板 ==")
URL0, MODEL0 = "https://api.deepseek.com", "deepseek-flash"


def mk(url=URL0, model=MODEL0, has_key=False, pin=True):
    p = ApiSettingsPanel(url, model, has_key, 180, 1.75, always_on_top=pin, pet_scale=1.0, min_font_px=15)
    p.got, p.deleted, p.closed_n = [], [], []
    p.saved.connect(lambda u, m, k: p.got.append((u, m, k)))
    p.key_deleted.connect(lambda: p.deleted.append(1))
    p.closed.connect(lambda: p.closed_n.append(1))
    return p


def press(w, key):
    QTest.keyClick(w, key)


panel = mk()
check("窗口类型是 Tool, 不是 Popup", panel.windowType() == Qt.WindowType.Tool and panel.windowType() != Qt.WindowType.Popup)
check("没有 WindowDoesNotAcceptFocus", not (panel.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus))
check("没有 WA_ShowWithoutActivating, 有 WA_TranslucentBackground",
      not panel.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating) and panel.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
check("FramelessWindowHint + 置顶标志", bool(panel.windowFlags() & Qt.WindowType.FramelessWindowHint) and bool(panel.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
check("不置顶时没有 WindowStaysOnTopHint", not (mk(pin=False).windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
check("钥匙框是 Password 模式", panel._key_edit.echoMode() == QLineEdit.EchoMode.Password)
check("地址/模型框是普通模式", panel._url_edit.echoMode() == QLineEdit.EchoMode.Normal and panel._model_edit.echoMode() == QLineEdit.EchoMode.Normal)
check("预填当前地址和模型", panel._url_edit.text() == URL0 and panel._model_edit.text() == MODEL0)
panelc = mk("https://example.com/v1", "my-model")
check("预填的是传进来的值", panelc._url_edit.text() == "https://example.com/v1" and panelc._model_edit.text() == "my-model")
check("没存过 key: 钥匙框为空 + 占位文字「粘贴你的 API key」", panel._key_edit.text() == "" and panel._key_edit.placeholderText() == "粘贴你的 API key")
check("没存过 key: 不显示「删除钥匙」", panel._delete_label.isHidden())
panel_k = mk(has_key=True)
check("已存过 key: 钥匙框留空 + 占位文字「已保存（留空则不改）」", panel_k._key_edit.text() == "" and panel_k._key_edit.placeholderText() == "已保存（留空则不改）")
check("已存过 key: 显示「删除钥匙」", not panel_k._delete_label.isHidden())
check("标题是「API 设置」", panel._title.text() == "API 设置")
check("底部三个可点文字", [panel._save_label.text(), panel._cancel_label.text()] == ["[保存]", "[取消]"] and panel_k._delete_label.text() == "[删除钥匙]")
check("宽度与对话框/聊天框相同(宽 = 宠物宽 x 2.75)", panel.width() == round(180 * 2.75))
check("Tab 顺序: 地址 -> 模型 -> 钥匙",
      panel._url_edit.nextInFocusChain() is panel._model_edit and panel._model_edit.nextInFocusChain() is panel._key_edit)
panel.open_and_focus()
pump()
check("打开后可见且焦点落在第一个空着的输入框(钥匙)", panel.isVisible() and panel.focusWidget() is panel._key_edit)
panel.close()

# save via Enter in each field
for name, getter in (("地址框", lambda p: p._url_edit), ("模型框", lambda p: p._model_edit), ("钥匙框", lambda p: p._key_edit)):
    p = mk()
    p.show()
    getter(p).setFocus()
    p._key_edit.setText(SENT)
    press(getter(p), Qt.Key.Key_Return)
    check(f"在{name}里按回车 = 保存", p.got == [(URL0, MODEL0, SENT)] and not p.isVisible(), str(p.got))

p = mk()
p.show()
p._key_edit.setText("  " + SENT + "  ")
p._save_label.clicked.emit()
check("点 [保存]: 钥匙去掉首尾空白后上报", p.got == [(URL0, MODEL0, SENT)])
check("保存后面板关闭, 钥匙框已清空", not p.isVisible() and p._key_edit.text() == "")
check("保存成功没有红字", p.error_text() == "")

p = mk()
p.show()
press(p._model_edit, Qt.Key.Key_Escape)
check("Esc = 取消: 关闭, 不上报", not p.isVisible() and p.got == [] and p.deleted == [] and len(p.closed_n) == 1)
p = mk()
p.show()
QTest.mouseClick(p._close_label, Qt.MouseButton.LeftButton)
check("× = 取消", not p.isVisible() and p.got == [] and len(p.closed_n) == 1)
p = mk()
p.show()
p._key_edit.setText(SENT)
QTest.mouseClick(p._cancel_label, Qt.MouseButton.LeftButton)
check("[取消]: 关闭, 什么都不上报", not p.isVisible() and p.got == [] and p.deleted == [])
p.show()
check("取消后钥匙框里不残留", p._key_edit.text() == "")
p.close()

p = mk("https://a.example/v1", "m")
p.show()
p._url_edit.setText("")
p._model_edit.setText("   ")
p._save_label.clicked.emit()
check("地址为空恢复默认值", p.got and p.got[0][0] == api_chat.DEFAULT_BASE_URL, str(p.got))
check("模型为空(纯空格)恢复默认值", p.got and p.got[0][1] == api_chat.DEFAULT_MODEL, str(p.got))
p = mk()
p.show()
p._url_edit.setText("  https://x.example/v1  ")
p._model_edit.setText("  mm  ")
p._save_label.clicked.emit()
check("地址和模型去掉首尾空白", p.got == [("https://x.example/v1", "mm", "")], str(p.got))

for bad in ("http://api.example.com/v1", "api.example.com", "ftp://x.example", "HTTP://x.example"):
    p = mk()
    p.show()
    p._url_edit.setText(bad)
    p._key_edit.setText(SENT)
    p._save_label.clicked.emit()
    check(f"地址 {bad!r}: 不保存", p.got == [])
    check(f"地址 {bad!r}: 面板不关", p.isVisible())
    check(f"地址 {bad!r}: 显示红字「地址必须以 https:// 开头」", p.error_text() == "地址必须以 https:// 开头", p.error_text())
    check(f"地址 {bad!r}: 红色样式", "#ff4040" in p._error.styleSheet())
    QTest.keyClick(p._url_edit, Qt.Key.Key_A)  # the user types again
    check(f"地址 {bad!r}: 继续输入后红字消失", p.error_text() == "")
    p.close()
p = mk()
p.show()
p._url_edit.setText("https://")
p._save_label.clicked.emit()
check("地址只有 https://: 不保存、面板不关、有提示", p.got == [] and p.isVisible() and p.error_text() == "地址不完整")
p.close()
p = mk()
p.show()
p._url_edit.setText("HTTPS://UPPER.example")
p._save_label.clicked.emit()
check("https:// 前缀不区分大小写", len(p.got) == 1)

p = mk(has_key=True)
p.show()
p._save_label.clicked.emit()
check("钥匙框留空: 上报空钥匙(不动已存的 key)", p.got == [(URL0, MODEL0, "")], str(p.got))
p = mk(has_key=True)
p.show()
QTest.mouseClick(p._delete_label, Qt.MouseButton.LeftButton)
check("点 [删除钥匙]: 上报 key_deleted 并关闭", p.deleted == [1] and p.got == [] and not p.isVisible())
check("面板本身不碰磁盘: 没有创建 api_key.txt", not KEYFILE.exists())

pp = mk()
pp.set_always_on_top(False)
check("set_always_on_top 同步 is_pinned", pp.is_pinned() is False)
pp.reconfigure(90, 1.75, 0.5, 15)
check("reconfigure 后宽度更新", pp.width() == round(90 * 2.75))

# ====================================================================== 4
print("== 4 菜单 ==")
spec = pet_menu._build_menu_spec(MagicMock())
save = next(s for s in spec if s.get("key") == "save")
labels = [i.get("label") for i in save["items"] if i["type"] != "separator"]
check("存档点末尾是「API 设置」(在「检查更新」之后)", labels[-2:] == ["检查更新", "API 设置"], str(labels))
item = save["items"][-1]
check("「API 设置」是 value_action", item["type"] == "value_action")
store.delete_key()
check("没有 key: 右侧显示「未接入」", item["get_value"]() == "未接入")
store.save_key(SENT)
check("有 key: 右侧显示「已接入」", item["get_value"]() == "已接入")
fake = MagicMock()
spec2 = pet_menu._build_menu_spec(fake)
next(s for s in next(x for x in spec2 if x.get("key") == "save")["items"] if s.get("label") == "API 设置")["action"]()
check("点击才去找 pet._open_api_settings(建菜单时不访问)", fake._open_api_settings.call_count == 1)
store.delete_key()


def make_pet():
    pet = MagicMock()
    pet.config = config_mod._default_copy()
    pet.config.update({"font_scale": 1.75, "bubble_min_font_size": 15, "panel_screen_margin": 8})
    pet._always_on_top = True
    pet.pet_scale = 1.0
    pet.pet_display_width = 180
    pet._pending_quit = False
    pet._api_panel = None
    pet._chat_box = None
    pet._main_menu_height.return_value = 100
    pet._place_popup = lambda panel, ref_h=None: panel.move(20, 20)
    pet.save_config = config_mod.save_config
    for name in ("_open_api_settings", "_on_api_settings_saved", "_on_api_key_deleted",
                 "_apply_click_through", "_toggle_always_on_top", "_apply_pet_scale"):
        setattr(pet, name, getattr(PetWindow, name).__get__(pet))
    return pet


pet = make_pet()
pet._open_api_settings()
pump()
panel = pet._api_panel
check("_open_api_settings: 面板存在、可见", isinstance(panel, ApiSettingsPanel) and panel.isVisible())
check("打开前关掉了当前菜单弹窗", pet._close_active_popup.called)
check("面板预填的是 config 里的地址和模型", panel._url_edit.text() == URL0 and panel._model_edit.text() == MODEL0)
pet._open_api_settings()
pump()
check("再开一次不会出现第二个面板(同一个对象)", pet._api_panel is panel)
tops = [w for w in QApplication.topLevelWidgets() if isinstance(w, ApiSettingsPanel) and w.isVisible()]
check("屏幕上只有一个面板", tops == [panel], str(len(tops)))
pet._apply_click_through(False)
check("解锁不会关面板", panel.isVisible())
pet._apply_click_through(True)
check("锁定会关闭面板", not panel.isVisible())
pet._open_api_settings()
pump()
pw._close_api_panel(pet)
check("隐身/退出用的 _close_api_panel 会关掉面板", not pet._api_panel.isVisible())
pw._close_api_panel(object())
check("_close_api_panel 对没有面板的对象无害", True)
src = (ROOT / "src" / "pet_window.py").read_text(encoding="utf-8")
check("_toggle_visible 里关面板(隐身)", "_close_api_panel(self)" in src[src.index("def _toggle_visible"):src.index("def _hide_with_flavor")])
check("_begin_quit 里关面板(退出)", "_close_api_panel(self)" in src[src.index("def _begin_quit"):src.index("def is_exiting")])
pet2 = make_pet()
pet2._pending_quit = True
pet2._open_api_settings()
check("退出中不开面板", pet2._api_panel is None)
pet = make_pet()
pet._open_api_settings()
pet._toggle_always_on_top.__func__ if False else None
pet.arbiter.begin_feature.return_value = True
pet._toggle_always_on_top(False)
check("切换置顶: 面板的 is_pinned 同步", pet._api_panel.is_pinned() is False)
pet._toggle_always_on_top(True)
check("切换置顶: 面板的 is_pinned 同步(再打开)", pet._api_panel.is_pinned() is True)
pet._api_panel.close()

# top-most keeper includes the panel
import topmost_keeper  # noqa: E402
from PyQt6.QtCore import QObject  # noqa: E402


class _QPet(QObject):
    def __init__(self, inner):
        super().__init__()
        self._inner = inner

    def __getattr__(self, n):
        return getattr(self._inner, n)


applied = []
orig_apply = topmost_keeper.apply_topmost
topmost_keeper.apply_topmost = lambda w, on: applied.append((w, on))
try:
    kp = make_pet()
    kp.is_exiting.return_value = False
    kp.is_pinned_on_top.return_value = False
    kp.isVisible.return_value = False
    kp.pomodoro_display.isVisible.return_value = False
    kp.dialogue_box.isVisible.return_value = False
    keeper = topmost_keeper.TopmostKeeper(_QPet(kp), guard_active=lambda: False)
    kp._open_api_settings()
    pump()
    keeper._tick()
    check("置顶开着且面板可见: 保持器会置顶面板", (kp._api_panel, True) in applied)
    applied.clear()
    kp._api_panel.set_always_on_top(False)
    keeper._tick()
    check("关掉置顶后: 保持器不会强行置顶面板", all(w is not kp._api_panel for w, _ in applied))
    kp._api_panel.set_always_on_top(True)
    kp._api_panel.close()
    applied.clear()
    keeper._tick()
    check("面板关着时: 保持器跳过面板", all(w is not kp._api_panel for w, _ in applied))
finally:
    topmost_keeper.apply_topmost = orig_apply

# ====================================================================== 5
print("== 5 保存/删除 -> 台词, config 里没有 key ==")
store.delete_key()
if CONFIG_FILE.exists():
    CONFIG_FILE.unlink()
pet = make_pet()
pet._open_api_settings()
pump()
panel = pet._api_panel
panel._url_edit.setText("https://api.example.com/v1")
panel._model_edit.setText("model-z")
panel._key_edit.setText(SENT)
press(panel._key_edit, Qt.Key.Key_Return)
pump()
check("保存: dm.trigger_api_saved 被调用一次", pet.dm.trigger_api_saved.call_count == 1 and pet.dm.trigger_api_key_deleted.call_count == 0)
check("保存: 地址和模型写进 config", pet.config["api_chat"] == {"base_url": "https://api.example.com/v1", "model": "model-z"}, str(pet.config["api_chat"]))
check("保存: key 写进 api_key.txt", store.load_key() == SENT)
cfg_text = CONFIG_FILE.read_text(encoding="utf-8")
check("config.json 已落盘且含新地址", "https://api.example.com/v1" in cfg_text)
check("config.json 原文里没有 key", SENT not in cfg_text and "api_key" not in cfg_text)
check("config 对象里没有 key", SENT not in json.dumps(pet.config))
check("面板关闭后钥匙框是空的", panel._key_edit.text() == "")

pet.dm.reset_mock()
pet._open_api_settings()
pump()
panel = pet._api_panel
check("已有 key: 重新打开后面板显示「删除钥匙」且钥匙框为空", not panel._delete_label.isHidden() and panel._key_edit.text() == "")
check("重新打开后预填的是刚保存的地址", panel._url_edit.text() == "https://api.example.com/v1")
panel._key_edit.setText("")
panel._save_label.clicked.emit()
pump()
check("钥匙框留空保存: 已存的 key 不变", store.load_key() == SENT)
check("钥匙框留空保存: 仍然说 api_saved", pet.dm.trigger_api_saved.call_count == 1)
pet.dm.reset_mock()
pet._open_api_settings()
pump()
pet._api_panel._delete_label.clicked.emit()
pump()
check("删除钥匙: 文件没了", not KEYFILE.exists() and store.load_key() == "")
check("删除钥匙: dm.trigger_api_key_deleted 被调用一次", pet.dm.trigger_api_key_deleted.call_count == 1 and pet.dm.trigger_api_saved.call_count == 0)
check("删除钥匙后 config.json 仍没有 key", SENT not in CONFIG_FILE.read_text(encoding="utf-8"))
pet.dm.reset_mock()
pet._open_api_settings()
pump()
pet._api_panel._key_edit.setText(SENT2)
pet._api_panel.close()
check("取消: dm 没说话、key 没存、config 没变", pet.dm.trigger_api_saved.call_count == 0 and not KEYFILE.exists())

# a failed key write does not announce success
pet = make_pet()
store.KEY_PATH = BADDIR
pet._open_api_settings()
pump()
pet._api_panel._key_edit.setText(SENT)
pet._api_panel._save_label.clicked.emit()
pump()
check("钥匙写入失败: 不说 api_saved, 改走错误兜底", pet.dm.trigger_api_saved.call_count == 0 and pet._show_error_flavor.call_count == 1)
store.KEY_PATH = KEYFILE

# real DialogueManager lines
dm = DialogueManager({"total_clicks": 0}, lambda c: None, stats=None)
calls = []
dm.speak = lambda category, segments, priority=None, bypass_interval=None: calls.append((category, segments, priority, bypass_interval))
dm.trigger_api_saved()
dm.trigger_api_key_deleted()
check("dm.trigger_api_saved -> speak(api_saved, PRI_CHAT, bypass_interval=True)",
      calls[0][0] == "api_saved" and calls[0][2] == PRI_CHAT and calls[0][3] is True, str(calls[:1]))
check("dm.trigger_api_key_deleted -> speak(api_key_deleted, PRI_CHAT, bypass_interval=True)",
      calls[1][0] == "api_key_deleted" and calls[1][2] == PRI_CHAT and calls[1][3] is True)
check("api_saved 的文案与表情", calls[0][1][0]["text"] == "哼，钥匙收下了。现在你可以跟我说话了……别说废话。" and calls[0][1][0]["face"] == ["smirk", "sheepish_smile", "frown"], str(calls[0][1]))
check("api_key_deleted 的文案与表情", calls[1][1][0]["text"] == "钥匙扔了？……行。反正我也不想跟你说话。" and calls[1][1][0]["face"] == ["frown", "n_right_glance", "lie"], str(calls[1][1]))
raw = json.loads((ROOT / "dialogues_chat.json").read_text(encoding="utf-8"))
faces_dir = ROOT / "assets" / "faces"
bad = []
for pool in ("chat_no_api", "api_saved", "api_key_deleted"):
    for entry in raw[pool]:
        names = [entry["face"]] if isinstance(entry["face"], str) else entry["face"]
        bad += [n for n in names if not (faces_dir / n).is_dir()]
check("三个池里的表情名都在 assets/faces/ 里", not bad, str(bad))
check("chat_no_api 没有被改动(触发逻辑仍是一律说 chat_no_api)", len(raw["chat_no_api"]) == 1 and "API" in raw["chat_no_api"][0]["text"])
dm2 = DialogueManager({"total_clicks": 0}, lambda c: None, stats=None)
got = []
dm2.show_requested.connect(lambda segs, cat: got.append(cat))
dm2.trigger_api_saved()
check("真实 dm: 发出 show_requested(category=api_saved)", got == ["api_saved"], str(got))
check("聊天框提交仍然一律说 chat_no_api(未改触发逻辑)", "trigger_chat_no_api()" in src[src.index("def _on_chat_submitted"):src.index("def _on_fight_clicked")])

# ====================================================================== 6
print("== 6 日志不泄漏 ==")
if LOGS.exists():
    for f in LOGS.glob("*"):
        f.unlink()
# success paths
pet = make_pet()
pet._open_api_settings()
pump()
pet._api_panel._key_edit.setText(SENT)
pet._api_panel._save_label.clicked.emit()
pet._open_api_settings()
pump()
pet._api_panel._delete_label.clicked.emit()
# rejected address (panel stays open, key typed)
pet._open_api_settings()
pump()
pet._api_panel._url_edit.setText("http://bad.example")
pet._api_panel._key_edit.setText(SENT2)
pet._api_panel._save_label.clicked.emit()
pet._api_panel.close()
# write / delete / read failures
store.KEY_PATH = BADDIR
store.save_key(SENT)
store.save_key(SENT2)
store.delete_key()
pet._open_api_settings()
pump()
pet._api_panel._key_edit.setText(SENT)
pet._api_panel._save_label.clicked.emit()
store.KEY_PATH = KEYFILE
KEYFILE.write_bytes(b"\xff\xfe" + SENT.encode("utf-8"))
store.load_key()
KEYFILE.unlink()
texts = "\n".join(f.read_text(encoding="utf-8", errors="replace") for f in TMP.rglob("*") if f.is_file() and f.suffix in (".log", ".txt") and f.name != "api_key.txt")
log_text = app_log.LOG_PATH.read_text(encoding="utf-8") if app_log.LOG_PATH.exists() else ""
check("失败路径确实写了日志(测试不是空转)", "API 钥匙" in log_text)
check("所有日志里都没有第一个哨兵 key", SENT not in texts)
check("所有日志里都没有第二个哨兵 key", SENT2 not in texts)
check("日志里只有固定句子 + 异常类型名", all(("API 钥匙" in ln and "：" in ln) for ln in log_text.splitlines() if "API 钥匙" in ln))
check("没有写出 error.log 的 traceback(没有未处理异常)", not error_log.LOG_PATH.exists() or SENT not in error_log.LOG_PATH.read_text(encoding="utf-8"))
check("api_settings_panel.py / api_key_store.py 里没有日志调用打印输入",
      "print(" not in (ROOT / "src" / "api_settings_panel.py").read_text(encoding="utf-8")
      and "log_event(f\"" not in (ROOT / "src" / "api_settings_panel.py").read_text(encoding="utf-8"))

# ====================================================================== 7
print("== 7 打包/忽略规则 ==")
gi = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
check(".gitignore 忽略 dev_data/ (源码运行时 api_key.txt 就在里面)", "dev_data/" in [l.strip() for l in gi])
check(".gitignore 另外直接忽略 api_key.txt", "api_key.txt" in [l.strip() for l in gi])
rel = (ROOT / "tools" / "build_release.py").read_text(encoding="utf-8")
check("build_release.py 把 api_key.txt 列入禁止打包的文件名", '"api_key.txt"' in rel[rel.index("FORBIDDEN_NAMES"):rel.index("FORBIDDEN_NAMES") + 200])
check("build_release.py 把 dev_data 目录列入禁止", 'n == "dev_data"' in rel)
spec_text = (ROOT / "FloweyPet.spec").read_text(encoding="utf-8")
check("FloweyPet.spec 的 datas 只列具体文件, 不含 dev_data / api_key.txt", "dev_data" not in spec_text and "api_key" not in spec_text)
check("用户数据目录在 exe 同目录之外(%APPDATA%)", "APPDATA" in (ROOT / "src" / "paths.py").read_text(encoding="utf-8"))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("API 设置用例全部通过")
sys.exit(0)
