"""Importing a folder the owner already has (design/folder-import.md).

A profile says what each level of a folder tree means — a collection, a
creator, a work — and how names become titles, entries and fields. `scan`
reads the tree once; `plan` is a pure function of that scan, a profile, this
run's own choices and the library as it stands, and is exactly what the
preview shows; `run` carries a fresh plan out, entry by entry, through the same
ingest a download takes.
"""
from __future__ import annotations

import os
import re
import stat
import threading
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import media
from . import operations as ops
from . import rules as R
from .models import DraftIn, FieldProvenance, TitleDoc
from .service import Library, chapter_num_key
from .vault import Vault, safe_id

DOC = "import-profiles.json"

# an image under one of these names, in a work's own folder, is its cover
COVER_NAMES = {"cover", "folder", "poster"}
# tokens the import fills itself — a name pattern may not define them
RESERVED = {"folder", "file", "n", "part", "date"}
# what a sidecar says about an entry an import stored — how a re-run knows it
IMPORTED_FROM = "folder"
_SHOWN_IGNORED = 200  # the plan lists this many ignored files and counts the rest


class InvalidProfile(ValueError):
    pass


# ---- the profile -----------------------------------------------------------------

class Level(BaseModel):
    role: Literal["collection", "creator", "work"] = "collection"
    # splits the folder name into tokens: `{site} @{handle} {creator}`
    pattern: str = ""


class Profile(BaseModel):
    id: str = ""
    name: str = ""
    # L0 (the folder picked) first. With the `level` anchor exactly one level is
    # the work, and nothing below it needs a role: below a work, a folder of
    # images is an entry and any other folder is a part.
    levels: list[Level] = [Level(role="work")]
    # `media`: the work is whichever folder holds the entries, at any depth —
    # for trees whose depth varies above the work
    anchor: Literal["level", "media"] = "level"
    workTitle: str = "{folder}"
    entryTitle: str = "{file}"
    dropTags: bool = False       # `[1080p] [Group]` out of entry names
    underscores: bool = False    # `_` read as a space in entry names
    # `none`: no number at all — the entry's name IS its label, the way an entry
    # typed by hand carries its whole name, and the work keeps the found order
    numbering: Literal["order", "name", "none"] = "order"
    orderBy: Literal["name", "date"] = "name"
    numPattern: str = ""         # `Ch. {n}` — required when numbering by name
    creatorTo: list[str] = ["authors"]
    # field id → value for every work; tokens are filled per work
    meta: dict[str, str | list[str]] = {}
    # fields this profile does not offer: their rows are hidden and the profile
    # leaves them alone — a value kept for one waits until it is shown again
    hidden: list[str] = []
    language: str = ""           # every entry's; tokens allowed
    group: str = ""
    entryDate: bool = True       # an entry's date is its file's date
    files: Literal["copy", "move"] = "copy"
    workExists: Literal["add", "separate", "skip"] = "add"
    entryExists: Literal["skip", "replace"] = "skip"
    cover: bool = True


class Overrides(BaseModel):
    """What the owner decided for this run only — never part of a profile."""
    off: list[str] = []                                 # work keys and entry paths left out
    titles: dict[str, str] = {}                         # work key → a title typed by hand
    meta: dict[str, dict[str, str | list[str]]] = {}    # work key → that work's own values


_TOKEN = re.compile(r"\{(\w+)\}")


def _literal(text: str) -> str:
    """Literal pattern text: any run of whitespace matches any run of it."""
    return r"\s+".join(re.escape(w) for w in re.split(r"\s+", text))


def name_pattern(pattern: str) -> re.Pattern | None:
    """`{site} @{handle} {creator}` as a regex over a whole folder name: the
    literal text must match, each token takes as little as it can, the last
    one takes the rest."""
    if not pattern.strip():
        return None
    names = _TOKEN.findall(pattern)
    if not names:
        raise InvalidProfile(f"{pattern!r} names no token")
    if len(set(names)) != len(names):
        raise InvalidProfile(f"{pattern!r} names a token twice")
    taken = sorted(n for n in names if n in RESERVED or re.fullmatch(r"L\d+", n))
    if taken:
        raise InvalidProfile(f"{{{taken[0]}}} is filled by the import itself")
    out, seen = "^", 0
    for bit in re.split(r"(\{\w+\})", pattern):
        m = _TOKEN.fullmatch(bit)
        if m:
            seen += 1
            out += f"(?P<{m[1]}>{'.+' if seen == len(names) else '.+?'})"
        elif bit:
            out += _literal(bit)
    try:
        return re.compile(out + "$", re.IGNORECASE)
    except re.error as e:
        raise InvalidProfile(f"{pattern!r} holds a token name a pattern cannot use") from e


