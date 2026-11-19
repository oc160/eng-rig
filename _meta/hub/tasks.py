# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：tasks.py —— Git-Native 多代理任务胶囊与越界拦截核心（四跨协作引擎）
# 来源：2026-11-16 bridge 协议重构（替代 9 目录散装文件 + 静态锁 + 手写看板）
# 提取：2026-11-16
# Author: EngineerRig
# 验证方式：tests/test_tasks.py（YAML往返/TTL租约/越界硬拦截/验收硬门/DeltaReplay/看板）
# 重跑命令：python -m pytest -q tests/test_tasks.py
# 依赖：纯标准库；单向被 asset_cli / agent_hooks / session_close 调用
# ──────────────────────────────────────────────────────────
"""Git-Native 多代理四跨协作引擎（跨代理/跨会话/跨设备/跨用户）。

设计核心：
  1. 一任务一胶囊：.tasks/<task_id>.md（YAML Frontmatter + Contract + Delta Handoff），
     彻底消除中心化 TASK_BOARD.md / CHANGELOG.md 的 Git 并发写冲突。
  2. TTL 租约防死锁：认领写 owner/session/lease_until；超时自动失效可接管。
  3. PreToolUse 物理防撞墙：check_write_scope 按 scope_globs 硬拦截他人锁区与越界修改。
  4. Built != Done 收敛门：complete_task 必须跑通 verify_cmd 才准标 done。
  5. DeltaReplay 断点续接：session_context 返回 ≤150 字未完子任务+交接摘要。
  6. 动静分离传输：.tasks/ 由独立轻量分支 tasks 承载（git worktree），零污染代码主分支。
"""
import fnmatch
import getpass
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent.parent

_TASK_KEYS = (
    "id", "project", "title", "status", "owner", "session",
    "lease_until", "depends_on", "scope_globs", "verify_cmd",
    "subtasks", "updated_at",
)
_LIST_KEYS = {"depends_on", "scope_globs", "subtasks"}
_VALID_STATUS = ("todo", "claimed", "blocked", "review", "done")
_TASK_ID_RE = re.compile(r"\b(T-\d{8}-\d+|T-\d+)\b")
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def tasks_dir(lib_root=None) -> Path:
    """定位任务胶囊目录：优先环境变量 ASSET_HUB_TASKS_DIR，默认 <lib>/.tasks。"""
    env = os.environ.get("ASSET_HUB_TASKS_DIR", "").strip()
    if env:
        return Path(env)
    return Path(lib_root or LIB) / ".tasks"


def default_owner(agent: str = "") -> str:
    """生成默认认领人标识 <user>@<host>:<agent>。"""
    user = os.environ.get("USERNAME") or os.environ.get("USER") or getpass.getuser() or "user"
    host = os.environ.get("COMPUTERNAME") or socket.gethostname() or "host"
    ag = agent or os.environ.get("ASSET_HUB_AGENT") or "agent"
    return f"{user}@{host}:{ag}"


def _now(now=None) -> datetime:
    if now is not None:
        return now
    return datetime.now().astimezone().replace(microsecond=0)


def _now_iso(now=None) -> str:
    return _now(now).isoformat()


