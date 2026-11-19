# -*- coding: utf-8 -*-
"""
session_close —— 会话收尾自检（一条命令代替"收尾三问"判断题）。

用法：python _meta/session_close.py   （在 成果整理/ 内运行；或任意 cwd，自动定位）

检查项（对应输出节）：
  1/6 库内未收尾变更：git status 清单（含 scripts/snippets 新脚本缺资产卡点名）
  2/6 技能 junction 存活：工作区级 + 用户级每条链是否存在且指向的 SKILL.md 可读
  3/6 绕路痕迹：有删除痕迹时提示四级升级协议/能力边界台账
  4/6 复盘提示（retro）：环境摩擦三查；改不动的记 _meta/待改进.md（升级战役清零）
  5/6 远端落后检测：fetch hub 后本地领先/落后提示（GitHub 镜像存在时一并提示）
  6/6 结论
退出码：0=干净；1=有需处置项（按输出逐条处理）
"""
import importlib.util
import json as _json
import os
import socket
import subprocess
import sys
from datetime import date as _date, datetime as _dt
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
# 技能清单从目录自动派生（review slim：消灭手动同步）
SKILLS = sorted(d.name for d in (LIB / "skills").iterdir()
                if d.is_dir() and d.name != "_template" and (d / "SKILL.md").exists())
WORKSPACE_SKILL_DIR = LIB.parent / ".agents" / "skills"
USER_SKILL_DIR = Path.home() / ".agents" / "skills"
SESSION_ARG = ""   # 可选 --session=<id>：retro 归属判定优先按 session 字段匹配（W1-1）


def run(cmd, cwd=LIB):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          shell=(os.name == "nt"), encoding="utf-8", errors="replace")


def sec(title):
    print(f"\n== {title} ==")


def check_git():
    fails = []
    st = run("git status --porcelain")
    lines = [l for l in st.stdout.splitlines() if l.strip()]
    if not lines:
        print("  ✓ 工作区干净")
    else:
        for l in lines:
            mark = " "
            f = l[3:].strip().strip('"')
            if f.startswith(("scripts/", "snippets/")) and f.endswith((".py", ".js", ".ps1", ".pyw")) \
                    and l.strip().startswith("??"):
                p = LIB / f
                if p.exists():
                    head = p.read_text(encoding="utf-8", errors="ignore")[:4000]
                    if "资产卡" not in head and "Asset Card" not in head:
                        mark = "✗"
                        fails.append(f"待入库脚本缺资产卡: {f}")
            print(f"  {mark} {l.strip()}")
        print("  → 处置：按归属精确 git add <路径> && git commit && git push hub（严禁 add -A，并行会话在场）")
    return fails


def check_data():
    """数据格式守卫：规范版本表的日期必须加引号（复用 gate_check.DATE_LINE 单源规则）。"""
    fails = []
    p = LIB / "_meta" / "规范版本表.yaml"
    if not p.exists():
        return fails
    import re
    date_re = None
    gp = LIB / "_meta" / "gate_check.py"
    if gp.is_file():
        try:
            sp = importlib.util.spec_from_file_location("_sc_gate_check", gp)
            mod = importlib.util.module_from_spec(sp)
            sp.loader.exec_module(mod)
            date_re = getattr(mod, "DATE_LINE", None)
        except Exception:
            date_re = None
    if date_re is None:
        date_re = re.compile(r"^\s+(?:implemented|effective|issued):\s+\d{4}-\d{2}-\d{2}\s*$", re.M)
    bad = date_re.findall(p.read_text(encoding="utf-8", errors="ignore"))
    if bad:
        print(f"  ✗ 规范版本表有 {len(bad)} 处未加引号日期（会被解析成 date 对象）→ 加引号后重跑测试")
        fails.append(f"规范版本表未加引号日期 {len(bad)} 处")
    else:
        print("  ✓ 规范版本表日期格式正常")
    return fails


