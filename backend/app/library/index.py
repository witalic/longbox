"""SQLite index over the library. It is a *rebuildable cache*: the on-disk
`title.json` documents are the source of truth, so dropping or corrupting this DB
never loses content (`rebuild()` re-scans the vault and repopulates it). The
schema is versioned via PRAGMA user_version — a mismatch simply drops the cache.
Which is why the version is moved as rarely as it can be: dropping it makes the
first launch after an update re-read every title in the library, and on a
networked vault that is the difference between opening the app and waiting for
it. A change that existing rows already satisfy is not a schema change.

Every value a filter or a facet asks about is a row of `title_values_v1`
(fields.stored_values), and every title's card — what a listing sends of it —
is a row of `title_cards_v1`, both written in the same transaction as their
title: a selection is SQL over an index, and neither a listing nor a facet
parses a document.

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
import logging
import sqlite3
import threading
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from . import cards, fields
from .fields import Field
from .models import TitleDoc

log = logging.getLogger("longbox.library")

_SCHEMA_VERSION = 6

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
    cover    TEXT NOT NULL DEFAULT '',
    source   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS titles_source ON titles (source);
CREATE INDEX IF NOT EXISTS titles_stamps ON titles (id, touched, media_at, cover);
"""

# The journal of bulk metadata runs (design/metadata-management.md §5) — the ONE
# thing in this file a rebuild cannot recreate, so it is not part of the cache:
# its tables are versioned by name, a `titles` schema change or a rebuild leaves
# them alone, and only deleting index.db loses them. What is lost then is the
# ability to undo, never content — the vault still holds every value.
_JOURNAL = """
CREATE TABLE IF NOT EXISTS journal_runs_v1 (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    summary     TEXT NOT NULL,
    scope       TEXT NOT NULL,
    actions     TEXT NOT NULL,
    titles      INTEGER NOT NULL DEFAULT 0,
    seconds     REAL NOT NULL DEFAULT 0,
    stopped     INTEGER NOT NULL DEFAULT 0,
    reverted_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS journal_changes_v1 (
    run      INTEGER NOT NULL,
    title_id TEXT NOT NULL,
    field    TEXT NOT NULL,
    before   TEXT NOT NULL,
    after    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS journal_changes_v1_run ON journal_changes_v1 (run);
CREATE TABLE IF NOT EXISTS rule_stats_v1 (
    rule  TEXT PRIMARY KEY,
    fired INTEGER NOT NULL DEFAULT 0,
    last  TEXT NOT NULL DEFAULT ''
);
"""
JOURNAL_KEEP = 50

# Derived from `titles` alone, so a cache of the cache: versioned by name like
# the journal — a card of another shape, or made by other rules (what a
# stand-in is, what counts as unread), is another table — and filled from the
# rows (never by dropping `titles`, which re-reads the vault). A card carries
# the stamps of the row it was made from: a row written without one — before
# the table, or by an older build, which keeps no cards — is found by them
# (through `titles_stamps`, never reading a document) and given its card and
# values again. `rev` moves with every card written: what a window holds of a
# title is current while its rev is. `video` says the title holds an episode —
# what a pass over the episodes finds them by, without reading every row's media.
_SIDE = """
CREATE TABLE IF NOT EXISTS title_values_v1 (
    id    TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS title_values_v1_key ON title_values_v1 (id, field, value);
CREATE INDEX IF NOT EXISTS title_values_v1_value ON title_values_v1 (field, value);
CREATE TABLE IF NOT EXISTS title_cards_v1 (
    id       TEXT PRIMARY KEY,
    touched  INTEGER NOT NULL,
    media_at INTEGER NOT NULL,
    cover    TEXT NOT NULL,
    rev      INTEGER NOT NULL,
    video    INTEGER NOT NULL,
    card     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS title_cards_v1_video ON title_cards_v1 (video) WHERE video = 1;
"""
_SIDE_COLUMNS = {"title_values_v1": {"id", "field", "value"},
                 "title_cards_v1": {"id", "touched", "media_at", "cover", "rev", "video", "card"}}
