# -*- coding: utf-8 -*-
import time
from functools import partial

from PyQt6.QtCore import QUrl
from PyQt6.QtMultimedia import QSoundEffect

from error_log import log_exception, log_message
from paths import SFX_DIR, SOUNDS_DIR

_SKIP_BLIP_CHARS = set(
    "。，、；：！？…—·,.;:!?~ \n\t\"'“”‘’()（）*"
)

# Menu blips have their own debounce so rapid-fire hover/scroll events don't
# stack sound on top of sound.
MENU_SFX_DEBOUNCE_S = 0.05


class AudioManager:
    def __init__(self, volume: float = 0.8, muted: bool = False, menu_sfx_enabled: bool = True):
        self.blip = self._make_effect(SOUNDS_DIR / "blip.wav", "blip")
        self.laugh = self._make_effect(SOUNDS_DIR / "laugh.wav", "laugh")
        self.menu_move = self._load_optional(SFX_DIR / "menu_move.wav", "menu_move")
        self.menu_select = self._load_optional(SFX_DIR / "menu_select.wav", "menu_select")
        self._last_menu_move = 0.0
        self._last_menu_select = 0.0
        self.volume = volume
        self.muted = muted
        self.menu_sfx_enabled = menu_sfx_enabled
        self._apply_volume()

    def _make_effect(self, path, name):
        """Every sound purpose (typing blip / laugh / menu move / menu
        select) gets exactly one QSoundEffect created here and reused for
        the app's entire lifetime -- never recreated per-play, per-char, or
        shared across purposes. QSoundEffect's Windows backend can still
        wedge a given instance into Status.Error after enough rapid-fire
        replays (the typing blip fires every ~38ms for hours at a time);
        _on_status_changed below detects that on *this* instance and
        rebuilds just it, so playback recovers without restarting the app.
        """
        effect = QSoundEffect()
        effect.setSource(QUrl.fromLocalFile(str(path)))
        effect.statusChanged.connect(partial(self._on_status_changed, name, path))
        return effect

    def _load_optional(self, path, name):
        """Menu move/select blips are optional: if the wav isn't at this
        path yet, the corresponding play_* call is just a no-op until one is
        dropped in."""
        if not path.exists():
            return None
        return self._make_effect(path, name)

    def _on_status_changed(self, name, path):
        effect = getattr(self, name, None)
        if effect is None or effect.status() != QSoundEffect.Status.Error:
            return
        log_message(f"audio: '{name}' sound effect entered Error status, recreating from {path}")
        try:
            setattr(self, name, self._make_effect(path, name))
            self._apply_volume()
        except Exception:
            log_exception(f"audio: failed to recreate '{name}' sound effect after Error status")

    def _apply_volume(self):
        v = 0.0 if self.muted else self.volume
        self.blip.setVolume(v)
        self.laugh.setVolume(v)
        # Menu sfx follow the volume level but are gated by menu_sfx_enabled,
        # not by "muted" -- the two systems are independent by design.
        if self.menu_move:
            self.menu_move.setVolume(self.volume)
        if self.menu_select:
            self.menu_select.setVolume(self.volume)

    def set_volume(self, volume: float):
        self.volume = max(0.0, min(1.0, volume))
        self._apply_volume()

    def set_muted(self, muted: bool):
        self.muted = muted
        self._apply_volume()

    def set_menu_sfx_enabled(self, enabled: bool):
        self.menu_sfx_enabled = enabled

    def _safe_play(self, effect, name):
        """play() itself doesn't raise on a Qt-side failure (QSoundEffect
        just sets Status.Error and stays silent), but this guards against
        anything on the Python side -- e.g. a bad effect reference -- so a
        sfx call can never take the rest of dialogue/menu handling down
        with it. Any real Qt-side error is caught separately by
        _on_status_changed via the statusChanged signal."""
        try:
            effect.play()
        except Exception:
            log_exception(f"audio: play() raised for '{name}'")

    def play_blip_for_char(self, ch: str):
        if self.muted or ch in _SKIP_BLIP_CHARS:
            return
        self._safe_play(self.blip, "blip")

    def play_laugh(self):
        if not self.muted:
            self._safe_play(self.laugh, "laugh")

    def play_menu_move(self):
        if not self.menu_sfx_enabled or not self.menu_move:
            return
        now = time.monotonic()
        if now - self._last_menu_move < MENU_SFX_DEBOUNCE_S:
            return
        self._last_menu_move = now
        self._safe_play(self.menu_move, "menu_move")

    def play_menu_select(self):
        if not self.menu_sfx_enabled or not self.menu_select:
            return
        now = time.monotonic()
        if now - self._last_menu_select < MENU_SFX_DEBOUNCE_S:
            return
        self._last_menu_select = now
        self._safe_play(self.menu_select, "menu_select")
