"""The owner's own layer over the sources — IN THE VAULT (`<root>/sources.json`).

Not recipe material: a recipe is what longbox LEARNED about a site, while a
group, a bookmark or a freeze is how the owner relates to it. Both describe the
library's sites, so both travel with the vault.

    {"schema": 3,
     "groups":  ["Manga", "Art"],          the owner's order of group names
     "order":   ["<domain>", …],           the owner's order of sites (inside each group)
     "sources": {"<domain>": {"group": "…", "homepage": "https://…", "frozen": true,
                              "bookmarks": [{"name", "url", "added"}]}},
     "removed": ["<domain>"]}              taken out of the Sources list
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..library.vault import UnreadableDocument, Vault
from .models import domain_key

DOC = "sources.json"
# the shape this build writes; ANY change to it bumps this (Vault.doc_transaction).
# 2 is the shape 1 had — development builds stamped every document with one
# shared number, and the libraries they wrote are read as they are.
# 3: the order of sites, a homepage per site, and when each link was saved.
SCHEMA = 3


def _section(doc: dict, key: str, kind: type) -> dict | list:
    value = doc.setdefault(key, kind())
    if not isinstance(value, kind):
        raise UnreadableDocument(f"{DOC} holds {key} in a shape this build cannot read")
    return value


def _strings(value: object) -> list[str]:
    return [x for x in value if isinstance(x, str) and x.strip()] if isinstance(value, list) else []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stamped(bookmarks: list[dict], before: list) -> list[dict]:
    """A list as sent, each link carrying when it was saved: kept from the link
    already stored at that address — "" for one saved before that was kept,
    which stays "" rather than reading as new — and stamped now for one that
    is new. A client never has to know: the star in the browser sends a name
    and an address."""
    had = {b["url"]: b.get("added") or "" for b in before if isinstance(b, dict) and b.get("url")}
    return [{**b, "added": b.get("added") or (had[b["url"]] if b["url"] in had else _now())}
            for b in bookmarks]


@dataclass(frozen=True)
class Snapshot:
    prefs: dict[str, dict]   # per domain: group, homepage, bookmarks, frozen — only what was ever said
    groups: list[str]
    removed: set[str]
    order: list[str]         # sites in the owner's order; one never placed sorts after


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
                        {domain_key(d) for d in _strings(doc.get("removed"))},
                        [domain_key(d) for d in _strings(doc.get("order"))])

    def put(self, domain: str, *, group: str | None = None, bookmarks: list[dict] | None = None,
            frozen: bool | None = None, homepage: str | None = None) -> None:
        """Partial: only what is supplied changes."""
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            self._put(doc, domain, group=group, bookmarks=bookmarks, frozen=frozen, homepage=homepage)

    def put_many(self, domains: list[str], *, group: str | None = None, frozen: bool | None = None) -> None:
        """The same change to several sites, as one write."""
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            for domain in domains:
                self._put(doc, domain, group=group, frozen=frozen)

    @staticmethod
    def _put(doc: dict, domain: str, *, group: str | None = None, bookmarks: list[dict] | None = None,
             frozen: bool | None = None, homepage: str | None = None) -> None:
        key = domain_key(domain)
        if not key:
            return  # no site: nothing to say about it
        prefs = _section(doc, "sources", dict)
        mine = dict(prefs.get(key) or {})
        if group is not None:
            mine["group"] = group.strip()
        if bookmarks is not None:
            mine["bookmarks"] = _stamped(bookmarks, mine.get("bookmarks") or [])
        if frozen is not None:
            mine["frozen"] = frozen
        if homepage is not None:
            mine["homepage"] = homepage.strip()
        for default in ("frozen", "homepage", "group"):
            if not mine.get(default):
                mine.pop(default, None)  # the default is not worth storing
        # an entry that says nothing is not worth keeping
        if mine.get("group") or mine.get("bookmarks") or mine.get("frozen") or mine.get("homepage"):
            prefs[key] = mine
        else:
            prefs.pop(key, None)

    def set_order(self, domains: list[str], *, moved: str = "", group: str | None = None) -> None:
        """The sites in the owner's order — the browser's rail shows them so.
        A site dragged into another group changes group in the same write: as
        two, a failure between them left it in one place on disk and another
        on screen."""
        seen: list[str] = []
        for d in domains:
            key = domain_key(d)
            if key and key not in seen:
                seen.append(key)
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            doc["order"] = seen
            if moved and group is not None:
                self._put(doc, moved, group=group)

    def rename_group(self, old: str, new: str) -> None:
        """A group renamed, and every site in it — the removed ones too, which
        would otherwise bring the old name back when restored — in one write."""
        old, new = old.strip(), new.strip()
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            groups = _strings(_section(doc, "groups", list))
            if new in groups and new != old:
                raise ValueError(f'a group called "{new}" already exists')
            doc["groups"] = [new if g == old else g for g in groups]
            for mine in _section(doc, "sources", dict).values():
                if isinstance(mine, dict) and mine.get("group") == old:
                    mine["group"] = new

    def delete_group(self, name: str) -> None:
        """A group gone, its sites with no group — in one write."""
        name = name.strip()
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            doc["groups"] = [g for g in _strings(_section(doc, "groups", list)) if g != name]
            prefs = _section(doc, "sources", dict)
            for key in list(prefs):
                if isinstance(prefs[key], dict) and prefs[key].get("group") == name:
                    self._put(doc, key, group="")

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
        self.set_removed_many([domain], removed)

    def set_removed_many(self, domains: list[str], removed: bool) -> None:
        keys = {domain_key(d) for d in domains}
        known = self.snapshot().removed
        if (keys <= known) if removed else not (keys & known):
            return  # nothing to say — and nothing to write
        with self.vault.doc_transaction(DOC, schema=SCHEMA) as doc:
            have = {domain_key(d) for d in _strings(_section(doc, "removed", list))}
            have = have | keys if removed else have - keys
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
