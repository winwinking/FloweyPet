# -*- coding: utf-8 -*-
"""Acceptance tests for the "待机动作" switch (config["idle_animation"]):
_enter_idle / _advance_idle_frame / _toggle_idle_animation, SpriteBank's
still_frame (load, rescale on tier change, fallback to idle_frames[0] when
the file is missing) and the right-click menu's toggle row.

Offscreen, FakeSelf style: PetWindow's methods are bound onto a stand-in
object (no real window); SpriteBank and the menu spec are the real ones.

Run:  QT_QPA_PLATFORM=offscreen python test_idle_animation_acceptance.py
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import compositor  # noqa: E402
import config as config_mod  # noqa: E402
import pet_menu  # noqa: E402
from pet_window import IDLE_FRAME_MS, PetWindow, State  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


class FakeTimer:
    def __init__(self):
        self.active = False
        self.starts = []

    def start(self, ms):
        self.active = True
        self.starts.append(ms)

    def stop(self):
        self.active = False


class FakeSelf:
    _enter_idle = PetWindow._enter_idle
    _advance_idle_frame = PetWindow._advance_idle_frame
    _toggle_idle_animation = PetWindow._toggle_idle_animation
    _current_frame_for_state = PetWindow._current_frame_for_state

    def __init__(self, sprites, config, state=State.IDLE):
        self.sprites = sprites
        self.config = config
        self.state = state
        self._dragging = False
        self._idle_idx = 0
        self._idle_timer = FakeTimer()
        self.shown = []        # every pixmap handed to _set_base_pixmap
        self.saved = []        # snapshot of config at each save_config call
        self.chat_scheduled = 0
        self.afk_rescheduled = 0
        self.arbiter = MagicMock()
        self.dm = MagicMock()

    def _set_base_pixmap(self, pm):
        self.shown.append(pm)

    def save_config(self, cfg):
        self.saved.append(dict(cfg))

    def _schedule_idle_chat(self):
        self.chat_scheduled += 1

    def _reschedule_afk_timers(self):
        self.afk_rescheduled += 1


sprites = compositor.SpriteBank(1.0)

# ===================================================================== 1
# Assets / SpriteBank
check("assets/idle_still/00.png 存在", (compositor.IDLE_STILL_DIR / "00.png").exists())
check("still_frame 已加载(非空)", sprites.still_frame is not None and not sprites.still_frame.isNull())
check("still_frame 是独立图片,不是 idle_frames[0]",
      sprites.still_frame.toImage() != sprites.idle_frames[0].toImage())
raw = sprites._raw_still[0]
check("still_frame 按 idle_frames 同样方式放大(原图尺寸 x BASE_SCALE)",
      sprites.still_frame.size().width() == round(raw.width() * compositor.BASE_SCALE)
      and sprites.still_frame.size().height() == round(raw.height() * compositor.BASE_SCALE))
old = sprites.still_frame
sprites.set_pet_scale(0.5)
check("切换大小档位: still_frame 一起重新缩放",
      sprites.still_frame is not old
      and sprites.still_frame.width() == round(raw.width() * compositor.BASE_SCALE * 0.5))
check("切换大小档位: idle_frames 与 still_frame 同时更新",
      sprites.idle_frames[0].width() == round(sprites._raw_idle[0].width() * compositor.BASE_SCALE * 0.5))
sprites.set_pet_scale(1.0)

# ===================================================================== 2
# still_frame missing -> falls back to idle_frames[0]
orig_dir = compositor.IDLE_STILL_DIR
compositor.IDLE_STILL_DIR = orig_dir.parent / "idle_still_does_not_exist"
try:
    bank = compositor.SpriteBank(1.0)
finally:
    compositor.IDLE_STILL_DIR = orig_dir
check("缺 idle_still: still_frame 退回 idle_frames[0]", bank.still_frame is bank.idle_frames[0])
bank.set_pet_scale(0.5)
check("缺 idle_still: 切换档位后仍是新的 idle_frames[0]", bank.still_frame is bank.idle_frames[0])

# ===================================================================== 3
# _enter_idle, animation ON (and key absent == default ON)
for label, cfg in (("开", {"idle_animation": True}), ("缺省", {})):
    f = FakeSelf(sprites, dict(cfg), state=State.APPEARING)
    f._enter_idle()
    check(f"_enter_idle({label}): 显示 idle_frames[0]", f.shown == [sprites.idle_frames[0]])
    check(f"_enter_idle({label}): 启动 _idle_timer(IDLE_FRAME_MS)", f._idle_timer.active and f._idle_timer.starts == [IDLE_FRAME_MS])
    check(f"_enter_idle({label}): 进入 IDLE 且帧号归零", f.state == State.IDLE and f._idle_idx == 0)
    check(f"_enter_idle({label}): 照旧排聊天/AFK 定时器", f.chat_scheduled == 1 and f.afk_rescheduled == 1)

# ===================================================================== 4
# _enter_idle, animation OFF
f = FakeSelf(sprites, {"idle_animation": False}, state=State.APPEARING)
f._idle_timer.active = True  # even if a timer was somehow running
f._enter_idle()
check("_enter_idle(关): 显示 still_frame", f.shown == [sprites.still_frame])
check("_enter_idle(关): 不启动 _idle_timer(且已停)", f._idle_timer.starts == [] and not f._idle_timer.active)
check("_enter_idle(关): 仍进入 IDLE,聊天/AFK 定时器照旧", f.state == State.IDLE and f.chat_scheduled == 1 and f.afk_rescheduled == 1)

# ===================================================================== 5
# _advance_idle_frame
f = FakeSelf(sprites, {"idle_animation": False})
f._advance_idle_frame()
check("_advance_idle_frame(关): 直接返回,不换图不动帧号", f.shown == [] and f._idle_idx == 0)
f = FakeSelf(sprites, {"idle_animation": True})
f._advance_idle_frame()
check("_advance_idle_frame(开): 照旧前进一帧", f._idle_idx == 1 and f.shown == [sprites.idle_frames[1]])

# ===================================================================== 6
# Toggling takes effect immediately while IDLE
f = FakeSelf(sprites, {"idle_animation": True})
f._enter_idle()
f._idle_idx = 2
f.shown.clear()
f._toggle_idle_animation(False)
check("IDLE 中关: 立即停在 still_frame", f.shown == [sprites.still_frame])
check("IDLE 中关: _idle_timer 已停", not f._idle_timer.active)
check("IDLE 中关: config 已写入 False 并保存", f.config["idle_animation"] is False and f.saved and f.saved[-1]["idle_animation"] is False)
f.shown.clear()
f._toggle_idle_animation(True)
check("IDLE 中开: 从第 0 帧开始", f._idle_idx == 0 and f.shown == [sprites.idle_frames[0]])
check("IDLE 中开: _idle_timer 重新启动", f._idle_timer.active and f._idle_timer.starts[-1] == IDLE_FRAME_MS)
check("IDLE 中开: config 已写入 True 并保存", f.config["idle_animation"] is True and f.saved[-1]["idle_animation"] is True)

# ===================================================================== 7
# Not IDLE: only the setting changes, nothing on screen is touched
for st in (State.TALKING, State.APPEARING, State.EXITING):
    for target in (False, True):
        f = FakeSelf(sprites, {"idle_animation": not target}, state=st)
        f._toggle_idle_animation(target)
        check(f"{st} 时切到 {target}: 只写 config,不换图不动定时器",
              f.shown == [] and f._idle_timer.starts == [] and f.config["idle_animation"] is target and len(f.saved) == 1)

# ===================================================================== 8
# No arbiter, no dialogue
f = FakeSelf(sprites, {"idle_animation": True})
f._toggle_idle_animation(False)
f._toggle_idle_animation(True)
check("不走 arbiter、不触发台词", not f.arbiter.mock_calls and not f.dm.mock_calls)

# ===================================================================== 9
# Redraw after a size-tier change keeps the still frame when OFF
f = FakeSelf(sprites, {"idle_animation": False})
check("档位切换后重取当前帧(关): 是 still_frame", f._current_frame_for_state() is sprites.still_frame)
f = FakeSelf(sprites, {"idle_animation": True})
f._idle_idx = 1
check("档位切换后重取当前帧(开): 是 idle_frames[1]", f._current_frame_for_state() is sprites.idle_frames[1])

# ===================================================================== 10
# Config default + menu row
check("DEFAULTS 里 idle_animation 默认 True", config_mod.DEFAULTS.get("idle_animation") is True)

pet = MagicMock()
pet.config = {"volume": 0.8}
pet._always_on_top = True
pet._locked = False
spec = pet_menu._build_menu_spec(pet)
# 待机动作 now sits in the 存档点 box (second layer), right after 大小
save_items = pet_menu.find_category(pet, "save")["items"]
labels = [s.get("label") for s in save_items]
check("「存档点」小框里有「待机动作」,紧跟在「大小」后面", "待机动作" in labels and labels.index("待机动作") == labels.index("大小") + 1, str(labels))
row = save_items[labels.index("待机动作")]
check("「待机动作」是 toggle 类型", row["type"] == "toggle")
check("get: 缺省读作 True", row["get"]() is True)
pet.config["idle_animation"] = False
check("get: 读 config 的 False", row["get"]() is False)
row["set"](False)
check("set: 调 _toggle_idle_animation(False)", pet._toggle_idle_animation.call_args_list == [((False,),)] or [c.args for c in pet._toggle_idle_animation.call_args_list] == [(False,)])
check("「待机动作」不声明 close_after(菜单保持打开)", not row.get("close_after"))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("待机动作开关用例全部通过")
sys.exit(0)
