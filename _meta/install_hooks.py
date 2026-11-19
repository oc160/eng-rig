# -*- coding: utf-8 -*-
# ── 资产卡 ────────────────────────────────────────────────
# 名称：install_hooks.py —— 把零口令钩子幂等装入 Claude Code 用户配置
# 来源：2026-10-21 团队版（用户无感要求）
# 提取：2026-10-21
# Author: EngineerRig
# 验证方式：--dry-run 看合并结果；重复运行不产生重复条目；原配置自动备份
# 重跑命令：python _meta/install_hooks.py [--dry-run]
# 依赖：纯标准库
# ──────────────────────────────────────────────────────────
"""把 SessionStart / UserPromptSubmit / PostToolUse 三个钩子合并进
~/.zcode/cli/config.json（可用 ZCODE_CONFIG 覆盖路径）。

2026-11-16 追加（Antigravity L1 桥接）：同时幂等生成仓库根 .agents/hooks.json，
按事件名挂 agent_hooks.py（stdin 收 JSON），细节见 write_antigravity_hooks。

幂等：按 agent_hooks.py 路径识别已有条目，先删后加，不重复。
安全：写前备份为 config.json.bak-YYYYmmdd-HHMMSS；只改 hooks 键，其余原样。
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
HOOK = LIB / "_meta" / "hooks" / "agent_hooks.py"
CONFIG = Path(os.environ.get("ZCODE_CONFIG", str(Path.home() / ".zcode" / "cli" / "config.json")))
AG_HOOKS = Path(os.environ.get("AG_HOOKS", str(LIB / ".agents" / "hooks.json")))
PY = sys.executable

EVENTS = {
    "SessionStart": ["--event=start"],
    "UserPromptSubmit": ["--event=prompt"],
    "PreToolUse": ["--event=pre"],
    "PostToolUse": ["--event=post"],
}
MATCHER = {
    "PreToolUse": "Write|Edit|ApplyPatch",
    "PostToolUse": "Write|Edit|ApplyPatch",
}

# ── Antigravity L1 桥接（2026-11-16，对齐 builtin agy-customizations/docs/hooks.md）──
# 仓库根 .agents/hooks.json：顶层为命名 hook（"asset-hub"），SessionStart / PreInvocation
# 为平铺 handler 列表，PreToolUse / PostToolUse 为 matcher + hooks 分组列表；命令使用绝对路径，
# 因 Antigravity 执行 hooks.json 时 cwd 为 .agents/ 目录。


def _ag_arg(path_str: str, is_hook: bool = False) -> str:
    """Antigravity 在 Windows 下通过 cmd /c + Go 参数转义执行 command，路径含双引号会变成 \"，
    故仅在含空格时优先转 8.3 短路径（若仍含空格，HOOK 回退相对 .agents 的路径，PY 回退 python）。"""
    s = str(path_str)
    if os.name != "nt":
        import shlex
        return shlex.quote(s)
    if " " not in s:
        return s
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.GetShortPathNameW(s, buf, 1024) and buf.value and " " not in buf.value:
        return buf.value
    if is_hook:
        return "../_meta/hooks/agent_hooks.py"
    return "python"


def build_antigravity_hook():
    cmd_base = f"{_ag_arg(PY)} {_ag_arg(HOOK, is_hook=True)} "
    return {
        "SessionStart": [{"type": "command", "command": cmd_base + "--event=start", "timeout": 20}],
        "PreInvocation": [{"type": "command", "command": cmd_base + "--event=prompt", "timeout": 20}],
        "PreToolUse": [{"matcher": "write_file|edit_file|replace_file_content|multi_replace_file_content", "hooks": [
            {"type": "command", "command": cmd_base + "--event=pre", "timeout": 10},
        ]}],
        "PostToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": cmd_base + "--event=post", "timeout": 20},
        ]}],
    }


def write_antigravity_hooks(dry=False):
    doc = {}
    if AG_HOOKS.exists():
        try:
            old = json.loads(AG_HOOKS.read_text(encoding="utf-8"))
            if isinstance(old, dict):
                doc = old
        except ValueError:
            pass
    doc.pop("hooks", None)                      # 清理早期未对齐的顶层键
    doc["asset-hub"] = build_antigravity_hook()
    if dry:
        print("（dry-run）将写入 " + str(AG_HOOKS) + "：")
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return
    AG_HOOKS.parent.mkdir(parents=True, exist_ok=True)
    AG_HOOKS.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="")
    print("✓ Antigravity hooks 已写入 " + str(AG_HOOKS) + "（幂等更新）")


def build_event(event, args):
    d = {"hooks": [{"type": "process", "command": PY,
                    "args": [str(HOOK)] + args, "timeoutMs": 20000}]}
    if event in MATCHER:
        d = {"matcher": MATCHER[event], **d}
    return d


def main():
    dry = "--dry-run" in sys.argv
    cfg = {}
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError:
            print("✗ 现有配置不是合法 JSON，拒绝改写：" + str(CONFIG))
            return 2
    hooks = cfg.setdefault("hooks", {})
    hooks["enabled"] = True
    events = hooks.setdefault("events", {})
    for ev, args in EVENTS.items():
        kept = []
        for grp in events.get(ev, []):
            inner = grp.get("hooks", [])
            if any(str(HOOK) in (h.get("args") or []) for h in inner):
                continue                       # 先删旧条目（幂等）
            kept.append(grp)
        kept.append(build_event(ev, args))
        events[ev] = kept
    out = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    if dry:
        print(out)
        write_antigravity_hooks(dry=True)
        return 0
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        bak = CONFIG.with_name(CONFIG.name + ".bak-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        bak.write_text(CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        print("已备份：" + str(bak))
    CONFIG.write_text(out, encoding="utf-8", newline="")
    print("✓ 钩子已装入 " + str(CONFIG) + "（SessionStart/UserPromptSubmit/PostToolUse）")
    write_antigravity_hooks()
    return 0


if __name__ == "__main__":
    sys.exit(main())
