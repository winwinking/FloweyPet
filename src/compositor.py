# -*- coding: utf-8 -*-
"""Builds display-ready QPixmaps from the raw sprite assets.

Two kinds of frames:
- "plain" frames (appear / idle): pre-baked full-character art, used as-is.
- "face" frames: a fixed stem/body base (stem_base.png) with a swappable
  head pasted on top at a precomputed offset (see assets/faces_manifest.json,
  built by scripts at asset-prep time -- no image math happens at runtime,
  just two drawPixmap calls, so switching expression is cheap and instant).

Scaling: BASE_SCALE is the 100%-tier multiplier (raw sprite px -> on-screen
px). The pet's size tier (pet_scale, e.g. 0.85) multiplies on top of that,
so the effective factor is BASE_SCALE * pet_scale. Raw (1x) pixmaps are
loaded from disk once and cached; only the final upscale is redone when the
tier changes (set_pet_scale), so switching tiers never touches disk again.

Alignment note: the face/stem composite in get_face() pastes the swappable
head onto stem_base at its manifest offset while both are still at native
1x resolution, then scales the *combined* canvas as a single bitmap. That
means the face-to-stem offset is baked into one bitmap before any scaling
happens, so every tier stays pixel-aligned automatically -- there's no
separate "multiply the offset by the tier" step needed anywhere.
"""
import json

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap

from paths import (
    APPEAR_DIR,
    APPEAR_NEUTRAL_DIR,
    FACES_DIR,
    FACES_MANIFEST_PATH,
    IDLE_DIR,
    IDLE_STILL_DIR,
    QUIT_ANIM_DIR,
    STEM_BASE_PATH,
)

BASE_SCALE = 4  # the 100% tier's upscale factor


class SpriteBank:
    def __init__(self, pet_scale: float = 1.0):
        self.pet_scale = pet_scale
        self._factor = BASE_SCALE * pet_scale

        self.stem_base = QPixmap(str(STEM_BASE_PATH))
        self.canvas_size = self.stem_base.size()
        with open(FACES_MANIFEST_PATH, "r", encoding="utf-8") as f:
            self.manifest = json.load(f)
        self._face_cache = {}

        self._raw_appear = self._load_raw(APPEAR_DIR, 9)
        self._raw_appear_neutral = self._load_raw(APPEAR_NEUTRAL_DIR, 9)
        self._raw_idle = self._load_raw(IDLE_DIR, 3)
        self._raw_still = self._load_raw_existing(IDLE_STILL_DIR)[:1]  # optional; [] -> idle_frames[0]
        self._raw_quit = self._load_raw_existing(QUIT_ANIM_DIR)

        self.appear_frames = []
        self.appear_neutral_frames = []
        self.idle_frames = []
        self.still_frame = None
        self.quit_frames = []
        self._rescale_all()

    # ------------------------------------------------------------- scaling
    def _scale(self, pm: QPixmap) -> QPixmap:
        return pm.scaled(
            round(pm.width() * self._factor),
            round(pm.height() * self._factor),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

    def set_pet_scale(self, pet_scale: float):
        """Switches size tier: rescales every cached raw frame at the new
        factor and drops the face-composite cache (it's keyed only by
        expr/frame_idx, not by tier, so stale-scale entries must go)."""
        self.pet_scale = pet_scale
        self._factor = BASE_SCALE * pet_scale
        self._face_cache = {}
        self._rescale_all()

    def _rescale_all(self):
        self.appear_frames = [self._scale(pm) for pm in self._raw_appear]
        self.appear_neutral_frames = [self._scale(pm) for pm in self._raw_appear_neutral]
        self.idle_frames = [self._scale(pm) for pm in self._raw_idle]
        self.still_frame = self._scale(self._raw_still[0]) if self._raw_still else self.idle_frames[0]
        self.quit_frames = [self._scale(pm) for pm in self._raw_quit]

    # -------------------------------------------------------------- loading
    def _load_raw(self, folder, count):
        return [QPixmap(str(folder / f"{i:02d}.png")) for i in range(count)]

    def _load_raw_existing(self, folder):
        """Like _load_raw, but for an optional folder with an unknown number
        of frames (e.g. a user-supplied quit animation): loads 00.png,
        01.png, ... for as many as actually exist, or [] if the folder is
        missing/empty."""
        frames = []
        if not folder.exists():
            return frames
        i = 0
        while (folder / f"{i:02d}.png").exists():
            frames.append(QPixmap(str(folder / f"{i:02d}.png")))
            i += 1
        return frames

    def face_frame_count(self, expr: str) -> int:
        entries = self.manifest.get(expr) or self.manifest["neutral"]
        return len(entries)

    def get_face(self, expr: str, frame_idx: int = 0) -> QPixmap:
        key = (expr, frame_idx)
        cached = self._face_cache.get(key)
        if cached is not None:
            return cached
        entries = self.manifest.get(expr) or self.manifest["neutral"]
        entry = entries[frame_idx % len(entries)]
        face_pm = QPixmap(str(FACES_DIR / expr / entry["file"]))

        canvas = QPixmap(self.canvas_size)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawPixmap(0, 0, self.stem_base)
        ox, oy = entry["offset"]
        painter.drawPixmap(ox, oy, face_pm)
        painter.end()

        scaled = self._scale(canvas)
        self._face_cache[key] = scaled
        return scaled
