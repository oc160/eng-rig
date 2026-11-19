# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：health_check.py —— 每日体检（测试/路径/junction/数据格式/git 状态一条命令）
# 来源：2026-10-20 架构评估 P1（RK0/RK3/RK4 缓解）
# 提取：2026-10-20
# Author: EngineerRig
# 验证方式：纯函数 + 本机全绿跑通；tests/test_health_check.py
# 重跑命令：python _meta/health_check.py [--quick] [--json] [--snapshot]
# 依赖：标准库；pytest（--quick 时可省）
# ──────────────────────────────────────────────────────────
"""每日体检。退出码 0=全绿，1=有红项。

检查项：
  1 测试基线      pytest 默认层（pytest.ini 已排除 com/realdata）
  2 路径门禁      check_paths.py（退出码 + 输出末行）
  3 技能分发      两级 .agents/skills 的 junction 是否解析到本仓 skills/
  4 数据格式      规范版本表是否有未加引号的日期（外部 cron 写入的已知回归点）
  5 git 卫生      工作树是否脏、是否领先 hub 未推

设计原则：只报告，不自动修——修复留给人工/门禁，避免"体检顺手改坏"。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
SKILLS = LIB / "skills"


def _load_date_line():
    gp = LIB / "_meta" / "gate_check.py"
    if gp.is_file():
        import importlib.util
        sp = importlib.util.spec_from_file_location("_hc_gate_check", gp)
        mod = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(mod)
        if hasattr(mod, "DATE_LINE"):
            return mod.DATE_LINE
    return re.compile(r"^\s+(?:implemented|effective|issued):\s+\d{4}-\d{2}-\d{2}\s*$", re.M)


DATE_LINE = _load_date_line()


def run(cmd, cwd=LIB, timeout=300):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", shell=(os.name == "nt"),
                          timeout=timeout)


def check_tests():
    r = run([sys.executable, "-m", "pytest", "-q"])
    tail = [l for l in r.stdout.strip().splitlines() if l.strip()][-1:] or ["(无输出)"]
    return r.returncode == 0, tail[0]


def check_paths():
    r = run([sys.executable, str(LIB / "_meta" / "check_paths.py")])
    lines = [l.strip() for l in r.stdout.splitlines() if "路径" in l]
    detail = lines[0] if lines else "(无路径输出)"
    bad = [l for l in r.stdout.splitlines() if l.strip().startswith("✗")]
    return r.returncode == 0, detail + (f"；{bad[0]}" if bad else "")


def check_junctions():
    """两级技能目录必须解析到本仓 skills/<name>（兼容派生仓与主仓同机并存场景）。"""
    bad = []
    other_hub = []
    targets = [LIB.parent / ".agents" / "skills", Path.home() / ".agents" / "skills"]
    names = [d.name for d in SKILLS.iterdir() if d.is_dir() and d.name != "_template"]
    for base in targets:
        for n in names:
            link = base / n
            try:
                real = link.resolve()
            except OSError:
                bad.append(f"{base}\\{n} 不存在")
                continue
            if not (real / "SKILL.md").is_file():
                bad.append(f"{base}\\{n} 断链")
            elif real != (SKILLS / n).resolve():
                other_hub.append(f"{base}\\{n}")
    local_dir = LIB / ".agents" / "skills"
    local_ok = local_dir.exists() and local_dir.resolve() == SKILLS.resolve()
    if not bad and not other_hub:
        return True, f"{len(names)} 技能 × 2 级"
    if not bad and other_hub and local_ok:
        return None, "本仓 .agents/skills 正常（工作区/用户级链指向主仓）"
    all_bad = bad + [f"{x} 指向他仓" for x in other_hub]
    return False, f"{len(names)} 技能 × 2 级；异常 {len(all_bad)}：{all_bad[:3]}"


def yaml_date_violations(text):
    """未加引号的 YAML 日期行（会被解析成 date 对象，破坏 schema 约定）。"""
    return DATE_LINE.findall(text)


def check_yaml_dates():
    p = LIB / "_meta" / "规范版本表.yaml"
    if not p.exists():
        return True, "无规范表（跳过）"
    hits = yaml_date_violations(p.read_text(encoding="utf-8", errors="replace"))
    return len(hits) == 0, f"未加引号日期 {len(hits)} 行" if hits else "日期格式正常"


def check_git():
    st = run(["git", "status", "--porcelain"]).stdout.strip().splitlines()
    dirty = [l for l in st if l.strip()]
    ahead = ""
    if "hub" in run(["git", "remote"]).stdout.split():
        ahead = run(["git", "rev-list", "--count", "hub/master..master"]).stdout.strip()
    msg = f"工作树 {'脏 ' + str(len(dirty)) + ' 项' if dirty else '干净'}"
    if ahead and ahead != "0":
        msg += f"；领先 hub {ahead} 提交"
    # 白天干活时工作树自然是脏的——只作提示，不判红；收尾判定交给 session_close
    ok = True if not dirty and ahead in ("", "0") else None
    return ok, msg


CHECKS = [
    ("测试基线", check_tests),
    ("路径门禁", check_paths),
    ("技能分发", check_junctions),
    ("数据格式", check_yaml_dates),
    ("git 卫生", check_git),
]

MARKER = LIB / "_meta" / ".last_health_check"


def snapshot_line():
    """W1-7 简版快照：一行 markdown 供架构文档"实况数字"粘贴（消手工采集漂移）。"""
    from datetime import date as _d
    n_idx = n_inbox = n_quar = n_open = n_retro = 0
    idx = LIB / "_meta" / "index" / "asset-index.json"
    if idx.exists():
        try:
            n_idx = len(json.loads(idx.read_text(encoding="utf-8"))["assets"])
        except (OSError, ValueError, KeyError):
            pass
    n_inbox = sum(1 for _ in (LIB / "_meta" / "inbox").rglob("*.meta.json"))
    staging = LIB.parent / "_inbox_staging"
    n_quar = sum(1 for _ in staging.rglob("*.meta.json")) if staging.exists() else 0
    q = LIB / "_meta" / "待改进.md"
    if q.exists():
        n_open = sum(1 for l in q.read_text(encoding="utf-8", errors="ignore").splitlines()
                     if l.strip().startswith("- ") and l.rstrip().endswith("open"))
    rj = LIB / "_meta" / "retro" / "retro.jsonl"
    if rj.exists():
        n_retro = sum(1 for l in rj.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip())
    return (f"- 实况快照（{_d.today()}）：索引 {n_idx} 条；收件箱 {n_inbox} 件、隔离 {n_quar} 件；"
            f"待改进 open {n_open} 条；retro {n_retro} 条")


def main():
    if "--snapshot" in sys.argv:
        print(snapshot_line())
        return 0
    quick = "--quick" in sys.argv
    as_json = "--json" in sys.argv
    rows, fails = [], 0
    for name, fn in CHECKS:
        if quick and name == "测试基线":
            rows.append({"check": name, "ok": None, "detail": "quick 跳过"})
            continue
        try:
            ok, detail = fn()
        except Exception as e:                       # 体检自身不许炸
            ok, detail = False, f"检查异常：{e}"
        rows.append({"check": name, "ok": ok, "detail": detail})
        if ok is False:                 # None = 提示级，不计失败
            fails += 1
    # W1-4：体检跑完（含红项，排除崩溃）落标记——SessionStart 钩子据此检测漏跑
    try:
        from datetime import date as _d
        MARKER.write_text(str(_d.today()), encoding="utf-8")
    except OSError:
        pass
    if as_json:
        print(json.dumps({"ok": fails == 0, "checks": rows}, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            mark = "✓" if r["ok"] else ("-" if r["ok"] is None else "✗")
            print(f"  {mark} {r['check']:<6} {r['detail']}")
        print(f"体检结论：{'全绿' if fails == 0 else str(fails) + ' 项红'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
