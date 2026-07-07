# -*- coding: utf-8 -*-
"""Owns dialogue.json, all trigger conditions, and the priority queue that
arbitrates between reminders, pomodoro status lines, "neglect" jabs, and
idle/click chatter.

Priority order (highest wins when something is queued while Flowey is
already mid-sentence): reminder > pomodoro status > neglect > chat.
A global minimum interval throttles only the bottom two tiers (chat,
neglect) so Flowey doesn't talk nonstop; reminders and pomodoro status
always get through immediately once the current line finishes.
"""
import ctypes
import json
import random
import time
from datetime import datetime

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from error_log import log_exception, log_message
from paths import DIALOGUE_INFO_PATH, DIALOGUE_PATH

PRI_REMINDER = 40
PRI_POMODORO = 30
PRI_NEGLECT = 20
PRI_CHAT = 10
PRI_CLICK = 0  # user-initiated, always shown immediately, never queued/throttled

OTHER_PET_KEYWORDS = ("桌宠", "桌面宠物", "shimeji", "desktop pet", "desktop mascot", "desktop buddy")

INFO_WEIGHTED_POOLS = ("normal_intro", "fourth_wall", "nonsense", "rare")
INFO_ANTI_SPAM_POOL = "anti_spam"
DEFAULT_INFO_WEIGHTS = {"normal_intro": 40, "fourth_wall": 30, "nonsense": 20, "rare": 10}


def _validate_info_entry(entry):
    """Returns (ok, reason). Keeps dialogues_info.json's format honest without
    ever raising -- a malformed hand-edited entry should just be skipped."""
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


def _normalize(entry):
    if "segments" in entry:
        return list(entry["segments"])
    return [{"text": entry["text"], "face": entry["face"]}]


def _other_pet_detected():
    """Best-effort heuristic: scan visible top-level window titles for
    keywords suggesting another desktop-pet program is running. Windows-only,
    ctypes-based (no extra dependency); silently returns False on any error
    since this is flavor, not something worth crashing over."""
    try:
        user32 = ctypes.windll.user32
        found = False

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(hwnd, _lparam):
            nonlocal found
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = (buf.value or "").strip()
            if title and title.lower() != "flowey":
                lower = title.lower()
                if any(kw.lower() in lower for kw in OTHER_PET_KEYWORDS):
                    found = True
                    return False
            return True

        user32.EnumWindows(callback, 0)
        return found
    except Exception:
        return False


