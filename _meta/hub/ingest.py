# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：ingest.py —— 收件捕获/反馈日志的纯逻辑核（判定/查重/路径/哈希）
# 来源：2026-11-16 复杂度整改（Antigravity 实测 asset_cli.py McCabe=436 全库最高，
#     纯计算体外移 _meta/hub/ 子包）
# 提取：2026-11-16（自 _meta/asset_cli.py 收件/日志辅助函数体逐行原样外移）
# Author: EngineerRig
# 验证方式：tests/test_ingest_capture.py + tests/test_feedback_loop.py（收件/闭环全链路）；
#     行为保真由全量 pytest 兜底
# 重跑命令：python -m pytest -q tests/test_ingest_capture.py tests/test_feedback_loop.py
# 依赖：纯标准库；不 import asset_cli（单向被依赖）；路径/盐值/开关均显式收参
# ──────────────────────────────────────────────────────────
"""收件/日志纯逻辑核（2026-11-16 自 asset_cli 外移，行为逐行保真）。

纪律：函数显式收参返回（目录路径/盐值/扩展名白名单/跳过目录集合），
不读 asset_cli 模块全局与环境变量——环境开关（ASSET_HUB_*）由薄壳判定后传参。
"""
import hashlib
import hmac
import importlib.util
import json
import os
import re
from pathlib import Path

_SKIP_SCRIPT_PREFIXES = ("_", "~$", "test_", "probe", "recon", "wave", "debug", "tmp", "scratch")
_SKIP_DOC_STEMS = {"readme", "plan", "todo", "handoff", "changelog", "timeline", "next_steps", "setup", "notes",
                   "summary", "context", "project_context", "selection", "deviations", "coordination", "lessons", "portable", "prompt_for_agent"}
_DOC_REUSE_RE = re.compile(r"^---\s*\r?\nname:|资产卡|Asset Card|触发词[:：]|Triggers?[:：]", re.I | re.M)
_SCORER = None


def _load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scorer():
    global _SCORER
    if _SCORER is None:
        _SCORER = _load_mod("_hub_scorer_for_ingest", Path(__file__).with_name("scorer.py"))
    return _SCORER


def ingest_reason(p, exts, skip):
    """外部产出是否值得收进收件箱（保守判定，宁缺勿滥）。
    exts=扩展名白名单；skip=跳过目录集合（temp 开关由调用方决定后传入）。"""
    try:
        if not p.is_file():
            return None
        suf = p.suffix.lower()
        if suf not in exts:
            return None
        if any(part.lower() in skip for part in p.parts):
            return None
        nl = p.name.lower()
        if nl.startswith(_SKIP_SCRIPT_PREFIXES) or nl.endswith((".tmp", ".bak")):
            return None
        size = p.stat().st_size
        if size < 1024 or size > 200 * 1024:
            return None
        text = p.read_text(encoding="utf-8", errors="ignore")
        if suf in {".py", ".ps1", ".pyw"}:
            signals = [s for s in ("argparse", "sys.argv", "def main", "__main__", "param(")
                       if s in text]
            if not signals:
                return None
            return "可执行入口:" + ",".join(signals[:2])
        if suf == ".md":
            if p.stem.lower() in _SKIP_DOC_STEMS:
                return None
            if not _DOC_REUSE_RE.search(text[:4000]):
                return None
            return "可复用文档"
        return "可执行脚本"
    except OSError:
        return None


def inbox_exists(roots, src, sha):
    """同源同哈希查重：roots=(收件箱, 暂存区) 下 meta 的 source+sha1 命中即已存在。
    返回已存在的产出文件 Path（去 .meta.json 后缀），无则 None。"""
    for root in roots:
        if not root.exists():
            continue
        for meta in root.rglob("*.meta.json"):
            try:
                d = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if d.get("source") == str(src) and d.get("sha1") == sha:
                return meta.with_name(meta.name[:-len(".meta.json")])
    return None


def find_representative(inbox, src, session):
    """同源同会话且仍待蒸馏的代表版本（review Q5 止血：不再 -1..-N 复制堆积）。"""
    if not session or not inbox.exists():
        return None
    for meta in inbox.rglob("*.meta.json"):
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("source") == str(src) and d.get("session") == session and d.get("status") == "待蒸馏":
            return meta.with_name(meta.name[:-len(".meta.json")])
    return None


def recent_keys(log_path, session, limit=300):
    """find.jsonl 里本会话已注入的去重键集合（review Q2：同会话相同查询+资产不重复注入）。
    存在性/NOLOG 守卫由薄壳负责，本函数只做解析。"""
    keys = set()
    for l in log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]:
        try:
            d = json.loads(l)
        except ValueError:
            continue
        if d.get("event") == "search" and d.get("session") == session and d.get("dedup_key"):
            keys.add(d["dedup_key"])
    return keys


def same_file(a, b):
    """同文件判定（P2-1）：normcase 后路径相等，或分段对齐的路径尾部匹配
    （相对 asset_path 是绝对读路径的尾段也算同文件；不在尾部匹配前做 abspath——
    相对路径会被 CWD 拼出多余中段，尾段反而对不上）。"""
    def _prep(s):
        return os.path.normcase(str(s)).replace(chr(92), "/").rstrip("/")
    try:
        ra, rb = _prep(a), _prep(b)
        if ra == rb or _prep(os.path.abspath(str(a))) == _prep(os.path.abspath(str(b))):
            return True
        sa, sb = ra.split("/"), rb.split("/")
        n = min(len(sa), len(sb))
        return bool(n) and sa[-n:] == sb[-n:]
    except (TypeError, ValueError, OSError):
        return False


