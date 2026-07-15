# -*- coding: utf-8 -*-
"""Unified audio entry point. Every sound in the app -- typing blip,
character laugh, menu move/select -- must go through this AudioManager;
nothing else in the codebase is allowed to play audio directly.

ENGINE HISTORY (do not re-litigate without reading this):

1. QSoundEffect, new instance per character  -> died after sustained use.
2. QSoundEffect, one persistent instance per purpose + reactive rebuild on
   Status.Error  -> still died: on this machine the effect goes silently
   dead (isPlaying() stuck True, no Error status, play() succeeds, zero
   audible output) within minutes of normal use.
3. + device-change listener, watchdog, unconditional periodic rotation
   (rebuild every 90s)  -> still died: live diagnosis on 2026-07-11 showed
   a wedged effect while the process's WASAPI session was healthy
   (volume 1.0, unmuted) and other processes played fine. Rebuilding a
   QSoundEffect does not help because all instances share QtMultimedia's
   process-global audio engine, and it is that engine that wedges; there
   is no Qt API to restart it short of restarting the process.
4. pygame.mixer swap (another session, 2026-07-11) -> reported the same
   silent failure; reverted the same day.
5. winmm PlaySound (SND_MEMORY|SND_ASYNC via ctypes) -> audible and immune
   to the Qt wedge, but WRONG SOUND: PlaySound re-opens the audio device
   on every call and purges the previous sound first. At the typing
   cadence (~26 blips/sec) the driver's stream-start ramp swallowed each
   blip's attack -- the user immediately reported the voice as "slow and
   hoarse" compared to the authentic snd_floweytalk1.wav. Reverted within
   a day. Lesson: the engine must keep its stream OPEN across plays, like
   QSoundEffect did -- that's what made the blip crisp.

CURRENT ENGINE: winmm waveOut via ctypes. One persistent waveOut handle
per purpose, opened once; each play is waveOutReset + waveOutWrite on the
already-open stream (microseconds, no device reopen, attack preserved --
sonically equivalent to the Qt engine's persistent mixed stream). Unlike
QSoundEffect, every waveOut call returns an honest MMRESULT error code,
so a broken handle is *detectable* and is rebuilt (close + reopen) with
one retry -- reactive recovery actually works here, unlike on Qt where
the failure was silent by design. winmm was proven to render on this
machine while the Qt engine was wedged in the same live process
(endpoint peak meter 0.26 vs 0.0 baseline, 2026-07-11). On Win10+ winmm
streams also survive default-device changes via automatic rerouting.

Volume: no per-play volume in this design either -- volume is applied by
pre-rendering scaled copies of each clip's frames via audioop.mul when
the volume changes, then swapping the buffers into the handles.
(waveOutSetVolume was deliberately NOT used: on some drivers it adjusts
the whole session, which would break the "menu sfx stay audible while
muted" rule.)

The AudioManager's public API is unchanged from the Qt version, so no
caller needs edits. The rotation/watchdog config keys are accepted and
ignored (nothing left to rotate; failures are loud now).
"""
import audioop
import ctypes
import time
import wave

import audio_log
from error_log import log_exception, log_message
from paths import SFX_DIR, SOUNDS_DIR

_SKIP_BLIP_CHARS = set(
    "。，、；：！？…—·,.;:!?~ \n\t\"'“”‘’()（）*"
)

# Menu blips have their own debounce so rapid-fire hover/scroll events don't
# stack sound on top of sound.
MENU_SFX_DEBOUNCE_S = 0.05

# How many consecutive play failures before we tell error.log the audio
# system itself looks broken (not just one bad call).
DEFAULT_WATCHDOG_THRESHOLD = 5

# ------------------------------------------------------------ winmm plumbing
_WAVE_MAPPER = 0xFFFFFFFF
_WAVE_FORMAT_PCM = 1


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", ctypes.c_uint16),
        ("nChannels", ctypes.c_uint16),
        ("nSamplesPerSec", ctypes.c_uint32),
        ("nAvgBytesPerSec", ctypes.c_uint32),
        ("nBlockAlign", ctypes.c_uint16),
        ("wBitsPerSample", ctypes.c_uint16),
        ("cbSize", ctypes.c_uint16),
    ]


class _WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.c_void_p),
        ("dwBufferLength", ctypes.c_uint32),
        ("dwBytesRecorded", ctypes.c_uint32),
        ("dwUser", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint32),
        ("dwLoops", ctypes.c_uint32),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_void_p),
    ]


_winmm = ctypes.WinDLL("winmm")
_winmm.waveOutOpen.restype = ctypes.c_uint
_winmm.waveOutPrepareHeader.restype = ctypes.c_uint
_winmm.waveOutUnprepareHeader.restype = ctypes.c_uint
_winmm.waveOutWrite.restype = ctypes.c_uint
_winmm.waveOutReset.restype = ctypes.c_uint
_winmm.waveOutClose.restype = ctypes.c_uint


