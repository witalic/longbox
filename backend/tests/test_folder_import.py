"""Importing a folder the owner already has (design/folder-import.md): what each
level of a tree means, what the plan shows, and what a run stores — offline,
with trees built in tmp_path."""
from __future__ import annotations

import os
import stat
import threading
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.library import folder_import as fi
from app.library.models import ChapterRow, DraftIn, FieldProvenance, TitleMeta
from app.library.rules import Rule
from app.library.service import Library
from app.main import create_app
from app.settings import get_settings

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _clean_settings(tmp_path, monkeypatch):
    from app import config_store
    monkeypatch.setattr(config_store, "config_dir", lambda: tmp_path / "cfg")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def lib(tmp_path):
    lib = Library(tmp_path / "lib")
    yield lib
    lib.close()


def mp4(path: Path, payload: bytes = b"\x00" * 4096) -> Path:
    """A file whose header says mp4 the way a real one does: `ftyp` at byte 4."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + payload)
    return path


def image(path: Path, data: bytes = JPEG) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def archive(path: Path, pages: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for i in range(pages):
            z.writestr(f"{i + 1:02d}.jpg", JPEG)
    return path


def creator_tree(src: Path) -> Path:
    """A creator's folder, batches inside."""
    who = src / "vidhub @kumo Kumo Works"
    mp4(who / "2025.1" / "桜の舞(春).mp4", b"\x01" * 100)
    mp4(who / "2025.1" / "月光 - Moonlight.mp4", b"\x02" * 200)
    mp4(who / "2025.2" / "step ロングver.mp4", b"\x03" * 300)
    mp4(who / "2025.2" / "step.mp4", b"\x04" * 400)
    image(who / "2025.2" / "thumb.jpg")
    return src


def by_creator() -> fi.Profile:
    return fi.Profile(
        name="Creator video folders",
        levels=[fi.Level(role="collection"),
                fi.Level(role="creator", pattern="{site} @{handle} {creator}"),
                fi.Level(role="work")],
        workTitle="{creator} {folder}", creatorTo=["authors"],
        meta={"type": "mmd", "tags": ["{site}", "mmd"]})


def go(lib, src, profile, overrides=None, **kw):
    s = fi.scan(src)
    return fi.run(lib, s, profile, overrides, **kw)


def titles(lib) -> dict:
    return {t.title: t for t in lib.query()}


# ---- reading a tree ----------------------------------------------------------

def test_a_creator_folder_of_batches_becomes_one_work_per_batch(lib, tmp_path):
    src = creator_tree(tmp_path / "src")
    pl = fi.plan(lib, fi.scan(src), by_creator())

    assert [w.title for w in pl.works] == ["Kumo Works 2025.1", "Kumo Works 2025.2"]
    first = pl.works[0]
    assert first.meta == {"type": "mmd", "tags": ["vidhub", "mmd"], "authors": ["Kumo Works"]}
    # natural order: `step` before `step ロングver`
    assert [(e.num, e.title) for e in pl.works[1].entries] == [
        ("1", "step"), ("2", "step ロングver")]
    # a thumbnail beside the episodes is not an entry of its own
    assert any(i.path.endswith("thumb.jpg") for i in pl.ignored)
    assert pl.entries == 4


def test_the_run_stores_what_the_plan_showed_and_leaves_the_originals(lib, tmp_path):
    src = creator_tree(tmp_path / "src")
    report = go(lib, src, by_creator())

    assert (report.imported, report.failed) == (4, [])
    t = titles(lib)["Kumo Works 2025.2"]
    assert (t.type, t.authors, t.tags) == ("mmd", ["Kumo Works"], ["vidhub", "mmd"])
    assert t.provenance["authors"].origin == "auto"  # a capture may still refine it
    assert [(c.num, c.title, c.kind, c.dl, c.origName) for c in t.chapters] == [
        ("1", "step", "video", True, "step.mp4"),
        ("2", "step ロングver", "video", True, "step ロングver.mp4")]
    assert (src / "vidhub @kumo Kumo Works" / "2025.2" / "step.mp4").is_file()  # a copy


