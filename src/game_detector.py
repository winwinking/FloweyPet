# -*- coding: utf-8 -*-
"""Game-launch/exit easter egg: detects UNDERTALE/DELTARUNE by polling the
system process-name list (ctypes CreateToolhelp32Snapshot, kernel32 only --
never a window title, never window content, never a screenshot) on a QTimer
and speaks a matching line from dialogues_game.json.

Each of the two games gets its own tiny not_running/running state machine.
A launch (not_running -> running) fires the launch egg (first-ever pick a
fixed "first" line and persist ut_first_seen/dr_first_seen via stats.py;
every later launch rolls dialogues_game.json's normal/rare launch pools).
An exit (running -> not_running) fires the exit egg, but only once the
process has been absent for `exit_confirm_polls` consecutive polls in a
row -- a single missed poll (process list momentarily not reflecting
reality) can't misfire an exit line.

Priority: an egg line here outranks idle/AFK ambient chatter (see
PetWindow._on_idle_chat_tick / _trigger_afk_unless_awaiting, both gated on
is_any_game_running()) but is outranked by an actual reminder/chase popup
on screen -- if pet.is_reminder_popup_active() is True at the moment a
launch/exit fires, the egg line is dropped on the spot, never queued. Same
for the exit-lock (pet.is_exiting()) and for a hidden pet
(not pet.isVisible()): the state machine still advances and the line is
still "consumed" (first-seen flag set, trigger counter incremented) --
only the on-screen bubble itself is skipped, with no replay later.

A fullscreen game covers the pet's whole screen, so a launch egg fired the
instant it starts would just be spoken to nobody. `game_detect.game_egg_display_mode`
picks how that's handled (see game_egg_display.py):
  - "topmost" (default): only if the foreground window is actually this
    game filling its monitor, temporarily raise the pet + dialogue box's
    native Z-order (never Qt's WindowStaysOnTopHint -- that tears down and
    recreates the window, a visible flicker) for exactly as long as the
    bubble is up, then restore each window to whatever its own "置顶"
    state already was (see PetWindow.is_pinned_on_top/DialogueBox.is_pinned).
    If even that doesn't actually win (an exclusive-fullscreen surface can
    sit above HWND_TOPMOST), this one occurrence downgrades itself into the
    behavior below instead of talking to a covered screen.
  - "deferred": never grabs the screen -- a launch line is held in
    `_pending_deferred[key]` until _poll() notices the foreground window is
    no longer this game (the user tabbed out) while it's still running, or
    discards it outright if the game exits without the user ever tabbing
    out (so "you started it" and "you're done" never land back to back).
"""
import json
import random

from PyQt6.QtCore import QObject, QTimer

import dialogue_log
import game_egg_display
from error_log import log_exception, log_message
from paths import DIALOGUE_GAME_PATH
from process_snapshot import running_process_names

GAME_KEYS = ("ut", "dr")
DEFAULT_PROCESS_NAMES = {"ut": "UNDERTALE.exe", "dr": "DELTARUNE.exe"}


def _validate_entry(entry):
    """Same lines+faces contract as dialogues_chase.json/dialogues_spam.json."""
    if not isinstance(entry, dict):
        return False, "entry is not a JSON object"
    lines = entry.get("lines")
    faces = entry.get("faces")
    if not isinstance(lines, list) or not lines or not all(isinstance(t, str) and t for t in lines):
        return False, "'lines' must be a non-empty array of non-empty strings"
    if not isinstance(faces, list) or len(faces) != len(lines):
        return False, "'faces' must be an array the same length as 'lines'"
    for slot in faces:
        if not isinstance(slot, list) or not slot or not all(isinstance(tag, str) and tag for tag in slot):
            return False, "each 'faces' slot must be a non-empty array of face-tag strings"
    return True, ""


def _entry_to_segments(entry):
    segments = []
    for line, faces in zip(entry["lines"], entry["faces"]):
        face = faces[0] if len(faces) == 1 else list(faces)
        segments.append({"text": line, "face": face})
    return segments


