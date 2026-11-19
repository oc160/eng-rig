# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：test_backup.py —— backup.py 写后即验（verify-on-write）与坏件防 prune 回归测试
# 来源：2026-11-19 变异点自持化改造
# 提取：2026-11-19
# Author: EngineerRig
# 验证方式：pytest tests/test_backup.py -q 全绿
# 重跑命令：python -m pytest -q tests/test_backup.py
# 依赖：pytest、纯标准库 _meta/backup.py
# ──────────────────────────────────────────────────────────
"""backup.py verify-on-write 与损坏不淘汰旧备份用例。"""
import gzip
import importlib.util
import sqlite3
import zipfile
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("backup_mod", LIB / "_meta" / "backup.py")
bk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bk)


def test_verify_artifact_accepts_valid_zip_and_gzip(tmp_path):
    zp = tmp_path / "memories-20261119-1000.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("a.txt", "hello")
    bk.verify_artifact("memories", zp)

    gp = tmp_path / "sessiondb-20261119-1000.sqlite.gz"
    with gzip.open(gp, "wb") as g:
        g.write(b"SQLite format 3\x00" + b"x" * 2048)
    bk.verify_artifact("sessiondb", gp)


def test_verify_artifact_rejects_corrupt_zip_and_truncated_gzip(tmp_path):
    zp = tmp_path / "bad.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("a.txt", "payload_secure_bytes")
    raw = bytearray(zp.read_bytes())
    idx = bytes(raw).index(b"payload_secure_bytes")
    raw[idx] ^= 0xFF
    zp.write_bytes(bytes(raw))
    with pytest.raises((RuntimeError, zipfile.BadZipFile)):
        bk.verify_artifact("memories", zp)

    gp = tmp_path / "bad.sqlite.gz"
    with gzip.open(gp, "wb") as g:
        g.write(b"abc" * 1000)
    gp.write_bytes(gp.read_bytes()[:-4])  # 截断尾部 CRC/ISIZE
    with pytest.raises(RuntimeError, match="gzip"):
        bk.verify_artifact("sessiondb", gp)


def test_corrupt_write_does_not_prune_good_old_backups(tmp_path, monkeypatch):
    dest = tmp_path / "out"
    dest.mkdir()
    old_good = dest / "memories-20261101-0101.zip"
    with zipfile.ZipFile(old_good, "w") as z:
        z.writestr("ok.txt", "safe")

    mem = tmp_path / "mem"
    mem.mkdir()
    (mem / "m.md").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(bk, "MEM", mem)
    monkeypatch.setattr(bk, "INBOX", tmp_path / "no_inbox")

    def fake_bundle(d, dry):
        bad = d / "repo-20261119-1200.bundle"
        bad.write_bytes(b"not a real git bundle")
        return bad, "21B"

    def fake_verify(prefix, path):
        if prefix == "memories":
            raise RuntimeError("模拟 zip 校验失败")
        return None

    monkeypatch.setattr(bk, "bundle_repo", fake_bundle)
    monkeypatch.setattr(bk, "verify_artifact", fake_verify)
    monkeypatch.setattr(bk.sys, "argv", ["backup.py", f"--dest={dest}", "--keep=1"])

    rc = bk.main()
    assert rc == 1
    # 旧的好备份绝不能被坏新备份挤掉；损坏的 memories-* 新件已被清理
    assert old_good.exists()
    assert len(list(dest.glob("memories-*.zip"))) == 1


def _load_zcode_bak():
    p = LIB / "_meta" / "backup_zcode_state.py"
    if not p.is_file():
        pytest.skip("internal-only")
    sp = importlib.util.spec_from_file_location("zcode_bak_mod", p)
    mod = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(mod)
    return mod


def test_zcode_state_verify_accepts_valid_and_rejects_corrupt(tmp_path):
    import io
    import tarfile

    zb = _load_zcode_bak()
    gp = tmp_path / "db-20261119.sqlite.gz"
    with gzip.open(gp, "wb") as g:
        g.write(b"SQLite format 3\x00" + b"a" * 1024)
    zb.verify_backup_file(gp)

    tp = tmp_path / "memories-20261119.tar.gz"
    with tarfile.open(tp, "w:gz") as tf:
        data = b"memory note\n"
        ti = tarfile.TarInfo("memories/m.md")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    zb.verify_backup_file(tp)

    gp.write_bytes(gp.read_bytes()[:-4])
    with pytest.raises(RuntimeError):
        zb.verify_backup_file(gp)

    tp.write_bytes(tp.read_bytes()[:20])
    with pytest.raises(RuntimeError):
        zb.verify_backup_file(tp)


def test_zcode_state_skips_remote_prune_on_failure(tmp_path, monkeypatch):
    import types

    zb = _load_zcode_bak()
    db_path = tmp_path / "db.sqlite"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE t (x INT)")
    con.commit()
    con.close()
    mem_dir = tmp_path / "memories"
    mem_dir.mkdir()
    (mem_dir / "note.md").write_text("ok\n", encoding="utf-8")

    monkeypatch.setattr(zb, "DB", db_path)
    monkeypatch.setattr(zb, "MEM", mem_dir)
    monkeypatch.setattr(zb, "verify_backup_file", lambda p: None)

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "tar":
            out_tar = Path(cmd[2])
            out_tar.write_bytes(b"fake-tar")
            return types.SimpleNamespace(returncode=0, stderr="")
        if cmd[0] == zb.SCP:
            return types.SimpleNamespace(returncode=1, stderr="mock scp err")
        return types.SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(zb.subprocess, "run", fake_run)
    rc = zb.main()
    assert rc == 1
    assert not any("xargs -r rm" in " ".join(c) for c in calls)

