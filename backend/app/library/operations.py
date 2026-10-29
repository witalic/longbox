"""Operations over many titles at once (design/metadata-management.md).

ONE function decides what an operation does to a document — `apply` — and both
halves of an operation run it: the PREVIEW over the documents the index already
holds (throwaway copies, no file opened), and the RUN over each document freshly
read under its title's lock. So what the owner is shown and what is written can
never disagree, and a title edited between the two is changed as it is now.

The run is built for thousands of titles: only titles that change are touched,
each with one read and one write, the index refreshed in batches without
reading any chapter directory, the files written by a small pool of workers.
A title that cannot be changed is counted and left as it was; it never stops
the others, and nothing written goes unjournaled.
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from threading import Event
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from . import fields
from .models import FieldProvenance, TitleDoc
from .vault import safe_id, type_dir_name

if TYPE_CHECKING:
    from .service import Library

log = logging.getLogger("longbox")

Progress = Callable[[int, int], None] | None

# ---- what a field holds, in the shape operations work on --------------------
#
# list    → list[str]                     genres, tags, people, your list fields
# single  → str                           title, type, status, year, your text fields
# flags   → list[str] of the flags set    adult / ai / censored
# chapter → {chapter id: str}             a chapter row's language, group or section

Shape = Literal["list", "single", "flags", "chapter"]
CHAPTER_FIELDS = {"language": "lang", "group": "group", "section": "section"}
FLAG_NAMES = ("adult", "ai", "censored")
_LIST = {"authors", "artists", "characters", "studio", "genres", "tags"}
_SINGLE = {"title", "alt", "type", "status", "year", "desc"}
FOLDED = {"type", "status"}  # open vocabularies stored lowercase (models.TitleMeta)
PEOPLE = {"authors", "artists"}  # whose favourite marks are kept by name (authors.json)


def shape(field_id: str) -> Shape | None:
    if field_id in CHAPTER_FIELDS:
        return "chapter"
    if field_id == "flags":
        return "flags"
    if field_id in _LIST:
        return "list"
    if field_id in _SINGLE:
        return "single"
    f = fields.by_id().get(field_id)
    if f is None or f.builtin:
        return None  # cover, source and the rest of the spine are not values
    return "list" if f.type == "list" else "single"


def read(doc: TitleDoc, fid: str):
    sh = shape(fid)
    if sh == "chapter":
        return {c.id: getattr(c, CHAPTER_FIELDS[fid]) for c in doc.chapters}
    if sh == "flags":
        return [n for n in FLAG_NAMES if getattr(doc.meta.flags, n)]
    if fid in _LIST:
        return list(getattr(doc.meta, fid))
    if fid in _SINGLE:
        return getattr(doc.meta, fid)
    v = doc.meta.custom.get(fid)
    if sh == "list":
        return list(v) if isinstance(v, list) else ([v] if v else [])
    return ", ".join(v) if isinstance(v, list) else (str(v) if v else "")


def write(doc: TitleDoc, fid: str, value) -> None:
    sh = shape(fid)
    if sh == "chapter":
        attr = CHAPTER_FIELDS[fid]
        for c in doc.chapters:
            if c.id in value:
                setattr(c, attr, value[c.id])
    elif sh == "flags":
        for n in FLAG_NAMES:
            setattr(doc.meta.flags, n, n in value)
    elif fid in _LIST:
        setattr(doc.meta, fid, list(value))
    elif fid in _SINGLE:
        setattr(doc.meta, fid, value.strip().lower() if fid in FOLDED else value)
    elif value:
        doc.meta.custom[fid] = list(value) if sh == "list" else value
    else:
        doc.meta.custom.pop(fid, None)


def values_of(doc: TitleDoc, fid: str) -> set[str]:
    v = read(doc, fid)
    if isinstance(v, dict):
        return {x for x in v.values() if x}
    if isinstance(v, list):
        return {x for x in v if x}
    return {v} if v else set()


def typed_by_owner(doc: TitleDoc, fid: str) -> bool:
    """Whether a field's value is the owner's own. A value with no recorded
    origin is treated like one — automatic capture treats it so (state-model
    §5) — and a chapter's values carry no provenance at all."""
    if shape(fid) == "chapter":
        return False
    p = doc.provenance.get(fid)
    return p is None or p.origin == "manual"


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v and v not in out:
            out.append(v)
    return out