def check_detours():
    """轻量绕路检测：工作树中有删除痕迹时提醒（B 系规则：失败现场采集）。
    检出时顺带输出四级升级协议（上下文提示，不靠 AI 记住 AGENTS.md）。"""
    r = run("git status --porcelain")
    dels = [l for l in r.stdout.splitlines() if l.strip().startswith((" D", "D"))]
    if dels:
        print(f"  ! 检测到 {len(dels)} 个删除痕迹——若是放弃方案/试错产物，考虑录入能力边界台账")
        print("  ↳ 库件不适用时的四级升级：改参 → 改 canonical(+测试) → 新写(+来源登记) → 现场绕行(不入库)")
        print("    禁止两极：硬套（改数据适配工具）和静默绕行（用了别的方案不记录）")
    return []


def check_retro_commit(session=None, hard=True):
    """retro 写后未提交 → 按归属分级（W1-1；W2-④ 修两个绕过口，review 必修⑤）。

    归属规则：给了 --session → **只按 session 字段判本会话**（date 不再覆盖归属——
    同日并行会话不再被误判）；未给 → 按行内 date==今天判（旧口径）。
    覆盖范围：`git diff HEAD`（staged+unstaged 一并看——只 add 不 commit 绕不过）。
    本会话＝硬失败+精确提交命令；并行遗留＝警告可代收账；hard=False（--quick）全警告。
    """
    warns = []
    rj = LIB / "_meta" / "retro" / "retro.jsonl"
    st = run(f'git status --porcelain -- "{rj.relative_to(LIB)}"').stdout.strip()
    if not st or not rj.exists():
        return []
    diff = run(f'git diff HEAD -- "{rj.relative_to(LIB)}"').stdout
    added = []
    for l in diff.splitlines():
        if l.startswith("+") and not l.startswith("+++"):
            body = l[1:].strip()
            if body.startswith("{"):
                try:
                    added.append(_json.loads(body))
                except ValueError:
                    pass
    if not added:
        return []
    today = str(_date.today())
    if session:
        mine = [r for r in added if r.get("session") == session]
    else:
        mine = [r for r in added if r.get("date") == today]
    others = [r for r in added if r not in mine]
    if others:
        ids = ", ".join(f'{r.get("session", "?")}@{r.get("date", "?")}' for r in others)
        print(f"  ! retro 有 {len(others)} 行并行会话遗留未提交（{ids}）→ 可代收账：")
        print(f"    git add _meta/retro/retro.jsonl && git commit -m \"retro：代收账({ids})\" && git push hub")
    if mine:
        msg = (f"retro：{mine[0].get('task', '')[:30]}" if mine else "retro")
        mark = "✗" if hard else "!"
        tailword = "写后必须提交，W1-1 硬失败" if hard else "quick 档仅提示，全量收尾时为硬失败"
        print(f"  {mark} 本会话 retro 已写未提交 {len(mine)} 行（{tailword}）→")
        print(f"    git add _meta/retro/retro.jsonl && git commit -m \"{msg}\" && git push hub")
        if hard:
            return ["本会话 retro 写后未提交"]
    return warns


def check_retro():
    """retro 环（借鉴 mattpocock/skills）：收尾时问"环境哪里让我变笨了"。
    小改当场做并 commit；改不动的记 _meta/待改进.md，升级战役清零。
    2026-10-27 评审升级：三查之外必须留一条结构化记录（retro.jsonl），
    周查重复发生率——"无需改变"也是合法结论。"""
    print("  ? 环境摩擦三查（有则处置，无则过）：")
    print("    a) 触发/导航：技能没命中、INDEX 场景速查缺行 → 当场补 description/加行")
    print("    b) 规则含糊：手册或 SKILL.md 哪句话让本次犹豫/返工 → 改那一句话并 commit")
    print("    c) 重复劳动：同一件事本轮手动做了第二遍 → 蒸馏入库或记待改进")
    rj = LIB / "_meta" / "retro" / "retro.jsonl"
    logged = False
    if rj.exists():
        try:
            logged = any(_json.loads(l).get("date") == str(_date.today())
                         for l in rj.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip())
        except ValueError:
            pass
    if not logged:
        print("  ✗ 今日尚无结构化 retro 记录（收尾强制项）→ python _meta/asset_cli.py record-retro "
              "--task=… --reused=<复用资产|不适用:原因> --deviation=… --produced=… "
              "&& python _meta/session_close.py（无改变也是合法结论，周查用 retro-stats）")
        return ["今日缺 retro 结构化记录"]
    q = LIB / "_meta" / "待改进.md"
    if q.exists():
        opens = [l for l in q.read_text(encoding="utf-8", errors="ignore").splitlines()
                 if l.strip().startswith("- ") and l.rstrip().endswith("open")]
        if opens:
            print(f"  ! 待改进队列 {len(opens)} 条 open → _meta/待改进.md（战役开战清零；"
                  f"超龄项跑 python _meta/asset_cli.py debts --aging）")
    return check_retro_commit(session=(SESSION_ARG or None))


