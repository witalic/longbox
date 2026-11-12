"""Value pages (design/value-pages.md): a page for every value of a list field
— a person, a studio, a character, a genre, a tag, a list of the owner's own.

What the owner writes about a value — a description, links, an image — lives in
the vault, one document per vocabulary under `.pages/`, so it travels with the
library. Everything else a page shows (its works, its numbers, the values it
travels with, the rules about it) is worked out from the index every time, so a
value nobody wrote about costs nothing and is never out of date.

A page is keyed by its vocabulary — the field the value is held in — and the
value case-folded: `Romance` and `romance` are one value to a rule, so they are
one page.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, field_validator

from . import cards, fields
from . import operations as ops
from . import rules as R
from .vault import UnreadableDocument, Vault, _atomic_write, safe_id

if TYPE_CHECKING:
    from .service import Library

log = logging.getLogger("longbox")

DIR = ".pages"  # a name no shelf can take: safe_id never yields a leading dot
# the shape a .pages/<vocabulary>.json is written in (Vault.doc_transaction).
# 2 is the shape 1 had — development builds stamped every document with one
# shared number, and the pages they wrote are read as they are.
SCHEMA = 2
MAX_DESC = 20_000
MAX_LINKS = 40
EXCERPT = 280


def key(value: str) -> str:
    return value.strip().casefold()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- which values have pages ---------------------------------------------------

def _qualifies(f: fields.Field) -> bool:
    # a list the owner fills: not flags (a fixed set), not language (it comes off
    # the chapters) — the same set Browse can group by, minus those two
    return f.type == "list" and f.control == "chips" and f.editable


def vocab_of(field_id: str) -> str | None:
    """The vocabulary a field's pages are kept in — the field's own; None for a
    field whose values have no pages."""
    f = fields.by_id().get(field_id)
    return f.id if f is not None and _qualifies(f) else None


_KIND = {"authors": "Person", "studio": "Studio", "characters": "Character",
         "genres": "Genre", "tags": "Tag"}


def kind_of(vocab: str) -> str:
    f = fields.by_id().get(vocab)
    return _KIND.get(vocab) or (f.label if f else vocab)


# ---- what is stored -----------------------------------------------------------

class Link(BaseModel):
    url: str
    label: str = ""

    @field_validator("url")
    @classmethod
    def _web(cls, v: str) -> str:
        v = v.strip()
        if urlsplit(v).scheme not in ("http", "https") or not urlsplit(v).hostname:
            raise ValueError("a link is a web address (http or https)")
        return v


class Page(BaseModel):
    value: str                 # the spelling it was written under
    desc: str = ""
    links: list[Link] = []
    image: str = ""            # a file name under .pages/images/<vocabulary>/ — unique per image
    updated: str = ""

    @property
    def key(self) -> str:
        return key(self.value)

    def empty(self) -> bool:
        return not (self.desc.strip() or self.links or self.image)


class PageStore:
    """The `.pages/<vocabulary>.json` documents. Read through a copy kept while
    the file's stamp holds, so a listing that asks for every page of a
    vocabulary costs one stat, not a parse."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self._seen: dict[str, tuple[tuple[int, int], dict[str, Page]]] = {}
        # bumped by every write this process makes: a read that a write overtook
        # is never kept — a stamp alone can repeat where a filesystem keeps
        # coarse times, and a stale copy would then outlive the write
        self._writes = 0

    @staticmethod
    def _name(vocab: str) -> str:
        return f"{DIR}/{safe_id(vocab)}.json"

    def image_dir(self, vocab: str) -> Path:
        return self.vault.root / DIR / "images" / safe_id(vocab)

    def pages(self, vocab: str) -> dict[str, Page]:
        name = self._name(vocab)
        writes = self._writes
        try:
            st = (self.vault.root / name).stat()
        except FileNotFoundError:
            return {}
        stamp = (st.st_mtime_ns, st.st_size)
        hit = self._seen.get(vocab)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        doc = self.vault.read_doc(name)
        got = {p.key: p for p in Vault.doc_items(doc, name, "pages", Page, "a page", schema=SCHEMA)}
        if writes == self._writes:
            self._seen[vocab] = (stamp, got)
        return got

    def get(self, vocab: str, value: str) -> Page | None:
        return self.pages(vocab).get(key(value))

    @contextmanager
    def edit(self, vocab: str) -> Iterator[dict[str, Page]]:
        """Load → change → save one vocabulary's pages as one step. A page left
        with nothing written is not stored."""
        self.vault.make_dir(self.vault.root / DIR)
        name = self._name(vocab)
        with self.vault.doc_transaction(name, schema=SCHEMA) as doc:
            pages = {p.key: p for p in Vault.doc_items(doc, name, "pages", Page, "a page", schema=SCHEMA)}
            yield pages
            doc["pages"] = [p.model_dump() for _k, p in sorted(pages.items()) if not p.empty()]
            self._writes += 1
        self._seen.pop(vocab, None)

    def write_image(self, vocab: str, value: str, data: bytes, ext: str) -> str:
        """A new image file — its own name, never reused, so the name is the
        version every cache keys it by."""
        d = self.image_dir(vocab)
        self.vault.make_dir(d)
        name = f"{safe_id(value)[:40]}-{uuid.uuid4().hex[:8]}.{ext.lstrip('.').lower()}"
        _atomic_write(d / name, data)
        return name

    def drop_image(self, vocab: str, name: str) -> None:
        if name:
            (self.image_dir(vocab) / Path(name).name).unlink(missing_ok=True)


