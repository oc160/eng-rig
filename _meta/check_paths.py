# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：check_paths.py —— 资产库自检：索引路径存在性 + 技能 frontmatter 校验
# 来源：2026-10-02 真实测试战役新增（长期维护件）
# 提取：2026-10-20 架构评估止血：截断误报与"末段试探"假阴性分离为 missing/suspect 两级
#       2026-11-02 W0-1：inbox 在途件按 captured 年龄分级（≤14 天降警告，超龄恢复拦截），
#       取代 9 条手工 inbox 白名单（SKIP_PATH_GATE 逃生通道不得常态化——已 5 次留痕绕行）
# Author: EngineerRig
# 验证方式：tests/test_check_paths_classify.py（纯函数负向用例）+ 本机全量零红
# 重跑命令：python _meta/check_paths.py
# 依赖：纯标准库
# ──────────────────────────────────────────────────────────
"""资产库自检。退出码：0=全部通过，1=存在失效路径，2=技能校验失败。

路径判定三级（judge）：
  ok       存在
  missing  不存在，且不像被解析截断 → 计入失败
  suspect  不存在，但匹配点后紧跟空格/+/{ 且父目录存在 → 疑似截断，仅告警

inbox 在途降级（W0-1）：missing 判定落在 _meta/inbox 未蒸馏件内时，按伴生 .meta.json 的
captured 年龄分级——≤14 天降 suspect（在途警告）；>14 天维持 missing（暂存区不得成为安全飞地）。

为什么分两级：散文里常见 "C:/Program Files"（空格）、"喀克24+700"（加号）会让正则提前收尾；
但"父目录存在就放过"会把真实悬空叶子（如 D:/RAGFlowBackup）一起吞掉——2026-10-20 实测放过 18 条。
现改为：只有"后接可疑字符且后续仍有路径延续"才算截断。
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent

# 路径字符集：遇到空白/引号/常见标点/括号/花括号即止
PATH_RE = re.compile(r"[A-Z]:[\\/](?:[^\s<>\"'|,;+)\]}`{}（），。；：…]+[\\/])*[^\s<>\"'|,;+)\]}`{}（），。；：…]*")
MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\n]+)\)")
REL_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_\\/:])((?:scripts|snippets|_meta)[\\/][A-Za-z0-9_\u4e00-\u9fff./\\-]+?\.(?:pyw|py|js|ps1|yaml|md)(?!\w))"
)
SKIP_TOKENS = ("://",)          # URL
SKIP_FILES = {LIB / "_meta" / "归档" / "原地路径救活日志.md",   # 历史记录，路径本就失效（2026-10 归档后修正）
              LIB / "_meta" / "整库整理工作流.md",      # 流程文档，含示例性提及
              LIB / "_meta" / "派单模板-原始会话知识萃取.md",  # 派单模板，目标目录属未来交付物
              }
# 历史档案目录：内容是某时刻的原样留存，路径按定义会随外部目录变动而失效（同 原地路径救活日志 口径）
SKIP_DIRS_HISTORICAL = {LIB / "_meta" / "原始需求" / "记忆快照"}
SKIP_DIRS = {"_template", ".agents"}       # 技能模板，占位名合法
SKIP_PATHS = set()              # Optional local path exceptions


def extract_paths(text):
    """产出 (start, path, end)：end 用于判断匹配点之后紧跟什么字符。"""
    for m in PATH_RE.finditer(text):
        p = m.group(0).rstrip("\\/.")
        p = p.split("｜")[0].rstrip("\\/.")   # 剥离资产卡行尾 ｜提取/｜验证 等栏位后缀
        # 顿号歧义：既可能是列表分隔（D:/a、D:/b），也可能是目录名（项目建议书、实施方案）。
        # 仅当顿号后紧跟盘符时按列表截断。
        while "、" in p:
            i = p.index("、")
            if re.match(r"[A-Z]:", p[i + 1:i + 3]):
                p = p[:i]
            else:
                break
        if len(p) < 8 or any(t in p for t in SKIP_TOKENS):
            continue
        yield m.start(), p, m.start() + len(p)


def truncation_suspect(text, end):
    """匹配点后是否像被解析器截断。

    '+'、'{' 在真实路径里可能出现、却不是散文里的合法终止符 → 一律视为截断。
    空格最常见（"Program Files"），但空格也是正常终止符 → 要求后续仍出现"某段/"才算延续。
    """
    if end >= len(text):
        return False
    c = text[end]
    if c in "+{":
        return True
    if c in " \t":
        return bool(re.match(r"[ \t]*[^\s,;，。；、)）\]】`|]+[/\\]", text[end:end + 120]))
    return False


def judge(norm, text, end, exists):
    """三级判定（exists 可注入，便于纯函数测试）。"""
    if exists(norm):
        return "ok"
    parent = str(Path(norm).parent).replace("\\", "/")
    if truncation_suspect(text, end) and exists(parent):
        return "suspect"
    return "missing"


# inbox 在途件年龄分级（v1.2，2026-11-16 Antigravity 评审 P0-1 修正）：
# W0-1 原设计"超 14 天恢复硬拦"实锤反噬——3 份 10-28/29 在途件满龄后拖红全库第四闸，
# 连续 6 次无关提交被迫 SKIP_PATH_GATE=1（同步门禁卡异步积压=门禁事实停摆）。
# 现口径：同步闸对 inbox 在途件一律警告不拦；--strict-inbox（异步检查用）恢复超龄报错。
INBOX_MAX_AGE_DAYS = 14
_STRICT_INBOX = False              # 同步闸默认警告口径；--strict-inbox 时超龄 inbox 件恢复报错


def inbox_age_ok(captured_iso, now=None):
    """在途豁免是否成立：captured 距 now ≤ INBOX_MAX_AGE_DAYS（now 可注入，纯函数）。

    解析失败按 False（超龄拦截）——meta 在场却读不出日期属结构故障，宁可拦下逼人看。
    """
    if not captured_iso:
        return False
    try:
        captured = datetime.fromisoformat(str(captured_iso).replace("Z", "+00:00"))
    except ValueError:
        return False
    now = now or datetime.now()
    if captured.tzinfo is not None and now.tzinfo is None:
        captured = captured.replace(tzinfo=None)   # 全库口径为本机时间，混用时去偏移
    return (now - captured).days <= INBOX_MAX_AGE_DAYS


def _inbox_in_transit(f, now=None, strict=False):
    """文档 f 是否属"未蒸馏在途件"：位于 _meta/inbox 且 captured 未超龄。

    无 meta 伴生件 → 视为在途（警告口径）：捕获三段流在个别手工放置场景下不产 meta，
    按"宁漏不误拦在途件"放行，靠 inbox-triage 对账兜底。
    inbox分诊_*.md 同属在途语义（原样引用捕获源路径，源删除后即 missing）——
    同步闸按报告 mtime 一律警告；strict 模式才按龄恢复严格（逼陈旧报告重生成或清源）。
    """
    now = now or datetime.now()
    parts = {p.lower() for p in f.parts}
    if {LIB.name, "_meta", "inbox"} <= parts:
        meta = f.with_name(f.name + ".meta.json")
        try:
            captured = json.loads(meta.read_text(encoding="utf-8")).get("captured", "")
        except (OSError, ValueError):
            return True                      # meta 读不出=结构故障，按在途放行，triage 兜底
        return inbox_age_ok(captured, now) if strict else True
    if "_meta" in parts and f.name.startswith("inbox分诊_") and f.suffix == ".md":
        try:
            return triage_report_intransit(datetime.fromtimestamp(f.stat().st_mtime), now, strict=strict)
        except OSError:
            return False
    return False


def triage_report_intransit(mtime, now, strict=False):
    """分诊报告在途判定（纯函数）：同步闸一律在途（警告）；strict 按龄判。"""
    return True if not strict else (now - mtime).days <= INBOX_MAX_AGE_DAYS


def check_docs():
    files = [LIB / "INDEX.md", LIB / "README.md"]
    files += [p for p in (LIB / "_meta").glob("*.md")]
    files += [p for p in (LIB / "成果").glob("*.md")]
    files += [p for p in (LIB / "scripts").rglob("*.md")]
    files += [p for p in LIB.rglob("*.md")
             if p.parent.name not in ("成果",) and ".agents" not in p.parts]
    seen, missing, suspect = set(), [], []
    for f in sorted(set(files)):
        if not f.exists() or ".git" in f.parts or "node_modules" in f.parts or f in SKIP_FILES:
            continue
        if any(hist in f.parents for hist in SKIP_DIRS_HISTORICAL):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for _, raw, end in extract_paths(text):
            norm = raw.replace("\\", "/")
            key = norm.lower()
            if key in seen:
                continue
            seen.add(key)
            if norm.lower() in SKIP_PATHS:
                continue
            target = norm
            if "*" in norm or "?" in norm:      # 通配符：查父目录
                target = str(Path(norm).parent)

            def _exists(p):
                try:
                    return Path(p).exists()
                except OSError:
                    # 无权限读取的路径(如 SYSTEM 的 systemprofile)无法判定,
                    # 不当作失效路径, 也避免整个检查器崩溃。
                    return True

            verdict = judge(target, text, end, _exists)
            if verdict == "missing" and _inbox_in_transit(f, strict=_STRICT_INBOX):
                # W0-1：未蒸馏在途件的外部工件引用降为警告（蒸馏入库后自动恢复严格）
                verdict = "suspect_intransit"
            if verdict == "missing" and "inbox过程件" in f.parts:
                # 2026-11-17 Phase1 归档件同语义：历史捕获记录的外部引用降警告
                verdict = "suspect_intransit"
            if verdict == "missing":
                missing.append((f.relative_to(LIB), norm))
            elif verdict.startswith("suspect"):
                suspect.append((f.relative_to(LIB), norm))
    return sorted(seen), missing, suspect


def check_skills():
    problems = []
    base = LIB / "skills"
    for d in sorted(base.iterdir()):
        if d.name in SKIP_DIRS:
            continue
        sk = d / "SKILL.md"
        if not sk.exists():
            problems.append(f"{d.name}: 缺 SKILL.md")
            continue
        t = sk.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", t, re.S)
        if not m:
            problems.append(f"{d.name}: frontmatter 缺失")
            continue
        fm = m.group(1)
        nm = re.search(r"^name:\s*(\S+)", fm, re.M)
        de = re.search(r"^description:\s*(.+?)(?=^\S|\Z)", fm, re.M | re.S)
        if not nm:
            problems.append(f"{d.name}: frontmatter 无 name")
        elif nm.group(1) != d.name:
            problems.append(f"{d.name}: name={nm.group(1)} 与目录名不一致")
        desc = (de.group(1).strip() if de else "")
        if len(desc) < 30:
            problems.append(f"{d.name}: description 过短({len(desc)}字)")
    # P3-7（Antigravity 首评）：技能防孤岛守卫——每个技能必须在 INDEX.md/INDEX-明细.md 登记
    idx_text = ""
    for f in ("INDEX.md", "INDEX-明细.md"):
        fp = LIB / f
        if fp.exists():
            idx_text += fp.read_text(encoding="utf-8", errors="replace")
    for d in sorted(base.iterdir()):
        if d.name in SKIP_DIRS or not (d / "SKILL.md").exists():
            continue
        if d.name not in idx_text:
            problems.append(f"{d.name}: 技能未在 INDEX 登记（孤岛，路由表不可达）")
    return problems


def check_internal_refs(root=LIB):
    """代码块感知的库内 Markdown 相对链接与脚本相对引用检查。"""
    root_p = Path(root)
    is_full_hub = (root_p / "_meta" / "手册.md").is_file()
    docs = []
    if (root_p / "skills").is_dir():
        docs.extend(sorted(p for p in (root_p / "skills").glob("*/SKILL.md") if "_template" not in p.parts))
    for name in ("INDEX.md", "README.md", "README_zh.md", "AGENTS.md", "_meta/AGENT-GUIDE.md"):
        cand = root_p / name
        if cand.is_file():
            docs.append(cand)
    missing = []
    seen = set()
    for mf in docs:
        rel_doc = str(mf.relative_to(root_p)).replace("\\", "/")
        fenced = False
        for line in mf.read_text(encoding="utf-8", errors="replace").splitlines():
            if re.match(r"^\s*(?:```|~~~)", line):
                fenced = not fenced
                continue
            if fenced:
                continue
            for m in MD_LINK_RE.finditer(line):
                raw = m.group(1).strip().strip("<>").split()[0]
                if not raw or raw.startswith(("#", "http://", "https://", "mailto:", "file://", "conversation://")):
                    continue
                if any(c in raw for c in "<>{}*$"):
                    continue
                tgt = raw.split("#", 1)[0].split("?", 1)[0]
                if not tgt:
                    continue
                if not (mf.parent / tgt).resolve().exists() and not (root_p / tgt).resolve().exists():
                    key = (rel_doc, raw)
                    if key not in seen:
                        seen.add(key)
                        missing.append(key)
            for m in REL_REF_RE.finditer(line):
                ref = m.group(1).replace("\\", "/")
                if any(x in ref for x in ("<", ">", "{", "}", "*", "$", "xxx", "示例", "foo", "demo")):
                    continue
                if ref.startswith("_meta/") and not is_full_hub:
                    continue
                if not (root_p / ref).exists() and not (mf.parent / ref).exists():
                    key = (rel_doc, ref)
                    if key not in seen:
                        seen.add(key)
                        missing.append(key)
    return missing


def main():
    global _STRICT_INBOX
    _STRICT_INBOX = "--strict-inbox" in sys.argv
    seen, missing, suspect = check_docs()
    print(f"扫描唯一盘符路径 {len(seen)} 条")
    if missing:
        print(f"✗ 失效路径 {len(missing)} 条：")
        for f, p in missing:
            print(f"  [{f}] {p}")
    else:
        print("✓ 路径全部有效")
    if suspect:
        print(f"! 降级为警告（疑似截断/inbox 在途件）{len(suspect)} 条：")
        for f, p in suspect[:10]:
            print(f"  [{f}] {p}")
        if len(suspect) > 10:
            print(f"  …另 {len(suspect) - 10} 条")
    rel_missing = check_internal_refs(LIB)
    if rel_missing:
        print(f"✗ 失效库内相对链接/引用 {len(rel_missing)} 条：")
        for f, p in rel_missing:
            print(f"  [{f}] {p}")
    else:
        print("✓ 库内相对链接与脚本引用全部有效")
    skills = check_skills()
    if skills:
        print(f"✗ 技能问题 {len(skills)} 个：")
        for s in skills:
            print("  " + s)
    else:
        print("✓ 技能 frontmatter 全部通过")
    return 1 if (missing or rel_missing) else (2 if skills else 0)


if __name__ == "__main__":
    sys.exit(main())
