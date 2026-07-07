# -*- coding: utf-8 -*-
"""The pet itself: a frameless, always-on-top, transparent window that can
be dragged around, plus its tray icon and right-click menu. Owns the
appear/idle/talk animation state machine and is the single writer of the
on-screen pixmap, so nothing can race the dialogue system for control of
Flowey's face.
"""
import functools
import random

from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QCursor, QIcon, QPainter
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

from dialogue_box import DialogueBox
from error_log import log_exception
from paths import ICON_PATH
from pomodoro_display import PomodoroDisplay
from undertale_ui import VOLUME_TIERS, ClickableLabel

CLICK_MOVE_THRESHOLD = 4
IDLE_FRAME_MS = 220
AFK_THRESHOLDS_MIN = (5, 10, 30, 60)
REQUIRE_CONFIRM_CATEGORIES = {"reminder_water", "reminder_meal", "reminder_sleep"}
REQUIRE_CONFIRM_NAG_PREFIX = "reminder_unconfirmed_"
EXIT_FRAME_MS = 125
EXIT_FAILSAFE_MS = 15000


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


class State:
    APPEARING = "appearing"
    IDLE = "idle"
    TALKING = "talking"
    EXITING = "exiting"


class PetWindow(QWidget):
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
        self._pending_hide = False
        self._burrow_on_done = None
        self._pomodoro_active = False
        self._current_category = None
        self._active_popup = None

        self.dialogue_box = DialogueBox(
            self.pet_display_width,
            config.get("font_scale", 1.75),
            audio,
            self._always_on_top,
            confirm_hold_ms=reminders.confirm_hold_ms(),
            test_mode=reminders.test_mode,
        )
        self.dialogue_box.finished.connect(self._on_dialogue_finished)
        self.dm.show_requested.connect(self._on_show_requested)

        self.pomodoro_display = PomodoroDisplay(self.pet_display_width, self._always_on_top)

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

    def _toggle_always_on_top(self, checked):
        self.stats.record_interaction()
        self._always_on_top = checked
        self.config["always_on_top"] = checked
        self.save_config(self.config)
        was_visible = self.isVisible()
        self.setWindowFlags(self._window_flags())
        if was_visible:
            self.show()
        self.dialogue_box.set_always_on_top(checked)
        self.pomodoro_display.set_always_on_top(checked)
        self.dm.trigger_menu_flavor("pin_on" if checked else "pin_off")

    def _resolve_start_position(self, w, h):
        screen = QApplication.primaryScreen().availableGeometry()
        pos = self.config.get("position")
        if pos is None:
            x = screen.x() + screen.width() - w - 40
            y = screen.y() + screen.height() - h - 60
        else:
            x, y = pos["x"], pos["y"]
        x = max(screen.x(), min(x, screen.x() + screen.width() - w))
        y = max(screen.y(), min(y, screen.y() + screen.height() - h))
        return x, y

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
            return
        self._set_base_pixmap(self._appear_frames[self._appear_idx])

    # --------------------------------------------------------------- idle
    def _enter_idle(self):
        self.state = State.IDLE
        self._idle_idx = 0
        self._set_base_pixmap(self.sprites.idle_frames[0])
        self._idle_timer.start(IDLE_FRAME_MS)
        self._schedule_idle_chat()
        self._reschedule_afk_timers()

    def _advance_idle_frame(self):
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
            and not self.reminders.is_awaiting_response()
        ):
            self.dm.trigger_idle_random()
        self._schedule_idle_chat()

    def _trigger_afk_unless_awaiting(self, level):
        # A reminder still waiting on a response takes priority over ambient
        # AFK chatter -- don't talk over it.
        if not self.reminders.is_awaiting_response():
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
        self._tray.setToolTip(f"Flowey - {status}" if status else "Flowey")

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
        self.dialogue_box.follow(self.x(), anchor_y, self.pet_display_width)

    # ------------------------------------------------------------- dialogue
    def _on_show_requested(self, segments, category):
        self.state = State.TALKING
        self._current_category = category
        self._position_dialogue_box()
        require_confirm = category in REQUIRE_CONFIRM_CATEGORIES or category.startswith(REQUIRE_CONFIRM_NAG_PREFIX)
        self.dialogue_box.start(segments, self._on_segment_face, require_confirm=require_confirm)

    def _on_segment_face(self, face_key):
        self._set_base_pixmap(self.sprites.get_face(face_key, 0))

    def _on_dialogue_finished(self):
        cat = self._current_category
        if cat in REQUIRE_CONFIRM_CATEGORIES or (cat and cat.startswith(REQUIRE_CONFIRM_NAG_PREFIX)):
            outcome = self.dialogue_box.response_outcome
            if outcome is not None:
                self.reminders.on_reminder_confirmed(perfunctory=(outcome == "acknowledged"))
        if self._pending_quit:
            self._start_burrow_animation(self._quit_now)
            return
        self.dm.on_finished()
        if self._pending_hide and self._current_category == "hide":
            self._pending_hide = False
            self._start_burrow_animation(self._finish_hide)
            return
        self._enter_idle()

    # ---------------------------------------------------------------- mouse
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
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

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            return
        self._dragging = False
        self.update()
        self.config["position"] = {"x": self.x(), "y": self.y()}
        self.save_config(self.config)
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
        self.dm.trigger_click()
        self.stats.record_interaction()

    # ----------------------------------------------------------------- menu
    def _build_menu(self):
        menu = QMenu()

        visible_action = menu.addAction("隐藏" if self.isVisible() else "显示 Flowey")
        visible_action.triggered.connect(self._toggle_visible)

        pin_action = menu.addAction("置顶")
        pin_action.setCheckable(True)
        pin_action.setChecked(self._always_on_top)
        pin_action.triggered.connect(self._toggle_always_on_top)

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
        self._close_active_popup()
        # Deferred to the next event-loop tick: showing a Qt.Popup
        # synchronously inside the mouse event that spawned it can make the
        # popup mistake the tail end of that same right-click for an
        # outside click and immediately close itself.
        QTimer.singleShot(0, self._show_context_menu)

    def _show_context_menu(self):
        from pet_menu import PetContextMenu
        from undertale_ui import position_avoid_pet_and_bubble

        menu = PetContextMenu(self)
        position_avoid_pet_and_bubble(menu, self.x(), self.y(), self.width(), self.height(), self.dialogue_box)
        self._active_popup = menu
        menu.show()
        menu.setFocus()

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
        self.stats.record_interaction()
        self._close_active_popup()
        if self.isVisible():
            self._hide_with_flavor()
        else:
            self._start_emerge(lambda: self.dm.trigger_menu_flavor("show"))

    def _hide_with_flavor(self):
        self._pending_hide = True
        self.dm.trigger_menu_flavor("hide")

    def _toggle_mute(self, checked):
        self.stats.record_interaction()
        self.config["muted"] = checked
        self.save_config(self.config)
        self.audio.set_muted(checked)
        self.dm.trigger_menu_flavor("mute_on" if checked else "mute_off")

    def _toggle_menu_sfx(self, checked):
        self.stats.record_interaction()
        self.config["menu_sfx_enabled"] = checked
        self.save_config(self.config)
        self.audio.set_menu_sfx_enabled(checked)

    def _set_volume(self, pct):
        self.stats.record_interaction()
        old_pct = round(self.config.get("volume", 0.8) * 100)
        self.config["volume"] = pct / 100
        self.save_config(self.config)
        self.audio.set_volume(pct / 100)
        if pct > old_pct:
            self.dm.trigger_menu_flavor("volume_up")
        elif pct < old_pct:
            self.dm.trigger_menu_flavor("volume_down")

    @_guarded_menu_action("音量面板")
    def _open_volume_panel(self):
        from undertale_ui import position_avoid_pet_and_bubble
        from volume_panel import VolumePanel

        self.stats.record_interaction()
        self._close_active_popup()
        panel = VolumePanel(
            lambda: round(self.config.get("volume", 0.8) * 100),
            self._set_volume,
            audio=self.audio,
            always_on_top=self._always_on_top,
        )
        position_avoid_pet_and_bubble(panel, self.x(), self.y(), self.width(), self.height(), self.dialogue_box)
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

        self.stats.record_interaction()
        self.config["autostart"] = checked
        self.save_config(self.config)
        set_autostart(checked)
        self.dm.trigger_menu_flavor("autostart_on" if checked else "autostart_off")

    @_guarded_menu_action("提醒设置")
    def _open_reminder_settings(self):
        from reminder_settings import ReminderPanel
        from undertale_ui import position_avoid_pet_and_bubble

        self.stats.record_interaction()
        self.dm.trigger_menu_flavor("reminder_settings")
        self._close_active_popup()
        panel = ReminderPanel(self.reminders, audio=self.audio, always_on_top=self._always_on_top)
        position_avoid_pet_and_bubble(panel, self.x(), self.y(), self.width(), self.height(), self.dialogue_box)
        self._active_popup = panel
        panel.show()
        panel.setFocus()

    @_guarded_menu_action("查看信息")
    def _view_info(self):
        self.stats.record_interaction()
        self.dm.trigger_view_info()

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
        self.stats.record_interaction()
        self.pomodoro.start()

    def _cancel_pomodoro_from_menu(self):
        self.stats.record_interaction()
        self.dm.trigger_menu_flavor("pomodoro_cancel")
        self.pomodoro.cancel()

    def _custom_pomodoro(self):
        focus, ok = QInputDialog.getInt(self, "番茄钟", "专注时长(分钟):", 25, 1, 180)
        if not ok:
            return
        rest, ok = QInputDialog.getInt(self, "番茄钟", "休息时长(分钟):", 5, 1, 60)
        if not ok:
            return
        self.stats.record_interaction()
        self.config["pomodoro"]["focus_minutes"] = focus
        self.config["pomodoro"]["break_minutes"] = rest
        self.save_config(self.config)
        self.pomodoro.start(focus, rest)

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
            "pomodoro_count": s.get("pomodoro_completed", 0),
            "water_rate": self.stats.response_rate("water"),
            "meal_rate": self.stats.response_rate("meal"),
            "egg_seen": seen,
            "egg_total": total,
            "egg_pct": egg_pct,
            "latest_time": s.get("latest_active_time"),
            "latest_score": s.get("latest_active_score"),
        }

    @_guarded_menu_action("记录")
    def _open_stats_panel(self):
        import roast
        from stats_panel import StatsPanel
        from undertale_ui import position_avoid_pet_and_bubble

        self.stats.record_interaction()
        self._close_active_popup()

        metrics = self._collect_stats_metrics()
        segments = []
        open_text, open_face = roast.open_line()
        if open_text:
            segments.append({"text": open_text, "face": open_face})
        roast_text, roast_face = roast.pick_roast_line(metrics)
        if roast_text:
            segments.append({"text": roast_text, "face": roast_face})
        if segments:
            self.dm.speak("stats_panel", segments)

        panel = StatsPanel(metrics, audio=self.audio, always_on_top=self._always_on_top)
        panel.closed.connect(self._on_stats_panel_closed)
        position_avoid_pet_and_bubble(panel, self.x(), self.y(), self.width(), self.height(), self.dialogue_box)
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
        if self._pending_quit:
            return
        self._pending_quit = True
        QTimer.singleShot(EXIT_FAILSAFE_MS, self._quit_now)

        segments = self.dm.pick_exit_segments()
        if not segments:
            self._start_burrow_animation(self._quit_now)
            return
        self.state = State.TALKING
        self._current_category = "exit"
        self.setVisible(True)
        self._position_dialogue_box()
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
        QApplication.instance().quit()