def test_the_same_tree_read_as_one_work_with_parts(lib, tmp_path):
    """Nested folders are not special: they are what the roles make them."""
    src = creator_tree(tmp_path / "src")
    p = by_creator().model_copy(update={
        "levels": [fi.Level(role="collection"), fi.Level(role="work", pattern="{site} @{handle} {creator}")],
        "workTitle": "{creator}", "creatorTo": []})
    go(lib, src, p)

    t = titles(lib)["Kumo Works"]
    assert [(c.num, c.section) for c in t.chapters] == [
        ("1", "2025.1"), ("2", "2025.1"), ("3", "2025.2"), ("4", "2025.2")]


def test_a_creator_shared_by_every_work_is_the_folder_picked(lib, tmp_path):
    src = creator_tree(tmp_path / "src") / "vidhub @kumo Kumo Works"
    p = fi.Profile(levels=[fi.Level(role="creator", pattern="{site} @{handle} {creator}"),
                           fi.Level(role="work")],
                   workTitle="{creator} {folder}", creatorTo=["artists"])
    pl = fi.plan(lib, fi.scan(src), p)
    assert [(w.title, w.meta.get("artists")) for w in pl.works] == [
        ("Kumo Works 2025.1", ["Kumo Works"]), ("Kumo Works 2025.2", ["Kumo Works"])]


def test_a_folder_that_does_not_read_as_its_pattern_says_so(lib, tmp_path):
    src = tmp_path / "src"
    mp4(src / "Just A Name" / "batch" / "a.mp4")
    pl = fi.plan(lib, fi.scan(src), by_creator())
    assert pl.works[0].meta["authors"] == ["Just A Name"]  # the whole name, never a guess
    assert any("does not read as" in w for w in pl.works[0].warnings)


def test_the_work_can_be_wherever_the_entries_are(lib, tmp_path):
    """For trees whose depth varies above the work."""
    src = tmp_path / "src"
    mp4(src / "Loose Work" / "e1.mp4")
    mp4(src / "Studio X" / "Deep Work" / "e1.mp4")
    pl = fi.plan(lib, fi.scan(src), fi.Profile(anchor="media", levels=[fi.Level(role="collection")]))
    assert sorted(w.title for w in pl.works) == ["Deep Work", "Loose Work"]


# ---- a manga tree ------------------------------------------------------------

def manga_tree(src: Path) -> Path:
    b = src / "Berserk"
    image(b / "cover.jpg")
    for n in (1, 2):
        for page in range(3):
            image(b / "Vol. 01" / f"Ch. {n:03d}" / f"{page + 1}.jpg")
    archive(b / "Vol. 02" / "Berserk Ch. 003 [Scans].cbz")
    (b / "Vol. 02").mkdir(parents=True, exist_ok=True)
    (b / "Vol. 02" / "Ch. 004.7z").write_bytes(b"this is no archive at all")
    (b / "Thumbs.db").write_bytes(b"junk")
    return src


def manga_profile(**kw) -> fi.Profile:
    return fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="work")],
                      numbering="name", numPattern="Ch. {n}", dropTags=True,
                      language="en", meta={"type": "manga"}, **kw)


def test_image_folders_and_archives_are_entries_and_a_pattern_numbers_them(lib, tmp_path):
    src = manga_tree(tmp_path / "src")
    pl = fi.plan(lib, fi.scan(src), manga_profile())
    w = pl.works[0]
    assert w.cover == "Berserk/cover.jpg"
    assert [(e.num, e.title, e.section, e.kind, e.state) for e in w.entries] == [
        ("1", "Ch. 001", "Vol. 01", "images", "new"),
        ("2", "Ch. 002", "Vol. 01", "images", "new"),
        ("3", "Berserk Ch. 003", "Vol. 02", "archive", "new"),
        ("", "", "Vol. 02", "archive", "unreadable")]  # listed, never stored
    assert any(i.path == "Berserk/Thumbs.db" for i in pl.ignored)


def test_a_folder_of_pages_is_stored_as_one_plain_zip(lib, tmp_path):
    src = manga_tree(tmp_path / "src")
    report = go(lib, src, manga_profile())
    assert (report.imported, report.skipped, report.failed) == (3, 1, [])
    t = titles(lib)["Berserk"]
    assert t.ownCovers  # cover.jpg became the cover
    first = t.chapters[0]
    assert (first.pages, first.lang, first.section) == (3, "en", "Vol. 01")
    stored = lib.vault.chapter_media_path(t.id, first.id)
    assert stored.suffix == ".zip" and zipfile.is_zipfile(stored)


# ---- running again -----------------------------------------------------------

