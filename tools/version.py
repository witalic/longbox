#!/usr/bin/env python
"""The app's version has ONE source: app-meta.json.

    python tools/version.py               check that every copy agrees with it
    python tools/version.py 2.3.0         release: set it (and today's date), write every copy
    python tools/version.py --tag v2.3.0  check, and that the tag being released names it

The sidecar, the shell and the About page read app-meta.json directly. A copy
exists only where a tool insists on a literal of its own: electron-builder reads
shell/package.json (installer names, the release it publishes), npm mirrors that
into its lockfile, pip reads backend/pyproject.toml, and the project page names
the version it describes. Nothing else may carry a version.
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
META = "app-meta.json"


def _top(key: str) -> str:
    """A top-level key of a two-space-indented JSON document."""
    return rf'^(  "{key}": ")([^"]*)(")'


# file → patterns whose middle group is the version; each must match exactly once
COPIES: dict[str, tuple[str, ...]] = {
    "shell/package.json": (_top("version"),),
    "shell/package-lock.json": (
        _top("version"),
        r'("packages": \{\s*"": \{\s*"name": "[^"]*",\s*"version": ")([^"]*)(")',
    ),
    "backend/pyproject.toml": (r'^(version = ")([^"]*)(")',),
    "docs/index.html": (r'(<span class="ver mono">v)([^<]*)(</span>)',),
}
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")  # numeric only: the Windows version resource is an int tuple


def _read(rel: str) -> str:
    # bytes in, bytes out: a text-mode round trip would rewrite the line endings
    return (ROOT / rel).read_bytes().decode("utf-8")


def _field(text: str, pattern: str) -> re.Match[str] | None:
    found = list(re.finditer(pattern, text, re.MULTILINE))
    return found[0] if len(found) == 1 else None


def _moved(rel: str) -> str:
    return f"{rel}: the version field moved — update COPIES in tools/version.py"


def current() -> str:
    m = _field(_read(META), _top("version"))
    if m is None:
        sys.exit(_moved(META))
    return m.group(2)


def check(version: str) -> list[str]:
    problems = []
    for rel, patterns in COPIES.items():
        text = _read(rel)
        for pattern in patterns:
            m = _field(text, pattern)
            if m is None:
                problems.append(_moved(rel))
            elif m.group(2) != version:
                line = text.count("\n", 0, m.start(2)) + 1
                problems.append(f"{rel}:{line}: {m.group(2)} (expected {version})")
    return problems


def release(version: str) -> None:
    edits = {META: ((_top("version"), version), (_top("updated"), datetime.date.today().isoformat()))}
    edits.update({rel: tuple((p, version) for p in patterns) for rel, patterns in COPIES.items()})
    out = {}
    for rel, subs in edits.items():
        text = _read(rel)
        for pattern, value in subs:
            m = _field(text, pattern)
            if m is None:
                sys.exit(_moved(rel))
            text = text[:m.start(2)] + value + text[m.end(2):]
        out[rel] = text
    # nothing is written until every field has been found
    for rel, text in out.items():
        (ROOT / rel).write_bytes(text.encode("utf-8"))
        print(f"{rel}: {version}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version", nargs="?", help="the new version (X.Y.Z)")
    ap.add_argument("--tag", help="fail unless this tag is v<version>")
    args = ap.parse_args()

    if args.version:
        if not VERSION_RE.fullmatch(args.version):
            sys.exit(f"not a version: {args.version!r} (expected X.Y.Z)")
        release(args.version)

    version = current()
    problems = check(version)
    if args.tag is not None and args.tag != f"v{version}":
        problems.append(f"tag {args.tag} does not name the version {version}")
    if problems:
        sys.exit("\n".join(problems))
    print(f"version {version}: every copy agrees")


if __name__ == "__main__":
    main()
