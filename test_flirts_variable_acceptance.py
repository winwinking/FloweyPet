# -*- coding: utf-8 -*-
"""Acceptance tests for the {flirts} placeholder in DialogueManager._substitute
and its use in dialogues_flirt.json's 71+ tier.

Run:  QT_QPA_PLATFORM=offscreen python test_flirts_variable_acceptance.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

TMP = Path(tempfile.mkdtemp(prefix="floweypet_flirts_"))
os.environ["FLOWEYPET_DATA_DIR"] = str(TMP / "data")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from PyQt6.QtCore import QCoreApplication  # noqa: E402

app = QCoreApplication(sys.argv)

import error_log  # noqa: E402

error_log.LOG_PATH = TMP / "error.log"

import dialogue_manager  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append((name, detail))


def subst(text, stats):
    fake = SimpleNamespace(config={"total_opens": 1, "total_clicks": 7}, stats=stats, _anticheat_placeholders={},
                           _session_duration_str=lambda: "0分钟", _days_since_install=lambda: 3)
    return dialogue_manager.DialogueManager._substitute(fake, text)


check("flirt_count 为 88 → {flirts} 替换成 88", subst("{flirts}次了", SimpleNamespace(data={"flirt_count": 88})) == "88次了")
check("同一句里多个 {flirts} 全部替换", subst("{flirts}和{flirts}", SimpleNamespace(data={"flirt_count": 88})) == "88和88")
check("stats 为 None → 替换成 0", subst("{flirts}次了", None) == "0次了")
check("stats.data 没有 flirt_count → 0", subst("{flirts}次", SimpleNamespace(data={})) == "0次")
check("不含 {flirts} 的文本不变", subst("你好", None) == "你好")
check("{clicks} 仍然正常", subst("{clicks}", None) == "7")

d = json.loads((ROOT / "dialogues_flirt.json").read_text(encoding="utf-8"))
tier4 = [t for t in d["flirt_response"] if t["min"] == 71][0]
hits = [e["lines"][0] for e in tier4["entries"] if "{flirts}" in e["lines"][0]]
check("第四档恰有一句用了 {flirts}", len(hits) == 1, str(len(hits)))
check("该句含两处 {flirts} 且不再有‘七十多’", bool(hits) and hits[0].count("{flirts}") == 2 and "七十多" not in hits[0])
if hits:
    check("用真实次数 88 渲染该句", subst(hits[0], SimpleNamespace(data={"flirt_count": 88})).startswith("88次了。你对我说了88次好话。"))

print()
if FAILURES:
    print(f"共 {len(FAILURES)} 项失败")
    sys.exit(1)
print("{flirts} 变量用例全部通过")
sys.exit(0)
