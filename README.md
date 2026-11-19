# EngineerRig (`eng-rig`) — Zero-Dependency Tool & Skill Harness for AI Coding Agents

> **English** | [中文说明](README_zh.md)

**100% Python Standard Library · Zero External DB · Zero Background Daemon · `<10ms` Deterministic Routing · AST Static Boundary Audit · Git-Native Multi-Agent Task Capsules**

**EngineerRig** keeps reusable engineering scripts, batch tools, operational skills (`SKILL.md`), and verification rules inside a local Git repository. Born in demanding engineering environments (air-gapped or restricted Windows/Linux workstations, CAD/Office/calculation verification scripts, strict format and accuracy requirements), it lets multiple AI coding agents (**Claude Code, Antigravity, Codex CLI, Cursor, Windsurf**) share one verified local toolkit — and works cleanly alongside conversation memory tools like **Mem0** or **MemoraX** (they remember past discussions; EngineerRig governs executable tools, skills, and commit guardrails).

---

## What It Solves

| Real-World Engineering Bottleneck | How EngineerRig Solves It |
| :--- | :--- |
| **1. Air-gapped / restricted workstations**<br>Cannot install Docker, vector databases, embedding services, or persistent background daemons. | **100% Python stdlib + AST static boundary audit**<br>Requires only Python 3.10+ and Git. `python _meta/arch_metrics.py --check` parses the AST of every governance module to guarantee zero third-party dependencies and zero unreviewed network calls. |
| **2. Rewriting the same scripts across sessions & clients**<br>Switching sessions or moving from Claude Code to Codex causes agents to rewrite scripts from scratch and hit old bugs. | **`<10ms` deterministic routing + negation filtering + 1-hop related hints**<br>Lifecycle hooks score `SKILL.md` frontmatter, `INDEX.md`, and 8-line script Asset Cards via field-weighted IDF, mask negated spans (`don't use X`), and inject a single `<=130-char` candidate pointer only on high-confidence hits. |
| **3. Secret leaks from auto-commit vs. unorganized script sprawl**<br>Engineering scripts often contain internal paths or parameters; auto-committing leaks secrets, while manual archiving lags behind. | **3-stage out-of-repo quarantine & inbox triage**<br>Hooks skip temporary debug scripts (`tmp`/`probe`/`test_`) and external git repos, stage new standalone scripts outside git (`../_inbox_staging`), scan UTF-8/GB18030/zip containers for secrets, and place clean scripts in `_meta/inbox/` without ever auto-committing. |
| **4. Multi-agent file collisions & premature task completion**<br>Parallel agents overwrite each other's files, conflict on shared task boards, forget scope after context compaction, or mark tasks done without running verification scripts. | **Single-file `.tasks/<id>.md` capsules + `PreToolUse` scope guard + `verify_cmd`**<br>Each task lives in an atomic `.tasks/<id>.md` file with TTL leases, pre-write `scope_globs` enforcement, post-compaction / 12-turn reminders, exit-code `0` verification, and an isolated `tasks` git worktree branch. |

---

## 30-Second Quickstart

Send this single line to your AI assistant inside the cloned repository:

```text
Run python _meta/asset_cli.py init to link skills and hooks, then test routing with: python _meta/asset_cli.py find "clean markdown" --format=context
```

Or run manually in your terminal:

```bash
# 1. Link skills, install client & Git hooks, create global 'asset' shim, build index
python _meta/asset_cli.py init

# 2. Test <10ms routing and negation filtering
python _meta/asset_cli.py find "clean markdown" --format=context
python _meta/asset_cli.py inject-candidate --prompt="don't use clean_markdown, format headings"

# 3. Generate single-file local HTML dashboard & inspect usage stats
python _meta/asset_cli.py ui --no-open
python _meta/asset_cli.py find-stats

# 4. Create a scope-guarded multi-agent task capsule
python _meta/asset_cli.py task new "Add section check script" --scope="scripts/**" --verify="python -m pytest -q"

# 5. Run AST boundary audit, 7-gate integrity checks, link checks, and unit tests
python _meta/arch_metrics.py --check
python _meta/gate_check.py --root .
python _meta/check_paths.py
python -m pytest -q
```

---

## Core Commands & Client Matrix

| Command | Action |
| :--- | :--- |
| `asset init` | Set up cross-client skill links, lifecycle hooks, Git `pre-commit`/`pre-push` hooks, and index |
| `asset find "<query>"` | Search skills, scenario rows, and carded scripts in `<10ms` (with 1-hop `related` hints) |
| `asset task <new/claim/handoff/done/board/sync>` | Manage `.tasks/<id>.md` capsules, TTL leases, scope guards, `verify_cmd`, and `tasks` branch sync |
| `asset ui` | Generate a zero-server single-file HTML status dashboard (`dashboard.html`) |
| `asset close --retro="task\|reused\|deviation\|produced"` | Record structured session summary, verify repo/links, and check remote sync |

| Tier | Capabilities | Clients |
| :--- | :--- | :--- |
| **L1 (Full Auto)** | Auto skill discovery + `SessionStart` / `UserPromptSubmit` / `PreToolUse` / `PostToolUse` hooks + Git hooks | Claude Code, Antigravity |
| **L2 (Semi-Auto)** | Auto skill linking + `asset` CLI routing + Git hooks (or manual `asset gates`) | Codex CLI, Cursor, Windsurf |
| **L3 (Universal)** | Direct `SKILL.md` / `INDEX.md` reading + manual `python _meta/asset_cli.py` commands | Any terminal or IDE coding agent |

---

## Detailed Agent Specifications

Human documentation is intentionally concise. Full machine-executable specifications for AI agents live in:
- **[AGENTS.md](AGENTS.md)** — Complete agent runtime rules (8-line Asset Card format, hook signals, 7 pre-commit checks, task capsule state machine, and coexistence rules with external memory layers).
- **[BOOTSTRAP.md](BOOTSTRAP.md)** — Idempotent agent self-installation procedure.
- **[LICENSE](LICENSE)** — MIT License.
