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

from .models import TitleDoc

_SCHEMA_VERSION = 4

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
    media_at INTEGER NOT NULL DEFAULT 0
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


def _flag_names(doc: TitleDoc) -> set[str]:
    f = doc.meta.flags
    return {name for name in ("adult", "ai", "censored") if getattr(f, name)}


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
                "unread", "started", "finished", "touched", "doc", "media", "media_at")

    @staticmethod
    def _row(title_id: str, doc: TitleDoc, touched: int = 0,
             media: dict[str, dict] | None = None, media_at: int = 0) -> dict:
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
        }

    @property
    def _insert_sql(self) -> str:
        cols = ", ".join(self._COLUMNS)
        vals = ", ".join(f":{c}" for c in self._COLUMNS)
        return f"INSERT INTO titles ({cols}) VALUES ({vals})"

    def rebuild(self, docs: dict[str, TitleDoc], touched: dict[str, int] | None = None,
                media: dict[str, dict[str, dict]] | None = None,
                media_at: dict[str, int] | None = None) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM titles")
            self._db.executemany(
                self._insert_sql,
                [self._row(tid, doc, (touched or {}).get(tid, 0), (media or {}).get(tid),
                           (media_at or {}).get(tid, 0))
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
                     media=excluded.media, media_at=excluded.media_at"""

    def upsert(self, title_id: str, doc: TitleDoc, touched: int = 0,
               media: dict[str, dict] | None = None, media_at: int = 0) -> None:
        with self._lock, self._db:
            self._db.execute(self._upsert_sql, self._row(title_id, doc, touched, media, media_at))

    def upsert_many(self, rows: list[tuple[str, TitleDoc, int, dict[str, dict], int]]) -> None:
        """One transaction for many titles — a launch that has to re-read a whole
        library must not pay a commit per title."""
        if not rows:
            return
        with self._lock, self._db:
            self._db.executemany(self._upsert_sql, [self._row(*r) for r in rows])

    def remove(self, title_id: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM titles WHERE id = ?", (title_id,))

    def stamps(self) -> dict[str, tuple[int, int]]:
        """Every indexed title's id and the (document, chapter-dir) mtimes it was
        indexed at. Comparing these against the vault is what lets a launch
        reload only what changed instead of re-reading the whole library."""
        with self._lock:
            rows = self._db.execute("SELECT id, touched, media_at FROM titles").fetchall()
        return {r["id"]: (r["touched"], r["media_at"]) for r in rows}

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) AS n FROM titles").fetchone()["n"]

    def get(self, title_id: str) -> tuple[TitleDoc, dict[str, dict]] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT doc, media FROM titles WHERE id = ?", (title_id,)).fetchone()
        return (TitleDoc.model_validate_json(row["doc"]), json.loads(row["media"])) if row else None

    def all_docs(self) -> list[tuple[str, TitleDoc]]:
        with self._lock:
            rows = self._db.execute("SELECT id, doc FROM titles").fetchall()
        return [(r["id"], TitleDoc.model_validate_json(r["doc"])) for r in rows]

    def query(
        self,
        *,
        search: str | None = None,
        fav: bool | None = None,
        min_rating: int | None = None,
        progress: str | None = None,  # unread | reading | completed (reading progress, NOT manga status)
        types: tuple[str, ...] = (),
        types_not: tuple[str, ...] = (),
        statuses: tuple[str, ...] = (),
        statuses_not: tuple[str, ...] = (),
        genres: tuple[str, ...] = (),
        genres_not: tuple[str, ...] = (),
        tags: tuple[str, ...] = (),
        tags_not: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
        languages_not: tuple[str, ...] = (),
        flags: tuple[str, ...] = (),
        flags_not: tuple[str, ...] = (),
        authors: tuple[str, ...] = (),
        authors_not: tuple[str, ...] = (),
        characters: tuple[str, ...] = (),
        characters_not: tuple[str, ...] = (),
        sort: str = "updated",
    ) -> list[tuple[str, TitleDoc, dict[str, dict]]]:
        where: list[str] = []
        params: list[object] = []
        if search:
            where.append("(title LIKE ? OR people LIKE ?)")
            like = f"%{search}%"
            params += [like, like]
        if types:
            where.append(f"type IN ({','.join('?' * len(types))})")
            params += list(types)
        if types_not:
            where.append(f"type NOT IN ({','.join('?' * len(types_not))})")
            params += list(types_not)
        if statuses:
            where.append(f"status IN ({','.join('?' * len(statuses))})")
            params += list(statuses)
        if statuses_not:
            where.append(f"status NOT IN ({','.join('?' * len(statuses_not))})")
            params += list(statuses_not)
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

        sql = "SELECT id, doc, media FROM titles"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + _SORTS.get(sort, _SORTS["updated"])
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        out = [(r["id"], TitleDoc.model_validate_json(r["doc"]), json.loads(r["media"]))
               for r in rows]

        # multi-valued facets live in the JSON doc — filter in Python (local scale)
        def keep(doc: TitleDoc) -> bool:
            g, t = set(doc.meta.genres), set(doc.meta.tags)
            langs = {c.lang for c in doc.chapters if c.lang}
            fl = _flag_names(doc)
            people = {*doc.meta.authors, *doc.meta.artists}
            if genres and not set(genres) <= g:
                return False
            if genres_not and set(genres_not) & g:
                return False
            if tags and not set(tags) <= t:
                return False
            if tags_not and set(tags_not) & t:
                return False
            if languages and not set(languages) <= langs:
                return False
            if languages_not and set(languages_not) & langs:
                return False
            if flags and not set(flags) <= fl:
                return False
            if flags_not and set(flags_not) & fl:
                return False
            if authors and not set(authors) <= people:
                return False
            if authors_not and set(authors_not) & people:
                return False
            chars = set(doc.meta.characters)
            if characters and not set(characters) <= chars:
                return False
            if characters_not and set(characters_not) & chars:
                return False
            return True

        return [(i, d, m) for i, d, m in out if keep(d)]

    def facet_counts(self, sel: dict) -> dict[str, list[dict]]:
        """Linked facet counts for the current selection: each facet is counted
        with every OTHER facet's filters applied. Single-valued facets (type,
        status) ignore their own selection so the alternatives stay visible;
        multi-valued ones keep their own includes (co-occurrence counts)."""
        # Eight facets ask eight questions, but they collapse to ONE whenever the
        # override does not actually change the selection — which is the whole
        # unfiltered library, i.e. every launch.
        seen: dict[tuple, list[TitleDoc]] = {}

        def docs(**over) -> list[TitleDoc]:
            kw = {**sel, **over}
            key = tuple(sorted((k, v) for k, v in kw.items() if v))
            if key not in seen:
                seen[key] = [d for _, d, _m in self.query(**kw)]
            return seen[key]

        out: dict[str, list[dict]] = {}
        type_docs = docs(types=(), types_not=())
        out["types"] = _counted(Counter(d.meta.type for d in type_docs if d.meta.type))
        status_docs = docs(statuses=(), statuses_not=())
        out["statuses"] = _counted(Counter(d.meta.status for d in status_docs if d.meta.status))
        genre_docs = docs(genres_not=())
        out["genres"] = _counted(Counter(g for d in genre_docs for g in set(d.meta.genres)))
        tag_docs = docs(tags_not=())
        out["tags"] = _counted(Counter(t for d in tag_docs for t in set(d.meta.tags)))
        lang_docs = docs(languages_not=())
        out["languages"] = _counted(Counter(l for d in lang_docs for l in {c.lang for c in d.chapters if c.lang}))
        flag_docs = docs(flags_not=())
        out["flags"] = _counted(Counter(f for d in flag_docs for f in _flag_names(d)))
        author_docs = docs(authors_not=())
        out["authors"] = _counted(Counter(n for d in author_docs for n in {*d.meta.authors, *d.meta.artists} if n))
        char_docs = docs(characters_not=())
        out["characters"] = _counted(Counter(c for d in char_docs for c in set(d.meta.characters)))
        return out