class DialogueManager(QObject):
    show_requested = pyqtSignal(list, str)  # segments, category

    def __init__(self, config, save_config, stats=None):
        super().__init__()
        self.config = config
        self.save_config = save_config
        self.stats = stats
        with open(DIALOGUE_PATH, "r", encoding="utf-8") as f:
            self.data = json.load(f)
        self.info_data = self._load_info_dialogues()
        self._info_trigger_times = []  # monotonic timestamps, for anti-spam
        self._last_info_pick = None  # (pool_name, index) of the last entry shown

        self.busy = False
        self._queue = []  # list of (priority, category, segments)
        self.last_auto_speak_end = 0.0
        self.last_interaction_time = time.monotonic()

        self.combo_count = 0
        self._combo_timer = QTimer(self)
        self._combo_timer.setSingleShot(True)
        self._combo_timer.timeout.connect(self._reset_combo)

        self._last_mouse_x = None
        self._mouse_dir = None

        self._days_since_last_active = None
        last_active = self.config.get("last_active_date")
        if last_active:
            try:
                prev = datetime.strptime(last_active, "%Y-%m-%d").date()
                self._days_since_last_active = (datetime.now().date() - prev).days
            except ValueError:
                pass

        self._ensure_today_tracked()

        self.config["total_opens"] = self.config.get("total_opens", 0) + 1
        self.save_config(self.config)

    # ---------------------------------------------------------------- pools
    def _pool(self, category):
        return self.data.get(category, [])

    # ---------------------------------------------------------- info easter egg
    def _load_info_dialogues(self):
        try:
            with open(DIALOGUE_INFO_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError):
            log_exception(f"dialogues_info: could not load {DIALOGUE_INFO_PATH}, 查看信息 will have nothing to say")
            return {}

        pools = {}
        for pool_name, entries in raw.items():
            if pool_name.startswith("_"):
                continue  # e.g. "_readme"
            if not isinstance(entries, list):
                log_message(f"dialogues_info: pool '{pool_name}' must be an array, skipping it entirely")
                continue
            cleaned = []
            for i, entry in enumerate(entries):
                ok, reason = _validate_info_entry(entry)
                if not ok:
                    log_message(f"dialogues_info: skipping malformed entry {pool_name}[{i}]: {reason}")
                    continue
                cleaned.append(entry)
            pools[pool_name] = cleaned
        return pools

    def _roll_info_pool(self):
        weights = self.config.get("info_easter_egg", {}).get("weights", DEFAULT_INFO_WEIGHTS)
        names = [n for n in INFO_WEIGHTED_POOLS if weights.get(n, 0) > 0 and self.info_data.get(n)]
        if not names:
            return None
        total = sum(weights.get(n, 0) for n in names)
        roll = random.random() * total
        cumulative = 0.0
        for name in names:
            cumulative += weights.get(name, 0)
            if roll < cumulative:
                return name
        return names[-1]

    def _pick_info_entry(self, pool_name):
        pool = self.info_data.get(pool_name) or []
        if not pool:
            return None
        indices = list(range(len(pool)))
        if self._last_info_pick and self._last_info_pick[0] == pool_name and len(indices) > 1:
            indices = [i for i in indices if i != self._last_info_pick[1]]
        idx = random.choice(indices)
        self._last_info_pick = (pool_name, idx)
        return pool[idx]

    def _build_info_segments(self, entry):
        segments = []
        for line, faces in zip(entry["lines"], entry["faces"]):
            face = faces[0] if len(faces) == 1 else list(faces)
            segments.append({"text": self._substitute(line), "face": face})
        return segments

    def trigger_view_info(self):
        """Right-click menu -> 查看信息. Always eventually shown (queued
        behind whatever Flowey is already saying), never silently dropped --
        this is a deliberate one-off action, not ambient chatter."""
        cfg = self.config.get("info_easter_egg", {})
        window_s = cfg.get("anti_spam_window_seconds", 60)
        trigger_count = cfg.get("anti_spam_trigger_count", 4)

        now = time.monotonic()
        self._info_trigger_times = [t for t in self._info_trigger_times if now - t < window_s]
        self._info_trigger_times.append(now)

        if len(self._info_trigger_times) >= trigger_count and self.info_data.get(INFO_ANTI_SPAM_POOL):
            pool_name = INFO_ANTI_SPAM_POOL
        else:
            pool_name = self._roll_info_pool()
        if pool_name is None:
            return

        entry = self._pick_info_entry(pool_name)
        if entry is None:
            return
        segments = self._build_info_segments(entry)
        self._submit(f"info_{pool_name}", segments, PRI_CHAT, bypass_interval=True)

    def _seen_today_bucket(self, key):
        """A no-repeat set scoped to today's date, persisted in config so it
        survives app restarts; automatically clears at midnight and again
        once every entry in a pool has been shown (so playback just loops
        into a fresh cycle instead of going silent)."""
        today = datetime.now().strftime("%Y-%m-%d")
        store = self.config.setdefault("_dialogue_seen", {"date": today, "seen": {}})
        if store.get("date") != today:
            store["date"] = today
            store["seen"] = {}
        return store["seen"].setdefault(key, [])

    def _pick(self, category):
        pool = self._pool(category)
        if not pool:
            return None
        seen = self._seen_today_bucket(category)
        available = [i for i in range(len(pool)) if i not in seen]
        if not available:
            seen.clear()
            available = list(range(len(pool)))
        idx = random.choice(available)
        seen.append(idx)
        self.save_config(self.config)
        return _normalize(pool[idx])

    def _pick_progressive(self, category, stage_idx):
        """Unlike _pick(), this never randomizes: the pursuit nag lines are
        meant to escalate in a fixed order (mild nudge -> angriest), so
        stage_idx deterministically selects how far into the pool to go,
        clamped to the last line once stage_idx runs past the pool length."""
        pool = self._pool(category)
        if not pool:
            return None
        idx = min(stage_idx, len(pool) - 1)
        return _normalize(pool[idx])

    def _pick_expression_line(self):
        pools = self.data.get("expression_lines", {})
        if not pools:
            return None
        expr = random.choice(list(pools.keys()))
        lines = pools[expr]
        if not lines:
            return None
        seen = self._seen_today_bucket(f"expression_lines:{expr}")
        available = [i for i in range(len(lines)) if i not in seen]
        if not available:
            seen.clear()
            available = list(range(len(lines)))
        idx = random.choice(available)
        seen.append(idx)
        self.save_config(self.config)
        return _normalize(lines[idx])

    # ------------------------------------------------------------- egg rolls
    def _roll_weighted(self, egg_category):
        """Rarest-first cumulative probability roll over a weighted 'eggs'
        list (see dialogue.json's *_eggs arrays). Returns normalized segments
        or None if nothing hit. Eggs that already fired today are excluded
        (and the day's list clears once every egg in the category has had
        its turn) so a handful of higher-probability eggs can't dominate a
        session the way plain probability-with-replacement would allow."""
        eggs = self.data.get(egg_category, [])
        if not eggs:
            return None
        seen = self._seen_today_bucket(f"{egg_category}:fired")
        eligible = [e for e in eggs if e.get("id") not in seen]
        if not eligible:
            seen.clear()
            eligible = eggs
        roll = random.random()
        cumulative = 0.0
        for egg in sorted(eligible, key=lambda e: e["probability"]):
            prob = egg["probability"]
            if egg.get("double_if_other_pet") and _other_pet_detected():
                prob *= 2
            cumulative += prob
            if roll < cumulative:
                seen.append(egg.get("id"))
                self.save_config(self.config)
                if self.stats and egg.get("id"):
                    self.stats.mark_rare_seen(egg["id"])
                segments = [dict(s) for s in egg["segments"]]
                if egg.get("effect") == "typing_slow":
                    for s in segments:
                        s["slow"] = True
                if egg.get("mouse_direction") and segments:
                    if self._mouse_dir == "right":
                        segments[0]["face"] = "n_right_glance"
                    elif self._mouse_dir == "left":
                        segments[0]["face"] = "n_left_glance"
                return segments
        return None

    def _roll_time_egg(self):
        eggs = self.data.get("time_eggs", [])
        if not eggs:
            return None
        hour = datetime.now().hour + datetime.now().minute / 60
        for egg in eggs:
            lo, hi = egg["hour_range"]
            in_window = lo <= hour < hi if lo <= hi else (hour >= lo or hour < hi - 24)
            if in_window and random.random() < egg["probability"]:
                return [dict(s) for s in egg["segments"]]
        return None

    # ------------------------------------------------------------ mouse hint
    def note_mouse_sample(self, x):
        if self._last_mouse_x is not None:
            dx = x - self._last_mouse_x
            if abs(dx) > 3:
                self._mouse_dir = "left" if dx < 0 else "right"
                if self.stats:
                    self.stats.record_activity()
        self._last_mouse_x = x

    # --------------------------------------------------------- time-of-day
    def check_time_of_day_triggers(self):
        """Proactive, session-aware time-of-day lines -- driven purely by
        wall-clock time and the stats-tracked activity chain, independent
        of any click, since the whole point is Flowey noticing on its own.
        Called on a timer from PetWindow."""
        if self.busy or not self.stats:
            return
        now = datetime.now()
        hour = now.hour + now.minute / 60
        boundary = self.config.get("day_boundary_hour", 6)
        logical_date = self.stats.current_logical_date()

        if hour < boundary:
            if hour < 1.0:
                return
            if self.stats.data.get("night_owl_notified_date") == logical_date:
                return
            if not self.stats.is_ongoing_night_session(now):
                return
            self.stats.data["night_owl_notified_date"] = logical_date
            self.stats.save()
            self._submit("night_owl_nag", self._pick("night_owl_nag"), PRI_CHAT, bypass_interval=True)
        elif boundary <= hour < 9.0:
            if self.stats.data.get("morning_greeted_date") == logical_date:
                return
            if self.stats.is_ongoing_night_session(now):
                return  # an all-nighter reaching past the boundary, not a fresh morning
            self.stats.data["morning_greeted_date"] = logical_date
            self.stats.save()
            if random.random() < self.config.get("morning_greet_probability", 0.2):
                self._submit("morning_greet", self._pick("morning_greet"), PRI_CHAT)

    # -------------------------------------------------------- text templates
    def _substitute(self, text):
        if "{opens}" in text:
            text = text.replace("{opens}", str(self.config.get("total_opens", 0)))
        if "{time}" in text:
            text = text.replace("{time}", datetime.now().strftime("%H:%M"))
        if "{duration}" in text:
            text = text.replace("{duration}", self._session_duration_str())
        if "{ver}" in text:
            text = text.replace("{ver}", str(self.config.get("version", "?")))
        if "{days}" in text:
            text = text.replace("{days}", str(self._days_since_install()))
        if "{clicks}" in text:
            text = text.replace("{clicks}", str(self.config.get("total_clicks", 0)))
        return text

    def _days_since_install(self):
        install = self.config.get("install_date")
        if not install:
            return 0
        try:
            d = datetime.strptime(install, "%Y-%m-%d").date()
        except ValueError:
            return 0
        return max(0, (datetime.now().date() - d).days)

    def days_since_install(self):
        return self._days_since_install()

    def total_egg_count(self):
        """Distinct egg ids across every *_eggs pool in dialogue.json --
        the denominator for the stats panel's egg-collection progress."""
        ids = set()
        for key, entries in self.data.items():
            if key.endswith("_eggs") and isinstance(entries, list):
                for e in entries:
                    if e.get("id"):
                        ids.add(e["id"])
        return len(ids)

    def _session_duration_str(self):
        start = self.config.get("_session_start")
        if not start:
            return "一段时间"
        secs = int(time.time() - start)
        h, rem = divmod(secs, 3600)
        m, _ = divmod(rem, 60)
        return f"{h}小时{m}分钟" if h > 0 else f"{m}分钟"

    # ------------------------------------------------------------- queueing
    def _within_min_interval(self):
        min_gap = self.config.get("global_min_interval_seconds", 180)
        return (time.monotonic() - self.last_auto_speak_end) < min_gap

    def _submit(self, category, segments, priority, bypass_interval=False):
        if segments is None:
            return
        if not bypass_interval and priority <= PRI_NEGLECT and self._within_min_interval():
            return  # too soon after the last automatic line; drop it
        if self.busy:
            if priority == PRI_CLICK:
                return  # clicks never interrupt or queue
            self._queue.append((priority, category, segments))
            self._queue.sort(key=lambda t: -t[0])
            return
        self._show(category, segments)

    def _show(self, category, segments):
        self.busy = True
        segments = [dict(s, text=self._substitute(s.get("text", ""))) for s in segments]
        self.show_requested.emit(segments, category)
        if self.stats:
            self.stats.record_dialogue_triggered()

    def speak(self, category, segments, priority=PRI_CLICK, bypass_interval=True):
        """Generic one-off "say this" entry point for callers outside the
        dialogue.json/dialogues_info.json domain (e.g. the stats panel's
        roast lines from stats_roast.json) that still want to go through
        the normal busy/queue/priority machinery instead of poking the
        dialogue box directly."""
        self._submit(category, segments, priority, bypass_interval=bypass_interval)

    def on_finished(self):
        """Call when the dialogue box has fully closed."""
        self.busy = False
        self.last_auto_speak_end = time.monotonic()
        if self._queue:
            priority, category, segments = self._queue.pop(0)
            self._show(category, segments)

    # ---------------------------------------------------------- interaction
    def record_interaction(self):
        self.last_interaction_time = time.monotonic()
        self.config["total_clicks"] = self.config.get("total_clicks", 0) + 1
        self._ensure_today_tracked()
        self.save_config(self.config)

    def _ensure_today_tracked(self):
        today = datetime.now().strftime("%Y-%m-%d")
        last = self.config.get("last_active_date")
        if last == today:
            return
        if last is not None:
            try:
                prev = datetime.strptime(last, "%Y-%m-%d").date()
                delta = (datetime.now().date() - prev).days
            except ValueError:
                delta = None
            if delta == 1:
                self.config["streak_days"] = self.config.get("streak_days", 0) + 1
            elif delta != 0:
                self.config["streak_days"] = 1
        else:
            self.config["streak_days"] = 1
            self.config["install_date"] = today
        self.config["last_active_date"] = today
        self.save_config(self.config)

    def seconds_since_interaction(self):
        return time.monotonic() - self.last_interaction_time

    # --------------------------------------------------------------- intro
    def trigger_intro(self):
        now = datetime.now()
        cfg = self.config
        if not cfg.get("first_run_done"):
            cfg["first_run_done"] = True
            self.save_config(cfg)
            self._submit("intro_first_run", self._pick("intro_first_run"), PRI_CLICK, bypass_interval=True)
            return
        if cfg.get("close_count_recent", 0) >= 3:
            self._submit("intro_rapid_restart", self._pick("intro_rapid_restart"), PRI_CLICK, bypass_interval=True)
            return
        if 0 <= now.hour < 4:
            self._submit("intro_late_night", self._pick("intro_late_night"), PRI_CLICK, bypass_interval=True)
            return
        if (now.day == 13):
            self._submit("intro_unlucky_day", self._pick("intro_unlucky_day"), PRI_CLICK, bypass_interval=True)
            return
        if self._days_since_last_active is not None and self._days_since_last_active >= 30:
            self._submit("intro_long_absence", self._pick("intro_long_absence"), PRI_CLICK, bypass_interval=True)
            return
        egg = self._roll_weighted("intro_eggs")
        if egg:
            self._submit("intro_egg", egg, PRI_CLICK, bypass_interval=True)
            return
        self._submit("intro", self._pick("intro"), PRI_CLICK, bypass_interval=True)

    # ---------------------------------------------------------------- click
    def _reset_combo(self):
        self.combo_count = 0

    def trigger_click(self):
        self.combo_count += 1
        self._combo_timer.start(8000)

        if self.combo_count == 5:
            self._submit("click_combo_5", self._pick("click_combo_5"), PRI_CLICK, bypass_interval=True)
            return
        if self.combo_count == 10:
            self._submit("click_combo_10", self._pick("click_combo_10"), PRI_CLICK, bypass_interval=True)
            return
        if self.combo_count == 20:
            self._submit("click_combo_20", self._pick("click_combo_20"), PRI_CLICK, bypass_interval=True)
            return

        now = datetime.now()
        cfg = self.config

        # long-absence reunion takes precedence over ordinary chatter
        if self.seconds_since_interaction() > cfg.get("reunion_after_minutes", 180) * 60:
            self._submit("reunion", self._pick("reunion"), PRI_CLICK, bypass_interval=True)
            return

        if now.hour == 0 and now.minute == 0:
            self._submit("click_midnight", self._pick("click_midnight"), PRI_CLICK, bypass_interval=True)
            return
        if now.hour == 3 and now.minute == 33:
            self._submit("click_3_33am", self._pick("click_3_33am"), PRI_CLICK, bypass_interval=True)
            return
        if now.month == 1 and now.day == 1:
            self._submit("click_new_year", self._pick("click_new_year"), PRI_CLICK, bypass_interval=True)
            return
        if (now.month, now.day) == (12, 25):
            self._submit("click_holiday", self._pick("click_holiday"), PRI_CLICK, bypass_interval=True)
            return
        bday = cfg.get("birthday")
        today_md = now.strftime("%m-%d")
        if bday == today_md and cfg.get("greeted_birthday_date") != now.strftime("%Y-%m-%d"):
            cfg["greeted_birthday_date"] = now.strftime("%Y-%m-%d")
            self.save_config(cfg)
            self._submit("click_birthday", self._pick("click_birthday"), PRI_CLICK, bypass_interval=True)
            return

        streak = cfg.get("streak_days", 0)
        greeted = set(cfg.get("greeted_streaks", []))
        for milestone in (365, 100, 30, 7):
            if streak >= milestone and milestone not in greeted:
                cfg.setdefault("greeted_streaks", []).append(milestone)
                self.save_config(cfg)
                self._submit(f"click_streak_{milestone}", self._pick(f"click_streak_{milestone}"), PRI_CLICK, bypass_interval=True)
                return

        time_egg = self._roll_time_egg()
        if time_egg:
            self._submit("click_egg", time_egg, PRI_CLICK, bypass_interval=True)
            return
        egg = self._roll_weighted("click_eggs")
        if egg:
            self._submit("click_egg", egg, PRI_CLICK, bypass_interval=True)
            return

        # weighted normal pool
        roll = random.random()
        deep_unlocked = cfg.get("total_clicks", 0) >= cfg.get("click_deep_unlock_clicks", 15)
        if roll < 0.15 and deep_unlocked:
            segs = self._pick("click_deep")
        elif roll < 0.35:
            segs = self._pick("click_fourthwall")
        elif roll < 0.55:
            segs = self._pick_expression_line()
        else:
            segs = self._pick("click_normal")
        self._submit("click_normal", segs, PRI_CLICK, bypass_interval=True)

    def check_position_easter_egg(self, win_x, win_y, win_w, win_h, screen_w, screen_h, margin=6):
        near_corner = (
            (win_x <= margin or win_x + win_w >= screen_w - margin)
            and (win_y <= margin or win_y + win_h >= screen_h - margin)
        )
        if near_corner:
            self._submit("click_corner", self._pick("click_corner"), PRI_CHAT)
            return
        cx, cy = win_x + win_w / 2, win_y + win_h / 2
        if abs(cx - screen_w / 2) < screen_w * 0.06 and abs(cy - screen_h / 2) < screen_h * 0.06:
            self._submit("click_center", self._pick("click_center"), PRI_CHAT)

    # ----------------------------------------------------------------- idle
    def trigger_idle_random(self):
        now = datetime.now()
        if now.minute == 0 and random.random() < 0.02:
            self._submit("idle_hourly", self._pick("idle_hourly"), PRI_CHAT)
            return
        time_egg = self._roll_time_egg()
        if time_egg:
            self._submit("idle_egg", time_egg, PRI_CHAT)
            return
        egg = self._roll_weighted("idle_eggs")
        if egg:
            self._submit("idle_egg", egg, PRI_CHAT)
            return
        segs = self._pick("idle") if random.random() < 0.7 else self._pick_expression_line()
        self._submit("idle", segs, PRI_CHAT)

    def trigger_afk(self, level):
        category = f"idle_afk_{level}min"
        self._submit(category, self._pick(category), PRI_CHAT)

    # ------------------------------------------------------------- pomodoro
    def trigger_pomodoro_status(self, kind):
        category = f"pomodoro_{kind}"  # 'start' or 'end'
        self._submit(category, self._pick(category), PRI_POMODORO, bypass_interval=True)

    def trigger_pomodoro_chat(self):
        self._submit("pomodoro_chat", self._pick("pomodoro_chat"), PRI_CHAT)

    # ------------------------------------------------------------ reminders
    def trigger_reminder(self, kind):
        category = f"reminder_{kind}"
        self._submit(category, self._pick(category), PRI_REMINDER, bypass_interval=True)

    def trigger_reminder_nag(self, kind, stage_idx):
        category = f"reminder_unconfirmed_{kind}"
        self._submit(category, self._pick_progressive(category, stage_idx), PRI_REMINDER, bypass_interval=True)

    def trigger_reminder_ack(self, kind):
        category = f"reminder_ack_{kind}"
        self._submit(category, self._pick(category), PRI_REMINDER, bypass_interval=True)

    # --------------------------------------------------------- menu flavor
    def trigger_menu_flavor(self, category):
        """One-off flavor line for a direct right-click-menu action (pin,
        hide, mute, volume, autostart, ...). Always eventually shown (never
        silently dropped like a click would be) but queues behind whatever
        is already talking."""
        self._submit(category, self._pick(category), PRI_CHAT, bypass_interval=True)

    def trigger_neglect(self, kind):
        category = f"neglect_{kind}"
        self._submit(category, self._pick(category), PRI_NEGLECT)

    # ------------------------------------------------------------------ exit
    def pick_exit_segments(self):
        segments = self._pick_exit_segments_raw()
        if segments is None:
            return None
        return [dict(s, text=self._substitute(s.get("text", ""))) for s in segments]

    def _pick_exit_segments_raw(self):
        cfg = self.config
        now = time.time()
        last_close = cfg.get("last_close_time")
        cfg["close_count_recent"] = cfg.get("close_count_recent", 0) + 1 if (
            last_close and (now - last_close) < 600
        ) else 0
        cfg["last_close_time"] = now
        self.save_config(cfg)

        if cfg["close_count_recent"] >= 3:
            return self._pick("exit_rapid_toggle")
        if not cfg.get("_ever_closed", False):
            cfg["_ever_closed"] = True
            self.save_config(cfg)
            return self._pick("exit_first_time")
        session_start = cfg.get("_session_start")
        if session_start and (now - session_start) > 3 * 3600:
            return self._pick("exit_long_session")

        hour = datetime.now().hour
        if (hour >= 22 or hour < 5) and random.random() < 0.02:
            return self._pick("exit_late_night")
        egg = self._roll_weighted("exit_eggs")
        if egg:
            return egg
        return self._pick("exit")
