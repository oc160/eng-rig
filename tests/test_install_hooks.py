# -*- coding: utf-8 -*-
"""钩子安装器用例：幂等、保留原配置、只增 hooks 键。"""
import json
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
SCRIPT = LIB / "_meta" / "install_hooks.py"


def run(cfg, ag_hooks=None):
    env = {**os.environ, "ZCODE_CONFIG": str(cfg)}
    if ag_hooks is not None:
        env["AG_HOOKS"] = str(ag_hooks)
    return subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def test_idempotent_and_preserves(tmp_path):
    cfg = tmp_path / "config.json"
    ag = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"plugins": {"enabledPlugins": {"x@y": True}}}), encoding="utf-8")
    assert run(cfg, ag).returncode == 0
    assert run(cfg, ag).returncode == 0
    d = json.loads(cfg.read_text(encoding="utf-8"))
    assert d["plugins"]["enabledPlugins"]["x@y"] is True
    assert d["hooks"]["enabled"] is True
    for ev in ("SessionStart", "UserPromptSubmit", "PostToolUse"):
        assert len(d["hooks"]["events"][ev]) == 1
        args = d["hooks"]["events"][ev][0]["hooks"][0]["args"]
        assert any("agent_hooks.py" in a for a in args)
    ag_d = json.loads(ag.read_text(encoding="utf-8"))
    assert "asset-hub" in ag_d and "hooks" not in ag_d
    assert ag_d["asset-hub"]["SessionStart"][0]["type"] == "command"
    assert ag_d["asset-hub"]["PreInvocation"][0]["type"] == "command"
    assert ag_d["asset-hub"]["PostToolUse"][0]["matcher"] == "*"


def test_refuses_invalid_json(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{not json", encoding="utf-8")
    assert run(cfg, tmp_path / "hooks.json").returncode == 2


def test_post_tool_matcher(tmp_path):
    cfg = tmp_path / "config.json"
    run(cfg, tmp_path / "hooks.json")
    d = json.loads(cfg.read_text(encoding="utf-8"))
    assert d["hooks"]["events"]["PostToolUse"][0]["matcher"] == "Write|Edit|ApplyPatch"


def test_global_antigravity_hooks_written(tmp_path):
    cfg = tmp_path / "config.json"
    ag = tmp_path / "local_hooks.json"
    ag_global = tmp_path / "global_hooks.json"
    env = {**os.environ, "ZCODE_CONFIG": str(cfg), "AG_HOOKS": str(ag), "AG_GLOBAL_HOOKS": str(ag_global)}
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    assert r.returncode == 0
    gd = json.loads(ag_global.read_text(encoding="utf-8"))
    cmd = gd["asset-hub"]["SessionStart"][0]["command"]
    assert "agent_hooks.py" in cmd and "../_meta/" not in cmd