class _WaveOutVoice:
    """One persistent, always-open waveOut stream for one clip. play() is
    restart-from-the-top, matching QSoundEffect.play() semantics. Any winmm
    error raises RuntimeError with the MMRESULT code so the caller can
    rebuild this voice."""

    def __init__(self, wav_params, frames):
        self._params = wav_params
        block_align = wav_params.nchannels * wav_params.sampwidth
        self._wfx = _WAVEFORMATEX(
            _WAVE_FORMAT_PCM,
            wav_params.nchannels,
            wav_params.framerate,
            wav_params.framerate * block_align,
            block_align,
            wav_params.sampwidth * 8,
            0,
        )
        self._h = ctypes.c_void_p()
        rc = _winmm.waveOutOpen(ctypes.byref(self._h), _WAVE_MAPPER, ctypes.byref(self._wfx), None, None, 0)
        if rc != 0:
            raise RuntimeError(f"waveOutOpen failed, MMRESULT={rc}")
        self._buf = None
        self._hdr = None
        try:
            self.set_frames(frames)
        except Exception:
            _winmm.waveOutClose(self._h)
            raise

    def set_frames(self, frames):
        """Swap in a new (e.g. re-volumed) sample buffer."""
        _winmm.waveOutReset(self._h)
        if self._hdr is not None:
            rc = _winmm.waveOutUnprepareHeader(self._h, ctypes.byref(self._hdr), ctypes.sizeof(_WAVEHDR))
            if rc != 0:
                raise RuntimeError(f"waveOutUnprepareHeader failed, MMRESULT={rc}")
        buf = ctypes.create_string_buffer(frames, len(frames))
        hdr = _WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
        hdr.dwBufferLength = len(frames)
        rc = _winmm.waveOutPrepareHeader(self._h, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR))
        if rc != 0:
            raise RuntimeError(f"waveOutPrepareHeader failed, MMRESULT={rc}")
        # Only publish after both calls succeeded, so a failed swap leaves
        # no half-initialized state behind.
        self._buf = buf
        self._hdr = hdr

    def play(self):
        # Reset stops any in-flight playback of this clip (marks the header
        # done) so the write below restarts it from the top -- same
        # restart-on-retrigger behavior the Qt engine had.
        _winmm.waveOutReset(self._h)
        rc = _winmm.waveOutWrite(self._h, ctypes.byref(self._hdr), ctypes.sizeof(_WAVEHDR))
        if rc != 0:
            raise RuntimeError(f"waveOutWrite failed, MMRESULT={rc}")

    def close(self):
        try:
            _winmm.waveOutReset(self._h)
            if self._hdr is not None:
                _winmm.waveOutUnprepareHeader(self._h, ctypes.byref(self._hdr), ctypes.sizeof(_WAVEHDR))
            _winmm.waveOutClose(self._h)
        except Exception:
            pass


