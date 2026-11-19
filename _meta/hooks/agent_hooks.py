# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：agent_hooks.py —— 宿主钩子：零口令路由 + 产出即入库
# 来源：2026-10-21 团队版（用户要求：无感使用，效率优先）
# 提取：2026-10-21
# Author: EngineerRig
# 验证方式：stdin 样例三事件冒烟；tests/test_agent_hooks.py
# 重跑命令：echo '{"prompt":"批量改报告格式"}' | python _meta/hooks/agent_hooks.py --event=prompt
# 依赖：纯标准库
# ──────────────────────────────────────────────────────────
"""宿主钩子（Claude Code / Claude Code / Antigravity 兼容）。

--event=start   SessionStart：一行说明 + 活跃任务胶囊断点注入，几乎无 token 成本
--event=prompt  UserPromptSubmit：自动识别任务号断点续接 + 资产检索注入（≤130 字）
--event=pre     PreToolUse：按任务胶囊 scope_globs 硬拦截他人锁区与越界写文件
--event=post    PostToolUse：文件写完后自动判定"可入库产出"→ 收件箱；读文件自动落 open 事件

无内容时静默退出 0；任何异常都静默（钩子不许打断会话）。
"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

LIB = Path(__file__).resolve().parents[2]
CLI = LIB / "_meta" / "asset_cli.py"
SKIP_PROMPTS = ("继续", "好", "可以", "行", "ok", "yes", "嗯", "对", "不用", "停")
WRITE_TOOLS = {
    "write_file", "edit_file", "write", "edit", "applypatch", "apply_patch",
    "multi_edit", "multiedit", "replace_file_content", "multi_replace_file_content",
}


def emit(event, text):
    if not text:
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event, "additionalContext": text}}, ensure_ascii=False))


def emit_ag(text):
    """Antigravity 协议（2026-11-16 L1 桥接）：PreInvocation 输出 injectSteps；
    schema 待实测校准（见 交接_Antigravity.md L1 部署说明节）。"""
    if not text:
        return
    print(json.dumps({"injectSteps": [
        {"systemMessage": {"systemMessage": text}}]}, ensure_ascii=False))


def read_input():
    try:
        return json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return {}


_CLI_MOD = None


def _parse_json_out(out: str):
    out = (out or "").strip()
    if out.startswith("{"):
        return json.loads(out)                           # --json 输出是多行单文档
    for line in reversed(out.splitlines()):
        if line.strip().startswith("{"):
            return json.loads(line)
    return None


def _get_cli_mod():
    global _CLI_MOD
    if _CLI_MOD is None and CLI.is_file():
        spec = importlib.util.spec_from_file_location("_asset_cli_hook", CLI)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CLI_MOD = mod
    return _CLI_MOD


def call_cli(args, timeout=20, ag=False):
    """优先进程内直调 asset_cli（<8ms）；失败时自动回退子进程保证隔离与兼容。"""
    os.environ.setdefault("ASSET_HUB_AGENT", "antigravity" if ag else "claude")
    if args:
        try:
            mod = _get_cli_mod()
            fn = mod.COMMANDS.get(args[0]) if mod else None
            if fn is not None:
                buf, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                    fn(list(args[1:]))
                return _parse_json_out(buf.getvalue())
        except Exception:
            pass
    try:
        r = subprocess.run([sys.executable, str(CLI)] + args, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return _parse_json_out(r.stdout or "")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def _is_ag(data):
    return any(k in data for k in ("conversationId", "workspacePaths", "lastUserInput", "toolCall"))


def _session_id(data):
    return str(data.get("session_id") or data.get("conversationId") or "")


def _project_name(data):
    ws = data.get("workspacePaths")
    p = (ws[0] if isinstance(ws, list) and ws else None) or data.get("cwd") or ""
    return Path(str(p)).name if p else ""


def _tasks_present():
    tdir = Path(os.environ.get("ASSET_HUB_TASKS_DIR", "").strip() or (LIB / ".tasks"))
    return tdir.is_dir() and any(tdir.glob("*.md"))


def _task_ctx(prompt="", sid="", ag=False, project=""):
    if not CLI.is_file() or not _tasks_present():
        return ""
    args = ["task", "session-ctx", "--prompt=" + prompt[:200], "--session=" + sid, "--json"]
    if project:
        args.append("--project=" + project[:80])
    res = call_cli(args, ag=ag)
    return (res or {}).get("text") or ""


def _tool_fp(data):
    tc = data.get("toolCall") if isinstance(data.get("toolCall"), dict) else {}
    tool_name = str(tc.get("name") or data.get("tool_name") or "").lower()
    ti = data.get("tool_input") or (tc.get("args") if isinstance(tc.get("args"), dict) else tc) or {}
    fp = (ti.get("file_path") or ti.get("path") or ti.get("Path")
          or ti.get("TargetFile") or ti.get("target_file") or ti.get("AbsolutePath"))
    return tool_name, fp


def _mk_dir_link(link: Path, target: Path) -> bool:
    """创建或自愈单条技能目录链接（仅修复缺失/断链，绝不覆写有效指向或自建目录）。"""
    if (link / "SKILL.md").is_file():
        return True
    if os.path.lexists(link):
        is_junc = os.name == "nt" and getattr(link, "is_junction", lambda: False)()
        if link.is_symlink():
            link.unlink()
        elif is_junc:
            link.rmdir()
        else:
            return False                        # 非托管真实目录占用，拒删不覆盖
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target.resolve())],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        return r.returncode == 0 and (link / "SKILL.md").is_file()
    link.symlink_to(target.resolve())
    return (link / "SKILL.md").is_file()


def _heal_skill_junctions() -> str:
    """SessionStart 快速巡检+自愈（<5ms 仅 stat；断链即修，修不好才告警）。"""
    sk_dir = LIB / "skills"
    if not sk_dir.is_dir():
        return ""
    skills = [d for d in sorted(sk_dir.iterdir())
              if d.is_dir() and d.name != "_template" and (d / "SKILL.md").is_file()]
    bases = [b for b in (LIB.parent / ".agents" / "skills", Path.home() / ".agents" / "skills")
             if b.parent.is_dir()]
    healed, failed = 0, []
    for b in bases:
        for s in skills:
            link = b / s.name
            if (link / "SKILL.md").is_file():
                continue
            if _mk_dir_link(link, s):
                healed += 1
            else:
                failed.append(str(link))
    ag_local = LIB / ".agents" / "skills"
    if ag_local.parent.is_dir() and not (ag_local / "task-intake" / "SKILL.md").is_file() and skills:
        if not os.path.lexists(ag_local):
            if _mk_dir_link(ag_local, sk_dir):
                healed += 1
            else:
                failed.append(str(ag_local))
    note = f" [healed {healed} skill links]" if healed else ""
    if failed:
        note += f" [warn: skill link heal failed: {failed[0]}]"
    return note


def on_start(data=None):
    data = data or {}
    ag = _is_ag(data)
    if not CLI.is_file():
        if ag:
            print("{}")
        return
    # W1-4 体检漏跑检测 + junction 断链就地自愈
    note = ""
    try:
        from datetime import date as _d, timedelta as _td
        marker = LIB / "_meta" / ".last_health_check"
        if marker.exists():
            last = marker.read_text(encoding="utf-8").strip()[:10]
            yd = str(_d.today() - _td(days=1))
            if last and last not in (yd, str(_d.today())):
                note = f" [warn: no health check marker for {yd}; run: python _meta/health_check.py --quick]"
    except Exception:
        pass                                  # 检测自身失败不拖累会话启动
    try:
        note += _heal_skill_junctions()
    except Exception:
        pass
    msg = "EngineerRig enabled: deterministic tool/skill routing and out-of-repo script capture active." + note
    tctx = _task_ctx(sid=_session_id(data), ag=ag, project=_project_name(data))
    if tctx:
        msg = msg + "\n" + tctx
    if ag:
        emit_ag(msg)
    else:
        emit("SessionStart", msg)


def _capsule_cadence(sid: str, data: dict, interval: int = 12):
    """任务胶囊提示节拍：首轮注入、2~11 轮静默省 token，compact 后或每 N 轮补注防失忆。"""
    if data.get("after_compact") or data.get("compacted") or data.get("isCompacted"):
        return True, "compact"
    st_file = os.environ.get("ASSET_HUB_CADENCE_STATE", "").strip()
    sp = Path(st_file) if st_file else (LIB.parent / "_asset_logs" / "hook_cadence.json")
    try:
        st = json.loads(sp.read_text(encoding="utf-8")) if sp.is_file() else {}
        if not isinstance(st, dict):
            st = {}
    except (OSError, ValueError):
        st = {}
    key = sid or "default"
    try:
        prev = int(st.pop(key, 0) or 0)
    except (TypeError, ValueError):
        prev = 0
    turns = prev + 1
    st[key] = turns
    while len(st) > 50:
        st.pop(next(iter(st)))
    try:
        sp.parent.mkdir(parents=True, exist_ok=True)
        tmp = sp.with_name(f".{sp.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8", newline="\n")
            os.replace(tmp, sp)
        finally:
            tmp.unlink(missing_ok=True)
    except OSError:
        return True, "first"
    if turns == 1:
        return True, "first"
    if turns > 1 and turns % interval == 0:
        return True, "cadence"
    return False, ""


def on_prompt(data):
    ag = _is_ag(data)
    q = (data.get("prompt") or data.get("lastUserInput") or "").strip().replace("\r", " ").replace("\n", " ")
    if (len(q) < 4 and "T-" not in q) or q.startswith("/") or q.lower() in SKIP_PROMPTS:
        if ag:
            print("{}")
        return
    sid = _session_id(data)
    tctx = ""
    if _tasks_present():
        explicit_tid = "T-" in q
        due, reason = _capsule_cadence(sid, data)
        if explicit_tid or due:
            raw_tctx = _task_ctx(prompt=q[:200], sid=sid, ag=ag, project=_project_name(data))
            if raw_tctx:
                tctx = (f"[Reminder] {raw_tctx}"
                        if (reason in ("compact", "cadence") and not explicit_tid)
                        else raw_tctx)
    # 纯路由函数在 CLI 侧（select_injection：阈值/家族路由/会话去重/反馈日志一体）
    res = call_cli(["inject-candidate", "--prompt=" + q[:200], "--session=" + sid], ag=ag)
    text = (res or {}).get("text") or ""
    combined = (tctx + "\n" + text) if (tctx and text) else (tctx or text)
    if combined:
        if ag:                                # Antigravity camelCase 协议
            emit_ag(combined)
        else:
            emit("UserPromptSubmit", combined)
    elif ag:
        print("{}")


def on_pre(data):
    ag = _is_ag(data)
    tool_name, fp = _tool_fp(data)
    if not fp or tool_name not in WRITE_TOOLS or not CLI.is_file() or not _tasks_present():
        if ag:
            print(json.dumps({"decision": "allow"}))
        return
    sid = _session_id(data)
    res = call_cli(["task", "check-write", "--path=" + str(fp), "--session=" + sid, "--json"], ag=ag)
    if res and res.get("ok") is False:
        reason = res.get("reason") or "Task scope boundary violation"
        if ag:
            print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
        else:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }}, ensure_ascii=False))
        return
    if ag:
        print(json.dumps({"decision": "allow"}))


def on_post(data):
    ag = _is_ag(data)
    tool_name, fp = _tool_fp(data)
    if not fp:
        if ag:
            print("{}")
        return
    sid = _session_id(data)
    read_only = tool_name in {"read_file", "view_file", "read", "grep_search",
                              "find_by_name", "list_dir", "view_file_outline", "view_code_item"}
    res = None if read_only else call_cli(["ingest", "--file=" + str(fp), "--session=" + sid], ag=ag)
    # P2-1 反馈闭环：读文件自动落 open 事件——CLI 侧比对本会话注入/检索的资产路径，
    # 同文件才记（结果丢弃、静默；无人需要记 query_id）
    call_cli(["log-open", "--path=" + str(fp), "--session=" + sid], ag=ag)
    if ag:
        print("{}")                            # Antigravity PostToolUse 契约要求输出 {}
        return
    if not res:
        return
    st = res.get("status")
    if st == "captured":
        emit("PostToolUse", "Captured to inbox (passed secret prescan, uncommitted): " + res.get("inbox", ""))
    elif st == "quarantined":
        emit("PostToolUse", "Quarantined outside repo (hit secret prescan): " + res.get("staging", "")[:64]
             + " flags:" + ",".join(res.get("flags", [])))
    elif st == "needs_card":
        emit("PostToolUse", "Script " + str(res.get("file", ""))[:50] + " is missing an 8-line Asset Card header")


def main():
    event = "prompt"
    for a in sys.argv:
        if a.startswith("--event="):
            event = a.split("=", 1)[1]
    try:
        data = read_input()
        if event == "start":
            on_start(data)
        elif event == "pre":
            on_pre(data)
        elif event == "post":
            on_post(data)
        else:
            on_prompt(data)
    except Exception:
        pass                                  # 钩子永不打断会话
    return 0


if __name__ == "__main__":
    sys.exit(main())
