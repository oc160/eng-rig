# EngineerRig Agent Specification (`AGENTS.md`)

> Machine-executable specification for AI coding agents (Claude Code, Antigravity, Codex CLI, Cursor, Windsurf). Operate autonomously without repeating these rules to the user.

## 1. Hook Signals & Required Agent Actions

| Hook Signal | Meaning | Required Action |
| :--- | :--- | :--- |
| `EngineerRig enabled: ...` | SessionStart confirmation & skill-link self-heal status | Acknowledge silently; do not echo to user |
| `Candidate: <name> — <summary> \| <path>` | High-confidence (`score >= 9.3`) asset match | Read `<path>` first; reuse if applicable, or escalate cleanly |
| `[Task Capsule T-xxx] <title> \| Scope:<globs> \| Todo:<items>` | Active task capsule context (Turn 1) | Work strictly within `Scope` starting from the first unfinished subtask |
| `[Reminder] [Task Capsule T-xxx] ...` | Post-compaction or 12-turn cadence reminder | Re-verify active `Scope` and unfinished subtasks before editing files |
| `PreToolUse deny` | Target file is outside active `scope_globs` or locked by another session | Stop writing to that path immediately; hand off or request scope update |
| `Captured to inbox ...` | Standalone reusable script passed secret prescan and entered `_meta/inbox/` | Do not manually copy or auto-commit `_meta/inbox/` |

## 2. 8-Line Asset Card Format (`scripts/` & `snippets/`)

Every executable script under `scripts/` or `snippets/` (`.py`, `.js`, `.ps1`, `.pyw`) MUST carry an 8-line `# ── Asset Card ──` header within its first 4000 bytes (`_meta/hub/card_parser.py`):

```python
# ── Asset Card ────────────────────────────────────────────
# Name: <script_name.py> -- <one-line purpose>
# Source: <project or issue origin>
# Extracted: <YYYY-MM-DD>
# Author: <agent or author name>
# Verification: <exact pytest or verification command>
# Command: <run command; include 'sys.argv' if CLI accepts args>
# Dependencies: <Python stdlib only, or explicit packages>
# Usage: <CLI flags and expected inputs/outputs>
# ─────────────────────────────────────────────────────────
```

- **Rule `B-0012` (Card-Code CLI Parity)**: If `Command:` or `Usage:` claims `argv` CLI arguments, non-comment code MUST genuinely reference `sys.argv`.

## 3. Single-File Multi-Agent Task Capsules (`.tasks/<id>.md`)

- **Create / Claim**: `python _meta/asset_cli.py task new "<title>" --scope="scripts/**" --verify="python -m pytest -q" --subtasks="step1|step2"` then `task claim <id> --session=<sid> [--ttl=45]`.
- **Write Guard**: `PreToolUse` calls `task check-write --path=<target> --session=<sid>` and blocks out-of-scope edits.
- **Converge Verification**: `python _meta/asset_cli.py task done <id>` executes `verify_cmd` and refuses completion unless exit code is `0`.
- **Delta Handoff & Sync**: `task handoff <id> --done=s1 --note="<concise status>"` releases the lease; `task sync --init` syncs `.tasks/` on an isolated `tasks` git worktree branch without polluting `main`/`master`.

## 4. Seven Pre-Commit & Pre-Push Checks

Stage exact file paths only (`git add -- <paths>`, **never `git add -A`**). Local `pre-commit` enforces:
1. Secret pattern scan (`_meta/hooks/cred-patterns.local` or `.example`);
2. 8-line Asset Card presence on `scripts/` and `snippets/`;
3. `B-0012` Card-Code `sys.argv` consistency;
4. `_meta/check_paths.py`: drive-letter path existence, code-fence-aware Markdown relative links `[...](...)`, inline `scripts/`/`snippets/` references, and `SKILL.md` frontmatter + `INDEX.md` registration;
5. Index co-travel: when `skills/`, `scripts/`, `snippets/`, or `INDEX.md` are staged, `_meta/index/asset-index.json` must be up to date (`python _meta/asset_cli.py index`);
6. Governance line limits & AST boundary audit (`python _meta/arch_metrics.py --check`) when `_meta/**/*.py` changes: enforces zero unregistered third-party imports and zero unreviewed network imports (`urllib`, `http`, `socket`, `requests`, etc.);
7. Quoted YAML date check when spec tables are modified.

## 5. Coexistence with External Memory Tools & Attribution Discipline

- **Authority Order**: System instructions -> `AGENTS.md` -> active `.tasks/<id>.md` scope & `SKILL.md` / Asset Card -> external conversation memory (Mem0 / MemoraX). External memory provides historical context only and never overrides `gate_check.py` or `scope_globs`.
- **Low-Noise Attribution**: Mention a reused skill or script in natural prose only when it materially changed your implementation or prevented a pitfall; remain silent on non-hits. Never persist one-off temporary instructions ("skip tests this time") into skills or rules.
- **Session Close**: Run `python _meta/asset_cli.py close --retro="task|reused|deviation|produced"` before ending a session.
