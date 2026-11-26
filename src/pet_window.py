# -*- coding: utf-8 -*-
"""The pet itself: a frameless, always-on-top, transparent window that can
be dragged around, plus its tray icon and right-click menu. Owns the
appear/idle/talk animation state machine and is the single writer of the
on-screen pixmap, so nothing can race the dialogue system for control of
Flowey's face.
"""
import functools
import os
import random
import sys
import time
from datetime import datetime

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QCursor, QDesktopServices, QIcon, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QSystemTrayIcon,
    QWidget,
    QWidgetAction,
)

import app_log
import anticheat
import api_key_store
import dialogue_log
import flirt as flirt_dialogue
import api_chat
from api_chat import DEFAULT_BASE_URL, DEFAULT_MODEL
from api_settings_panel import ApiSettingsPanel
from chat_box import ChatBox
from dialogue_arbiter import DialogueArbiter
from config import DEFAULT_PET_SIZE_TIERS
from dialogue_box import DialogueBox
from dialogue_manager import PRI_CHAT
from error_log import log_exception
from flirt import FlirtController
from game_detector import GameDetector
from paths import APP_DIR, ICON_PATH, IS_DEV
from pomodoro_display import PomodoroDisplay
from topmost_keeper import TopmostKeeper
import update_flow
import updater_apply
import version
from update_checker import should_announce
from undertale_ui import VOLUME_TIERS, ClickableLabel
from win_zorder import apply_topmost, set_click_through

CLICK_MOVE_THRESHOLD = 4
IDLE_FRAME_MS = 220
AFK_THRESHOLDS_MIN = (5, 10, 30, 60)
# Reminder/chase popup categories (submitted by ReminderChaseController)
# mapped to their Undertale choice-bar buttons. First popups offer
# [确定][等等], chase-stage popups offer [确定][知道了] (敷衍), the
# single-shot sleep popup offers [确定][等等] but never chases -- see
# reminder_chase.py for the full state machine. The dialogue box reports
# whichever key was clicked (or "timeout") back through
# _on_dialogue_finished into the controller.
_FIRST_BUTTONS = (("confirm", "确定"), ("wait", "等等"))
_STAGE_BUTTONS = (("confirm", "确定"), ("dismiss", "知道了"))
CHASE_POPUP_BUTTONS = {
    "chase_popup_first_water": _FIRST_BUTTONS,
    "chase_popup_first_meal": _FIRST_BUTTONS,
    "chase_popup_stage_water": _STAGE_BUTTONS,
    "chase_popup_stage_meal": _STAGE_BUTTONS,
    "chase_popup_sleep": _FIRST_BUTTONS,
}
EXIT_FRAME_MS = 125
EXIT_FAILSAFE_MS = 15000
UPDATE_QUIT_DEADLINE_MS = 15000  # the updater only waits 30 s for this process
CHAT_WAIT_LINGER_MS = 40_000  # the chat "……" bubble stays up at most this long (the request itself times out at 30 s)
CHAT_RATE_WINDOW_S = 600
# API error category -> dialogues_chat.json pool
CHAT_ERROR_POOLS = {
    "auth": "chat_api_key_error",
    "bad_request": "chat_check_settings",
    "no_balance": "chat_no_balance",
    "no_config": "chat_no_api",
}
CHAT_ERROR_DEFAULT_POOL = "chat_api_error"  # network, server, rate_limit, bad_response, no_persona, ...
HIDE_FAILSAFE_GRACE_MS = 6000  # generous margin over any realistic escalation-line playback time


def clamp_origin_into_area(x, y, w, h, area):
    """Top-left (x, y) that keeps a w x h window inside `area` (a QRect-like
    with x()/y()/width()/height(), typically a screen's availableGeometry).
    A window larger than the area is pinned to the area's top-left."""
    x = max(area.x(), min(x, area.x() + area.width() - w))
    y = max(area.y(), min(y, area.y() + area.height() - h))
    return x, y