def number_pattern(pattern: str) -> re.Pattern:
    """`Ch. {n}` as a search over an entry's name: `{n}` is the number."""
    if pattern.count("{n}") != 1:
        raise InvalidProfile("a number pattern holds {n} exactly once")
    before, after = pattern.split("{n}")
    return re.compile(_literal(before) + r"(\d+(?:[.,]\d+)?)" + _literal(after), re.IGNORECASE)


def _value_ok(fid: str, value: object) -> None:
    sh = ops.shape(fid)
    if sh not in ("list", "single", "flags") or fid == "title":
        raise InvalidProfile(f"{fid} is not a field an import can fill")
    if sh == "single" and not isinstance(value, str):
        raise InvalidProfile(f"{fid} holds one value")
    if sh in ("list", "flags") and not isinstance(value, list):
        raise InvalidProfile(f"{fid} holds a list")
    if sh == "flags" and set(value) - set(ops.FLAG_NAMES):
        raise InvalidProfile(f"flags are {', '.join(ops.FLAG_NAMES)}")


def check(p: Profile) -> Profile:
    """Refuse a profile that cannot mean anything — before a file is looked at."""
    roles = [lv.role for lv in p.levels]
    levels = p.levels
    if p.anchor == "level":
        if roles.count("work") != 1:
            raise InvalidProfile("exactly one level is the work")
        levels = p.levels[: roles.index("work") + 1]
    elif "work" in roles:
        raise InvalidProfile("the work is the folder holding the entries — no level is")
    for lv in levels:
        name_pattern(lv.pattern)
    if p.numbering == "name":
        number_pattern(p.numPattern)
    for fid in p.creatorTo:
        if ops.shape(fid) != "list":
            raise InvalidProfile(f"a creator goes into a list field, not {fid}")
    for fid, v in p.meta.items():
        _value_ok(fid, v)
    return p.model_copy(update={"id": p.id or uuid.uuid4().hex[:10], "levels": levels,
                                "name": p.name.strip()})


def check_overrides(o: Overrides) -> Overrides:
    for values in o.meta.values():
        for fid, v in values.items():
            _value_ok(fid, v)
    return o


# ---- profiles live in the vault ----------------------------------------------------

