"""SQLite index over the library. It is a *rebuildable cache*: the on-disk
`title.json` documents are the source of truth, so dropping or corrupting this DB
never loses content (`rebuild()` re-scans the vault and repopulates it). The
schema is versioned via PRAGMA user_version — a mismatch simply drops the cache.

Filtering model (design: linked faceted filters with exclusions):
- single-valued facets (type, status): include = OR within the facet;
- multi-valued facets (genres, tags, languages): include = AND (narrowing);
- excludes always remove a title that carries the value;
- reading progress is the USER layer axis, never the manga's own status:
  unread = not started · reading = started but not finished · completed = every
  chapter marked read (titles without chapters are simply "not started").
"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
from pathlib import Path

from . import fields
from .fields import Field
from .models import TitleDoc

_SCHEMA_VERSION = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS titles (
    rowid    INTEGER PRIMARY KEY AUTOINCREMENT,
    id       TEXT UNIQUE NOT NULL,
    title    TEXT NOT NULL,
    people   TEXT NOT NULL,
    type     TEXT NOT NULL,
    status   TEXT NOT NULL,
    rating   INTEGER NOT NULL,
    fav      INTEGER NOT NULL,
    unread   INTEGER NOT NULL,
    started  INTEGER NOT NULL,
    finished INTEGER NOT NULL,
    touched  INTEGER NOT NULL DEFAULT 0,
    doc      TEXT NOT NULL,
    media    TEXT NOT NULL DEFAULT '{}',
    media_at INTEGER NOT NULL DEFAULT 0,
    cover    TEXT NOT NULL DEFAULT ''
);
"""

_SORTS = {
    # `touched` is the document's own mtime, so "recently updated" survives a
    # rebuild — rowid order would silently become creation (then alphabetical)
    # order the first time the cache is rebuilt
    "updated": "touched DESC, rowid DESC",
    "title": "title COLLATE NOCASE ASC",
    "rating": "rating DESC, title COLLATE NOCASE ASC",
    "unread": "unread DESC, title COLLATE NOCASE ASC",
}


def _counted(counter: Counter) -> list[dict]:
    return [{"v": v, "n": n} for v, n in sorted(counter.items(), key=lambda x: (-x[1], x[0]))]