def check_junctions():
    fails = []
    counts = []
    for label, base in [("工作区级", WORKSPACE_SKILL_DIR), ("用户级", USER_SKILL_DIR)]:
        ok_n = 0
        for s in SKILLS:
            d = base / s
            try:
                ok = (d / "SKILL.md").is_file()
            except OSError:
                ok = False
            if not ok:
                fails.append(f"junction 断链: {d}")
                print(f"  ✗ [{label}] {d}")
            else:
                ok_n += 1
        counts.append(f"{label} {ok_n}/{len(SKILLS)}")
    if not fails:
        print("  ✓ 技能链存活：" + "，".join(counts))
    return fails


def check_remotes():
    remotes = run("git remote").stdout.split()
    if "hub" in remotes:
        run("git fetch hub --quiet")
        ahead = run("git rev-list --count hub/master..master").stdout.strip()
        behind = run("git rev-list --count master..hub/master").stdout.strip()
        if ahead not in ("", "0"):
            print(f"  ✗ 领先 hub {ahead} 个提交未推送 → git push hub")
            return [f"hub 落后 {ahead} 提交"]
        print("  ✓ 已与 225 (hub) 同步")
        if behind not in ("", "0"):
            print(f"  ! hub 有 {behind} 个本地没有的提交（其他设备推的？）→ git pull")
        if "mirror" in remotes:
            print("  ! 外发纪律：主仓永不推 GitHub；mirror 远端仅限 export_public.py 白名单派生仓")
        return []
    print("  ! 无 hub 远端（新设备？跑 install.ps1 配置）")
    return []