def _fits(fid: str, value: str) -> bool:
    """Whether a value is one this field may hold: a number field a number, a
    date field a date, and a title is never empty."""
    if fid == "title":
        return bool(value.strip())
    f = fields.by_id().get(fid)
    if f is None or f.builtin or not value:
        return True
    if f.type == "number":
        try:
            float(value.replace(",", "."))
        except ValueError:
            return False
    if f.type == "date":
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            return False
    return True


# ---- actions -----------------------------------------------------------------

Kind = Literal["rename", "remove", "move", "add", "set", "clear", "replace"]


class Action(BaseModel):
    """One thing to do to one field. `values` are the values acted on (rename,
    remove, move) or the values given (add, and set on a list); `to` is the
    value they become (rename, move, set) or the replacement text (replace)."""
    kind: Kind
    field: str
    values: list[str] = []
    to: str = ""
    toField: str = ""
    find: str = ""


class InvalidAction(ValueError):
    pass


def check(actions: list[Action]) -> list[Action]:
    """Refuse what cannot mean anything — before a single title is looked at.
    Folded vocabularies are folded here, so a rename into `Webtoon` lands as
    the `webtoon` the type field stores."""
    if not actions:
        raise InvalidAction("nothing to do")
    out = []
    for a in actions:
        sh = shape(a.field)
        if sh is None:
            raise InvalidAction(f"{a.field} holds no values an operation can change")
        if a.field in FOLDED:
            a = a.model_copy(update={"values": [v.strip().lower() for v in a.values],
                                     "to": a.to.strip().lower(), "find": a.find.lower()})
        if a.kind in ("rename", "remove", "move") and not a.values:
            raise InvalidAction(f"{a.kind} needs the values it acts on")
        if a.kind == "rename" and not a.to.strip():
            raise InvalidAction("rename needs the value to rename into — remove takes values away")
        if a.field == "title" and a.kind in ("remove", "clear", "move", "set"):
            raise InvalidAction("every title keeps its own title — rename or replace text in it")
        if sh == "flags" and a.kind in ("rename", "replace", "move"):
            raise InvalidAction("flags are a fixed set — add, remove, set or clear them")
        if a.kind == "move":
            to_shape = shape(a.toField)
            if sh not in ("list", "single") or to_shape not in ("list", "single", "flags"):
                raise InvalidAction("a value moves between a title's own fields")
            if a.toField in (a.field, "title"):
                raise InvalidAction("a move needs another field to move into")
            if a.toField in FOLDED or to_shape == "flags":
                a = a.model_copy(update={"to": a.to.strip().lower()})
            if to_shape == "flags" and a.to and a.to not in FLAG_NAMES:
                raise InvalidAction(f"flags are {', '.join(FLAG_NAMES)}")
        if a.kind == "add" and sh not in ("list", "flags"):
            raise InvalidAction("add puts values into a list — set fills a single value")
        if a.kind in ("add", "set") and sh == "flags" and set(a.values) - set(FLAG_NAMES):
            raise InvalidAction(f"flags are {', '.join(FLAG_NAMES)}")
        if a.kind == "replace" and not a.find:
            raise InvalidAction("replace needs the text to find")
        if a.kind in ("rename", "set") and not _fits(a.field, a.to):
            raise InvalidAction(f"{a.to!r} is not what {a.field} holds")
        if a.kind == "move" and a.to and not _fits(a.toField, a.to):
            raise InvalidAction(f"{a.to!r} is not what {a.toField} holds")
        out.append(a)
    return out


