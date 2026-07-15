# -*- coding: utf-8 -*-
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from PyQt6.QtWidgets import QApplication

from error_log import install_global_hook

install_global_hook()

from config import load_config, save_config
from audio import AudioManager
import audio_log
from compositor import SpriteBank
from dialogue_manager import DialogueManager
from pomodoro import Pomodoro
from reminders import Reminders
from single_instance import SingleInstanceGuard
from stats import Stats
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