def _guarded_menu_action(context):
    """Wraps a right-click-menu entry point (opening a panel, playing a
    dialogue line) so an exception inside it is logged and reported to the
    user via a Flowey bubble instead of the process aborting -- PyQt6
    aborts the whole app once a Python exception escapes a Qt-invoked slot,
    so this is the only thing standing between a bug in one panel/one bad
    data file and the pet vanishing outright."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            try:
                return fn(self, *args, **kwargs)
            except Exception:
                log_exception(f"menu action failed: {context}")
                self._show_error_flavor()

        return wrapper

    return decorator


def _chat_visible(pet):
    """True while the chat box (行动 -> 聊天) is open. A module-level helper,
    not a method, so lightweight stand-ins for PetWindow (tests) that lack
    the attribute simply count as "closed"."""
    box = getattr(pet, "_chat_box", None)
    return box is not None and box.isVisible()


def _close_chat(pet):
    if _chat_visible(pet):
        pet._chat_box.close()


def _close_api_panel(pet):
    """Closes the 「API 设置」 panel if it is open (lock / hide / quit)."""
    panel = getattr(pet, "_api_panel", None)
    if panel is not None and panel.isVisible():
        panel.close()


class State:
    APPEARING = "appearing"
    IDLE = "idle"
    TALKING = "talking"
    EXITING = "exiting"


class PetWindow(QWidget):
    _update_result = pyqtSignal(object)  # one-click update worker thread -> main thread

    def __init__(self, config, save_config, dialogue_manager, audio, sprites, pomodoro, reminders, stats):
        self._always_on_top = config.get("always_on_top", True)
        super().__init__(None, self._window_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.config = config
        self.save_config = save_config
        self.dm = dialogue_manager
        self.audio = audio
        self.sprites = sprites
        self.pomodoro = pomodoro
        self.reminders = reminders
        self.stats = stats
        self.pet_scale = config.get("pet_scale", 1.0)

        pm0 = sprites.appear_frames[0]
        self.setFixedSize(pm0.size())
        self.pet_display_width = pm0.width()

        self.move(*self._resolve_start_position(pm0.width(), pm0.height()))

        self.state = State.APPEARING
        self._appear_idx = 0
        self._idle_idx = 0
        self._base_pixmap = pm0
        self._dragging = False
        self._drag_moved = False
        self._drag_offset = QPoint()
        self._pending_quit = False
        self._update_checker = None
        self._update_info = None  # newest version found by UpdateChecker, shown in the tray menu
        self._init_update_flow()
        self._exit_interrupted = False  # this exit sequence's one-and-only interrupt line already used?
        self._resume_exit_animation_after_interrupt = False
        self._pending_hide = False
        self._pending_hide_token = None  # distinguishes this hide request's failsafe from a later one
        self._burrow_on_done = None
        self._pomodoro_active = False
        self._current_category = None
        self._active_popup = None
        self._chat_box = None  # created lazily by _open_chat; there is only ever one
        self._api_panel = None  # the 「API 设置」 panel, created by _open_api_settings; only ever one open
        self._chat_session = None  # api_chat.ChatSession, created lazily by _get_chat_session; history lives in memory only
        self._chat_waiting = False  # a chat reply is on its way
        self._chat_sent_times = []  # clock readings of requests actually sent (local rate limit)
        self._chat_clock = time.monotonic  # injectable for tests
        self._last_face_key = None
        self._appear_source = "appear"
        self._last_duplicate_launch_roast = float("-inf")
        self._locked = False  # click-through lock is never persisted: always starts unlocked
        self._locked_click_count = 0
        self._last_lock_hint_time = float("-inf")

        self.dialogue_box = DialogueBox(
            self.pet_display_width,
            config.get("font_scale", 1.75),
            audio,
            self._always_on_top,
            confirm_hold_ms=reminders.confirm_hold_ms(),
            test_mode=reminders.test_mode,
            pet_scale=self.pet_scale,
            min_font_px=config.get("bubble_min_font_size", 15),
            screen_margin=config.get("bubble_screen_margin", 8),
            is_locked=lambda: self._locked,
        )
        self.dialogue_box.finished.connect(self._on_dialogue_finished)
        self.dialogue_box.hold_started.connect(self._on_dialogue_hold_started)
        self.dm.show_requested.connect(self._on_show_requested)
        self.dm.on_urgent_queued = self._on_urgent_line_queued
        self.arbiter = DialogueArbiter(config, self.dm, self)
        self.click_detector = anticheat.AutoClickerDetector()
        self.game_detector = GameDetector(config, self.dm, self, self.stats)

        self.flirt = FlirtController(config, parent=self)
        self.flirt.on_hesitate = self._on_flirt_hesitate
        self.flirt.on_escape = self._on_flirt_escape
        self.flirt.on_stall = self._on_flirt_stall

        self.pomodoro_display = PomodoroDisplay(self.pet_display_width, self._always_on_top, is_locked=lambda: self._locked)

        self.pomodoro.active_changed.connect(self._on_pomodoro_active_changed)
        self.pomodoro.tick.connect(self._refresh_tray_tooltip)
        self.pomodoro.tick.connect(self._on_pomodoro_tick)

        self._appear_timer = QTimer(self)
        self._appear_timer.timeout.connect(self._advance_appear)
        self._exit_timer = QTimer(self)
        self._exit_timer.timeout.connect(self._advance_exit)
        self._exit_frames = []
        self._exit_idx = 0
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._advance_idle_frame)
        self._idle_chat_timer = QTimer(self)
        self._idle_chat_timer.setSingleShot(True)
        self._idle_chat_timer.timeout.connect(self._on_idle_chat_tick)
        self._afk_timers = {
            level: self._make_singleshot(lambda lv=level: self._trigger_afk_unless_awaiting(lv))
            for level in AFK_THRESHOLDS_MIN
        }
        self._mouse_sample_timer = QTimer(self)
        self._mouse_sample_timer.timeout.connect(
            lambda: self.dm.note_mouse_sample(QCursor.pos().x())
        )
        self._mouse_sample_timer.start(500)

        self._time_of_day_timer = QTimer(self)
        self._time_of_day_timer.timeout.connect(self.dm.check_time_of_day_triggers)
        self._time_of_day_timer.start(60_000)

        self._topmost_keeper = TopmostKeeper(
            self,
            guard_active=self.game_detector.is_topmost_guard_active,
            interval_ms=config.get("topmost_keep_interval_ms", 1000),
        )
        self._topmost_keeper.start()

        self._tray = QSystemTrayIcon(QIcon(str(ICON_PATH)), self)
        self._tray.setToolTip("Flowey")
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.setContextMenu(self._build_menu())
        self._tray.show()

        self._start_appear()

    # --------------------------------------------------------------- window
    def _window_flags(self):
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self._always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        return flags

    def showEvent(self, event):
        super().showEvent(event)
        set_click_through(self, self._locked)

    def _apply_click_through(self, on):
        if on:
            _close_chat(self)  # locking closes the chat box (it would otherwise be a live input on a locked pet)
            _close_api_panel(self)
        for w in (self, self.dialogue_box, self.pomodoro_display):
            set_click_through(w, on)
        # The tooltip's "（已锁定）" suffix follows the lock. Deferred to the event
        # loop: QSystemTrayIcon.setToolTip talks to the shell, and the caller may
        # be sitting inside the tray menu's own click dispatch.
        QTimer.singleShot(0, self._refresh_tray_tooltip)

    def is_pinned_on_top(self):
        """Public hook for GameDetector/TopmostGuard: what the 右键菜单"置顶"
        checkbox currently says -- i.e. what z-order this window should be
        restored to once a temporary game-egg topmost cycle ends."""
        return self._always_on_top

    def _toggle_always_on_top(self, checked):
        if not self.arbiter.begin_feature("pin"):
            return  # inside the 0.5s debounce cooldown: state frozen, ignored
        self.stats.record_interaction()
        self._always_on_top = checked
        self.config["always_on_top"] = checked
        self.save_config(self.config)
        apply_topmost(self, checked)
        self.dialogue_box.set_always_on_top(checked)
        self.pomodoro_display.set_always_on_top(checked)
        chat = getattr(self, "_chat_box", None)
        if chat is not None:
            chat.set_always_on_top(checked)
        api_panel = getattr(self, "_api_panel", None)
        if api_panel is not None:
            api_panel.set_always_on_top(checked)
        self.arbiter.finish_feature("pin", lambda: self.dm.trigger_menu_flavor("pin_on" if checked else "pin_off"))

    def _resolve_start_position(self, w, h):
        screen = QApplication.primaryScreen().availableGeometry()
        pos = self.config.get("position")
        if pos is None:
            x = screen.x() + screen.width() - w - 40
            y = screen.y() + screen.height() - h - 60
        else:
            x, y = pos["x"], pos["y"]
        return clamp_origin_into_area(x, y, w, h, screen)

    def _make_singleshot(self, callback):
        t = QTimer(self)
        t.setSingleShot(True)
        t.timeout.connect(callback)
        return t

    # ------------------------------------------------------------- painting
    def _set_base_pixmap(self, pm):
        self._base_pixmap = pm
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pm = self.sprites.get_face("drag_face", 0) if self._dragging else self._base_pixmap
        painter.drawPixmap(0, 0, pm)

    # ------------------------------------------------------------- appear
    def _start_appear(self):
        self.state = State.APPEARING
        self._appear_source = "appear"
        self._appear_frames = self.sprites.appear_frames
        self._appear_on_done = None
        self._appear_idx = 0
        self._set_base_pixmap(self._appear_frames[0])
        self._appear_timer.start(125)

    def _start_emerge(self, on_done):
        """Re-entrance animation after un-hiding: the neutral-face grow
        animation played forward, using the same appear_neutral frames that
        _start_burrow_animation plays in reverse to bury Flowey -- so
        hide/show read as the same motion undone instead of two unrelated
        animations."""
        self.state = State.APPEARING
        self._appear_source = "appear_neutral"
        self._appear_frames = self.sprites.appear_neutral_frames
        self._appear_on_done = on_done
        self._appear_idx = 0
        self.setVisible(True)
        self._set_base_pixmap(self._appear_frames[0])
        self._appear_timer.start(125)

    def _advance_appear(self):
        self._appear_idx += 1
        if self._appear_idx >= len(self._appear_frames):
            self._appear_timer.stop()
            self._enter_idle()
            on_done = self._appear_on_done
            self._appear_on_done = None
            if on_done:
                on_done()
            else:
                self.dm.trigger_intro()
                self._maybe_say_startup_update_line()
            return
        self._set_base_pixmap(self._appear_frames[self._appear_idx])

    # --------------------------------------------------------------- idle
    def _enter_idle(self):
        self.state = State.IDLE
        self._idle_idx = 0
        if self.config.get("idle_animation", True):
            self._set_base_pixmap(self.sprites.idle_frames[0])
            self._idle_timer.start(IDLE_FRAME_MS)
        else:
            self._idle_timer.stop()
            self._set_base_pixmap(self.sprites.still_frame)
        self._schedule_idle_chat()
        self._reschedule_afk_timers()

    def _advance_idle_frame(self):
        if not self.config.get("idle_animation", True):
            return
        if self.state != State.IDLE or self._dragging:
            return
        self._idle_idx = (self._idle_idx + 1) % len(self.sprites.idle_frames)
        self._set_base_pixmap(self.sprites.idle_frames[self._idle_idx])

    def _schedule_idle_chat(self):
        delay_ms = random.randint(30, 90) * 1000
        self._idle_chat_timer.start(delay_ms)

    def _on_idle_chat_tick(self):
        if (
            self.state == State.IDLE
            and not self._dragging
            and not self._pomodoro_active
            and not _chat_visible(self)
            and not self.reminders.is_awaiting_response()
            and not self.flirt.is_confronting()
            and not self.game_detector.is_any_game_running()
        ):
            self.dm.trigger_idle_random()
        self._schedule_idle_chat()

    def _trigger_afk_unless_awaiting(self, level):
        # A reminder still waiting on a response, an active flirt
        # confrontation, or a tracked game (UNDERTALE/DELTARUNE) currently
        # running takes priority over ambient AFK chatter -- don't talk
        # over any of them. Exit lock takes priority over everything.
        if self._pending_quit:
            return
        if (
            not _chat_visible(self)
            and not self.reminders.is_awaiting_response()
            and not self.flirt.is_confronting()
            and not self.game_detector.is_any_game_running()
        ):
            self.dm.trigger_afk(level)

    def _reschedule_afk_timers(self):
        for level, t in self._afk_timers.items():
            t.start(level * 60_000)

    # ------------------------------------------------------------- pomodoro
    def _on_pomodoro_active_changed(self, active):
        self._pomodoro_active = active
        self._refresh_tray_tooltip()

    def _refresh_tray_tooltip(self):
        status = self.pomodoro.status_text()
        text = f"Flowey - {status}" if status else "Flowey"
        if self._locked:
            text += "（已锁定）"
        self._tray.setToolTip(text)

    def _on_pomodoro_tick(self):
        if self.pomodoro.is_running:
            m, s = divmod(max(self.pomodoro.remaining, 0), 60)
            self.pomodoro_display.set_time(m, s)
            self._position_pomodoro_display()
            if self.dialogue_box.isVisible():
                self._position_dialogue_box()
        else:
            self.pomodoro_display.hide_display()

    def _position_pomodoro_display(self):
        self.pomodoro_display.follow(self.x(), self.y(), self.pet_display_width)

    def _position_dialogue_box(self):
        anchor_y = self.pomodoro_display.y() if self.pomodoro_display.isVisible() else self.y()
        pet_bottom = self.y() + self.height()
        self.dialogue_box.follow(self.x(), anchor_y, self.pet_display_width, pet_bottom)
        if _chat_visible(self):
            # the bubble may have just flipped below the pet: keep the chat box clear of it
            self._position_chat_box(self.dialogue_box.geometry())

    # ------------------------------------------------------------- dialogue
    def _on_show_requested(self, segments, category):
        if self._pending_quit:
            # Exit lock (reg 1/2): any dm-routed request (idle chat, AFK,
            # menu flavor, reminders, duplicate-launch roast, ...) that
            # tries to pop a bubble once 退出 has been clicked is silently
            # swallowed and logged instead of interrupting the goodbye
            # line/burrow animation. dm.busy is left as-is (stuck True if
            # this came from dm._show()) -- harmless since the process
            # exits within EXIT_FAILSAFE_MS regardless.
            dialogue_log.log_event(f"退出锁定：拦截对话请求 category={category}")
            return
        # Defensive: a DialogueArbiter preempt() (or anything else routed
        # through dm._show() while the box was already mid-typing) needs
        # the box fully reset first -- stopping an already-idle box is a
        # safe no-op, so this is unconditional rather than only for the
        # preempt path.
        self.dialogue_box.stop_immediately()
        self.state = State.TALKING
        self._current_category = category
        self._position_dialogue_box()
        options = CHASE_POPUP_BUTTONS.get(category)
        hold_ms = self.reminders.chase.hold_ms_for_category(category) if options else None
        if category == "chat_wait":
            # the "……" while the API answers: stays up (see DialogueBox linger_ms)
            self.dialogue_box.start(
                segments, self._on_segment_face, choice_options=options, hold_ms=hold_ms, linger_ms=CHAT_WAIT_LINGER_MS
            )
            return
        self.dialogue_box.start(
            segments, self._on_segment_face, choice_options=options, hold_ms=hold_ms
        )

    def _on_segment_face(self, face_key):
        self._last_face_key = face_key
        self._set_base_pixmap(self.sprites.get_face(face_key, 0))

    def _on_dialogue_hold_started(self):
        """The popup's lines are fully typed and its button bar is now just
        sitting there for the 30-60s hold -- put the pet back into the idle
        bobbing animation instead of leaving it frozen mid-talk-pose. The
        bubble (with its buttons) stays on screen; only the sprite state
        changes. Guarded so a hold can never yank the pet out of the exit
        burrow animation."""
        if self._current_category == "chat_wait" and self.dm.has_queued_above(PRI_CHAT):
            # a reminder / pomodoro line queued up behind the waiting "……": let it through
            self.dialogue_box.end_linger()
            return
        if self.state == State.TALKING and not self._dragging:
            self._enter_idle()

    def _on_urgent_line_queued(self):
        if self._current_category == "chat_wait" and self.dialogue_box.is_lingering():
            self.dialogue_box.end_linger()

    def _on_dialogue_finished(self):
        cat = self._current_category
        # Tells the arbiter "whatever was on screen just finished" -- a
        # no-op unless it was one of its own feature reactions, in which
        # case a same-feature click that arrived mid-playback (pending)
        # now gets to schedule the next stage. Must run before anything
        # below reacts to this same finish.
        self.arbiter.on_bubble_finished()
        if cat in CHASE_POPUP_BUTTONS:
            # Report BEFORE dm.on_finished() so the controller's reaction
            # line lands in the queue and plays the moment the box frees up.
            outcome = self.dialogue_box.response_outcome or "timeout"
            self.reminders.chase.on_popup_outcome(outcome)
        if cat == "exit_interrupt":
            # The one-and-only interrupt line for this exit sequence (see
            # _handle_exit_interrupt) just finished -- resume the paused
            # burrow animation, or if we were still in the pre-burrow
            # goodbye phase, skip straight to it. Never resumes the
            # original farewell text.
            if self._resume_exit_animation_after_interrupt:
                self._resume_exit_animation_after_interrupt = False
                self._exit_timer.start(EXIT_FRAME_MS)
            else:
                self._start_burrow_animation(self._quit_now)
            return
        if self._pending_quit:
            self._start_burrow_animation(self._quit_now)
            return
        self.dm.on_finished()
        if self._pending_hide:
            self._pending_hide = False
            self._start_burrow_animation(self._finish_hide)
            return
        # dm.on_finished() may have immediately started the next queued
        # line (e.g. the chase reaction right after a popup) -- in that case
        # the box is visible again and the pet is TALKING; stomping it back
        # to IDLE here would let idle frames overwrite the talking face.
        if not self.dialogue_box.isVisible():
            self._enter_idle()

    # ---------------------------------------------------------------- mouse
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._pending_quit:
            # Exit lock: no dragging, no click-combo/interaction bookkeeping
            # while quitting -- the only thing a click can do now is the
            # escalating "被打断" reaction (see _handle_exit_interrupt).
            self._handle_exit_interrupt()
            return
        if self._locked:
            # Locked: the pet's body ignores clicks for dialogue purposes --
            # no combo/boredom tracking, no interaction stats. But a click
            # must still be able to dismiss an already-open menu/panel
            # (otherwise locking leaves no way to close the right-click
            # menu without picking an item), and the pet stays draggable by
            # default so it can still be repositioned while locked --
            # lock_disable_drag opts out of that if ever wanted.
            self._register_locked_click()
            self._close_active_popup()
            if self.config.get("lock_disable_drag", False):
                return
            self._dragging = True
            self._drag_moved = False
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            self.update()
            return
        self._close_active_popup()
        self._dragging = True
        self._drag_moved = False
        self._drag_offset = event.globalPosition().toPoint() - self.pos()
        self.dm.record_interaction()
        self.stats.record_activity()
        self._reschedule_afk_timers()
        self.update()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            return
        new_pos = event.globalPosition().toPoint() - self._drag_offset
        if not self._drag_moved:
            moved_px = (new_pos - self.pos()).manhattanLength()
            if moved_px > CLICK_MOVE_THRESHOLD:
                self._drag_moved = True
        self.move(new_pos)
        if self.pomodoro_display.isVisible():
            self._position_pomodoro_display()
        if self.dialogue_box.isVisible():
            self._position_dialogue_box()
        elif _chat_visible(self):
            self._position_chat_box()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        self.update()
        self.config["position"] = {"x": self.x(), "y": self.y()}
        self.save_config(self.config)
        if self._locked:
            # Reachable whenever lock_disable_drag is False (the default,
            # see mousePressEvent) -- position is saved above like any
            # drag, but locked clicks never trigger click dialogue/easter
            # eggs regardless of whether this was a real drag or a
            # stationary click-then-release.
            return
        if self._drag_moved:
            screen = self.screen().geometry()
            self.dm.check_position_easter_egg(
                self.x(), self.y(), self.width(), self.height(), screen.width(), screen.height()
            )
        else:
            self._handle_click()

    def _handle_click(self):
        if self.dm.busy:
            return
        if self.click_detector.is_cooling_down():
            # AC-06 冷却期：连点器发作到顶后 30 秒内完全无视点击，见 anticheat.py。
            return
        stage = self.click_detector.on_click()
        if stage is not None:
            self.dm.trigger_auto_clicker_stage(stage)
            self.stats.record_interaction()
            return
        if self.arbiter.note_body_click():
            # Spam-clicking the pet's own body: the arbiter already showed
            # the "厌倦" reaction directly (higher priority than ordinary
            # click chat, reg 7) -- don't also fire the normal click line.
            self.stats.record_interaction()
            return
        self.dm.trigger_click()
        self.stats.record_interaction()

    def _register_locked_click(self):
        """Counts clicks landed on the locked pet's body -- purely to fire
        an occasional out-of-character reminder that lock is a deliberate
        toggle, not the app being broken (see dialogue.json's lock_hint
        pool). Never triggers any in-character reaction."""
        self._locked_click_count += 1
        threshold = self.config.get("lock_hint_click_threshold", 10)
        if self._locked_click_count < threshold:
            return
        self._locked_click_count = 0
        cooldown = self.config.get("lock_hint_cooldown_seconds", 300)
        now = time.monotonic()
        if now - self._last_lock_hint_time < cooldown:
            return
        self._last_lock_hint_time = now
        self.dm.trigger_lock_hint()

    # ----------------------------------------------------------------- menu
    def _build_menu(self):
        menu = QMenu()

        update_info = getattr(self, "_update_info", None)
        if isinstance(update_info, dict) and "version" in update_info:
            ctl = getattr(self, "_update_ctl", None)
            if ctl is not None and ctl.busy:
                update_action = menu.addAction(f"正在更新到 v{ctl.version}…")
            else:
                update_action = menu.addAction(f"更新到 v{update_info['version']}")
            update_action.triggered.connect(self._on_update_action)
            menu.addSeparator()

        visible_action = menu.addAction("隐藏" if self.isVisible() else "显示 Flowey")
        visible_action.triggered.connect(self._toggle_visible)

        pin_action = menu.addAction("置顶")
        pin_action.setCheckable(True)
        pin_action.setChecked(self._always_on_top)
        pin_action.triggered.connect(self._toggle_always_on_top)

        lock_action = menu.addAction("锁定")
        lock_action.setCheckable(True)
        lock_action.setChecked(self._locked)
        lock_action.triggered.connect(self._on_tray_lock_triggered)
        # The check mark is re-synced from the real state only here, every time
        # the menu is about to open -- never from inside the click handler.
        menu.aboutToShow.connect(lambda: lock_action.setChecked(self._locked))

        menu.addSeparator()
        pomo_menu = menu.addMenu("番茄钟")
        if self.pomodoro.is_running:
            status = pomo_menu.addAction(self.pomodoro.status_text() or "")
            status.setEnabled(False)
            cancel_action = pomo_menu.addAction("取消番茄钟")
            cancel_action.triggered.connect(self._cancel_pomodoro_from_menu)
        else:
            start_action = pomo_menu.addAction("开始专注 (25+5)")
            start_action.triggered.connect(self._start_default_pomodoro)
            custom_action = pomo_menu.addAction("自定义时长…")
            custom_action.triggered.connect(self._custom_pomodoro)

        reminder_action = menu.addAction("提醒…")
        reminder_action.triggered.connect(self._open_reminder_settings)

        menu.addSeparator()
        mute_action = menu.addAction("静音")
        mute_action.setCheckable(True)
        mute_action.setChecked(self.config.get("muted", False))
        mute_action.triggered.connect(self._toggle_mute)

        menu.addAction(self._build_volume_action(menu))

        autostart_action = menu.addAction("开机自启动")
        autostart_action.setCheckable(True)
        autostart_action.setChecked(self.config.get("autostart", False))
        autostart_action.triggered.connect(self._toggle_autostart)

        menu.addSeparator()
        info_action = menu.addAction("查看信息")
        info_action.triggered.connect(self._view_info)

        menu.addSeparator()
        quit_action = menu.addAction("退出")
        quit_action.triggered.connect(self.request_quit)

        return menu

    def _build_volume_action(self, menu):
        """The tray menu's volume row: all tiers laid out side by side so a
        single click sets the level directly. This is a native QMenu (Windows
        renders it, not us), so it can't host the Undertale popup/heart-cursor
        look the pet's own right-click menu uses for its "音量" row -- that
        one opens a separate VolumePanel instead, see pet_menu.py."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(20, 4, 20, 4)
        layout.setSpacing(10)
        layout.addWidget(QLabel("音量"))
        layout.addStretch(1)

        labels = []

        def nearest_index():
            current = round(self.config.get("volume", 0.8) * 100)
            return min(range(len(VOLUME_TIERS)), key=lambda i: abs(VOLUME_TIERS[i] - current))

        def refresh():
            idx = nearest_index()
            for i, lbl in enumerate(labels):
                font = lbl.font()
                font.setBold(i == idx)
                lbl.setFont(font)
                lbl.setStyleSheet("color: #0078D4;" if i == idx else "")

        def choose(pct):
            self._set_volume(pct)
            refresh()
            menu.close()

        for pct in VOLUME_TIERS:
            lbl = ClickableLabel(f"{pct}%")
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            lbl.clicked.connect(lambda p=pct: choose(p))
            labels.append(lbl)
            layout.addWidget(lbl)

        refresh()
        action = QWidgetAction(menu)
        action.setDefaultWidget(widget)
        return action

    def contextMenuEvent(self, event):
        if self._pending_quit:
            dialogue_log.log_event("退出锁定：右键菜单请求已拦截")
            return
        self._close_active_popup()
        # Deferred to the next event-loop tick: showing a Qt.Popup
        # synchronously inside the mouse event that spawned it can make the
        # popup mistake the tail end of that same right-click for an
        # outside click and immediately close itself.
        QTimer.singleShot(0, self._show_context_menu)

    def _show_context_menu(self):
        from pet_menu import PetContextMenu

        self._close_active_popup()
        menu = PetContextMenu(self, ui_scale=self._ui_scale(), min_font_px=self.config.get("ui_min_font_size", 10))
        self._place_popup(menu)
        self._active_popup = menu
        menu.show()
        menu.setFocus()

    def _place_popup(self, popup, ref_h=None):
        """The one placement rule for every popup (see
        undertale_ui.position_avoid_pet_and_bubble). ref_h is the main
        menu's height, so all popups agree on above/below/side."""
        from undertale_ui import position_avoid_pet_and_bubble

        position_avoid_pet_and_bubble(
            popup,
            self.x(),
            self.y(),
            self.width(),
            self.height(),
            self.dialogue_box,
            margin=self.config.get("panel_screen_margin", 8),
            ref_h=ref_h,
        )

    def _main_menu_height(self):
        """Height the main menu has right now (measured on a throwaway,
        never-shown instance) -- the common yardstick for deciding whether
        the pet hugs the top/bottom screen edge."""
        from pet_menu import PetContextMenu

        probe = PetContextMenu(self, ui_scale=self._ui_scale(), min_font_px=self.config.get("ui_min_font_size", 10))
        probe.finish_layout()
        height = probe.height()
        probe.deleteLater()
        return height

    def _open_menu_category(self, key):
        """Main menu's category row clicked: the row closes the main menu
        itself; the category's box opens on the next event-loop tick (never
        from inside the click that is still closing the menu)."""
        QTimer.singleShot(0, lambda: self._show_category_popup(key))

    def _show_category_popup(self, key):
        from pet_menu import CategoryPopup, find_category

        if self._pending_quit:
            return
        spec = find_category(self, key)
        if spec is None:
            return
        self._close_active_popup()
        box = CategoryPopup(self, spec, ui_scale=self._ui_scale(), min_font_px=self.config.get("ui_min_font_size", 10))
        if box.flirt_row is not None:
            # The 调情 hover/escape state machine listens to the box that
            # actually holds the 调情 row (not the main menu), so entering
            # the box from the main menu never reads as "menu closed".
            box.selection_changed.connect(
                lambda row: self.flirt.on_menu_selection_changed(row is box.flirt_row)
            )
            box.closed.connect(self.flirt.on_menu_closed)
        self._place_popup(box, ref_h=self._main_menu_height())
        self._active_popup = box
        box.show()
        box.setFocus()

    def _back_to_main_menu(self):
        """"< 返回" / Esc in a category box: close it, reopen the main menu
        (queued, like a fresh right-click)."""
        self._close_active_popup()
        if self._pending_quit:
            return
        QTimer.singleShot(0, self._show_context_menu)

    def _close_active_popup(self):
        if self._active_popup is not None:
            self._active_popup.close()
            self._active_popup = None

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_visible()
        self._tray.setContextMenu(self._build_menu())

    def _toggle_visible(self):
        # Hiding makes the pet disappear, which would otherwise strand any
        # open context menu / reminder panel floating with nothing beside it.
        if not self.arbiter.begin_feature("hide"):
            return  # inside the 0.5s debounce cooldown: state frozen, ignored
        self.stats.record_interaction()
        self._close_active_popup()
        if self.isVisible():
            _close_chat(self)
            _close_api_panel(self)
            self._hide_with_flavor()
        else:
            self._start_emerge(lambda: self.arbiter.finish_feature("hide", lambda: self.dm.trigger_menu_flavor("show")))

    def _hide_with_flavor(self):
        # Burrowing (actually hiding) is triggered from _on_dialogue_finished
        # once the "hide" line finishes -- but the arbiter may end up
        # speaking NOTHING at all for this click (silence stage / a
        # reminder popup suppressing it), in which case dialogue_box.finished
        # would never fire and the pet would be stuck stranded, still
        # visible, forever. This failsafe (same pattern as reminder_chase's
        # popup failsafe) forces the burrow anyway if nothing resolved it
        # within a generous margin. The token guards against a stale
        # failsafe from an earlier hide click firing after a newer one.
        self._pending_hide = True
        token = object()
        self._pending_hide_token = token
        self.arbiter.finish_feature("hide", lambda: self.dm.trigger_menu_flavor("hide"))
        QTimer.singleShot(
            int(self.arbiter.feature_debounce_seconds * 1000) + HIDE_FAILSAFE_GRACE_MS,
            lambda: self._hide_failsafe(token),
        )

    def _hide_failsafe(self, token):
        if self._pending_hide and self._pending_hide_token is token:
            dialogue_log.log_event("隐身失效保护：本次台词被抑制/沉默，强制执行钻土动画")
            self._pending_hide = False
            self._start_burrow_animation(self._finish_hide)

    # ------------------------------------------------------- single instance
    def on_duplicate_launch_activation(self):
        """Called (via SingleInstanceGuard.on_activate_requested, see
        main.py) whenever a second FloweyPet.exe is launched while this one
        is already running. Cancels hide unconditionally; the roast line
        itself is separately cooldown- and busy-gated."""
        if self.isVisible():
            self._maybe_roast_duplicate_launch()
        else:
            self._start_emerge(self._maybe_roast_duplicate_launch)

    def _maybe_roast_duplicate_launch(self):
        if self.dm.busy:
            app_log.log_event("重复启动吐槽已跳过（正在播放对话/追讨弹窗）")
            return  # the unhide (if any) already happened; just skip the roast
        cooldown = self.config.get("duplicate_launch_cooldown_seconds", 10)
        now = time.monotonic()
        if now - self._last_duplicate_launch_roast < cooldown:
            app_log.log_event("重复启动吐槽已跳过（冷却中）")
            return
        self._last_duplicate_launch_roast = now
        app_log.log_event("重复启动吐槽已播放")
        self.dm.trigger_duplicate_launch()

    def _toggle_mute(self, checked):
        if not self.arbiter.begin_feature("mute"):
            return  # inside the 0.5s debounce cooldown: state frozen, ignored
        self.stats.record_interaction()
        self.config["muted"] = checked
        self.save_config(self.config)
        self.audio.set_muted(checked)
        self.arbiter.finish_feature("mute", lambda: self.dm.trigger_menu_flavor("mute_on" if checked else "mute_off"))

    # ---------------------------------------------------------- update check
    def attach_update_checker(self, checker):
        self._update_checker = checker
        checker.update_available.connect(self._on_update_available)

    def _open_update_page(self, url):
        QDesktopServices.openUrl(QUrl(url))

    # ------------------------------------------------- one-click update
    def _init_update_flow(self):
        self._update_ctl = update_flow.UpdateController(
            can_update=lambda info: update_flow.can_one_click_update(info, IS_DEV, str(APP_DIR)),
            fetch=update_flow.fetch_package,
            launch=updater_apply.default_launcher,
            open_page=lambda url: self._open_update_page(url),
            say=self._say_update_line,
            quit_for_update=self.request_quit_for_update,
            arm_deadline=self._arm_update_quit_deadline,
            is_quitting=lambda: self._pending_quit,
            post=self._update_result.emit,
            self_info=lambda: (os.getpid(), updater_apply.get_process_creation_time(os.getpid())),
            old_version=version.VERSION,
            app_dir=str(APP_DIR),
            log=app_log.log_event,
            on_state_change=self._refresh_update_menu,
        )
        self._update_result.connect(self._on_update_result)
        self._update_quit_timer = None
        self._startup_update_category = update_flow.startup_update_category(sys.argv[1:])

    def _on_update_action(self, *_checked):
        info = self._update_info
        if isinstance(info, dict) and "version" in info:
            self._update_ctl.on_click(info)

    def _on_update_result(self, result):
        self._update_ctl.on_result(result)

    def _refresh_update_menu(self):
        tray = getattr(self, "_tray", None)
        if tray is not None:
            tray.setContextMenu(self._build_menu())

    def _say_update_line(self, category):
        if self.isVisible() and not self._pending_quit:
            self.dm.trigger_update_line(category)

    def _arm_update_quit_deadline(self):
        """From the moment the updater is launched it only waits
        UPDATE_QUIT_DEADLINE_MS for this process: quit no matter what."""
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._quit_now)
        timer.start(UPDATE_QUIT_DEADLINE_MS)
        self._update_quit_timer = timer

    def _maybe_say_startup_update_line(self):
        """After an update / failed update, said once at the first appearance."""
        category = getattr(self, "_startup_update_category", None)
        if category and self.isVisible() and not self._pending_quit:
            self._startup_update_category = None
            self.dm.trigger_update_line(category)

    def _on_update_available(self, info):
        # The tray menu is built once at startup and again after each tray
        # activation, so rebuild it right now to make the new entry appear.
        self._update_info = info
        self._tray.setContextMenu(self._build_menu())
        # Speak only when Flowey is actually on screen and not leaving; in
        # that case don't record it either, so the next check can announce.
        if self.isVisible() and not self._pending_quit and should_announce(self.config, info["version"]):
            self.dm.trigger_update_announce(info["version"], info.get("line"), info.get("face"))
            self.config["last_announced_version"] = info["version"]
            self.save_config(self.config)

    def _toggle_update_check(self, checked):
        self.stats.record_interaction()
        self.config["check_updates"] = checked
        self.save_config(self.config)
        if self._update_checker is not None:
            self._update_checker.set_enabled(checked)

    def _toggle_menu_sfx(self, checked):
        self.stats.record_interaction()
        self.config["menu_sfx_enabled"] = checked
        self.save_config(self.config)
        self.audio.set_menu_sfx_enabled(checked)

    def _toggle_idle_animation(self, checked):
        # Plain preference switch: no arbiter, no dialogue.
        self.config["idle_animation"] = checked
        self.save_config(self.config)
        if self.state == State.IDLE:
            self._idle_idx = 0
            if checked:
                self._set_base_pixmap(self.sprites.idle_frames[0])
                self._idle_timer.start(IDLE_FRAME_MS)
            else:
                self._idle_timer.stop()
                self._set_base_pixmap(self.sprites.still_frame)

    def _toggle_lock(self, checked):
        # Routed through DialogueArbiter same as pin/mute/hide/autostart,
        # but lock has its own dedicated 3-tier toggle-count escalation
        # (无语/不耐烦/爆发) instead of the generic per-feature ladder --
        # see dialogue_arbiter.py's _wrap_lock_provider. The tantrum check
        # happens BEFORE begin_feature's debounce gate: during that window
        # the toggle is a complete no-op (no state change, no dialogue),
        # not just a debounce-cooldown skip.
        if self.arbiter.is_lock_tantrum_active():
            dialogue_log.log_event("锁定：闹脾气冷区内，切换请求被忽略（功能未生效）")
            return
        if not self.arbiter.begin_feature("lock"):
            return  # inside the 0.5s debounce cooldown: state frozen, ignored
        self.stats.record_interaction()
        self._locked = checked
        self._apply_click_through(checked)
        if not checked:
            self._locked_click_count = 0
        self.arbiter.finish_feature("lock", lambda: self.dm.trigger_menu_flavor("lock_on" if checked else "lock_off"))

    def _force_unlock(self):
        """Emergency exit from click-through lock (tray menu / hotkey). Must
        always succeed: it never goes through begin_feature or the tantrum
        zone, doesn't speak and doesn't touch stats -- it just unlocks and
        reopens the arbiter's lock gates so the next toggle isn't refused."""
        self._locked = False
        self._apply_click_through(False)
        self._locked_click_count = 0
        self.arbiter.reset_lock_gates()
        dialogue_log.log_event("强制解锁（托盘/热键）")

    def _on_tray_lock_triggered(self, checked):
        """Tray menu 锁定 item: unlocking is the forced path (never
        refused); locking goes through the normal arbiter flow (may be
        refused during the debounce/tantrum gates).

        Runs inside the tray menu's own click dispatch, so it only decides
        what to do (from the lock state AT CLICK TIME) and defers the actual
        lock/unlock -- which flips native window styles and touches the tray
        icon -- to the next event-loop turn, after the menu has finished
        closing. It does not touch the action's check mark either: that is
        re-synced only by the menu's aboutToShow."""
        was_locked = self._locked
        QTimer.singleShot(0, self._force_unlock if was_locked else (lambda: self._toggle_lock(True)))

    def _set_volume(self, pct):
        # Continuous-style feature (reg 4): every call really applies the
        # new volume right away, no debounce gate on that. Only the
        # dialogue collapses to the trailing edge -- each call restarts
        # the arbiter's timer and replaces the pending reaction, so a burst
        # of rapid adjustments stays silent until it settles and then
        # speaks once, for whichever single before/after pair was last.
        self.stats.record_interaction()
        old_pct = round(self.config.get("volume", 0.8) * 100)
        self.config["volume"] = pct / 100
        self.save_config(self.config)
        self.audio.set_volume(pct / 100)

        def _speak(old=old_pct, new=pct):
            if new > old:
                self.dm.trigger_menu_flavor("volume_up")
            elif new < old:
                self.dm.trigger_menu_flavor("volume_down")

        self.arbiter.finish_feature("volume", _speak)

    @_guarded_menu_action("音量面板")
    def _open_volume_panel(self):
        from volume_panel import VolumePanel

        self.stats.record_interaction()
        self._close_active_popup()
        panel = VolumePanel(
            lambda: round(self.config.get("volume", 0.8) * 100),
            self._set_volume,
            audio=self.audio,
            always_on_top=self._always_on_top,
            ui_scale=self._ui_scale(),
            min_font_px=self.config.get("ui_min_font_size", 10),
        )
        self._place_popup(panel, ref_h=self._main_menu_height())
        self._active_popup = panel
        panel.show()
        panel.setFocus()

    # ------------------------------------------------------------- sizing
    def _ui_scale(self):
        """Menus/panels only follow *part* of the way the pet shrinks, so
        they never become illegibly tiny at the pet's smallest tier."""
        ratio = self.config.get("ui_follow_ratio", 0.5)
        return 1 - (1 - self.pet_scale) * ratio

    def _current_frame_for_state(self):
        """Refetches whatever frame should be on screen right now from
        self.sprites at its *current* (just-updated) scale -- used right
        after a tier switch to redraw the pet at the new size without
        waiting for the next animation tick. self.sprites.set_pet_scale()
        rebuilds appear_frames/appear_neutral_frames/idle_frames/quit_frames
        as brand-new list objects, so any list captured earlier (e.g.
        self._appear_frames, self._exit_frames) is stale and must be
        re-pulled from self.sprites rather than indexed directly."""
        if self.state == State.TALKING and self._last_face_key:
            return self.sprites.get_face(self._last_face_key, 0)
        if self.state == State.APPEARING:
            frames = (
                self.sprites.appear_neutral_frames
                if self._appear_source == "appear_neutral"
                else self.sprites.appear_frames
            )
            self._appear_frames = frames
            idx = min(self._appear_idx, len(frames) - 1)
            return frames[idx]
        if self.state == State.EXITING:
            frames = self.sprites.quit_frames or list(reversed(self.sprites.appear_neutral_frames))
            self._exit_frames = frames
            idx = min(self._exit_idx, len(frames) - 1)
            return frames[idx]
        if not self.config.get("idle_animation", True):
            return self.sprites.still_frame
        idx = min(self._idle_idx, len(self.sprites.idle_frames) - 1)
        return self.sprites.idle_frames[idx]

    def _apply_pet_scale(self, scale):
        """Switches size tier in place: rescales every sprite, keeps the
        pet's bottom-center anchored (feet don't move), and cascades the
        new pet_display_width to the bubble/pomodoro badge so they stay
        proportioned and correctly positioned. Applies instantly, no
        restart needed; persists immediately so it survives one."""
        self.stats.record_interaction()
        old_bottom = self.y() + self.height()
        old_center_x = self.x() + self.width() / 2

        self.sprites.set_pet_scale(scale)
        self.pet_scale = scale

        new_size = self.sprites.appear_frames[0].size()
        new_w, new_h = new_size.width(), new_size.height()
        new_x = round(old_center_x - new_w / 2)
        new_y = old_bottom - new_h
        # Growing a tier can push the pet past a screen edge: clamp back
        # into the available area of the screen the pet is on right now.
        screen = self.screen() or QApplication.primaryScreen()
        new_x, new_y = clamp_origin_into_area(new_x, new_y, new_w, new_h, screen.availableGeometry())
        self.setFixedSize(new_w, new_h)
        self.move(new_x, new_y)
        self.pet_display_width = new_w

        self._set_base_pixmap(self._current_frame_for_state())

        self.dialogue_box.reconfigure(
            self.pet_display_width,
            self.config.get("font_scale", 1.75),
            scale,
            self.config.get("bubble_min_font_size", 15),
        )
        chat = getattr(self, "_chat_box", None)
        if chat is not None:
            chat.reconfigure(
                self.pet_display_width,
                self.config.get("font_scale", 1.75),
                scale,
                self.config.get("bubble_min_font_size", 15),
            )
        api_panel = getattr(self, "_api_panel", None)
        if api_panel is not None:
            api_panel.reconfigure(
                self.pet_display_width,
                self.config.get("font_scale", 1.75),
                scale,
                self.config.get("bubble_min_font_size", 15),
            )
        self.pomodoro_display.reconfigure(self.pet_display_width)
        if self.dialogue_box.isVisible():
            self._position_dialogue_box()
        elif _chat_visible(self):
            self._position_chat_box()
        if self.pomodoro_display.isVisible():
            self._position_pomodoro_display()

        self.config["pet_scale"] = scale
        self.save_config(self.config)
        self.update()

    @_guarded_menu_action("大小面板")
    def _open_size_panel(self):
        from size_panel import SizePanel

        self.stats.record_interaction()
        self._close_active_popup()
        panel = SizePanel(
            lambda: self.pet_scale,
            self._apply_pet_scale,
            self.config.get("pet_size_tiers", DEFAULT_PET_SIZE_TIERS),
            audio=self.audio,
            always_on_top=self._always_on_top,
            ui_scale=self._ui_scale(),
            min_font_px=self.config.get("ui_min_font_size", 10),
        )
        self._place_popup(panel, ref_h=self._main_menu_height())
        self._active_popup = panel
        panel.show()
        panel.setFocus()

    def _toggle_pomodoro_from_menu(self):
        if self.pomodoro.is_running:
            self._cancel_pomodoro_from_menu()
        else:
            self._custom_pomodoro()

    def _toggle_autostart(self, checked):
        from config import set_autostart

        if not self.arbiter.begin_feature("autostart"):
            return  # inside the 0.5s debounce cooldown: state frozen, ignored
        self.stats.record_interaction()
        self.config["autostart"] = checked
        self.save_config(self.config)
        set_autostart(checked)
        self.arbiter.finish_feature(
            "autostart", lambda: self.dm.trigger_menu_flavor("autostart_on" if checked else "autostart_off")
        )

    @_guarded_menu_action("提醒设置")
    def _open_reminder_settings(self):
        from reminder_settings import ReminderPanel

        self.stats.record_interaction()
        # Opening the panel always works (it's a view, not a persistent
        # toggle) -- only the flavor line is debounced/escalated.
        self.arbiter.finish_feature("reminder_settings", lambda: self.dm.trigger_menu_flavor("reminder_settings"))
        self._close_active_popup()
        panel = ReminderPanel(
            self.reminders,
            audio=self.audio,
            always_on_top=self._always_on_top,
            ui_scale=self._ui_scale(),
            min_font_px=self.config.get("ui_min_font_size", 10),
        )
        self._place_popup(panel, ref_h=self._main_menu_height())
        self._active_popup = panel
        panel.show()
        panel.setFocus()

    # ------------------------------------------------------------------ chat
    def is_chat_open(self):
        return _chat_visible(self)

    @_guarded_menu_action("聊天")
    def _open_chat(self):
        """行动 -> 聊天. One ChatBox for the whole session: already open
        just means "focus it again", never a second box."""
        self._close_active_popup()
        if self._pending_quit:
            return
        self.stats.record_interaction()
        box = self._chat_box
        if box is None:
            box = ChatBox(
                self.pet_display_width,
                self.config.get("font_scale", 1.75),
                self._always_on_top,
                self.pet_scale,
                self.config.get("bubble_min_font_size", 15),
            )
            box.submitted.connect(self._on_chat_submitted)
            box.set_waiting(self._chat_waiting)
            self._chat_box = box
        if not box.isVisible():
            box.show()  # needs a size on screen before it can be placed
        self._position_chat_box()
        box.open_and_focus()
        # The menu popup that triggered us is still closing and may hand
        # keyboard focus back to the pet window as it goes; take it again
        # on the next event-loop turn.
        QTimer.singleShot(0, lambda: box.open_and_focus() if box.isVisible() else None)

    def _position_chat_box(self, bubble_rect=None):
        """Under the pet, centred. If that spot doesn't fit the screen (with
        bubble_screen_margin) or would overlap the pet or the dialogue
        bubble: right of the pet, then left, then above it, and as a last
        resort the screen-clamped spot below."""
        from dialogue_box import GAP_ABOVE_PET
        from undertale_ui import clamp_rect_to_screen, screen_available_rect

        box = getattr(self, "_chat_box", None)
        if box is None:
            return
        margin = self.config.get("bubble_screen_margin", 8)
        pet_rect = QRect(self.x(), self.y(), self.width(), self.height())
        screen = screen_available_rect(pet_rect.x(), pet_rect.y(), pet_rect.width(), pet_rect.height())
        if bubble_rect is None and self.dialogue_box.isVisible():
            bubble_rect = self.dialogue_box.geometry()
        bw, bh = box.width(), box.height()
        gap = GAP_ABOVE_PET
        pet_bottom = pet_rect.y() + pet_rect.height()
        cx = pet_rect.x() + (pet_rect.width() - bw) // 2
        inner = screen.adjusted(margin, margin, -margin, -margin)

        def clear(r):
            return inner.contains(r) and not r.intersects(pet_rect) and (bubble_rect is None or not r.intersects(bubble_rect))

        below = clamp_rect_to_screen(QRect(cx, pet_bottom + gap, bw, bh), screen, margin)
        candidates = [
            below,
            QRect(pet_rect.x() + pet_rect.width() + gap, pet_bottom - bh, bw, bh),
            QRect(pet_rect.x() - gap - bw, pet_bottom - bh, bw, bh),
            clamp_rect_to_screen(QRect(cx, pet_rect.y() - gap - bh, bw, bh), screen, margin),
        ]
        chosen = next((r for r in candidates if clear(r)), below)
        box.move(chosen.x(), chosen.y())

    # ------------------------------------------------------------ chat <-> API
    def _make_chat_session(self):
        """Factory for the one ChatSession (tests replace it to inject a fake
        fetcher)."""
        return api_chat.ChatSession(
            self._api_settings,
            self.dm._substitute,
            self._chat_context,
        )

    def _get_chat_session(self):
        session = self._chat_session
        if session is None:
            session = self._make_chat_session()
            session.replied.connect(self._on_chat_replied)
            session.failed.connect(self._on_chat_failed)
            session.probed.connect(self._on_api_probed)
            self._chat_session = session
        return session

    def _api_settings(self):
        """Read fresh on every request: address/model from config, the key
        straight from api_key.txt (never cached anywhere else)."""
        cfg = self.config.get("api_chat")
        cfg = cfg if isinstance(cfg, dict) else {}
        return {
            "base_url": cfg.get("base_url") or DEFAULT_BASE_URL,
            "model": cfg.get("model") or DEFAULT_MODEL,
            "api_key": api_key_store.load_key(),
        }

    def _chat_context(self):
        return {"time": datetime.now().strftime("%H:%M"), "days": self.dm.days_since_install()}

    def _chat_limits(self):
        cfg = self.config.get("api_chat")
        cfg = cfg if isinstance(cfg, dict) else {}
        min_interval = cfg.get("min_interval_seconds", 3)
        max_per_window = cfg.get("max_per_10min", 30)
        if isinstance(min_interval, bool) or not isinstance(min_interval, (int, float)) or min_interval < 0:
            min_interval = 3
        if isinstance(max_per_window, bool) or not isinstance(max_per_window, int) or max_per_window < 1:
            max_per_window = 30
        return min_interval, max_per_window

    def _say_chat_line(self, category, segments):
        """Plays a chat line (reply / error / wait). Normally it cuts in over
        whatever is on screen (usually the waiting "……"); while a reminder
        popup is up, or the app is quitting, it queues instead and never
        interrupts."""
        if not segments or not self.isVisible():
            return
        if self._pending_quit or self.is_reminder_popup_active():
            self.dm.speak(category, segments, priority=PRI_CHAT, bypass_interval=True)
        else:
            self.dm.cut_in(category, segments)

    def _say_chat_pool(self, pool):
        self._say_chat_line(pool, self.dm.pick_chat_line(pool))

    def _say_chat_wait(self):
        """The "……" shown while the API answers. Skipped (not queued) when a
        reminder popup is up or we are quitting."""
        if self._pending_quit or self.is_reminder_popup_active():
            return
        self._say_chat_line("chat_wait", self.dm.pick_chat_line("chat_wait"))

    def _restore_chat_text(self, text):
        box = getattr(self, "_chat_box", None)
        if box is not None:
            box.restore_text(text)

    def _set_chat_waiting(self, waiting):
        self._chat_waiting = waiting
        box = getattr(self, "_chat_box", None)
        if box is not None:
            box.set_waiting(waiting)

    def _on_chat_submitted(self, text):
        # What the user types and what comes back are never logged.
        if self._pending_quit:
            return
        self.stats.record_interaction()
        if not api_key_store.has_key():
            self._restore_chat_text(text)  # nothing was sent: give the typed line back
            self.dm.trigger_chat_no_api()
            return
        session = self._get_chat_session()
        now = self._chat_clock()
        min_interval, max_per_window = self._chat_limits()
        sent = [t for t in self._chat_sent_times if now - t < CHAT_RATE_WINDOW_S]
        self._chat_sent_times = sent
        if session.busy or (sent and now - sent[-1] < min_interval) or len(sent) >= max_per_window:
            self._restore_chat_text(text)  # not sent (rate limit / a request in flight): give the line back
            self._say_chat_pool("chat_too_frequent")
            return
        self._chat_sent_times.append(now)
        self._set_chat_waiting(True)
        self._say_chat_wait()
        session.ask(text)  # the answer arrives through replied / failed (maybe right away in tests)

    def _on_chat_replied(self, reply):
        self._set_chat_waiting(False)
        self._say_chat_line("chat_reply", [{"text": reply["text"], "face": reply["face"]}])

    def _on_chat_failed(self, code):
        self._set_chat_waiting(False)
        self._say_chat_pool(CHAT_ERROR_POOLS.get(code, CHAT_ERROR_DEFAULT_POOL))

    def _on_api_probed(self, outcome):
        """Result of the connection test started by saving the API settings."""
        if outcome == "ok":
            self._say_chat_pool("api_saved")
        else:
            self._say_chat_pool(CHAT_ERROR_POOLS.get(outcome, CHAT_ERROR_DEFAULT_POOL))

    # --------------------------------------------------------- API settings
    @_guarded_menu_action("API 设置")
    def _open_api_settings(self):
        """存档点 -> API 设置. One panel at a time: already open just
        means "focus it again"."""
        self._close_active_popup()
        if self._pending_quit:
            return
        self.stats.record_interaction()
        panel = self._api_panel
        if panel is not None and panel.isVisible():
            panel.open_and_focus()
            return
        if panel is not None:
            panel.deleteLater()
        cfg = self.config.get("api_chat")
        cfg = cfg if isinstance(cfg, dict) else {}
        panel = ApiSettingsPanel(
            cfg.get("base_url") or DEFAULT_BASE_URL,
            cfg.get("model") or DEFAULT_MODEL,
            api_key_store.has_key(),
            self.pet_display_width,
            self.config.get("font_scale", 1.75),
            self._always_on_top,
            self.pet_scale,
            self.config.get("bubble_min_font_size", 15),
        )
        panel.saved.connect(self._on_api_settings_saved)
        panel.key_deleted.connect(self._on_api_key_deleted)
        self._api_panel = panel
        self._place_popup(panel, ref_h=self._main_menu_height())
        panel.open_and_focus()
        # The menu popup that triggered us is still closing and may hand
        # keyboard focus back to the pet window as it goes; take it again
        # on the next event-loop turn.
        QTimer.singleShot(0, lambda: panel.open_and_focus() if panel.isVisible() else None)

    def _on_api_settings_saved(self, base_url, model, key):
        """Panel -> save. Address and model go to config.json; the key (only
        if one was typed) goes to api_key.txt, never to config.json. Nothing
        typed is logged."""
        api = self.config.get("api_chat")
        if not isinstance(api, dict):
            api = {}
            self.config["api_chat"] = api
        api["base_url"] = base_url
        api["model"] = model
        self.save_config(self.config)
        if key and not api_key_store.save_key(key):
            self._show_error_flavor()
            return
        if not api_key_store.has_key():
            self._say_chat_pool("api_saved_no_key")
            return
        session = self._get_chat_session()
        if session.busy:
            # a chat request is already on its way: no connection test now
            self.dm.trigger_api_saved()
            return
        self._say_chat_wait()
        session.probe()  # the outcome arrives through probed -> _on_api_probed

    def _on_api_key_deleted(self):
        if not api_key_store.delete_key():
            self._show_error_flavor()
            return
        self.dm.trigger_api_key_deleted()

    @_guarded_menu_action("战斗")
    def _on_fight_clicked(self):
        # Placeholder: one line from dialogue.json's "fight" pool.
        self.stats.record_interaction()
        self.arbiter.finish_feature("fight", lambda: self.dm.trigger_menu_flavor("fight"))

    @_guarded_menu_action("查看信息")
    def _view_info(self):
        self.stats.record_interaction()
        self.arbiter.finish_feature("view_info", self.dm.trigger_view_info)

    # ----------------------------------------------------------------- flirt
    def _on_flirt_hesitate(self):
        segments = flirt_dialogue.pick_hesitate()
        if segments:
            self.dm.speak("flirt_hesitate", segments)

    def _on_flirt_escape(self):
        segments = flirt_dialogue.pick_escape()
        if segments:
            self.dm.speak("flirt_escape", segments)

    def _on_flirt_stall(self):
        segments = flirt_dialogue.pick_stall()
        if segments:
            self.dm.speak("flirt_stall", segments)

    @_guarded_menu_action("调情")
    def _on_flirt_clicked(self):
        was_confronting = self.flirt.resolve_click()
        self.stats.record_interaction()
        new_count = self.stats.record_flirt()
        response_segments = flirt_dialogue.pick_response(new_count) or []
        if was_confronting:
            segments = (flirt_dialogue.pick_caught_confirm() or []) + response_segments
            category = "flirt_caught_confirm"
        else:
            segments = response_segments
            category = "flirt_response"
        if segments:
            self.dm.speak(category, segments)

    def _show_error_flavor(self):
        """Last-resort fallback bubble for _guarded_menu_action: whatever
        broke, at least tell the user where to look instead of failing
        silently or taking the whole app down with it."""
        segments = [
            {
                "text": "呃……我这里出了点故障。去看看error.log吧。",
                "face": ["shocked", "sheepish_smile"],
            }
        ]
        try:
            self.dm.speak("internal_error", segments)
        except Exception:
            log_exception("_show_error_flavor itself failed")

    def _start_default_pomodoro(self):
        # Pomodoro's own "start"/"end" status lines (Pomodoro.start()'s
        # trigger_pomodoro_status("start")) are a separate, higher-priority
        # mechanism and untouched here -- the arbiter only tracks repeated
        # start/cancel/start menu clicks for the 番茄钟 escalation ladder,
        # with no normal-tier line of its own to speak (provider=None).
        if not self.arbiter.begin_feature("pomodoro"):
            return
        self.stats.record_interaction()
        self.pomodoro.start()
        self.arbiter.finish_feature("pomodoro")

    def _cancel_pomodoro_from_menu(self):
        if not self.arbiter.begin_feature("pomodoro"):
            return
        self.stats.record_interaction()
        self.pomodoro.cancel()
        self.arbiter.finish_feature("pomodoro", lambda: self.dm.trigger_menu_flavor("pomodoro_cancel"))

    def _custom_pomodoro(self):
        focus, ok = QInputDialog.getInt(self, "番茄钟", "专注时长(分钟):", 25, 1, 180)
        if not ok:
            return
        rest, ok = QInputDialog.getInt(self, "番茄钟", "休息时长(分钟):", 5, 1, 60)
        if not ok:
            return
        if not self.arbiter.begin_feature("pomodoro"):
            return
        self.stats.record_interaction()
        self.config["pomodoro"]["focus_minutes"] = focus
        self.config["pomodoro"]["break_minutes"] = rest
        self.save_config(self.config)
        self.pomodoro.start(focus, rest)
        self.arbiter.finish_feature("pomodoro")

    # ----------------------------------------------------------------- stats
    def _collect_stats_metrics(self):
        s = self.stats.data
        total_eggs = self.dm.total_egg_count()
        seen, total, egg_pct = self.stats.rare_progress(total_eggs)
        return {
            "days": self.dm.days_since_install(),
            "hours": self.stats.companion_hours(),
            "clicks": self.config.get("total_clicks", 0),
            "interactions": s.get("interaction_count", 0),
            "dialogues": s.get("dialogue_trigger_count", 0),
            "pomodoro_count": s.get("pomodoro_completed", 0),
            "water_rate": self.stats.response_rate("water"),
            "meal_rate": self.stats.response_rate("meal"),
            "water_dismissed": self.stats.dismissed_count("water"),
            "meal_dismissed": self.stats.dismissed_count("meal"),
            "egg_seen": seen,
            "egg_total": total,
            "egg_pct": egg_pct,
            "latest_time": s.get("latest_active_time"),
            "latest_score": s.get("latest_active_score"),
            "flirt_count": s.get("flirt_count", 0),
            "flirt_roast_threshold": self.config.get("flirt", {}).get("roast_threshold_count", 20),
        }

    @_guarded_menu_action("记录")
    def _open_stats_panel(self):
        import roast
        from stats_panel import StatsPanel

        self.stats.record_interaction()
        self._close_active_popup()

        metrics = self._collect_stats_metrics()

        def _speak_roast():
            segments = []
            open_text, open_face = roast.open_line()
            if open_text:
                segments.append({"text": open_text, "face": open_face})
            roast_segments = roast.pick_roast_line(metrics)
            if roast_segments:
                segments.extend(roast_segments)
            if segments:
                self.dm.speak("stats_panel", segments)

        # Opening the panel always works (it's a view, not a persistent
        # toggle) -- only the roast line is debounced/escalated ("记录").
        self.arbiter.finish_feature("stats", _speak_roast)

        panel = StatsPanel(
            metrics,
            audio=self.audio,
            always_on_top=self._always_on_top,
            ui_scale=self._ui_scale(),
            min_font_px=self.config.get("ui_min_font_size", 10),
        )
        panel.closed.connect(self._on_stats_panel_closed)
        self._place_popup(panel, ref_h=self._main_menu_height())
        self._active_popup = panel
        panel.show()
        panel.setFocus()

    def _on_stats_panel_closed(self):
        import roast

        close_text, close_face = roast.close_line()
        if close_text:
            self.dm.speak("stats_panel_close", [{"text": close_text, "face": close_face}])

    # ---------------------------------------------------------------- quit
    def request_quit(self):
        self._begin_quit(self.dm.pick_exit_segments)

    def request_quit_for_update(self):
        """Same exit as request_quit, but the goodbye line comes from the
        update_restarting pool (the new version is about to take over)."""
        self._begin_quit(lambda: self.dm.pick_update_segments("update_restarting"))

    def _begin_quit(self, pick_segments):
        if self._pending_quit:
            return
        ctl = getattr(self, "_update_ctl", None)
        if ctl is not None:
            ctl.cancel()  # a download still running is abandoned; an updater already launched is not
        # Exit lock (reg 1): from this instant on, the right-click menu
        # won't open (contextMenuEvent), idle/AFK ambient chatter can't
        # speak (_trigger_afk_unless_awaiting / dm busy-lock via
        # _on_show_requested), and any reminder/chase round in progress is
        # voided immediately and silently -- only the goodbye line +
        # burrow animation play from here on, and clicking Flowey during
        # either only gets the escalating "被打断" reaction (see
        # _handle_exit_interrupt), never a real interactive dialogue.
        self._pending_quit = True
        self._exit_interrupted = False
        self._resume_exit_animation_after_interrupt = False
        self._close_active_popup()
        _close_chat(self)
        _close_api_panel(self)
        self.reminders.chase.lock_for_exit()
        QTimer.singleShot(EXIT_FAILSAFE_MS, self._quit_now)

        segments = pick_segments()
        if not segments:
            self._start_burrow_animation(self._quit_now)
            return
        self.state = State.TALKING
        self._current_category = "exit"
        self.setVisible(True)
        self._position_dialogue_box()
        self.dialogue_box.start(segments, self._on_segment_face)

    def is_exiting(self):
        """Public hook for DialogueArbiter: True from the instant 退出 is
        clicked until the process actually terminates."""
        return self._pending_quit

    def is_reminder_popup_active(self):
        """Public hook for DialogueArbiter: True only while an actual
        reminder/chase popup is on screen (not merely CHASING/waiting
        between stages) -- that's the one thing that outranks every
        arbiter-driven reaction (reg 10)."""
        return self._current_category in CHASE_POPUP_BUTTONS and self.dialogue_box.isVisible()

    def force_end_bubble(self):
        """Public hook for DialogueArbiter: immediately silences whatever
        bubble is currently on screen, with no follow-up line of its own --
        called right before the arbiter shows its own (debounced) feature
        reaction, so a feature click always cuts Flowey off rather than
        waiting for her to finish (reg 3). Returns True iff Flowey was
        actually mid-speech, which the arbiter treats as a real
        "talked over" interruption (reg 5/6)."""
        was_talking = self.dialogue_box.isVisible()
        if not was_talking:
            return False
        self.dialogue_box.stop_immediately()
        self.dm.cut_off()
        if self.state == State.TALKING:
            self._enter_idle()
        return True

    def _handle_exit_interrupt(self):
        """Clicking Flowey while the exit goodbye line or burrow animation
        is playing: per the confirmed design, this plays exactly ONE
        randomly-picked "被打断" line for the whole exit sequence, no
        matter how many times (or how many separate moments) Flowey gets
        clicked while quitting -- not one line per click. The first click
        (whenever it happens) triggers it; every click after that,
        including more clicks while that one line is still playing, is a
        pure no-op (does not cut it short, does not trigger another one).
        Once it finishes, continue straight to the burrow animation --
        never resume the original farewell text."""
        if self._exit_interrupted:
            return
        self._exit_interrupted = True
        was_animating = self._exit_timer.isActive()
        if was_animating:
            self._exit_timer.stop()
        self.dialogue_box.stop_immediately()
        segments = self.arbiter.pick_exit_interrupt_segments()
        if not segments:
            # No lines available (pool missing) -- just resume/continue
            # silently rather than leaving the animation stuck paused.
            if was_animating:
                self._exit_timer.start(EXIT_FRAME_MS)
            return
        self._resume_exit_animation_after_interrupt = was_animating
        self._current_category = "exit_interrupt"
        self.dialogue_box.start(segments, self._on_segment_face)

    def _start_burrow_animation(self, on_done):
        """Shrink back into the ground: the neutral-face grow animation
        played in reverse. Shared by quit and hide so both 'leave' the same
        way; on_done fires once the last frame has shown."""
        self.state = State.EXITING
        self.dialogue_box.stop_immediately()
        self.pomodoro_display.hide_display()
        self._exit_frames = self.sprites.quit_frames or list(reversed(self.sprites.appear_neutral_frames))
        self._exit_idx = 0
        self._burrow_on_done = on_done
        self.setVisible(True)
        self._set_base_pixmap(self._exit_frames[0])
        self._exit_timer.start(EXIT_FRAME_MS)

    def _advance_exit(self):
        self._exit_idx += 1
        if self._exit_idx >= len(self._exit_frames):
            self._exit_timer.stop()
            callback = self._burrow_on_done
            self._burrow_on_done = None
            if callback:
                callback()
            return
        self._set_base_pixmap(self._exit_frames[self._exit_idx])

    def _finish_hide(self):
        self.setVisible(False)

    def _quit_now(self):
        self._pending_quit = True
        self._topmost_keeper.stop()
        QApplication.instance().quit()
