# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：indexer.py —— 索引构建纯计算核（frontmatter/触发词/§0 行/卡片条目组装）
# 来源：2026-11-16 复杂度整改（Antigravity 实测 asset_cli.py McCabe=436 全库最高，
#     纯计算体外移 _meta/hub/ 子包）
# 提取：2026-11-16（自 _meta/asset_cli.py build_index 的解析与组装体逐行原样外移）
# Author: EngineerRig
# 验证方式：tests/test_index_build.py（§0 不漏行/关键行在场/触发词不截断/带卡脚本入索引）+
#     tests/test_asset_cli.py；行为保真由全量 pytest 兜底
# 重跑命令：python -m pytest -q tests/test_index_build.py
# 依赖：纯标准库；不 import asset_cli（单向被依赖）；文件路径/卡解析结果均显式收参
# ──────────────────────────────────────────────────────────
"""索引构建纯计算核（2026-11-16 自 asset_cli 外移，行为逐行保真）。

纪律：函数一律显式收参返回（目录路径/索引原文/卡解析结果），不读 asset_cli 模块
全局；触发词/反向触发词正则以本模块为唯一定义源，asset_cli 顶部重导出同名对象。
"""
import importlib.util
import re
from pathlib import Path

TRIGGER_RE = re.compile(
    r"(?:触发词|Triggers?)[:：]?\s*(.+?)(?:\n\s*(?:不适用|Anti-triggers?|Do not use)|\n---|\Z)",
    re.S | re.I,
)
# 反向触发词（review 未提、DeltaReplay ρ 的零模型版）：技能"不适用：…"/ "Do not use:" 行=现成的适用性负标注。
ANTI_RE = re.compile(r"(?:不适用|Anti-triggers?|Do not use(?:\s+when)?)[:：]?\s*(.+?)\Z", re.S | re.I)
CARD_EXTS = {".py", ".js", ".ps1", ".pyw"}

_SCORER = None


def _scorer():
    """装载同目录 scorer（平化归一 flat 共用；sys.path 无本目录，按文件位置装载）。"""
    global _SCORER
    if _SCORER is None:
        spec = importlib.util.spec_from_file_location(
            "_hub_scorer_for_indexer", Path(__file__).with_name("scorer.py"))
        _SCORER = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_SCORER)
    return _SCORER


# ── frontmatter / 文本规整 ────────────────────────────────
def frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    return (m.group(1) if m else "")


def fm_value(fm, key):
    m = re.search(rf"^{key}:\s*(.+?)(?=^[a-z_]+:|\Z)", fm, re.S | re.M)
    v = (m.group(1).strip() if m else "")
    return re.sub(r"^[|>][-+]?\s*", "", v)      # 去掉 YAML 块标量指示符（| / >）


def one_line(s, limit=46):
    s = re.sub(r"\s+", " ", (s or "").replace("|", "/")).strip()
    return s[:limit]


def parse_related(raw):
    """解析 SKILL.md frontmatter 的 related 字段（逗号/换行分隔，无前缀默认 skill:）。"""
    if not raw:
        return []
    out = []
    for item in re.split(r"[,\n，、;；]+", str(raw).strip().strip("[]")):
        x = item.strip(" -\"'`")
        if x:
            out.append(x if ":" in x else f"skill:{x}")
    return out[:5]


def triggers(desc, trigger_re=None):
    m = (trigger_re or TRIGGER_RE).search(desc or "")
    if not m:
        return []
    raw = m.group(1)
    # 触发词段遇到"流程：/用途：…"等新键即止（make-report-ppt 曾把流程段吞成 141 字触发词）
    raw = re.split(
        r"\n\s*(?:流程|用途|说明|输出|场景|用法|注意事项|效果|不适用|Steps|Usage|Notes|Output|Anti-triggers?|Do not use)[:：]",
        raw, flags=re.I,
    )[0]
    # 先按分隔符（含换行）切分再压空格——中文折行词不得粘连；英文词组保留单词间单空格
    parts = re.split(r"[；;、/，,\n]", raw)
    out = []
    for t in parts:
        t = t.strip(" ，,。.、")
        if re.search(r"[\u4e00-\u9fff]", t):
            t = re.sub(r"\s+", "", t)
        else:
            t = re.sub(r"\s+", " ", t).strip()
        if 2 <= len(t) <= 30:
            out.append(t)
    # 不截正常触发词（曾截 14 字，review 二轮 Q1）；>30 字的不是触发词是段落，弃
    return out[:24]