# ---- what a page shows ----------------------------------------------------------

class Work(BaseModel):
    id: str
    title: str
    cover: str = ""
    rating: int = 0


class Neighbour(BaseModel):
    field: str                 # the field to open its page by
    value: str
    n: int


class Group(BaseModel):
    label: str
    items: list[Neighbour]


class SourceCount(BaseModel):
    source: str                # a domain; "" when it has none
    typed: bool = False        # typed by the owner rather than captured
    n: int


class RuleNote(BaseModel):
    id: str
    n: int                     # its place in the list, as the Rules tab numbers it
    kind: Literal["spelling", "renames", "removes", "moves", "implies", "impliedBy"]
    field: str
    values: list[str]
    target: str = ""
    targetField: str = ""
    domains: list[str] = []
    enabled: bool = True


class PageOut(BaseModel):
    field: str
    vocab: str
    kind: str
    value: str                 # the spelling most titles use
    key: str
    stored: bool
    desc: str = ""
    links: list[Link] = []
    image: str = ""            # its URL, "" without one
    titles: int = 0
    chapters: int = 0
    read: int = 0
    rated: int = 0
    rating: float = 0
    works: list[Work] = []
    groups: list[Group] = []
    sources: list[SourceCount] = []
    rules: list[RuleNote] = []
    rulesError: str = ""
    spellings: list[str] = []  # what the editor's "also written" holds: site-free rename rules
    held: dict[str, int] = {}  # a spelling → the titles that still hold it
    fav: bool | None = None    # people only (authors.json)
    personId: str = ""         # the id a person's favourite mark is kept by


class PeekOut(BaseModel):
    field: str
    kind: str
    value: str
    excerpt: str = ""
    titles: int = 0
    covers: list[str] = []


def image_url(field_id: str, value: str, image: str) -> str:
    if not image:
        return ""
    return f"/api/pages/{quote(field_id)}/image?value={quote(value)}&v={quote(image)}"


_GROUPS: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "authors": [("Worked with", ("authors", "studio")), ("Their tags", ("tags", "genres"))],
    "studio": [("People", ("authors",)), ("Their tags", ("tags", "genres"))],
    "characters": [("Alongside", ("characters",)), ("Tags", ("tags", "genres"))],
}
_NEIGHBOURS = 14


def _groups_for(vocab: str) -> list[tuple[str, tuple[str, ...]]]:
    return _GROUPS.get(vocab) or [("Often together", tuple(dict.fromkeys(
        [vocab, "tags", "genres"])))]