def triage_suggest(src, items):
    """来源簇机器建议（review Q5：inbox 从逐件通读降为按簇裁决的辅助判定）。"""
    s = str(src).replace("\\", "/").lower()
    n = len(items)
    if any(k in s for k in (".zcode", ".kimi-code", "appdata")):
        return "宿主缓存类→仅归档/弃用"
    if n >= 10:
        return f"高频迭代（{n}件）→蒸馏最新版进 canonical，其余归档"
    if n >= 3:
        return "多版本→保留最新，其余归档"
    return "常规→按价值裁决"


def qhash(salt, q):
    """查询加盐哈希（review Q3 纪律：日志只存哈希绝不存原文）。盐由薄壳的 _salt() 传入。"""
    return hmac.new(salt, _scorer().norm(q).encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace 原子落盘（防并行会话读到半截 JSON/YAML）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def aggregate_retro_proofs(recs):
    """聚合 retro.jsonl 重复偏差（≥2 次）为证据链条目：{deviation, count, proofs}。"""
    groups = {}
    for r in recs:
        d = re.sub(r"\s+", " ", (r.get("deviation") or "")).strip()
        if not d or d.lower() == "none" or d == "无":
            continue
        g = groups.setdefault(d[:60], {"deviation": d[:60], "count": 0, "proofs": []})
        g["count"] += 1
        tag = f"{str(r.get('ts', '?'))[:10]}@{str(r.get('session') or '-')[:12]}"
        if tag not in g["proofs"]:
            g["proofs"].append(tag)
    return sorted((v for v in groups.values() if v["count"] >= 2), key=lambda x: (-x["count"], x["deviation"]))


def merge_retro_debts(existing_text, today, items):
    """将重复偏差 proof count 原地更新或追加至待改进文本，返回 (new_text, changed, promotions)。"""
    lines, changed = (existing_text or "# 待改进队列（retro 环产出）\n").splitlines(), 0
    for it in items:
        badge = f"[proof={it['count']} | 证据:{','.join(it['proofs'][-3:])}]"
        row = f"- {today} | 重复偏差 | {it['deviation']} {badge} | 处置:升格为闸/技能或归档 | open"
        idx = next((i for i, l in enumerate(lines)
                    if "| 重复偏差 |" in l and it["deviation"][:35] in l and not l.rstrip().endswith("| done")), None)
        if idx is None or lines[idx] != row:
            lines.append(row) if idx is None else lines.__setitem__(idx, row)
            changed += 1
    return "\n".join(lines).rstrip() + "\n", changed, [it for it in items if it["count"] >= 3]


def redact_staging_bytes(data: bytes, filename: str, forbidden_re, secret_res, text_exts=None):
    """对隔离区可复用文本执行模式脱敏，返回 (redacted_bytes|None, hit_count)。禁用文件/二进制不放行。"""
    exts = text_exts or {".py", ".js", ".ps1", ".pyw", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".sh"}
    if forbidden_re.search(filename) or Path(filename).suffix.lower() not in exts:
        return None, 0
    for enc in ("utf-8", "gb18030"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            text = None
    if not text:
        return None, 0
    hits = 0
    for pat in secret_res:
        text, n = pat.subn("<REDACTED>", text)
        hits += n
    return (text.encode(enc), hits) if hits else (None, 0)


def rescue_staging(staging_dir: Path, inbox_dir: Path, gate_path: Path, day: str) -> int:
    """扫描隔离区并尝试脱敏救回：仅当替换后二次预扫 0 告警时才移入 inbox。"""
    if not staging_dir.exists() or not gate_path.is_file():
        return 0
    g, rescued = _load_mod("_asset_gate_redact", gate_path), 0
    for mp in sorted(staging_dir.rglob("*.meta.json")):
        src = mp.with_name(mp.name[:-len(".meta.json")])
        try:
            md, raw = json.loads(mp.read_text(encoding="utf-8")), src.read_bytes()
        except (OSError, ValueError):
            continue
        red, hits = redact_staging_bytes(raw, src.name, g.FORBIDDEN, g.SECRETS, getattr(g, "TEXT_EXTS", None))
        if not red or g._scan_bytes(src.name, red):
            continue
        dest = inbox_dir / (str(md.get("captured", ""))[:10] or day)
        dest.mkdir(parents=True, exist_ok=True)
        dst, n = dest / src.name, 1
        while dst.exists():
            dst, n = dest / f"{src.stem}-{n}{src.suffix}", n + 1
        dst.write_bytes(red)
        md.update({"status": "待蒸馏", "redacted": True, "redacted_hits": hits, "sha1": hashlib.sha1(red).hexdigest(), "size": len(red), "flags": []})
        atomic_write_text(dst.with_name(dst.name + ".meta.json"), json.dumps(md, ensure_ascii=False, indent=1))
        src.unlink(missing_ok=True)
        mp.unlink(missing_ok=True)
        rescued += 1
    return rescued

