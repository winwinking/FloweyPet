# -*- coding: utf-8 -*-
"""Acceptance tests for the 3.3.0 size tiers (25%-200%): the one-time
pet_size_tiers migration (config.apply_size_settings), pet_scale snapping
(config.snap_pet_scale), and panel widths at every new tier. config.CONFIG_PATH
is pointed at a temp dir; dev_data and %APPDATA% are never touched.

Run:  QT_QPA_PLATFORM=offscreen python test_size_tiers_acceptance.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="floweypet_tiers_"))
DATA = TMP / "data"
os.environ["FLOWEYPET_DATA_DIR"] = str(DATA)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import json  # noqa: E402

from PyQt6.QtGui import QFontMetrics  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)

import error_log  # noqa: E402

error_log.LOG_PATH = TMP / "error.log"

import config  # noqa: E402
import paths  # noqa: E402

CFG = DATA / "config.json"
config.CONFIG_PATH = CFG
paths.CONFIG_PATH = CFG

from fonts import pixel_font  # noqa: E402
from size_panel import SizePanel  # noqa: E402
from stats_panel import StatsPanel  # noqa: E402
from undertale_ui import UiMetrics  # noqa: E402

FAILURES = []
NEW = [2.0, 1.5, 1.25, 1.0, 0.75, 0.5, 0.25]
OLD = [1.0, 0.85, 0.7, 0.55]


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def start(raw=None):
    """Simulate a startup: write config.json (or none), load, apply."""
    if DATA.exists():
        for p in DATA.iterdir():
            p.unlink()
    DATA.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        CFG.write_text(json.dumps(raw), encoding="utf-8")
    cfg = config.load_config()
    changed = config.apply_size_settings(cfg)
    disk = json.loads(CFG.read_text(encoding="utf-8")) if CFG.exists() else None
    return cfg, disk, changed


check("读写的是本测试自己的临时目录", CFG.parent == DATA and str(DATA).startswith(tempfile.gettempdir()))
check("DEFAULT_PET_SIZE_TIERS 是新七档", config.DEFAULT_PET_SIZE_TIERS == NEW)
check("DEFAULTS 的挡位等于新列表且记号默认 1", config.DEFAULTS["pet_size_tiers"] == NEW and config.DEFAULTS["size_tiers_schema"] == 1)
check("所有新挡位 * BASE_SCALE 都是整数", all(float(4 * t).is_integer() for t in NEW))

# 1. migration
cfg, disk, changed = start({"pet_size_tiers": OLD, "pet_scale": 1.0})
check("旧挡位无记号 → 内存里是新列表、记号 2", cfg["pet_size_tiers"] == NEW and cfg["size_tiers_schema"] == 2)
check("旧挡位无记号 → 文件里也写进去了", disk is not None and disk["pet_size_tiers"] == NEW and disk["size_tiers_schema"] == 2)
check("迁移返回 True", changed is True)
log_text = (TMP / "error.log").read_text(encoding="utf-8") if (TMP / "error.log").exists() else ""
check("日志里写明了旧列表", "0.85" in log_text and "0.55" in log_text, log_text[-200:])

# 2. already migrated, user-edited list is kept
custom = [1.0, 0.6]
cfg, disk, changed = start({"pet_size_tiers": custom, "size_tiers_schema": 2, "pet_scale": 0.6})
check("记号 2 且列表被用户改过 → 不再覆盖", cfg["pet_size_tiers"] == custom and disk == {"pet_size_tiers": custom, "size_tiers_schema": 2, "pet_scale": 0.6})
check("记号 2 且 pet_scale 在列表里 → 没有改动、没有写文件", changed is False and cfg["pet_scale"] == 0.6)

# 3. snapping (pure function)
for src, want in ((0.55, 0.5), (0.85, 0.75), (0.7, 0.75), (1.0, 1.0), (0.3, 0.25), (3.0, 2.0)):
    got = config.snap_pet_scale(src, NEW)
    check(f"吸附 {src} → {want}", got == want, str(got))
check("吸附: 距离相同取较大 (0.625 在 0.5 与 0.75 正中 → 0.75)", config.snap_pet_scale(0.625, NEW) == 0.75)
check("吸附: 非数字当 1.0", config.snap_pet_scale("x", NEW) == 1.0 and config.snap_pet_scale(None, NEW) == 1.0)
check("吸附: 挡位列表无效时原值不动", config.snap_pet_scale(0.55, []) == 0.55 and config.snap_pet_scale(0.55, None) == 0.55)

# 3b. snapping at startup, independent of the schema stamp
cfg, disk, changed = start({"pet_size_tiers": NEW, "size_tiers_schema": 2, "pet_scale": 0.55})
check("启动时 pet_scale 0.55 不在列表 → 吸附成 0.5 并保存", cfg["pet_scale"] == 0.5 and disk["pet_scale"] == 0.5 and changed)
check("吸附记了日志", "0.55" in (TMP / "error.log").read_text(encoding="utf-8") and "吸附" in (TMP / "error.log").read_text(encoding="utf-8"))
cfg, disk, changed = start({"pet_size_tiers": OLD, "pet_scale": 0.55})
check("旧用户 0.55 先迁移再吸附 → 0.5", cfg["pet_scale"] == 0.5 and disk["pet_scale"] == 0.5 and disk["pet_size_tiers"] == NEW)

# 3c. only the intended fields are touched
other = {"pet_size_tiers": OLD, "pet_scale": 0.85, "volume": 0.3, "muted": True, "position": {"x": 5, "y": 6}}
cfg, disk, _ = start(other)
check("只动 pet_size_tiers / size_tiers_schema / pet_scale", disk["volume"] == 0.3 and disk["muted"] is True and disk["position"] == {"x": 5, "y": 6})

# 4. brand-new user
cfg, disk, changed = start(None)
check("全新用户: 新列表、记号 2、pet_scale 1.0", cfg["pet_size_tiers"] == NEW and cfg["size_tiers_schema"] == 2 and cfg["pet_scale"] == 1.0)

# 5. second run does nothing
_, _, changed2 = (cfg, None, config.apply_size_settings(cfg))
check("再次启动不再改动", changed2 is False)

# 6. panel widths across every new tier (ui_scale formula as PetWindow._ui_scale)
def ui_scale_for(pet_scale, ratio=0.5):
    return 1 - (1 - pet_scale) * ratio


big = {
    "days": 9999, "hours": 12345.6, "clicks": 99999, "interactions": 88888, "pomodoro_count": 7777,
    "flirt_count": 6666, "water_rate": 100.0, "meal_rate": 0.0, "water_dismissed": 5555,
    "meal_dismissed": 4444, "egg_seen": 999, "egg_total": 999, "egg_pct": 100.0, "latest_time": "23:59",
}
stat_rows = [
    ("陪伴天数", "9999天"), ("陪伴时长", "12345.6小时"), ("点击次数", "99999次"), ("互动次数", "88888次"),
    ("番茄钟完成", "7777次"), ("调情次数", "6666次"), ("喝水响应率", "100%"), ("吃饭响应率", "0%"),
    ("敷衍次数(水/饭)", "5555/4444次"), ("彩蛋收集", "999/999 (100%)"), ("最晚活跃", "23:59"),
]
for tier in NEW:
    scale = ui_scale_for(tier)
    m = UiMetrics(scale, 10)
    fm = QFontMetrics(pixel_font(m.font_px))
    chrome = m.gutter_width + m.panel_padding * 2 + m.border_width * 2
    sp = StatsPanel(big, ui_scale=scale, min_font_px=10)
    worst = max(sum(fm.horizontalAdvance(p) for p in r) + 12 * (len(r) - 1) for r in stat_rows)
    check(f"记录面板 tier={tier} ui_scale={scale:.3f} 装得下所有文字", sp.width() - chrome >= worst, f"{sp.width() - chrome} < {worst}")
    sp.close()
    pet_scales = []
    zp = SizePanel(lambda: tier, lambda s: pet_scales.append(s), NEW, ui_scale=scale, min_font_px=10)
    worst = max(fm.horizontalAdvance(f"{round(t * 100)}%") for t in NEW)
    check(f"大小面板 tier={tier} ui_scale={scale:.3f} 装得下所有文字", zp.width() - chrome >= worst, f"{zp.width() - chrome} < {worst}")
    check(f"大小面板 tier={tier} 行数 = 挡位数 (7)", len(zp._selectable) == 7, str(len(zp._selectable)))
    check(f"大小面板 tier={tier} 心形落在当前挡位那一行", zp._selected_index == NEW.index(tier), str(zp._selected_index))
    zp.close()

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("大小挡位用例全部通过")
sys.exit(0)
