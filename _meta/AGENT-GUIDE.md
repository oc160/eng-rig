# EngineerRig Agent Operating Guide (`_meta/AGENT-GUIDE.md`)

> Autonomous operating contract for connected AI coding agents. Do not recite this file to the user.

## Hook Signals

- `EngineerRig enabled: ...` — Session initialized and skill links verified.
- `Candidate: X — summary | path` — Read `path` first; reuse if applicable.
- `[Task Capsule T-xxx] ...` / `[Reminder] [Task Capsule T-xxx] ...` — Respect `Scope` globs and resume from the first unfinished subtask.
- `PreToolUse deny` — Out-of-scope file write blocked; stop writing to that path.
- `Captured to inbox: ...` — New standalone script passed secret prescan and was staged in `_meta/inbox/`.

## Core Rules

1. **Search before writing**: Follow hook candidates, or run `python _meta/asset_cli.py find "<keywords>" --format=context`.
2. **Card every reusable script**: Place reusable scripts under `scripts/<family>/` with an 8-line `# ── Asset Card ──` header. Prefix throwaway debug scripts with `_`, `tmp`, or `probe`.
3. **Respect task leases**: Use `asset task new/claim/handoff/done` for multi-agent work. `task done` requires `verify_cmd` to exit `0`.
4. **Stage exact paths**: Always `git add -- <exact_paths>` (never `git add -A`). Run `python _meta/asset_cli.py index` when skills or scripts change.
5. **Low-noise attribution**: Mention a reused asset naturally only when it materially helped; never store one-off temporary instructions as permanent rules.
6. **Atomic close**: Finish with `python _meta/asset_cli.py close --retro="task|reused|deviation|produced"`.