_SIDE_TABLES = tuple(_SIDE_COLUMNS)


def _marks(values) -> str:
    return ",".join("?" * len(values))

_SORTS = {
    # `touched` is the document's own mtime, so "recently updated" survives a
    # rebuild — rowid order would silently become creation (then alphabetical)
    # order the first time the cache is rebuilt
    "updated": "titles.touched DESC, titles.rowid DESC",
    "title": "titles.title COLLATE NOCASE ASC",
    "rating": "titles.rating DESC, titles.title COLLATE NOCASE ASC",
    "unread": "titles.unread DESC, titles.title COLLATE NOCASE ASC",
}


def _site(domain: str, url: str) -> str:
    """The site a title came from: its recorded domain, else its link's host."""
    return domain or (urlsplit(url).hostname or "")


def _counted(counter: Counter) -> list[dict]:
    return [{"v": v, "n": n} for v, n in sorted(counter.items(), key=lambda x: (-x[1], x[0]))]


class LibraryIndex:
    def __init__(self, db_path: Path) -> None:
        # the vault it sits in exists already (Vault refuses one that does not)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        # ONE connection shared by the threadpool means one transaction context:
        # without this lock a reader can observe (and another writer can commit)
        # a rebuild's half-emptied table.
        self._lock = threading.RLock()
        # Moves with every write into `titles`. A window showing a list asks
        # whether it changed — one number — instead of reloading the library.
        self.epoch = 0
        # A write into `titles` failed: the vault may hold what the index does
        # not, and the session closes unsettled (Library.sync_in_background).
        self.missed = False
        # the index is a cache: an old schema is dropped, never migrated
        if self._db.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION:
            self._drop_titles()
            self._db.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        # The site a title came from got a column of its own after the table
        # had one per type and status. Added in place, never by a schema bump:
        # dropping the cache would re-read every title from the vault, and the
        # values are all in the documents this table already holds — filled
        # ONCE, as the column arrives: a pass over every row's document at each
        # launch would read the whole table from the share before serving.
        added = False
        if self._db.execute("SELECT 1 FROM sqlite_master WHERE name = 'titles'").fetchone():
            have = {r["name"] for r in self._db.execute("PRAGMA table_info(titles)")}
            if "source" not in have:
                self._db.execute("ALTER TABLE titles ADD COLUMN source TEXT NOT NULL DEFAULT ''")
                added = True
        self._db.executescript(_SCHEMA)
        # …and a table left by a half-applied schema (an interrupted upgrade, a
        # column added without bumping the version) is dropped too: a cache may
        # cost a rebuild, never a launch that cannot start.
        have = {r["name"] for r in self._db.execute("PRAGMA table_info(titles)")}
        if not set(self._COLUMNS) <= have:
            self._drop_titles()
            self._db.executescript(_SCHEMA)
        self._db.executescript(_JOURNAL)
        for table, columns in _SIDE_COLUMNS.items():  # another shape: a cache, made again
            have = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
            if have and have != columns:
                self._db.execute(f"DROP TABLE {table}")
        no_values = not self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'title_values_v1'").fetchone()
        self._db.executescript(_SIDE)
        if no_values:  # a card stands for its values too: both are made again
            with self._db:
                self._db.execute("DELETE FROM title_cards_v1")
        if added:
            self._fill_sources()
        # checked against the rows at the first read that needs them (`_side`),
        # never on the way to a launch: a first fill parses every document
        self._sided = False

    def _drop_titles(self) -> None:
        """The cache gone — and what is derived from it with it."""
        self._db.executescript("".join(f"DROP TABLE IF EXISTS {t};" for t in ("titles", *_SIDE_TABLES)))

    def _side(self) -> None:
        """Before a read of the cards or the values: they stand for every row."""
        if self._sided:
            return
        with self._lock:
            if not self._sided:
                self._heal_side()
                self._sided = True

    def _heal_side(self) -> None:
        """Cards and values for every row written without them, from the
        documents the rows hold — no title opened. Finding them is one join
        over an index of the stamps; at most opens it finds nothing. A row
        whose document cannot be read is left without (and said so), never
        holding up the others."""
        with self._lock, self._db:
            stale = [r["id"] for r in self._db.execute(
                "SELECT t.id AS id FROM titles t LEFT JOIN title_cards_v1 c ON c.id = t.id"
                " WHERE c.id IS NULL OR c.touched != t.touched OR c.media_at != t.media_at"
                " OR c.cover != t.cover")]
            for i in range(0, len(stale), 500):  # under SQLite's variable limit
                chunk = stale[i:i + 500]
                for r in self._db.execute(
                        f"SELECT id, doc, media, cover FROM titles WHERE id IN ({_marks(chunk)})", chunk).fetchall():
                    try:
                        self._put_side(r["id"], TitleDoc.model_validate_json(r["doc"]),
                                       json.loads(r["media"]), r["cover"])
                    except Exception:  # noqa: BLE001 — one unreadable row never holds up the rest
                        log.warning("no card for %s: its indexed document cannot be read", r["id"],
                                    exc_info=True)
            for table in _SIDE_TABLES:  # what another build removed
                self._db.execute(f"DELETE FROM {table} WHERE id NOT IN (SELECT id FROM titles)")
        if stale:
            log.info("cards and values made for %d titles", len(stale))

    def _put_side(self, title_id: str, doc: TitleDoc, sidecars: dict[str, dict], covers: str) -> None:
        """Inside a write: the title's card and values, as its row now stands."""
        row = self._db.execute("SELECT touched, media_at, cover FROM titles WHERE id = ?", (title_id,)).fetchone()
        held = self._db.execute("SELECT rev FROM title_cards_v1 WHERE id = ?", (title_id,)).fetchone()
        rev = (held["rev"] if held else 0) + 1
        video = any(isinstance(side, dict) and side.get("kind") == "video" for side in sidecars.values())
        self._db.execute(
            "INSERT OR REPLACE INTO title_cards_v1 (id, touched, media_at, cover, rev, video, card)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title_id, row["touched"], row["media_at"], row["cover"], rev, int(video),
             cards.card(title_id, doc, sidecars, covers, rev).model_dump_json()))
        self._db.execute("DELETE FROM title_values_v1 WHERE id = ?", (title_id,))
        self._db.executemany(
            "INSERT OR IGNORE INTO title_values_v1 (id, field, value) VALUES (?, ?, ?)",
            [(title_id, fid, v) for fid, vs in fields.stored_values(doc).items() for v in vs])

    def _fill_sources(self) -> None:
        """The new column, from the documents this table holds — read by
        SQLite, no title opened. (A row an older build writes afterwards
        carries no source until this build writes that title again.)"""
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT id, json_extract(doc, '$.meta.source.domain') AS d, json_extract(doc, '$.meta.source.url') AS u"
                " FROM titles WHERE source = '' AND (IFNULL(d, '') != '' OR IFNULL(u, '') != '')").fetchall()
            self._db.executemany("UPDATE titles SET source = ? WHERE id = ?",
                                 [(_site(r["d"] or "", r["u"] or ""), r["id"]) for r in rows])

    def close(self) -> None:
        with self._lock:
            self._db.close()

    _COLUMNS = ("id", "title", "people", "type", "status", "rating", "fav",
                "unread", "started", "finished", "touched", "doc", "media", "media_at",
                "cover", "source")

    @staticmethod
    def _row(title_id: str, doc: TitleDoc, touched: int = 0,
             media: dict[str, dict] | None = None, media_at: int = 0, covers: str = "") -> dict:
        m, u = doc.meta, doc.user
        states = [u.read.get(c.id, "unread") for c in doc.chapters]
        total = len(states)
        read_done = sum(1 for s in states if s == "read")
        return {
            # `people` is a denormalized search column: alt title + all names
            "id": title_id, "title": m.title,
            "people": " ".join([m.alt, *m.authors]),
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
            # The title's cover endpoint URLs, space-separated (a URL here never
            # holds one), each versioned by its file's mtime: composing them per
            # listing means one stat per title, on the network, every time.
            #
            # The COLUMN is still called `cover`, and that is deliberate. A
            # schema change drops this whole cache, and a library of a thousand
            # titles then pays a full re-read on the one launch after an update
            # — for a rename. The values need no conversion either: a title with
            # one cover stores exactly the string it always did, and nothing had
            # more than one until now.
            "cover": covers,
            # the site it came from — what the Sources list counts
            "source": _site(m.source.domain, m.source.url),
        }

    @property
    def _insert_sql(self) -> str:
        cols = ", ".join(self._COLUMNS)
        vals = ", ".join(f":{c}" for c in self._COLUMNS)
        return f"INSERT INTO titles ({cols}) VALUES ({vals})"

    @contextmanager
    def _writing(self, moves: bool = True):
        """One transaction into `titles`: the epoch moves (when a title may
        have), and a failure is kept."""
        with self._lock:
            self.epoch += moves
            try:
                with self._db:
                    yield
            except BaseException:
                self.missed = True
                raise

    def rebuild(self, docs: dict[str, TitleDoc], touched: dict[str, int] | None = None,
                media: dict[str, dict[str, dict]] | None = None,
                media_at: dict[str, int] | None = None,
                covers: dict[str, str] | None = None) -> None:
        with self._writing():
            self._db.execute("DELETE FROM titles")
            for table in _SIDE_TABLES:
                self._db.execute(f"DELETE FROM {table}")
            self._db.executemany(
                self._insert_sql,
                [self._row(tid, doc, (touched or {}).get(tid, 0), (media or {}).get(tid),
                           (media_at or {}).get(tid, 0), (covers or {}).get(tid, ""))
                 for tid, doc in docs.items()],
            )
            for tid, doc in docs.items():
                self._put_side(tid, doc, (media or {}).get(tid) or {}, (covers or {}).get(tid, ""))

    def _upsert_sql(self, touched: str = "MAX(excluded.touched, titles.touched)") -> str:
        return f"""{self._insert_sql}
                   ON CONFLICT(id) DO UPDATE SET
                     title=excluded.title, people=excluded.people, type=excluded.type,
                     status=excluded.status, rating=excluded.rating, fav=excluded.fav,
                     unread=excluded.unread, started=excluded.started, finished=excluded.finished,
                     touched={touched}, doc=excluded.doc,
                     media=excluded.media, media_at=excluded.media_at,
                     cover=excluded.cover, source=excluded.source"""

    def upsert(self, title_id: str, doc: TitleDoc, touched: int = 0,
               media: dict[str, dict] | None = None, media_at: int = 0, covers: str = "") -> None:
        with self._writing():
            self._db.execute(self._upsert_sql(),
                             self._row(title_id, doc, touched, media, media_at, covers))
            self._put_side(title_id, doc, media or {}, covers)

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
        # the stamp the scan SAW, exactly: kept higher than the file (MAX), a
        # document restored older would never match its row again, and every
        # verification would read it once more
        guarded = (f"{self._upsert_sql('excluded.touched')} WHERE titles.touched = :was_touched"
                   " AND titles.media_at = :was_media_at AND titles.cover = :was_covers")
        with self._writing():
            for tid, doc, touched, media, media_at, covers, was in rows:
                row = self._row(tid, doc, touched, media, media_at, covers)
                # all THREE stamps: a cover replaced from a local file changes
                # only this column — title.json and the chapter directory never move
                row["was_touched"], row["was_media_at"], row["was_covers"] = was
                if self._db.execute(guarded, row).rowcount:
                    self._put_side(tid, doc, media or {}, covers)

    # ---- the bulk metadata path (operations.py) ----

    _META_COLUMNS = ("title", "people", "type", "status", "rating", "fav", "unread",
                     "started", "finished", "touched", "doc", "source")

    def apply_meta(self, rows: list[tuple[str, TitleDoc, int, int]], run: int | None = None,
                   changes: list[tuple[str, str, object, object]] = ()) -> None:
        """Refresh the document side of many rows in ONE transaction, with the
        journal's rows for them. Each row is (id, doc, the stamp written, the
        stamp the document had when it was read).

        A metadata change moves neither chapters nor covers, so the media and
        cover columns are kept as they are — no chapter directory is read for
        this, and the card is made from what they hold. The rows are written after their titles' locks were released, so
        each is guarded by the stamp it was READ at: a row indexed since from a
        later write carries a later stamp and is left alone."""
        sets = ", ".join(f"{c} = :{c}" for c in self._META_COLUMNS)
        with self._writing(moves=bool(rows)):  # a journal note alone moves no title
            for tid, doc, touched, read_at in rows:
                row = self._row(tid, doc, touched)
                if self._db.execute(f"UPDATE titles SET {sets} WHERE id = :id AND touched <= :read_at",
                                    {**{c: row[c] for c in (*self._META_COLUMNS, "id")},
                                     "read_at": read_at}).rowcount:
                    held = self._db.execute("SELECT media, cover FROM titles WHERE id = ?", (tid,)).fetchone()
                    self._put_side(tid, doc, json.loads(held["media"]), held["cover"])
            if run is not None and changes:
                self._db.executemany(
                    "INSERT INTO journal_changes_v1 (run, title_id, field, before, after)"
                    " VALUES (?, ?, ?, ?, ?)",
                    [(run, tid, f, json.dumps(b, ensure_ascii=False), json.dumps(a, ensure_ascii=False))
                     for tid, f, b, a in changes])

    def types(self) -> dict[str, str]:
        """Every title's type — which shelf it sits on — off the indexed column,
        without parsing a single document."""
        with self._lock:
            return {r["id"]: r["type"] for r in self._db.execute("SELECT id, type FROM titles")}

    def episodes(self) -> dict[str, dict[str, dict]]:
        """Every episode's sidecar, by title and stem, off the carried media — so a
        pass over the episodes knows which files to open without a walk. Only
        the titles holding one are read (the cards say which)."""
        self._side()
        with self._lock:
            rows = self._db.execute(
                "SELECT t.id AS id, t.media AS media FROM title_cards_v1 c"
                " JOIN titles t ON t.id = c.id WHERE c.video = 1").fetchall()
        out: dict[str, dict[str, dict]] = {}
        for r in rows:
            sides = {stem: s for stem, s in json.loads(r["media"]).items() if s.get("kind") == "video"}
            if sides:
                out[r["id"]] = sides
        return out

    def docs_for(self, ids: list[str]) -> list[tuple[str, TitleDoc]]:
        """The indexed documents of these titles, in the order asked."""
        found: dict[str, TitleDoc] = {}
        with self._lock:
            for i in range(0, len(ids), 500):  # under SQLite's variable limit
                chunk = ids[i:i + 500]
                rows = self._db.execute(
                    f"SELECT id, doc FROM titles WHERE id IN ({','.join('?' * len(chunk))})",
                    chunk).fetchall()
                found.update({r["id"]: TitleDoc.model_validate_json(r["doc"]) for r in rows})
        return [(i, found[i]) for i in ids if i in found]

    def journal_open(self, at: str, summary: str, scope: dict, actions: list[dict]) -> int:
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO journal_runs_v1 (at, summary, scope, actions) VALUES (?, ?, ?, ?)",
                (at, summary, json.dumps(scope, ensure_ascii=False),
                 json.dumps(actions, ensure_ascii=False)))
            return int(cur.lastrowid)

    def journal_close(self, run: int, titles: int, seconds: float, stopped: bool) -> None:
        """Finish a run's record, and let the oldest runs go past the last
        JOURNAL_KEEP — with the changes they would have reverted."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE journal_runs_v1 SET titles = ?, seconds = ?, stopped = ? WHERE id = ?",
                (titles, round(seconds, 2), int(stopped), run))
            old = [r[0] for r in self._db.execute(
                "SELECT id FROM journal_runs_v1 ORDER BY id DESC LIMIT -1 OFFSET ?",
                (JOURNAL_KEEP,)).fetchall()]
            if old:
                marks = ",".join("?" * len(old))
                self._db.execute(f"DELETE FROM journal_changes_v1 WHERE run IN ({marks})", old)
                self._db.execute(f"DELETE FROM journal_runs_v1 WHERE id IN ({marks})", old)

    def journal_runs(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM journal_runs_v1 ORDER BY id DESC").fetchall()
        return [{"id": r["id"], "at": r["at"], "summary": r["summary"],
                 "scope": json.loads(r["scope"]), "actions": json.loads(r["actions"]),
                 "titles": r["titles"], "seconds": r["seconds"], "stopped": bool(r["stopped"]),
                 "revertedAt": r["reverted_at"]} for r in rows]

    def journal_changes(self, run: int) -> list[tuple[str, str, object, object]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT title_id, field, before, after FROM journal_changes_v1 WHERE run = ? ORDER BY rowid",
                (run,)).fetchall()
        return [(r["title_id"], r["field"], json.loads(r["before"]), json.loads(r["after"]))
                for r in rows]

    def rule_fired(self, rules: list[str], at: str) -> None:
        """Count a change each of these rules made — so a rule nothing triggers
        any more is visible."""
        if not rules:
            return
        with self._lock, self._db:
            self._db.executemany(
                "INSERT INTO rule_stats_v1 (rule, fired, last) VALUES (?, 1, ?)"
                " ON CONFLICT(rule) DO UPDATE SET fired = fired + 1, last = excluded.last",
                [(r, at) for r in rules])

    def rule_stats_keep(self, rules: list[str]) -> None:
        """Forget the counts of rules that are gone."""
        with self._lock, self._db:
            if rules:
                marks = ",".join("?" * len(rules))
                self._db.execute(f"DELETE FROM rule_stats_v1 WHERE rule NOT IN ({marks})", rules)
            else:
                self._db.execute("DELETE FROM rule_stats_v1")

    def rule_stats(self) -> dict[str, dict]:
        with self._lock:
            rows = self._db.execute("SELECT rule, fired, last FROM rule_stats_v1").fetchall()
        return {r["rule"]: {"fired": r["fired"], "last": r["last"]} for r in rows}

    def journal_reverted(self, run: int, at: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE journal_runs_v1 SET reverted_at = ? WHERE id = ?", (at, run))

    def remove(self, title_id: str) -> None:
        with self._writing():
            self._db.execute("DELETE FROM titles WHERE id = ?", (title_id,))
            for table in _SIDE_TABLES:
                self._db.execute(f"DELETE FROM {table} WHERE id = ?", (title_id,))

    def revs(self, ids: list[str]) -> dict[str, int]:
        """Where each of these titles' cards is now — what a window holding
        them whole checks them against; a title that is gone is not named."""
        self._side()
        with self._lock:
            return {r["id"]: r["rev"] for r in self._db.execute(
                "SELECT id, rev FROM title_cards_v1 WHERE id IN (SELECT value FROM json_each(?))",
                (json.dumps(ids),))}

    def rev_of(self, title_id: str) -> int:
        self._side()
        with self._lock:
            row = self._db.execute("SELECT rev FROM title_cards_v1 WHERE id = ?", (title_id,)).fetchone()
        return row["rev"] if row else 0

    def ids(self) -> set[str]:
        with self._lock:
            return {r["id"] for r in self._db.execute("SELECT id FROM titles")}

    def stamps(self) -> dict[str, tuple[int, int, str]]:
        """Every indexed title's id with the (document mtime, chapter-dir mtime,
        cover URLs) it was indexed at. Comparing these against one vault scan is
        what lets a launch reload only what changed — a cover URL carries that
        file's own mtime, so a cover replaced outside the app is noticed too."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, touched, media_at, cover FROM titles").fetchall()
        return {r["id"]: (r["touched"], r["media_at"], r["cover"]) for r in rows}

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) AS n FROM titles").fetchone()["n"]

    def media_of(self, title_id: str) -> tuple[dict[str, dict], int, str] | None:
        """A row's chapter sidecars, the stamp they were read at, and its cover URLs."""
        with self._lock:
            row = self._db.execute("SELECT media, media_at, cover FROM titles WHERE id = ?",
                                   (title_id,)).fetchone()
        return (json.loads(row["media"]), row["media_at"], row["cover"]) if row else None

    def get(self, title_id: str) -> tuple[TitleDoc, dict[str, dict], str] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT doc, media, cover FROM titles WHERE id = ?", (title_id,)).fetchone()
        if not row:
            return None
        return TitleDoc.model_validate_json(row["doc"]), json.loads(row["media"]), row["cover"]

    def ids_by_title(self) -> dict[str, list[str]]:
        """Title ids keyed by their title as a person would match it — trimmed,
        case folded. Several titles may share one."""
        with self._lock:
            rows = self._db.execute("SELECT id, title FROM titles").fetchall()
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault((r["title"] or "").strip().casefold(), []).append(r["id"])
        return out

    def source_counts(self) -> dict[str, int]:
        """How many titles each site holds, off the indexed column — the Sources
        list asks on every change to it."""
        with self._lock:
            rows = self._db.execute(
                "SELECT source, COUNT(*) AS n FROM titles WHERE source != '' GROUP BY source").fetchall()
        return {r["source"]: r["n"] for r in rows}

    def docs(self, sort: str = "updated", **selection) -> list[tuple[str, TitleDoc]]:
        """The documents of a selection (every one, with none), in its order —
        never the media: what reads or edits values needs only the documents,
        and the media is the row's heaviest column to read off a network disk.
        Anything that has to SHOW a title goes through `query()` or a card."""
        self._side()
        where, params = self._where(**selection)
        sql = "SELECT id, doc FROM titles"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + _SORTS.get(sort, _SORTS["updated"])
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [(r["id"], TitleDoc.model_validate_json(r["doc"])) for r in rows]

    def query(
        self,
        *,
        search: str | None = None,
        fav: bool | None = None,
        min_rating: int | None = None,
        progress: str | None = None,  # unread | reading | completed (reading progress, NOT manga status)
        include: dict[str, tuple[str, ...]] | None = None,
        exclude: dict[str, tuple[str, ...]] | None = None,
        ids: list[str] | None = None,
        sort: str = "updated",
    ) -> list[tuple[str, TitleDoc, dict[str, dict], str]]:
        """Field filters arrive keyed by field id (`fields.py`), so a field this
        module has never heard of filters exactly like one it has."""
        self._side()
        where, params = self._where(search, fav, min_rating, progress, include, exclude, ids)
        sql = "SELECT id, doc, media, cover FROM titles"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + _SORTS.get(sort, _SORTS["updated"])
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [(r["id"], TitleDoc.model_validate_json(r["doc"]), json.loads(r["media"]), r["cover"])
                for r in rows]

    def values_of(self, field: str) -> list[tuple[str, str]]:
        """Every (title, value) of one field — what a value page matches a
        spelling against, with no document read."""
        self._side()
        with self._lock:
            return [(r["id"], r["value"]) for r in self._db.execute(
                "SELECT id, value FROM title_values_v1 WHERE field = ?", (field,))]

    def grouped(self, field: str, **selection) -> list[tuple[str, str, str]]:
        """(value, title, its card) for every value of one field the titles of
        a selection hold, in the library's order — a browse, without a document."""
        self._side()
        where, params = self._where(**selection)
        sql = ("SELECT v.value AS value, v.id AS id, c.card AS card FROM title_values_v1 v"
               " JOIN titles ON titles.id = v.id JOIN title_cards_v1 c ON c.id = v.id WHERE v.field = ?"
               + "".join(f" AND {w}" for w in where) + " ORDER BY " + _SORTS["updated"])
        with self._lock:
            return [(r["value"], r["id"], r["card"]) for r in self._db.execute(sql, [field, *params])]

    def cards(self, sort: str = "updated", **selection) -> list[str]:
        """The cards of a selection, in its order, as the JSON each was stored as."""
        self._side()
        where, params = self._where(**selection)
        sql = "SELECT c.card AS card FROM titles JOIN title_cards_v1 c ON c.id = titles.id"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY " + _SORTS.get(sort, _SORTS["updated"])
        with self._lock:
            return [r["card"] for r in self._db.execute(sql, params)]

    @staticmethod
    def _where(search: str | None = None, fav: bool | None = None, min_rating: int | None = None,
               progress: str | None = None, include: dict[str, tuple[str, ...]] | None = None,
               exclude: dict[str, tuple[str, ...]] | None = None,
               ids: list[str] | None = None) -> tuple[list[str], list[object]]:
        """A selection as SQL over `titles`. A single-valued field has a column:
        its includes are alternatives. A multi-valued one is narrowed: a title
        holds EVERY included value. An excluded value removes a title that
        holds it, either way. `ids` keeps only those titles."""
        include, exclude = include or {}, exclude or {}
        where: list[str] = []
        params: list[object] = []
        if ids is not None:  # one parameter, however many: no variable limit
            where.append("titles.id IN (SELECT value FROM json_each(?))")
            params.append(json.dumps(ids))
        if search:
            where.append("(titles.title LIKE ? OR titles.people LIKE ?)")
            like = f"%{search}%"
            params += [like, like]
        for f in fields.filterable():
            inc = tuple(dict.fromkeys(include.get(f.id, ())))
            exc = tuple(dict.fromkeys(exclude.get(f.id, ())))
            if f.column:
                for values, op in ((inc, "IN"), (exc, "NOT IN")):
                    if values:
                        where.append(f"titles.{f.column} {op} ({_marks(values)})")
                        params += values
                continue
            # sets, not per-row subqueries: each is read once per statement
            if inc:
                where.append("titles.id IN (SELECT id FROM title_values_v1 WHERE field = ?"
                             f" AND value IN ({_marks(inc)}) GROUP BY id HAVING COUNT(*) = ?)")
                params += [f.id, *inc, len(inc)]
            if exc:
                where.append("titles.id NOT IN (SELECT id FROM title_values_v1 WHERE field = ?"
                             f" AND value IN ({_marks(exc)}))")
                params += [f.id, *exc]
        if fav:
            where.append("titles.fav = 1")
        if min_rating:
            where.append("titles.rating >= ?")
            params.append(min_rating)
        if progress == "unread":
            where.append("titles.started = 0")
        elif progress == "reading":
            where.append("titles.started = 1 AND titles.finished = 0")
        elif progress == "completed":
            where.append("titles.finished = 1")
        return where, params

    def facet_counts(self, sel: dict) -> dict[str, list[dict]]:
        """Linked facet counts for the current selection: each facet is counted
        with every OTHER facet's filters applied. Single-valued facets (type,
        status) ignore their own selection so the alternatives stay visible;
        multi-valued ones keep their own includes (co-occurrence counts)."""
        self._side()
        # Every facet asks its own question, but the facets whose question is
        # the same — the whole unfiltered library, at every launch — are
        # counted by ONE statement.
        asked: dict[tuple, list[Field]] = {}
        for f in fields.facets():
            exclude = {k: v for k, v in (sel.get("exclude") or {}).items() if k != f.id}
            include = sel.get("include") or {}
            if f.column:  # single-valued: its own include hides the alternatives
                include = {k: v for k, v in include.items() if k != f.id}
            where, params = self._where(sel.get("search"), sel.get("fav"), sel.get("min_rating"),
                                        sel.get("progress"), include, exclude)
            asked.setdefault((tuple(where), tuple(params)), []).append(f)
        counts: dict[str, Counter] = {f.id: Counter() for f in fields.facets()}
        with self._lock:
            for (where, params), group in asked.items():
                ids = [f.id for f in group]
                sql = ("SELECT v.field AS f, v.value AS v, COUNT(*) AS n FROM title_values_v1 v"
                       f" JOIN titles ON titles.id = v.id WHERE v.field IN ({_marks(ids)})"
                       + "".join(f" AND {w}" for w in where) + " GROUP BY v.field, v.value")
                for r in self._db.execute(sql, [*ids, *params]):
                    counts[r["f"]][r["v"]] = r["n"]
        return {fid: _counted(c) for fid, c in counts.items()}