class LibraryIndex:
    def __init__(self, db_path: Path | str) -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # ONE connection shared by the threadpool means one transaction context:
        # without this lock a reader can observe (and another writer can commit)
        # a rebuild's half-emptied table.
        self._lock = threading.RLock()
        # the index is a cache: an old schema is dropped, never migrated
        if self._db.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION:
            self._db.executescript("DROP TABLE IF EXISTS titles;")
            self._db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        self._db.executescript(_SCHEMA)
        # …and a table left by a half-applied schema (an interrupted upgrade, a
        # column added without bumping the version) is dropped too: a cache may
        # cost a rebuild, never a launch that cannot start.
        have = {r["name"] for r in self._db.execute("PRAGMA table_info(titles)")}
        if not set(self._COLUMNS) <= have:
            self._db.executescript("DROP TABLE IF EXISTS titles;")
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    _COLUMNS = ("id", "title", "people", "type", "status", "rating", "fav",
                "unread", "started", "finished", "touched", "doc", "media", "media_at",
                "cover")

    @staticmethod
    def _row(title_id: str, doc: TitleDoc, touched: int = 0,
             media: dict[str, dict] | None = None, media_at: int = 0, cover: str = "") -> dict:
        m, u = doc.meta, doc.user
        states = [u.read.get(c.id, "unread") for c in doc.chapters]
        total = len(states)
        read_done = sum(1 for s in states if s == "read")
        return {
            # `people` is a denormalized search column: alt title + all names
            "id": title_id, "title": m.title,
            "people": " ".join([m.alt, *m.authors, *m.artists]),
            "type": m.type, "status": m.status, "rating": u.rating, "fav": int(u.fav),
            "unread": total - read_done,
            "started": int(any(s in ("read", "reading") for s in states)),
            "finished": int(total > 0 and read_done == total),
            "touched": touched,
            "doc": doc.model_dump_json(by_alias=True),
            # The chapter sidecars, carried WITH the row: composing a listing
            # otherwise re-reads one JSON file per chapter from disk, which is
            # what made a big library take seconds to appear.
            "media": json.dumps(media or {}),
            "media_at": media_at,
            # the cover endpoint URL, versioned by the file's mtime: composing it
            # per listing means one stat per title, on the network, every time
            "cover": cover,
        }

    @property
    def _insert_sql(self) -> str:
        cols = ", ".join(self._COLUMNS)
        vals = ", ".join(f":{c}" for c in self._COLUMNS)
        return f"INSERT INTO titles ({cols}) VALUES ({vals})"

    def rebuild(self, docs: dict[str, TitleDoc], touched: dict[str, int] | None = None,
                media: dict[str, dict[str, dict]] | None = None,
                media_at: dict[str, int] | None = None,
                cover: dict[str, str] | None = None) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM titles")
            self._db.executemany(
                self._insert_sql,
                [self._row(tid, doc, (touched or {}).get(tid, 0), (media or {}).get(tid),
                           (media_at or {}).get(tid, 0), (cover or {}).get(tid, ""))
                 for tid, doc in docs.items()],
            )

    @property
    def _upsert_sql(self) -> str:
        return f"""{self._insert_sql}
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title, people=excluded.people, type=excluded.type,
                     status=excluded.status, rating=excluded.rating, fav=excluded.fav,
                     unread=excluded.unread, started=excluded.started, finished=excluded.finished,
                     touched=MAX(excluded.touched, titles.touched), doc=excluded.doc,
                     media=excluded.media, media_at=excluded.media_at,
                     cover=excluded.cover"""

    def upsert(self, title_id: str, doc: TitleDoc, touched: int = 0,
               media: dict[str, dict] | None = None, media_at: int = 0, cover: str = "") -> None:
        with self._lock, self._db:
            self._db.execute(self._upsert_sql,
                             self._row(title_id, doc, touched, media, media_at, cover))

    def upsert_many(self, rows: list[tuple[str, TitleDoc, int, dict[str, dict], int, str,
                                            tuple[int, int, str]]]) -> None:
        """One transaction for many titles — a launch that has to re-read a whole
        library must not pay a commit per title.

        This is the path a background verification uses, and it does NOT hold the
        title locks, so each row carries the stamps its scan SAW in the index and
        updates only while they still hold. That refuses exactly one thing — a
        row someone else rewrote in the meantime — and nothing else: a vault
        restored from a backup, whose files are OLDER than the ones indexed, is
        still picked up, which a "newer wins" rule would ignore forever."""
        if not rows:
            return
        guarded = (f"{self._upsert_sql} WHERE titles.touched = :was_touched"
                   " AND titles.media_at = :was_media_at AND titles.cover = :was_cover")
        params = []
        for tid, doc, touched, media, media_at, cover, was in rows:
            row = self._row(tid, doc, touched, media, media_at, cover)
            # all THREE stamps: a cover replaced from a local file changes only
            # this column — title.json and the chapter directory never move
            row["was_touched"], row["was_media_at"], row["was_cover"] = was
            params.append(row)
        with self._lock, self._db:
            self._db.executemany(guarded, params)

    def remove(self, title_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM titles WHERE id = ?", (title_id,))

    def stamps(self) -> dict[str, tuple[int, int, str]]:
        """Every indexed title's id with the (document mtime, chapter-dir mtime,
        cover URL) it was indexed at. Comparing these against one vault scan is
        what lets a launch reload only what changed — the cover URL carries the
        cover's own mtime, so a cover replaced outside the app is noticed too."""
        with self._lock:
            rows = self._db.execute("SELECT id, touched, media_at, cover FROM titles").fetchall()
        return {r["id"]: (r["touched"], r["media_at"], r["cover"]) for r in rows}

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) AS n FROM titles").fetchone()["n"]

    def get(self, title_id: str) -> tuple[TitleDoc, dict[str, dict], str] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT doc, media, cover FROM titles WHERE id = ?", (title_id,)).fetchone()
        if not row:
            return None
        return TitleDoc.model_validate_json(row["doc"]), json.loads(row["media"]), row["cover"]

    def all_docs(self) -> list[tuple[str, TitleDoc, str]]:
        with self._lock:
            rows = self._db.execute("SELECT id, doc, cover FROM titles").fetchall()
        return [(r["id"], TitleDoc.model_validate_json(r["doc"]), r["cover"]) for r in rows]

    def query(
        self,
        *,
        search: str | None = None,
        fav: bool | None = None,
        min_rating: int | None = None,
        progress: str | None = None,  # unread | reading | completed (reading progress, NOT manga status)
        include: dict[str, tuple[str, ...]] | None = None,
        exclude: dict[str, tuple[str, ...]] | None = None,
        sort: str = "updated",
    ) -> list[tuple[str, TitleDoc, dict[str, dict], str]]:
        """Field filters arrive keyed by field id (`fields.py`), so a field this
        module has never heard of filters exactly like one it has."""
        include, exclude = include or {}, exclude or {}
        where: list[str] = []
        params: list[object] = []
        if search:
            where.append("(title LIKE ? OR people LIKE ?)")
            like = f"%{search}%"
            params += [like, like]
        # single-valued fields have an indexed column — let SQLite do those
        for f in fields.facets():
            if not f.column:
                continue
            for values, op in ((include.get(f.id, ()), "IN"), (exclude.get(f.id, ()), "NOT IN")):
                if values:
                    where.append(f"{f.column} {op} ({','.join('?' * len(values))})")
                    params += list(values)
        if fav:
            where.append("fav = 1")
        if min_rating:
            where.append("rating >= ?")
            params.append(min_rating)
        if progress == "unread":
            where.append("started = 0")
        elif progress == "reading":
            where.append("started = 1 AND finished = 0")
        elif progress == "completed":
            where.append("finished = 1")

        sql = "SELECT id, doc, media, cover FROM titles"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + _SORTS.get(sort, _SORTS["updated"])
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        out = [(r["id"], TitleDoc.model_validate_json(r["doc"]), json.loads(r["media"]), r["cover"])
               for r in rows]

        # multi-valued fields live in the JSON doc — filter in Python (local scale)
        def keep(doc: TitleDoc) -> bool:
            for f in fields.facets():
                if f.column:
                    continue
                inc, exc = include.get(f.id, ()), exclude.get(f.id, ())
                if not inc and not exc:
                    continue
                have = fields.facet_values(f, doc)
                if inc and not set(inc) <= have:
                    return False
                if exc and set(exc) & have:
                    return False
            return True

        return [(i, d, m, c) for i, d, m, c in out if keep(d)]

    def facet_counts(self, sel: dict) -> dict[str, list[dict]]:
        """Linked facet counts for the current selection: each facet is counted
        with every OTHER facet's filters applied. Single-valued facets (type,
        status) ignore their own selection so the alternatives stay visible;
        multi-valued ones keep their own includes (co-occurrence counts)."""
        # Every facet asks its own question, but they collapse to ONE whenever
        # the override does not actually change the selection — which is the
        # whole unfiltered library, i.e. every launch.
        seen: dict[tuple, list[TitleDoc]] = {}

        def docs(without: Field) -> list[TitleDoc]:
            kw = dict(sel)
            kw["exclude"] = {k: v for k, v in (sel.get("exclude") or {}).items() if k != without.id}
            if without.column:  # single-valued: its own include hides the alternatives
                kw["include"] = {k: v for k, v in (sel.get("include") or {}).items()
                                 if k != without.id}
            key = (kw.get("search"), kw.get("fav"), kw.get("min_rating"), kw.get("progress"),
                   tuple(sorted((k, tuple(v)) for k, v in (kw.get("include") or {}).items() if v)),
                   tuple(sorted((k, tuple(v)) for k, v in (kw.get("exclude") or {}).items() if v)))
            if key not in seen:
                seen[key] = [d for _, d, _m, _c in self.query(**kw)]
            return seen[key]

        return {f.id: _counted(Counter(v for d in docs(f) for v in fields.facet_values(f, d) if v))
                for f in fields.facets()}
