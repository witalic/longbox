"""A listing sends cards — what a list shows of a title, never its chapters —
kept ready by the index at every write, so listing a library parses no
document. A title is opened whole, and whatever a window receives of it is a
card too."""
from __future__ import annotations

import json
import sqlite3
import zipfile

from fastapi.testclient import TestClient

from app.library.index import LibraryIndex
from app.library.models import ChapterRow, DraftIn, TitleDoc, TitleMeta, UserPatch
from app.library.service import Library
from app.main import create_app


def _zip(path, pages=2):
    with zipfile.ZipFile(path, "w") as z:
        for i in range(pages):
            z.writestr(f"{i + 1:03}.jpg", b"\xff\xd8\xff" + bytes([i]))
    return path


def _cards(lib: Library, **sel) -> dict[str, dict]:
    return {c["id"]: c for c in json.loads(lib.cards(**sel))}


def test_the_card_follows_every_write(tmp_path):
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk"),
                             chapters=[ChapterRow(id="c1", num="1", lang="en", group="dex"),
                                       ChapterRow(id="c2", num="2", lang="uk")]))
    card = _cards(lib)[out.id]
    assert (card["title"], card["chapterCount"], card["pageCount"], card["unread"]) == ("Berserk", 2, 0, 2)
    assert (card["chapterLangs"], card["chapterGroups"]) == (["en", "uk"], ["dex"])
    assert "chapters" not in card and "provenance" not in card

    lib.attach_chapter_media(out.id, num="1", lang="en", group="dex", chapter_id="c1",
                             src=_zip(tmp_path / "c1.zip", 3), sidecar={})
    lib.patch_user(out.id, UserPatch(fav=True, read={"c1": "read"}))
    card = _cards(lib)[out.id]
    assert (card["pageCount"], card["fav"], card["unread"]) == (3, True, 1)

    lib.delete(out.id)
    assert _cards(lib) == {}
    lib.close()


def test_a_whole_title_is_a_card_too(tmp_path):
    """Whatever a window receives of a title refreshes the card it lists."""
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk", genres=["Dark"]),
                             chapters=[ChapterRow(id="c1", num="1")]))
    whole = lib.get(out.id).model_dump(mode="json")
    card = _cards(lib)[out.id]
    assert {k: whole[k] for k in card} == card
    lib.close()


def test_a_coverless_tile_names_what_it_may_wear(tmp_path):
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Pages"),
                             chapters=[ChapterRow(id=f"c{i}", num=str(i)) for i in range(1, 9)]))
    for i in range(1, 9):
        lib.attach_chapter_media(out.id, num=str(i), lang="", group="", chapter_id=f"c{i}",
                                 src=_zip(tmp_path / f"c{i}.zip"), sidecar={})
    card = _cards(lib)[out.id]
    assert [c["id"] for c in card["standIns"]] == ["c1", "c2", "c3", "c4", "c5", "c6"]
    lib.set_cover(out.id, b"\xff\xd8\xffcover", "jpg")
    assert _cards(lib)[out.id]["standIns"] == []  # a cover of its own needs none
    lib.close()


def test_the_api_lists_cards_and_opens_titles_whole(tmp_path):
    lib = Library(tmp_path / "v")
    a = lib.create(DraftIn(meta=TitleMeta(title="A", type="manga"), chapters=[ChapterRow(id="c1", num="1")]))
    b = lib.create(DraftIn(meta=TitleMeta(title="B", type="manhwa")))
    with TestClient(create_app(lib)) as c:
        listed = c.get("/api/library", params={"f": "type:manga", "sort": "title"}).json()
        assert [t["title"] for t in listed] == ["A"] and "chapters" not in listed[0]
        whole = c.get("/api/library/titles", params={"ids": [b.id, "gone", a.id]}).json()
        assert [t["id"] for t in whole] == [b.id, a.id]
        assert [ch["id"] for ch in whole[1]["chapters"]] == ["c1"]
    lib.close()


def test_a_row_written_without_a_card_is_given_one_at_the_next_open(tmp_path):
    """An older build keeps no cards: what it wrote — a new title, a changed
    one — is found by the row's stamp, not by reading every document."""
    db = tmp_path / "index.db"
    idx = LibraryIndex(db)
    idx.upsert("kept", TitleDoc(meta=TitleMeta(title="Kept")), touched=1)
    idx.upsert("changed", TitleDoc(meta=TitleMeta(title="Before")), touched=1)
    idx.close()
    con = sqlite3.connect(db)  # as an older build writes: the row only
    changed = TitleDoc(meta=TitleMeta(title="After")).model_dump_json(by_alias=True)
    con.execute("UPDATE titles SET doc = ?, title = 'After', touched = 2 WHERE id = 'changed'", (changed,))
    con.execute("INSERT INTO titles (id, title, people, type, status, rating, fav, unread, started,"
                " finished, touched, doc) VALUES ('new', 'New', '', '', '', 0, 0, 0, 0, 0, 3, ?)",
                (TitleDoc(meta=TitleMeta(title="New")).model_dump_json(by_alias=True),))
    con.commit()
    con.close()

    idx = LibraryIndex(db)
    titles = {json.loads(c)["id"]: json.loads(c)["title"] for c in idx.cards(sort="title")}
    assert titles == {"changed": "After", "kept": "Kept", "new": "New"}
    idx.close()


