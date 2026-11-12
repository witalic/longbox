"""Every value a filter or a facet asks about is a row of `title_values_v1`,
written with its title: a selection is SQL, and counting a facet parses no
document. An index from before that table gains it in place, once."""
from __future__ import annotations

import sqlite3

from app.library import fields
from app.library.index import LibraryIndex
from app.library.models import CustomFieldDef, TitleDoc, TitleMeta


def _doc(title: str = "T", **meta) -> TitleDoc:
    return TitleDoc(meta=TitleMeta(title=title, **meta))


def _counts(idx: LibraryIndex, field: str, sel: dict | None = None) -> dict[str, int]:
    return {r["v"]: r["n"] for r in idx.facet_counts(sel or {})[field]}


def test_values_follow_every_write(tmp_path):
    idx = LibraryIndex(tmp_path / "index.db")
    idx.upsert("a", _doc(genres=["Action", "Drama"]))
    idx.upsert("b", _doc(genres=["Action"]))
    assert _counts(idx, "genres") == {"Action": 2, "Drama": 1}
    idx.upsert("b", _doc(genres=["Comedy"]))  # replaced, not added to
    assert _counts(idx, "genres") == {"Action": 1, "Drama": 1, "Comedy": 1}
    idx.apply_meta([("a", _doc(genres=["Drama"]), 5, 0)])
    assert _counts(idx, "genres") == {"Drama": 1, "Comedy": 1}
    idx.remove("b")
    assert _counts(idx, "genres") == {"Drama": 1}
    idx.close()


def test_a_write_the_guard_refuses_leaves_the_newer_values(tmp_path):
    """A verification that read a title before a commit may not put its values
    back over the commit's — exactly as it may not put back the row."""
    idx = LibraryIndex(tmp_path / "index.db")
    idx.upsert("a", _doc(genres=["Newer"]), touched=20)
    idx.upsert_many([("a", _doc(genres=["Stale"]), 10, {}, 0, "", (10, 0, ""))])
    assert _counts(idx, "genres") == {"Newer": 1}
    idx.apply_meta([("a", _doc(genres=["Stale"]), 30, 10)])  # read before the newer write
    assert _counts(idx, "genres") == {"Newer": 1}
    idx.close()


def test_a_field_defined_after_its_values_were_written_needs_no_pass(tmp_path):
    idx = LibraryIndex(tmp_path / "index.db")
    idx.upsert("a", _doc(custom={"shelf": ["boxed", "read"]}))
    idx.upsert("b", _doc(custom={"shelf": "boxed"}))
    try:
        fields.set_custom([CustomFieldDef(id="shelf", label="Shelf", type="list", facet=True)])
        assert _counts(idx, "shelf") == {"boxed": 2, "read": 1}
        got = idx.query(include={"shelf": ("boxed", "read")})
        assert [tid for tid, *_ in got] == ["a"]  # a list narrows: every value held
    finally:
        fields.set_custom([])
    idx.close()


def test_an_index_from_before_the_table_is_filled_once(tmp_path, monkeypatch):
    db = tmp_path / "index.db"
    idx = LibraryIndex(db)
    idx.upsert("a", _doc(tags=["x"]))
    idx.upsert("b", _doc(tags=["x", "y"]))
    idx.close()
    con = sqlite3.connect(db)
    con.execute("DROP TABLE title_values_v1")  # as the build before this one left it
    con.commit()
    con.close()

    idx = LibraryIndex(db)
    assert {r["id"] for r in idx._db.execute("SELECT id FROM titles")} == {"a", "b"}  # nothing dropped
    assert _counts(idx, "tags") == {"x": 2, "y": 1}
    idx.close()

    made: list[str] = []
    real = LibraryIndex._put_side
    monkeypatch.setattr(LibraryIndex, "_put_side", lambda self, tid, *a: (made.append(tid), real(self, tid, *a)))
    LibraryIndex(db).close()
    assert made == []  # once, not at every open


def test_a_peek_a_browse_and_a_spelling_read_no_document(tmp_path, monkeypatch):
    """A hovered chip, a browse axis, a value's spelling: off the stored values
    and cards. A value page reads only the documents of the titles holding it."""
    from app.library import pages as P
    from app.library.models import DraftIn
    from app.library.service import Library

    lib = Library(tmp_path / "v")
    for title, authors in (("A", ["Miura"]), ("B", ["Miura "]), ("C", ["Other"])):
        lib.create(DraftIn(meta=TitleMeta(title=title, authors=authors, tags=["dark"])))
    parsed: list[int] = []
    real = TitleDoc.model_validate_json
    monkeypatch.setattr(TitleDoc, "model_validate_json",
                        classmethod(lambda cls, *a, **kw: (parsed.append(1), real(*a, **kw))[1]))

    peek = P.peek(lib, "authors", "MIURA")
    assert peek.titles == 2 and peek.value.strip() == "Miura"  # a value page folds case
    groups = {g.value: (g.titles, g.chapters, g.topTags) for g in lib.browse("authors")}
    assert groups == {"Miura": (2, 0, ["dark"]), "Other": (1, 0, ["dark"])}
    assert P.held_keys(lib, "authors") == {"miura", "other"}
    assert parsed == []

    page = P.view(lib, "authors", "miura")
    assert sorted(w.title for w in page.works) == ["A", "B"]
    assert len(parsed) == 2  # the two holders, not the library
    lib.close()


def test_a_selection_means_what_it_meant_before_the_values_table(tmp_path):
    idx = LibraryIndex(tmp_path / "index.db")
    idx.upsert("a", _doc(type="manga", genres=["Action", "Drama"]))
    idx.upsert("b", _doc(type="manhwa", genres=["Action"]))
    idx.upsert("c", _doc(type="manga", genres=["Drama"]))
    ids = lambda **sel: sorted(t for t, *_ in idx.query(**sel))
    assert ids(include={"genres": ("Action", "Action")}) == ["a", "b"]  # a repeated value is one
    assert ids(include={"type": ("manga", "manhwa")}) == ["a", "b", "c"]  # a column: alternatives
    sel = {"include": {"genres": ("Action",), "type": ("manga",)}, "exclude": {"genres": ("Comedy",)}}
    counts = idx.facet_counts(sel)
    # a list keeps its own include (what goes WITH Action) …
    assert {r["v"]: r["n"] for r in counts["genres"]} == {"Action": 1, "Drama": 1}
    # … a column drops it, so its alternatives stay visible
    assert {r["v"]: r["n"] for r in counts["type"]} == {"manga": 1, "manhwa": 1}
    # and a facet is counted without its own exclusion
    idx.upsert("d", _doc(type="manga", genres=["Action", "Comedy"]))
    assert {r["v"]: r["n"] for r in idx.facet_counts(sel)["genres"]}["Comedy"] == 1
    idx.close()


def test_a_journal_note_alone_moves_no_title(tmp_path):
    idx = LibraryIndex(tmp_path / "index.db")
    run = idx.journal_open("now", "note", {}, [])
    before = idx.epoch
    idx.apply_meta([], run, [("a", "genres", [], ["x"])])
    assert idx.epoch == before
    idx.close()