def anti_triggers(desc, anti_re=None):
    m = (anti_re or ANTI_RE).search(desc or "")
    if not m:
        return []
    raw = m.group(1)
    raw = re.split(r"\n\s*(?:[\u4e00-\u9fff]{2,4}|[A-Za-z][A-Za-z_-]{1,15})[:：]", raw)[0]  # 后续新键即止
    raw = re.sub(r"（[^）]*）|\([^)]*\)", " ", raw)             # 括号引导（用 xxx 技能）不属负场景
    out = [t.strip(" ，,。.、；;") for t in re.split(r"[；;、/，,\n]", raw)]
    out = [re.sub(r"(的)?场景$", "", t).strip() for t in out]       # "…的场景"后缀剥掉，留核词
    # W2-② 收紧：中文 ≥3..12 字；英文允许 3..28 字短语
    return [t for t in out if len(t) >= 3 and len(t) <= (12 if re.search(r"[\u4e00-\u9fff]", t) else 28)][:8]


# ── 索引条目组装 ──────────────────────────────────────────
def skill_assets(skills_dir):
    """skills/*/SKILL.md → skill 条目（frontmatter/触发词/反向触发词解析一体）。"""
    assets = []
    for d in sorted(skills_dir.iterdir()):
        sk = d / "SKILL.md"
        if not sk.is_file() or d.name == "_template":
            continue
        t = sk.read_text(encoding="utf-8", errors="replace")
        fm = frontmatter(t)
        name = fm_value(fm, "name") or d.name
        desc = fm_value(fm, "description")
        body = re.sub(r"\s+", " ", desc or t.split("---", 2)[-1])[:400]
        row = {
            "id": f"skill:{d.name}",
            "kind": "skill",
            "name": name,
            "summary": one_line(body),
            "text": re.sub(r"\s+", " ", body)[:600],
            "triggers": triggers(desc),
            "anti_triggers": anti_triggers(desc),
            "path": f"skills/{d.name}/SKILL.md",
            "visibility": "internal",
        }
        rel = parse_related(fm_value(fm, "related"))
        if rel:
            row["related"] = rel
        assets.append(row)
    return assets


def scan_carded(base_dir, roots=("scripts", "snippets")):
    """scripts/snippets 中带资产卡的规范入口（与四闸"必须有卡"同口径，review Q4：
    只索引规范入口，helper/归档/实验件不入）。返回 [(Path, 头部4000字)]。"""
    out = []
    for base in roots:
        root = base_dir / base
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in CARD_EXTS:
                continue
            head = p.read_text(encoding="utf-8", errors="ignore")[:4000]
            if "资产卡" in head or "Asset Card" in head:
                out.append((p, head))
    return out


