# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：arch_metrics.py —— 架构度量基线（扇入扇出/耦合/复杂度/一致性/共改聚类）
# 来源：2026-10-20 架构评估 Phase 3 新建
# 提取：2026-10-20；口径见 _meta/架构评估/03-度量基线.md
# Author: EngineerRig
# 验证方式：纯函数（mccabe_of/parse_refs）+ 本机全量跑通；tests/test_arch_metrics.py
# 重跑命令：python _meta/arch_metrics.py [--json] [--cochange=80]
# 依赖：纯标准库（ast/re/subprocess）
# ──────────────────────────────────────────────────────────
"""架构度量基线。输出供《03-度量基线》引用，下届升级战役可直接对比。

指标：
  A 技能面：行数/字节/库内引用/外部绝对路径数
  B 构件扇入：被多少个技能引用的脚本或 snippet（>1 才列出）
  C 家族耦合：scripts 家族间 import/路径引用矩阵的对角外计数
  D 复杂度：McCabe（ast 分支计数）分布，列出 top10 与 _meta 治理件
  E 一致性：磁盘技能数 vs INDEX 登记数 vs 构件清单条目数
  F 共改聚类：git 历史中经常同一提交出现的文件对（隐式耦合）
  G 规模：目录文件数/行数
"""
import ast
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".zcode", "_template", "inbox", "归档"}

REF_RE = re.compile(r"(?:scripts|snippets|_meta)[\\/][A-Za-z0-9_\u4e00-\u9fff./\\-]+?\.(?:py|js|ps1|pyw|yaml|md)")
EXT_RE = re.compile(r"[A-Z]:[\\/]")


def walk_files(root, exts):
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        yield p


def read(p):
    return p.read_text(encoding="utf-8", errors="replace")


def mccabe_of(src, filename="<unknown>"):
    """McCabe 圈复杂度：1 + 判定节点数（if/for/while/except/with? 不含 with；and/or 不重复计）。"""
    try:
        tree = ast.parse(src, filename=filename)
    except SyntaxError:
        return None
    n = 1
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler,
                             ast.Assert, ast.IfExp)):
            n += 1
        elif isinstance(node, ast.BoolOp):
            n += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            n += 1 + len(node.ifs)
        elif isinstance(node, ast.Match):
            n += len(node.cases)
        elif isinstance(node, ast.match_case):
            pass
    return n


def parse_refs(text):
    refs = set()
    for m in REF_RE.finditer(text):
        refs.add(m.group(0).replace("\\", "/"))
    return refs


def skill_metrics():
    rows = []
    base = LIB / "skills"
    for d in sorted(base.iterdir()):
        sk = d / "SKILL.md"
        if not sk.is_file() or d.name in SKIP_DIRS:
            continue
        t = read(sk)
        rows.append({
            "skill": d.name,
            "lines": len(t.splitlines()),
            "bytes": len(t.encode("utf-8")),
            "refs": sorted(parse_refs(t)),
            "ext_paths": len(EXT_RE.findall(t)),
        })
    return rows


def fan_in(rows):
    cnt = Counter()
    for r in rows:
        for ref in r["refs"]:
            cnt[ref] += 1
    return [{"target": k, "fan_in": v} for k, v in cnt.most_common() if v > 1]


def family_coupling():
    fams = [d.name for d in (LIB / "scripts").iterdir() if d.is_dir()]
    matrix = defaultdict(Counter)
    for p in walk_files(LIB / "scripts", {".py", ".js"}):
        parts = p.relative_to(LIB / "scripts").parts
        if len(parts) < 2:
            continue
        src_fam = parts[0]
        t = read(p)
        for m in re.finditer(r"(?:from|import)\s+([A-Za-z0-9_.]+)", t):
            mod = m.group(1).split(".")[0]
            matrix[src_fam][mod] += 1
        for m in re.finditer(r"scripts[/\\]([A-Za-z0-9_]+)[/\\]", t):
            if m.group(1) in fams and m.group(1) != src_fam:
                matrix[src_fam][m.group(1)] += 1
    cross = sum(sum(c for k, c in row.items() if k in fams and k != fam) for fam, row in matrix.items())
    return fams, {f: dict(c) for f, c in matrix.items()}, cross


