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
from pathlib import Path

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


_DEC_RE = re.compile(r"^-\s+(\d{4}-\d{2}-\d{2})\s*\|\s*([DN]-\d+)\s*\|\s*([^|]+)\|\s*([^|]+?)(?:\s*\|\s*关键词[:：]\s*(.+))?$")


def parse_decisions_md(md_path) -> list:
    """解析决策记忆（已拍板/明确不做），供 find 并行提示而不扰动 assets IDF 基线。"""
    p = Path(md_path) if md_path else None
    if not p or not p.is_file():
        return []
    tag, out = "已拍板", []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if s.startswith("## "):
            tag = "明确不做" if "不做" in s else "已拍板"
            continue
        m = _DEC_RE.match(s)
        if not m:
            continue
        _, did, scope, conclusion, kws = m.groups()
        scope, conclusion = scope.strip(), conclusion.strip()
        raw_t = [did, scope] + re.split(r"[,，、/\s]+", kws or "")
        trigs = list(dict.fromkeys(t.strip() for t in raw_t if len(t.strip()) >= 2))[:16]
        out.append({
            "id": f"decision:{did}",
            "kind": "decision",
            "name": f"{tag} {did} ({scope})",
            "summary": re.sub(r"\s+", " ", conclusion).replace("|", "/")[:88],
            "text": f"{did} {tag} {scope} {conclusion} {' '.join(trigs)}"[:600],
            "triggers": trigs,
            "anti_triggers": [],
            "path": "_meta/决策记忆.md",
            "visibility": "internal",
        })
    return out


def parse_infra_yaml(yaml_path) -> list:
    """纯标准库解析 _meta/内网基建清单.yaml，返回基建条目列表；文件缺失返回 []。"""
    p = Path(yaml_path) if yaml_path else None
    if not p or not p.is_file():
        return []
    out, cur, list_key = [], None, None
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if ln.startswith("- id:"):
            cur = {"id": ln.split(":", 1)[1].strip(), "endpoints": [], "redlines": [], "docs": []}
            out.append(cur)
            list_key = None
        elif cur and ln.startswith("    - ") and list_key:
            cur[list_key].append(s[2:].strip())
        elif cur and ln.startswith("  ") and not ln.startswith("    ") and ":" in ln:
            k, v = ln.strip().split(":", 1)
            v = v.strip()
            if k in ("endpoints", "redlines", "docs"):
                cur[k], list_key = ([v] if v else []), (k if not v else None)
            else:
                cur[k], list_key = v.strip("'\""), None
    return out


def infra_assets(yaml_path) -> list:
    """把内网基建条目转成检索条目（归入 boundaries 通道，不偏移 assets IDF）。"""
    out = []
    for it in parse_infra_yaml(yaml_path):
        iid, name, summary = it["id"], it.get("name", ""), it.get("summary", "")
        trigs = [x.strip() for x in re.split(r"[,，、\s]+", f"{iid},{name},{it.get('triggers', '')}") if len(x.strip()) >= 2]
        ep = "; ".join(it.get("endpoints") or [])
        out.append({
            "id": f"infra:{iid}",
            "kind": "infra",
            "name": f"{iid} ({name})" if name else iid,
            "summary": re.sub(r"\s+", " ", f"{summary} | {ep}").replace("|", "/")[:88],
            "text": f"{iid} {name} {summary} {ep} {it.get('usage', '')}"[:600],
            "triggers": list(dict.fromkeys(trigs))[:18],
            "anti_triggers": [],
            "path": "_meta/内网基建清单.yaml",
            "visibility": "internal",
        })
    return out


def cmd_infra(argv, yaml_path) -> int:
    """asset infra 子命令：为新项目一键输出可复用的脱敏内网基建接入说明书。"""
    import json
    items = parse_infra_yaml(yaml_path)
    only = next((a.split("=", 1)[1] for a in argv if a.startswith("--only=")), "")
    if only:
        keys = [k.strip().lower() for k in only.split(",") if k.strip()]
        items = [it for it in items if any(k == it["id"].lower() or k in (it["id"] + it.get("name", "")).lower() for k in keys)]
    if "--json" in argv:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("（暂无已登记基建；可于 _meta/内网基建清单.yaml 按模板补充）")
        return 0
    if "--list" in argv:
        for it in items:
            ep0 = (it.get("endpoints") or [""])[0]
            print(f"- {it['id']:14s} | {it.get('name', '')} | {ep0}")
        return 0
    print(f"# 内网可用基建清单（共 {len(items)} 项，来源 `_meta/内网基建清单.yaml`）\n")
    for it in items:
        print(f"## {it['id']} — {it.get('name', '')}")
        print(f"- **定位**：{it.get('summary', '')}")
        for e in it.get("endpoints") or []:
            print(f"- **入口**：`{e}`")
        if it.get("usage"):
            print(f"- **用法**：{it['usage']}")
        if it.get("auth_ref"):
            print(f"- **凭据出处**：{it['auth_ref']}")
        for r in it.get("redlines") or []:
            print(f"- **红线**：{r}")
        if it.get("docs"):
            print(f"- **深入文档**：{', '.join(f'`{d}`' for d in it['docs'])}")
        print()
    return 0