def s0_entries(index_text):
    """INDEX §0 场景速查行解析：| 我想… | 用这个 | 位置 |。

    2026-10-27 review 二轮修正：原正则单元格长度上限（40/60/160）静默漏掉 4 行高频条目；
    现放宽上限并登记 dropped_rows——一致性测试（tests/test_index_build.py）断言其必为空。
    返回 (entry条目列表, dropped列表, 解析成功行数)。"""
    assets = []
    sec_m = re.search(r"^## 0\..*?(?=^## \d|\Z)", index_text, re.M | re.S)
    sec = sec_m.group(0) if sec_m else index_text
    dropped = []
    parsed_entries = 0
    for row in re.finditer(r"^\|(.+)\|\s*$", sec, re.M):
        cells = [c.strip() for c in row.group(1).split("|")]
        want = cells[0] if cells else ""
        if want.startswith(("我想", "---", "时点", "Task", "Goal", "I want")) or set(want) <= set("-: ") or len(cells) < 3:
            continue
        m = re.match(r"^\|\s*([^|]{3,200})\s*\|\s*([^|]{2,300})\s*\|\s*([^|]{2,1500})\s*\|\s*$",
                     row.group(0), re.M)
        if not m:
            dropped.append(want[:30])
            continue
        want, use, loc = (x.strip() for x in m.groups())
        paths = re.findall(r"`([^`]+)`", loc)
        # 稳定语义 id（review Q3/Q6：日志与数据集要可读、跨编辑稳定）
        use_name = use.replace("**", "").strip()
        slug = re.sub(r"[^\w\u4e00-\u9fff]+", "", use_name.split("（")[0])[:24] or "row"
        assets.append({
            "id": f"entry:{slug}",
            "kind": "entry",
            "name": use_name,
            "summary": one_line(want, 40),
            "text": f"{want} {use} {loc}"[:400],
            # W2-② 收紧：entry 触发词 ≥3 字且排除孤立格式扩展名（防 a+pdf+b 拆出裸 "pdf" 抢路由）
            "triggers": [t for t in (w.strip(" *_`") for w in re.split(r"[，,、/+＋]", want))
                         if len(t) >= 3 and t.lower() not in {"pdf", "dxf", "png", "jpg", "docx", "xlsx", "txt", "json", "yaml", "csv", "zip"}][:10],
            "path": paths[0] if paths else one_line(loc, 60),
            "visibility": "internal",
        })
        parsed_entries += 1
    return assets, dropped, parsed_entries


def script_asset(rel, stem, head, card, fields_text):
    """带卡脚本条目组装（review Q4：元数据取自资产卡，不另建注册表）。
    rel=库内相对路径；card=parse_asset_card(head) 结果；fields_text=card_fields_text(head)。
    P3-1（Antigravity 首评）：卡内来源/依赖/用法/验证字段并入检索文本——
    此前功能词埋在头部原始文本里，零结果率 19% 的重要来源。"""
    name = re.split(r"[——\-—]", card.get("名称") or stem, 1)[0].strip() or stem
    use = card.get("重跑命令") or card.get("用法") or f"库内脚本 {rel}"
    text = re.sub(r"\s+", " ", head)[:420] + " " + fields_text
    return {
        "id": f"script:{rel}",
        "kind": "script",
        "name": one_line(name, 40),
        "summary": one_line(use, 46),
        "text": text[:760],
        "triggers": [stem],
        "path": rel,
        "visibility": "internal",
    }


def inherit_anti(assets):
    """W2-② anti 继承（note: entry/script 按目标关系继承所指技能的负标注——
    "弧形闸门结构计算"曾从无 anti 的 entry:钢闸门计算引擎 绕行注入）：
    entry 按"用这个"列提及的技能名；script 按技能正文提及该文件名（保守单向，只加负不改正）。
    就地修改 assets（无返回值，与原实现一致）。"""
    flat = _scorer().flat
    skills = {a["id"]: a for a in assets if a["kind"] == "skill"}
    skill_by_word = {}
    for a in skills.values():
        skill_by_word[flat(a["name"])] = a
        skill_by_word[flat(a["id"].split(":", 1)[1])] = a
    for a in assets:
        if a["kind"] not in ("entry", "script") or a.get("anti_triggers"):
            continue
        src = None
        if a["kind"] == "entry":
            for w in re.split(r"[（）()/，,、\s]+", a.get("name", "")):
                src = skill_by_word.get(flat(w))
                if src:
                    break
            if src is None:                      # 位置列的 skills/<目录> 路径解析（W2-②）
                m = re.search(r"skills/([\w\-]+)", a.get("text") or "")
                src = skills.get(f"skill:{m.group(1)}") if m else None
            if src is None:                      # 触发词包含式解析：entry 词含某技能触发词
                w0 = flat("".join(a.get("name", "").split()[:1]))  # 主词（去（…）后缀）
                for s in skills.values():
                    if any(len(t) >= 3 and t in w0 for t in s.get("triggers", [])):
                        src = s
                        break
        else:
            stem = Path(a["path"]).name
            src = next((s for s in skills.values() if stem in (s.get("text") or "")), None)
        if src and src.get("anti_triggers"):
            a["anti_triggers"] = list(src["anti_triggers"])
