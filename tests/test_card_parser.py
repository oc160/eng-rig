# -*- coding: utf-8 -*-
"""资产卡统一解析器用例（Antigravity 首评 P3-1）。"""
import importlib.util
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "card_parser", LIB / "_meta" / "hub" / "card_parser.py")
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)

SAMPLE = """# -*- coding: utf-8 -*-
# ── 资产卡 ──────────────────────────────────────
# 名称：report_anchors.py —— 送审稿 docx → 原文锚点库
# 来源：D:/projects/demo/9.9评审/（2026-11-07 实战蒸馏）
#     —— 专家意见里的章节号应校验
# 提取日期：2026-11-07
# Author: EngineerRig
# 验证方式：实战✓
# 重跑命令：python report_anchors.py 送审稿.docx _anchors_raw.txt
# 依赖：pip install python-docx
# ────────────────────────────────────────────────
# -*- coding: utf-8 -*-
import sys
"""


def test_parse_full_card():
    c = cp.parse_asset_card(SAMPLE)
    assert c["名称"].startswith("report_anchors.py")
    assert "9.9评审" in c["来源"] and "章节号" in c["来源"]   # 续行并入
    assert c["依赖"] == "pip install python-docx"
    assert "送审稿.docx" in c["重跑命令"]


def test_parse_no_card_returns_empty():
    c = cp.parse_asset_card("# 注释\nprint(1)\n")
    assert c == {k: "" for k in cp.FIELDS}


def test_card_fields_text_for_index():
    t = cp.card_fields_text(SAMPLE)
    assert "python-docx" in t and "9.9评审" in t and len(t) <= 400   # 依赖+来源字段入检索文本


def test_all_carded_repo_scripts_parse_without_error():
    n = 0
    for base in ("scripts", "snippets"):
        for p in (LIB / base).rglob("*"):
            if not p.is_file() or p.suffix.lower() not in {".py", ".js", ".ps1", ".pyw"}:
                continue
            head = p.read_text(encoding="utf-8", errors="ignore")[:4000]
            if "资产卡" in head or "Asset Card" in head:
                c = cp.parse_asset_card(head)
                assert isinstance(c["名称"], str)
                n += 1
    assert n >= 1                                    # 全量带卡件解析不抛错（主仓 >150，公开派生仓 ≥1）


def test_parse_english_asset_card():
    """中英双语同构：英文 Asset Card 字段映射到同一规范键。"""
    en = (
        "# -*- coding: utf-8 -*-\n"
        "# ── Asset Card ────────────────────────────\n"
        "# Name: demo.py -- Format markdown headings\n"
        "# Source: https://github.com/example/demo\n"
        "# Extracted: 2026-11-16\n"
        "# Author: Antigravity\n"
        "# Verification: pytest tests/test_demo.py\n"
        "# Command: python demo.py input.md\n"
        "# Dependencies: stdlib only\n"
        "# ─────────────────────────────────────────\n"
        "import sys\n"
    )
    c = cp.parse_asset_card(en)
    assert c["名称"].startswith("demo.py")
    assert c["来源"] == "https://github.com/example/demo"
    assert c["依赖"] == "stdlib only"
    assert "demo.py" in c["重跑命令"]
    assert "stdlib only" in cp.card_fields_text(en)
