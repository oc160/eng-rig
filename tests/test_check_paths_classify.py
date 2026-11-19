# -*- coding: utf-8 -*-
"""check_paths 判定的纯函数负向用例。

存在意义（2026-10-20 架构评估发现）：旧版"末段试探"规则是"目标不存在但父目录存在就放过"，
实测静默吞掉 18 条真实悬空路径（如 D:/RAGFlowBackup），而全量检查仍报"路径全部有效"。
本文件锁死三级判定的边界，防止再次退化。
"""
import importlib.util
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_paths", LIB / "_meta" / "check_paths.py")
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)


def _exists_only(*allowed):
    allowed = {a for a in allowed}
    return lambda p: p in allowed


def test_missing_leaf_not_forgiven_by_parent():
    """父目录存在 ≠ 路径有效：真实悬空叶子必须判 missing。"""
    text = "备份在 D:/RAGFlowBackup 目录下"
    end = text.index("D:/RAGFlowBackup") + len("D:/RAGFlowBackup")
    verdict = cp.judge("D:/RAGFlowBackup", text, end, _exists_only("D:/"))
    assert verdict == "missing"


def test_space_truncation_is_suspect_when_continuation_has_slash():
    """'C:/Program Files/...' 被空格截断，且后续仍有 'Files/' → suspect。"""
    text = "见 C:/Program Files/Microsoft Office/root"
    end = text.index("C:/Program") + len("C:/Program")
    verdict = cp.judge("C:/Program", text, end, _exists_only("C:/", "C:/Program Files"))
    assert verdict == "suspect"


def test_plus_truncation_is_suspect():
    """'喀克24+700' 被加号截断（加号不是散文终止符）→ suspect。"""
    text = "目录 D:/demo/喀克24+700闸室"
    end = text.index("D:/demo/喀克24") + len("D:/demo/喀克24")
    verdict = cp.judge("D:/demo/喀克24", text, end, _exists_only("D:/demo"))
    assert verdict == "suspect"


def test_existing_path_is_ok():
    assert cp.judge("D:/demo", "D:/demo", 7, _exists_only("D:/demo")) == "ok"


def test_missing_without_truncation_marker_is_missing():
    """后接中文标点/换行的缺失路径一律 missing（不得降级）。"""
    for text, path in (("见 D:/不存在目录。", "D:/不存在目录"),
                       ("路径 D:/不存在目录\n", "D:/不存在目录")):
        end = text.index(path) + len(path)
        assert cp.judge(path, text, end, _exists_only("D:/")) == "missing"


def test_suspect_requires_parent_to_exist():
    """截断形态但父目录也不存在 → missing（不是 suspect）。"""
    text = "C:/Program Files/x"
    end = len("C:/Program")
    assert cp.judge("C:/Program", text, end, _exists_only()) == "missing"


# ── W0-1 inbox 在途件年龄分级（2026-11-02）────────────────────────────
def test_inbox_age_within_grace_is_ok():
    """captured 距今 10 天（≤14）→ 在途豁免成立（降警告）。"""
    from datetime import datetime, timedelta
    now = datetime(2026, 11, 2, 12, 0, 0)
    captured = (now - timedelta(days=10)).isoformat()
    assert cp.inbox_age_ok(captured, now) is True


def test_inbox_age_over_grace_blocks():
    """captured 距今 20 天（>14）→ 豁免不成立（维持拦截，暂存区不得成为安全飞地）。"""
    from datetime import datetime, timedelta
    now = datetime(2026, 11, 2, 12, 0, 0)
    captured = (now - timedelta(days=20)).isoformat()
    assert cp.inbox_age_ok(captured, now) is False
    # 解析失败同样按超龄拦截（meta 在场却读不出日期属结构故障）
    assert cp.inbox_age_ok("not-a-date", now) is False
    assert cp.inbox_age_ok("", now) is False


def test_inbox_boundary_is_14_days():
    """边界值：恰好 14 天仍在豁免内（超龄从第 15 天起算）。"""
    from datetime import datetime, timedelta
    now = datetime(2026, 11, 2, 12, 0, 0)
    assert cp.inbox_age_ok((now - timedelta(days=cp.INBOX_MAX_AGE_DAYS)).isoformat(), now) is True
    assert cp.inbox_age_ok((now - timedelta(days=cp.INBOX_MAX_AGE_DAYS + 1)).isoformat(), now) is False


def test_triage_report_intransit_by_mtime():
    """分诊报告原样引用捕获源路径——v1.2 契约：同步闸一律在途（警告）；
    --strict-inbox 异步口径才按龄恢复严格（2026-11-16 Antigravity P0-1，终结 SKIP 破窗）。"""
    from datetime import datetime, timedelta
    now = datetime(2026, 11, 2, 12, 0, 0)
    assert cp.triage_report_intransit(now - timedelta(days=2), now) is True       # 同步闸：一律在途
    assert cp.triage_report_intransit(now - timedelta(days=20), now) is True      # 同步闸：超龄仍警告
    assert cp.triage_report_intransit(now - timedelta(days=2), now, strict=True) is True   # strict：新鲜在途
    assert cp.triage_report_intransit(now - timedelta(days=20), now, strict=True) is False  # strict：超龄严格


def test_skills_index_garrison(tmp_path, monkeypatch):
    """⑦ 技能防孤岛守卫（Antigravity 首评 P3-2）：未在 INDEX 登记的技能必须报"孤岛"。"""
    NL = chr(10)
    sk = tmp_path / "skills" / "xskill"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        NL.join(["---", "name: xskill",
                 "description: 这是一个足够长超过三十个字符的示例技能描述用于通过校验。",
                 "---", "正文"]),
        encoding="utf-8")
    (tmp_path / "INDEX.md").write_text("# idx" + NL + "（不含技能名）" + NL, encoding="utf-8")
    monkeypatch.setattr(cp, "LIB", tmp_path)
    probs = cp.check_skills()
    assert any("孤岛" in x and "xskill" in x for x in probs), probs
    (tmp_path / "INDEX.md").write_text("场景行提到 xskill 技能" + NL, encoding="utf-8")
    assert not any("孤岛" in x for x in cp.check_skills())


def test_check_internal_refs_catches_broken_and_skips_fences_and_external(tmp_path):
    sk = tmp_path / "skills" / "demo"
    (sk / "scripts").mkdir(parents=True)
    (sk / "scripts" / "local.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "docs.md").write_text("# doc\n", encoding="utf-8")
    (sk / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 示例技能描述超过三十个字用来测试库内链接与脚本引用校验。\n---\n"
        "见 [文档](../../docs.md) 与 [缺失](missing.md)。\n"
        "脚本 scripts/local.py 存在，scripts/gone.py 不存在。\n"
        "外部路径 D:/work/scripts/ext.py 不误报。\n"
        "```bash\n"
        "python scripts/in_fence_only.py\n"
        "[假链](in_fence.md)\n"
        "```\n",
        encoding="utf-8",
    )
    miss = cp.check_internal_refs(tmp_path)
    targets = {t for _, t in miss}
    assert "missing.md" in targets
    assert "scripts/gone.py" in targets
    assert "../../docs.md" not in targets
    assert "scripts/local.py" not in targets
    assert "scripts/in_fence_only.py" not in targets
    assert "in_fence.md" not in targets
    assert not any("ext.py" in t for t in targets)

