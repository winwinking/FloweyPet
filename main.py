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
from compositor import SpriteBank
from dialogue_manager import DialogueManager
from pomodoro import Pomodoro
from reminders import Reminders
from stats import Stats
from pet_window import PetWindow


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    config = load_config()
    config["_session_start"] = time.time()
    save_config(config)

    audio = AudioManager(
        volume=config.get("volume", 0.8),
        muted=config.get("muted", False),
        menu_sfx_enabled=config.get("menu_sfx_enabled", True),
    )
    sprites = SpriteBank(config.get("pet_scale", 1.0))
    stats = Stats(config)
    dm = DialogueManager(config, save_config, stats)
    pomodoro = Pomodoro(config, save_config, dm, stats)
    reminders = Reminders(config, save_config, dm, stats)
    app.aboutToQuit.connect(stats.save)

    window = PetWindow(config, save_config, dm, audio, sprites, pomodoro, reminders, stats)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