def excerpt_of(desc: str) -> str:
    text = " ".join(desc.split())
    return text if len(text) <= EXCERPT else text[:EXCERPT].rsplit(" ", 1)[0] + "…"


def view(lib: Library, field_id: str, value: str) -> PageOut | None:
    """Everything a page shows, in ONE pass over the library."""
    vocab = vocab_of(field_id)
    if vocab is None or not value.strip():
        return None
    k = key(value)
    try:
        rules = lib.rules.rules()
        rules_error = ""
    except (UnreadableDocument, OSError) as e:
        rules, rules_error = [], str(e)
    notes = _rule_notes(rules, vocab, k)
    spelled = {key(s) for n in notes if n.kind == "spelling" for s in n.values}

    # Who holds the value — and the spellings rules fold into it — off the
    # stored values; only those titles' documents are read for the rest.
    spellings: Counter = Counter()
    holding: dict[str, set[str]] = defaultdict(set)
    holders: set[str] = set()
    for tid, v in lib.index.values_of(vocab):
        kv = key(v)
        if kv == k:
            spellings[v] += 1
            holders.add(tid)
        elif kv in spelled:
            holding[kv].add(tid)  # a title is counted once, however often it holds a spelling
    held = Counter({kv: len(ids) for kv, ids in holding.items()})
    works: list[Work] = []
    sources: Counter = Counter()
    groups = _groups_for(vocab)
    near: list[Counter] = [Counter() for _ in groups]
    near_spelling: dict[tuple[str, str], Counter] = defaultdict(Counter)
    near_field: dict[tuple[str, str], str] = {}
    chapters = read = rated = rating_sum = 0

    for tid, doc, sidecars, covers in lib.index.query(ids=sorted(holders)) if holders else ():
        shown = cards.shown_covers(tid, doc, sidecars, covers)
        works.append(Work(id=tid, title=doc.meta.title, cover=shown[0] if shown else "",
                          rating=doc.user.rating))
        chapters += len(doc.chapters)
        states = [doc.user.read.get(c.id, "unread") for c in doc.chapters]
        read += bool(states) and all(s == "read" for s in states)
        if doc.user.rating:
            rated += 1
            rating_sum += doc.user.rating
        typed = ops.typed_by_owner(doc, vocab)
        sources[("" if typed else doc.meta.source.domain or "", typed)] += 1
        for gi, (_label, gfields) in enumerate(groups):
            seen: set[tuple[str, str]] = set()
            for f in gfields:
                fv = vocab_of(f)
                if fv is None:
                    continue
                vals = ops.read(doc, f)
                for v in vals if isinstance(vals, list) else []:
                    nk = (fv, key(v))
                    if nk == (vocab, k) or nk in seen:
                        continue
                    seen.add(nk)
                    near[gi][nk] += 1
                    near_spelling[nk][v] += 1
                    near_field.setdefault(nk, f)

    page = lib.pages.get(vocab, value)
    display = spellings.most_common(1)[0][0] if spellings else (page.value if page else value.strip())
    out = PageOut(field=field_id, vocab=vocab, kind=kind_of(vocab), value=display, key=k,
                  stored=page is not None, titles=len(works), chapters=chapters, read=read,
                  rated=rated, rating=round(rating_sum / rated, 1) if rated else 0, works=works,
                  sources=[SourceCount(source=s, typed=t, n=n) for (s, t), n in sources.most_common()],
                  rules=notes, rulesError=rules_error,
                  spellings=_spellings(notes, k), held={s: held[key(s)] for s in _all_spellings(notes, k)})
    out.groups = [Group(label=label, items=[
        Neighbour(field=near_field[nk], value=near_spelling[nk].most_common(1)[0][0], n=n)
        for nk, n in near[gi].most_common(_NEIGHBOURS)]) for gi, (label, _f) in enumerate(groups)]
    out.groups = [g for g in out.groups if g.items]
    if page is not None:
        out.desc, out.links = page.desc, page.links
        if page.image and (lib.pages.image_dir(vocab) / page.image).is_file():
            out.image = image_url(field_id, display, page.image)
    if vocab == "authors":
        out.personId = safe_id(display)
        out.fav = out.personId in lib.vault.author_favorites()
    return out


