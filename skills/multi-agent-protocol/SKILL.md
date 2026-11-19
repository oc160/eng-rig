---
name: multi-agent-protocol
description: >-
  Coordinate multi-agent, cross-session, and cross-device tasks via Git-Native single-file task capsules (.tasks/<id>.md) with TTL leases, PreToolUse scope guards, and verify_cmd gates.
  Triggers: task capsule, multi-agent collaboration, task board, agent handoff, task dispatch, cross-agent sync.
  Do not use: real-time communication (use WebSocket), real-time whiteboard.
---

# Git-Native Multi-Agent Collaboration Protocol (`multi-agent-protocol`)

Workflow: `task new -> task claim -> PreToolUse scope guard -> task done (verify_cmd) / task handoff -> task sync`.

## Key Mechanisms (`_meta/hub/tasks.py`)

1. **One Task, One File (`.tasks/<task_id>.md`)**: Eliminates Git merge conflicts on shared board files; written atomically via temp file + `os.replace`.
2. **TTL Leases**: `task claim <id> --session=<sid> --ttl=45` prevents stale deadlocks if an agent disconnects.
3. **Hook Enforcement (`_meta/hooks/agent_hooks.py`)**:
   - `SessionStart` / `UserPromptSubmit`: Injects `<=150-char` capsule summary on Turn 1, stays silent on Turns 2..11, and emits `[Reminder]` after context compaction or every 12th turn.
   - `PreToolUse`: Calls `task check-write` and returns `deny` if a file edit falls outside `scope_globs` or hits another agent's active lease.
4. **Converge Verification (`verify_cmd`)**: `task done <id>` runs `verify_cmd` and blocks completion unless exit code is `0`.
5. **Isolated Git Worktree Sync**: `task sync --init` mounts `.tasks/` onto an orphan `tasks` branch so task state syncs across machines without polluting `main`/`master`.
