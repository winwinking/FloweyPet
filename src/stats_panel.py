# -*- coding: utf-8 -*-
"""Undertale-styled read-only usage-stats panel ("记录"), opened from the
pet's right-click menu. Same black/white bordered, pixel-font look as
ReminderPanel/PetContextMenu (see undertale_ui.py for the shared kit).

Every row here is display-only, so all rows are added non-selectable
(no heart cursor lands on them) and the panel is widened past the shared
default width since stat labels run longer than menu/toggle labels.

The panel itself never speaks -- the "flavor"/roast commentary that
accompanies it (from stats_roast.json via roast.py) is spoken by Flowey
through the normal dialogue box, since it carries a face per line and the
panel has no sprite of its own to show it on.
"""
from undertale_ui import (
    HoverButton,
    MenuRow,
    UndertalePopup,
    make_info_row,
    make_separator_row,
)

PANEL_WIDTH = 230


def _fmt_rate(rate):
    return f"{rate:.0f}%" if rate is not None else "暂无数据"


def _fmt_egg(seen, total, pct):
    if not total:
        return "暂无数据"
    return f"{seen}/{total} ({pct:.0f}%)"


class StatsPanel(UndertalePopup):
    def __init__(self, metrics, audio=None, always_on_top=True, parent=None):
        super().__init__(audio=audio, always_on_top=always_on_top, parent=parent)
        self.setFixedWidth(PANEL_WIDTH)

        self.add_row(make_info_row("陪伴天数", f"{metrics['days']}天", self.font), selectable=False)
        self.add_row(make_info_row("陪伴时长", f"{metrics['hours']:.1f}小时", self.font), selectable=False)
        self.add_row(make_info_row("点击次数", f"{metrics['clicks']}次", self.font), selectable=False)
        self.add_row(make_info_row("互动次数", f"{metrics['interactions']}次", self.font), selectable=False)
        self.add_row(make_info_row("番茄钟完成", f"{metrics['pomodoro_count']}次", self.font), selectable=False)
        self.add_row(make_separator_row(), selectable=False)

        self.add_row(make_info_row("喝水响应率", _fmt_rate(metrics["water_rate"]), self.font), selectable=False)
        self.add_row(make_info_row("吃饭响应率", _fmt_rate(metrics["meal_rate"]), self.font), selectable=False)
        self.add_row(make_separator_row(), selectable=False)

        self.add_row(
            make_info_row(
                "彩蛋收集",
                _fmt_egg(metrics["egg_seen"], metrics["egg_total"], metrics["egg_pct"]),
                self.font,
            ),
            selectable=False,
        )
        self.add_row(
            make_info_row("最晚活跃", metrics["latest_time"] or "暂无", self.font), selectable=False
        )
        self.add_row(make_separator_row(), selectable=False)
        self.add_row(self._build_button_row())

        self.finish_layout()

    def _confirm_and_close(self):
        if self.audio:
            self.audio.play_menu_select()
        self.close()

    def _build_button_row(self):
        row = MenuRow(self.font)
        ok_btn = HoverButton("确定", self.font)
        ok_btn.clicked.connect(self._confirm_and_close)
        row.row_layout.addStretch(1)
        row.row_layout.addWidget(ok_btn)
        row.row_layout.addStretch(1)
        row.activate = self._confirm_and_close
        return row
