# EngineerRig (`eng-rig`) — AI Engineering Toolkit & Guardrail Harness for Real-World Projects

> **English** | [中文说明](README_zh.md) · **License: [MIT](LICENSE)**

**100% Python Standard Library · Offline & Air-Gapped Ready (Windows / Linux) · Zero External DB · Zero Background Daemon · `<10ms` Tool Routing · Mandatory Deliverable Verification**

**EngineerRig** helps engineers in **civil, hydraulic, structural, architectural, mechanical, and surveying workflows** turn AI-written scripts, CAD/Office batch utilities, and engineering checklists into a verified local toolkit inside Git — so AI assistants (**Claude Code, Antigravity, Codex CLI, Cursor, Windsurf**) reuse your proven tools instead of rewriting buggy scripts from scratch, and never corrupt drawings, reports, or sensitive project paths. It also works cleanly alongside conversation memory tools like **Mem0** or **MemoraX**.

---

## Real Engineering Pain Points vs. How EngineerRig Solves Them

| Traditional Engineering Pain Point | How EngineerRig Solves It |
| :--- | :--- |
| **1. Proven CAD / calculation / report scripts get forgotten on the next project**<br>You spend hours getting AI to fix a DXF section script, hydraulic calculation, or Word formatting macro. Next project or new session, AI rewrites it from scratch with broken formulas or ruined layouts. | **Reuse proven tools in `<10ms` instead of rewriting**<br>Every tool carries an 8-line Asset Card (`Name`, `Source`, `Verification`, `Command`). Hooks automatically match your request and hand AI the exact verified script path (filtering out negated tools like `don't use X` and surfacing related alternatives). |
| **2. Zero tolerance for errors: untouched files modified & unverified deliverables**<br>Drawings and calculation reports go to formal review; you cannot rely on AI merely claiming "done", or have parallel windows overwrite each other's chapters and drawings. | **Write-scope locks, anti-forgetting reminders & exit-code `0` checks**<br>Tasks lock allowed directories (`scope_globs`) in `.tasks/<id>.md`; out-of-bounds edits are blocked by `PreToolUse` hooks; boundaries are re-injected after compaction or every 12 turns; completion requires `verify_cmd` to pass with exit code `0`, and pre-commit blocks scripts that fake CLI argument docs. |
| **3. Scripts scatter across folders, while project names & passwords block sharing**<br>One-off coordinate converters, table formatters, and spec-check tools get lost after deadlines, yet often contain client names, local paths, or internal credentials. | **Out-of-repo secret quarantine & version cleanup**<br>Hooks skip temporary debug files (`tmp`/`probe`), copy standalone scripts outside Git to scan UTF-8, GB18030, and `.docx`/`.xlsx`/`.zip` contents for secrets first, quarantine flagged files (`debts --aging`), merge iterative versions via `inbox-triage`, and never auto-commit. |
| **4. Restricted Windows & air-gapped institute networks forbid heavy stacks**<br>Engineering workstations run Windows (for local CAD/Office COM automation) inside restricted or offline networks where Docker, vector databases, and cloud upload plugins are banned. | **100% Python stdlib + offline Git + AST privacy verification**<br>Zero third-party runtime packages, zero databases, and zero background daemons. Run `python _meta/arch_metrics.py --check` anytime to inspect every core file's AST and verify zero third-party imports or hidden network calls. |

---

## 30-Second Quickstart

Send this line to your AI assistant inside the cloned folder:

```text
Run python _meta/asset_cli.py init to set up skill links and hooks, then test routing with: python _meta/asset_cli.py find "clean markdown" --format=context
```

Or run manually:

```bash
# 1. Initialize skill links, client & Git hooks, 'asset' CLI wrapper, and index
python _meta/asset_cli.py init

# 2. Test <10ms tool lookup and negation handling
python _meta/asset_cli.py find "clean markdown" --format=context
python _meta/asset_cli.py inject-candidate --prompt="don't use clean_markdown, format headings"

# 3. Open local single-file HTML dashboard & triage captured scripts
python _meta/asset_cli.py ui --no-open
python _meta/asset_cli.py inbox-triage

# 4. Create a scope-locked task with mandatory verification command
python _meta/asset_cli.py task new "Check channel sections" --scope="scripts/**" --verify="python -m pytest -q"

# 5. Run AST dependency/network audit, commit checks, link check, and tests
python _meta/arch_metrics.py --check
python _meta/gate_check.py --root .
python _meta/check_paths.py
python -m pytest -q
```

---

## Core Commands & Client Support

| Command | Purpose |
| :--- | :--- |
| `asset init` | Set up cross-client skill links, lifecycle hooks, Git checks, and index |
| `asset find "<task>"` | Find verified scripts, workflows, and related alternatives in `<10ms` |
| `asset task <new/claim/handoff/done/board/sync>` | Manage `.tasks/<id>.md` scope locks, TTL leases, `verify_cmd` checks, and `tasks` branch sync |
| `asset ui` | Generate a zero-server single-file HTML status dashboard (`dashboard.html`) |
| `asset close --retro="task\|reused\|deviation\|produced"` | Record structured summary and verify clean workspace, links, and remote sync |

| Tier | Capabilities | Clients |
| :--- | :--- | :--- |
| **L1 (Full Auto)** | Auto skill linking + `SessionStart` / `UserPromptSubmit` / `PreToolUse` / `PostToolUse` hooks + Git checks | Claude Code, Antigravity |
| **L2 (Semi-Auto)** | Auto skill linking + `asset` CLI + Git checks | Codex CLI, Cursor, Windsurf |
| **L3 (Universal)** | Direct `SKILL.md` / `INDEX.md` reading + CLI commands | Any terminal or IDE agent |

---

## Agent Docs & License

- **Agent Specifications**: Machine-readable runtime rules live in **[AGENTS.md](AGENTS.md)** and **[BOOTSTRAP.md](BOOTSTRAP.md)** (8-line Asset Card spec, hook signals, 7 pre-commit checks, and external memory coexistence boundaries).
- **License**: Released under the **[MIT License](LICENSE)** — free to use, modify, privately deploy, and redistribute in personal, academic, engineering-institute, and commercial workflows (provided the copyright notice is preserved; engineering outputs remain subject to professional review under applicable domain standards).
