"""Upgrades for `title.json` documents written by older versions.

The vault is the source of truth and it outlives any single build, so a change
to the document's shape is a MIGRATION, not an edit: the reader upgrades what
it finds on the way in, and the next commit writes the current shape back. The
index has always had this (a schema mismatch drops and rebuilds the cache); the
documents did not, which meant the first feature to change their shape would
have had to migrate a user's library retroactively.

Each step takes the raw parsed JSON of version N and returns version N + 1.
Steps must be pure and never lose a value — a field that goes away is folded
into the one that takes its place — and never touch the user layer.
"""
from __future__ import annotations

from collections.abc import Callable

# the shape this build writes
CURRENT_SCHEMA = 6

# Fields since folded into another (the title step that folded each says why).
# The other documents that name fields — recipes, rules, import profiles, the
# operations journal — are read through these, never rewritten in bulk.
FOLDED_FIELDS = {"artists": "authors"}


def folded(field_id: str) -> str:
    return FOLDED_FIELDS.get(field_id, field_id)


def fold_values(values: dict) -> dict:
    """A {field id: value} map written before a fold, keyed as the fields are
    now: a folded field's list joins the list it was folded into, each value
    once, and fills it when that one is empty."""
    out = {k: v for k, v in values.items() if k not in FOLDED_FIELDS}
    for fid, v in values.items():
        if fid not in FOLDED_FIELDS:
            continue
        into = FOLDED_FIELDS[fid]
        have = out.get(into)
        if isinstance(have, list) and isinstance(v, list):
            out[into] = [*have, *(x for x in v if x not in have)]
        elif not have:
            out[into] = v
    return out


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


def _v3_to_v4(doc: dict) -> dict:
    """How the reader shows a title used to be remembered by the machine, keyed
    by the title's id; it belongs to the title, so it moves with the vault."""
    user = dict(doc.get("user") or {})
    user.setdefault("reader", {})
    return {**doc, "user": user}


def _v4_to_v5(doc: dict) -> dict:
    """An entry can belong to a part of its work — a volume, a season, a year."""
    chapters = [{**c, "section": c.get("section", "")} if isinstance(c, dict) else c
                for c in doc.get("chapters") or []]
    return {**doc, "chapters": chapters}


def _v5_to_v6(doc: dict) -> dict:
    """Authors and artists are one field — who made the work (owner,
    2026-11-11). The artists join the authors, each name once; artists the
    owner typed keep the joined list theirs, so capture never overwrites them."""
    meta = dict(doc.get("meta") or {})
    artists = meta.pop("artists", None)
    artists = artists if isinstance(artists, list) else []
    authors = meta.get("authors")
    names, seen = [], set()
    for name in [*(authors if isinstance(authors, list) else []), *artists]:
        if isinstance(name, str) and name.strip() and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    meta["authors"] = names
    provenance = dict(doc.get("provenance") or {})
    drew = provenance.pop("artists", None)
    wrote = provenance.get("authors")
    if artists and isinstance(drew, dict) and (
            not isinstance(wrote, dict)
            or (drew.get("origin") == "manual" and wrote.get("origin") != "manual")):
        provenance["authors"] = drew
    return {**doc, "meta": meta, "provenance": provenance}


_STEPS: dict[int, Callable[[dict], dict]] = {
    1: _v1_to_v2,
    2: _v2_to_v3,
    3: _v3_to_v4,
    4: _v4_to_v5,
    5: _v5_to_v6,
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
