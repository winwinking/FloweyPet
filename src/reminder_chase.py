# -*- coding: utf-8 -*-
"""ReminderChaseController: owns the complete lifecycle of one reminder
round -- IDLE -> SHOWING(弹窗中) -> CHASING(等待下次追讨) -> ended
(RESPONDED / DISMISSED / EXPIRED).

Root-cause history (2026-07-12 investigation, see reminder.log for the
full write-up): the previous implementation had no independent scheduler.
The next chase stage was only ever scheduled inside the popup's
`finished` signal handler, hop by hop, so any close path that skipped
`finished` -- most notably PetWindow's hide/quit burrow animation calling
DialogueBox.stop_immediately() -- left the round stuck in its awaiting
state forever: no further chase popups, proactive chatter suppressed
until the next round happened to supersede it. On top of that the 60s
confirm-hold used bare QTimer.singleShot calls that could never be
cancelled, so a stale hold timer from an earlier popup could prematurely
close a later one, and superseded rounds could only *guard against* their
leftover timers (round-id checks), never actually stop them. This
controller fixes all three by construction:

* Chase stage times are ABSOLUTE offsets from the round start
  (reminder_pursuit_stages_minutes, default the 5th/10th/20th/30th
  minute), scheduled on a controller-owned, stoppable QTimer -- fully
  independent of any popup's lifecycle.
* Every popup gets a failsafe timer slightly longer than its on-screen
  hold: if the popup is destroyed without reporting an outcome (hidden
  pet, quit animation, anything), the round still flows on as a timeout
  instead of hanging.
* Ending a round stops both timers outright; nothing is left to fire late.

All timers are main-thread QTimers (this QObject lives on the GUI
thread); no time.sleep, no worker threads touch the UI.

Popup flow per round (吃饭/喝水, chase-eligible):
  stage 0 (首次): buttons [确定][等等]; 确定 ends RESPONDED, 等等/超时
  enter the chase.
  stages 1..4 (第5/10/20/30分钟): buttons [确定][知道了]; 确定 ends
  RESPONDED, 知道了 ends DISMISSED (已敷衍, counted in rounds but not in
  responded), 超时 advances -- after the final stage it ends EXPIRED
  (未响应).
睡觉 shows once with [确定][等等] and never chases.

If a different kind comes due while a round is already active (SHOWING or
CHASING), it is NOT cancelled and it does NOT cancel the round in
progress -- it's queued (one pending slot per kind) and starts for real
the moment the current round fully ends, whatever that end state was.
2026-07-12 (second pass): replaces an earlier "new round instantly
supersedes/voids the old one" design -- every reminder now gets its full
run instead of an early one being cut short just because a second kind
happened to come due first.

reminder_test_mode compresses 1 minute -> 1 second for the stage points;
the popup hold shrinks to reminder_test_mode_hold_seconds (default 3s)
so the whole chain can be watched end-to-end in under a minute.
"""
import json
import random
import time
from datetime import datetime, timedelta

from PyQt6.QtCore import QObject, QTimer

import reminder_log
from error_log import log_exception, log_message
from paths import DIALOGUE_CHASE_PATH

PRI_REMINDER = 40  # mirrors dialogue_manager.PRI_REMINDER

DEFAULT_STAGES_MINUTES = [5, 10, 20, 30]
DEFAULT_HOLD_SECONDS = 60  # 首次提醒(和睡觉)弹窗停留
DEFAULT_CHASE_HOLD_SECONDS = 30  # 追讨弹窗停留
DEFAULT_TEST_HOLD_SECONDS = 3
FAILSAFE_GRACE_MS = 8_000  # extra slack past the hold before assuming the popup died silently

CHASE_ELIGIBLE_KINDS = ("water", "meal")
KIND_LABELS = {"water": "喝水", "meal": "吃饭", "sleep": "睡觉"}
_KIND_POOL = {"water": "chase_water", "meal": "chase_food"}
_STAGE_KEYS = ("first", "m5", "m10", "m20", "m30")

# Round-end states
RESPONDED = "RESPONDED"
DISMISSED = "DISMISSED"
EXPIRED = "EXPIRED"

_END_LABELS = {
    RESPONDED: "响应",
    DISMISSED: "敷衍（点了知道了）",
    EXPIRED: "最终未响应",
}

# Built-in fallbacks when a pool is missing/empty so the flow never stalls
# on an incomplete dialogues_chase.json -- deliberately bland, they exist
# to be replaced by the real lines.
_PLACEHOLDER = {
    "popup": {"lines": ["……提醒时间到了。（该级台词缺失）"], "faces": [["neutral"]]},
    "confirm": {"lines": ["……嗯。去吧。"], "faces": [["neutral"]]},
    "wait": {"lines": ["……好，那待会儿见。"], "faces": [["neutral"]]},
    "dismiss": {"lines": ["……知道了是吧。记下了。"], "faces": [["neutral"]]},
    "timeout": {"lines": ["……没有回应。"], "faces": [["neutral"]]},
}