def test_running_the_same_import_again_stores_nothing_twice(lib, tmp_path):
    src = creator_tree(tmp_path / "src")
    go(lib, src, by_creator())
    again = go(lib, src, by_creator())
    assert (again.imported, again.skipped) == (0, 4)
    assert [len(t.chapters) for t in lib.query()] == [2, 2]


def test_what_is_new_to_a_work_continues_after_what_it_holds(lib, tmp_path):
    src = creator_tree(tmp_path / "src")
    go(lib, src, by_creator())
    # a file that sorts FIRST arrives later: numbering by place must not shift
    # what is stored, nor take a number something else already has
    mp4(src / "vidhub @kumo Kumo Works" / "2025.1" / "0 intro.mp4", b"\x09" * 50)
    pl = fi.plan(lib, fi.scan(src), by_creator())
    assert [(e.title, e.num, e.state) for e in pl.works[0].entries] == [
        ("0 intro", "3", "new"),
        ("月光 - Moonlight", "1", "exists"),
        ("桜の舞(春)", "2", "exists")]


def test_a_work_already_in_the_library_keeps_what_the_owner_typed(lib, tmp_path):
    lib.create(DraftIn(meta=TitleMeta(title="Kumo Works 2025.1", type="dance", tags=["keep"],
                                      authors=["Typed By Hand"]),
                       provenance={"type": FieldProvenance(origin="auto"),
                                   "tags": FieldProvenance(origin="auto"),
                                   "authors": FieldProvenance(origin="manual")}))
    go(lib, creator_tree(tmp_path / "src"), by_creator())
    t = titles(lib)["Kumo Works 2025.1"]
    assert t.authors == ["Typed By Hand"]       # manual: untouched
    assert t.tags == ["keep", "vidhub", "mmd"]   # automatic list: gains, never loses
    assert t.type == "mmd"                      # automatic single: filled
    assert len(t.chapters) == 2


def test_an_entry_the_work_already_has_is_skipped_or_replaced(lib, tmp_path):
    """Numbered by place, what is new to a work goes after what it holds; a
    number read from the name can name an entry the work already has."""
    src = tmp_path / "src"
    mp4(src / "Show" / "Ep 1.mp4", b"\x05" * 10)
    out = lib.create(DraftIn(meta=TitleMeta(title="Show"), chapters=[ChapterRow(id="c1", num="1")]))
    p = fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="work")],
                   numbering="name", numPattern="Ep {n}")

    assert fi.plan(lib, fi.scan(src), p).works[0].entries[0].state == "exists"
    report = go(lib, src, p.model_copy(update={"entryExists": "replace"}))
    assert report.replaced == 1
    t = lib.get(out.id)
    assert [(c.id, c.dl) for c in t.chapters] == [("c1", True)]  # same entry, media attached


def test_a_separate_work_is_made_beside_one_of_the_same_name(lib, tmp_path):
    lib.create(DraftIn(meta=TitleMeta(title="Show")))
    mp4(tmp_path / "src" / "Show" / "e1.mp4")
    p = fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="work")], workExists="separate")
    go(lib, tmp_path / "src", p)
    assert sorted(len(t.chapters) for t in lib.query() if t.title == "Show") == [0, 1]


# ---- what is refused ---------------------------------------------------------

def test_two_entries_with_one_number_are_a_conflict_not_an_overwrite(lib, tmp_path):
    src = tmp_path / "src"
    mp4(src / "Show" / "Ep 1.mp4", b"\x01")
    mp4(src / "Show" / "Ep 1 (v2).mp4", b"\x02")
    p = fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="work")],
                   numbering="name", numPattern="Ep {n}")
    pl = fi.plan(lib, fi.scan(src), p)
    assert [e.state for e in pl.works[0].entries] == ["conflict", "conflict"]
    assert go(lib, src, p).imported == 0


def test_two_folders_that_give_one_title_are_a_conflict(lib, tmp_path):
    src = tmp_path / "src"
    mp4(src / "A" / "Show" / "e.mp4")
    mp4(src / "B" / "Show" / "e.mp4")
    p = fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="collection"), fi.Level(role="work")])
    assert [w.action for w in fi.plan(lib, fi.scan(src), p).works] == ["conflict", "conflict"]


