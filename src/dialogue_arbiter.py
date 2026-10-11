# -*- coding: utf-8 -*-
"""DialogueArbiter: the single gate every *functional* dialogue request
(menu-item toggles/actions, and clicking Flowey's own body too fast) must
pass through, so repeatedly hammering a menu item can't turn into a
dialogue-spam machine gun.

Priority order this enforces (highest wins, lower ones are just dropped --
never queued, see dialogue_manager.preempt()):
    exit lock (handled by PetWindow itself, before this class is even
        consulted -- the right-click menu is unreachable while EXITING)
  > a reminder/chase popup currently on screen (is_reminder_popup_active())
  > interrupt-rant / per-feature "anger" stage (both count as 喷人)
  > per-feature "annoyance" stage / body-click "boredom"
  > per-feature "impatience" stage / interrupted-reply "不满"
  > the feature's own pre-existing normal flavor line (normal_provider)
  > (click/ambient chat and idle chatter live in DialogueManager, untouched
    by this class except that a functional line always force-ends them --
    see PetWindow.force_end_bubble())

**A feature's own currently-playing reaction is never cut short by more
clicks on that same feature** (2026-07-12 fix: clicking rapidly used to
preempt-and-restart the bubble every time, so a fast clicker would only
ever see whatever the LAST click's line was, and it looked like nothing
escalated). Instead: `_active_key` remembers which feature's reaction is
currently on screen; a same-key click while it's active just sets a
"pending" flag instead of touching the bubble at all. Once that reaction
actually finishes (PetWindow.on_bubble_finished -> on_bubble_finished()),
if a click was pending, the *next* stage speaks after a delay that grows
each time this happens back-to-back (feature_escalation_delay_steps --
1-2s, then 3-4s, ... -- reset once the feature goes fully idle). A click
on a *different* feature (or ambient chat) while something is showing is
still a genuine interruption and force-ends it as before.

Stage numbers are no longer picked from a raw click-count bracket (that
let the same stage's pool play more than once) -- each feature just
remembers "the next stage to speak" and advances it by exactly one every
time a reaction actually resolves (spoken or not), so 不耐烦/烦躁/愤怒
(=喷人)/沉默 each only ever play once per climb through the ladder.

Three independent escalation tracks, all driven by dialogues_spam.json:

* Per-feature ladder (置顶/静音/隐身/开机自启/番茄钟/提醒设置/音量/记录/
  查看信息 -- "features.<key>.stage1..4" in dialogues_spam.json): resets
  after feature_reset_seconds of disuse while below the silence stage,
  and self-recovers feature_silence_seconds after entering it. Stage 4
  ("沉默") speaks its pool's one "last word" line once, then goes
  genuinely silent (feature keeps working) until it recovers.
* Talking-interrupt track (clicking a *different* feature while Flowey is
  mid-sentence): plays "不满" (interrupted_reply) instead of that
  feature's normal/staged line; a session-wide interrupt counter reaching
  interrupt_rant_threshold fires "喷人" (rant_interrupt) once, then resets
  and enters interrupt_rant_cooldown_seconds during which further
  threshold hits downgrade back to "不满" instead of ranting again.
* 锁定 toggle-count track (see _wrap_lock_provider): its own dedicated
  3-tier ladder over a raw switch count (not the generic per-feature
  stage machinery, "lock" has no "features.lock" pool) -- 1..t1 normal,
  t1+1..t2 "无语" (lock_toggle_tired), t2+1..t3 "不耐烦"
  (lock_toggle_annoyed), t3+1 "爆发" (lock_toggle_rant, one fixed line)
  which resets the count and enters interrupt_rant_cooldown_seconds
  (reused, not a separate cooldown), during which hitting t3+1 again
  downgrades to "annoyed" instead of re-ranting. Optionally also opens a
  bounded lock_toggle_tantrum_seconds window where the toggle itself is
  inert (see PetWindow._toggle_lock / is_lock_tantrum_active).

Every decision is logged to dialogue.log (see dialogue_log.py) so "why
didn't it say anything" / "why did it say that" is always
reconstructable.
"""
import json
import random
from time import monotonic

from PyQt6.QtCore import QObject, QTimer