def peek(lib: Library, field_id: str, value: str) -> PeekOut | None:
    """The page in brief, asked for on every hovered chip: a count, the
    spelling, the first lines and a few covers — off the stored values and
    cards, no document read."""
    vocab = vocab_of(field_id)
    if vocab is None or not value.strip():
        return None
    k = key(value)
    spellings: Counter = Counter()
    holders: set[str] = set()
    for tid, v in lib.index.values_of(vocab):
        if key(v) == k:
            spellings[v] += 1
            holders.add(tid)
    covers = [c["cover"] for c in (json.loads(card) for card in lib.index.cards(ids=sorted(holders)))
              if c["cover"]][:5] if holders else []
    page = lib.pages.get(vocab, value)
    return PeekOut(field=field_id, kind=kind_of(vocab),
                   value=spellings.most_common(1)[0][0] if spellings else (page.value if page else value.strip()),
                   excerpt=excerpt_of(page.desc) if page else "", titles=len(holders), covers=covers)


def _rule_notes(rules: list[R.Rule], field: str, k: str) -> list[RuleNote]:
    out: list[RuleNote] = []
    for n, r in enumerate(rules, 1):
        base = dict(id=r.id, n=n, field=r.field, domains=r.domains, enabled=r.enabled)
        matched = k in {key(m) for m in r.match}
        if r.field == field and r.action == "rename" and key(r.to) == k:
            # a rule that only fixes the case (romance → Romance) shows what it fixes
            out.append(RuleNote(kind="spelling", values=[m for m in r.match if key(m) != k] or r.match, **base))
        elif r.field == field and matched:
            kind = {"rename": "renames", "remove": "removes", "move": "moves", "add": "implies"}[r.action]
            out.append(RuleNote(kind=kind, values=r.match, target=r.to, targetField=r.toField, **base))
        elif r.action in ("add", "move") and r.toField == field and key(r.to) == k:
            out.append(RuleNote(kind="impliedBy", values=r.match, target=r.to, targetField=r.toField, **base))
    return out


def _all_spellings(notes: list[RuleNote], k: str) -> list[str]:
    return list({key(s): s for n in notes if n.kind == "spelling" for s in n.values if key(s) != k}.values())


def _spellings(notes: list[RuleNote], k: str) -> list[str]:
    return list({key(s): s for n in notes if n.kind == "spelling" and n.enabled and not n.domains
                 for s in n.values if key(s) != k}.values())


# ---- what the owner writes ------------------------------------------------------

class PageIn(BaseModel):
    desc: str = ""
    links: list[Link] = []
    spellings: list[str] | None = None  # None leaves the rules alone


def _vocab_for(field_id: str, value: str) -> str:
    vocab = vocab_of(field_id)
    if vocab is None:
        raise ValueError(f"{field_id} values have no pages")
    if not value.strip():
        raise ValueError("a page belongs to a value — this one is empty")
    return vocab


def spelling_of(lib: Library, vocab: str, value: str) -> str:
    """How a value is written: as most titles hold it, else as its page was
    written, else as asked — the page may have been opened by any spelling."""
    k = key(value)
    seen: Counter = Counter(v for _tid, v in lib.index.values_of(vocab) if key(v) == k)
    if seen:
        return seen.most_common(1)[0][0]
    page = lib.pages.get(vocab, value)
    return page.value if page else value.strip()


