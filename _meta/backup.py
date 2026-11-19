# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：backup.py —— 资产库灾备（git bundle + 会话库 + 记忆目录，带留存策略）
# 来源：2026-10-20 架构评估 P0（RK2：225 单点/无自动备份）
# 提取：2026-10-20
# Author: EngineerRig
# 验证方式：--dry-run 清单正确 + 本机三件产物生成 + 二次运行留存正确
# 重跑命令：python _meta/backup.py            # 默认：bundle + 记忆目录（不含 1.6G 会话库）
#          python _meta/backup.py --with-db  # 额外打包会话库（周例行）
# 依赖：标准库
# ──────────────────────────────────────────────────────────
"""本地灾备。默认目标 ${WORKSPACE}\\_backups\\asset-hub（仓外，不入 git）。

产物：
  repo-<ts>.bundle            整个仓库（含全部历史，git bundle 可 clone 恢复）
  memories-<ts>.zip           Claude Code 项目记忆目录（专家知识，不可再生）
  asset-inbox-<ts>.zip        _meta/inbox 收件箱（*.meta.json + 件本体；目录不存在则跳过）
  sessiondb-<ts>.sqlite.gz    会话库（--with-db；1.6G，用 sqlite backup API 一致性快照）

留存：每类保留最近 --keep 份（默认 5）。
安全：目标目录不得位于仓库内；会话库含原始对话，只留本机/225，永不入 git。
"""
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from datetime import datetime
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
DEFAULT_DEST = Path(os.environ.get("ASSET_HUB_BACKUP", str(Path.home() / ".asset-hub-backups")))
DB = Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"
MEM = Path.home() / ".zcode" / "cli" / "memories"
INBOX = LIB / "_meta" / "inbox"


def ts():
    return datetime.now().strftime("%Y%m%d-%H%M")


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def bundle_repo(dest, dry):
    out = dest / f"repo-{ts()}.bundle"
    if dry:
        return out, "dry-run"
    r = subprocess.run(["git", "bundle", "create", str(out), "--all"], cwd=LIB,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git bundle 失败：{r.stderr.strip()}")
    return out, human(out.stat().st_size)


def zip_memories(dest, dry):
    out = dest / f"memories-{ts()}.zip"
    if dry:
        return out, "dry-run"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in MEM.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(MEM))
                n += 1
    return out, f"{human(out.stat().st_size)} / {n} 文件"


def zip_inbox(dest, dry):
    out = dest / f"asset-inbox-{ts()}.zip"
    if dry:
        return out, "dry-run"
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in INBOX.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(INBOX))
                n += 1
    return out, f"{human(out.stat().st_size)} / {n} 文件"


def dump_db(dest, dry):
    out = dest / f"sessiondb-{ts()}.sqlite.gz"
    if dry:
        return out, "dry-run"
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        src = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp_path)
        with dst:
            src.backup(dst)          # 一致性快照，不怕并发写
        dst.close()
        src.close()
        with open(tmp_path, "rb") as f, gzip.open(out, "wb", compresslevel=6) as g:
            shutil.copyfileobj(f, g)
    finally:
        try:
            Path(tmp_path).unlink()
        except OSError:
            pass
    return out, human(out.stat().st_size)


def prune(dest, prefix, keep):
    olds = sorted(dest.glob(f"{prefix}-*"), key=lambda p: p.name, reverse=True)
    removed = []
    for p in olds[keep:]:
        p.unlink()
        removed.append(p.name)
    return removed


def verify_artifact(prefix: str, path: Path):
    """写后即验（verify-on-write）：bundle verify / zip testzip / 流式 gzip CRC。
    校验失败抛 RuntimeError，调用方据此删坏件并阻止 prune 误删好备份。"""
    if prefix == "repo":
        r = subprocess.run(["git", "bundle", "verify", str(path)], cwd=LIB,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError(f"git bundle verify 失败：{(r.stderr or r.stdout).strip()[:160]}")
    elif prefix in ("memories", "asset-inbox"):
        with zipfile.ZipFile(path, "r") as z:
            bad = z.testzip()
            if bad is not None:
                raise RuntimeError(f"zip 校验损坏条目：{bad}")
    elif prefix == "sessiondb":
        try:
            with gzip.open(path, "rb") as g:
                while g.read(1 << 20):
                    pass
        except (OSError, EOFError, zlib.error) as e:
            raise RuntimeError(f"gzip 校验失败：{e}")


def main():
    dry = "--dry-run" in sys.argv
    with_db = "--with-db" in sys.argv
    as_json = "--json" in sys.argv
    dest = DEFAULT_DEST
    keep = 5
    for a in sys.argv:
        if a.startswith("--dest="):
            dest = Path(a.split("=", 1)[1])
        elif a.startswith("--keep="):
            keep = int(a.split("=", 1)[1])

    if LIB in dest.resolve().parents or dest.resolve() == LIB:
        print("✗ 目标目录不得位于仓库内（会被 git 收走）", file=sys.stderr)
        return 2
    dest.mkdir(parents=True, exist_ok=True)

    jobs = [("repo", bundle_repo), ("memories", zip_memories)]
    if INBOX.is_dir():                     # 收件箱不存在则静默跳过
        jobs.append(("asset-inbox", zip_inbox))
    if with_db:
        jobs.append(("sessiondb", dump_db))

    rows, errs = [], 0
    for prefix, fn in jobs:
        p = None
        try:
            p, info = fn(dest, dry)
            if not dry:
                verify_artifact(prefix, p)
            removed = [] if dry else prune(dest, prefix, keep)
            rows.append({"artifact": prefix, "path": str(p), "size": info, "pruned": removed, "verified": not dry})
        except Exception as e:
            if p is not None and not dry:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            rows.append({"artifact": prefix, "error": str(e)})
            errs += 1

    if as_json:
        print(json.dumps({"dest": str(dest), "dry_run": dry, "results": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"备份目标：{dest}（dry-run={dry}，留存 {keep} 份/类）")
        for r in rows:
            if "error" in r:
                print(f"  ✗ {r['artifact']}: {r['error']}")
            else:
                pruned = f"；清理 {len(r['pruned'])} 份" if r["pruned"] else ""
                print(f"  ✓ {r['artifact']:<9} {r['size']:<18} {Path(r['path']).name}{pruned}")
        print("结论：" + ("全绿" if errs == 0 else f"{errs} 项失败"))
    return 0 if errs == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
