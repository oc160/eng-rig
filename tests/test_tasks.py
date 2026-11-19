# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：test_tasks.py —— Git-Native 多代理任务胶囊与钩子执法单元测试
# 来源：2026-11-16 bridge 协议重构（四跨协作引擎验收套件）
# 提取：2026-11-16
# Author: EngineerRig
# 验证方式：pytest tests/test_tasks.py -q 全绿（含 YAML 往返/TTL/越界门/验收门/跨仓 tasks 分支同步）
# 重跑命令：python -m pytest -q tests/test_tasks.py
# 依赖：pytest、git（仅 sync 隔离分支测试用）、纯标准库 _meta/hub/tasks.py
# ──────────────────────────────────────────────────────────
"""Git-Native 多代理任务胶囊全链路单测（纯隔离 tmp_path，不污染仓库状态）。"""
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
HOOK = LIB / "_meta" / "hooks" / "agent_hooks.py"
CLI = LIB / "_meta" / "asset_cli.py"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, LIB / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tk = _load("_hub_tasks_test", "_meta/hub/tasks.py")


def _run_hook(event, payload, tdir):
    env = {**os.environ, "ASSET_HUB_TASKS_DIR": str(tdir), "ASSET_HUB_FIND_NOLOG": "1"}
    r = subprocess.run([sys.executable, str(HOOK), f"--event={event}"],
                       input=json.dumps(payload, ensure_ascii=False),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return r, (json.loads(r.stdout) if r.stdout.strip() else None)


# ── 1. YAML 解析/序列化往返与创建 ────────────────────────

def test_parse_multiline_yaml_and_dump_roundtrip(tmp_path):
    raw = """---
id: T-20261116-01
project: asset-hub
title: "标题: 含冒号与 # 井号" # 行尾注释
status: claimed               # todo | claimed | done
owner: alice@pc1:antigravity
session: ag-1
lease_until: "2026-11-16T16:30:00+08:00"
depends_on:
  - T-20261116-00
scope_globs:
  - "_meta/hub/**"
  - "tests/test_tasks.py"
verify_cmd: pytest tests/test_tasks.py -q
subtasks:
  - {id: s1, text: "解析器对齐", done: true}
  - {id: s2, text: "越界门测试", done: false}
updated_at: "2026-11-16T16:00:00+08:00"
extra_field: harness-compat
---

## 契约与目标 (Contract & Goal)
只改 scope_globs 内文件。

## 断点交接 (Delta Handoff)
已完 s1，待补 s2。
"""
    p = tmp_path / "T-20261116-01.md"
    p.write_text(raw, encoding="utf-8")
    parsed = tk.parse_task_file(p)
    assert parsed["id"] == "T-20261116-01"
    assert parsed["title"] == "标题: 含冒号与 # 井号"
    assert parsed["status"] == "claimed"
    assert parsed["depends_on"] == ["T-20261116-00"]
    assert parsed["scope_globs"] == ["_meta/hub/**", "tests/test_tasks.py"]
    assert parsed["subtasks"][0] == {"id": "s1", "text": "解析器对齐", "done": True}
    assert parsed["subtasks"][1]["done"] is False
    assert parsed["extra_field"] == "harness-compat"
    assert "只改 scope_globs" in parsed["contract"]
    assert "已完 s1" in parsed["handoff"]

    dumped = tk.dump_task_file(parsed)
    p2 = tmp_path / "roundtrip.md"
    p2.write_text(dumped, encoding="utf-8")
    reparsed = tk.parse_task_file(p2)
    assert reparsed == parsed


def test_create_task_auto_id_sequence(tmp_path):
    t1 = tk.create_task(tmp_path, "任务一", scope_globs=["_meta/hub/**"],
                        subtasks=["步骤A", "步骤B"])
    t2 = tk.create_task(tmp_path, "任务二")
    assert t1["id"].endswith("-01") and t2["id"].endswith("-02")
    assert t1["subtasks"] == [
        {"id": "s1", "text": "步骤A", "done": False},
        {"id": "s2", "text": "步骤B", "done": False},
    ]


# ── 2. 认领、TTL 租约与依赖阻断 ──────────────────────────

def test_claim_ttl_and_depends_on(tmp_path):
    t1 = tk.create_task(tmp_path, "前置基础", verify_cmd="")
    t2 = tk.create_task(tmp_path, "下游任务", depends_on=[t1["id"]])

    # 前置未 done 阻断认领
    ok, msg, _ = tk.claim_task(tmp_path, t2["id"], owner="bob", session="s2")
    assert not ok and "前置任务" in msg

    # 完成前置后可认领
    ok_done, _, _ = tk.complete_task(tmp_path, t1["id"], lib_root=LIB, skip_verify=True)
    assert ok_done

    t0 = datetime(2026, 11, 16, 10, 0, 0).astimezone()
    ok_a, _, _ = tk.claim_task(tmp_path, t2["id"], owner="alice@pc1", session="s1", ttl_minutes=30, now=t0)
    assert ok_a

    # 活跃租约内他方无法抢锁；同会话可续租
    ok_steal, msg_s, _ = tk.claim_task(tmp_path, t2["id"], owner="bob@pc2", session="s2", now=t0 + timedelta(minutes=10))
    assert not ok_steal and "alice@pc1" in msg_s
    ok_renew, _, _ = tk.claim_task(tmp_path, t2["id"], owner="", session="s1", ttl_minutes=30, now=t0 + timedelta(minutes=10))
    assert ok_renew

    # 超过 TTL 后自动失效可被接管（防死锁）
    ok_take, _, taken = tk.claim_task(tmp_path, t2["id"], owner="bob@pc2", session="s2", now=t0 + timedelta(minutes=45))
    assert ok_take and taken["owner"] == "bob@pc2" and taken["session"] == "s2"


# ── 3. scope_globs 越界硬拦截 & PreToolUse 双协议 ───────

def test_check_write_scope_isolation(tmp_path):
    tk.create_task(tmp_path, "开发核心", task_id="T-1", scope_globs=["_meta/hub/**"])
    tk.create_task(tmp_path, "补写测试", task_id="T-2", scope_globs=["tests/test_tasks.py"])
    now = datetime.now().astimezone().replace(microsecond=0)
    tk.claim_task(tmp_path, "T-1", owner="alice", session="s1", now=now)
    tk.claim_task(tmp_path, "T-2", owner="bob", session="s2", now=now)

    # Bob 不能改 Alice 锁定的 _meta/hub/tasks.py
    ok, reason = tk.check_write_scope(tmp_path, LIB / "_meta" / "hub" / "tasks.py", session="s2", lib_root=LIB, now=now)
    assert not ok and "T-1" in reason

    # Bob 也不能超出自身 T-2 声明改 INDEX.md
    ok2, reason2 = tk.check_write_scope(tmp_path, LIB / "INDEX.md", session="s2", lib_root=LIB, now=now)
    assert not ok2 and "T-2" in reason2 and "超出" in reason2

    # Bob 可以改自己的 tests/test_tasks.py 与 .tasks/ 与库外 tmp
    assert tk.check_write_scope(tmp_path, LIB / "tests" / "test_tasks.py", session="s2", lib_root=LIB, now=now)[0]
    assert tk.check_write_scope(tmp_path, LIB / ".tasks" / "T-2.md", session="s2", lib_root=LIB, now=now)[0]
    assert tk.check_write_scope(tmp_path, tmp_path / "scratch.py", session="s2", lib_root=LIB, now=now)[0]


def test_pretooluse_hook_blocks_and_allows_both_protocols(tmp_path):
    tk.create_task(tmp_path, "核心模块", task_id="T-10", scope_globs=["_meta/hub/**"])
    tk.claim_task(tmp_path, "T-10", owner="alice", session="sess-alice")

    target_locked = str(LIB / "_meta" / "hub" / "tasks.py")
    target_free = str(LIB / "README.md")

    # Claude Code PreToolUse: 踩锁区 deny，非锁区静默放行
    _, z_deny = _run_hook("pre", {
        "tool_name": "Edit", "tool_input": {"file_path": target_locked}, "session_id": "sess-bob"
    }, tmp_path)
    assert z_deny and z_deny["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "T-10" in z_deny["hookSpecificOutput"]["permissionDecisionReason"]

    _, z_allow = _run_hook("pre", {
        "tool_name": "Edit", "tool_input": {"file_path": target_free}, "session_id": "sess-bob"
    }, tmp_path)
    assert z_allow is None

    # Antigravity PreToolUse: 踩锁区 {"decision": "deny"}, 允许 {"decision": "allow"}
    _, ag_deny = _run_hook("pre", {
        "toolCall": {"name": "replace_file_content", "args": {"TargetFile": target_locked}},
        "conversationId": "ag-bob",
    }, tmp_path)
    assert ag_deny and ag_deny["decision"] == "deny" and "T-10" in ag_deny["reason"]

    _, ag_allow = _run_hook("pre", {
        "toolCall": {"name": "edit_file", "args": {"Path": target_locked}},
        "conversationId": "sess-alice",
    }, tmp_path)
    assert ag_allow == {"decision": "allow"}


# ── 4. Converge 验收门、DeltaReplay 续接、动态看板 ────────

def test_converge_verify_gate_built_not_done(tmp_path):
    py = f'"{sys.executable}"'
    tk.create_task(tmp_path, "失败验收", task_id="T-20", verify_cmd=f'{py} -c "import sys; sys.exit(3)"')
    ok_fail, msg_fail, t_after = tk.complete_task(tmp_path, "T-20", lib_root=LIB)
    assert not ok_fail and "rc=3" in msg_fail
    assert tk.parse_task_file(tmp_path / "T-20.md")["status"] != "done"

    tk.create_task(tmp_path, "通过验收", task_id="T-21",
                   verify_cmd=f'{py} -c "print(1)"', subtasks=["实现", "自测"])
    ok_pass, _, t_done = tk.complete_task(tmp_path, "T-21", lib_root=LIB)
    assert ok_pass and t_done["status"] == "done" and t_done["lease_until"] == ""
    assert all(s["done"] for s in t_done["subtasks"])


def test_handoff_deltareplay_and_board(tmp_path):
    tk.create_task(tmp_path, "断点交接示例", task_id="T-20261116-99",
                   scope_globs=["_meta/hub/tasks.py"],
                   subtasks=["建表", "补单测", "同步"])
    tk.claim_task(tmp_path, "T-20261116-99", owner="alice", session="s1")
    ok, _, handed = tk.handoff_task(
        tmp_path, "T-20261116-99",
        note="建表已完成，差补单测与同步，直接改 tests/test_tasks.py",
        done_subtasks=["s1"],
    )
    assert ok and handed["status"] == "todo" and handed["lease_until"] == ""
    assert handed["subtasks"][0]["done"] is True and handed["subtasks"][1]["done"] is False

    # 下一个会话在 prompt 里提及任务号，钩子自动认领并输出 ≤150 字断点摘要
    ctx = tk.session_context(tmp_path, session="s2", owner="bob@pc2",
                             prompt="继续 T-20261116-99 把单测补完")
    assert ctx and len(ctx) <= 150
    assert "T-20261116-99" in ctx and "补单测" in ctx
    assert tk.parse_task_file(tmp_path / "T-20261116-99.md")["session"] == "s2"

    # 端到端 Prompt Hook 也注入该任务胶囊上下文
    _, pr = _run_hook("prompt", {
        "lastUserInput": "接手 T-20261116-99 继续干", "conversationId": "ag-new"
    }, tmp_path)
    # 已被 s2 活跃锁定，ag-new 不能抢锁；释放后 ag-new 可自动认领并注入
    tk.handoff_task(tmp_path, "T-20261116-99", note="再交给 ag-new")
    _, pr2 = _run_hook("prompt", {
        "lastUserInput": "接手 T-20261116-99 继续干", "conversationId": "ag-new"
    }, tmp_path)
    msg2 = pr2["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert "T-20261116-99" in msg2 and "ag-new" in tk.parse_task_file(tmp_path / "T-20261116-99.md")["session"]

    board = tk.render_board(tmp_path)
    assert "T-20261116-99" in board and "1/3" in board and "_meta/hub/tasks.py" in board


# ── 5. 跨设备 Git tasks 分支同步（零污染 master） ──────────

def test_git_tasks_branch_cross_device_sync(tmp_path):
    def _g(cwd, *args):
        return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                              capture_output=True, text=True, encoding="utf-8")

    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)

    dev_a = tmp_path / "dev_a"
    subprocess.run(["git", "clone", str(remote), str(dev_a)], check=True, capture_output=True)
    _g(dev_a, "config", "user.name", "Alice")
    _g(dev_a, "config", "user.email", "alice@example.com")
    (dev_a / "README.md").write_text("main\n", encoding="utf-8")
    _g(dev_a, "add", "README.md")
    _g(dev_a, "commit", "-m", "init master")
    _g(dev_a, "branch", "-M", "master")
    _g(dev_a, "push", "-u", "origin", "master")
    master_before = _g(dev_a, "rev-parse", "master").stdout.strip()

    # 设备 A 创建任务并通过 tasks 分支同步到远端
    tdir_a = dev_a / ".tasks"
    tk.create_task(tdir_a, "跨设备协作测试", task_id="T-30", scope_globs=["src/**"])
    ok_a, msg_a = tk.sync_tasks_git(tdir_a, lib_root=dev_a, init_branch=True)
    assert ok_a, msg_a

    # master 提交历史零变化
    assert _g(dev_a, "rev-parse", "master").stdout.strip() == master_before

    # 设备 B 克隆同一远端并挂载 tasks 分支，自动收到 T-30
    dev_b = tmp_path / "dev_b"
    subprocess.run(["git", "clone", str(remote), str(dev_b)], check=True, capture_output=True)
    _g(dev_b, "config", "user.name", "Bob")
    _g(dev_b, "config", "user.email", "bob@example.com")
    tdir_b = dev_b / ".tasks"
    ok_b, msg_b = tk.sync_tasks_git(tdir_b, lib_root=dev_b, init_branch=True)
    assert ok_b, msg_b
    assert (tdir_b / "T-30.md").is_file()
    assert tk.parse_task_file(tdir_b / "T-30.md")["title"] == "跨设备协作测试"


def test_cli_task_subcommand_roundtrip(tmp_path):
    env = {**os.environ, "ASSET_HUB_TASKS_DIR": str(tmp_path)}
    def _cli(*args):
        return subprocess.run([sys.executable, str(CLI), "task", *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)

    assert _cli("new", "CLI任务", "--id=T-90", "--scope=tests/test_tasks.py", "--subtasks=a|b").returncode == 0
    assert _cli("claim", "T-90", "--owner=cli-u", "--session=cli-s").returncode == 0
    chk = _cli("check-write", "--path=README.md", "--session=cli-s", "--json")
    assert chk.returncode == 1 and json.loads(chk.stdout)["ok"] is False
    assert _cli("handoff", "T-90", "--done=s1", "--note=完成a").returncode == 0
    assert _cli("done", "T-90", "--skip-verify").returncode == 0
    bd = _cli("board")
    assert bd.returncode == 0 and "T-90" in bd.stdout and "2/2" in bd.stdout


def test_cross_project_sibling_scope_globs(tmp_path):
    ws = tmp_path / "AI项目"
    hub = ws / "成果整理"
    rev = ws / "逆向工程"
    tdir = hub / ".tasks"
    (rev / "work").mkdir(parents=True)
    tk.create_task(tdir, "跨仓逆向任务", task_id="T-95", scope_globs=["逆向工程/work/**"])
    ok, _, _ = tk.claim_task(tdir, "T-95", owner="ag", session="s-ag")
    assert ok

    assert tk.check_write_scope(tdir, rev / "work" / "a.py", session="s-ag", owner="ag", lib_root=hub)[0] is True
    ok_out, msg_out = tk.check_write_scope(tdir, rev / "other.py", session="s-ag", owner="ag", lib_root=hub)
    assert not ok_out and "超出当前任务" in msg_out
    ok_other, msg_other = tk.check_write_scope(tdir, rev / "work" / "a.py", session="s-zc", owner="zc", lib_root=hub)
    assert not ok_other and "归属活跃任务" in msg_other
    assert tk.check_write_scope(tdir, tmp_path / "outside" / "temp.py", session="s-ag", owner="ag", lib_root=hub)[0] is True


def test_session_context_project_isolation_and_handoff(tmp_path):
    tdir = tmp_path / ".tasks"
    tk.create_task(tdir, "逆向断点任务", project="逆向工程", task_id="T-96", scope_globs=["逆向工程/work/**"])
    tk.claim_task(tdir, "T-96", owner="user@pc:zcode", session="s-zc")
    tk.handoff_task(tdir, "T-96", note="已完成trace，剩对拍")

    # 同项目下即使换了另一客户端（Antigravity 无 T-96 点名），也能自动看到待接力提示
    ctx_rev = tk.session_context(tdir, session="s-ag", owner="user@pc:antigravity", project="逆向工程")
    assert "[待接力 T-96]" in ctx_rev and "已完成trace" in ctx_rev

    # 切到《成果整理》项目窗口时，完全隔离，不串台注入《逆向工程》任务
    assert tk.session_context(tdir, session="s-zc", owner="user@pc:zcode", project="成果整理") == ""
    assert tk.session_context(tdir, session="s-ag", owner="user@pc:antigravity", project="成果整理") == ""