class GameDetector(QObject):
    def __init__(
        self,
        config,
        dm,
        pet,
        stats,
        process_name_provider=running_process_names,
        foreground_name_provider=game_egg_display.foreground_process_name,
        presenter=None,
    ):
        super().__init__()
        self.dm = dm
        self.pet = pet
        self.stats = stats
        self._enum = process_name_provider
        self._foreground_name = foreground_name_provider
        self._pools = self._load_pools()

        cfg = config.get("game_detect", {})
        self._process_names = cfg.get("process_names") or dict(DEFAULT_PROCESS_NAMES)
        self._exit_confirm_polls = max(1, cfg.get("exit_confirm_polls", 2))
        self._rare_weight = cfg.get("rare_launch_weight", 15)
        poll_seconds = max(1, cfg.get("poll_interval_seconds", 5))
        self._display_mode = cfg.get("game_egg_display_mode", "topmost")
        if self._display_mode not in ("topmost", "deferred"):
            self._display_mode = "topmost"

        self._states = {key: "not_running" for key in GAME_KEYS}
        self._miss_counts = {key: 0 for key in GAME_KEYS}
        # key -> {"pool_name", "segments"} for a launch line held back either
        # because the whole session is in "deferred" mode, or because a
        # "topmost" attempt got occluded anyway (exclusive fullscreen) and
        # downgraded itself for this one occurrence -- see _downgrade_to_deferred.
        self._pending_deferred = {}

        self._presenter = presenter or game_egg_display.TopmostEggPresenter(
            guard=game_egg_display.TopmostGuard(
                poll_interval_ms=max(30, cfg.get("occlusion_poll_interval_ms", 150)),
                max_duration_ms=max(1000, cfg.get("occlusion_guard_max_ms", 60000)),
            )
        )

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        if cfg.get("enabled", True):
            self._timer.start(poll_seconds * 1000)

    def is_topmost_guard_active(self):
        """Public hook for TopmostKeeper: True while a game-egg temporary
        topmost cycle is running."""
        return self._presenter.is_guard_active()

    # ------------------------------------------------------------- pools
    _EXPECTED_POOLS = tuple(
        f"game_{key}_{suffix}" for key in GAME_KEYS for suffix in ("first", "launch", "launch_rare", "exit")
    )

    def _load_pools(self):
        try:
            with open(DIALOGUE_GAME_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            log_message(
                f"dialogues_game: {DIALOGUE_GAME_PATH} 不存在，游戏彩蛋台词将没有内容可播（进程检测仍会正常运行）"
            )
            return {}
        except (OSError, json.JSONDecodeError) as e:
            log_exception(f"dialogues_game: 无法加载 {DIALOGUE_GAME_PATH}（整个文件解析失败，所有彩蛋池将为空）: {e}")
            return {}
        if not isinstance(raw, dict):
            log_message(f"dialogues_game: 顶层必须是JSON对象，实际是 {type(raw).__name__}，所有彩蛋池将为空")
            return {}

        pools = {}
        for pool_name, entries in raw.items():
            if pool_name.startswith("_"):
                continue
            if not isinstance(entries, list):
                log_message(f"dialogues_game: 池 {pool_name} 不是数组（实际是 {type(entries).__name__}），已跳过整池")
                pools[pool_name] = []
                continue
            cleaned = []
            for i, entry in enumerate(entries):
                ok, reason = _validate_entry(entry)
                if not ok:
                    log_message(f"dialogues_game: skipping malformed entry {pool_name}[{i}]: {reason}")
                    continue
                cleaned.append(entry)
            if len(cleaned) != len(entries):
                log_message(
                    f"dialogues_game: 池 {pool_name} 共 {len(entries)} 条，{len(entries) - len(cleaned)} 条因格式错误被跳过，实际可用 {len(cleaned)} 条"
                )
            pools[pool_name] = cleaned

        # Startup accounting -- exactly what investigation step 1 asks for:
        # confirm every expected pool actually loaded with entries, and call
        # out anything that's missing/empty loudly instead of only ever
        # discovering it later via a silent "pool empty, skip" at trigger time.
        for pool_name in self._EXPECTED_POOLS:
            if pool_name not in pools:
                log_message(f"dialogues_game: 池 {pool_name} 在json里完全不存在（键缺失），该触发点将永远静默跳过")
            else:
                log_message(f"dialogues_game: 池 {pool_name} 加载完成，可用条目数={len(pools[pool_name])}")
        return pools

    # ------------------------------------------------------------- polling
    def is_any_game_running(self):
        return any(state == "running" for state in self._states.values())

    def _poll(self):
        running_names = self._enum()
        # Only pay for a foreground-window lookup when something is
        # actually waiting on it (deferred mode, or a topmost-mode
        # occurrence that downgraded itself -- see _pending_deferred).
        foreground_name = self._foreground_name() if self._pending_deferred else None
        for key in GAME_KEYS:
            proc_name = (self._process_names.get(key) or DEFAULT_PROCESS_NAMES[key]).lower()
            present = proc_name in running_names
            if present:
                self._miss_counts[key] = 0
                if self._states[key] == "not_running":
                    self._states[key] = "running"
                    self._on_launch(key)
                elif (
                    key in self._pending_deferred
                    and foreground_name is not None
                    and foreground_name != proc_name
                ):
                    self._fire_deferred(key)
            elif self._states[key] == "running":
                self._miss_counts[key] += 1
                if self._miss_counts[key] >= self._exit_confirm_polls:
                    self._states[key] = "not_running"
                    self._miss_counts[key] = 0
                    if key in self._pending_deferred:
                        dialogue_log.log_event(
                            f"游戏彩蛋：[{key}] deferred 启动台词作废（游戏已退出，期间未曾切出过）"
                        )
                        del self._pending_deferred[key]
                    self._on_exit(key)

    # ------------------------------------------------------------ triggers
    def _on_launch(self, key):
        already_seen = self.stats.is_game_first_seen(key)
        dialogue_log.log_event(f"游戏彩蛋：[{key}] 启动检测到，{key}_first_seen(启动时读到的值)={already_seen}")
        if not already_seen:
            self.stats.mark_game_first_seen(key)
            dialogue_log.log_event(
                f"游戏彩蛋：[{key}] 首次触发，已标记 {key}_first_seen=True 并落盘（落盘后复读校验={self.stats.is_game_first_seen(key)}）"
            )
            tier, pool_name = "first", f"game_{key}_first"
        elif random.random() * 100 < self._rare_weight:
            tier, pool_name = "rare", f"game_{key}_launch_rare"
        else:
            tier, pool_name = "normal", f"game_{key}_launch"
        self._trigger(key, "launch", tier, pool_name)

    def _on_exit(self, key):
        self._trigger(key, "exit", "exit", f"game_{key}_exit")

    def _trigger(self, key, event, tier, pool_name):
        pool = self._pools.get(pool_name)
        if not pool:
            dialogue_log.log_event(
                f"游戏彩蛋警告：[{key}] {event}/{tier} 池 {pool_name} 为空（键缺失或全部条目校验失败，见启动日志），本次跳过不播"
            )
            return
        index = random.randrange(len(pool))
        entry = pool[index]
        segments = _entry_to_segments(entry)
        self.stats.record_game_egg_trigger()
        preview = entry["lines"][0][:20]
        dialogue_log.log_event(
            f"游戏彩蛋触发：[{key}] {event}/{tier} 池={pool_name} 池大小={len(pool)} 抽中索引={index} 内容摘要=\"{preview}\" 显示模式={self._display_mode}"
        )

        if self._display_mode == "deferred" and event == "launch":
            self._pending_deferred[key] = {"pool_name": pool_name, "segments": segments}
            dialogue_log.log_event(f"游戏彩蛋：[{key}] deferred 模式，暂不弹出，等待切出游戏后播放")
            return

        self._show_now_or_topmost(key, pool_name, segments)

    def _fire_deferred(self, key):
        payload = self._pending_deferred.pop(key, None)
        if not payload:
            return
        dialogue_log.log_event(f"游戏彩蛋：[{key}] 已切出游戏，播放此前攒下的启动台词")
        self._show_now_or_topmost(key, payload["pool_name"], payload["segments"])

    def _show_now_or_topmost(self, key, pool_name, segments):
        if not self._gate_ok(key, pool_name):
            return
        if self._display_mode != "topmost":
            self._display_now(pool_name, segments)
            return
        proc_name = (self._process_names.get(key) or DEFAULT_PROCESS_NAMES[key]).lower()
        self._presenter.present(
            key=key,
            pet=self.pet,
            dialogue_box=self.pet.dialogue_box,
            process_name_lower=proc_name,
            show_now=lambda: self._display_now(pool_name, segments),
            on_downgrade=lambda: self._downgrade_to_deferred(key, pool_name, segments),
            log=dialogue_log.log_event,
        )

    def _gate_ok(self, key, pool_name):
        """Same higher-priority-content checks the original single-shot
        _trigger always did, just factored out so both an immediate
        launch/exit and a later deferred fire-off share them."""
        if self.pet.is_exiting() or self.pet.is_reminder_popup_active():
            dialogue_log.log_event(f"游戏彩蛋：[{key}] {pool_name} 被更高优先级对话抑制，静默跳过（不排队补播）")
            return False
        if not self.pet.isVisible():
            dialogue_log.log_event(f"游戏彩蛋：[{key}] {pool_name} 桌宠隐身中，跳过弹泡（不补播）")
            return False
        return True

    def _display_now(self, pool_name, segments):
        self.pet.force_end_bubble()
        self.dm.preempt(pool_name, segments)

    def _downgrade_to_deferred(self, key, pool_name, segments):
        self.pet.force_end_bubble()
        self._pending_deferred[key] = {"pool_name": pool_name, "segments": segments}