@dataclass
class Effect:
    changes: dict[str, tuple[object, object]] = dataclass_field(default_factory=dict)
    conflicts: set[str] = dataclass_field(default_factory=set)
    origins: dict[str, tuple[object, object]] = dataclass_field(default_factory=dict)  # by `claim`

    def __bool__(self) -> bool:
        return bool(self.changes)


def place(cur: list[str] | str, moving: list[str], to_shape: str | None) -> tuple[object, list[str]]:
    """Where moved values go, decided BEFORE anything leaves its field: the new
    destination value and which of `moving` it took. A single slot takes one
    value, and only when empty (or already holding it); several different
    candidates for one slot are a conflict, never decided silently."""
    if to_shape == "list":
        have = list(cur)
        for v in moving:
            if v not in have:
                have.append(v)
        return have, moving
    if to_shape == "flags":
        took = [v for v in moving if v in FLAG_NAMES]
        return _dedupe([*cur, *took]), took
    distinct = {v.casefold() for v in moving}
    if len(distinct) != 1:
        return cur, []
    if not cur:
        return moving[0], moving
    return cur, moving if cur.casefold() in distinct else []


def apply(doc: TitleDoc, actions: list[Action]) -> Effect:
    """Run the actions over one document IN PLACE, in order — each sees what
    the earlier ones made. Returns what changed, field by field. A write the
    field cannot hold (a date that is not one, an empty title) is left out and
    counted as a conflict."""
    before: dict[str, object] = {}
    conflicts: set[str] = set()

    def put(fid: str, value) -> bool:
        if isinstance(value, str) and shape(fid) == "single" and not _fits(fid, value):
            conflicts.add(fid)
            return False
        if fid not in before:
            before[fid] = read(doc, fid)
        write(doc, fid, value)
        return True

    for a in actions:
        sh, cur, hit = shape(a.field), read(doc, a.field), set(a.values)
        if a.kind == "rename":
            if sh == "list":
                put(a.field, _dedupe([a.to if v in hit else v for v in cur]))
            elif sh == "single" and cur in hit:
                put(a.field, a.to)
            elif sh == "chapter":
                put(a.field, {k: a.to if v in hit else v for k, v in cur.items()})
        elif a.kind == "remove":
            if sh in ("list", "flags"):
                put(a.field, [v for v in cur if v not in hit])
            elif sh == "single" and cur in hit:
                put(a.field, "")
            elif sh == "chapter":
                put(a.field, {k: "" if v in hit else v for k, v in cur.items()})
        elif a.kind == "move":
            here = [v for v in cur if v in hit] if sh == "list" else ([cur] if cur in hit else [])
            if not here:
                continue
            fold = a.toField in FOLDED or shape(a.toField) == "flags"
            targets = [(a.to or v).strip().lower() if fold else (a.to or v) for v in here]
            dest, took = place(read(doc, a.toField), targets, shape(a.toField))
            # a value leaves its field only once it has somewhere to be
            placed = {v for v, t in zip(here, targets) if t in took}
            if len(placed) < len(here):
                conflicts.add(a.toField)
            if not placed or not put(a.toField, dest):
                continue
            put(a.field, [v for v in cur if v not in placed] if sh == "list" else "")
            # a value keeps the origin it came with into a field it fills
            if not before.get(a.toField) and a.field in doc.provenance:
                doc.provenance[a.toField] = doc.provenance[a.field].model_copy()
        elif a.kind == "add":
            put(a.field, _dedupe([*cur, *a.values]))
        elif a.kind == "set":
            if sh == "single":
                put(a.field, a.to)
            elif sh == "chapter":
                put(a.field, {k: a.to for k in cur})
            elif sh == "flags":
                put(a.field, [v for v in FLAG_NAMES if v in hit])
            else:
                put(a.field, _dedupe(a.values))
        elif a.kind == "clear":
            put(a.field, {k: "" for k in cur} if sh == "chapter" else ([] if sh in ("list", "flags") else ""))
        elif a.kind == "replace":
            if sh == "single":
                put(a.field, cur.replace(a.find, a.to).strip())
            elif sh == "list":
                put(a.field, _dedupe([v.replace(a.find, a.to).strip() for v in cur]))
            elif sh == "chapter":
                put(a.field, {k: v.replace(a.find, a.to).strip() for k, v in cur.items()})

    changes: dict[str, tuple[object, object]] = {}
    for f, b in before.items():
        a = read(doc, f)
        if isinstance(b, dict):  # a chapter field: only the rows that changed
            moved = {k for k in a if a[k] != b.get(k)}
            if moved:
                changes[f] = ({k: b.get(k, "") for k in moved}, {k: a[k] for k in moved})
        elif a != b:
            changes[f] = (b, a)
    return Effect(changes, conflicts)