def save(lib: Library, field_id: str, value: str, body: PageIn) -> None:
    vocab = _vocab_for(field_id, value)
    if len(body.desc) > MAX_DESC or len(body.links) > MAX_LINKS:
        raise ValueError("the description or the list of links is too long")
    # an unreadable document refuses BEFORE the rules change
    written = lib.pages.get(vocab, value)
    # the spelling titles use costs a pass over the library: asked only when a
    # page is new or the rules that land on it change
    spelled = (spelling_of(lib, vocab, value) if written is None or body.spellings is not None
               else written.value)
    if body.spellings is not None:
        set_spellings(lib, vocab, spelled, body.spellings)
    with lib.pages.edit(vocab) as pages:
        k = key(value)
        cur = pages.get(k) or Page(value=spelled)
        pages[k] = cur.model_copy(update={"desc": body.desc.strip(), "links": body.links,
                                          "updated": _now()})


def set_spellings(lib: Library, vocab: str, value: str, wanted: list[str]) -> None:
    """Other spellings ARE rename rules — site-free, enabled, landing on this
    value — kept in `rules.json` with every other rule, so capture, typing and
    the page can never disagree. A spelling taken off leaves its rule; a rule
    left matching nothing goes. What a rule matches of the value's own spelling
    (romance → Romance, a case merge) is never a spelling the editor shows, so
    it is never taken off either."""
    k = key(value)
    want = {key(s): s.strip() for s in wanted if s.strip() and key(s) != k}
    with lib.rules.edit() as rules:
        ours = [r for r in rules if r.field == vocab and r.action == "rename" and r.enabled
                and not r.domains and key(r.to) == k]
        have = {key(m) for r in ours for m in r.match}
        for r in ours:
            r.match = [m for m in r.match if key(m) in want or key(m) == k]
        gone = {id(r) for r in ours if not r.match}
        rules[:] = [r for r in rules if id(r) not in gone]
        missing = [s for kk, s in want.items() if kk not in have]
        keep = [r for r in ours if id(r) not in gone]
        if missing and keep:
            keep[0].match = [*keep[0].match, *missing]
        elif missing:
            rules.append(R.Rule(field=vocab, match=missing, action="rename", to=value))
    # the counts of a rule that went go with it
    lib.index.rule_stats_keep([r.id for r in rules if r.id])


def set_image(lib: Library, field_id: str, value: str, data: bytes, ext: str) -> None:
    vocab = _vocab_for(field_id, value)
    written = lib.pages.get(vocab, value)  # an unreadable document refuses before a file is written
    spelled = written.value if written is not None else spelling_of(lib, vocab, value)
    name = lib.pages.write_image(vocab, spelled, data, ext)
    old = ""
    try:
        with lib.pages.edit(vocab) as pages:
            k = key(value)
            cur = pages.get(k) or Page(value=spelled)
            old = cur.image
            pages[k] = cur.model_copy(update={"image": name, "updated": _now()})
    except BaseException:
        lib.pages.drop_image(vocab, name)
        raise
    lib.pages.drop_image(vocab, old)


def drop_image(lib: Library, field_id: str, value: str) -> None:
    vocab = vocab_of(field_id)
    if vocab is None:
        return
    old = ""
    with lib.pages.edit(vocab) as pages:
        cur = pages.get(key(value))
        if cur is not None:
            old = cur.image
            pages[cur.key] = cur.model_copy(update={"image": "", "updated": _now()})
    lib.pages.drop_image(vocab, old)


def held_keys(lib: Library, vocab: str) -> set[str]:
    return {key(v) for _tid, v in lib.index.values_of(vocab)}


class Stored(BaseModel):
    key: str
    value: str
    excerpt: str = ""
    image: str = ""
    held: bool = True


def stored(lib: Library, field_id: str) -> list[Stored]:
    """Every page written in a field's vocabulary — what a listing marks as
    described, and the pages whose value no title holds any more."""
    vocab = vocab_of(field_id)
    if vocab is None:
        return []
    pages = lib.pages.pages(vocab)
    if not pages:
        return []
    held = held_keys(lib, vocab)
    return [Stored(key=k, value=p.value, excerpt=excerpt_of(p.desc), held=k in held,
                   image=image_url(field_id, p.value, p.image)) for k, p in sorted(pages.items())]


