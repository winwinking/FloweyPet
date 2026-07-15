# -*- coding: utf-8 -*-
"""Central path resolution. Works both from source (python main.py) and
from a PyInstaller --onedir build, where dialogue.json / config.json sit
as plain sibling files next to the executable so the user can edit them
without touching any code.
"""
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent

ASSETS_DIR = APP_DIR / "assets"
ICON_PATH = ASSETS_DIR / "icon.ico"
FACES_DIR = ASSETS_DIR / "faces"
APPEAR_DIR = ASSETS_DIR / "appear"
APPEAR_NEUTRAL_DIR = ASSETS_DIR / "appear_neutral"
IDLE_DIR = ASSETS_DIR / "idle"
SOUNDS_DIR = ASSETS_DIR / "sounds"
SFX_DIR = ASSETS_DIR / "sfx"  # Undertale-menu move/select blips (see undertale_ui.py)
FONTS_DIR = ASSETS_DIR / "fonts"
FONT_DIR = ASSETS_DIR / "font"  # DSEG7 digit font lives here (singular, per spec)
# Optional dedicated exit animation; if empty/missing, PetWindow falls back
# to playing APPEAR_DIR's frames in reverse (the "burrow back into the
# ground" effect), which is the actual "spawn" animation already in use.
QUIT_ANIM_DIR = ASSETS_DIR / "anim" / "quit"

DIALOGUE_PATH = APP_DIR / "dialogue.json"
DIALOGUE_INFO_PATH = APP_DIR / "dialogues_info.json"
DIALOGUE_SYSTEM_PATH = APP_DIR / "dialogues_system.json"  # app/system-lifecycle lines, e.g. duplicate_launch
DIALOGUE_FLIRT_PATH = APP_DIR / "dialogues_flirt.json"
DIALOGUE_CHASE_PATH = APP_DIR / "dialogues_chase.json"  # reminder-chase (追讨) line pools, see reminder_chase.py
DIALOGUE_SPAM_PATH = APP_DIR / "dialogues_spam.json"  # anti-spam escalation line pools, see dialogue_arbiter.py
DIALOGUE_GAME_PATH = APP_DIR / "dialogues_game.json"  # UNDERTALE/DELTARUNE launch-detect egg lines, see game_detector.py
STATS_ROAST_PATH = APP_DIR / "stats_roast.json"
CONFIG_PATH = APP_DIR / "config.json"
STATS_PATH = APP_DIR / "stats.json"
FACES_MANIFEST_PATH = ASSETS_DIR / "faces_manifest.json"
COMPOSE_META_PATH = ASSETS_DIR / "compose_meta.json"
STEM_BASE_PATH = ASSETS_DIR / "stem_base.png"
