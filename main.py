# -*- coding: utf-8 -*-
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtWidgets import QApplication

from error_log import install_global_hook, log_exception

install_global_hook()

from config import load_config, read_autostart_command, read_shadow_clicks, save_config
from audio import AudioManager
import app_log
import audio_log
from compositor import SpriteBank
from dialogue_manager import DialogueManager
from paths import APP_DIR, IS_DEV, USER_DATA_DIR
from pomodoro import Pomodoro
from reminders import Reminders
from single_instance import SingleInstanceGuard
from stats import Stats
from user_data import autostart_exe_dir, migrate_legacy_data, pick_legacy_source
from pet_window import PetWindow


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    config = load_config()

    # Single-instance guard: must run before any window is created. If a
    # live instance already answers, this process notifies it and exits
    # without ever constructing PetWindow -- see single_instance.py for why
    # the ordering (connect-probe first, stale-lock cleanup only after it
    # fails) makes this safe against a crashed previous process's leftover
    # lock.
    guard = SingleInstanceGuard(
        config.get("single_instance_key", "FloweyPet_SingleInstance_Lock"),
        config.get("single_instance_connect_timeout_ms", 500),
    )
    if not guard.try_claim():
        sys.exit(0)

    # Move config.json / stats.json from beside the exe into the user data
    # dir. Must run only after the single-instance check above passed, so a
    # second launch never rewrites data under a running first instance.
    try:
        # Packaged build only: the old data may sit beside a different exe
        # (e.g. the one the autostart entry points at), not beside this one.
        fallback_dirs = [] if IS_DEV else [autostart_exe_dir(read_autostart_command())]
        legacy_config, legacy_stats, source_dir = pick_legacy_source(APP_DIR, fallback_dirs)
        result = migrate_legacy_data(USER_DATA_DIR, legacy_config, legacy_stats, read_shadow_clicks)
        app_log.log_event(f"数据迁移：{result}（数据目录 {USER_DATA_DIR}，旧数据来源 {source_dir}）")
    except Exception:
        log_exception("migrate_legacy_data")
    config = load_config()

    config["_session_start"] = time.time()
    save_config(config)

    audio = AudioManager(
        volume=config.get("volume", 0.8),
        muted=config.get("muted", False),
        menu_sfx_enabled=config.get("menu_sfx_enabled", True),
        audio_debug=config.get("audio_debug", False),
        watchdog_threshold=config.get("audio_watchdog_threshold", 5),
        rotation_seconds=config.get("audio_rotation_seconds", 90),
    )
    if config.get("audio_stress_test_on_startup", False):
        def _report_stress_result(stats):
            audio_log.log_event(f"启动自测报告：{stats}")

        audio.run_stress_test(iterations=200, on_done=_report_stress_result)
    sprites = SpriteBank(config.get("pet_scale", 1.0))
    stats = Stats(config)
    dm = DialogueManager(config, save_config, stats)
    pomodoro = Pomodoro(config, save_config, dm, stats)
    reminders = Reminders(config, save_config, dm, stats)
    app.aboutToQuit.connect(stats.save)

    window = PetWindow(config, save_config, dm, audio, sprites, pomodoro, reminders, stats)
    guard.on_activate_requested = window.on_duplicate_launch_activation
    app.aboutToQuit.connect(guard.close)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
