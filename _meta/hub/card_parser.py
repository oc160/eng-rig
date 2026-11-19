# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：card_parser.py —— 资产卡统一解析器（三处口径归一：索引/门禁/会话收尾）
# 来源：2026-11-16 Antigravity 首评 P3-1 采纳（此前 pre-commit 用 awk、gate_check 用
#     "资产卡" in text、asset_cli 用正则各抓各的，字段从不校验也不进索引）
# 提取：2026-11-16
# Author: EngineerRig
# 验证方式：tests/test_card_parser.py（195 带卡件实测合规率：名称 140/195、用法 154/195，
#     故字段全部可选——解析器只提取不强制）
# 重跑命令：python -c "from pathlib import Path; import sys; sys.path.insert(0, r'_meta/hub'); from card_parser import parse_asset_card; print(parse_asset_card(Path('_meta/asset_cli.py').read_text(encoding='utf-8'))['名称'])"
# 依赖：纯标准库
# ──────────────────────────────────────────────────────────
"""资产卡解析（宽容口径）：从脚本头部的"── 资产卡 ──"块提取键值对。

块边界：含"资产卡"的行起，到首条非注释行/──── 结束线/文件头 4000 字符止。
键白名单（均返回，缺省空串）：名称/来源/提取日期/产出AI/验证方式/重跑命令/依赖/用法。
续行：以 # 开头且比键行多缩进的行并入上一键值。
"""
import re

FIELDS = ("名称", "来源", "提取日期", "产出AI", "验证方式", "重跑命令", "依赖", "用法")
EN_MAP = {
    "name": "名称",
    "source": "来源",
    "extracted": "提取日期",
    "author": "产出AI",
    "verification": "验证方式",
    "command": "重跑命令",
    "dependencies": "依赖",
    "usage": "用法",
}
_CN_MAP = {k.lower(): k for k in FIELDS}
_KEY = re.compile(
    r"^#\s*(" + "|".join(FIELDS) + r"|Name|Source|Extracted|Author|Verification|Command|Dependencies|Usage)\s*[：:]\s*(.*)$",
    re.I,
)
_END = re.compile(r"^#\s*(?:─{3,}|-{6,})")  # 卡块底部装饰线（中英双语兼容）
_MAX_SCAN = 4000


def parse_asset_card(text: str) -> dict:
    """提取资产卡字段（纯函数，支持中英文资产卡/Asset Card）。无卡返回全空 dict，不抛错。"""
    out = {k: "" for k in FIELDS}
    lines = (text or "").splitlines()
    in_card = False
    cur = None
    for ln in lines:
        if len(ln) > _MAX_SCAN:
            break
        if ("资产卡" in ln or "Asset Card" in ln) and not in_card:
            in_card = True
            continue
        if not in_card:
            continue
        if not ln.startswith("#"):
            break                            # 首条代码行=卡块结束
        if _END.match(ln):
            break
        m = _KEY.match(ln)
        if m:
            raw_k = m.group(1).lower()
            cur = EN_MAP.get(raw_k) or _CN_MAP.get(raw_k)
            val = m.group(2).strip()
            out[cur] = (out[cur] + " " + val).strip() if out[cur] and val else (out[cur] or val)
        elif cur and ln.startswith("#"):
            cont = ln.lstrip("#").strip()
            if cont and not cont.startswith(("─", "---")):
                out[cur] = (out[cur] + " " + cont).strip()
    return out


def card_fields_text(text: str, cap: int = 400) -> str:
    """索引用：把卡内可检索字段（来源/依赖/用法/验证方式）拼成一段文本（截 cap）。"""
    c = parse_asset_card(text)
    parts = [c[k] for k in ("来源", "依赖", "用法", "验证方式") if c[k]]
    return (" ".join(parts))[:cap]
