# -*- coding: utf-8 -*-
"""Central path resolution. Works both from source (python main.py) and
from a PyInstaller --onedir build, where dialogue.json and the other
dialogue*.json files sit as plain sibling files next to the executable so
the user can edit them without touching any code. config.json / stats.json
are per-user data and live in USER_DATA_DIR instead (see below); the old
location next to the executable is only read by the one-time migration in
user_data.py. This module only defines paths -- it creates no directories.
"""
import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent.parent

IS_DEV = not getattr(sys, "frozen", False)

# Where per-user data (config.json, stats.json) lives. Order:
#   1. FLOWEYPET_DATA_DIR (tests / manual override)
#   2. running from source -> <repo>/dev_data (git-ignored)
#   3. packaged build -> %APPDATA%\FloweyPet
#   4. fallback -> ~/.floweypet
if os.environ.get("FLOWEYPET_DATA_DIR"):
    USER_DATA_DIR = Path(os.environ["FLOWEYPET_DATA_DIR"])
elif IS_DEV:
    USER_DATA_DIR = APP_DIR / "dev_data"
elif os.environ.get("APPDATA"):
    USER_DATA_DIR = Path(os.environ["APPDATA"]) / "FloweyPet"
else:
    USER_DATA_DIR = Path.home() / ".floweypet"

LOG_DIR = USER_DATA_DIR / "logs"

ASSETS_DIR = APP_DIR / "assets"
ICON_PATH = ASSETS_DIR / "icon.ico"
FACES_DIR = ASSETS_DIR / "faces"
APPEAR_DIR = ASSETS_DIR / "appear"
APPEAR_NEUTRAL_DIR = ASSETS_DIR / "appear_neutral"
IDLE_DIR = ASSETS_DIR / "idle"
IDLE_STILL_DIR = ASSETS_DIR / "idle_still"  # single still frame shown when the idle animation is switched off
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
DIALOGUE_ANTICHEAT_PATH = APP_DIR / "dialogues_anticheat.json"  # auto-clicker + save-tamper line pools, see anticheat.py
STATS_ROAST_PATH = APP_DIR / "stats_roast.json"
CONFIG_PATH = USER_DATA_DIR / "config.json"
STATS_PATH = USER_DATA_DIR / "stats.json"
# Pre-migration location (next to the executable); read-only, see user_data.py
LEGACY_CONFIG_PATH = APP_DIR / "config.json"
LEGACY_STATS_PATH = APP_DIR / "stats.json"
FACES_MANIFEST_PATH = ASSETS_DIR / "faces_manifest.json"
COMPOSE_META_PATH = ASSETS_DIR / "compose_meta.json"
STEM_BASE_PATH = ASSETS_DIR / "stem_base.png"
