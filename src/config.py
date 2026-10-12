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

from error_log import log_exception, log_message
from paths import CONFIG_PATH, IS_DEV
from user_data import atomic_write_json, quarantine_bad_file

# Pet size tiers (multipliers, biggest first). Sprites are drawn at
# BASE_SCALE(4) * tier, so every tier here gives a whole-number upscale
# factor -- pixel art loses rows at a fractional one.
DEFAULT_PET_SIZE_TIERS = [2.0, 1.5, 1.25, 1.0, 0.75, 0.5, 0.25]

DEFAULTS = {
    "position": None,  # None => PetWindow picks a sensible on-screen default
    "pet_scale": 1.0,             # current size tier multiplier, see pet_size_tiers
    "pet_size_tiers": list(DEFAULT_PET_SIZE_TIERS),  # list of size tiers, see DEFAULT_PET_SIZE_TIERS
    # Bookkeeping for the one-time 3.3.0 tier-list replacement (see
    # apply_size_settings). Default 1 so a config.json without the key is
    # treated as holding the old 85/70/55% tiers.
    "size_tiers_schema": 1,
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
    "idle_animation": True,  # False: idle shows one still frame instead of looping the 3 idle frames
    "menu_sfx_enabled": True,  # independent of "muted": only gates menu move/select blips
    "audio_debug": False,               # log every play attempt (not just failures/rebuilds) to audio.log
    "audio_watchdog_threshold": 5,      # consecutive play failures across all channels before a forced full rebuild
    "audio_rotation_seconds": 90,        # unconditionally rebuild every sound player on this interval (0 disables)
    "audio_stress_test_on_startup": False,  # dev-only: fire 200 randomized mixed plays at launch, see audio.py
    "font_scale": 1.75,
    "always_on_top": True,
    "autostart": False,
    "check_updates": True,  # 是否检查更新：只读取一个公开的 version.json，不发送任何数据，只提示、不自动替换
    "update_url": "https://raw.githubusercontent.com/winwinking/FloweyPet/master/version.json",  # 版本信息文件的地址
    "update_check_hours": 3,  # 启动后每隔多少小时再检查一次
    "update_first_delay_seconds": 8,  # 启动后等多少秒做第一次检查，避开开场动画
    "last_announced_version": None,  # 上次已经播报过的新版本号，同一个版本只说一次
    "first_run_done": False,
    "birthday": None,          # "MM-DD" or null
    "install_date": None,      # "YYYY-MM-DD"
    "last_active_date": None,  # "YYYY-MM-DD", for streak tracking
    "streak_days": 0,
    "total_clicks": 0,
    "tamper_detect_count": 0,  # 反作弊：累计检测到 total_clicks 被手动篡改的次数，永不清零，见 anticheat.py
    "tamper_locked": False,    # tamper_detect_count > 5 后置 True，永久生效——此后篡改只剩极简回应，不再纠缠
    "neg_tamper_count": 0,     # 反作弊：累计检测到篡改成负数的次数，用于区分 NEG01(首次)/NEG02(第二次起)
    "last_close_time": None,   # ISO timestamp
    "close_count_recent": 0,
    "greeted_streaks": [],     # which streak milestones already shown
    "greeted_click_milestones": [],  # which total_clicks milestones (special_milestone_*) already shown
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


def _default_copy():
    # Deep copy: DEFAULTS nests dicts/lists, a shallow dict() would let edits
    # to the returned config leak back into DEFAULTS.
    return json.loads(json.dumps(DEFAULTS))


def load_config():
    if not CONFIG_PATH.exists():
        return _default_copy()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except OSError as e:
        log_message(f"load_config: 读取 {CONFIG_PATH} 失败（{e!r}），本次使用默认值，文件未改动")
        return _default_copy()
    except ValueError as e:  # json.JSONDecodeError / UnicodeDecodeError
        moved = quarantine_bad_file(CONFIG_PATH)
        log_message(f"load_config: {CONFIG_PATH} 不是合法 JSON（{e!r}），已改名为 {moved}，使用默认值")
        return _default_copy()
    if not isinstance(loaded, dict):
        moved = quarantine_bad_file(CONFIG_PATH)
        log_message(f"load_config: {CONFIG_PATH} 内容不是 JSON 对象，已改名为 {moved}，使用默认值")
        return _default_copy()
    return _deep_merge(DEFAULTS, loaded)


def snap_pet_scale(value, tiers):
    """The tier closest to `value` (ties go to the larger tier). A
    non-numeric value counts as 1.0; an unusable tier list leaves the value
    alone."""
    ok = [t for t in tiers if isinstance(t, (int, float)) and not isinstance(t, bool)] if isinstance(tiers, list) else []
    if not ok:
        return value
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        value = 1.0
    return min(ok, key=lambda t: (round(abs(t - value), 9), -t))


def apply_size_settings(cfg):
    """Startup fix-ups for the size settings; call right after load_config()
    and before anything reads pet_scale. (1) One-time: size_tiers_schema < 2
    -> replace pet_size_tiers with the new list. (2) Every start: if
    pet_scale isn't one of the tiers, snap it to the nearest. Saves once if
    anything changed; touches only pet_size_tiers, size_tiers_schema and
    pet_scale. Returns True if it changed something."""
    changed = False
    if cfg.get("size_tiers_schema", 1) < 2:
        log_message(f"config migrate: 大小挡位改为新列表，旧列表 {cfg.get('pet_size_tiers')!r}")
        cfg["pet_size_tiers"] = list(DEFAULT_PET_SIZE_TIERS)
        cfg["size_tiers_schema"] = 2
        changed = True
    tiers = cfg.get("pet_size_tiers")
    scale = cfg.get("pet_scale", 1.0)
    if isinstance(tiers, list) and scale not in tiers:
        snapped = snap_pet_scale(scale, tiers)
        if snapped != scale:
            log_message(f"config: pet_scale {scale!r} 不在挡位 {tiers!r} 里，吸附到 {snapped!r}")
            cfg["pet_scale"] = snapped
            changed = True
    if changed:
        save_config(cfg)
    return changed


def save_config(cfg):
    try:
        atomic_write_json(CONFIG_PATH, cfg)
    except OSError:
        log_exception("save_config")


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# A source run and the packaged exe must not share one autostart entry:
# otherwise toggling autostart from a source run would overwrite the exe's
# entry with "python main.py" (and the exe's sync would overwrite it back).
_RUN_NAME = "FloweyPetDev" if IS_DEV else "FloweyPet"


def read_autostart_command():
    """The command string stored in the HKCU Run key for FloweyPet, or None
    if unset/unreadable. Read-only."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, _RUN_NAME)
        return value if isinstance(value, str) else None
    except OSError:
        return None


def _autostart_command():
    """The command the Run key should hold for the program that is running now."""
    target = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return f'"{target}"'
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return f'"{target}" "{main_py}"'


def set_autostart(enabled: bool):
    """Adds/removes the Run entry. True on success, False if the registry
    refused (OSError)."""
    command = _autostart_command()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, _RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def sync_autostart(config, read_command=None, write=None, expected=None, is_dev=None):
    """At startup, if autostart is switched on, make sure the Run entry points
    at the program that is running now (the folder may have moved or the exe
    been replaced). Never turns autostart on or off by itself and never
    raises. Returns "skipped_dev" | "off" | "ok" | "updated" | "failed"."""
    try:
        if (IS_DEV if is_dev is None else is_dev):
            return "skipped_dev"
        if config.get("autostart") is not True:
            return "off"
        current = (read_autostart_command if read_command is None else read_command)()
        want = _autostart_command() if expected is None else expected
        if isinstance(current, str) and current.strip().lower() == want.strip().lower():
            return "ok"
        ok = (set_autostart if write is None else write)(True)
        return "updated" if ok else "failed"
    except Exception:  # noqa: BLE001 - startup must never break on this
        return "failed"


# ------------------------------------------------------- anti-cheat shadow copy
# total_clicks lives in config.json, in the per-user data directory
# (paths.USER_DATA_DIR) where anyone can open and hand-edit it. To detect that, we keep a second copy
# somewhere a casual editor won't think to look or touch alongside it: the
# registry, under the same HKCU root the autostart key above already uses.
# Only the app's own legitimate click-increment path writes this -- see
# anticheat.py's SaveTamperGuard, which compares the two at every startup.
_ANTICHEAT_KEY = r"Software\FloweyPet"
# Source runs and the packaged exe must not share one shadow value: they keep
# separate config.json files, so a shared value would look like tampering.
_ANTICHEAT_VALUE = "ShadowTotalClicksDev" if IS_DEV else "ShadowTotalClicks"


def read_shadow_clicks():
    """Returns the last known-good total_clicks the app itself wrote, or
    None if the registry value has never been set (fresh install, or the
    key was wiped) -- callers must treat None as "no baseline yet", not 0."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _ANTICHEAT_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, _ANTICHEAT_VALUE)
        return int(value)
    except (OSError, ValueError):
        return None


def write_shadow_clicks(value):
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _ANTICHEAT_KEY) as key:
            winreg.SetValueEx(key, _ANTICHEAT_VALUE, 0, winreg.REG_SZ, str(int(value)))
    except OSError:
        pass
