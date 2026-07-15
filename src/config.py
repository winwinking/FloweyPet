# -*- coding: utf-8 -*-
"""config.json load/save with defaults. All persisted user-facing settings
live here: window position, volume, font scale, pomodoro/reminder options,
autostart, and the small bits of state the dialogue system needs to
remember across restarts (streak days, last interaction, etc).
"""
import json
import sys
import winreg
from pathlib import Path

from paths import CONFIG_PATH

DEFAULTS = {
    "position": None,  # None => PetWindow picks a sensible on-screen default
    "pet_scale": 1.0,             # current size tier multiplier, see pet_size_tiers
    "pet_size_tiers": [1.0, 0.85, 0.7, 0.55],  # editable list of size tiers (100% first/max)
    "ui_follow_ratio": 0.5,       # how much menus/panels shrink relative to the pet (0=not at all, 1=fully)
    "bubble_min_font_size": 15,   # floor for the dialogue bubble's font size in px (~70% of the 21px default)
    "ui_min_font_size": 10,       # floor for menu/panel font size in px
    "panel_screen_margin": 8,     # safety margin (px) kept clear of the screen edge for menus/panels
    "bubble_screen_margin": 8,    # safety margin (px) kept clear of the screen edge for the dialogue bubble
    "locked": False,              # 锁定：开启后点击桌宠本体完全无视（不触发对话/厌倦反应）；右键菜单/提醒/待机搭话不受影响
    "lock_disable_drag": False,   # 锁定时是否额外禁止拖动桌宠本体（默认允许拖动，锁定后仍可移动位置）
    "lock_hint_click_threshold": 10,  # 锁定期间点击桌宠本体累计这么多次，播一句"我被锁住了"提示
    "lock_hint_cooldown_seconds": 300,  # 锁定提示台词的冷却
    "volume": 0.8,
    "muted": False,
    "menu_sfx_enabled": True,  # independent of "muted": only gates menu move/select blips
    "audio_debug": False,               # log every play attempt (not just failures/rebuilds) to audio.log
    "audio_watchdog_threshold": 5,      # consecutive play failures across all channels before a forced full rebuild
    "audio_rotation_seconds": 90,        # unconditionally rebuild every sound player on this interval (0 disables)
    "audio_stress_test_on_startup": False,  # dev-only: fire 200 randomized mixed plays at launch, see audio.py
    "font_scale": 1.75,
    "always_on_top": True,
    "autostart": False,
    "first_run_done": False,
    "birthday": None,          # "MM-DD" or null
    "install_date": None,      # "YYYY-MM-DD"
    "last_active_date": None,  # "YYYY-MM-DD", for streak tracking
    "streak_days": 0,
    "total_clicks": 0,
    "last_close_time": None,   # ISO timestamp
    "close_count_recent": 0,
    "greeted_streaks": [],     # which streak milestones already shown
    "greeted_birthday_date": None,
    "pomodoro": {
        "focus_minutes": 25,
        "break_minutes": 5,
    },
    "reminders": {
        "water": {"enabled": False, "interval_minutes": 45},
        "meal": {"enabled": True, "times": ["12:00", "18:00"]},
        "sleep": {"enabled": True, "times": ["23:00"]},
    },
    "pomodoro_chat_min_minutes": 5,
    "pomodoro_chat_max_minutes": 10,
    "neglect_after_minutes": 120,
    "reminder_pursuit_stages_minutes": [5, 10, 20, 30],  # 追讨点位，相对本轮起点的分钟数
    "reminder_pursuit_hold_seconds": 60,  # 首次提醒/睡觉弹窗停留秒数（无操作自动收起）
    "reminder_chase_hold_seconds": 30,  # 追讨弹窗（第5/10/20/30分钟）停留秒数
    "reminder_test_mode": False,  # dev-only: 追讨点位按1分钟=1秒压缩并强制触发喝水提醒, see reminder_chase.py
    "reminder_test_mode_hold_seconds": 3,  # 测试模式下弹窗停留秒数（压缩后），改大可手动逐级点按钮
    "single_instance_key": "FloweyPet_SingleInstance_Lock",  # QLocalServer/QLocalSocket name, see single_instance.py
    "single_instance_connect_timeout_ms": 500,  # how long the startup probe waits for an existing instance to answer
    "duplicate_launch_cooldown_seconds": 10,  # min gap between duplicate-launch roast lines, so rapid re-double-clicking only quips once
    "global_min_interval_seconds": 180,
    "reunion_after_minutes": 180,
    "click_deep_unlock_clicks": 15,
    "day_boundary_hour": 6,          # 0:00-this hour counts as "still last night" for the stats panel
    "session_gap_minutes": 90,        # activity gaps longer than this start a new session
    "morning_greet_probability": 0.2,  # odds of the "起这么早" line on a genuinely fresh early-morning boot
    "version": "3.0.0",  # shown in the "查看信息" easter egg via {ver}; edit by hand to bump it
    "info_easter_egg": {
        "weights": {"normal_intro": 40, "fourth_wall": 30, "nonsense": 20, "rare": 10},
        "anti_spam_window_seconds": 60,
        "anti_spam_trigger_count": 4,
    },
    "flirt": {
        "hesitate_seconds": 6,      # how long the heart cursor must dwell on 调情 before "抓包" triggers
        "stall_seconds": 8,         # how much longer, after being caught, before the stall line fires
        "cooldown_minutes": 5,      # no repeat "抓包" within this many minutes; resets on restart (not persisted)
        "roast_threshold_count": 20,  # flirt_count needed to unlock the 记录 panel's dedicated flirt roast line
    },
    "game_detect": {
        # UNDERTALE/DELTARUNE 启动/退出检测彩蛋，见 game_detector.py。只匹配
        # 进程名(不读窗口标题/内容/截图)，process_names 里的值忽略大小写。
        "enabled": True,
        "poll_interval_seconds": 5,
        "process_names": {"ut": "UNDERTALE.exe", "dr": "DELTARUNE.exe"},
        "exit_confirm_polls": 2,      # 进程消失需连续这么多个轮询周期才判定退出
        "rare_launch_weight": 15,     # 非首次启动时抽中稀有台词池的权重(0-100，普通池占剩余)
        "game_egg_display_mode": "topmost",  # "topmost"=彩蛋期间临时置顶 / "deferred"=不抢屏，攒到切出游戏时再说
        "occlusion_poll_interval_ms": 150,   # topmost 模式下，轮询气泡是否已关闭(从而恢复层级)的间隔
        "occlusion_guard_max_ms": 60000,     # 兜底：无论如何这么久后强制恢复层级，防止异常卡死为置顶
    },
    "dialogue_spam": {
        # 功能防抖: 同一功能项(置顶/静音/隐身/开机自启/番茄钟/提醒设置/记录/查看信息)
        # 触发后进入这么多秒的冷却，期间再点无效(功能不切换/不触发台词)；
        # 音量走"连续调整合并"模式：每次都真的应用新音量，但把台词推迟到
        # 停止操作后的这个时长，只说最后一句。见 dialogue_arbiter.py。
        "feature_debounce_seconds": 0.5,
        # 同一功能项的台词自己正在播放时再点它：不会打断/重播，只是排队，
        # 等这句播完后才触发下一级(不耐烦->烦躁->愤怒=喷人->沉默，严格依次
        # 递增，绝不重复同一级)，等待的间隔从这个列表里按"连续第几次排队"
        # 取一个随机区间(秒)，越往后越长；超出列表长度后固定用最后一档。
        "feature_escalation_delay_steps": [[1, 2], [3, 4], [5, 6], [7, 8]],
        # 该功能项这么多秒没再被触发，就把它的阶段计数清零，回到"正常"。
        "feature_reset_seconds": 25,
        # 进入第四级(沉默)后，至少这么多秒完全不再为该功能项配台词(功能仍正常生效)。
        "feature_silence_seconds": 150,
        # 连点小花本体(而不是功能菜单项): 这个时间窗口内点够这么多次，
        # 视为"厌倦"，改播 annoyed_spam_click 而不是普通互动台词。
        "body_click_window_seconds": 5,
        "body_click_threshold": 4,
        # 小花说话中点了功能项(打断): 累计这么多次打断触发一次"喷人"
        # (rant_interrupt)，之后计数清零并进入下面的喷人冷却，冷却内只降级
        # 播 interrupted_reply(不满)。
        "interrupt_rant_threshold": 3,
        "interrupt_rant_cooldown_seconds": 300,
        # "锁定"反复切换专属三级吐槽(不走上面的通用features阶梯，见
        # dialogue_arbiter.py的_wrap_lock_provider)：切换计数1~t1播正常
        # lock_on/lock_off，t1+1~t2播"无语"，t2+1~t3播"不耐烦"，t3+1播
        # "爆发"(固定一条)后计数清零，进入上面的interrupt_rant_cooldown_seconds
        # 冷却(复用，不单独配置)。
        "lock_toggle_thresholds": [3, 6, 9],
        # 爆发播完后，锁定开关进入这么多秒的"闹脾气"冷区：期间切换请求整个
        # 无效(功能不生效、不出台词)，到点后自动恢复正常，不会永久锁死。
        "lock_toggle_tantrum_seconds": 10,
    },
}


def _deep_merge(defaults, loaded):
    out = dict(defaults)
    for k, v in loaded.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            return _deep_merge(DEFAULTS, loaded)
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULTS)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "FloweyPet"


def set_autostart(enabled: bool):
    target = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        command = f'"{target}"'
    else:
        main_py = Path(__file__).resolve().parent.parent / "main.py"
        command = f'"{target}" "{main_py}"'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, _RUN_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        pass