def _parse_dt(val: str):
    if not val:
        return None
    try:
        return datetime.fromisoformat(str(val).strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _align_tz(dt: datetime, ref: datetime) -> datetime:
    if dt.tzinfo is None and ref.tzinfo is not None:
        return dt.replace(tzinfo=ref.tzinfo)
    if dt.tzinfo is not None and ref.tzinfo is None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


# ── YAML Frontmatter 无依赖解析与序列化 ───────────────────

def _strip_inline_comment(val: str) -> str:
    in_sq = in_dq = esc = False
    for i, ch in enumerate(val):
        if esc:
            esc = False
            continue
        if ch == "\\" and in_dq:
            esc = True
            continue
        if ch == '"' and not in_sq:
            in_dq = not in_dq
        elif ch == "'" and not in_dq:
            in_sq = not in_sq
        elif ch == "#" and not in_sq and not in_dq and (i == 0 or val[i - 1].isspace()):
            return val[:i].rstrip()
    return val.rstrip()


def _split_top_level(text: str, sep: str = ",") -> list:
    parts, buf = [], []
    in_sq = in_dq = esc = False
    depth = 0
    for ch in text:
        if esc:
            buf.append(ch)
            esc = False
            continue
        if ch == "\\" and in_dq:
            buf.append(ch)
            esc = True
            continue
        if ch == '"' and not in_sq:
            in_dq = not in_dq
        elif ch == "'" and not in_dq:
            in_sq = not in_sq
        elif not in_sq and not in_dq:
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth = max(0, depth - 1)
            elif ch == sep and depth == 0:
                part = "".join(buf).strip()
                if part:
                    parts.append(part)
                buf = []
                continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_inline_dict(raw: str) -> dict:
    inner = raw.strip()[1:-1].strip()
    out = {}
    if not inner:
        return out
    for item in _split_top_level(inner, ","):
        # 按顶层首个冒号切分，避免 URL/时间串内冒号误切
        in_sq = in_dq = esc = False
        colon = -1
        for i, ch in enumerate(item):
            if esc:
                esc = False
                continue
            if ch == "\\" and in_dq:
                esc = True
                continue
            if ch == '"' and not in_sq:
                in_dq = not in_dq
            elif ch == "'" and not in_dq:
                in_sq = not in_sq
            elif ch == ":" and not in_sq and not in_dq:
                colon = i
                break
        if colon < 0:
            continue
        k = _parse_scalar(item[:colon].strip(), coerce_bool=False)
        v = _parse_scalar(item[colon + 1:].strip(), coerce_bool=True)
        out[str(k)] = v
    return out


def _parse_scalar(val: str, coerce_bool: bool = False):
    v = _strip_inline_comment(val).strip()
    if not v:
        return ""
    if v[0] in "[{":
        try:
            return json.loads(v)
        except ValueError:
            if v.startswith("{") and v.endswith("}"):
                return _parse_inline_dict(v)
            if v.startswith("[") and v.endswith("]"):
                inner = v[1:-1].strip()
                return [_parse_scalar(x, coerce_bool=True) for x in _split_top_level(inner, ",")] if inner else []
    if v.startswith('"') and v.endswith('"') and len(v) >= 2:
        try:
            return json.loads(v)
        except ValueError:
            return v[1:-1]
    if v.startswith("'") and v.endswith("'") and len(v) >= 2:
        return v[1:-1].replace("''", "'")
    if coerce_bool:
        low = v.lower()
        if low == "true":
            return True
        if low == "false":
            return False
        if low in ("null", "none", "~"):
            return None
    return v


def _split_frontmatter(text: str) -> tuple:
    m = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n(.*))?\Z", text, re.S)
    if not m:
        return [], text
    return m.group(1).splitlines(), (m.group(2) or "")


def _parse_body_sections(body: str) -> tuple:
    contract_lines, handoff_lines = [], []
    mode = "contract"
    for line in (body or "").splitlines():
        hm = re.match(r"^##\s+(.+?)\s*$", line)
        if hm:
            heading = hm.group(1).lower()
            if "交接" in heading or "handoff" in heading:
                mode = "handoff"
            else:
                mode = "contract"
            continue
        (handoff_lines if mode == "handoff" else contract_lines).append(line)
    return "\n".join(contract_lines).strip(), "\n".join(handoff_lines).strip()


def _normalize_subtasks(items) -> list:
    out = []
    for idx, it in enumerate(items or [], 1):
        if isinstance(it, dict):
            out.append({
                "id": str(it.get("id") or f"s{idx}"),
                "text": str(it.get("text") or ""),
                "done": bool(it.get("done", False)),
            })
        elif str(it).strip():
            out.append({"id": f"s{idx}", "text": str(it).strip(), "done": False})
    return out


