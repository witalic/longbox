"""Upgrades for `title.json` documents written by older versions.

The vault is the source of truth and it outlives any single build, so a change
to the document's shape is a MIGRATION, not an edit: the reader upgrades what
it finds on the way in, and the next commit writes the current shape back. The
index has always had this (a schema mismatch drops and rebuilds the cache); the
documents did not, which meant the first feature to change their shape would
have had to migrate a user's library retroactively.

Each step takes the raw parsed JSON of version N and returns version N + 1.
Steps must be pure and additive — never drop a field a newer build might still
be reading, and never touch the user layer.
"""
from __future__ import annotations

from collections.abc import Callable

# the shape this build writes
CURRENT_SCHEMA = 3


def _v1_to_v2(doc: dict) -> dict:
    """Video chapters remember a playback position, page chapters do not — but
    both kinds live in one title, so the map belongs to the shared user layer."""
    user = dict(doc.get("user") or {})
    user.setdefault("position", {})
    return {**doc, "user": user}


def _v2_to_v3(doc: dict) -> dict:
    """The vault holds more than one kind of work now, and an episode has a
    studio the way a manga has an author."""
    meta = dict(doc.get("meta") or {})
    meta.setdefault("studio", [])
    return {**doc, "meta": meta}


_STEPS: dict[int, Callable[[dict], dict]] = {
    1: _v1_to_v2,
    2: _v2_to_v3,
}


def migrate(raw: dict) -> tuple[dict, bool]:
    """Bring a parsed document up to CURRENT_SCHEMA. Returns (doc, changed).

    An unknown FUTURE version is left exactly as it is: a newer build wrote it,
    and mangling it here would damage a library the user still opens there.
    """
    version = raw.get("schema")
    if not isinstance(version, int) or version < 1:
        version = 1  # pre-versioned documents are the original shape
    changed = False
    while version < CURRENT_SCHEMA:
        step = _STEPS.get(version)
        if step is None:  # a gap in the chain is a bug, not something to guess at
            break
        raw = step(raw)
        version += 1
        raw["schema"] = version
        changed = True
    return raw, changed