PROV_NOTE = "provenance:"  # a journal row holding a field's origin from before the owner's edit


def claim(doc: TitleDoc, eff: Effect) -> None:
    """The owner editing many titles at once is the owner editing each of them:
    every field the change touched becomes manual, so capture leaves it alone
    (state-model §5). What each origin was rides along, for a revert."""
    mine = FieldProvenance(origin="manual")
    for f in eff.changes:
        if shape(f) == "chapter":
            continue
        was = doc.provenance.get(f)
        if was != mine:
            doc.provenance[f] = mine.model_copy()
            eff.origins[f] = (was.model_dump() if was else None, mine.model_dump())


# ---- scope -----------------------------------------------------------------------

def in_scope(lib: Library, ids: list[str] | None, selection: dict) -> list[tuple[str, TitleDoc]]:
    """The titles an operation may touch, as the INDEX holds them — a selection
    of ids, or exactly what the library shows under these filters."""
    if ids is not None:
        return lib.index.docs_for(ids)
    return [(tid, doc) for tid, doc, _m, _c in lib.index.query(**selection)]


def _shown(value) -> str:
    if isinstance(value, dict):
        vals = Counter(v for v in value.values() if v)
        return ", ".join(f"{v} ×{n}" if n > 1 else v for v, n in vals.most_common()) or "—"
    if isinstance(value, list):
        return ", ".join(value) or "—"
    return value or "—"


class SampleRow(BaseModel):
    titleId: str
    title: str
    field: str
    before: str
    after: str


class Preview(BaseModel):
    inScope: int
    titles: int            # titles that change
    manual: int            # changed fields whose value the owner typed
    auto: int              # changed fields whose value came from capture
    conflicts: int         # titles where something had nowhere to go, or did not fit
    shelfMoves: int        # titles that change shelf
    shelfRename: list[str] = []  # [old, new] when the whole shelf is renamed at once
    chapterTwins: int = 0  # chapter rows that come to share number + language + group
    untouched: int = 0     # titles that already hold the rename's target
    sample: list[SampleRow] = []


@dataclass
class Plan:
    preview: Preview
    ids: list[str]  # the titles that change, in scope order


def _twins(doc: TitleDoc) -> int:
    seen = Counter((c.num, c.lang, c.group) for c in doc.chapters)
    return sum(n for n in seen.values() if n > 1)


def plan(lib: Library, docs: list[tuple[str, TitleDoc]], actions: list[Action],
         sample: int = 8) -> Plan:
    """Everything an operation will do, worked out from the index — no file is
    opened, which is why the preview is instant on a large library."""
    renames = [a for a in actions if a.kind == "rename"]
    ids: list[str] = []
    manual = auto = conflicts = moves = twins = untouched = 0
    rows: list[SampleRow] = []
    moved: dict[str, tuple[str, str]] = {}
    for tid, doc in docs:
        shelf = type_dir_name(doc.meta.type)
        targets_held = any(a.to in values_of(doc, a.field) for a in renames)
        twins_before = _twins(doc)
        eff = apply(doc, actions)
        conflicts += bool(eff.conflicts)
        if not eff:
            untouched += targets_held
            continue
        ids.append(tid)
        for f, (b, a) in eff.changes.items():
            if shape(f) != "chapter":
                if typed_by_owner(doc, f):
                    manual += 1
                else:
                    auto += 1
            if len(rows) < sample:
                rows.append(SampleRow(titleId=tid, title=doc.meta.title, field=f,
                                      before=_shown(b), after=_shown(a)))
        if type_dir_name(doc.meta.type) != shelf:
            moves += 1
            moved[tid] = (shelf, type_dir_name(doc.meta.type))
        twins += max(0, _twins(doc) - twins_before)
    pv = Preview(inScope=len(docs), titles=len(ids), manual=manual, auto=auto,
                 conflicts=conflicts, shelfMoves=moves, chapterTwins=twins,
                 untouched=untouched, sample=rows)
    pv.shelfRename = _whole_shelf(lib, moved)
    return Plan(pv, ids)