def parse_task_file(path: Path) -> dict:
    """读取并解析任务胶囊 Markdown 文件为标准字典（纯标准库，兼容 JSON 内联与多行列表）。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    fm_lines, body = _split_frontmatter(text)
    meta = {k: ([] if k in _LIST_KEYS else "") for k in _TASK_KEYS}
    meta["project"] = "asset-hub"
    meta["status"] = "todo"
    cur_key = None
    for raw in fm_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lm = re.match(r"^\s*-\s*(.*)$", raw)
        if lm and cur_key is not None:
            item = _parse_scalar(lm.group(1), coerce_bool=False)
            if not isinstance(meta.get(cur_key), list):
                meta[cur_key] = []
            meta[cur_key].append(item)
            continue
        km = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", raw)
        if not km:
            continue
        cur_key = km.group(1)
        rest = km.group(2).strip()
        if _strip_inline_comment(rest) == "":
            meta[cur_key] = [] if cur_key in _LIST_KEYS else ""
        else:
            parsed = _parse_scalar(rest, coerce_bool=False)
            if cur_key in _LIST_KEYS and not isinstance(parsed, list):
                parsed = [parsed] if parsed else []
            meta[cur_key] = parsed
    meta["subtasks"] = _normalize_subtasks(meta.get("subtasks"))
    meta["depends_on"] = [str(x) for x in (meta.get("depends_on") or []) if str(x).strip()]
    meta["scope_globs"] = [str(x).replace("\\", "/") for x in (meta.get("scope_globs") or []) if str(x).strip()]
    contract, handoff = _parse_body_sections(body)
    meta["contract"] = contract
    meta["handoff"] = handoff
    return meta


def _yaml_val(v) -> str:
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    s = "" if v is None else str(v)
    if s == "":
        return '""'
    if (any(c in s for c in (":", "#", '"', "'", "[", "]", "{", "}", "\n"))
            or s.strip() != s or s.lower() in ("true", "false", "null", "none", "~")):
        return json.dumps(s, ensure_ascii=False)
    return s


def dump_task_file(task: dict) -> str:
    """将任务字典序列化为规范 YAML Frontmatter + 双节 Markdown 正文。"""
    t = dict(task)
    t["subtasks"] = _normalize_subtasks(t.get("subtasks"))
    lines = ["---"]
    seen = set()
    for k in _TASK_KEYS:
        seen.add(k)
        default = [] if k in _LIST_KEYS else ""
        lines.append(f"{k}: {_yaml_val(t.get(k, default))}")
    for k, v in t.items():
        if k in seen or k in ("contract", "handoff"):
            continue
        lines.append(f"{k}: {_yaml_val(v)}")
    lines.append("---")
    lines.append("")
    lines.append("## 契约与目标 (Contract & Goal)")
    contract = (t.get("contract") or "").strip()
    if contract:
        lines.append(contract)
    lines.append("")
    lines.append("## 断点交接 (Delta Handoff)")
    handoff = (t.get("handoff") or "").strip()
    if handoff:
        lines.append(handoff)
    lines.append("")
    return "\n".join(lines)


def _write_task(tdir: Path, task: dict) -> dict:
    Path(tdir).mkdir(parents=True, exist_ok=True)
    p = Path(tdir) / f"{task['id']}.md"
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(dump_task_file(task), encoding="utf-8", newline="\n")
        os.replace(tmp, p)
    finally:
        tmp.unlink(missing_ok=True)
    return task


# ── 核心状态机与锁/越界/验收逻辑 ──────────────────────────

def lease_active(task: dict, now=None) -> bool:
    """判定任务当前是否持有未过期租约（claimed 且 lease_until > now）。"""
    if (task or {}).get("status") != "claimed":
        return False
    lu = _parse_dt(task.get("lease_until", ""))
    if lu is None:
        return False
    cur = _now(now)
    lu = _align_tz(lu, cur)
    return lu > cur


def _same_actor(task: dict, session: str = "", owner: str = "") -> bool:
    t_sess = str(task.get("session") or "")
    t_owner = str(task.get("owner") or "")
    if t_sess:
        if session:
            return session == t_sess
        return bool(owner and owner == t_owner)
    if owner and t_owner:
        return owner == t_owner
    return False


def _next_task_id(tdir: Path, now=None) -> str:
    day = _now(now).strftime("%Y%m%d")
    prefix = f"T-{day}-"
    seq = 0
    if Path(tdir).is_dir():
        for p in Path(tdir).glob(f"{prefix}*.md"):
            tail = p.stem[len(prefix):]
            if tail.isdigit():
                seq = max(seq, int(tail))
    return f"{prefix}{seq + 1:02d}"


def _all_tasks(tdir: Path) -> list:
    d = Path(tdir)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        t = parse_task_file(p)
        if t.get("id"):
            out.append(t)
    return out


def create_task(tdir: Path, title: str, project: str = "asset-hub", task_id: str = None,
                scope_globs=None, verify_cmd: str = "", subtasks=None,
                depends_on=None, contract: str = "") -> dict:
    """新建任务胶囊并写入 <tdir>/<task_id>.md。"""
    tdir = Path(tdir)
    tid = (task_id or "").strip() or _next_task_id(tdir)
    dest = tdir / f"{tid}.md"
    if dest.exists():
        raise FileExistsError(f"任务已存在: {tid}")
    task = {
        "id": tid,
        "project": (project or "asset-hub").strip(),
        "title": title.strip(),
        "status": "todo",
        "owner": "",
        "session": "",
        "lease_until": "",
        "depends_on": [str(x).strip() for x in (depends_on or []) if str(x).strip()],
        "scope_globs": [str(x).strip().replace("\\", "/") for x in (scope_globs or []) if str(x).strip()],
        "verify_cmd": (verify_cmd or "").strip(),
        "subtasks": _normalize_subtasks(subtasks),
        "updated_at": _now_iso(),
        "contract": (contract or "").strip(),
        "handoff": "",
    }
    return _write_task(tdir, task)


def claim_task(tdir: Path, task_id: str, owner: str = "", session: str = "",
               ttl_minutes: int = 45, force: bool = False, now=None) -> tuple:
    """认领或续租任务；检前置依赖与活跃租约互斥，过期租约可自动接管。"""
    tdir = Path(tdir)
    p = tdir / f"{task_id}.md"
    if not p.is_file():
        return False, f"任务不存在: {task_id}", {}
    task = parse_task_file(p)
    if task.get("status") == "done" and not force:
        return False, f"任务 {task_id} 已完成 (done)，如需重开请传 --force", task
    if not force:
        for dep in task.get("depends_on") or []:
            dp = tdir / f"{dep}.md"
            if not dp.is_file() or parse_task_file(dp).get("status") != "done":
                return False, f"前置任务 {dep} 尚未完成，不能认领 {task_id}", task
        if lease_active(task, now=now) and not _same_actor(task, session=session, owner=owner):
            holder = task.get("owner") or task.get("session") or "未知代理"
            return False, f"任务 {task_id} 已被 {holder} 锁定至 {task.get('lease_until')}", task
    cur = _now(now)
    same_sess = bool(session and session == task.get("session"))
    same_owner_renew = bool(not session and owner and owner == task.get("owner") and lease_active(task, now=now))
    task["status"] = "claimed"
    task["owner"] = (owner or (task.get("owner") if same_sess else "") or default_owner()).strip()
    if not same_owner_renew:
        task["session"] = session
    task["lease_until"] = (cur + timedelta(minutes=int(ttl_minutes))).isoformat()
    task["updated_at"] = cur.isoformat()
    _write_task(tdir, task)
    return True, f"✓ 已认领 {task_id}（owner={task['owner']}, 租约至 {task['lease_until']}）", task


def _match_glob(rel: str, pattern: str) -> bool:
    pat = pattern.strip().replace("\\", "/")
    if not pat:
        return False
    r_cmp = rel.lower() if os.name == "nt" else rel
    p_cmp = pat.lower() if os.name == "nt" else pat
    if p_cmp.endswith("/"):
        prefix = p_cmp.rstrip("/")
        return r_cmp == prefix or r_cmp.startswith(prefix + "/")
    if fnmatch.fnmatchcase(r_cmp, p_cmp):
        return True
    if p_cmp.startswith("**/") and fnmatch.fnmatchcase(r_cmp, p_cmp[3:]):
        return True
    if "/" not in p_cmp and fnmatch.fnmatchcase(Path(r_cmp).name, p_cmp):
        return False
    if "*" not in p_cmp and "?" not in p_cmp and "[" not in p_cmp:
        return r_cmp == p_cmp or r_cmp.startswith(p_cmp + "/")
    return False


def _match_any_scope(rel: str, globs: list) -> bool:
    return any(_match_glob(rel, g) for g in (globs or []))


def check_write_scope(tdir: Path, target_path, session: str = "", owner: str = "",
                      lib_root=None, now=None) -> tuple:
    """PreToolUse 越界硬拦截：保护他人活跃锁区，限制自身不超出声明的 scope_globs（支持跨兄弟项目路径）。"""
    tdir = Path(tdir)
    if not tdir.is_dir():
        return True, ""
    tasks = _all_tasks(tdir)
    active = [t for t in tasks if lease_active(t, now=now)]
    if not active:
        return True, ""
    root = Path(lib_root or LIB).resolve()
    tp = Path(target_path)
    if not tp.is_absolute():
        tp = root / tp
    tp_res = tp.resolve()
    abs_posix = tp_res.as_posix()
    in_ws = True
    try:
        rels = [tp_res.relative_to(root).as_posix()]
    except ValueError:
        try:
            rels = [tp_res.relative_to(root.parent).as_posix(), abs_posix]
        except ValueError:
            rels = [abs_posix]
            in_ws = False
    rel = rels[0]
    if any(r == ".tasks" or r.startswith((".tasks/", ".git/")) or "/.git/" in r or "/.tasks/" in r for r in rels):
        return True, ""

    mine = [t for t in active if _same_actor(t, session=session, owner=owner)]
    others = [t for t in active if t not in mine]

    for t in others:
        if t.get("scope_globs") and any(_match_any_scope(r, t["scope_globs"]) for r in rels):
            holder = t.get("owner") or t.get("session") or "其他代理"
            return False, (
                f"拒绝越界写入：{rel} 归属活跃任务 {t['id']} "
                f"(owner={holder}, 租约至 {t.get('lease_until')})"
            )

    if not in_ws:
        return True, ""
    scoped_mine = [t for t in mine if t.get("scope_globs")]
    if mine and len(scoped_mine) == len(mine):
        if not any(_match_any_scope(r, t["scope_globs"]) for t in scoped_mine for r in rels):
            primary = scoped_mine[0]
            return False, (
                f"拒绝越界写入：{rel} 超出当前任务 {primary['id']} "
                f"声明的边界 {primary['scope_globs']}"
            )
    return True, ""


def _git_diff_summary(lib_root: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(lib_root), "status", "-s"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
        lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        if not lines:
            return "工作区无未提交改动"
        return "改动：" + ", ".join(lines[:5]) + (f" 等{len(lines)}项" if len(lines) > 5 else "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def handoff_task(tdir: Path, task_id: str, note: str = "", done_subtasks=None,
                 blocked: bool = False, lib_root=None) -> tuple:
    """DeltaReplay 断点交接：勾完子任务、写 ≤200 字交接摘要并释放租约。"""
    tdir = Path(tdir)
    p = tdir / f"{task_id}.md"
    if not p.is_file():
        return False, f"任务不存在: {task_id}", {}
    task = parse_task_file(p)
    done_set = {str(x).strip() for x in (done_subtasks or []) if str(x).strip()}
    for st in task.get("subtasks") or []:
        if st.get("id") in done_set:
            st["done"] = True
    summary = re.sub(r"\s+", " ", note or "").strip()
    if not summary and lib_root is not None:
        summary = _git_diff_summary(Path(lib_root))
    if len(summary) > 200:
        summary = summary[:199] + "…"
    if summary:
        task["handoff"] = summary
    task["lease_until"] = ""
    task["status"] = "blocked" if blocked else "todo"
    task["updated_at"] = _now_iso()
    _write_task(tdir, task)
    return True, f"✓ 已交接 {task_id} (status={task['status']})", task


def complete_task(tdir: Path, task_id: str, lib_root=None, skip_verify: bool = False) -> tuple:
    """Converge 验收硬门（Built != Done）：跑通 verify_cmd 才允许标 done。"""
    tdir = Path(tdir)
    p = tdir / f"{task_id}.md"
    if not p.is_file():
        return False, f"任务不存在: {task_id}", {}
    task = parse_task_file(p)
    vcmd = (task.get("verify_cmd") or "").strip()
    root = Path(lib_root or LIB)
    if vcmd and not skip_verify:
        try:
            r = subprocess.run(vcmd, shell=True, cwd=str(root), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"验收门执行异常: {exc}", task
        if r.returncode != 0:
            raw = [ln.strip() for ln in ((r.stderr or "") + "\n" + (r.stdout or "")).splitlines() if ln.strip()]
            tail = raw[-1] if raw else ""
            return False, f"验收门未通过 (rc={r.returncode}): {tail[:160]}", task
    for st in task.get("subtasks") or []:
        st["done"] = True
    now_iso = _now_iso()
    task["status"] = "done"
    task["lease_until"] = ""
    task["updated_at"] = now_iso
    stamp = f"验收通过 ({now_iso[:16]})"
    prev = re.sub(r"\s+", " ", task.get("handoff") or "").strip()
    task["handoff"] = (f"{stamp} | {prev}" if prev else stamp)[:200]
    _write_task(tdir, task)
    return True, f"✓ 任务 {task_id} 已验收并结项 (done)", task


def session_context(tdir: Path, session: str = "", owner: str = "", prompt: str = "", now=None) -> str:
    """生成 ≤150 字断点续接摘要；若 prompt 点名任务号且可认领则自动认领/续租。"""
    tdir = Path(tdir)
    if not tdir.is_dir():
        return ""
    for tid in _TASK_ID_RE.findall(prompt or ""):
        if (tdir / f"{tid}.md").is_file():
            claim_task(tdir, tid, owner=owner, session=session, now=now)
    tasks = _all_tasks(tdir)
    mine = [t for t in tasks if lease_active(t, now=now) and _same_actor(t, session=session, owner=owner)]
    if not mine:
        return ""
    t = mine[0]
    scope = ",".join(t.get("scope_globs") or []) or "*"
    pending = ";".join(st.get("text", "") for st in (t.get("subtasks") or []) if not st.get("done")) or "final verification"
    handoff = re.sub(r"\s+", " ", t.get("handoff") or "").strip()
    msg = f"[Task Capsule {t['id']}] {t.get('title', '')} | Scope:{scope} | Todo:{pending}"
    if handoff:
        msg += f" | Handoff:{handoff}"
    return msg[:149] + "…" if len(msg) > 150 else msg


def render_board(tdir: Path, now=None) -> str:
    """动态聚合 .tasks/*.md 生成 Markdown 看板，替代手写 TASK_BOARD.md。"""
    tasks = _all_tasks(tdir)
    if not tasks:
        return "# 任务看板 (.tasks 动态聚合，只读)\n\n(暂无任务)"
    lines = [
        "# 任务看板 (.tasks 动态聚合，只读)",
        "",
        "| ID | 状态 | 持有人 (Owner) | 租约 | 进度 | 边界 (Scope) | 标题 | 更新 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in tasks:
        sts = t.get("subtasks") or []
        done_n = sum(1 for s in sts if s.get("done"))
        prog = f"{done_n}/{len(sts)}" if sts else "-"
        if t.get("status") == "claimed":
            if lease_active(t, now=now):
                lu = t.get("lease_until", "")
                lease_s = f"至 {lu[11:16]}" if len(lu) >= 16 else "有效"
            else:
                lease_s = "已过期"
        else:
            lease_s = "-"
        scope = ", ".join(t.get("scope_globs") or []) or "*"
        owner = (t.get("owner") or t.get("session") or "-").replace("|", "/")
        title = (t.get("title") or "").replace("|", "/")
        upd = (t.get("updated_at") or "")[:16].replace("T", " ")
        lines.append(f"| {t['id']} | {t.get('status', 'todo')} | {owner} | {lease_s} | {prog} | `{scope}` | {title} | {upd} |")
    return "\n".join(lines)


# ── Git 独立 tasks 分支同步（跨设备 / 跨用户，零污染 master） ──

def _git(cwd: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=30)


def _pick_remote(lib_root: Path) -> str:
    r = _git(lib_root, "remote")
    remotes = r.stdout.split()
    for pref in ("hub", "origin"):
        if pref in remotes:
            return pref
    return remotes[0] if remotes else ""


def _ensure_worktree(tdir: Path, lib_root: Path) -> tuple:
    tdir = Path(tdir)
    if (tdir / ".git").exists():
        return True, ""
    if _git(lib_root, "rev-parse", "--git-dir").returncode != 0:
        return False, "当前目录不是 git 仓库"
    backup = {}
    if tdir.exists():
        for f in tdir.glob("*.md"):
            backup[f.name] = f.read_bytes()
        shutil.rmtree(tdir)
    remote = _pick_remote(lib_root)
    has_local = _git(lib_root, "show-ref", "--verify", "--quiet", "refs/heads/tasks").returncode == 0
    if not has_local and remote:
        if _git(lib_root, "ls-remote", "--heads", remote, "tasks").stdout.strip():
            _git(lib_root, "fetch", remote, "tasks:tasks")
            has_local = True
    if not has_local:
        cm = _git(lib_root, "commit-tree", _EMPTY_TREE_SHA, "-m", "tasks: initialize capsule branch")
        if cm.returncode != 0:
            return False, f"创建 tasks 根分支失败: {cm.stderr.strip()}"
        _git(lib_root, "update-ref", "refs/heads/tasks", cm.stdout.strip())
    wt = _git(lib_root, "worktree", "add", str(tdir), "tasks")
    if wt.returncode != 0:
        tdir.mkdir(parents=True, exist_ok=True)
        for name, data in backup.items():
            (tdir / name).write_bytes(data)
        return False, f"挂载 .tasks worktree 失败: {wt.stderr.strip()}"
    for name, data in backup.items():
        (tdir / name).write_bytes(data)
    return True, "✓ 已挂载独立 tasks 分支 worktree"


def sync_tasks_git(tdir: Path, lib_root=None, init_branch: bool = False) -> tuple:
    """在独立 tasks 分支提交并同步 .tasks/*.md 到远端，绝不污染 master 分支。"""
    root = Path(lib_root or LIB)
    tdir = Path(tdir)
    if not (tdir / ".git").exists():
        if not init_branch:
            return False, ".tasks 尚未挂载为独立 git worktree；加 --init 自动初始化 tasks 分支"
        ok, msg = _ensure_worktree(tdir, root)
        if not ok:
            return False, msg
    st = _git(tdir, "status", "--porcelain")
    if st.stdout.strip():
        files = [f.name for f in sorted(tdir.glob("*.md"))]
        if files:
            _git(tdir, "add", "--", *files)
            _git(tdir, "commit", "-m", "tasks: sync capsules")
    remote = _pick_remote(root)
    if not remote:
        return True, "✓ .tasks 已本地提交（未配置远端）"
    if _git(tdir, "ls-remote", "--heads", remote, "tasks").stdout.strip():
        _git(tdir, "fetch", remote, "tasks")
        rb = _git(tdir, "pull", "--rebase", remote, "tasks")
        if rb.returncode != 0:
            _git(tdir, "rebase", "--abort")
            return False, f"远端同步冲突，已中止 rebase: {rb.stderr.strip()[:120]}"
    ps = _git(tdir, "push", "-u", remote, "tasks")
    if ps.returncode != 0:
        return False, f"推送 tasks 分支失败: {ps.stderr.strip()[:120]}"
    return True, f"✓ .tasks 已与 {remote}/tasks 同步"


# ── CLI 子命令分发 ────────────────────────────────────────

def _parse_kv(argv: list) -> tuple:
    pos, kv, flags = [], {}, set()
    multi = {}
    for a in argv:
        if a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            kv[k] = v
            multi.setdefault(k, []).append(v)
        elif a.startswith("--"):
            flags.add(a[2:])
        else:
            pos.append(a)
    return pos, kv, flags, multi


def _split_csv(val: str) -> list:
    return [x.strip() for x in (val or "").split(",") if x.strip()]


def cmd_task(argv: list, lib_root=None) -> int:
    """asset task 子命令入口：new/claim/handoff/done/board/show/check-write/session-ctx/sync。"""
    root = Path(lib_root or LIB)
    tdir = tasks_dir(root)
    if not argv or argv[0] in ("-h", "--help"):
        print("用法：asset task <new|claim|handoff|done|board|show|check-write|session-ctx|sync> [参数]")
        return 0 if argv else 2
    sub = argv[0]
    pos, kv, flags, multi = _parse_kv(argv[1:])

    if sub == "new":
        title = kv.get("title") or (" ".join(pos).strip())
        if not title:
            print("✗ 缺少任务标题", file=sys.stderr)
            return 2
        subs = []
        for s in multi.get("subtask", []):
            subs.append(s)
        if kv.get("subtasks"):
            subs.extend(x.strip() for x in kv["subtasks"].split("|") if x.strip())
        t = create_task(
            tdir, title=title, project=kv.get("project", "asset-hub"),
            task_id=kv.get("id"), scope_globs=_split_csv(kv.get("scope", "")),
            verify_cmd=kv.get("verify", ""), subtasks=subs,
            depends_on=_split_csv(kv.get("depends", "")), contract=kv.get("contract", ""),
        )
        print(f"✓ 已创建 {t['id']} -> {tdir / (t['id'] + '.md')}")
        return 0

    if sub == "claim":
        tid = kv.get("id") or (pos[0] if pos else "")
        if not tid:
            print("✗ 缺少任务 ID", file=sys.stderr)
            return 2
        ok, msg, _ = claim_task(tdir, tid, owner=kv.get("owner", ""), session=kv.get("session", ""),
                                ttl_minutes=int(kv.get("ttl", "45")), force=("force" in flags))
        print(msg)
        return 0 if ok else 1

    if sub == "handoff":
        tid = kv.get("id") or (pos[0] if pos else "")
        if not tid:
            print("✗ 缺少任务 ID", file=sys.stderr)
            return 2
        ok, msg, _ = handoff_task(tdir, tid, note=kv.get("note", ""),
                                  done_subtasks=_split_csv(kv.get("done", "")),
                                  blocked=("blocked" in flags), lib_root=root)
        print(msg)
        return 0 if ok else 1

    if sub == "done":
        tid = kv.get("id") or (pos[0] if pos else "")
        if not tid:
            print("✗ 缺少任务 ID", file=sys.stderr)
            return 2
        ok, msg, _ = complete_task(tdir, tid, lib_root=root, skip_verify=("skip-verify" in flags))
        print(msg)
        return 0 if ok else 1

    if sub == "board":
        print(render_board(tdir))
        return 0

    if sub == "show":
        tid = kv.get("id") or (pos[0] if pos else "")
        p = tdir / f"{tid}.md"
        if not p.is_file():
            print(f"✗ 任务不存在: {tid}", file=sys.stderr)
            return 1
        if "json" in flags:
            print(json.dumps(parse_task_file(p), ensure_ascii=False, indent=2))
        else:
            print(p.read_text(encoding="utf-8", errors="replace"))
        return 0

    if sub == "check-write":
        fp = kv.get("path") or (pos[0] if pos else "")
        ok, reason = check_write_scope(tdir, fp, session=kv.get("session", ""),
                                       owner=kv.get("owner", ""), lib_root=root)
        if "json" in flags:
            print(json.dumps({"ok": ok, "reason": reason}, ensure_ascii=False))
        elif reason:
            print(reason)
        return 0 if ok else 1

    if sub == "session-ctx":
        txt = session_context(tdir, session=kv.get("session", ""),
                              owner=kv.get("owner", ""), prompt=kv.get("prompt", ""))
        if "json" in flags:
            print(json.dumps({"text": txt}, ensure_ascii=False))
        elif txt:
            print(txt)
        return 0

    if sub == "sync":
        ok, msg = sync_tasks_git(tdir, lib_root=root, init_branch=("init" in flags))
        print(msg)
        return 0 if ok else 1

    print(f"未知 task 子命令: {sub}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(cmd_task(sys.argv[1:], LIB))
