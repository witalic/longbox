"""The site each title came from is an indexed column — the Sources list counts
it with one GROUP BY — and an index from before that column gains it in place,
from the documents it already holds: never by dropping the cache, which would
re-read every title from the vault."""
from __future__ import annotations

import sqlite3

from app.library.index import LibraryIndex
from app.library.models import SourceRef, TitleDoc, TitleMeta


def _doc(domain: str = "", url: str = "") -> TitleDoc:
    return TitleDoc(meta=TitleMeta(title="T", source=SourceRef(domain=domain, url=url)))


def test_sites_are_counted_off_the_column(tmp_path):
    idx = LibraryIndex(tmp_path / "index.db")
    idx.upsert("a", _doc("mangadex.org"))
    idx.upsert("b", _doc("mangadex.org"))
    idx.upsert("c", _doc("", "https://www.webtoons.com/en/x"))  # no domain recorded: the link's host
    idx.upsert("d", _doc())                                    # no source at all
    assert idx.source_counts() == {"mangadex.org": 2, "www.webtoons.com": 1}
    idx.upsert("b", _doc("pixiv.net"))                         # a title moved to another site
    assert idx.source_counts() == {"mangadex.org": 1, "pixiv.net": 1, "www.webtoons.com": 1}
    idx.close()


def test_an_index_from_before_the_column_keeps_its_rows(tmp_path):
    db = tmp_path / "index.db"
    idx = LibraryIndex(db)
    idx.upsert("a", _doc("mangadex.org"))
    idx.upsert("b", _doc("", "https://x.com/someone"))
    idx.close()
    # the table as the build before this one left it: no `source` column
    con = sqlite3.connect(db)
    con.execute("DROP INDEX titles_source")
    con.execute("ALTER TABLE titles DROP COLUMN source")
    con.commit()
    con.close()

    idx = LibraryIndex(db)
    assert {r["id"] for r in idx._db.execute("SELECT id FROM titles")} == {"a", "b"}  # nothing dropped
    assert idx.source_counts() == {"mangadex.org": 1, "x.com": 1}
    idx.close()


def test_the_column_is_filled_once_not_at_every_open(tmp_path, monkeypatch):
    """Filling it reads every row's document; done at each launch, that is the
    whole table read from the share before the sidecar serves."""
    db = tmp_path / "index.db"
    LibraryIndex(db).close()
    calls = []
    monkeypatch.setattr(LibraryIndex, "_fill_sources", lambda self: calls.append(1))
    LibraryIndex(db).close()
    assert calls == []
