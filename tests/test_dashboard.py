# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：test_dashboard.py —— 白盒仪表盘快照采集/脱敏隔离/XSS转义单测
# 来源：2026-11-17 PromptHub 对比后补齐的白盒可观测层
# 提取：2026-11-17
# Author: EngineerRig
# 验证方式：pytest tests/test_dashboard.py -q 全绿
# 重跑命令：python -m pytest -q tests/test_dashboard.py
# 依赖：pytest、纯标准库 _meta/hub/dashboard.py
# ──────────────────────────────────────────────────────────
"""单页白盒仪表盘（_meta/hub/dashboard.py）单元测试。"""
import importlib.util
import json
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, LIB / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dash = _load("_hub_dash_test", "_meta/hub/dashboard.py")
ac = _load("_asset_cli_dash", "_meta/asset_cli.py")


def test_collect_snapshot_hides_quarantined_secrets_and_escapes_html(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSET_HUB_TASKS_DIR", str(tmp_path / "tasks"))
    ibx = tmp_path / "inbox" / "2026-11-17"
    ibx.mkdir(parents=True)
    (ibx / "clean_helper.py").write_text("def hi():\n    return 1\n", encoding="utf-8")
    (ibx / "clean_helper.py.meta.json").write_text(json.dumps({
        "source": "D:/demo/clean_helper.py",
        "captured": "2026-11-17T10:00:00",
        "status": "待蒸馏",
    }), encoding="utf-8")

    # 隔离区文件含真实敏感串：dashboard 只准列元数据，绝不读取/回显正文
    stg = tmp_path / "staging" / "2026-11-17"
    stg.mkdir(parents=True)
    secret_tok = "SECRET_NEVER_LEAK_TOKEN_987654"
    (stg / "bad.py").write_text(f'api_key = "{secret_tok}"\n', encoding="utf-8")
    (stg / "bad.py.meta.json").write_text(json.dumps({
        "source": "D:/demo/bad.py",
        "captured": "2026-11-17T10:05:00",
        "flags": ["bad.py: 命中凭据模式 R2（第 1 行）"],
        "status": "隔离待审",
    }), encoding="utf-8")

    flog = tmp_path / "find.jsonl"
    flog.write_text("\n".join([
        json.dumps({"event": "search", "candidates": [{"id": "skill:task-intake"}], "injected": True}),
        json.dumps({"event": "open", "asset": "skill:task-intake"}),
    ]) + "\n", encoding="utf-8")

    idx = {"assets": [{
        "id": "skill:xss-probe", "kind": "skill", "name": "xss</script>probe",
        "summary": "摘要</script>测试", "triggers": ["触发1"], "anti_triggers": ["禁词"],
        "related": ["skill:task-intake"], "path": "skills/task-intake/SKILL.md",
        "visibility": "public",
    }]}

    snap = dash.collect_snapshot(lib_root=LIB, index_data=idx, find_log=flog,
                                 inbox_dir=tmp_path / "inbox", staging_dir=tmp_path / "staging")
    assert len(snap["inbox"]) == 1 and "def hi()" in snap["inbox"][0]["preview"]
    assert len(snap["staging"]) == 1 and secret_tok not in json.dumps(snap)
    assert snap["telemetry"]["searches"] == 1 and snap["telemetry"]["opens"] == 1

    html = dash.render_html(snap)
    assert secret_tok not in html
    assert "</script>probe" not in html and "<\\/script>probe" in html


def test_cli_ui_generates_dashboard_html_and_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ac, "LOG_DIR", tmp_path)
    monkeypatch.setattr(ac, "FIND_LOG", tmp_path / "find.jsonl")
    monkeypatch.setattr(ac, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(ac, "STAGING", tmp_path / "staging")
    monkeypatch.setattr(ac, "load_index", lambda allow_rebuild=True: ({"assets": []}, False))

    out_html = tmp_path / "my_dash.html"
    rc = ac.cmd_ui(["--no-open", f"--out={out_html}"])
    assert rc == 0 and out_html.is_file()
    txt = out_html.read_text(encoding="utf-8")
    assert "Asset Hub 白盒工作台" in txt and "hub-data" in txt

    capsys.readouterr()
    rc2 = ac.cmd_ui(["--json"])
    assert rc2 == 0
    parsed = json.loads(capsys.readouterr().out)
    assert "assets" in parsed and "telemetry" in parsed and "hosts" in parsed