def complexity():
    py = list(walk_files(LIB / "scripts", {".py"})) + list(walk_files(LIB / "snippets", {".py"}))
    vals = []
    for p in py:
        c = mccabe_of(read(p), filename=str(p.relative_to(LIB)))
        if c is not None:
            vals.append((c, str(p.relative_to(LIB))))
    vals.sort(reverse=True)
    gov = [v for v in vals if v[1].startswith("_meta")]  # _meta 不在扫描面，保留位
    for p in sorted((LIB / "_meta").glob("*.py")):
        c = mccabe_of(read(p), filename=str(p.relative_to(LIB)))
        if c is not None:
            gov.append((c, str(p.relative_to(LIB))))
    gov.sort(reverse=True)
    nums = sorted(v[0] for v in vals) or [0]
    return {
        "n": len(vals),
        "max": nums[-1],
        "median": nums[len(nums) // 2],
        "over_20": sum(1 for v in nums if v > 20),
        "over_40": sum(1 for v in nums if v > 40),
        "top10": vals[:10],
        "governance": gov[:12],
    }


def consistency(rows):
    idx = read(LIB / "INDEX.md")
    mentioned = sum(1 for r in rows if r["skill"] in idx)
    manifest = LIB / "_meta" / "架构评估" / "构件清单.yaml"
    man_ids = len(re.findall(r"^\s+- id:", read(manifest), re.M)) if manifest.exists() else 0
    return {
        "skills_on_disk": len(rows),
        "skills_in_index": mentioned,
        "manifest_ids": man_ids,
        "index_chars": len(idx),
    }


def cochange(n=80):
    r = subprocess.run(["git", "-c", "core.quotepath=false", "log", f"-n{n}",
                        "--pretty=format:@@%H", "--name-only"],
                       cwd=LIB, capture_output=True, text=True, encoding="utf-8", errors="replace")
    commits, cur = [], None
    for line in r.stdout.splitlines():
        if line.startswith("@@"):
            cur = set()
            commits.append(cur)
        elif line.strip() and cur is not None:
            cur.add(line.strip())
    pair = Counter()
    for fs in commits:
        fs = sorted(f for f in fs if f and not f.startswith(("node_modules", ".zcode")))
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                pair[(fs[i], fs[j])] += 1
    return [{"a": a, "b": b, "times": c} for (a, b), c in pair.most_common(12) if c >= 2]


def scale():
    out = {}
    for name, exts in (("skills", {".md", ".py", ".pyw"}), ("scripts", {".py", ".js", ".ps1", ".pyw"}),
                       ("snippets", {".py", ".js"}), ("tests", {".py"}),
                       ("_meta", {".md", ".py", ".yaml", ".txt"})):
        root = LIB / name
        files = list(walk_files(root, exts))
        lines = sum(len(read(p).splitlines()) for p in files)
        out[name] = {"files": len(files), "lines": lines}
    return out


def build():
    rows = skill_metrics()
    fams, coupling, cross = family_coupling()
    return {
        "version": "2026-10-20",
        "scale": scale(),
        "skills": {r["skill"]: {"lines": r["lines"], "bytes": r["bytes"],
                                "internal_refs": len(r["refs"]), "ext_paths": r["ext_paths"]}
                   for r in rows},
        "skill_summary": {
            "count": len(rows),
            "avg_lines": round(sum(r["lines"] for r in rows) / max(1, len(rows)), 1),
            "zero_internal_refs": [r["skill"] for r in rows if not r["refs"]],
            "ext_paths_total": sum(r["ext_paths"] for r in rows),
        },
        "fan_in_gt1": fan_in(rows),
        "families": fams,
        "family_matrix": coupling,
        "cross_family_refs": cross,
        "complexity": complexity(),
        "consistency": consistency(rows),
        "cochange_top": cochange(),
    }


def render(d):
    L = []
    L.append(f"架构度量基线 {d['version']}")
    L.append("== 规模 ==")
    for k, v in d["scale"].items():
        L.append(f"  {k:<9} {v['files']:>4} 文件 {v['lines']:>6} 行")
    s = d["skill_summary"]
    L.append("== 技能 ==")
    L.append(f"  技能数 {s['count']}，均值 {s['avg_lines']} 行，外部绝对路径合计 {s['ext_paths_total']}")
    L.append(f"  零库内引用: {', '.join(s['zero_internal_refs']) or '无'}")
    L.append("== 扇入 >1 ==")
    for x in d["fan_in_gt1"] or []:
        L.append(f"  {x['fan_in']} × {x['target']}")
    L.append("== 家族耦合 ==")
    fams = d["families"]
    L.append(f"  家族 {len(fams)} 个；跨家族引用计数 {d['cross_family_refs']}")
    for f, row in sorted(d["family_matrix"].items()):
        cross = {k: v for k, v in row.items() if k in fams and k != f}
        if cross:
            L.append(f"  {f} → " + ", ".join(f"{k}×{v}" for k, v in sorted(cross.items(), key=lambda x: -x[1])))
    c = d["complexity"]
    L.append("== 复杂度（McCabe）==")
    L.append(f"  n={c['n']} max={c['max']} median={c['median']} >20:{c['over_20']} >40:{c['over_40']}")
    for v, p in c["top10"]:
        L.append(f"  {v:>4} {p}")
    L.append("  -- 治理件 --")
    for v, p in c["governance"]:
        L.append(f"  {v:>4} {p}")
    k = d["consistency"]
    L.append("== 一致性 ==")
    L.append(f"  磁盘技能 {k['skills_on_disk']} / INDEX 提及 {k['skills_in_index']} / 构件清单 id {k['manifest_ids']}")
    L.append(f"  INDEX 体积 {k['index_chars']} 字符")
    L.append("== 共改聚类（隐式耦合，近 80 提交）==")
    for x in d["cochange_top"]:
        L.append(f"  {x['times']}× {x['a']} ↔ {x['b']}")
    return "\n".join(L)


GOVERNANCE_LINE_LIMITS = {
    "_meta/asset_cli.py": 1350,
    "_meta/hub/tasks.py": 900,
    "_meta/hub/scorer.py": 450,
    "_meta/hub/dashboard.py": 450,
    "_meta/hub/indexer.py": 300,
    "_meta/hub/ingest.py": 250,
    "_meta/gate_check.py": 400,
    "_meta/export_public.py": 300,
    "_meta/session_close.py": 420,
    "_meta/check_paths.py": 380,
    "_meta/hooks/agent_hooks.py": 420,
}

NET_MODULES = {"urllib", "http", "socket", "ftplib", "smtplib", "ssl", "requests", "httpx", "aiohttp"}
ALLOWED_THIRD_PARTY = {
    "_meta/boundary_replay.py": {"yaml"},
    "_meta/fetch_spec_pdfs.py": {"requests"},
    "_meta/gen_index_from_yaml.py": {"yaml"},
    "_meta/gen_rules_from_yaml.py": {"yaml"},
    "_meta/mine_minglu_to_yaml.py": {"yaml"},
}
# session_close.py 与 hub/tasks.py 仅使用 socket.gethostname() 识别本机主机名，无网络通信
REVIEWED_NET_SOURCES = {
    "_meta/fetch_spec_pdfs.py": {"requests", "urllib"},
    "_meta/session_close.py": {"socket"},
    "_meta/hub/tasks.py": {"socket"},
}


def check_line_limits(root=LIB):
    """核心治理件防反弹基线检查：防止再次长出大泥球。返回违规列表（空=全过）。"""
    errs = []
    for rel, cap in GOVERNANCE_LINE_LIMITS.items():
        p = Path(root) / rel
        if p.is_file():
            n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
            if n > cap:
                errs.append(f"{rel}: {n} 行 > 基线上限 {cap}")
    return errs


def check_boundary_imports(root=LIB):
    """AST 静态边界守卫：拦截未登记第三方依赖与未审计网络能力 import。"""
    root_p = Path(root)
    files = sorted({
        *root_p.glob("_meta/*.py"),
        *root_p.glob("_meta/hub/*.py"),
        *root_p.glob("_meta/hooks/*.py"),
    })
    local_mods = {"_meta", "hub"} | {p.stem for p in files}
    stdlib = sys.stdlib_module_names
    errs = []
    for p in files:
        if not p.is_file():
            continue
        rel = str(p.relative_to(root_p)).replace("\\", "/")
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"), filename=rel)
        except SyntaxError as exc:
            errs.append(f"{rel}: Python 语法错误（{exc.msg}）")
            continue
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mods.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods.add(node.module.split(".")[0])
        allowed_tp = ALLOWED_THIRD_PARTY.get(rel, set())
        allowed_net = REVIEWED_NET_SOURCES.get(rel, set())
        for mod in sorted(mods):
            if mod not in stdlib and mod not in local_mods and mod not in allowed_tp:
                errs.append(f"{rel}: 未登记第三方依赖 import {mod}")
            elif mod in NET_MODULES and mod not in allowed_net:
                errs.append(f"{rel}: 未审计网络能力 import {mod}（须登记 REVIEWED_NET_SOURCES）")
    return errs


def main():
    if "--check" in sys.argv:
        errs = check_line_limits(LIB) + check_boundary_imports(LIB)
        if errs:
            for e in errs:
                print("✗", e)
            return 1
        print("✓ 治理件行数基线与静态边界审计通过")
        return 0
    d = build()
    if "--json" in sys.argv:
        print(json.dumps(d, ensure_ascii=False, indent=2))
    else:
        print(render(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
