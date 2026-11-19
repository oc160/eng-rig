---
name: oss-absorb
description: >-
  Evaluate, security-audit, and distill external open-source GitHub repositories or paper mechanisms into EngineerRig.
  Triggers: absorb open source, evaluate github repo, extract oss skill, distill repo, oss-absorb.
  Do not use: tweaking internal scripts, running existing assets.
---

# External Open-Source Absorption (`oss-absorb`)

Workflow: `Shallow clone outside repo -> 3-Gate Security Audit -> Distill into stdlib asset + test -> Clean up sandbox`.

## Step 1: Out-of-Repo Shallow Clone

```bash
git clone --depth 1 <repo_url> ../_sandbox_oss/<repo_name>
```
Never copy an unreviewed repository directly into the working tree.

## Step 2: Three Security & Architecture Checks
1. **Network & Telemetry**: Reject hard-coded external telemetry or mandatory cloud dependencies.
2. **Dependency Weight**: Extract pure algorithms or workflow patterns using Python standard library only.
3. **License & Overlap**: Check license compatibility (avoid copyleft contamination) and run `python _meta/asset_cli.py find "<keywords>"` to avoid duplicating existing assets.

## Step 3: Distill & Verify
- **Workflow / Rule**: Add `<=10` lines to an existing skill or create `skills/<name>/SKILL.md` (`<=65` lines) and register in `INDEX.md`.
- **Executable Script**: Add under `scripts/<family>/` with an 8-line `# ── Asset Card ──` header and a regression test under `tests/`.
- Run `python _meta/asset_cli.py index`, `python _meta/check_paths.py`, and `python -m pytest -q`, then remove `../_sandbox_oss/<repo_name>`.