import dialogue_log
from error_log import log_exception, log_message
from paths import DIALOGUE_SPAM_PATH

_STAGE_NAMES = {1: "不耐烦", 2: "烦躁", 3: "愤怒/喷人", 4: "沉默"}

_EMPTY_POOLS = {
    "features": {},
    "exit_interrupt": {},
    "interrupted_reply": [],
    "rant_interrupt": [],
    "annoyed_spam_click": [],
    "lock_toggle_tired": [],
    "lock_toggle_annoyed": [],
    "lock_toggle_rant": [],
}

_DEFAULT_LOCK_TOGGLE_THRESHOLDS = [3, 6, 9]


def _validate_entry(entry):
    """Same lines+faces contract as dialogues_chase.json/dialogues_info.json."""
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


class DialogueArbiter(QObject):
    def __init__(self, config, dm, pet):
        super().__init__()
        self.config = config
        self.dm = dm
        self.pet = pet
        self._pools = self._load_pools()

        spam_cfg = config.get("dialogue_spam", {})
        self.feature_debounce_seconds = spam_cfg.get("feature_debounce_seconds", 0.5)
        steps = spam_cfg.get("feature_escalation_delay_steps", [[1, 2], [3, 4], [5, 6], [7, 8]])
        self.feature_escalation_delay_steps = steps if steps else [[1, 2], [3, 4], [5, 6], [7, 8]]
        self.feature_reset_seconds = spam_cfg.get("feature_reset_seconds", 25)
        self.feature_silence_seconds = spam_cfg.get("feature_silence_seconds", 150)
        self.body_click_window_seconds = spam_cfg.get("body_click_window_seconds", 5)
        self.body_click_threshold = spam_cfg.get("body_click_threshold", 4)
        self.interrupt_rant_threshold = spam_cfg.get("interrupt_rant_threshold", 3)
        self.interrupt_rant_cooldown_seconds = spam_cfg.get("interrupt_rant_cooldown_seconds", 300)

        thresholds = spam_cfg.get("lock_toggle_thresholds", _DEFAULT_LOCK_TOGGLE_THRESHOLDS)
        if not (isinstance(thresholds, list) and len(thresholds) == 3 and all(isinstance(t, int) for t in thresholds)):
            thresholds = _DEFAULT_LOCK_TOGGLE_THRESHOLDS
        self.lock_toggle_thresholds = thresholds
        self.lock_tantrum_seconds = spam_cfg.get("lock_toggle_tantrum_seconds", 10)

        self._features = {}  # key -> {stage, last_use, cooldown_until, silence_until, pending, escalation_repeats}
        self._timers = {}  # key -> QTimer
        self._pending_provider = {}  # key -> callable | None

        self._active_key = None  # which feature's own reaction is currently on screen, if any

        self._interrupt_count = 0
        self._interrupt_rant_cooldown_until = 0.0

        self._body_clicks = []  # monotonic timestamps

        # "锁定" toggle-count escalation -- deliberately separate from
        # self._features's generic per-feature stage ladder (see
        # _wrap_lock_provider's docstring for why sharing it would break).
        self._lock = {"count": 0, "last_toggle": 0.0, "rant_cooldown_until": 0.0, "tantrum_until": 0.0}

    # ------------------------------------------------------------- pool load
    def _load_pools(self):
        try:
            with open(DIALOGUE_SPAM_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            log_message(f"dialogues_spam: {DIALOGUE_SPAM_PATH} 不存在，防刷屏台词将没有内容可播（功能仍会正常生效）")
            return dict(_EMPTY_POOLS)
        except (OSError, json.JSONDecodeError):
            log_exception(f"dialogues_spam: 无法加载 {DIALOGUE_SPAM_PATH}")
            return dict(_EMPTY_POOLS)
        if not isinstance(raw, dict):
            log_message("dialogues_spam: 顶层必须是JSON对象")
            return dict(_EMPTY_POOLS)

        features = {}
        for key, stages in (raw.get("features") or {}).items():
            if not isinstance(stages, dict):
                continue
            features[key] = {
                stage_key: self._clean_entries(entries, f"features.{key}.{stage_key}")
                for stage_key, entries in stages.items()
                if isinstance(entries, list)
            }

        exit_interrupt = {
            stage_key: self._clean_entries(entries, f"exit_interrupt.{stage_key}")
            for stage_key, entries in (raw.get("exit_interrupt") or {}).items()
            if isinstance(entries, list)
        }

        flat = {}
        for pool_name in (
            "interrupted_reply",
            "rant_interrupt",
            "annoyed_spam_click",
            "lock_toggle_tired",
            "lock_toggle_annoyed",
            "lock_toggle_rant",
        ):
            entries = raw.get(pool_name)
            flat[pool_name] = self._clean_entries(entries, pool_name) if isinstance(entries, list) else []

        return {"features": features, "exit_interrupt": exit_interrupt, **flat}

    @staticmethod
    def _clean_entries(entries, where):
        cleaned = []
        for i, entry in enumerate(entries):
            ok, reason = _validate_entry(entry)
            if not ok:
                log_message(f"dialogues_spam: skipping malformed entry {where}[{i}]: {reason}")
                dialogue_log.log_event(f"台词坏条目已跳过：{where}[{i}]（{reason}）")
                continue
            cleaned.append(entry)
        return cleaned

    @staticmethod
    def _pick_pool(pool):
        if not pool:
            return None
        return _entry_to_segments(random.choice(pool))

    def _growing_delay(self, repeats):
        steps = self.feature_escalation_delay_steps
        lo, hi = steps[min(repeats, len(steps) - 1)]
        return random.uniform(lo, hi)

    # ------------------------------------------------------------- features
    def _feature(self, key):
        return self._features.setdefault(key, {})

    def _debounce_timer(self, key):
        timer = self._timers.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda k=key: self._fire(k))
            self._timers[key] = timer
        return timer

    def begin_feature(self, key):
        """Call at the very top of a toggle-style feature handler, BEFORE
        mutating any state. Returns True if this click is accepted (caller
        must go ahead and apply its change, then call finish_feature) or
        False if it landed inside the 0.5s cooldown from a previous
        accepted click on this same feature -- the caller must then do
        absolutely nothing else (state frozen, no dialogue, regla 3). This
        flat cooldown is independent of whether a reaction is currently
        being *spoken* (see finish_feature/_active_key) -- it only guards
        against re-toggling the underlying state too fast."""
        state = self._feature(key)
        now = monotonic()
        if state.get("cooldown_until", 0) > now:
            dialogue_log.log_event(f"功能[{key}]点击被0.5s防抖冷却吞掉，功能未切换")
            return False
        state["cooldown_until"] = now + self.feature_debounce_seconds
        return True

    def finish_feature(self, key, normal_provider=None):
        """Call right after a feature actually changed state -- either
        immediately following an accepted begin_feature() (toggle-style
        features), or unconditionally every time a continuous-style
        feature (volume) applies a new value. normal_provider (optional
        zero-arg callable) is invoked ~feature_debounce_seconds later, but
        only if this click resolves to the plain "normal" tier (not talked
        over, no escalation, no bored/rant override) -- it should do
        exactly what the feature used to do unconditionally, e.g.
        `lambda: self.dm.trigger_menu_flavor("pin_on")`.

        If this same feature's own reaction is *already* playing on screen
        right now, this call does NOT cut it short or restart anything --
        it just remembers a click happened; the next stage speaks only
        once the current line finishes (see on_bubble_finished), after a
        delay that grows the more times this happens back-to-back."""
        if key == "lock":
            # Lock's tier must reflect the toggle count AT THE MOMENT OF
            # THIS TOGGLE, so it's decided synchronously right here, before
            # this click can fall into the "still playing, just queue"
            # branch below -- not deferred to whenever it eventually speaks.
            normal_provider = self._wrap_lock_provider(normal_provider)
        if self._active_key == key:
            state = self._feature(key)
            state["pending"] = True
            state["pending_provider"] = normal_provider
            return
        self._note_click(key, normal_provider)
        self._debounce_timer(key).start(int(self.feature_debounce_seconds * 1000))

    def _note_click(self, key, normal_provider):
        self._pending_provider[key] = normal_provider
        state = self._feature(key)
        state["escalation_repeats"] = 0  # a genuinely fresh click restarts the delay-growth ramp
        if self.pet.is_reminder_popup_active():
            state["pending_suppressed"] = True
            state["pending_interrupt"] = False
            dialogue_log.log_event(f"功能[{key}]触发：提醒/追讨弹窗显示中，本次台词将被抑制（功能仍会生效）")
        else:
            was_interrupting = self.pet.force_end_bubble()
            state["pending_suppressed"] = False
            state["pending_interrupt"] = was_interrupting
            if was_interrupting:
                self._interrupt_count += 1
                dialogue_log.log_event(f"功能[{key}]打断了Flowey说话，会话打断计数={self._interrupt_count}")
                # Whatever was forcibly cut off is gone for good -- if it was
                # one of our own feature reactions (possibly a DIFFERENT
                # feature than the one just clicked), its own queued
                # "pending" resume (if any) is now moot.
                prev_key = self._active_key
                self._active_key = None
                if prev_key:
                    prev_state = self._feature(prev_key)
                    prev_state["pending"] = False
                    prev_state["pending_provider"] = None
        self._touch_idle_reset(key)

    def _touch_idle_reset(self, key):
        state = self._feature(key)
        now = monotonic()
        silence_until = state.get("silence_until", 0)
        if silence_until and silence_until <= now:
            dialogue_log.log_event(f"功能[{key}]沉默期已结束，阶段重置")
            state["silence_until"] = 0
            state["stage"] = 0
        elif not silence_until:
            last_use = state.get("last_use", 0)
            if last_use and (now - last_use) > self.feature_reset_seconds and state.get("stage", 0):
                dialogue_log.log_event(f"功能[{key}]闲置超过{self.feature_reset_seconds}s，阶段重置")
                state["stage"] = 0
        state["last_use"] = now

    def _feature_pool(self, key, stage):
        return self._pools.get("features", {}).get(key, {}).get(f"stage{stage}", [])

    def _fire(self, key):
        state = self._feature(key)
        suppressed = state.pop("pending_suppressed", False)
        interrupted = state.pop("pending_interrupt", False)
        provider = self._pending_provider.pop(key, None)

        if suppressed:
            dialogue_log.log_event(f"功能[{key}]台词已丢弃：提醒/追讨弹窗期间不得插话")
            return
        if self.pet.is_exiting():
            dialogue_log.log_event(f"功能[{key}]台词已丢弃：程序正在退出")
            return
        if interrupted:
            self._speak_interrupt_reaction(key)
            return
        if key == "lock":
            # Lock never touches the generic per-feature stage ladder (no
            # "features.lock" pool exists, see _wrap_lock_provider) -- the
            # provider was already fully decided (normal/tired/annoyed/
            # rant) back when finish_feature was called.
            self._active_key = key
            if provider:
                provider()
            return
        self._speak_stage(key, provider)

    def _speak_stage(self, key, provider):
        """Speaks whatever this feature's "next stage" currently is, then
        advances it by exactly one (capped at 4) so the same stage's pool
        never plays twice in a row -- regardless of whether anything was
        actually shown (a missing pool or a None provider still counts as
        this stage having "happened", so the ladder can't get stuck)."""
        state = self._feature(key)
        now = monotonic()
        if state.get("silence_until", 0) > now:
            dialogue_log.log_event(f"功能[{key}]处于沉默冷却期，不播台词")
            return
        stage = state.get("stage", 0)

        if stage == 0:
            dialogue_log.log_event(f"功能[{key}]播放正常台词（阶段0）")
            state["stage"] = 1
            if provider:
                self._active_key = key
                provider()
            return

        pool = self._feature_pool(key, stage)
        segs = self._pick_pool(pool) if pool else None
        if segs is None:
            dialogue_log.log_event(f"功能[{key}]阶段{stage}台词池缺失，回退为正常台词")
            state["stage"] = min(stage + 1, 4)
            if stage >= 4:
                state["silence_until"] = now + self.feature_silence_seconds
            if provider:
                self._active_key = key
                provider()
            return

        self._active_key = key
        ok = self.dm.preempt(f"spam_{key}_stage{stage}", segs)
        dialogue_log.log_event(f"功能[{key}]阶段{stage}（{_STAGE_NAMES.get(stage, stage)}）ok={ok}")
        state["stage"] = min(stage + 1, 4)
        if stage >= 4:
            state["silence_until"] = now + self.feature_silence_seconds
            dialogue_log.log_event(f"功能[{key}]进入沉默冷却{self.feature_silence_seconds}s")

    # ------------------------------------------------------------------ lock
    def _wrap_lock_provider(self, normal_provider):
        """"锁定"专属三级吐槽（不走通用features.<key>四级梯度，见类文档）：
        每次实际发生的切换（锁定或解锁各算一次）在这里被计入一个独立的原始
        计数器，按 lock_toggle_thresholds=[t1,t2,t3] 分档 1..t1=正常/
        t1+1..t2=无语/t2+1..t3=不耐烦/t3+1=爆发（固定一条，播完计数清零并
        进入 interrupt_rant_cooldown_seconds 喷人冷却，冷却期内再次达到
        t3+1 降级为不耐烦，不重复爆发）。计数在连续两次切换间隔超过
        feature_reset_seconds 时重置（复用现有配置，不新增字段）。

        分档在这里同步算出并烘焙进返回的闭包里，而不是等真正播放时才算，
        这样即便这次点击落进"本功能正在播放、只能排队"分支（见
        finish_feature），排到的也始终是"点击那一刻"的档位，不会被后续
        更快的连续点击悄悄改写。"""
        now = monotonic()
        lock = self._lock
        if lock["last_toggle"] and (now - lock["last_toggle"]) > self.feature_reset_seconds:
            dialogue_log.log_event(f"锁定切换计数：闲置超过{self.feature_reset_seconds}s，计数重置")
            lock["count"] = 0
        lock["last_toggle"] = now
        lock["count"] += 1
        count = lock["count"]

        t1, t2, t3 = self.lock_toggle_thresholds
        in_cooldown = now < lock["rant_cooldown_until"]
        if count <= t1:
            tier = "normal"
        elif count <= t2:
            tier = "tired"
        elif count <= t3:
            tier = "annoyed"
        elif in_cooldown:
            tier = "annoyed"
        else:
            tier = "rant"

        downgraded = in_cooldown and count > t3 and tier != "rant"
        dialogue_log.log_event(
            f"锁定切换计数={count} 判定阶段={tier}" + ("（喷人冷却内降级，不重复爆发）" if downgraded else "")
        )

        if tier == "normal":
            return normal_provider

        if tier == "rant":
            lock["count"] = 0
            lock["rant_cooldown_until"] = now + self.interrupt_rant_cooldown_seconds
            if self.lock_tantrum_seconds > 0:
                lock["tantrum_until"] = now + self.lock_tantrum_seconds
                dialogue_log.log_event(f"锁定爆发：开关进入{self.lock_tantrum_seconds}s闹脾气冷区")
            pool_name = "lock_toggle_rant"
        else:
            pool_name = "lock_toggle_tired" if tier == "tired" else "lock_toggle_annoyed"

        pool = self._pools.get(pool_name, [])
        segs = self._pick_pool(pool) if pool else None
        if segs is None:
            dialogue_log.log_event(f"锁定[{tier}]台词池缺失，降级为正常锁定/解锁台词")
            return normal_provider
        return lambda: self.dm.preempt(f"spam_lock_{tier}", segs)

    def is_lock_tantrum_active(self):
        """Public hook for PetWindow._toggle_lock: True for
        lock_tantrum_seconds right after the tier-10 outburst fires --
        during this window the lock/unlock toggle itself must be a no-op
        (caller must not mutate state or call begin_feature/finish_feature
        at all). Always a bounded future timestamp set once in
        _wrap_lock_provider, so this can never get stuck True forever."""
        return monotonic() < self._lock["tantrum_until"]

    def reset_lock_gates(self):
        """Public hook for PetWindow._force_unlock (tray/hotkey emergency
        exit): clears the two gates that can make the lock toggle refuse a
        request -- the post-outburst tantrum zone and lock's own 0.5s
        debounce cooldown -- so the very next toggle is never rejected. The
        escalation counter and the rant cooldown are deliberately left
        alone (they only shape what gets *said*), and no other feature's
        state is touched."""
        self._lock["tantrum_until"] = 0.0
        self._feature("lock").pop("cooldown_until", None)

    def on_bubble_finished(self):
        """Call from PetWindow whenever ANY dialogue box content finishes
        (regardless of what it was -- a no-op unless it was one of our own
        feature reactions). If a click came in for the SAME feature while
        it was playing, this is where the next stage actually gets
        scheduled, after a delay that grows each consecutive time this
        happens (reset once the feature idles back to a fresh click)."""
        key = self._active_key
        self._active_key = None
        if key is None:
            return
        state = self._feature(key)
        if not state.pop("pending", False):
            return
        provider = state.pop("pending_provider", None)
        self._pending_provider[key] = provider
        repeats = state.get("escalation_repeats", 0)
        delay = self._growing_delay(repeats)
        state["escalation_repeats"] = repeats + 1
        dialogue_log.log_event(f"功能[{key}]连续点击排队中，{delay:.1f}s后触发下一句")
        self._debounce_timer(key).start(int(delay * 1000))

    # -------------------------------------------------------- talk-interrupt
    def _speak_interrupt_reaction(self, key):
        now = monotonic()
        if self._interrupt_count >= self.interrupt_rant_threshold and now >= self._interrupt_rant_cooldown_until:
            segs = self._pick_pool(self._pools.get("rant_interrupt", []))
            if segs is not None:
                self._active_key = key
            ok = self.dm.preempt("spam_rant_interrupt", segs)
            dialogue_log.log_event(
                f"喷人触发（打断累计{self._interrupt_count}次，功能[{key}]）ok={ok}，"
                f"计数清零，进入{self.interrupt_rant_cooldown_seconds}s喷人冷却"
            )
            self._interrupt_count = 0
            self._interrupt_rant_cooldown_until = now + self.interrupt_rant_cooldown_seconds
            return
        segs = self._pick_pool(self._pools.get("interrupted_reply", []))
        if segs is not None:
            self._active_key = key
        ok = self.dm.preempt("spam_interrupted_reply", segs)
        dialogue_log.log_event(f"播放不满台词（打断反应，功能[{key}]）ok={ok} 当前打断计数={self._interrupt_count}")

    # ------------------------------------------------------------ body click
    def note_body_click(self):
        """Call from PetWindow._handle_click, only when Flowey is currently
        silent (the caller already gates on dm.busy same as before), before
        deciding whether to play the ordinary click reaction. Returns True
        if this click was spam (a 'bored' line was already shown by this
        call, caller must NOT also trigger the normal click dialogue) or
        False for an ordinary click the caller should handle as usual."""
        now = monotonic()
        window = self.body_click_window_seconds
        self._body_clicks = [t for t in self._body_clicks if now - t < window]
        self._body_clicks.append(now)
        if len(self._body_clicks) < self.body_click_threshold:
            return False
        segs = self._pick_pool(self._pools.get("annoyed_spam_click", []))
        ok = self.dm.preempt("spam_annoyed_click", segs)
        dialogue_log.log_event(f"连点本体判定为厌倦（{len(self._body_clicks)}次/{window}s窗口）ok={ok}")
        return True

    # -------------------------------------------------------------- exit
    def pick_exit_interrupt_segments(self):
        """The whole exit sequence only ever gets ONE "被打断" reaction,
        picked at random -- not an escalating ladder like the per-feature
        one, since there's nothing to escalate against (only one trigger
        ever happens per exit, see PetWindow._handle_exit_interrupt).
        Uses exit_interrupt's "stage1" pool (the mildest, most
        first-interruption-appropriate tier) from dialogues_spam.json;
        stage2-5 are simply unused by this simplified design."""
        pool = self._pools.get("exit_interrupt", {}).get("stage1", [])
        if not pool:
            dialogue_log.log_event("退出打断：台词池缺失（exit_interrupt.stage1），静默继续退出")
            return None
        segs = self._pick_pool(pool)
        dialogue_log.log_event("退出打断：播放 exit_interrupt.stage1")
        return segs