class ProfileStore:
    """Import profiles describe the shape of the owner's folders, not where they
    are — no paths — so they travel with the library like its rules do."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault

    def profiles(self) -> list[Profile]:
        return Vault.doc_items(self.vault.read_doc(DOC), DOC, "profiles", Profile, "a profile")

    def save(self, profiles: list[Profile]) -> list[Profile]:
        checked = [check(p) for p in profiles]
        with self.vault.doc_transaction(DOC) as doc:
            doc["profiles"] = [p.model_dump() for p in checked]
        return checked


# ---- the scan: the tree, read once -------------------------------------------------

Kind = Literal["video", "archive", "image", "other"]


def _kind(name: str) -> Kind:
    ext = Path(name).suffix.lower()
    if ext in media.VIDEO_EXTS:
        return "video"
    if ext in media.ARCHIVE_EXTS:
        return "archive"
    if ext in media.IMAGE_EXTS:
        return "image"
    return "other"


@dataclass
class FileItem:
    name: str
    rel: str            # posix path from the root
    size: int
    mtime: float
    kind: Kind
    readable: bool = True


@dataclass
class Node:
    name: str
    rel: str
    mtime: float
    dirs: list[Node] = field(default_factory=list)
    files: list[FileItem] = field(default_factory=list)

    @property
    def image_leaf(self) -> bool:
        """A folder of nothing but pages: below a work, one entry."""
        return (not self.dirs and any(f.kind == "image" for f in self.files)
                and not any(f.kind in ("video", "archive") for f in self.files))


@dataclass
class Scan:
    root: Path
    tree: Node
    skipped: list[str]  # what the walk could not or would not enter, and why


def _readable(path: Path, kind: Kind) -> bool:
    if kind == "video":
        return media.looks_like_video(path)
    if kind == "archive":
        return media.looks_like_archive(path)
    return True


def scan(root: Path, *, skip: Path | None = None) -> Scan:
    """The whole tree in one walk. Directory entries carry their stat data, so a
    network drive pays one listing per folder and one header read per video or
    archive — nothing else is opened until the run. `skip` is a folder never to
    descend into: the library itself, when it lives under the chosen one."""
    skipped: list[str] = []
    # kept as picked: a mapped drive must not turn into the share it maps
    root = Path(os.path.abspath(root))
    skip_at, skip_id = "", (0, 0)
    if skip is not None:
        try:
            rel = Path(os.path.relpath(skip.resolve(), root.resolve())).as_posix()
        except ValueError:  # another drive altogether
            rel = ".."
        skip_at = "" if rel.startswith("..") else os.path.normcase(rel)
        st = os.stat(skip)
        skip_id = (st.st_dev, st.st_ino)

    def is_library(path: str, sub: str) -> bool:
        if skip_at and os.path.normcase(sub) == skip_at:
            return True
        if not skip_id[1]:
            return False  # a filesystem with no inode numbers: the path check is all there is
        st = os.stat(path)  # directory entries carry no identity on Windows
        return (st.st_dev, st.st_ino) == skip_id

    def walk(path: Path, rel: str, mtime: float) -> Node:
        node = Node(path.name, rel, mtime)
        try:
            with os.scandir(path) as it:
                items = list(it)
        except OSError as e:
            skipped.append(f"{rel or path.name}: {e.strerror or e}")
            return node
        for de in sorted(items, key=lambda d: media.natural_key(d.name)):
            sub = f"{rel}/{de.name}" if rel else de.name
            try:
                st = de.stat(follow_symlinks=False)
                if de.is_dir(follow_symlinks=False):
                    if de.name.startswith(".") or de.name in ("$RECYCLE.BIN", "System Volume Information"):
                        continue
                    # A junction reads as a plain folder, and may lead anywhere —
                    # back up the tree, or into the library, whose media a move
                    # would then delete. Links are never followed.
                    if getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        skipped.append(f"{sub}: a link to another folder — not followed")
                        continue
                    if is_library(de.path, sub):
                        skipped.append(f"{sub}: the library itself")
                        continue
                    node.dirs.append(walk(Path(de.path), sub, st.st_mtime))
                elif de.is_file(follow_symlinks=False):
                    kind = _kind(de.name)
                    node.files.append(FileItem(de.name, sub, st.st_size, st.st_mtime, kind,
                                               _readable(Path(de.path), kind)))
            except OSError as e:
                skipped.append(f"{sub}: {e.strerror or e}")
        return node

    return Scan(root, walk(root, "", root.stat().st_mtime), skipped)


def describe(s: Scan, depth: int = 6) -> dict:
    """What the tree looks like, level by level — the names the structure
    editor shows beside each level, and how much of each kind there is."""
    levels: list[dict] = []
    frontier = [s.tree]
    for d in range(depth):
        if not frontier:
            break
        levels.append({"depth": d, "folders": len(frontier), "names": [n.name for n in frontier[:4]]})
        frontier = [c for n in frontier for c in n.dirs]
    counts: Counter = Counter()
    stack = [s.tree]
    while stack:
        n = stack.pop()
        counts["folders"] += 1
        counts.update(f.kind for f in n.files)
        stack.extend(n.dirs)
    return {"root": str(s.root), "levels": levels, "counts": dict(counts), "skipped": s.skipped}


# ---- the plan --------------------------------------------------------------------

EntryState = Literal["new", "exists", "replace", "conflict", "unreadable", "off", "skip"]


class EntryPlan(BaseModel):
    path: str                     # from the root — what `Overrides.off` names
    kind: Literal["video", "archive", "images"]
    files: int = 1
    num: str = ""
    title: str = ""
    section: str = ""
    date: str = ""
    lang: str = ""
    group: str = ""
    size: int = 0
    state: EntryState = "new"
    reason: str = ""


class WorkPlan(BaseModel):
    key: str                      # the work folder's path from the root ("." for the root)
    folder: str
    title: str
    titleId: str = ""             # the library title it adds to
    action: Literal["new", "add", "separate", "skip", "conflict", "off"] = "new"
    reason: str = ""
    meta: dict[str, str | list[str]] = {}
    ruled: list[str] = []         # fields the library's rules changed on the way in
    own: bool = False             # carries values of its own for this run
    tokens: dict[str, str] = {}   # what its folder names read as — the structure editor shows them
    cover: str = ""               # path from the root of the image that becomes its cover
    entries: list[EntryPlan] = []
    warnings: list[str] = []
    # for the run alone: the rule ids that shaped its metadata and its entries'
    # identity (counted once per write, as capture counts them), and whether its
    # title was typed by hand — a decision, never an automatic value
    fired: list[str] = Field(default_factory=list, exclude=True)
    identityFired: list[str] = Field(default_factory=list, exclude=True)
    typed: bool = Field(default=False, exclude=True)


class Ignored(BaseModel):
    path: str
    reason: str


class Plan(BaseModel):
    works: list[WorkPlan] = []
    ignored: list[Ignored] = []
    ignoredCount: int = 0
    warnings: list[str] = []
    entries: int = 0              # what Import would store
    bytes: int = 0


@dataclass
class _Entry:
    path: str
    rel: str                      # from the work's folder: what a re-run recognises it by
    kind: Literal["video", "archive", "images"]
    base: str                     # the name {file} stands for
    section: str
    size: int
    mtime: float
    readable: bool
    sources: list[FileItem]


@dataclass
class _Work:
    node: Node
    chain: list[Node]             # root → work
    cover: FileItem | None = None
    entries: list[_Entry] = field(default_factory=list)


def fill(template: str, tok: dict[str, str]) -> str:
    """A template with its tokens filled; a token nothing defines stays visible."""
    return " ".join(_TOKEN.sub(lambda m: tok.get(m[1], m[0]), template).split())


def _unique(values: list[str]) -> list[str]:
    return [v for v in dict.fromkeys(values) if v]


def _num_text(raw: str) -> str:
    v = float(raw.replace(",", "."))
    return str(int(v)) if v.is_integer() else f"{v:g}"


def _date(mtime: float) -> str:
    try:
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
    except (OSError, OverflowError, ValueError):  # before 1970 on Windows, or garbage
        return ""


def _clean(name: str, p: Profile) -> str:
    if p.dropTags:
        name = re.sub(r"\[[^\]]*\]", " ", name)
    if p.underscores:
        name = name.replace("_", " ")
    return " ".join(name.split()) or name


def _is_cover(f: FileItem) -> bool:
    return f.kind == "image" and Path(f.name).stem.casefold() in COVER_NAMES


def _works(s: Scan, p: Profile, ignored: list[Ignored]) -> list[_Work]:
    """Which folders are works — by level, or wherever the entries are."""
    out: list[_Work] = []
    w_at = next(i for i, lv in enumerate(p.levels) if lv.role == "work") if p.anchor == "level" else -1

    def holds_entries(n: Node) -> bool:
        return (any(f.kind in ("video", "archive") for f in n.files)
                or any(d.image_leaf for d in n.dirs) or n.image_leaf)

    def visit(n: Node, chain: list[Node]) -> None:
        if (len(chain) - 1 == w_at) if p.anchor == "level" else holds_entries(n):
            out.append(_Work(n, chain))
            return
        for f in n.files:
            ignored.append(Ignored(path=f.rel, reason="not media" if f.kind == "other" else "above the work"))
        for d in n.dirs:
            visit(d, [*chain, d])

    visit(s.tree, [s.tree])
    return out


def _collect(w: _Work, p: Profile, ignored: list[Ignored]) -> None:
    """A work's entries, in order: each folder's own entries by name or date,
    then its parts in name order."""
    def from_work(rel: str) -> str:
        return (rel[len(w.node.rel) + 1:] if w.node.rel else rel) or "."

    def folder(n: Node, section: list[str]) -> None:
        here: list[_Entry] = []
        loose: list[FileItem] = []
        part = " / ".join(section)
        for f in n.files:
            if n is w.node and p.cover and w.cover is None and _is_cover(f):
                w.cover = f
            elif f.kind in ("video", "archive"):
                here.append(_Entry(f.rel, from_work(f.rel), f.kind, Path(f.name).stem, part,
                                   f.size, f.mtime, f.readable, [f]))
            elif f.kind == "image":
                loose.append(f)
            else:
                ignored.append(Ignored(path=f.rel, reason="not media"))
        for d in n.dirs:
            if d.image_leaf:
                pages = [f for f in d.files if f.kind == "image"]
                ignored.extend(Ignored(path=f.rel, reason="not a page") for f in d.files if f.kind != "image")
                here.append(_Entry(d.rel, from_work(d.rel), "images", d.name, part,
                                   sum(f.size for f in pages), d.mtime, True, pages))
        if loose:
            if here or any(not d.image_leaf for d in n.dirs):
                # a thumbnail beside episodes is not an entry of its own
                ignored.extend(Ignored(path=f.rel, reason="an image beside other entries") for f in loose)
            else:
                here.append(_Entry(n.rel or ".", from_work(n.rel), "images", n.name, part,
                                   sum(f.size for f in loose), max(f.mtime for f in loose), True, loose))
        if p.orderBy == "date":
            here.sort(key=lambda e: (e.mtime, media.natural_key(e.base)))
        else:
            here.sort(key=lambda e: media.natural_key(e.base))
        w.entries.extend(here)
        for d in n.dirs:
            if not d.image_leaf:
                folder(d, [*section, d.name])

    folder(w.node, [])


def _tokens(w: _Work, p: Profile, warnings: list[str]) -> tuple[dict[str, str], list[str]]:
    """The work's tokens — every folder name on its path, what the level
    patterns read out of them — and its creators, outermost first."""
    tok: dict[str, str] = {}
    creators: list[str] = []
    for depth, n in enumerate(w.chain):
        tok[f"L{depth}"] = n.name
        if depth >= len(p.levels):
            continue
        lv = p.levels[depth]
        rx = name_pattern(lv.pattern)
        groups: dict[str, str] = {}
        if rx is not None:
            m = rx.match(n.name)
            if m:
                groups = {k: v.strip() for k, v in m.groupdict().items()}
                tok.update(groups)
            else:
                warnings.append(f"{n.name!r} does not read as {lv.pattern!r} — the whole name is used")
        if lv.role == "creator" and n is not w.node:
            creators.append(groups.get("creator") or n.name)
    tok["folder"] = w.node.name
    tok["creator"] = creators[-1] if creators else tok.get("creator", "")
    return tok, creators


def _filled(v: str, tok: dict[str, str], warnings: list[str]) -> str:
    """A value with its tokens filled — or nothing, when one of them is not
    read from this work's folders: `{site}` must never be stored as a tag."""
    out = fill(v, tok)
    left = _TOKEN.search(out)
    if left:
        warnings.append(f"{left[0]} is not read from this work's folders — {v!r} is left out")
        return ""
    return out


