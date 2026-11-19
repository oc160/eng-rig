---
name: task-intake
description: >-
  Align scope, acceptance criteria, existing asset reuse, and implementation path before starting a new engineering task.
  Triggers: new task, clarify requirements, task scope, acceptance criteria, before coding, task-intake.
  Do not use: continuing an in-progress task, trivial one-line edits, or session close.
---

# Task Intake — Four Pre-Work Questions (`task-intake`)

Workflow: `Restate Scope -> Define Verification -> Check Existing Assets -> Pick Escalation Path`.

## 1. What is the exact scope?
Restate the deliverable and explicit non-goals in one sentence.

## 2. What is the verification command?
Define the deterministic check (unit test, golden-file diff, or exit-code check) that proves completion.

## 3. Does an asset already exist?
Run `python _meta/asset_cli.py find "<task keywords>" --format=context` and check `INDEX.md §0` before writing new code.

## 4. Which path to take?
Follow the 4-level escalation order:
1. Reuse existing script with parameters;
2. Extend canonical script + add regression test;
3. Write new script with 8-line `# ── Asset Card ──` header + test;
4. One-off local script (prefix with `_` or `tmp` so it does not pollute `inbox/`).
