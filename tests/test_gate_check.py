# -*- coding: utf-8 -*-
"""服务端闸（gate_check）负向用例：合成违规树必须被拒。"""
import importlib.util
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("gate_check", LIB / "_meta" / "gate_check.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)

FM = "---\nname: {n}\ndescription: {d}\n---\n"


def _tree(root):
    (root / "skills" / "ok").mkdir(parents=True)
    (root / "skills" / "ok" / "SKILL.md").write_text(
        FM.format(n="ok", d="这是一个用于演示的合法技能描述，长度明显超过三十个字符的判定门槛。"), encoding="utf-8")
    (root / "_meta").mkdir()
    (root / "_meta" / "规范版本表.yaml").write_text('standards:\n- id: X\n  implemented: "2026-01-01"\n', encoding="utf-8")
    (root / "scripts").mkdir()
    (root / "scripts" / "good.py").write_text("# ── 资产卡 ──\nprint(1)\n", encoding="utf-8")
    return root


def test_clean_tree_passes(tmp_path):
    assert gc.check(_tree(tmp_path)) == []


def test_missing_frontmatter_rejected(tmp_path):
    r = _tree(tmp_path)
    (r / "skills" / "bad").mkdir()
    (r / "skills" / "bad" / "SKILL.md").write_text("没有 frontmatter\n", encoding="utf-8")
    assert any("frontmatter" in e for e in gc.check(r))


def test_unquoted_date_rejected(tmp_path):
    r = _tree(tmp_path)
    (r / "_meta" / "规范版本表.yaml").write_text("standards:\n- id: X\n  implemented: 2026-01-01\n", encoding="utf-8")
    assert any("日期未加引号" in e for e in gc.check(r))


def test_missing_asset_card_rejected(tmp_path):
    r = _tree(tmp_path)
    (r / "scripts" / "bad.py").write_text("print(1)\n", encoding="utf-8")
    assert any("资产卡" in e for e in gc.check(r))


def test_forbidden_path_rejected(tmp_path):
    r = _tree(tmp_path)
    (r / "invoices").mkdir()
    (r / "invoices" / "x.md").write_text("x\n", encoding="utf-8")
    assert any("禁止入库" in e for e in gc.check(r))


def test_secret_pattern_rejected(tmp_path):
    r = _tree(tmp_path)
    (r / "docs").mkdir()
    (r / "docs" / "a.md").write_text('password = "abcdefg123"\n', encoding="utf-8")
    assert any("凭据" in e for e in gc.check(r))


def test_secret_error_does_not_echo_content(tmp_path):
    # 脱敏纪律（reviewer 评审 2026-10-27）：告警只记规则号+行号，不回显命中内容
    r = _tree(tmp_path)
    (r / "docs").mkdir()
    (r / "docs" / "a.md").write_text('password = "abcdefg123"\n', encoding="utf-8")
    errs = gc.check(r)
    assert any("凭据" in e and "R1" in e and "第 1 行" in e for e in errs)
    assert not any("abcdefg123" in e for e in errs)


def test_push_blob_scan_catches_intermediate_secret(tmp_path):
    """推送模式：中间提交含密、最后删除也必须拦住（树级只看 tip 的盲区）。"""
    import subprocess as sp
    wt = tmp_path / "wt"
    sp.run(["git", "init", "-q", "-b", "master", str(wt)], check=True)

    def commit(msg):
        sp.run(["git", "-C", str(wt), "add", "-A"], check=True)
        sp.run(["git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", msg], check=True)

    def head():
        return sp.run(["git", "-C", str(wt), "rev-parse", "HEAD"],
                      capture_output=True, text=True, check=True).stdout.strip()

    (wt / "README.md").write_text("clean\n" * 20, encoding="utf-8")
    commit("c1")
    c1 = head()
    (wt / "s.md").write_text('password = "abcdefg123"\n', encoding="utf-8")
    commit("c2 add secret")
    (wt / "s.md").unlink()
    commit("c3 remove secret")
    c3 = head()

    # 模拟 pre-receive 现场：分支指针仍停在 c1（c3 尚不可达），扫推送新 tip c3
    sp.run(["git", "-C", str(wt), "update-ref", "refs/heads/master", c1], check=True)
    assert gc.scan_push(wt, c1) == []                               # 干净基线：无新增对象不误报
    errs = gc.scan_push(wt, c3)
    assert any("s.md" in e and "凭据" in e for e in errs)            # 中间 blob 被拦（树级盲区）
    assert not any("abcdefg123" in e for e in errs)                 # 脱敏：不回显内容


def test_push_diff_bounded_by_oldrev_not_refs(tmp_path):
    """复审 B2：含密对象即使已被其他 ref（tag）引用，对本分支新引入仍必须扫到。"""
    import subprocess as sp
    wt = tmp_path / "wt2"
    sp.run(["git", "init", "-q", "-b", "master", str(wt)], check=True)

    def commit(msg):
        sp.run(["git", "-C", str(wt), "add", "-A"], check=True)
        sp.run(["git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", msg], check=True)

    def head():
        return sp.run(["git", "-C", str(wt), "rev-parse", "HEAD"],
                      capture_output=True, text=True, check=True).stdout.strip()

    (wt / "README.md").write_text("clean\n" * 20, encoding="utf-8")
    commit("c1")
    c1 = head()
    (wt / "x.md").write_text('password = "abcdefg123"\n', encoding="utf-8")
    commit("c2 secret")
    c2 = head()
    sp.run(["git", "-C", str(wt), "tag", "v9", c2], check=True)     # tag 先行引用（含密对象已可达）
    (wt / "tail.md").write_text("ok\n" * 20, encoding="utf-8")
    commit("c3")
    c3 = head()

    # 全局 --not --all 视角下 c2/c3 的对象都已被 tag 可达 → 按全局口径会漏（复现 B2）；
    # 按 oldrev=c1 差集口径必须抓到
    assert gc.scan_push(wt, c3, oldrev="0" * 40) == []               # 旧口径确实漏（回归对照）
    errs = gc.scan_push(wt, c3, oldrev=c1)
    assert any("x.md" in e and "凭据" in e for e in errs)            # 新口径抓到


def test_exemptions_narrowed_to_tests_py(tmp_path):
    """复审 B1：豁免只限 tests/*.py；标记在库内其他位置不再放行。"""
    r = _tree(tmp_path)
    (r / "docs").mkdir()
    # ① 库内普通文件加 test-fixture 标记 → 必须仍被拦
    (r / "docs" / "marked.md").write_text('password = "abcdefg123"  # test-fixture\n',
                                          encoding="utf-8")
    errs = gc.check(r)
    assert any("marked.md" in e and "凭据" in e for e in errs)
    # ② tests/ 下非 .py（如 json）含密 → 必须被拦
    (r / "tests").mkdir()
    (r / "tests" / "data.json").write_text('{"password": "abcdefg123456"}\n', encoding="utf-8")
    errs = gc.check(r)
    assert any("data.json" in e and "凭据" in e for e in errs)
    # ③ tests/*.py 合成夹具仍豁免（否则测试套件永远红）
    (r / "tests" / "test_fix.py").write_text('P = "password = \\"abcdefg123\\"  # test-fixture"\n',
                                             encoding="utf-8")
    errs = gc.check(r)
    assert not any("test_fix.py" in e for e in errs)


def test_content_based_scan_json_docx_gbk(tmp_path):
    """复审 B4：内容本位——json/docx 内嵌/GBK 文本都要扫，不看扩展名放行。"""
    r = _tree(tmp_path)
    (r / "cfg").mkdir()
    (r / "cfg" / "a.json").write_text('{"password": "abcdefg123456"}\n', encoding="utf-8")
    assert any("a.json" in e and "凭据" in e for e in gc.check(r))
    (r / "cfg" / "noext").write_text('password = "abcdefg123456"\n', encoding="utf-8")
    assert any("noext" in e and "凭据" in e for e in gc.check(r))
    # GBK 编码文本（utf-8 严格解码失败）也要扫到
    (r / "cfg" / "gbk.md").write_bytes('说明\r\npassword = "abcdefg123456"\r\n'.encode("gb18030"))
    assert any("gbk.md" in e and "凭据" in e for e in gc.check(r))
    # docx(zip) 内嵌 document.xml 含密 → 以 容器::内件 记报
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", '<w:t>password = "abcdefg123456"</w:t>')
    (r / "cfg" / "d.docx").write_bytes(buf.getvalue())
    errs = gc.check(r)
    assert any("d.docx" in e and "凭据" in e for e in errs)


def test_archive_worktree_attributes_ignores_export_ignore(tmp_path):
    """复审 B3：裸仓（225 场景）上 --worktree-attributes 让 archive 无视提交内 .gitattributes 的 export-ignore。"""
    import subprocess as sp
    wt = tmp_path / "wt3"
    sp.run(["git", "init", "-q", "-b", "master", str(wt)], check=True)
    (wt / "secret.md").write_text('password = "abcdefg123"\n', encoding="utf-8")
    (wt / ".gitattributes").write_text("secret.md export-ignore\n", encoding="utf-8")
    sp.run(["git", "-C", str(wt), "add", "-A"], check=True)
    sp.run(["git", "-C", str(wt), "-c", "user.email=t@t", "-c", "user.name=t",
            "commit", "-qm", "c1"], check=True)
    bare = tmp_path / "b.git"                                        # 服务端是裸仓（无 worktree 属性文件）
    sp.run(["git", "clone", "-q", "--bare", str(wt), str(bare)], check=True)
    import io
    import tarfile

    def names(*archive_args):
        b = sp.run(["git", "-C", str(bare), "archive", *archive_args, "HEAD"],
                   capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(b)) as t:
            return t.getnames()

    assert "secret.md" not in names()                          # 默认 archive 确实被 export-ignore 隐藏
    assert "secret.md" in names("--worktree-attributes")       # --worktree-attributes 后被检树=真实 tip
def test_gate_check_catches_card_code_argv_mismatch(tmp_path):
    """纯 Python 闸也拦截 B-0012 卡-码失实（卡声称 argv，非注释代码无 sys.argv）。"""
    r = _tree(tmp_path)
    (r / "scripts" / "fake_cli.py").write_text(
        "# ── 资产卡 ──\n"
        "# 名称：fake_cli.py\n"
        "# 重跑命令：python scripts/fake_cli.py <file>（argv CLI）\n"
        "# ──────────────\n"
        "# sys.argv 写在注释里不能自证\n"
        "print(1)\n",
        encoding="utf-8",
    )
    errs = gc.check(r)
    assert any("fake_cli.py" in e and "sys.argv" in e for e in errs)

    (r / "scripts" / "real_cli.py").write_text(
        "# ── 资产卡 ──\n"
        "# 名称：real_cli.py\n"
        "# 用法：python scripts/real_cli.py --help（argv CLI）\n"
        "# ──────────────\n"
        "import sys\n"
        "print(sys.argv[1:])\n",
        encoding="utf-8",
    )
    errs2 = gc.check(r)
    assert not any("real_cli.py" in e for e in errs2)


def test_zip_container_entry_and_size_limits(monkeypatch):
    """防爆三限：zip 条目数与解压总量超限必须 fail-closed 拦截。"""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(6):
            z.writestr(f"a{i}.txt", "ok")
    monkeypatch.setattr(gc, "ZIP_MAX_ENTRIES", 5)
    errs = gc._scan_bytes("many.zip", buf.getvalue())
    assert any("条目数" in e for e in errs)

    monkeypatch.setattr(gc, "ZIP_MAX_ENTRIES", 500)
    monkeypatch.setattr(gc, "ZIP_MAX_TOTAL_BYTES", 10)
    errs2 = gc._scan_bytes("big.zip", buf.getvalue())
    assert any("解压总量" in e for e in errs2)
