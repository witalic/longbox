"""The owner's own layer over the sources — IN THE VAULT (`<root>/sources.json`).

Not recipe material: a recipe is what longbox LEARNED about a site, while a
group, a bookmark or a freeze is how the owner relates to it. Both describe the
library's sites, so both travel with the vault.

    {"schema": 1,
     "groups":  ["Manga", "Art"],          the owner's order of group names
     "sources": {"<domain>": {"group": "…", "bookmarks": [{"name", "url"}], "frozen": true}},
     "removed": ["<domain>"]}              taken out of the Sources list
"""
from __future__ import annotations

from dataclasses import dataclass

from ..library.vault import UnreadableDocument, Vault
from .models import domain_key

DOC = "sources.json"
# the shape this build writes; ANY change to it bumps this (Vault.doc_transaction).
# 2 is the shape 1 had — development builds stamped every document with one
# shared number, and the libraries they wrote are read as they are.
SCHEMA = 2


def _section(doc: dict, key: str, kind: type) -> dict | list:
    value = doc.setdefault(key, kind())
    if not isinstance(value, kind):
        raise UnreadableDocument(f"{DOC} holds {key} in a shape this build cannot read")
    return value


def _strings(value: object) -> list[str]:
    return [x for x in value if isinstance(x, str) and x.strip()] if isinstance(value, list) else []


@dataclass(frozen=True)
class Snapshot:
    prefs: dict[str, dict]   # per domain: group, bookmarks, frozen — only what was ever said
    groups: list[str]
    removed: set[str]


class SourcePrefs:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault

    def snapshot(self) -> Snapshot:
        """The whole layer from ONE read, so a listing never mixes two versions."""
        doc = self.vault.read_doc(DOC)
        raw = doc.get("sources")
        prefs = ({domain_key(d): p for d, p in raw.items() if isinstance(p, dict)}
                 if isinstance(raw, dict) else {})
        return Snapshot(prefs, _strings(doc.get("groups")),
                        {domain_key(d) for d in _strings(doc.get("removed"))})

    def put(self, domain: str, *, group: str | None = None,
            bookmarks: list[dict] | None = None, frozen: bool | None = None) -> None:
        """Partial: only what is supplied changes."""
        key = domain_key(domain)
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            prefs = _section(doc, "sources", dict)
            mine = dict(prefs.get(key) or {})
            if group is not None:
                mine["group"] = group.strip()
            if bookmarks is not None:
                mine["bookmarks"] = bookmarks
            if frozen is not None:
                mine["frozen"] = frozen
            if not mine.get("frozen"):
                mine.pop("frozen", None)  # False is the default; storing it says nothing
            # an entry that says nothing is not worth keeping
            if mine.get("group") or mine.get("bookmarks") or mine.get("frozen"):
                prefs[key] = mine
            else:
                prefs.pop(key, None)

    def set_groups(self, groups: list[str]) -> list[str]:
        seen: list[str] = []
        for g in groups:
            name = g.strip()
            if name and name not in seen:
                seen.append(name)
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            doc["groups"] = seen
        return seen

    def set_removed(self, domain: str, removed: bool) -> None:
        key = domain_key(domain)
        if (key in self.snapshot().removed) == removed:
            return  # nothing to say — and nothing to write
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            have = {domain_key(d) for d in _strings(_section(doc, "removed", list))}
            if removed:
                have.add(key)
            else:
                have.discard(key)
            doc["removed"] = sorted(have)

    def adopt(self, prefs: dict[str, dict], groups: list[str], removed: list[str]) -> None:
        """Take another store's layer, filling only what this vault leaves empty:
        a group it already gave a site stays, bookmarks join by URL, a freeze from
        either side holds, and its group order stays first. Removals are taken only
        by a vault that never recorded any — one that did may have restored a site
        on purpose."""
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            mine = _section(doc, "sources", dict)
            for domain, theirs in prefs.items():
                if not isinstance(theirs, dict):
                    continue
                key = domain_key(domain)
                entry = dict(mine.get(key) or {})
                if not entry.get("group") and theirs.get("group"):
                    entry["group"] = theirs["group"]
                marks = list(entry.get("bookmarks") or [])
                have = {b.get("url") for b in marks if isinstance(b, dict)}
                marks += [b for b in theirs.get("bookmarks") or []
                          if isinstance(b, dict) and b.get("url") and b["url"] not in have]
                if marks:
                    entry["bookmarks"] = marks
                if entry.get("frozen") or theirs.get("frozen"):
                    entry["frozen"] = True
                if entry:
                    mine[key] = entry
            order = _strings(_section(doc, "groups", list))
            doc["groups"] = order + [g for g in _strings(groups) if g not in order]
            if "removed" not in doc:
                doc["removed"] = sorted({domain_key(d) for d in _strings(removed)})
