# -*- coding: utf-8 -*-
"""Builds display-ready QPixmaps from the raw sprite assets.

Two kinds of frames:
- "plain" frames (appear / idle): pre-baked full-character art, used as-is.
- "face" frames: a fixed stem/body base (stem_base.png) with a swappable
  head pasted on top at a precomputed offset (see assets/faces_manifest.json,
  built by scripts at asset-prep time -- no image math happens at runtime,
  just two drawPixmap calls, so switching expression is cheap and instant).
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
    QUIT_ANIM_DIR,
    STEM_BASE_PATH,
)

SCALE = 4


class SpriteBank:
    def __init__(self):
        self.stem_base = QPixmap(str(STEM_BASE_PATH))
        self.canvas_size = self.stem_base.size()
        with open(FACES_MANIFEST_PATH, "r", encoding="utf-8") as f:
            self.manifest = json.load(f)
        self._face_cache = {}
        self.appear_frames = self._load_plain(APPEAR_DIR, 9)
        self.appear_neutral_frames = self._load_plain(APPEAR_NEUTRAL_DIR, 9)
        self.idle_frames = self._load_plain(IDLE_DIR, 3)
        self.quit_frames = self._load_existing(QUIT_ANIM_DIR)

    def _scale(self, pm: QPixmap) -> QPixmap:
        return pm.scaled(
            pm.width() * SCALE,
            pm.height() * SCALE,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

    def _load_plain(self, folder, count):
        frames = []
        for i in range(count):
            pm = QPixmap(str(folder / f"{i:02d}.png"))
            frames.append(self._scale(pm))
        return frames

    def _load_existing(self, folder):
        """Like _load_plain, but for an optional folder with an unknown
        number of frames (e.g. a user-supplied quit animation): loads
        00.png, 01.png, ... for as many as actually exist, or [] if the
        folder is missing/empty."""
        frames = []
        if not folder.exists():
            return frames
        i = 0
        while (folder / f"{i:02d}.png").exists():
            frames.append(self._scale(QPixmap(str(folder / f"{i:02d}.png"))))
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