def _commit_retro_file(msg_title):
    """仅提交 _meta/retro/retro.jsonl（不影响他人暂存区文件）。"""
    rel = "_meta/retro/retro.jsonl"
    st = run(f'git status --porcelain -- "{rel}"').stdout.strip()
    if not st:
        return
    add_r = subprocess.run(["git", "add", rel], cwd=LIB, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if add_r.returncode != 0:
        print(f"  ! retro 自动暂存失败：{add_r.stderr.strip()}")
        return
    msg = f"retro：{msg_title[:30]}" if msg_title else "retro：自动收尾"
    staged = [x for x in run("git diff --cached --name-only").stdout.splitlines() if x.strip()]
    cmd = ["git", "commit"]
    if any(x.replace("\\", "/") != rel for x in staged):
        cmd += ["--only", rel]
    cmd += ["-m", msg]
    cm = subprocess.run(cmd, cwd=LIB, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if cm.returncode == 0:
        print(f"  ✓ retro 已自动提交（{msg}）")
    else:
        print(f"  ! retro 自动提交未完成：{(cm.stderr or cm.stdout).strip()[:120]}")


def _auto_retro(argv):
    """原子收尾入口：--retro='task|reused|deviation|produced' 或 --task=...，写后自动提交 retro.jsonl。"""
    retro_spec = next((a.split("=", 1)[1] for a in argv if a.startswith("--retro=")), None)
    kv = {a[2:].split("=", 1)[0]: a[2:].split("=", 1)[1] for a in argv if a.startswith("--") and "=" in a}
    task = kv.get("task", "")
    reused = kv.get("reused", "")
    deviation = kv.get("deviation", "")
    produced = kv.get("produced", "")
    evidence = kv.get("evidence", "")
    if retro_spec is not None:
        parts = (retro_spec.split("|") + ["", "", "", ""])[:4]
        task = task or parts[0].strip()
        reused = reused or parts[1].strip()
        deviation = deviation or parts[2].strip()
        produced = produced or parts[3].strip()
    if task:
        cmd = [sys.executable, str(LIB / "_meta" / "asset_cli.py"), "record-retro", f"--task={task}"]
        for k, v in (("reused", reused), ("deviation", deviation), ("produced", produced),
                     ("evidence", evidence), ("session", SESSION_ARG)):
            if v:
                cmd.append(f"--{k}={v}")
        r = subprocess.run(cmd, cwd=LIB, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.stdout.strip():
            print("  " + r.stdout.strip())
        if r.returncode != 0 and r.stderr.strip():
            print("  ! record-retro 报错：" + r.stderr.strip()[:120])
        elif "--no-commit-retro" not in argv:
            _commit_retro_file(task)
    elif "--commit-retro" in argv:
        _commit_retro_file("")


def _load_tasks_mod():
    p = LIB / "_meta" / "hub" / "tasks.py"
    if not p.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_hub_tasks_close", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _handle_task_actions(argv):
    fails = []
    th = None
    for a in argv:
        if a.startswith("--task-done="):
            th = th or _load_tasks_mod()
            if th:
                tid = a.split("=", 1)[1].strip()
                ok, msg, _ = th.complete_task(th.tasks_dir(LIB), tid, lib_root=LIB)
                print("  " + msg)
                if not ok:
                    fails.append(f"任务验收未通过: {tid}")
        elif a.startswith("--task-handoff="):
            th = th or _load_tasks_mod()
            if th:
                spec = a.split("=", 1)[1]
                tid, _, note = spec.partition("|")
                ok, msg, _ = th.handoff_task(th.tasks_dir(LIB), tid.strip(), note=note.strip(), lib_root=LIB)
                print("  " + msg)
                if not ok:
                    fails.append(f"任务交接失败: {tid.strip()}")
    return fails


def check_tasks():
    """提示当前宿主持有的活跃任务胶囊（未完成需验收结项或交接释放租约）。"""
    th = _load_tasks_mod()
    if not th:
        return []
    tdir = th.tasks_dir(LIB)
    if not tdir.is_dir():
        return []
    host = os.environ.get("COMPUTERNAME") or socket.gethostname() or ""
    for p in sorted(tdir.glob("*.md")):
        t = th.parse_task_file(p)
        if not th.lease_active(t):
            continue
        if SESSION_ARG:
            mine = (t.get("session") == SESSION_ARG)
        else:
            owner = t.get("owner") or ""
            mine = (not owner) or (host and host in owner)
        if mine:
            tid = t.get("id", p.stem)
            print(f"  ! 本机仍有活跃认领任务：{tid} ({t.get('title', '')}) —— "
                  f"收尾可用 --task-done={tid} 或 --task-handoff=\"{tid}|交接说明\"")
    return []


def main():
    global SESSION_ARG
    quick = "--quick" in sys.argv
    SESSION_ARG = next((a.split("=", 1)[1] for a in sys.argv
                        if a.startswith("--session=")), "")
    _auto_retro(sys.argv[1:])
    fails = []
    fails += _handle_task_actions(sys.argv[1:])
    if quick:
        # W1-6 分级收尾：quick = git + 数据守卫 + junction + retro 提交提示（全警告档）；
        # 小改 quick、大改全量六节（绕路/远端/今日 retro 强制项只走全量）
        print("== quick 档（W1-6：git 收尾 + 数据守卫 + junction + retro 提交提示）==")
        fails += check_git()
        fails += check_data()
        check_tasks()
        fails += check_junctions()
        check_retro_commit(session=(SESSION_ARG or None), hard=False)
        return 1 if fails else 0
    sec("1/6 库内未收尾变更")
    fails += check_git()
    fails += check_data()
    fails += check_tasks()
    sec("2/6 技能 junction 存活")
    fails += check_junctions()
    sec("3/6 绕路痕迹")
    fails += check_detours()
    sec("4/6 复盘提示（retro）")
    fails += check_retro()
    sec("5/6 远端同步状态")
    fails += check_remotes()
    sec("6/6 结论")
    if fails:
        print(f"  ✗ {len(fails)} 项需处置：")
        for f in fails:
            print("   -", f)
        return 1
    print("  ✓ 全部通过：变更已收尾、技能链存活、远端同步")
    return 0


if __name__ == "__main__":
    sys.exit(main())