def _values(p: Profile, own: dict, tok: dict[str, str], creators: list[str],
            warnings: list[str]) -> dict[str, str | list[str]]:
    """Field → value for one work: every work's values, then its own on top — a
    list gains them, a single field is replaced — and its creators first."""
    def filled(v: str | list[str]) -> str | list[str]:
        if isinstance(v, list):
            return _unique([_filled(x, tok, warnings) for x in v])
        return _filled(v, tok, warnings)

    out: dict[str, str | list[str]] = {fid: filled(v) for fid, v in p.meta.items() if fid not in p.hidden}
    for fid, v in own.items():
        if fid in p.hidden:
            continue
        v = filled(v)
        if isinstance(v, list):
            out[fid] = _unique([*(out.get(fid) or []), *v])
        elif v:
            out[fid] = v
    for fid in p.creatorTo:
        out[fid] = _unique([*creators, *(out.get(fid) or [])])
    return {k: v for k, v in out.items() if v}


def _identity(rules: list, p: Profile, tok: dict[str, str], fired: list[str],
              warnings: list[str]) -> list[tuple[str, str]]:
    """Every entry's language and group: as the rules leave them — the naming a
    new chapter gets — and as typed, the spelling an entry stored before a rule
    existed is still known by (the same two `attach_chapter_media` matches)."""
    raw = (_filled(p.language, tok, warnings), _filled(p.group, tok, warnings))
    ruled = Library.ruled_identity(rules, *raw, "", fired)
    return list(dict.fromkeys((ruled, raw)))


