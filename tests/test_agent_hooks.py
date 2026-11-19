# -*- coding: utf-8 -*-
"""零口令钩子的行为用例：提问自动路由、短句静默、产出自动入库、SessionStart 一行说明。"""
import json
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
HOOK = LIB / "_meta" / "hooks" / "agent_hooks.py"


def run(event, payload, env=None):
    e = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, str(HOOK), "--event=" + event],
                          input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", env=e)


def out(res):
    return json.loads(res.stdout) if res.stdout.strip() else None


def test_prompt_routes_to_asset():
    has_ft = (LIB / "skills" / "format-toolbox").is_dir()
    prompt = "帮我把这 12 份报告的页脚和空白页处理一下" if has_ft else "evaluate github repo and absorb open source"
    want = "format-toolbox" if has_ft else "oss-absorb"
    res = run("prompt", {"prompt": prompt, "session_id": "t"},
              env={"ASSET_HUB_FIND_NOLOG": "1"})
    data = out(res)
    assert data and "Candidate:" in data["hookSpecificOutput"]["additionalContext"]
    assert want in data["hookSpecificOutput"]["additionalContext"]


def test_short_prompt_is_silent():
    assert run("prompt", {"prompt": "你好"}).stdout.strip() == ""


def test_start_emits_one_line():
    data = out(run("start", {}))
    assert data and data["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_post_captures_new_script(tmp_path):
    f = tmp_path / "tool_demo.py"
    f.write_text("import argparse\n\ndef main():\n    pass\n\nif __name__ == '__main__':\n    main()\n"
                 + "# pad\n" * 200, encoding="utf-8")
    res = run("post", {"tool_input": {"file_path": str(f)}, "session_id": "t"},
              env={"ASSET_HUB_INGEST_DRY": "1", "ASSET_HUB_INGEST_ALLOW_TEMP": "1"})
    data = out(res)
    assert data and "Captured to inbox" in data["hookSpecificOutput"]["additionalContext"]


def test_post_ignores_unrelated_file(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("x" * 2000, encoding="utf-8")
    assert run("post", {"tool_input": {"file_path": str(f)}}).stdout.strip() == ""


def test_antigravity_protocol_start_prompt_post(tmp_path):
    """Antigravity L1 协议：camelCase 载荷走 injectSteps 与 {} 空回执，read_file 不触发 ingest。"""
    has_ft = (LIB / "skills" / "format-toolbox").is_dir()
    prompt = "帮我把这 12 份报告的页脚和空白页处理一下" if has_ft else "evaluate github repo and absorb open source"
    want = "format-toolbox" if has_ft else "oss-absorb"
    st = out(run("start", {"conversationId": "ag-1", "workspacePaths": [str(LIB)]}))
    assert st and "injectSteps" in st and st["injectSteps"][0]["systemMessage"]["systemMessage"]
    pr = out(run("prompt", {"lastUserInput": prompt, "conversationId": "ag-1"},
                 env={"ASSET_HUB_FIND_NOLOG": "1"}))
    assert pr and "injectSteps" in pr and want in pr["injectSteps"][0]["systemMessage"]["systemMessage"]
    silent = out(run("prompt", {"lastUserInput": "你好", "conversationId": "ag-1"}))
    assert silent == {}
    f = tmp_path / "tool_ag.py"
    f.write_text("import argparse\n" + "# pad\n" * 200, encoding="utf-8")
    ro = out(run("post", {"toolCall": {"name": "read_file", "args": {"Path": str(f)}}, "conversationId": "ag-1"},
                 env={"ASSET_HUB_INGEST_DRY": "1", "ASSET_HUB_INGEST_ALLOW_TEMP": "1"}))
    assert ro == {}


def test_heal_skill_junctions_repairs_missing_and_preserves_unmanaged(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, str(LIB / "_meta" / "hooks"))
    import agent_hooks as ah

    lib = tmp_path / "hub"
    (lib / "skills" / "demo-skill").mkdir(parents=True)
    (lib / "skills" / "demo-skill" / "SKILL.md").write_text("# demo\n", encoding="utf-8")
    (lib / "skills" / "keep-real").mkdir(parents=True)
    (lib / "skills" / "keep-real" / "SKILL.md").write_text("# real\n", encoding="utf-8")
    (tmp_path / ".agents").mkdir()
    (lib / ".agents").mkdir()

    # Existing non-empty unmanaged dir should NOT be overwritten
    user_skills = tmp_path / "home" / ".agents" / "skills"
    (user_skills / "keep-real").mkdir(parents=True)
    (user_skills / "keep-real" / "custom.txt").write_text("user local\n", encoding="utf-8")

    monkeypatch.setattr(ah, "LIB", lib)
    monkeypatch.setattr(ah.Path, "home", staticmethod(lambda: tmp_path / "home"))

    note = ah._heal_skill_junctions()
    assert "healed" in note and "heal failed" in note
    assert (tmp_path / ".agents" / "skills" / "demo-skill" / "SKILL.md").is_file()
    assert (user_skills / "demo-skill" / "SKILL.md").is_file()
    assert (lib / ".agents" / "skills" / "demo-skill" / "SKILL.md").is_file()
    assert (user_skills / "keep-real" / "custom.txt").is_file()
    assert not (user_skills / "keep-real" / "SKILL.md").exists()


def test_capsule_cadence_and_compact_reminder(tmp_path):
    import importlib.util

    sp = importlib.util.spec_from_file_location("tk_cap", LIB / "_meta" / "hub" / "tasks.py")
    tk = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(tk)

    tdir = tmp_path / "tasks"
    tk.create_task(tdir, "节拍验证任务", task_id="T-20261119-01",
                   scope_globs=["_meta/**"], subtasks=["步骤一"])
    tk.claim_task(tdir, "T-20261119-01", owner="tester", session="s-cad")

    cad_file = tmp_path / "cadence.json"
    env = {
        "ASSET_HUB_TASKS_DIR": str(tdir),
        "ASSET_HUB_CADENCE_STATE": str(cad_file),
        "ASSET_HUB_FIND_NOLOG": "1",
    }

    def _msg(payload):
        d = out(run("prompt", payload, env=env)) or {}
        if "injectSteps" in d:
            return d["injectSteps"][0]["systemMessage"]["systemMessage"]
        return (d.get("hookSpecificOutput") or {}).get("additionalContext", "")

    prompt_txt = "帮我核对当前模块进度细节"
    m1 = _msg({"prompt": prompt_txt, "session_id": "s-cad"})
    assert "[Task Capsule T-20261119-01]" in m1 and "[Reminder]" not in m1

    m2 = _msg({"prompt": prompt_txt, "session_id": "s-cad"})
    assert "Task Capsule" not in m2 and "[Reminder]" not in m2

    mc = _msg({"prompt": prompt_txt, "session_id": "s-cad", "after_compact": True})
    assert "[Reminder]" in mc and "T-20261119-01" in mc

    cad_file.write_text(json.dumps({"s-cad": 11}), encoding="utf-8")
    m12 = _msg({"prompt": prompt_txt, "session_id": "s-cad"})
    assert "[Reminder]" in m12 and "T-20261119-01" in m12
    assert not list(tmp_path.glob("*.tmp"))


