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
_SKIP_DOC_STEMS = {
    "readme", "plan", "todo", "handoff", "changelog", "timeline", "next_steps",
    "setup", "notes", "summary", "context", "project_context", "selection",
    "deviations", "coordination", "lessons", "portable", "prompt_for_agent",
}
_DOC_REUSE_RE = re.compile(r"^---\s*\r?\nname:|资产卡|Asset Card|触发词[:：]|Triggers?[:：]", re.I | re.M)

_SCORER = None


def _scorer():
    """装载同目录 scorer（norm 规整共用；sys.path 无本目录，按文件位置装载）。"""
    global _SCORER
    if _SCORER is None:
        spec = importlib.util.spec_from_file_location(
            "_hub_scorer_for_ingest", Path(__file__).with_name("scorer.py"))
        _SCORER = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_SCORER)
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