def _writable(doc: TitleDoc, fid: str) -> bool:
    """An import fills a field only where it is empty or automatic — what the
    owner typed is never touched (state-model §5)."""
    return not ops.read(doc, fid) or not ops.typed_by_owner(doc, fid)


def plan(lib: Library, s: Scan, p: Profile, o: Overrides | None = None) -> Plan:
    return _plan(lib, s, check(p), o)[0]


def _plan(lib: Library, s: Scan, p: Profile, o: Overrides | None) -> tuple[Plan, dict[str, _Entry]]:
    o = check_overrides(o or Overrides())
    off = set(o.off)
    out = Plan()
    ignored: list[Ignored] = []
    rules = lib.incoming_rules()
    by_title = lib.index.ids_by_title()
    numrx = number_pattern(p.numPattern) if p.numbering == "name" else None

    found = _works(s, p, ignored)
    if not found:
        out.warnings.append("no folder sits where the work is — check the levels")
    plans: list[WorkPlan] = []
    entries: dict[str, _Entry] = {}
    for w in found:
        _collect(w, p, ignored)
        key = w.node.rel or "."
        warnings: list[str] = []
        tok, creators = _tokens(w, p, warnings)
        typed = (o.titles.get(key) or "").strip()
        title = typed or fill(p.workTitle, tok) or w.node.name
        wp = WorkPlan(key=key, folder=w.node.name, title=title, warnings=warnings, tokens=tok,
                      own=bool(o.meta.get(key)), cover=w.cover.rel if w.cover else "", typed=bool(typed))
        values = _values(p, o.meta.get(key, {}), tok, creators, wp.warnings)

        # the library title this work would land in
        existing = by_title.get(title.casefold(), [])
        doc: TitleDoc | None = None
        sidecars: dict[str, dict] = {}
        if existing and p.workExists != "separate":
            got = lib.index.get(existing[0])
            if got is not None:
                doc, sidecars, _ = got
                wp.titleId = existing[0]
            if len(existing) > 1:
                wp.warnings.append(f"{len(existing)} titles are named {title!r} — this adds to the first")
        if key in off:
            wp.action = "off"
        elif doc is not None:
            wp.action = "add" if p.workExists == "add" else "skip"
        elif existing:
            wp.action = "separate"

        # metadata, through the rules — for a work already there, against what it
        # holds in every field a value may arrive in, rules' targets included
        reach = [f for f in dict.fromkeys([*values, *(r.toField for r in rules if r.toField)])
                 if ops.shape(f) in ("list", "single", "flags")]
        ctx = {fid: ops.read(doc, fid) for fid in reach} if doc is not None else {}
        locked = [fid for fid in reach if not _writable(doc, fid)] if doc is not None else []
        ruled, hits = R.apply(rules, values, "", ctx, locked)
        wp.fired = [h.rule for h in hits]
        wp.ruled = sorted({h.field for h in hits})
        wp.meta = {k: v for k, v in ruled.items() if v and k not in locked}

        spellings = _identity(rules, p, tok, wp.identityFired, wp.warnings)
        wp.entries = _entries(w, p, numrx, doc, sidecars, spellings, tok, off, wp)
        entries.update((e.path, e) for e in w.entries)
        plans.append(wp)

    titles = Counter(wp.title.casefold() for wp in plans if wp.action != "off")
    for wp in plans:
        if wp.action != "off" and titles[wp.title.casefold()] > 1:
            wp.action, wp.reason = "conflict", "another folder gives the same title"
        if wp.action in ("skip", "conflict", "off"):
            for e in wp.entries:
                if e.state in ("new", "replace"):
                    e.state = "off" if wp.action == "off" else "skip"
    out.works = plans
    out.ignoredCount = len(ignored)
    out.ignored = ignored[:_SHOWN_IGNORED]
    go = [e for wp in plans for e in wp.entries if e.state in ("new", "replace")]
    out.entries, out.bytes = len(go), sum(e.size for e in go)
    return out, entries