def delete(lib: Library, field_id: str, value: str) -> bool:
    """Delete a page whose value no title holds — the only page that can go:
    one that still has works is the owner's writing about something here.
    False when there was no such page."""
    vocab = vocab_of(field_id)
    if vocab is None:
        return False
    k = key(value)
    if k in held_keys(lib, vocab):
        raise ValueError("titles still hold this value — its page stays")
    with lib.pages.edit(vocab) as pages:
        cur = pages.pop(k, None)
    if cur is not None:
        lib.pages.drop_image(vocab, cur.image)
    return cur is not None


# ---- a page follows its value (design/value-pages.md §5) ---------------------------

PAGE_NOTE = "page:"  # a journal row's field for a page a run moved; its title id is ""


def _merge(src: Page, dst: Page | None, to: str) -> Page:
    if dst is None:
        return src.model_copy(update={"value": to})
    desc = dst.desc.strip()
    if src.desc.strip():
        desc = (desc + "\n\n" if desc else "") + f"{src.value}: {src.desc.strip()}"
    urls = {link.url for link in dst.links}
    return dst.model_copy(update={
        "desc": desc, "links": [*dst.links, *(link for link in src.links if link.url not in urls)],
        "image": dst.image or src.image, "updated": _now()})


Note = tuple[str, str, object, object]


def follow(lib: Library, actions: list[ops.Action], journal: Callable[[Note], None]) -> None:
    """After a run: a page whose value no title holds any more follows a rename
    to the new spelling, merging into a page already written there — nothing
    the owner wrote is dropped. A value still held somewhere keeps its page.

    Each move is journaled the moment it is made, so a revert can put back
    whatever moved even when a later one could not. A pages document that
    cannot be read is logged and left as it is: the titles' run is done, and
    it never fails because of a page."""
    held: dict[str, set[str]] = {}
    for a in actions:
        vocab = vocab_of(a.field) if a.kind == "rename" else None
        if vocab is None:
            continue
        try:
            written = lib.pages.pages(vocab)
            olds = [o for o in a.values if key(o) in written and key(o) != key(a.to)]
            if olds and vocab not in held:
                held[vocab] = held_keys(lib, vocab)
            for old in olds:
                if key(old) in held[vocab]:
                    continue
                moved: Note | None = None
                with lib.pages.edit(vocab) as pages:
                    src = pages.get(key(old))
                    if src is not None:
                        dst = pages.get(key(a.to))
                        merged = _merge(src, dst, a.to)
                        del pages[src.key]
                        pages[merged.key] = merged
                        moved = ("", PAGE_NOTE + vocab,
                                 {"from": src.model_dump(), "to": dst.model_dump() if dst else None},
                                 {"to": merged.model_dump()})
                if moved is not None:
                    journal(moved)
        except (UnreadableDocument, OSError) as e:
            log.warning("pages: %s pages stay where they are: %s", vocab, e)


def unfollow(lib: Library, notes: list[tuple[str, object, object]]) -> tuple[int, int]:
    """Put back the pages a run moved — only where the page it made is still as
    the run left it, and nothing was written under the old spelling since.
    Returns how many were kept as they are now, and how many could not be
    reached (a pages document that cannot be read or written)."""
    kept = failed = 0
    for field, before, after in reversed(notes):
        vocab = field[len(PAGE_NOTE):]
        src = Page.model_validate(before["from"])
        made = Page.model_validate(after["to"])
        try:
            with lib.pages.edit(vocab) as pages:
                cur = pages.get(made.key)
                if cur is None or cur != made or src.key in pages:
                    kept += 1
                    continue
                pages[src.key] = src
                if before["to"] is not None:
                    pages[made.key] = Page.model_validate(before["to"])
                else:
                    del pages[made.key]
        except (UnreadableDocument, OSError) as e:
            failed += 1
            log.warning("pages: %s page %s not put back: %s", vocab, src.value, e)
    return kept, failed