def _whole_shelf(lib: Library, moved: dict[str, tuple[str, str]]) -> list[str]:
    """[old, new] when every title on one shelf moves to one shelf that does
    not exist yet — then the move is a single directory rename."""
    pairs = set(moved.values())
    if len(pairs) != 1:
        return []
    old, new = next(iter(pairs))
    shelves = {tid: type_dir_name(t) for tid, t in lib.index.types().items()}
    on_old = {tid for tid, s in shelves.items() if s == old}
    if on_old != set(moved) or new in shelves.values() or (lib.root / new).exists():
        return []
    return [old, new]


# ---- the run ------------------------------------------------------------------

WORKERS = 4      # file I/O in flight at once: overlaps a network share's round trips
BATCH = 200      # titles per index transaction


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Outcome:
    changed: int = 0
    failed: int = 0
    stopped: bool = False
    docs: dict[str, TitleDoc] = dataclass_field(default_factory=dict)  # what each changed title holds now


def _rewrite_all(lib: Library, ids: list[str], change_for: Callable[[str], Callable[[TitleDoc], object]],
                 progress: Progress, stop: Event | None, run: int | None = None,
                 journal: Callable[[str, object], list[tuple[str, str, object, object]]] | None = None,
                 ) -> Outcome:
    """Rewrite these titles, each through the change `change_for` gives it,
    WORKERS at a time, refreshing the index (and the journal) every BATCH
    titles. A title that fails is counted and left as it was; whatever was
    written is journaled and indexed even when something fails."""
    out = Outcome()
    total, done = len(ids), 0
    rows: list[tuple[str, TitleDoc, int, int]] = []
    notes: list[tuple[str, str, object, object]] = []

    def flush() -> None:
        if rows or notes:
            lib.index.apply_meta(rows, run, notes)
            rows.clear()
            notes.clear()

    if progress is not None:
        progress(0, total)
    queue = iter(ids)
    inflight: dict[Future, str] = {}
    try:
        with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="lb-ops") as pool:
            while True:
                while len(inflight) < WORKERS * 4 and not (stop is not None and stop.is_set()):
                    tid = next(queue, None)
                    if tid is None:
                        break
                    inflight[pool.submit(lib.vault.rewrite_in_place, tid, change_for(tid))] = tid
                if not inflight:
                    break
                finished, _ = wait(inflight, return_when=FIRST_COMPLETED)
                for fut in finished:
                    tid = inflight.pop(fut)
                    done += 1
                    try:
                        got = fut.result()
                    except Exception as e:  # noqa: BLE001 — one title must not cost the others
                        out.failed += 1
                        log.warning("metadata: %s left as it was: %s", tid, e)
                        continue
                    if got is not None:
                        doc, effect, stamp, read_stamp = got
                        rows.append((tid, doc, stamp, read_stamp))
                        if journal is not None:
                            notes.extend(journal(tid, effect))
                        out.docs[tid] = doc
                        out.changed += 1
                    if len(rows) >= BATCH:
                        flush()
                if progress is not None:
                    progress(done, total)
    finally:
        flush()
    out.stopped = done < total
    return out


