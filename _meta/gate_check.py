# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：gate_check.py —— 服务端闸（纯标准库，可在 225 Linux 上对任意树离线跑）
# 来源：2026-10-21 团队版架构（多用户第 1 项：服务端执法）
# 提取：2026-10-21
# Author: EngineerRig
# 验证方式：tests/test_gate_check.py（合成违规树 + 推送 blob 扫描 + 对抗绕过）+ 本仓全绿
# 重跑命令：python _meta/gate_check.py --root .　（推送模式：--git-dir <bare仓> --push <新rev> [--old <旧rev>]）
# 部署纪律（执法外置，2026-10-27 reviewer 评审修正）：服务端 pre-receive 只执行
#   225 仓外固定副本（$HOME/gate/asset-hub/gate_check.py，owner 经 _meta/hooks/deploy_gate.sh
#   显式部署）；推送内容自带的本文件不再作为执法程序——改扫描器≠改执法。
# 依赖：纯标准库（不依赖 pytest / 第三方包，服务端可裸跑）
# ──────────────────────────────────────────────────────────
"""服务端闸：本地 pre-commit 是便利，这里才是执法。

检查（与本地四闸同源，但不依赖 git 与第三方包）：
  1 技能 frontmatter（name=目录名、description≥30）
  2 规范版本表日期必须加引号（YAML date 对象会破坏 schema）
  3 scripts/snippets 可执行件必须有资产卡（前 4000 字节）
  4 危险路径/文件（invoices/payroll/private-vault、会话导出、本机凭据词表）
  5 通用凭据模式（口令/密钥/私钥块）——内容本位：json/toml/sh/无扩展名/
    GBK 文本/docx 等 zip 容器内嵌文本都扫，不按扩展名放行
  6 单文件 >10MB 拒绝（二进制/成果实物不入库）
  7 推送模式（--git-dir + --push [--old]）：扫本次推送涉及的提交差集内全部 blob
    （含中间提交）；按 oldrev 定界，不因对象已被其他 ref 可达而漏扫

豁免纪律（reviewer 复审 B1 后收窄）：tests/ 目录豁免仅限 .py 测试件；
test-fixture 行标记只在 tests/ 内生效——库内任意位置加标记不再放行。

凭据告警纪律：只记规则号 R1/R2/R3 + 路径 + 行号，绝不回显命中内容。

退出码：0 通过；1 有违规；2 参数错误。
"""
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".zcode", ".venv"}
EXEC_EXT = {".py", ".js", ".ps1", ".pyw"}
CARD_EXTS = EXEC_EXT
TEXT_EXTS = {".md", ".py", ".js", ".ps1", ".pyw", ".yaml", ".yml", ".txt", ".ini",
             ".json", ".toml", ".xml", ".csv", ".sh", ".cfg", ".conf", ".log"}
