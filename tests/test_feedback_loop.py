# -*- coding: utf-8 -*-
"""反馈闭环最后一公里（Antigravity 评审 P2-1 采纳）：注入记忆 → open 自动落 → adopt 自动关联。

机制：inject/find 命中时把 query_id↔asset 记入会话状态（无人需要记 query_id）；
钩子读文件静默调 log-open 比对同文件落 open 事件；收尾 record-retro --reused
自动按 asset_id/asset_path 包含关系补 adopt 事件。
"""
import importlib.util
import json
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("asset_cli_fb", LIB / "_meta" / "asset_cli.py")
ac4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ac4)

STATE_NAME = "last_session_state.json"

# 小索引：单资产 + 精确触发词命中（注入阈值 9.3，本例 ≈12.8）
INDEX = {"assets": [
    {"id": "skill:format-toolbox", "kind": "skill", "name": "format-toolbox",
     "summary": "Word 批量格式处理", "text": "word 批量 格式 处理 页脚 空白页",
     "triggers": ["批量改页脚", "格式工具箱"], "path": "skills/format-toolbox/SKILL.md",
     "visibility": "internal"},
]}


def _redirect(monkeypatch, tmp_path):
    """日志类路径全部指到 tmp；NOLOG 口径关闭（保证真写盘可断言）。"""
    monkeypatch.delenv("ASSET_HUB_FIND_NOLOG", raising=False)
    monkeypatch.setattr(ac4, "LOG_DIR", tmp_path)
    monkeypatch.setattr(ac4, "FIND_LOG", tmp_path / "find.jsonl")
    monkeypatch.setattr(ac4, "SESSION_STATE", tmp_path / STATE_NAME)
    monkeypatch.setattr(ac4, "_SALT", None)
    monkeypatch.setattr(ac4, "RETRO", tmp_path / "retro.jsonl")


def _write_state(tmp_path, session, query_id, ts="2026-11-16T10:00:00"):
    """直接落一份会话状态（与用例1 经 cmd_inject_candidate 产出的结构一致）。"""
    st = {session: {"query_id": query_id, "asset_id": "skill:format-toolbox",
                    "asset_path": "skills/format-toolbox/SKILL.md", "ts": ts}}
    (tmp_path / STATE_NAME).write_text(json.dumps(st, ensure_ascii=False),
                                       encoding="utf-8", newline="\n")


def _last_log(tmp_path):
    p = tmp_path / "find.jsonl"
    assert p.exists(), "find.jsonl 未落盘"
    return json.loads(p.read_text(encoding="utf-8").splitlines()[-1])


def test_inject_candidate_remembered_in_session_state(monkeypatch, tmp_path, capsys):
    # 用例1 注入记忆：注入命中 → SESSION_STATE 的 session 键带 query_id/asset_id/asset_path
    _redirect(monkeypatch, tmp_path)
    monkeypatch.setattr(ac4, "load_index", lambda allow_rebuild=True: (INDEX, False))
    ac4.cmd_inject_candidate(["--prompt=批量改页脚", "--session=s1"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["text"] and out["decision"] == "top1"      # 确实注入（防阈值漂移后用例变空转）
    st = json.loads((tmp_path / STATE_NAME).read_text(encoding="utf-8"))
    rec = st.get("s1")
    assert rec and rec["asset_id"] == "skill:format-toolbox"
    assert rec["asset_path"] == "skills/format-toolbox/SKILL.md"
    assert rec["query_id"] and rec["ts"]


def test_find_remembered_with_cli_key(monkeypatch, tmp_path, capsys):
    # 用例1b：find 命中且无 session → 落 "cli" 键（不落空串键）
    _redirect(monkeypatch, tmp_path)
    monkeypatch.setattr(ac4, "load_index", lambda allow_rebuild=True: (INDEX, False))
    ac4.cmd_find(["批量改页脚"])
    capsys.readouterr()
    st = json.loads((tmp_path / STATE_NAME).read_text(encoding="utf-8"))
    assert "cli" in st and st["cli"]["asset_id"] == "skill:format-toolbox"


def test_log_open_records_open_event(monkeypatch, tmp_path, capsys):
    # 用例2 open 自动落：读的文件=注入记忆里的 asset_path → find.jsonl 末行 event=open 带 query_id
    _redirect(monkeypatch, tmp_path)
    _write_state(tmp_path, "s1", "qid111")
    ac4.cmd_log_open(["--path=skills/format-toolbox/SKILL.md", "--session=s1"])
    out = json.loads(capsys.readouterr().out.strip())
    assert out["logged"] is True
    rec = _last_log(tmp_path)
    assert rec["event"] == "open" and rec["query_id"] == "qid111"
    assert rec["asset"] == "skill:format-toolbox"
    # 路径尾部匹配分支：绝对读路径（前缀不同、尾段相同）也能对上
    ac4.cmd_log_open(["--path=D:/任意库/skills/format-toolbox/SKILL.md", "--session=s1"])
    assert json.loads(capsys.readouterr().out.strip())["logged"] is True


def test_record_retro_auto_adopts_by_asset_id(monkeypatch, tmp_path, capsys):
    # 用例3 adopt 自动关联：无 --query，reused 含 asset_id → 自动落 adopt 且 retro 照常写入
    _redirect(monkeypatch, tmp_path)
    _write_state(tmp_path, "s1", "qid222")
    ac4.cmd_record_retro(["--task=t", "--reused=skill:format-toolbox"])
    capsys.readouterr()
    rec = _last_log(tmp_path)
    assert rec["event"] == "adopt" and rec["query_id"] == "qid222"
    assert rec["asset"] == "skill:format-toolbox"
    lines = (tmp_path / "retro.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["reused"] == "skill:format-toolbox"


def test_log_open_ignores_unrelated_path(monkeypatch, tmp_path, capsys):
    # 用例4 不相关路径不落：logged=false 且 find.jsonl 无 open 事件
    _redirect(monkeypatch, tmp_path)
    _write_state(tmp_path, "s1", "qid333")
    ac4.cmd_log_open(["--path=C:/无关.docx", "--session=s1"])
    out = json.loads(capsys.readouterr().out.strip())
    assert out["logged"] is False
    p = tmp_path / "find.jsonl"
    assert not p.exists() or '"event": "open"' not in p.read_text(encoding="utf-8")


def test_query_hash_propagates_from_search_to_open_and_adopt(monkeypatch, tmp_path, capsys):
    _redirect(monkeypatch, tmp_path)
    monkeypatch.setattr(ac4, "load_index", lambda allow_rebuild=True: (INDEX, False))
    ac4.cmd_inject_candidate(["--prompt=批量改页脚", "--session=s_hash"])
    capsys.readouterr()
    search_rec = _last_log(tmp_path)
    qh = search_rec.get("query_hash", "")
    assert qh
    st = json.loads((tmp_path / STATE_NAME).read_text(encoding="utf-8"))
    assert st["s_hash"].get("query_hash") == qh

    ac4.cmd_log_open(["--path=skills/format-toolbox/SKILL.md", "--session=s_hash"])
    capsys.readouterr()
    open_rec = _last_log(tmp_path)
    assert open_rec["event"] == "open" and open_rec.get("query_hash") == qh

    ac4.cmd_record_retro(["--task=t", "--reused=skill:format-toolbox"])
    capsys.readouterr()
    adopt_rec = _last_log(tmp_path)
    assert adopt_rec["event"] == "adopt" and adopt_rec.get("query_hash") == qh
    assert not list(tmp_path.glob("*.tmp"))