def test_an_index_from_before_the_cards_is_given_them_once(tmp_path, monkeypatch):
    db = tmp_path / "index.db"
    idx = LibraryIndex(db)
    idx.upsert("a", TitleDoc(meta=TitleMeta(title="A")))
    idx.close()
    con = sqlite3.connect(db)
    con.execute("DROP TABLE title_cards_v1")
    con.commit()
    con.close()
    idx = LibraryIndex(db)
    assert [json.loads(c)["title"] for c in idx.cards()] == ["A"]
    idx.close()
    made: list[str] = []
    real = LibraryIndex._put_side
    monkeypatch.setattr(LibraryIndex, "_put_side", lambda self, tid, *a: (made.append(tid), real(self, tid, *a)))
    LibraryIndex(db).close()
    assert made == []


def test_what_another_build_changed_besides_the_document_is_seen(tmp_path):
    """An older build replacing a cover or adding pages may leave title.json
    — and so `touched` — as it was: the cover and media stamps tell."""
    db = tmp_path / "index.db"
    idx = LibraryIndex(db)
    idx.upsert("a", TitleDoc(meta=TitleMeta(title="A")), touched=1, covers="/api/titles/a/cover?v=1.1.jpg")
    idx.upsert("b", TitleDoc(meta=TitleMeta(title="B"), chapters=[ChapterRow(id="c1", num="1")]), touched=1)
    idx.close()
    con = sqlite3.connect(db)
    con.execute("UPDATE titles SET cover = '/api/titles/a/cover?v=2.2.jpg' WHERE id = 'a'")
    con.execute("""UPDATE titles SET media = '{"c1": {"pages": 7}}', media_at = 5 WHERE id = 'b'""")
    con.commit()
    con.close()
    idx = LibraryIndex(db)
    got = {c["id"]: c for c in map(json.loads, idx.cards())}
    assert got["a"]["cover"] == "/api/titles/a/cover?v=2.2.jpg"
    assert got["b"]["pageCount"] == 7
    idx.close()


def test_a_row_that_cannot_be_read_holds_up_nothing(tmp_path):
    db = tmp_path / "index.db"
    idx = LibraryIndex(db)
    idx.upsert("good", TitleDoc(meta=TitleMeta(title="Good")))
    idx.upsert("bad", TitleDoc(meta=TitleMeta(title="Bad")))
    idx.close()
    con = sqlite3.connect(db)
    con.execute("DROP TABLE title_cards_v1")
    con.execute("UPDATE titles SET doc = '{not json' WHERE id = 'bad'")
    con.commit()
    con.close()
    idx = LibraryIndex(db)  # opens, and lists what it can
    assert [json.loads(c)["title"] for c in idx.cards()] == ["Good"]
    idx.close()


def test_a_side_table_of_another_shape_is_made_again(tmp_path):
    db = tmp_path / "index.db"
    idx = LibraryIndex(db)
    idx.upsert("a", TitleDoc(meta=TitleMeta(title="A")))
    idx.close()
    con = sqlite3.connect(db)  # as a build that kept fewer columns left it
    con.execute("DROP TABLE title_cards_v1")
    con.execute("CREATE TABLE title_cards_v1 (id TEXT PRIMARY KEY, touched INTEGER, card TEXT)")
    con.commit()
    con.close()
    idx = LibraryIndex(db)
    assert [json.loads(c)["title"] for c in idx.cards()] == ["A"]
    idx.close()


def test_the_rev_tells_a_window_which_whole_titles_moved(tmp_path):
    lib = Library(tmp_path / "v")
    a = lib.create(DraftIn(meta=TitleMeta(title="A")))
    b = lib.create(DraftIn(meta=TitleMeta(title="B")))
    held = lib.get(a.id).rev
    assert held == _cards(lib)[a.id]["rev"]
    lib.patch_user(a.id, UserPatch(fav=True))
    assert lib.get(a.id).rev == _cards(lib)[a.id]["rev"] != held
    with TestClient(create_app(lib)) as c:
        lib.delete(b.id)
        revs = c.post("/api/library/revs", json={"ids": [a.id, b.id]}).json()
        assert revs == {a.id: lib.get(a.id).rev}  # a gone title is not named
    lib.close()


def test_the_episode_pass_reads_only_titles_that_hold_one(tmp_path):
    """Run at every launch (the playable-copy queue), it must not read every
    row's media off a network disk to find the few episodes."""
    db = tmp_path / "index.db"
    idx = LibraryIndex(db)
    idx.upsert("pages", TitleDoc(meta=TitleMeta(title="P"), chapters=[ChapterRow(id="c1", num="1")]),
               media={"c1": {"pages": 3}})
    idx.upsert("show", TitleDoc(meta=TitleMeta(title="S"), chapters=[ChapterRow(id="e1", num="1")]),
               media={"e1": {"kind": "video", "duration": 60}})
    assert idx.episodes() == {"show": {"e1": {"kind": "video", "duration": 60}}}
    plan = " ".join(r[3] for r in idx._db.execute(
        "EXPLAIN QUERY PLAN SELECT t.id, t.media FROM title_cards_v1 c JOIN titles t ON t.id = c.id"
        " WHERE c.video = 1"))
    assert "title_cards_v1_video" in plan  # found by the flag, not by reading every row's media
    idx.close()
    con = sqlite3.connect(db)  # an older build adds an episode, and keeps no card
    con.execute("""UPDATE titles SET media = '{"c1": {"kind": "video"}}', media_at = 9 WHERE id = 'pages'""")
    con.commit()
    con.close()
    idx = LibraryIndex(db)
    assert set(idx.episodes()) == {"pages", "show"}
    idx.close()
