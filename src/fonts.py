# -*- coding: utf-8 -*-
from PyQt6.QtGui import QFontDatabase, QFont

from paths import FONT_DIR, FONTS_DIR

_family = None
_dseg7_family = None
_dseg7_is_fallback = False
_dseg7_warned = False


def load_pixel_font_family():
    global _family
    if _family is not None:
        return _family
    font_path = FONTS_DIR / "fusion-pixel.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    families = QFontDatabase.applicationFontFamilies(font_id)
    _family = families[0] if families else "Consolas"
    return _family


def pixel_font(point_size: int) -> QFont:
    font = QFont(load_pixel_font_family())
    font.setPixelSize(point_size)
    font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
    return font


def _find_dseg7_file():
    if not FONT_DIR.exists():
        return None
    candidates = sorted(FONT_DIR.glob("*.ttf"))
    classic = [c for c in candidates if "dseg7" in c.name.lower() and "classic" in c.name.lower()]
    for c in classic:
        if "regular" in c.name.lower():
            return c
    if classic:
        return classic[0]
    return candidates[0] if candidates else None


def load_dseg7_family():
    """Returns the DSEG7 font family name, or the pixel-font family as a
    fallback if assets/font/ doesn't have a DSEG7 ttf yet. Prints a one-time
    console notice when falling back, since this is meant to be temporary."""
    global _dseg7_family, _dseg7_is_fallback, _dseg7_warned
    if _dseg7_family is not None:
        return _dseg7_family
    path = _find_dseg7_file()
    if path is not None:
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            _dseg7_family = families[0]
            return _dseg7_family
    _dseg7_is_fallback = True
    if not _dseg7_warned:
        _dseg7_warned = True
        print(
            "[FloweyPet] 未在 assets/font/ 找到 DSEG7 数码管字体，"
            "番茄钟倒计时暂用像素字体代替显示。"
        )
    _dseg7_family = load_pixel_font_family()
    return _dseg7_family


def dseg7_font_is_fallback() -> bool:
    load_dseg7_family()
    return _dseg7_is_fallback


def dseg7_font(pixel_size: int) -> QFont:
    font = QFont(load_dseg7_family())
    font.setPixelSize(pixel_size)
    if dseg7_font_is_fallback():
        font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
    return font