def _entries(w: _Work, p: Profile, numrx: re.Pattern | None, doc: TitleDoc | None,
             sidecars: dict[str, dict], spellings: list[tuple[str, str]], tok: dict[str, str],
             off: set[str], wp: WorkPlan) -> list[EntryPlan]:
    """Numbers, names and states for one work's entries.

    A file an earlier run stored is that entry for good — its number, its
    spelling, whatever it is called now; a file that changed since is that entry
    changed, never a second one. What is new to the work continues after the
    highest number it holds, and an entry that will not be stored takes no
    number, so leaving one out never leaves a gap."""
    rows = {safe_id(c.id): c for c in doc.chapters} if doc is not None else {}
    before = {side["origPath"]: (side.get("origSize"), rows[cid])
              for cid, side in sidecars.items()
              if side.get("importedFrom") == IMPORTED_FROM and side.get("origPath") and cid in rows}
    lang, group = spellings[0]

    def ident(num: str, lg: str, gr: str) -> tuple[str, str, str]:
        return (num.strip().casefold(), lg.strip().casefold(), gr.strip().casefold())

    out: list[EntryPlan] = []
    pending: list[tuple[_Entry, EntryPlan]] = []
    claimed: set[tuple[str, str, str]] = set()  # entries this folder's own files already are
    for e in w.entries:
        ep = EntryPlan(path=e.path, kind=e.kind, files=len(e.sources), size=e.size,
                       section=e.section, lang=lang, group=group,
                       date=_date(e.mtime) if p.entryDate else "")
        out.append(ep)
        known = before.get(e.rel)
        if known is not None:
            row = known[1]
            claimed.add(ident(row.num, row.lang, row.group))
            ep.num, ep.title, ep.lang, ep.group = row.num, row.title, row.lang, row.group
            if known[0] == e.size:
                ep.state, ep.reason = "exists", "imported before"
            elif not e.readable:
                ep.state, ep.reason = "unreadable", "the file's own bytes are not what its name says"
            elif e.path in off:
                ep.state = "off"
            elif p.entryExists == "replace":
                ep.state, ep.reason = "replace", "changed since it was imported — its media is replaced"
            else:
                ep.state, ep.reason = "exists", "changed since it was imported — Replace media stores it"
        elif not e.readable:
            ep.state, ep.reason = "unreadable", "the file's own bytes are not what its name says"
        elif e.path in off:
            ep.state = "off"
        else:
            pending.append((e, ep))

    if p.numbering == "none":
        for e, ep in pending:
            ep.num = fill(p.entryTitle, {**tok, "file": _clean(e.base, p), "n": "",
                                         "part": e.section, "date": ep.date}) or e.base
    # numbers: from the name, or by place — after whatever the work already holds
    parsed: dict[int, str] = {}
    if numrx is not None:
        for i, (e, _) in enumerate(pending):
            m = numrx.search(_clean(e.base, p))
            if m:
                parsed[i] = _num_text(m.group(1))
        if len(parsed) < len(pending):
            wp.warnings.append(f"{len(pending) - len(parsed)} of {len(pending)} entries do not read "
                               f"as {p.numPattern!r} — numbered after the rest")
    whole = [k[1] for k in map(chapter_num_key, [*(r.num for r in rows.values()), *parsed.values()])
             if k[0] == 0]
    nxt = int(max(whole)) + 1 if whole else 1
    for i, (e, ep) in enumerate(pending if p.numbering != "none" else []):
        if i in parsed:
            ep.num = parsed[i]
        else:
            ep.num, nxt = str(nxt), nxt + 1
        ep.title = fill(p.entryTitle, {**tok, "file": _clean(e.base, p), "n": ep.num,
                                       "part": e.section, "date": ep.date})

    # identities: one entry per number in a work. One the work holds is found by
    # either spelling, and is replaced only when no other file of this folder
    # already is that entry — two files must never take turns at one entry.
    held = {ident(r.num, r.lang, r.group): r for r in rows.values()}
    seen = Counter(ident(ep.num, lang, group) for _, ep in pending)
    for _, ep in pending:
        row = next((held[k] for k in (ident(ep.num, lg, gr) for lg, gr in spellings) if k in held), None)
        if seen[ident(ep.num, lang, group)] > 1:
            ep.state, ep.reason = "conflict", f"another entry is also number {ep.num}"
        elif row is not None and ident(row.num, row.lang, row.group) in claimed:
            ep.state, ep.reason = "conflict", f"entry {ep.num} is already another file of this folder"
        elif row is not None:
            ep.lang, ep.group = row.lang, row.group  # the spelling it is stored under
            if p.entryExists == "replace":
                ep.state, ep.reason = "replace", f"replaces the media of entry {ep.num}"
            else:
                ep.state, ep.reason = "exists", f"the work already has entry {ep.num}"
    return out