class AudioManager:
    def __init__(
        self,
        volume: float = 0.8,
        muted: bool = False,
        menu_sfx_enabled: bool = True,
        audio_debug: bool = False,
        watchdog_threshold: int = DEFAULT_WATCHDOG_THRESHOLD,
        rotation_seconds: int = 0,  # accepted for config compat; unused by the winmm engine
    ):
        self._audio_debug = audio_debug
        self._watchdog_threshold = max(1, watchdog_threshold)

        self._specs = {
            "blip": {"path": SOUNDS_DIR / "blip.wav", "optional": False, "channel": "voice"},
            "laugh": {"path": SOUNDS_DIR / "laugh.wav", "optional": False, "channel": "voice"},
            "menu_move": {"path": SFX_DIR / "menu_move.wav", "optional": True, "channel": "ui"},
            "menu_select": {"path": SFX_DIR / "menu_select.wav", "optional": True, "channel": "ui"},
        }
        # name -> (wave params, raw frames) at full volume, loaded once.
        self._loaded = {}
        for name, spec in self._specs.items():
            self._loaded[name] = self._load_wav(spec["path"], name, spec["optional"])

        self.volume = max(0.0, min(1.0, volume))
        self.muted = muted
        self.menu_sfx_enabled = menu_sfx_enabled

        # name -> persistent open waveOut stream (or None if clip missing).
        self._voices = {}
        for name in self._specs:
            self._voices[name] = self._build_voice(name)

        self._consecutive_failures = 0
        self._last_menu_move = 0.0
        self._last_menu_select = 0.0

        audio_log.log_event("音频引擎启动：winmm/waveOut常开流（引擎沿革见audio.py头部注释）")

    # ------------------------------------------------------------- loading
    def _load_wav(self, path, name, optional):
        if optional and not path.exists():
            return None
        try:
            with wave.open(str(path), "rb") as w:
                return (w.getparams(), w.readframes(w.getnframes()))
        except Exception:
            log_exception(f"audio: failed to load '{name}' from {path}")
            return None

    def _scaled_frames(self, name):
        loaded = self._loaded.get(name)
        if loaded is None:
            return None
        params, frames = loaded
        v = self.volume
        if v >= 0.999:
            return frames
        return audioop.mul(frames, params.sampwidth, v)

    def _build_voice(self, name):
        loaded = self._loaded.get(name)
        if loaded is None:
            return None
        try:
            voice = _WaveOutVoice(loaded[0], self._scaled_frames(name))
        except Exception:
            log_exception(f"audio: failed to open waveOut voice for '{name}'")
            return None
        return voice

    # ----------------------------------------------------------- public API
    def set_volume(self, volume: float):
        self.volume = max(0.0, min(1.0, volume))
        for name, voice in self._voices.items():
            if voice is None:
                continue
            try:
                voice.set_frames(self._scaled_frames(name))
            except Exception:
                log_exception(f"audio: buffer swap failed for '{name}', rebuilding voice")
                voice.close()
                self._voices[name] = self._build_voice(name)
        if self._audio_debug:
            audio_log.log_event(f"re-rendered all clips at volume={self.volume:.2f}")

    def set_muted(self, muted: bool):
        self.muted = muted

    def set_menu_sfx_enabled(self, enabled: bool):
        self.menu_sfx_enabled = enabled

    def play_blip_for_char(self, ch: str):
        if self.muted or ch in _SKIP_BLIP_CHARS:
            return
        self._play("blip")

    def play_laugh(self):
        if not self.muted:
            self._play("laugh")

    def play_menu_move(self):
        if not self.menu_sfx_enabled:
            return
        now = time.monotonic()
        if now - self._last_menu_move < MENU_SFX_DEBOUNCE_S:
            return
        self._last_menu_move = now
        self._play("menu_move")

    def play_menu_select(self):
        if not self.menu_sfx_enabled:
            return
        now = time.monotonic()
        if now - self._last_menu_select < MENU_SFX_DEBOUNCE_S:
            return
        self._last_menu_select = now
        self._play("menu_select")

    # -------------------------------------------------------------- playing
    def _play(self, name):
        voice = self._voices.get(name)
        if voice is None:
            return
        try:
            voice.play()
        except Exception:
            # Honest error from winmm: this handle is sick. Rebuild it and
            # retry exactly once -- unlike QSoundEffect, a fresh waveOut
            # handle shares no wedgeable engine state with the dead one.
            log_exception(f"audio: play failed for '{name}', rebuilding voice")
            voice.close()
            voice = self._build_voice(name)
            self._voices[name] = voice
            try:
                if voice is not None:
                    voice.play()
                    self._record_success(name, rebuilt=True)
                    return
            except Exception:
                log_exception(f"audio: retry after rebuild also failed for '{name}'")
            self._record_failure(name)
            return
        self._record_success(name)

    def _record_failure(self, name):
        self._consecutive_failures += 1
        channel = self._specs[name]["channel"]
        audio_log.log_event(
            f"play failed: purpose='{name}' channel='{channel}' consecutive={self._consecutive_failures}"
        )
        if self._consecutive_failures == self._watchdog_threshold:
            log_message(f"音频系统异常：winmm连续{self._consecutive_failures}次播放失败")

    def _record_success(self, name, rebuilt=False):
        self._consecutive_failures = 0
        if rebuilt:
            audio_log.log_event(f"rebuild+retry ok: purpose='{name}'")
        elif self._audio_debug:
            channel = self._specs[name]["channel"]
            audio_log.log_event(f"play ok: purpose='{name}' channel='{channel}'")

    # ------------------------------------------------------------- dev tools
    def run_stress_test(self, iterations=200, on_done=None):
        """Dev-only verification tool (config `audio_stress_test_on_startup`).
        Fires `iterations` randomly-mixed plays across every purpose at
        random short intervals. Kept API-compatible with the Qt-era version."""
        import random

        from PyQt6.QtCore import QTimer

        stats = {"attempts": 0, "failures": 0, "rebuilds": 0}
        state = {"remaining": iterations}
        purposes = ["blip", "laugh", "menu_move", "menu_select"]
        audio_log.log_event(f"压力自测开始：{iterations}次混合播放")

        def step():
            if state["remaining"] <= 0:
                audio_log.log_event(
                    "压力自测结束：尝试={attempts} 失败={failures} 重建={rebuilds}".format(**stats)
                )
                if on_done:
                    on_done(stats)
                return
            state["remaining"] -= 1
            stats["attempts"] += 1
            before = self._consecutive_failures
            name = random.choice(purposes)
            if name == "blip":
                self.play_blip_for_char("a")
            elif name == "laugh":
                self.play_laugh()
            elif name == "menu_move":
                self.play_menu_move()
            else:
                self.play_menu_select()
            if self._consecutive_failures > before:
                stats["failures"] += 1
            QTimer.singleShot(random.randint(5, 40), step)

        QTimer.singleShot(0, step)
