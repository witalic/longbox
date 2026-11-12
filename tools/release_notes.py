#!/usr/bin/env python
"""What a release says on GitHub: its section of CHANGELOG.md.

    python tools/release_notes.py 3.4.0     print the notes for that version
    python tools/release_notes.py v3.4.0    a tag names it as well

It fails when the changelog has no section for the version, so the build
refuses a tag nobody wrote the changes down for.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ("Which file to download, and getting past the first-launch warning: "
           "[Install and get started](https://witalic.github.io/longbox/#start).")


def section(version: str, changelog: str) -> str | None:
    """The body of `## [version] …`, up to the next section or the link list."""
    found = re.search(
        rf"^## \[{re.escape(version.removeprefix('v'))}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]\n]+\]: |\Z)",
        changelog, re.S | re.M)
    body = found.group(1).strip() if found else ""
    return body or None


def notes(version: str) -> str | None:
    body = section(version, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
    return f"{body}\n\n---\n\n{INSTALL}\n" if body else None


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    text = notes(argv[0])
    if text is None:
        print(f"CHANGELOG.md has no section for {argv[0]}", file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
