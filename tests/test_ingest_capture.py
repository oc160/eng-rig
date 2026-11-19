# -*- coding: utf-8 -*-
"""产出捕获三段流（库外暂存 → 敏感预扫 → 发布/隔离）与 fail-closed 纪律。

背景（reviewer 评审 2026-10-27 采纳）：原 ingest 自动 git add+commit 违反
"自动化只发现"，且未扫描就落库内。现改为：暂存区在库外，干净才发布，
命中/扫描器异常一律隔离（fail-closed），全程零 git 操作。
"""
import importlib.util
import json
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("asset_cli_ing", LIB / "_meta" / "asset_cli.py")
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

PAD = "# pad line for size threshold\n" * 60


def _redirect(monkeypatch, tmp_path):
    # pytest 的 tmp_path 位于系统 Temp 下，须像 test_agent_hooks 一样放行 temp 段
    monkeypatch.setenv("ASSET_HUB_INGEST_ALLOW_TEMP", "1")
    monkeypatch.setattr(cli, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(cli, "STAGING", tmp_path / "staging")


def _src(tmp_path, name, text):
    f = tmp_path / "src" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return f


def _run(f, capsys, session="t"):
    cli.cmd_ingest([f"--file={f}", f"--session={session}"])
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_same_source_session_updates_representative(monkeypatch, tmp_path, capsys):
    # inbox 止血（review Q5）：同源同会话不复制 -1..-N，原地更新代表件并记版本链
    _redirect(monkeypatch, tmp_path)
    f = _src(tmp_path, "tool_d.py", "import argparse\n# v1\n" + PAD)
    out1 = _run(f, capsys, session="s1")
    assert out1["status"] == "captured" and "updated" not in out1
    f.write_text("import argparse\n# v2 内容变了\n" + PAD, encoding="utf-8")
    out2 = _run(f, capsys, session="s1")
    assert out2["status"] == "captured" and out2["updated"] is True and out2["versions"] == 2
    files = [p for p in (tmp_path / "inbox").rglob("tool_d*.py") if p.suffix == ".py"]
    assert len(files) == 1                          # 没有复制出 -1 副本
    metas = [m for m in (tmp_path / "inbox").rglob("*.meta.json") if m.name.startswith("tool_d")]
    meta = json.loads(metas[0].read_text(encoding="utf-8"))
    assert meta["versions"] == 2 and len(meta["sha_history"]) == 1


def test_clean_file_publishes_without_git(monkeypatch, tmp_path, capsys):
    _redirect(monkeypatch, tmp_path)
    f = _src(tmp_path, "tool_a.py", "import argparse\nimport sys\n\n" + PAD)
    out = _run(f, capsys)
    assert out["status"] == "captured" and out["published"] is True
    assert list((tmp_path / "inbox").rglob("tool_a.py"))
    assert not list((tmp_path / "staging").rglob("tool_a.py"))      # 暂存副本已清
    metas = list((tmp_path / "inbox").rglob("*.meta.json"))
    assert metas and json.loads(metas[0].read_text(encoding="utf-8"))["status"] == "待蒸馏"


def test_secret_file_quarantined_outside_repo(monkeypatch, tmp_path, capsys):
    _redirect(monkeypatch, tmp_path)
    secret = 'password = "abcdefg123456"'
    f = _src(tmp_path, "tool_b.py", "import argparse\n" + secret + "\n" + PAD)
    out = _run(f, capsys)
    assert out["status"] == "quarantined"
    assert out["staging"].startswith(str(tmp_path / "staging"))
    assert not list((tmp_path / "inbox").rglob("tool_b.py"))        # 库内零落盘
    meta = json.loads(next((tmp_path / "staging").rglob("tool_b.py.meta.json"))
                       .read_text(encoding="utf-8"))
    assert meta["status"] == "隔离待审" and meta["flags"]
    assert "abcdefg123456" not in json.dumps(out)                   # 告警不回显内容


def test_duplicate_capture_skipped(monkeypatch, tmp_path, capsys):
    _redirect(monkeypatch, tmp_path)
    f = _src(tmp_path, "tool_c.py", "import argparse\n" + PAD)
    assert _run(f, capsys)["status"] == "captured"
    assert _run(f, capsys)["status"] == "skipped"                   # 暂存+收件箱合并去重


def test_prescan_fail_closed_on_error():
    # 扫描器加载失败必须返回非空——宁可隔离，不可带密进库
    orig = cli.LIB
    try:
        cli.LIB = cli.LIB / "_no_such_dir_zzz"
        assert cli._prescan(b"print(1)\n", "a.py")
    finally:
        cli.LIB = orig


def test_marker_cannot_bypass_prescan(tmp_path):
    # 复审 B1：库外文件加 test-fixture 标记不再放行——必须隔离
    assert cli._prescan(b'password = "abcdefg123456"  # test-fixture\n', "note.md")


def test_prescan_matches_gate_scan_bytes():
    """W1 预扫一致性（2026-11-02）：捕获预扫与服务端闸共用同一扫描器——本测试锁死
    两者对同一 fixture 的判定不漂移（谁单独改了一边，这里立刻红）。"""
    import io
    import zipfile
    _g = importlib.util.spec_from_file_location("gate_parity", LIB / "_meta" / "gate_check.py")
    gate = importlib.util.module_from_spec(_g)
    _g.loader.exec_module(gate)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<w:p>password = "abcdefg123456"</w:p>')
    cases = [
        ("clean.py", b"import argparse\n# pad\n" * 60),
        ("dirty.py", b'password = "abcdefg123456"\n'),
        ("note.md", b'password = "abcdefg123456"  # test-fixture\n'),   # 库外标记不放行
        ("嵌套.docx", buf.getvalue()),                                    # zip 容器内嵌密
    ]
    for name, data in cases:
        a = bool(cli._prescan(data, name))
        b = bool(gate._scan_bytes(name, data))
        assert a == b, f"{name}: _prescan={a} / gate={b} —— 扫描器判定漂移"
    # 脏例须为真脏（防两边一起退化成"永远干净"的假一致）
    assert any(cli._prescan(d, n) for n, d in cases[1:])


def test_ingest_skips_process_docs_and_probes_but_captures_reusable_md(monkeypatch, tmp_path, capsys):
    """源头收紧（2026-11-16）：过程文档 PLAN.md 与 probe_*.py 不入收件箱；带技能头的 .md 正常捕获。"""
    _redirect(monkeypatch, tmp_path)
    plan = _src(tmp_path, "PLAN.md", "# Project Plan\n" + "step details\n" * 120)
    probe = _src(tmp_path, "probe_test.py", "import argparse\n" + PAD)
    skill_md = _src(tmp_path, "my_skill.md",
                    "---\nname: my-skill\ndescription: |\n  Demo\n  Triggers: foo, bar\n---\n" + PAD)
    assert _run(plan, capsys)["status"] == "skipped"
    assert _run(probe, capsys)["status"] == "skipped"
    out = _run(skill_md, capsys)
    assert out["status"] == "captured" and out["published"] is True


def test_ingest_skips_oversized_file(monkeypatch, tmp_path, capsys):
    _redirect(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "INGEST_MAX_BYTES", 1024)
    big = _src(tmp_path, "tool_big.py", "import argparse\n" + "# x\n" * 500)
    out = _run(big, capsys)
    assert out["status"] == "skipped" and out["why"] == "too_large"