def test_a_profile_that_cannot_mean_anything_is_refused():
    with pytest.raises(fi.InvalidProfile):
        fi.check(fi.Profile(levels=[fi.Level(role="work"), fi.Level(role="work")]))
    with pytest.raises(fi.InvalidProfile):
        fi.check(fi.Profile(levels=[fi.Level(role="work", pattern="{folder} x")]))
    with pytest.raises(fi.InvalidProfile):
        fi.check(fi.Profile(numbering="name", numPattern="no number here"))
    with pytest.raises(fi.InvalidProfile):
        fi.check(fi.Profile(meta={"title": "x"}))


# ---- this run's own choices --------------------------------------------------

def test_a_work_can_be_given_values_of_its_own_and_another_title(lib, tmp_path):
    src = creator_tree(tmp_path / "src")
    o = fi.Overrides(titles={"vidhub @kumo Kumo Works/2025.2": "Spring batch"},
                     meta={"vidhub @kumo Kumo Works/2025.2": {"tags": ["extra"], "year": "2025"}},
                     off=["vidhub @kumo Kumo Works/2025.1/月光 - Moonlight.mp4"])
    go(lib, src, by_creator(), o)
    t = titles(lib)
    assert t["Spring batch"].tags == ["vidhub", "mmd", "extra"] and t["Spring batch"].year == "2025"
    assert t["Kumo Works 2025.1"].tags == ["vidhub", "mmd"]
    assert len(t["Kumo Works 2025.1"].chapters) == 1  # the one left out


def test_values_arrive_through_the_librarys_rules(lib, tmp_path):
    lib.rules.save([Rule(field="tags", match=["MMD"], action="rename", to="3d dance")])
    src = creator_tree(tmp_path / "src")
    pl = fi.plan(lib, fi.scan(src), by_creator())
    assert pl.works[0].meta["tags"] == ["vidhub", "3d dance"] and pl.works[0].ruled == ["tags"]


# ---- moving ------------------------------------------------------------------

def test_a_move_deletes_an_original_only_once_its_entry_is_stored(lib, tmp_path):
    src = manga_tree(tmp_path / "src")
    report = go(lib, src, manga_profile(files="move"))
    assert report.imported == 3 and report.kept == 0
    b = src / "Berserk"
    assert not (b / "Vol. 01").exists()                  # emptied, so gone
    assert not (b / "cover.jpg").exists()
    assert (b / "Vol. 02" / "Ch. 004.7z").is_file()      # never stored, so never deleted
    assert (b / "Thumbs.db").is_file() and src.is_dir()  # not media; and never the root
    assert titles(lib)["Berserk"].chapters[2].pages == 2


def test_stop_puts_the_run_down_between_entries(lib, tmp_path):
    stop = threading.Event()
    stop.set()
    report = go(lib, creator_tree(tmp_path / "src"), by_creator(), stop=stop)
    assert report.stopped and report.imported == 0 and lib.query() == []


# ---- profiles and the API ----------------------------------------------------

def test_profiles_travel_with_the_library(lib):
    saved = fi.ProfileStore(lib.vault).save([by_creator()])
    again = fi.ProfileStore(lib.vault).profiles()
    assert [p.id for p in again] == [saved[0].id] and again[0].workTitle == "{creator} {folder}"
    assert (lib.root / fi.DOC).is_file()


def test_scan_plan_and_run_through_the_api(tmp_path):
    lib = Library(tmp_path / "lib")
    src = creator_tree(tmp_path / "src")
    with TestClient(create_app(lib)) as c:
        sc = c.post("/api/import/scan", json={"root": str(src)}).json()
        assert sc["levels"][1]["names"] == ["vidhub @kumo Kumo Works"] and sc["counts"]["video"] == 4
        body = {"scanId": sc["scanId"], "profile": by_creator().model_dump()}
        assert c.post("/api/import/plan", json=body).json()["entries"] == 4
        report = c.post("/api/import/run", json=body).json()
        assert report["imported"] == 4
        assert c.get("/api/import/runs").json()[0]["imported"] == 4
        # the library itself is never a folder to import
        assert c.post("/api/import/scan", json={"root": str(lib.root)}).status_code == 422
        bad = {**body, "profile": {"levels": [{"role": "work"}, {"role": "work"}]}}
        assert c.post("/api/import/plan", json=bad).status_code == 422
    lib.close()


# ---- what the review of the first cut found ----------------------------------