# ---- the run ---------------------------------------------------------------------

class Failure(BaseModel):
    path: str
    reason: str


class WorkDone(BaseModel):
    titleId: str
    title: str
    created: bool
    added: int = 0


class Report(BaseModel):
    root: str
    profile: str = ""
    at: str = ""
    files: Literal["copy", "move"] = "copy"
    imported: int = 0
    replaced: int = 0
    skipped: int = 0
    failed: list[Failure] = []
    works: list[WorkDone] = []
    kept: int = 0                 # originals a move could not delete
    stopped: bool = False


Progress = Callable[..., None] | None


def _new_draft(values: dict, typed: set[str], manual_order: bool) -> DraftIn:
    doc = TitleDoc()
    for fid, v in values.items():
        ops.write(doc, fid, v)
    if manual_order:
        # labels with no number sort by their text; the found order is the one
        doc.meta.chapterOrder = "manual"
    return DraftIn(meta=doc.meta, provenance={
        fid: FieldProvenance(origin="manual" if fid in typed else "auto") for fid in values})


def _top_up(values: dict) -> Callable[[TitleDoc], bool]:
    """Merge a work's values into a title already there: a list gains values, a
    single field is filled — each only where the field is empty or automatic."""
    def change(doc: TitleDoc) -> bool:
        moved = False
        for fid, v in values.items():
            if not _writable(doc, fid):
                continue
            cur = ops.read(doc, fid)
            new = _unique([*cur, *v]) if isinstance(v, list) else v
            if new != cur:
                ops.write(doc, fid, new)
                doc.provenance[fid] = FieldProvenance(origin="auto")
                moved = True
        return moved
    return change


