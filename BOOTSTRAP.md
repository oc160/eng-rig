# BOOTSTRAP — Agent Self-Installation Specification

> Send this file to your AI coding agent (Claude Code, Antigravity, Codex CLI, Cursor, Windsurf) to complete local setup idempotently.

## Execution Steps

### 0. Prerequisites

```bash
python --version        # Python 3.10+ required
git --version
```

### 1. Initialize EngineerRig (100% Python stdlib, zero external packages)

Run from the root of this repository:

```bash
python _meta/asset_cli.py init
python _meta/asset_cli.py find "clean markdown" --format=context
python _meta/arch_metrics.py --check
python _meta/gate_check.py --root .
python _meta/check_paths.py
```

`init` idempotently links `skills/*` into workspace and user skill directories (`.agents/skills`, `~/.claude/skills`, `~/.gemini/config/skills`), configures lifecycle hooks and `.git/hooks/{pre-commit,pre-push}`, installs the `asset` CLI wrapper, rebuilds `_meta/index/asset-index.json`, and runs a quick health check.

### 2. Allowed Write Scope During Bootstrap

- Repository directory, `~/.agents/skills`, `~/.claude/skills`, `~/.gemini/config/skills`, `~/.local/bin`, and client hook config files.
- Stop and report immediately on any permission error.

### 3. Single-Line Completion Report

```text
Bootstrap: OK / FAILED (step N) | commit: <git rev-parse --short HEAD> | indexed_assets: <count> | error: <none or message>
```