@pytest.mark.skipif(os.name != "nt", reason="a directory junction is a Windows thing")
def test_a_junction_is_never_followed_into_the_library(lib, tmp_path):
    """A junction reads as a plain folder. Followed into the vault, a move would
    have imported the library's own media — and deleted it."""
    import _winapi
    src = tmp_path / "src"
    mp4(src / "Show" / "e.mp4")
    out = lib.create(DraftIn(meta=TitleMeta(title="Kept"), chapters=[ChapterRow(id="c1", num="1")]))
    lib.attach_chapter_media(out.id, num="1", lang="", group="", src=mp4(tmp_path / "k.mp4"),
                             sidecar={"filename": "k.mp4"})
    _winapi.CreateJunction(str(lib.root), str(src / "Library"))
    s = fi.scan(src)
    assert any("not followed" in x for x in s.skipped)
    report = fi.run(lib, s, fi.Profile(anchor="media", levels=[fi.Level(role="collection")], files="move"))
    assert report.imported == 1
    assert lib.vault.chapter_media_path(out.id, "c1") is not None


def test_the_library_is_never_a_source_whatever_the_path_says(lib, tmp_path):
    out = lib.create(DraftIn(meta=TitleMeta(title="Kept")))
    inside = mp4(lib.root / "stray" / "x.mp4")
    with pytest.raises(ValueError):
        lib.import_entry(out.id, [inside], num="1", lang="", group="", title="", section="", date="",
                         sidecar={}, filename="x.mp4", link=True, replace=False)
    assert inside.is_file()


def test_a_read_only_original_is_copied_not_linked(lib, tmp_path):
    src = tmp_path / "src"
    ep = mp4(src / "Show" / "e1.mp4")
    os.chmod(ep, stat.S_IREAD)
    try:
        report = go(lib, src, fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="work")],
                                         files="move"))
        t = titles(lib)["Show"]
        stored = lib.vault.chapter_media_path(t.id, t.chapters[0].id)
        assert report.imported == 1 and stored.stat().st_nlink == 1
        assert os.access(stored, os.W_OK)  # the vault can still rewrite it atomically
    finally:
        if ep.exists():
            os.chmod(ep, stat.S_IWRITE | stat.S_IREAD)


def test_a_move_whose_original_stays_leaves_the_vault_its_own_file(lib, tmp_path, monkeypatch):
    src = tmp_path / "src"
    mp4(src / "Show" / "e1.mp4")
    monkeypatch.setattr(fi, "_drop", lambda path: False)  # Explorer holding the file
    report = go(lib, src, fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="work")],
                                     files="move"))
    t = titles(lib)["Show"]
    assert report.kept == 1
    assert lib.vault.chapter_media_path(t.id, t.chapters[0].id).stat().st_nlink == 1


def test_one_file_that_fails_does_not_stop_the_run(lib, tmp_path, monkeypatch):
    src = creator_tree(tmp_path / "src")
    real = Library.import_entry

    def flaky(self, tid, sources, **kw):
        if sources[0].name == "step.mp4":
            raise RuntimeError("the disk said no")
        return real(self, tid, sources, **kw)

    monkeypatch.setattr(Library, "import_entry", flaky)
    report = go(lib, src, by_creator())
    assert report.imported == 3 and [f.reason for f in report.failed] == ["the disk said no"]


def test_old_file_dates_neither_break_the_plan_nor_the_pages(lib, tmp_path):
    src = tmp_path / "src"
    for i in (1, 2):
        os.utime(image(src / "Book" / "Ch 1" / f"{i}.jpg"), (86400, 86400))  # 1970: no zip can say it
    old = mp4(src / "Book" / "x.mp4")
    try:
        os.utime(old, (-86400 * 400, -86400 * 400))  # before 1970
    except OSError:
        pass  # a filesystem that cannot hold it cannot hand it to the plan either
    p = fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="work")])
    pl = fi.plan(lib, fi.scan(src), p)
    assert {e.kind for e in pl.works[0].entries} == {"images", "video"}
    assert go(lib, src, p).imported == 2


