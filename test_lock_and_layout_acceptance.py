# -*- coding: utf-8 -*-
"""Acceptance tests for this session's three changes (offscreen, no real
GUI interaction -- same pattern as test_chase_acceptance.py /
test_dialogue_arbiter_acceptance.py):

1. Panel width auto-fit (记录/提醒/音量/大小 panels never clip a value,
   across all 4 pet-size ui_scale tiers).
2. Screen-edge clamp + flip for the dialogue bubble and popup panels
   (multi-monitor-aware: clamps against the screen actually under the
   pet, not always the primary one).
3. Lock feature: dialogue.json's lock_on/lock_off/lock_hint pools load
   (and a malformed entry is skipped, not crashed on), and the click-
   counting/cooldown logic behind the "我被锁住了" hint line.

PetWindow.mousePressEvent's locked branch is exercised directly (via a
duck-typed fake event + fake self, not a full PetWindow/live GUI) since
that's exactly where the "menu won't close while locked" bug lived. A
full live PetWindow with real sprite assets/QSystemTrayIcon is still not
constructed here, same carve-out test_dialogue_arbiter_acceptance.py
documents for cases 9-11 -- the real hands-on click-through is still the
user's to do.

Run:  QT_QPA_PLATFORM=offscreen python test_lock_and_layout_acceptance.py
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import dialogue_box as dialogue_box_mod  # noqa: E402
from dialogue_box import DialogueBox  # noqa: E402
from dialogue_manager import DialogueManager  # noqa: E402
from pet_window import PetWindow  # noqa: E402
from stats_panel import StatsPanel  # noqa: E402
from undertale_ui import (  # noqa: E402
    UiMetrics,
    clamp_rect_to_screen,
    fit_panel_width,
)

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


# ===================================================================== 1
# Panel width auto-fit, across the 4 default pet_size_tiers with the
# default ui_follow_ratio=0.5 (matches PetWindow._ui_scale's formula).
PET_SIZE_TIERS = [1.0, 0.85, 0.7, 0.55]
UI_FOLLOW_RATIO = 0.5


def ui_scale_for(pet_scale):
    return 1 - (1 - pet_scale) * UI_FOLLOW_RATIO


for tier in PET_SIZE_TIERS:
    scale = ui_scale_for(tier)
    metrics = StatsPanel(
        {
            "days": 9999,
            "hours": 12345.6,
            "clicks": 99999,
            "interactions": 88888,
            "pomodoro_count": 7777,
            "flirt_count": 6666,
            "water_rate": 100.0,
            "meal_rate": 0.0,
            "water_dismissed": 5555,
            "meal_dismissed": 4444,
            "egg_seen": 999,
            "egg_total": 999,
            "egg_pct": 100.0,
            "latest_time": "23:59",
        },
        ui_scale=scale,
        min_font_px=10,
    ).metrics
    # Recompute independently (not just re-reading the panel's own
    # self.width()) so this is a real check against the panel's actual
    # rendered font, not a tautology against its own stored value.
    from PyQt6.QtGui import QFontMetrics

    from fonts import pixel_font

    font = pixel_font(metrics.font_px)
    fm = QFontMetrics(font)
    rows = [
        ("陪伴天数", "9999天"),
        ("陪伴时长", "12345.6小时"),
        ("点击次数", "99999次"),
        ("互动次数", "88888次"),
        ("番茄钟完成", "7777次"),
        ("调情次数", "6666次"),
        ("喝水响应率", "100%"),
        ("吃饭响应率", "0%"),
        ("敷衍次数(水/饭)", "5555/4444次"),
        ("彩蛋收集", "999/999 (100%)"),
        ("最晚活跃", "23:59"),
    ]
    panel = StatsPanel(
        {
            "days": 9999, "hours": 12345.6, "clicks": 99999, "interactions": 88888,
            "pomodoro_count": 7777, "flirt_count": 6666, "water_rate": 100.0, "meal_rate": 0.0,
            "water_dismissed": 5555, "meal_dismissed": 4444, "egg_seen": 999, "egg_total": 999,
            "egg_pct": 100.0, "latest_time": "23:59",
        },
        ui_scale=scale,
        min_font_px=10,
    )
    interior = panel.width() - metrics.gutter_width - metrics.panel_padding * 2 - metrics.border_width * 2
    worst_row_w = max(sum(fm.horizontalAdvance(p) for p in parts) + 12 * (len(parts) - 1) for parts in rows)
    check(
        f"记录面板 ui_scale={scale:.3f} (tier={tier}) 大数值不截断",
        interior >= worst_row_w,
        f"interior={interior} worst_row_w={worst_row_w}",
    )
    panel.close()

# Volume/size panels reuse the same fit_panel_width tool -- sanity-check
# the tool directly with an extreme (hand-edited config) tier value.
metrics = UiMetrics(1.0, 10)
from fonts import pixel_font as _pixel_font  # noqa: E402

font = _pixel_font(metrics.font_px)
w = fit_panel_width(metrics, font, [("100000%",)], base_width=140)
fm = QFontMetrics(font)
check(
    "音量/大小面板共用的 fit_panel_width 对超长数值也不截断",
    w - metrics.gutter_width - metrics.panel_padding * 2 - metrics.border_width * 2 >= fm.horizontalAdvance("100000%"),
)

# ===================================================================== 2
# clamp_rect_to_screen: pure geometry, no QScreen needed.
screen = QRect(0, 0, 800, 600)
inside = QRect(100, 100, 50, 50)
check("clamp: 完全在屏幕内的矩形不变", clamp_rect_to_screen(inside, screen, margin=8) == inside)

over_right = QRect(790, 100, 50, 50)
clamped = clamp_rect_to_screen(over_right, screen, margin=8)
check("clamp: 超出右边界被拉回", clamped.x() + clamped.width() <= screen.width() - 8, str(clamped))

over_top = QRect(100, -30, 50, 50)
clamped = clamp_rect_to_screen(over_top, screen, margin=8)
check("clamp: 超出上边界被拉回", clamped.y() >= 8, str(clamped))

# ===================================================================== 3
# DialogueBox.follow(): flip above<->below + clamp, using a small
# monkeypatched screen so every corner case is reachable without a real
# multi-monitor setup.
FAKE_SCREEN = QRect(0, 0, 400, 300)


class FakeAudio:
    def play_blip_for_char(self, ch):
        pass

    def play_laugh(self):
        pass

    def play_menu_move(self):
        pass

    def play_menu_select(self):
        pass


dialogue_box_mod.screen_available_rect = lambda x, y, w=1, h=1: FAKE_SCREEN

box = DialogueBox(100, 1.0, FakeAudio(), screen_margin=8)

# Pet near the TOP of the screen: "above" placement would go negative,
# must flip to below instead, and must not overlap the pet.
pet_x, pet_y, pet_w, pet_h = 150, 5, 100, 50
box.follow(pet_x, pet_y, pet_w, pet_y + pet_h)
pet_rect = QRect(pet_x, pet_y, pet_w, pet_h)
box_rect = QRect(box.x(), box.y(), box.width(), box.height())
check(
    "气泡: 桌宠贴屏幕顶部时翻转到下方且不遮挡本体",
    box.y() > pet_y and not box_rect.intersects(pet_rect),
    f"box_rect={box_rect} pet_rect={pet_rect}",
)
check("气泡: 翻转后仍完整在屏幕内(顶部用例)", FAKE_SCREEN.contains(box_rect), str(box_rect))

# Pet near the BOTTOM: default "above" placement should fit normally.
pet_x, pet_y, pet_w, pet_h = 150, 260, 100, 35
box.follow(pet_x, pet_y, pet_w, pet_y + pet_h)
check("气泡: 桌宠贴底部时维持默认(上方)位置", box.y() < pet_y)

# Pet in a CORNER (top-left): both x and y need clamping/flipping together.
pet_x, pet_y, pet_w, pet_h = 2, 2, 100, 50
box.follow(pet_x, pet_y, pet_w, pet_y + pet_h)
box_rect = QRect(box.x(), box.y(), box.width(), box.height())
pet_rect = QRect(pet_x, pet_y, pet_w, pet_h)
check("气泡: 左上角情形完整在屏幕内", FAKE_SCREEN.contains(box_rect), str(box_rect))
check("气泡: 左上角情形不遮挡本体", not box_rect.intersects(pet_rect), f"{box_rect} vs {pet_rect}")

# Pet in the opposite corner (bottom-right).
pet_x, pet_y, pet_w, pet_h = 320, 260, 100, 35
box.follow(pet_x, pet_y, pet_w, pet_y + pet_h)
box_rect = QRect(box.x(), box.y(), box.width(), box.height())
pet_rect = QRect(pet_x, pet_y, pet_w, pet_h)
check("气泡: 右下角情形完整在屏幕内", FAKE_SCREEN.contains(box_rect), str(box_rect))
check("气泡: 右下角情形不遮挡本体", not box_rect.intersects(pet_rect), f"{box_rect} vs {pet_rect}")

box.close()

# ===================================================================== 4
# dialogue.json's new lock_* pools actually load, and a malformed entry
# gets skipped (never crashes), from the *real* dialogue.json.
dm = DialogueManager({"total_clicks": 0}, lambda cfg: None, stats=None)
check("台词: lock_on 池非空", bool(dm.data.get("lock_on")))
check("台词: lock_off 池非空", bool(dm.data.get("lock_off")))
check("台词: lock_hint 池非空", bool(dm.data.get("lock_hint")))

bad_pool = [
    {"text": "正常占位句", "face": "smile"},
    {"text": "", "face": "smile"},  # empty text -- must be skipped
    {"text": "缺表情"},  # missing face -- must be skipped
    "not even a dict",  # must be skipped
]
ok, reason = DialogueManager._validate_flavor_entry(bad_pool[0])
check("台词校验: 正常条目通过", ok, reason)
for bad_entry in bad_pool[1:]:
    ok, reason = DialogueManager._validate_flavor_entry(bad_entry)
    check(f"台词校验: 坏条目被拒 ({bad_entry!r})", not ok)

dm.data["_test_bad_pool"] = list(bad_pool)
dm._sanitize_flavor_pools(["_test_bad_pool"])
check(
    "台词校验: sanitize 后坏条目全部被剔除，只剩1条",
    dm.data["_test_bad_pool"] == [bad_pool[0]],
    str(dm.data["_test_bad_pool"]),
)

# ===================================================================== 5
# Lock click-counting/hint-cooldown logic (PetWindow._register_locked_click
# / _toggle_lock) -- exercised via the real unbound methods against a
# lightweight fake `self`, since a full PetWindow needs real sprite assets
# / QSystemTrayIcon plumbing this test intentionally doesn't construct
# (same carve-out as test_dialogue_arbiter_acceptance.py's cases 9-11).


class FakeDM:
    def __init__(self):
        self.hints = 0
        self.flavors = []

    def trigger_lock_hint(self):
        self.hints += 1

    def trigger_menu_flavor(self, category):
        self.flavors.append(category)


class FakeStats:
    def __init__(self):
        self.interactions = 0

    def record_interaction(self):
        self.interactions += 1


class FakeArbiter:
    """_toggle_lock now routes through DialogueArbiter's begin_feature/
    finish_feature/is_lock_tantrum_active (lock's own dedicated 3-tier
    escalation, see dialogue_arbiter.py) instead of calling
    dm.trigger_menu_flavor directly -- this fake resolves synchronously
    (no debounce timer) so this file's existing checks on _toggle_lock's
    OWN state-mutation logic stay simple; the escalation tier logic itself
    (normal/tired/annoyed/rant) has its own dedicated acceptance test."""

    def __init__(self):
        self.tantrum = False
        self.begin_calls = []

    def is_lock_tantrum_active(self):
        return self.tantrum

    def begin_feature(self, key):
        self.begin_calls.append(key)
        return True

    def finish_feature(self, key, normal_provider=None):
        if normal_provider:
            normal_provider()


class FakeSelf:
    def __init__(self, config):
        self.config = config
        self.dm = FakeDM()
        self.stats = FakeStats()
        self.arbiter = FakeArbiter()
        # Lock is click-through now and is never persisted: PetWindow always
        # starts unlocked, so the fake no longer seeds _locked from config.
        self._locked = False
        self.click_through_calls = []
        self._locked_click_count = 0
        self._last_lock_hint_time = float("-inf")
        self._saved = []

    def save_config(self, cfg):
        self._saved.append(dict(cfg))

    def _apply_click_through(self, on):
        self.click_through_calls.append(on)


cfg = {"lock_hint_click_threshold": 10, "lock_hint_cooldown_seconds": 300}
fake = FakeSelf(cfg)
for _ in range(9):
    PetWindow._register_locked_click(fake)
check("锁定提示: 9次点击不触发", fake.dm.hints == 0)
PetWindow._register_locked_click(fake)
check("锁定提示: 第10次点击触发一次", fake.dm.hints == 1)
for _ in range(10):
    PetWindow._register_locked_click(fake)
check("锁定提示: 冷却期内(5分钟)第20次点击不再触发", fake.dm.hints == 1)

# Simulate the cooldown having elapsed.
fake._last_lock_hint_time = time.monotonic() - 301
for _ in range(10):
    PetWindow._register_locked_click(fake)
check("锁定提示: 冷却过后再满10次可再次触发", fake.dm.hints == 2)

fake2 = FakeSelf({"locked": False})
PetWindow._toggle_lock(fake2, True)
check("锁定开关: 打开后 self._locked 为真", fake2._locked is True)
# REPLACED (was: 打开后配置项写入 True -> fake2.config["locked"] is True).
# Lock is now mouse click-through at the native-window layer and is no
# longer persisted: _toggle_lock must NOT write config["locked"] nor call
# save_config, and must apply click-through to the windows instead.
check("锁定开关: 打开后 self._locked 为 True(新语义a)", fake2._locked is True)
check("锁定开关: 打开后未把 locked 写进配置/未落盘(新语义b)",
      fake2.config.get("locked") is not True and fake2._saved == [], str(fake2.config))
check("锁定开关: 打开后穿透被应用,最后一次为 True(新语义c)",
      bool(fake2.click_through_calls) and fake2.click_through_calls[-1] is True, str(fake2.click_through_calls))
check("锁定开关: 打开播放 lock_on 台词", fake2.dm.flavors == ["lock_on"])
fake2._locked_click_count = 7
PetWindow._toggle_lock(fake2, False)
check("锁定开关: 关闭后 self._locked 为假", fake2._locked is False)
check("锁定开关: 关闭时点击计数被重置", fake2._locked_click_count == 0)
check("锁定开关: 关闭播放 lock_off 台词", fake2.dm.flavors == ["lock_on", "lock_off"])
check("锁定开关: begin_feature('lock') 被调用两次(开+关)", fake2.arbiter.begin_calls == ["lock", "lock"])

fake2.arbiter.tantrum = True
before_state, before_flavors = fake2._locked, list(fake2.dm.flavors)
PetWindow._toggle_lock(fake2, not before_state)
check("锁定开关: 闹脾气冷区内切换完全无效(状态不变)", fake2._locked == before_state)
check("锁定开关: 闹脾气冷区内不触发任何台词", fake2.dm.flavors == before_flavors)
check("锁定开关: 闹脾气冷区内不调用begin_feature(不占用防抖)", fake2.arbiter.begin_calls == ["lock", "lock"])


# mousePressEvent's locked branch -- this is the exact bug reported: a
# click on a locked pet must still be able to close an already-open popup
# (right-click menu), and must leave the pet draggable by default so it
# can still be repositioned while locked.
class FakeMouseEvent:
    def __init__(self, pos):
        self._pos = pos

    def button(self):
        return Qt.MouseButton.LeftButton

    def globalPosition(self):
        class _F:
            def __init__(self, p):
                self._p = p

            def toPoint(self):
                return self._p

        return _F(self._pos)


class FakePopup:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeSelfPress(FakeSelf):
    _register_locked_click = PetWindow._register_locked_click

    def __init__(self, config):
        super().__init__(config)
        self._pending_quit = False
        self._dragging = False
        self._drag_moved = False
        self._drag_offset = QPoint()
        self._active_popup = FakePopup()

    def pos(self):
        return QPoint(100, 100)

    def update(self):
        pass

    def _close_active_popup(self):
        if self._active_popup is not None:
            self._active_popup.close()
            self._active_popup = None


fake3 = FakeSelfPress({"locked": True, "lock_hint_click_threshold": 10, "lock_hint_cooldown_seconds": 300})
fake3._locked = True
popup = fake3._active_popup
PetWindow.mousePressEvent(fake3, FakeMouseEvent(QPoint(150, 120)))
check("锁定点击: 打开的右键菜单被关闭", popup.closed is True)
check("锁定点击: 默认(lock_disable_drag=False)仍进入拖动", fake3._dragging is True)
check("锁定点击: 不记录互动/统计(dm/stats 无 record_interaction 调用)", fake3.stats.interactions == 0)

fake4 = FakeSelfPress({"locked": True, "lock_disable_drag": True})
fake4._locked = True
popup2 = fake4._active_popup
PetWindow.mousePressEvent(fake4, FakeMouseEvent(QPoint(150, 120)))
check("锁定点击: lock_disable_drag=True 时右键菜单仍被关闭", popup2.closed is True)
check("锁定点击: lock_disable_drag=True 时不进入拖动", fake4._dragging is False)

# ===================================================================== 6
# Old config.json missing every new key must not crash on load
# (config._deep_merge backfills defaults) -- simulates a pre-upgrade
# config.json with none of this session's new fields at all.
import config as config_mod  # noqa: E402

old_config = {"volume": 0.5, "always_on_top": True}
merged = config_mod._deep_merge(config_mod.DEFAULTS, old_config)
check("老配置兼容: 缺 locked 字段默认 False", merged["locked"] is False)
check("老配置兼容: 缺 lock_disable_drag 字段默认 False(锁定后仍可拖动)", merged["lock_disable_drag"] is False)
check("老配置兼容: 缺 panel_screen_margin/bubble_screen_margin 有默认值", merged["panel_screen_margin"] == 8 and merged["bubble_screen_margin"] == 8)

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("lock/layout 用例全部通过")
sys.exit(0)