DATE_LINE = re.compile(r"^\s+(?:implemented|effective|issued):\s+\d{4}-\d{2}-\d{2}\s*$", re.M)
FORBIDDEN = re.compile(r"(invoices|payroll|private[-_]?vault|session[-_]\w*\.zip|\.ragflow_key|cred-patterns\.local)")
SECRETS = [
    # 键后可选引号：兼容 JSON/YAML 带引号键（"password": "..."，reviewer 复审 B4 实测缺口）
    re.compile(r"(password|passwd|pwd)[\"']?[ \t]*[:=][ \t]*[\"'][^\"'\s]{6,}[\"']", re.I),
    re.compile(r"(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)[\"']?[ \t]*[:=][ \t]*[\"'][A-Za-z0-9_-]{16,}[\"']", re.I),
    re.compile(r"-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
]
MAX_BYTES = 10 * 1024 * 1024
ZIP_MAX_ENTRIES = 500
ZIP_MAX_TOTAL_BYTES = 16 * 1024 * 1024
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
ZERO = "0" * 40


def tracked_files(root):
    """有 .git 时只检查已跟踪文件（工作树里的本机凭据词表不应误报）；否则全部文件。"""
    if (root / ".git").exists():
        try:
            r = subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=false",
                                "ls-files"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode == 0:
                return {p.replace("/", "\\") for p in r.stdout.splitlines() if p}
        except OSError:
            pass
    return None


def iter_files(root):
    for p in root.rglob("*"):
        if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            yield p


def _scan_text_lines(rel, text):
    """凭据行扫描（脱敏输出）：每文件至多一条——只含规则号+行号，不含命中内容。

    豁免（reviewer 复审 B1 收窄）：tests/ 下的 .py 测试件整体豁免（合成夹具）；
    test-fixture 行标记只在 tests/ 目录内生效。
    """
    in_tests = rel.parts and rel.parts[0] == "tests"
    if in_tests and rel.suffix == ".py":
        return []
    for lineno, line in enumerate(text.splitlines(), 1):
        if in_tests and "test-fixture" in line:
            continue
        rid = next((i for i, pat in enumerate(SECRETS, 1) if pat.search(line)), None)
        if rid:
            return [f"{rel}: 命中凭据模式 R{rid}（第 {lineno} 行，内容不回显）"]
    return []


def _scan_bytes(rel_str, data):
    """内容本位凭据扫描（复审 B4：不按扩展名放行）。

    UTF-8 严格解码成功即扫（json/toml/sh/无扩展名全覆盖）；
    失败且属中文文本类扩展时按 GB18030 严格解码再扫（GBK 历史文件）；
    zip 容器（docx/xlsx/docm/xlsm/zip）解包扫内部 xml/rels/txt/json；
    其余二进制跳过内容扫描（路径/尺寸闸在外层已生效）。
    """
    rel = Path(rel_str)
    try:
        return _scan_text_lines(rel, data.decode("utf-8"))
    except UnicodeDecodeError:
        pass
    if rel.suffix.lower() in TEXT_EXTS or rel.suffix == "":
        try:
            return _scan_text_lines(rel, data.decode("gb18030"))
        except UnicodeDecodeError:
            pass
    if rel.suffix.lower() in {".docx", ".docm", ".xlsx", ".xlsm", ".zip"}:
        import io
        import zipfile
        errs = []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                infos = z.infolist()
                if len(infos) > ZIP_MAX_ENTRIES:
                    return [f"{rel_str}: zip 条目数 {len(infos)} 超限（>{ZIP_MAX_ENTRIES}，fail-closed）"]
                total_uncomp = 0
                for info in infos:
                    if CTRL_RE.search(info.filename):
                        return [f"{rel_str}: zip 内含控制字符路径（fail-closed）"]
                    total_uncomp += max(0, info.file_size)
                    if total_uncomp > ZIP_MAX_TOTAL_BYTES:
                        return [f"{rel_str}: zip 解压总量超 {ZIP_MAX_TOTAL_BYTES // 1024 // 1024}MB 限额（fail-closed）"]
                    if info.filename.lower().endswith((".xml", ".rels", ".txt", ".json")):
                        try:
                            inner = z.read(info.filename).decode("utf-8", errors="replace")
                        except (OSError, UnicodeDecodeError, ValueError):
                            continue
                        errs.extend(_scan_text_lines(Path(f"{rel_str}::{info.filename}"), inner))
            return errs
        except Exception:
            return [f"{rel_str}: zip 容器无法解包（fail-closed 拒检）"]
    return []


def scan_push(git_dir, newrev, oldrev=ZERO):
    """推送模式：扫本次推送提交差集内的全部 blob（含中间提交）。

    差集按 oldrev 定界（复审 B2）：分支更新用 newrev --not oldrev——
    即使对象已被 tag/其他分支引用，只要对本分支是新引入的就扫；
    新建 ref（oldrev=ZERO）用 --not --all（此时所有对象都是新的）。
    逐 blob fail-closed：读不到/查不到一律报错（复审 B5）。
    """
    def git_text(*args, stdin=None):
        return subprocess.run(["git", "-C", str(git_dir), "-c", "core.quotepath=false",
                               *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", input=stdin)

    not_args = ["--not", oldrev] if oldrev != ZERO else ["--not", "--all"]
    r = git_text("rev-list", "--objects", newrev, *not_args)
    if r.returncode != 0:
        return [f"rev-list 失败：{(r.stderr or '').strip()[:60]}（内容不回显）"]
    name_of = {}
    for line in r.stdout.splitlines():
        if line.strip():
            sha, _, path = line.partition(" ")
            name_of[sha] = path
    if not name_of:
        return []
    b = git_text("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)",
                 stdin="\n".join(name_of) + "\n")
    if b.returncode != 0:
        return [f"cat-file 失败：{(b.stderr or '').strip()[:60]}（内容不回显）"]
    errs = []
    seen = set()
    for row in b.stdout.splitlines():
        parts = row.split()
        if len(parts) == 2 and parts[1] in ("missing", "dangling"):
            errs.append(f"{name_of.get(parts[0], parts[0])}: 对象缺失（fail-closed）")
            continue
        if len(parts) != 3 or parts[1] != "blob":
            continue
        sha = parts[0]
        try:
            size = int(parts[2])
        except ValueError:
            errs.append(f"{name_of.get(sha, sha)}: 尺寸不可解析（fail-closed）")
            continue
        if sha in seen:
            continue
        seen.add(sha)
        rel_str = name_of.get(sha) or "(匿名blob)"
        if size > MAX_BYTES:
            errs.append(f"{rel_str}: 新增 blob {size // 1024 // 1024}MB 超限")
            continue
        if FORBIDDEN.search(rel_str.replace("\\", "/")):
            errs.append(f"{rel_str}: 禁止入库的敏感路径/文件（新增 blob）")
            continue
        c = subprocess.run(["git", "-C", str(git_dir), "cat-file", "blob", sha],
                           capture_output=True)
        if c.returncode != 0 or not c.stdout and size > 0:
            errs.append(f"{rel_str}: 无法读取 blob（fail-closed）")
            continue
        errs.extend(_scan_bytes(rel_str, c.stdout))
    return errs



def _card_argv_claimed(head):
    """提取资产卡 用法/重跑命令 行是否声称 argv 化（优先复用 hub/card_parser，独立部署回退轻正则）。"""
    cp = Path(__file__).resolve().parent / "hub" / "card_parser.py"
    if cp.is_file():
        import importlib.util
        spec = importlib.util.spec_from_file_location("_gc_card_parser", cp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        card = mod.parse_asset_card(head)
        return "argv" in (card.get("重跑命令", "") + " " + card.get("用法", "")).lower()
    in_card = False
    on_cmd = False
    for ln in head.splitlines()[:40]:
        if not in_card:
            if "资产卡" in ln or "Asset Card" in ln:
                in_card = True
            continue
        if re.match(r"^#\s*(?:─{3,}|-{6,})", ln) or not ln.startswith("#"):
            break
        if re.match(r"^#\s*(?:用法|重跑命令|Usage|Command)\s*[：:]", ln, re.I):
            on_cmd = True
        elif re.match(r"^#\s*[^ \t]", ln):
            on_cmd = False
        if on_cmd and "argv" in ln.lower():
            return True
    return False


def check(root):
    errs = []
    tracked = tracked_files(root)
    # 1 skills
    for sk in (root / "skills").glob("*/SKILL.md"):
        d = sk.parent.name
        if d == "_template":
            continue
        t = sk.read_text(encoding="utf-8", errors="replace")
        m = re.match(r"^---\s*\n(.*?)\n---", t, re.S)
        if not m:
            errs.append(f"{sk.relative_to(root)}: frontmatter 缺失")
            continue
        fm = m.group(1)
        nm = re.search(r"^name:\s*(\S+)", fm, re.M)
        de = re.search(r"^description:\s*(.+?)(?=^\S|\Z)", fm, re.M | re.S)
        if not nm or nm.group(1) != d:
            errs.append(f"{sk.relative_to(root)}: name 与目录不一致")
        if not de or len(de.group(1).strip()) < 30:
            errs.append(f"{sk.relative_to(root)}: description 过短")

    for p in iter_files(root):
        rel = p.relative_to(root)
        if tracked is not None and str(rel).replace("/", "\\") not in tracked:
            continue
        # 4 forbidden
        if FORBIDDEN.search(str(rel)):
            errs.append(f"{rel}: 禁止入库的敏感路径/文件")
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        # 6 size
        if st.st_size > MAX_BYTES:
            errs.append(f"{rel}: 单文件 {st.st_size // 1024 // 1024}MB 超限（成果实物走 Seafile）")
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        # 5 secrets：内容本位（复审 B4——不按扩展名放行，zip 容器解包内扫）
        errs.extend(_scan_bytes(str(rel).replace("\\", "/"), data))
        if p.suffix.lower() not in {".md", ".py", ".js", ".ps1", ".pyw", ".yaml", ".yml",
                                    ".txt", ".ini", ".json"}:
            continue
        text = data.decode("utf-8", errors="replace")
        # 2 yaml dates
        if p.name == "规范版本表.yaml":
            hits = DATE_LINE.findall(text)
            if hits:
                errs.append(f"{rel}: {len(hits)} 处日期未加引号")
        # 3 asset card + B-0012 card-code sys.argv consistency
        if p.suffix.lower() in CARD_EXTS and rel.parts[0] in ("scripts", "snippets"):
            head = text[:4000]
            if "资产卡" not in head and "Asset Card" not in head:
                errs.append(f"{rel}: 缺资产卡头")
            elif _card_argv_claimed(head):
                non_cmt = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
                if "sys.argv" not in non_cmt:
                    errs.append(f"{rel}: 资产卡声称 argv 化但代码不含 sys.argv（B-0012 卡-码失实）")
    return errs


def main():
    root = None
    git_dir = None
    push_rev = None
    old_rev = ZERO
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--root" and i + 1 < len(args):
            root, i = Path(args[i + 1]), i + 2
        elif a == "--git-dir" and i + 1 < len(args):
            git_dir, i = Path(args[i + 1]), i + 2
        elif a == "--push" and i + 1 < len(args):
            push_rev, i = args[i + 1], i + 2
        elif a == "--old" and i + 1 < len(args):
            old_rev, i = args[i + 1], i + 2
        else:
            i += 1
    if push_rev:
        if not git_dir or not git_dir.is_dir():
            print("✗ 推送模式需要 --git-dir <bare 仓路径>")
            return 2
        errs = scan_push(git_dir, push_rev, old_rev)
        label = f"推送新增对象（{push_rev[:10]}…）"
    else:
        root = root or Path(".")
        if not root.is_dir():
            print(f"✗ 根目录不存在：{root}")
            return 2
        errs, label = check(root), str(root)
    if errs:
        print(f"✗ 服务端闸未通过（{len(errs)} 项）：")
        for e in errs[:40]:
            print("  -", e)
        if len(errs) > 40:
            print(f"  …另 {len(errs) - 40} 项")
        return 1
    print(f"✓ 服务端闸通过（{label}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