def _carry_favourites(lib: Library, actions: list[Action]) -> None:
    """A person merged into another keeps their favourite mark: the marks are
    kept by name, so the name it is carried to inherits it."""
    favs = lib.vault.author_favorites()
    for a in actions:
        if a.kind == "rename" and a.field in PEOPLE and any(safe_id(v) in favs for v in a.values):
            lib.vault.set_author_favorite(safe_id(a.to), True)


def run(lib: Library, docs: list[tuple[str, TitleDoc]], actions: list[Action], *,
        summary: str, scope: dict, own: bool = False, progress: Progress = None,
        stop: Event | None = None) -> dict:
    """`own`: the owner's edit of these titles (the library's bulk editor), not
    the tidying of a vocabulary — the fields it changes become theirs."""
    from . import pages  # pages builds on this module
    started = time.monotonic()
    p = plan(lib, docs, actions, sample=0)
    if not p.ids:
        return {"run": 0, "changed": 0, "failed": 0, "stopped": False, "seconds": 0.0}
    run_id = lib.index.journal_open(_now(), summary, scope, [a.model_dump() for a in actions])
    out = Outcome()
    try:
        if p.preview.shelfRename and lib.vault.rename_shelf(*p.preview.shelfRename, p.ids):
            # The shelf has moved; a run put down now would leave titles on it
            # still saying their old type. It finishes.
            stop = None

        def change(doc: TitleDoc) -> Effect:
            eff = apply(doc, actions)
            if own and eff:
                claim(doc, eff)
            return eff

        def journal(tid: str, eff: Effect) -> list[tuple[str, str, object, object]]:
            return ([(tid, f, b, a) for f, (b, a) in eff.changes.items()]
                    + [(tid, PROV_NOTE + f, b, a) for f, (b, a) in eff.origins.items()])

        out = _rewrite_all(lib, p.ids, lambda _tid: change, progress, stop, run_id, journal)
        _carry_favourites(lib, actions)
        pages.follow(lib, actions, lambda note: lib.index.apply_meta([], run_id, [note]))
    finally:
        seconds = time.monotonic() - started
        lib.index.journal_close(run_id, out.changed, seconds, out.stopped)
        outcome = f"{out.changed} title(s) changed" if out.changed else "nothing changed"
        if out.failed:
            outcome += f", {out.failed} left as they were (unreadable, newer, or in use)"
        lib.record_op(summary, started, outcome, stopped=out.stopped)
    return {"run": run_id, "changed": out.changed, "failed": out.failed, "stopped": out.stopped,
            "seconds": round(seconds, 2)}


