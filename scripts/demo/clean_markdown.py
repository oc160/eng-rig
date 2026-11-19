# -*- coding: utf-8 -*-
# ── Asset Card ────────────────────────────────────────────
# Name: clean_markdown.py -- Normalize markdown headings and trailing whitespace
# Source: EngineerRig public seed example (stdlib demo)
# Extracted: 2026-11-16
# Author: EngineerRig
# Verification: python scripts/demo/clean_markdown.py --help
# Command: python scripts/demo/clean_markdown.py input.md [output.md] (sys.argv CLI)
# Dependencies: Python stdlib only
# Usage: Pass markdown file path via sys.argv to normalize headings and whitespace
# ─────────────────────────────────────────────────────────
"""Normalize ATX markdown headings (`#Title` -> `# Title`) and trailing spaces."""
import re
import sys
from pathlib import Path


def clean_text(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        ln = raw.rstrip()
        ln = re.sub(r"^(#{1,6})([^#\s])", r"\1 \2", ln)
        lines.append(ln)
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print("Usage: python clean_markdown.py <input.md> [output.md]")
        return 0 if args else 2
    src = Path(args[0])
    dst = Path(args[1]) if len(args) > 1 else src
    dst.write_text(clean_text(src.read_text(encoding="utf-8")), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