def _drop(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def run(lib: Library, s: Scan, p: Profile, o: Overrides | None = None, *,
        progress: Progress = None, stop: threading.Event | None = None) -> Report:
    """Carry out a plan made NOW — the library may have moved since the preview.
    Works go one at a time and entries one at a time, each a critical section of
    its own, so the library fills in while the run goes and Stop puts it down
    between two entries, never inside one. A move deletes an original only once
    its entry is stored; a file that fails is reported and the run goes on."""
    p = check(p)
    pl, entries = _plan(lib, s, p, o)
    move = p.files == "move"
    report = Report(root=str(s.root), profile=p.name, files=p.files,
                    at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    # every work the run touches: what is new with its entries, and a work the
    # library has with whatever it gains — metadata and a cover, entries or not
    work = [(wp, [ep for ep in wp.entries if ep.state in ("new", "replace")])
            for wp in pl.works if wp.action in ("new", "add", "separate")]
    work = [(wp, mine) for wp, mine in work if mine or wp.action == "add"]
    total = sum(len(mine) for _, mine in work)
    total_bytes = sum(ep.size for _, mine in work for ep in mine)
    report.skipped = sum(len(wp.entries) for wp in pl.works) - total
    done = done_bytes = 0
    emptied: set[Path] = set()
    fired: list[str] = []
    if progress:
        progress(0, total, "", 0, total_bytes)
    for wp, mine in work:
        if stop is not None and stop.is_set():
            report.stopped = True
            break
        try:
            if wp.titleId:
                tid = wp.titleId
                lib.amend(tid, _top_up(wp.meta))
            else:
                tid = lib.create(_new_draft({"title": wp.title, **wp.meta},
                                            {"title"} if wp.typed else set(), p.numbering == "none")).id
        except Exception as e:  # noqa: BLE001 — one work that cannot be written is one work
            report.failed.extend(Failure(path=ep.path, reason=f"the work could not be written: {e}")
                                 for ep in mine)
            done += len(mine)
            done_bytes += sum(ep.size for ep in mine)
            continue
        fired += wp.fired
        wd = WorkDone(titleId=tid, title=wp.title, created=not wp.titleId)
        report.works.append(wd)
        if wp.cover and (wd.created or not lib.vault.cover_paths(tid)):
            _cover(lib, tid, s.root / wp.cover, move, report, emptied)
        for ep in mine:
            if stop is not None and stop.is_set():
                report.stopped = True
                break
            if progress:
                progress(done, total, ep.path, done_bytes, total_bytes)
            _store(lib, s, tid, wp, ep, entries[ep.path], move, report, wd, emptied, fired)
            done += 1
            done_bytes += ep.size
            if progress:
                progress(done, total, ep.path, done_bytes, total_bytes)
        if report.stopped:
            break
    _sweep(s.root, emptied)
    if fired:
        lib.index.rule_fired(fired, report.at)
    return report


def _store(lib: Library, s: Scan, tid: str, wp: WorkPlan, ep: EntryPlan, e: _Entry, move: bool,
           report: Report, wd: WorkDone, emptied: set[Path], fired: list[str]) -> None:
    sources = [s.root / f.rel for f in e.sources]
    name = Path(e.path).name if e.path != "." else s.root.name
    # what the entry ARRIVED as — a folder's name must not read as a video
    filename = f"{name}.zip" if e.kind == "images" and _kind(name) == "video" else name
    sidecar = {"importedFrom": IMPORTED_FROM, "origPath": e.rel, "origSize": e.size,
               "downloadedAt": report.at}
    try:
        outcome, cid = lib.import_entry(tid, sources, num=ep.num, lang=ep.lang, group=ep.group,
                                        title=ep.title, section=ep.section, date=ep.date,
                                        sidecar=sidecar, filename=filename, link=move,
                                        replace=ep.state == "replace")
    except Exception as err:  # noqa: BLE001 — a file that fails is reported, and the run goes on
        report.failed.append(Failure(path=ep.path, reason=str(err) or type(err).__name__))
        return
    if outcome == "exists":
        report.skipped += 1
        return
    wd.added += 1
    if outcome == "replaced":
        report.replaced += 1
    else:
        report.imported += 1
        fired.extend(wp.identityFired)
    if not move:
        return
    kept = sum(not _drop(f) for f in sources)
    emptied.update(f.parent for f in sources)
    if kept:
        report.kept += kept
        try:
            lib.own_media(tid, cid)  # the original stays: the vault's copy must not be the same file
        except OSError as err:
            report.failed.append(Failure(path=ep.path, reason=f"stored, but still the original's file: {err}"))


def _cover(lib: Library, tid: str, path: Path, move: bool, report: Report, emptied: set[Path]) -> None:
    try:
        data = path.read_bytes()
    except OSError as e:
        report.failed.append(Failure(path=path.name, reason=f"cover: {e.strerror or e}"))
        return
    ext = media.sniff_ext(data)
    if not ext:
        report.failed.append(Failure(path=path.name, reason="cover: not an image a cover can be"))
        return
    try:
        lib.set_cover(tid, data, ext)
    except Exception as e:  # noqa: BLE001 — a cover that cannot be stored costs the cover only
        report.failed.append(Failure(path=path.name, reason=f"cover: {e}"))
        return
    if move:
        report.kept += not _drop(path)
        emptied.add(path.parent)


def _sweep(root: Path, emptied: set[Path]) -> None:
    """Folders a move emptied go too — and a parent left empty by that, up to
    but never including the root. A folder that still holds anything stays."""
    for d in sorted(emptied, key=lambda p: len(p.parts), reverse=True):
        while d != root and root in d.parents:
            try:
                d.rmdir()
            except OSError:
                break  # not empty — or not ours to remove
            d = d.parent