def revert(lib: Library, run_id: int, *, progress: Progress = None,
           stop: Event | None = None) -> dict:
    """Put back what a run changed — only where a field still holds what the
    run wrote, so a later edit is never rolled back."""
    from . import pages  # pages builds on this module
    started = time.monotonic()
    by_title: dict[str, list[tuple[str, object, object]]] = defaultdict(list)
    moved: list[tuple[str, object, object]] = []  # value pages the run carried to a new spelling
    for tid, f, b, a in lib.index.journal_changes(run_id):
        if not tid and f.startswith(pages.PAGE_NOTE):
            moved.append((f, b, a))
        else:
            by_title[tid].append((f, b, a))
    kept: list[int] = []  # one entry per value changed since the run — workers append

    def undo_for(tid: str) -> Callable[[TitleDoc], bool]:
        def undo(doc: TitleDoc) -> bool:
            changed = False
            back: set[str] = set()  # fields put back as they were
            for f, b, a in by_title[tid]:
                if f.startswith(PROV_NOTE) or shape(f) is None:
                    continue
                cur = read(doc, f)
                if isinstance(a, dict):
                    back = dict(cur)
                    for cid, written in a.items():
                        if cur.get(cid) == written:
                            back[cid] = (b or {}).get(cid, "")
                        elif cid in cur:
                            kept.append(1)
                    if back != cur:
                        write(doc, f, back)
                        changed = True
                elif cur == a:
                    write(doc, f, b)
                    changed = True
                    back.add(f)
                else:
                    kept.append(1)
            # an origin goes back only with its value: a value typed since keeps
            # the manual mark that protects it
            for f, b, a in by_title[tid]:
                fid = f.removeprefix(PROV_NOTE)
                if fid == f or fid not in back:
                    continue
                cur = doc.provenance.get(fid)
                if (cur.model_dump() if cur else None) == a:
                    if b is None:
                        doc.provenance.pop(fid, None)
                    else:
                        doc.provenance[fid] = FieldProvenance.model_validate(b)
            return changed
        return undo

    out = Outcome()
    pages_left = 0  # moved pages that could not be put back: the run is not reverted yet
    finished = False  # the rewrite came back at all — an exception out of it reverted nothing provable
    try:
        out = _rewrite_all(lib, list(by_title), undo_for, progress, stop)
        if not out.stopped:
            pages_kept, pages_left = pages.unfollow(lib, moved)
            kept.extend([1] * pages_kept)
        finished = True
    finally:
        if finished and not out.stopped and not out.failed and not pages_left:
            lib.index.journal_reverted(run_id, _now())
        outcome = f"{out.changed} title(s) reverted"
        if kept:
            outcome += f", {len(kept)} value(s) changed since and kept"
        if out.failed:
            outcome += f", {out.failed} left as they were"
        if pages_left:
            outcome += f", {pages_left} value page(s) not put back"
        lib.record_op(f"revert run {run_id}", started, outcome, stopped=out.stopped)
    return {"reverted": out.changed, "kept": len(kept), "failed": out.failed, "stopped": out.stopped}


# ---- what a field holds across a scope -------------------------------------------

def field_values(docs: list[tuple[str, TitleDoc]], fid: str) -> list[dict]:
    """Every value of one field in the scope, with how many titles hold it and
    how many of those the owner typed."""
    titles: Counter = Counter()
    typed: Counter = Counter()
    for _tid, doc in docs:
        mine = typed_by_owner(doc, fid)
        for v in values_of(doc, fid):
            titles[v] += 1
            typed[v] += mine
    return [{"value": v, "titles": n, "typed": typed[v]}
            for v, n in sorted(titles.items(), key=lambda x: (-x[1], x[0].casefold()))]


def field_counts(docs: list[tuple[str, TitleDoc]]) -> dict[str, int]:
    """How many distinct values each operable field holds in the scope."""
    ids = [f.id for f in fields.registry() if shape(f.id) is not None] + list(CHAPTER_FIELDS)
    seen: dict[str, set[str]] = {fid: set() for fid in dict.fromkeys(ids)}
    for _tid, doc in docs:
        for fid, bag in seen.items():
            bag |= values_of(doc, fid)
    return {fid: len(bag) for fid, bag in seen.items()}


def describe(actions: list[Action]) -> str:
    """One line for the history: what was done, in the owner's words."""
    parts = []
    for a in actions:
        vals = ", ".join(a.values)
        if a.kind == "rename":
            parts.append(f"{'merged' if len(a.values) > 1 else 'renamed'} {a.field} {vals} → {a.to}")
        elif a.kind == "remove":
            parts.append(f"removed {a.field} {vals}")
        elif a.kind == "move":
            parts.append(f"moved {a.field} {vals} → {a.toField}" + (f": {a.to}" if a.to else ""))
        elif a.kind == "add":
            parts.append(f"added {a.field} {vals}")
        elif a.kind == "set":
            parts.append(f"set {a.field} to {a.to or vals}")
        elif a.kind == "clear":
            parts.append(f"cleared {a.field}")
        else:
            parts.append(f"replaced “{a.find}” with “{a.to}” in {a.field}")
    return "; ".join(parts)


def scope_json(ids: list[str] | None, selection: dict) -> dict:
    if ids is not None:
        return {"ids": len(ids)}
    return {k: v for k, v in json.loads(json.dumps(selection, default=list)).items() if v}