def test_two_files_never_take_turns_at_one_entry(lib, tmp_path):
    src = tmp_path / "src"
    mp4(src / "Show" / "Ep 1.mp4", b"\x01" * 10)
    p = fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="work")],
                   numbering="name", numPattern="Ep {n}", entryExists="replace")
    go(lib, src, p)
    mp4(src / "Show" / "Ep 1 v2.mp4", b"\x02" * 20)
    pl = fi.plan(lib, fi.scan(src), p)
    assert sorted((e.path, e.state) for e in pl.works[0].entries) == [
        ("Show/Ep 1 v2.mp4", "conflict"), ("Show/Ep 1.mp4", "exists")]
    assert go(lib, src, p).replaced == 0 and go(lib, src, p).replaced == 0


def test_a_file_that_changed_is_its_entry_changed_not_a_new_one(lib, tmp_path):
    src = tmp_path / "src"
    mp4(src / "Show" / "a.mp4", b"\x01" * 10)
    mp4(src / "Show" / "b.mp4", b"\x02" * 10)
    p = fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="work")])
    go(lib, src, p)
    mp4(src / "Show" / "a.mp4", b"\x03" * 99)  # re-encoded in place
    e = fi.plan(lib, fi.scan(src), p).works[0].entries[0]
    assert (e.num, e.state) == ("1", "exists") and "changed" in e.reason
    report = go(lib, src, p.model_copy(update={"entryExists": "replace"}))
    assert report.replaced == 1 and report.imported == 0
    assert [c.num for c in titles(lib)["Show"].chapters] == ["1", "2"]


def test_an_entry_stored_before_a_rule_is_not_doubled(lib, tmp_path):
    out = lib.create(DraftIn(meta=TitleMeta(title="Show"),
                             chapters=[ChapterRow(id="c1", num="1", lang="English")]))
    lib.rules.save([Rule(field="language", match=["English"], action="rename", to="en")])
    mp4(tmp_path / "src" / "Show" / "Ep 1.mp4")
    p = fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="work")], language="English",
                   numbering="name", numPattern="Ep {n}", entryExists="replace")
    go(lib, tmp_path / "src", p)
    assert [(c.id, c.lang, c.dl) for c in lib.get(out.id).chapters] == [("c1", "English", True)]


def test_a_token_nothing_filled_is_never_stored(lib, tmp_path):
    src = tmp_path / "src"
    mp4(src / "Just A Name" / "batch" / "a.mp4")
    pl = fi.plan(lib, fi.scan(src), by_creator().model_copy(update={"language": "{site}"}))
    assert pl.works[0].meta["tags"] == ["mmd"] and pl.works[0].entries[0].lang == ""
    assert any("{site} is not read" in w for w in pl.works[0].warnings)


def test_a_webp_page_never_hides_a_jpeg_of_the_same_name(lib, tmp_path):
    from PIL import Image
    src = tmp_path / "src"
    (src / "Book" / "Ch 1").mkdir(parents=True)
    Image.new("RGB", (4, 4), (200, 30, 90)).save(src / "Book" / "Ch 1" / "01.webp", "WEBP")
    image(src / "Book" / "Ch 1" / "01.jpg")
    go(lib, src, fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="work")]))
    assert titles(lib)["Book"].chapters[0].pages == 2


def test_a_title_typed_for_one_work_is_the_owners_own(lib, tmp_path):
    src = creator_tree(tmp_path / "src")
    go(lib, src, by_creator(), fi.Overrides(titles={"vidhub @kumo Kumo Works/2025.1": "Winter"}))
    t = titles(lib)
    assert t["Winter"].provenance["title"].origin == "manual"
    assert t["Kumo Works 2025.2"].provenance["title"].origin == "auto"


def test_running_again_brings_new_metadata_to_works_already_imported(lib, tmp_path):
    src = creator_tree(tmp_path / "src")
    go(lib, src, by_creator())
    again = by_creator().model_copy(update={"meta": {"type": "mmd", "tags": ["{site}", "mmd", "dance"]}})
    go(lib, src, again)
    assert titles(lib)["Kumo Works 2025.1"].tags == ["vidhub", "mmd", "dance"]


def test_run_refuses_values_a_field_cannot_hold(tmp_path):
    lib = Library(tmp_path / "lib")
    src = creator_tree(tmp_path / "src")
    with TestClient(create_app(lib)) as c:
        sid = c.post("/api/import/scan", json={"root": str(src)}).json()["scanId"]
        r = c.post("/api/import/run", json={"scanId": sid, "profile": by_creator().model_dump(),
                                            "overrides": {"meta": {"x": {"title": "no"}}}})
        assert r.status_code == 422
    lib.close()
