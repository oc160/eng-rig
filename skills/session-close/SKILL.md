---
name: session-close
description: >-
  Run atomic session-close verification (_meta/session_close.py), record structured retro, verify skill links and git sync status until exit code 0.
  Triggers: close session, wrap up, finish session, pre-close check, session-close, session_close.
  Do not use: mid-task pause when work is still in progress.
---

# Atomic Session Close (`session-close`)

Workflow: `Run close with --retro -> Resolve any reported items -> Re-run until exit code 0`.

## Step 1: Record Retro & Run 6-Section Verification

```bash
python _meta/asset_cli.py close --retro="task|reused_asset|deviation|produced"
```

## Step 2: Resolve Reported Sections

| Section | Required Action |
| :--- | :--- |
| `1/6 Uncommitted changes` | Stage exact modified paths (`git add -- <paths>`, never `git add -A`), ensure new `scripts/` have an 8-line Asset Card, and commit |
| `2/6 Skill links` | Re-run `python _meta/asset_cli.py init` if any skill link is broken |
| `4/6 Retro commit` | `close --retro` auto-commits `_meta/retro/retro.jsonl`; push if ahead of remote |
| `5/6 Remote sync` | Push local commits or pull upstream changes |

## Step 3: Confirm Exit Code 0

Re-run `python _meta/asset_cli.py close` and confirm exit code `0`.