def _validate_entry(entry):
    """Same lines+faces contract as dialogues_info.json (see
    dialogue_manager._validate_info_entry). Returns (ok, reason)."""
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


class ReminderChaseController(QObject):
    IDLE = "IDLE"
    SHOWING = "SHOWING"
    CHASING = "CHASING"

    def __init__(self, config, dialogue_manager, stats=None):
        super().__init__()
        self.config = config
        self.dm = dialogue_manager
        self.stats = stats
        self.test_mode = bool(config.get("reminder_test_mode", False))
        self._pools = self._load_pools()

        self._state = self.IDLE
        self._kind = None
        self._round_id = 0
        self._stage_idx = 0  # 0 = first popup, 1..len(stages) = chase popups
        self._round_start = 0.0  # monotonic seconds
        self._pending_queue = []  # kinds waiting for the current round to end
        self._exit_locked = False  # True once the user clicks 退出, see lock_for_exit()

        # The ONLY two timers in the system, both owned here so ending a
        # round can stop them dead (no stale singleShots left behind).
        self._stage_timer = QTimer(self)
        self._stage_timer.setSingleShot(True)
        self._stage_timer.timeout.connect(self._on_stage_due)

        self._failsafe_timer = QTimer(self)
        self._failsafe_timer.setSingleShot(True)
        self._failsafe_timer.timeout.connect(self._on_popup_failsafe)

    # ---------------------------------------------------------------- config
    def _stages_minutes(self):
        stages = self.config.get("reminder_pursuit_stages_minutes", DEFAULT_STAGES_MINUTES)
        if not isinstance(stages, list) or not stages:
            return list(DEFAULT_STAGES_MINUTES)
        return stages

    def _stage_unit_seconds(self):
        # test mode: "1 minute" of chase schedule = 1 real second
        return 1 if self.test_mode else 60

    def hold_ms(self):
        """首次提醒/睡觉弹窗的按钮停留时长 (default 60s)."""
        if self.test_mode:
            return int(self.config.get("reminder_test_mode_hold_seconds", DEFAULT_TEST_HOLD_SECONDS) * 1000)
        return int(self.config.get("reminder_pursuit_hold_seconds", DEFAULT_HOLD_SECONDS) * 1000)

    def chase_hold_ms(self):
        """追讨弹窗(第5/10/20/30分钟)的按钮停留时长 (default 30s)."""
        if self.test_mode:
            return int(self.config.get("reminder_test_mode_hold_seconds", DEFAULT_TEST_HOLD_SECONDS) * 1000)
        return int(self.config.get("reminder_chase_hold_seconds", DEFAULT_CHASE_HOLD_SECONDS) * 1000)

    def hold_ms_for_category(self, category):
        """Per-popup hold for PetWindow: chase-stage popups sit for the
        shorter chase hold, first/sleep popups for the full one."""
        if category.startswith("chase_popup_stage_"):
            return self.chase_hold_ms()
        return self.hold_ms()

    def is_chase_eligible(self, kind):
        return kind in CHASE_ELIGIBLE_KINDS

    def is_round_active(self):
        """True from first popup until the round fully ends -- suppresses
        idle/AFK chatter for the whole round including CHASING waits."""
        return self._state != self.IDLE

    # ----------------------------------------------------------- line pools
    def _load_pools(self):
        try:
            with open(DIALOGUE_CHASE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            log_message(f"dialogues_chase: {DIALOGUE_CHASE_PATH} not found, chase lines fall back to placeholders")
            return {}
        except (OSError, json.JSONDecodeError):
            log_exception(f"dialogues_chase: could not load {DIALOGUE_CHASE_PATH}")
            return {}
        if not isinstance(raw, dict):
            log_message("dialogues_chase: top level must be a JSON object")
            return {}

        cleaned = {}
        for pool_name, stages in raw.items():
            if pool_name.startswith("_") or not isinstance(stages, dict):
                continue
            cleaned_stages = {}
            for stage_key, roles in stages.items():
                # sleep_once has role lists directly at this level
                if isinstance(roles, list):
                    cleaned_stages[stage_key] = self._clean_entries(
                        roles, f"{pool_name}.{stage_key}"
                    )
                elif isinstance(roles, dict):
                    cleaned_stages[stage_key] = {
                        role: self._clean_entries(entries, f"{pool_name}.{stage_key}.{role}")
                        for role, entries in roles.items()
                        if isinstance(entries, list)
                    }
            cleaned[pool_name] = cleaned_stages
        return cleaned

    @staticmethod
    def _clean_entries(entries, where):
        cleaned = []
        for i, entry in enumerate(entries):
            ok, reason = _validate_entry(entry)
            if not ok:
                log_message(f"dialogues_chase: skipping malformed entry {where}[{i}]: {reason}")
                reminder_log.log_event(f"台词坏条目已跳过：{where}[{i}]（{reason}）")
                continue
            cleaned.append(entry)
        return cleaned

    def _pick(self, kind, stage_idx, role):
        """Random entry from the pool for (kind, stage, role); placeholder
        (never None) when the pool is missing or empty."""
        if kind == "sleep":
            pool = (self._pools.get("sleep_once") or {}).get(role) or []
        else:
            stages = self._pools.get(_KIND_POOL.get(kind, ""), {})
            stage_key = _STAGE_KEYS[min(stage_idx, len(_STAGE_KEYS) - 1)]
            pool = (stages.get(stage_key) or {}).get(role) or []
        if not pool:
            reminder_log.log_event(f"台词池缺失，使用占位文本：{kind} 第{stage_idx}级 {role}")
            return _PLACEHOLDER.get(role, _PLACEHOLDER["popup"])
        return random.choice(pool)

    def _speak(self, category, kind, stage_idx, role):
        entry = self._pick(kind, stage_idx, role)
        self.dm.speak(category, _entry_to_segments(entry), priority=PRI_REMINDER, bypass_interval=True)

    # -------------------------------------------------------------- rounds
    def start_round(self, kind):
        """A reminder came due. If a previous round is still running (even
        deep in CHASING), this one does NOT cancel it -- every reminder
        gets its full run. It queues (one pending slot per kind, so firing
        the same kind twice while it's already queued doesn't stack) and
        starts for real the moment the current round fully ends, whatever
        that round's own end state was."""
        if self._exit_locked:
            reminder_log.log_event(f"提醒已丢弃（程序正在退出）：{KIND_LABELS.get(kind, kind)}")
            return
        if self._state != self.IDLE:
            if kind not in self._pending_queue:
                self._pending_queue.append(kind)
                reminder_log.log_event(
                    f"提醒排队：{KIND_LABELS.get(kind, kind)}"
                    f"（等待当前 {KIND_LABELS.get(self._kind, self._kind)} 轮结束后开始）"
                )
            return
        self._start_round_now(kind)

    def lock_for_exit(self):
        """Called once, the instant the user clicks 退出 (see PetWindow.
        request_quit): no further reminder round may start from this point
        on, and any round already in progress (SHOWING or CHASING, popup
        on screen or not) is voided immediately and silently rather than
        waiting for it to resolve on its own -- reg 1's "提醒/追讨若在进行
        直接静默作废"."""
        self._exit_locked = True
        if self._state != self.IDLE:
            reminder_log.log_event(f"退出锁定：作废进行中的提醒轮次：{KIND_LABELS.get(self._kind, self._kind)}")
        if self._pending_queue:
            reminder_log.log_event(f"退出锁定：作废排队中的提醒：{[KIND_LABELS.get(k, k) for k in self._pending_queue]}")
        self._pending_queue = []
        self._stage_timer.stop()
        self._failsafe_timer.stop()
        self._state = self.IDLE
        self._kind = None
        self._stage_idx = 0

    def _start_round_now(self, kind):
        self._round_id += 1
        self._kind = kind
        self._stage_idx = 0
        self._round_start = time.monotonic()
        self._show_popup()

    def _show_popup(self):
        self._state = self.SHOWING
        kind = self._kind
        idx = self._stage_idx
        if kind == "sleep":
            category = "chase_popup_sleep"
            label = "（单次，无追讨）"
        elif idx == 0:
            category = f"chase_popup_first_{kind}"
            label = "（首次）"
        else:
            minutes = self._stages_minutes()[idx - 1]
            category = f"chase_popup_stage_{kind}"
            label = f"追讨第{idx}次（第{minutes}分钟）"
        reminder_log.log_event(f"提醒弹出：{KIND_LABELS.get(kind, kind)}{label}")
        self._speak(category, kind, idx, "popup")
        # Failsafe: popup typing time varies, so give it the hold plus a
        # generous grace. If no outcome came back by then, the popup was
        # closed without ever reporting (see module docstring) -- flow on
        # as a timeout rather than hanging the round.
        hold = self.chase_hold_ms() if (idx > 0 and kind != "sleep") else self.hold_ms()
        self._failsafe_timer.start(hold + FAILSAFE_GRACE_MS + 30_000)

    def on_popup_outcome(self, outcome):
        """Called by PetWindow when a chase popup finished: outcome is
        'confirm' | 'wait' | 'dismiss' | 'timeout'. Anything arriving while
        no popup is awaited (stale/duplicate report) is a no-op."""
        if self._state != self.SHOWING:
            return
        self._failsafe_timer.stop()
        kind = self._kind
        idx = self._stage_idx
        label = KIND_LABELS.get(kind, kind)
        stage_note = "首次" if idx == 0 else f"追讨第{idx}级"

        if outcome == "confirm":
            reminder_log.log_event(f"点击确定：{label}（{stage_note}，已响应）")
            self._speak(self._reaction_category(kind), kind, idx, "confirm")
            self._end_round(RESPONDED)
            return

        if outcome == "dismiss" and idx > 0 and self.is_chase_eligible(kind):
            reminder_log.log_event(f"点击知道了：{label}（{stage_note}，记为已敷衍）")
            self._speak(self._reaction_category(kind), kind, idx, "dismiss")
            self._end_round(DISMISSED)
            return

        # wait (等等, first popup / sleep) or timeout
        how = "点击等等" if outcome == "wait" else "超时未点击"
        role = "wait" if outcome == "wait" else "timeout"
        reminder_log.log_event(f"{how}：{label}（{stage_note}）")
        self._speak(self._reaction_category(kind), kind, idx, role)

        if not self.is_chase_eligible(kind):
            # 睡觉只弹一次：确定以外的一切结果都直接收场，不追讨也不记未响应
            reminder_log.log_event(f"本轮终止：{label}（原因：单次提醒收起，无追讨）")
            self._reset(record=None)
            return

        next_idx = idx + 1
        if next_idx > len(self._stages_minutes()):
            self._end_round(EXPIRED)
            return
        self._schedule_stage(next_idx)

    @staticmethod
    def _reaction_category(kind):
        return f"chase_reply_{kind}"

    def _schedule_stage(self, next_idx):
        """Arm the controller-owned timer for chase stage next_idx at its
        ABSOLUTE offset from the round start (第5/10/20/30分钟相对本轮起点,
        not relative to the previous popup)."""
        self._state = self.CHASING
        self._stage_idx = next_idx
        minutes = self._stages_minutes()[next_idx - 1]
        target = self._round_start + minutes * self._stage_unit_seconds()
        delay_s = max(0.05, target - time.monotonic())
        wall_target = (datetime.now() + timedelta(seconds=delay_s)).strftime("%H:%M:%S")
        reminder_log.log_event(
            f"排程追讨：{KIND_LABELS.get(self._kind, self._kind)} 第{next_idx}级（第{minutes}分钟），"
            f"目标时间 {wall_target}（{delay_s:.0f}秒后）"
        )
        self._stage_timer.start(int(delay_s * 1000))

    def _on_stage_due(self):
        if self._state != self.CHASING:
            return  # round ended between arming and firing; timer should be stopped, this is belt-and-braces
        self._show_popup()

    def _on_popup_failsafe(self):
        if self._state != self.SHOWING:
            return
        reminder_log.log_event(
            f"弹窗未正常回报结果（可能被隐身/退出动画收起），按超时流转：{KIND_LABELS.get(self._kind, self._kind)}"
        )
        self.on_popup_outcome("timeout")

    # ---------------------------------------------------------------- ending
    def _end_round(self, end_state):
        kind = self._kind
        label = KIND_LABELS.get(kind, kind)
        reminder_log.log_event(f"本轮终止：{label}（原因：{_END_LABELS[end_state]}）")
        if self.is_chase_eligible(kind):
            outcome = {
                RESPONDED: "responded",
                DISMISSED: "dismissed",
                EXPIRED: "unresponded",
            }[end_state]
        else:
            outcome = "responded" if end_state == RESPONDED else None
        self._reset(record=outcome)

    def _reset(self, record):
        if record and self.stats:
            self.stats.record_reminder_round(self._kind, record)
        # Stop BOTH timers so a dead round can never pop a stray window.
        self._stage_timer.stop()
        self._failsafe_timer.stop()
        self._state = self.IDLE
        self._kind = None
        self._stage_idx = 0
        self._start_next_pending()

    def _start_next_pending(self):
        """A different kind came due while this round was still running --
        its turn is now. See start_round()'s docstring."""
        if not self._pending_queue:
            return
        next_kind = self._pending_queue.pop(0)
        reminder_log.log_event(f"排队提醒开始：{KIND_LABELS.get(next_kind, next_kind)}")
        self._start_round_now(next_kind)
