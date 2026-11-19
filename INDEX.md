# EngineerRig Index

> Portable scenario routing index (`§0`). Keep entries concise so agents locate tools in one pass.

## 0. Scenario Quick Lookup (I want X → Use Y)

| I want to… | Use this | Location |
|---|---|---|
| Align scope, acceptance, and reuse before starting a non-trivial task | **task-intake skill** | `skills/task-intake/SKILL.md` |
| Coordinate multi-agent tasks with TTL leases, scope guards, and delta handoffs | **multi-agent-protocol skill** | `skills/multi-agent-protocol/SKILL.md` (`_meta/hub/tasks.py`) |
| Close a session, record retro, and verify git/links atomically | **session-close skill** | `skills/session-close/SKILL.md` (`_meta/session_close.py`) |
| Evaluate and distill an external GitHub repository or paper safely | **oss-absorb meta-skill** | `skills/oss-absorb/SKILL.md` |
| Normalize markdown headings and trailing whitespace via stdlib CLI | **clean_markdown demo script** | `scripts/demo/clean_markdown.py` |
