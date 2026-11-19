# -*- coding: utf-8 -*-
"""arch_metrics 纯函数用例（McCabe 计数、引用解析、扇入口径）。"""
import importlib.util
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("arch_metrics", LIB / "_meta" / "arch_metrics.py")
am = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(am)


def test_mccabe_linear_is_one():
    assert am.mccabe_of("a = 1\nb = 2\n") == 1


def test_mccabe_counts_branches():
    src = "if a:\n    pass\nelif b:\n    pass\nelse:\n    pass\n"
    assert am.mccabe_of(src) >= 3


def test_mccabe_syntax_error_returns_none():
    assert am.mccabe_of("def broken(:\n") is None


def test_parse_refs_accepts_both_slashes():
    text = r"python scripts\dxf\dxf_tools.py 与 scripts/dxf/dxf_tools.py"
    refs = am.parse_refs(text)
    assert "scripts/dxf/dxf_tools.py" in refs


def test_parse_refs_rejects_outside_library():
    assert am.parse_refs("D:/external/x.py") == set()


def test_governance_line_limits_pass():
    assert am.check_line_limits(LIB) == []


def test_boundary_imports_pass_on_repo():
    assert am.check_boundary_imports(LIB) == []


def test_boundary_imports_rejects_unreviewed_net_and_dep(tmp_path):
    meta = tmp_path / "_meta"
    meta.mkdir()
    (meta / "evil_net.py").write_text("import urllib.request\n", encoding="utf-8")
    (meta / "evil_dep.py").write_text("import foo_third_party\n", encoding="utf-8")
    (meta / "pure.py").write_text("import json\nfrom pathlib import Path\n", encoding="utf-8")
    errs = am.check_boundary_imports(tmp_path)
    assert any("evil_net.py" in e and "urllib" in e for e in errs)
    assert any("evil_dep.py" in e and "foo_third_party" in e for e in errs)
    assert not any("pure.py" in e for e in errs)

