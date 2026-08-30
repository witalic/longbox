"""ComicInfo.xml — the mirror that makes a chapter readable without longbox.

A mirror, never a second source of truth: it is written where the archive is
being rewritten anyway, and read only into fields nobody has decided yet.
"""
from __future__ import annotations

import zipfile

import pytest

from app.library import comicinfo, media
from app.library.models import ChapterRow, DraftIn, TitleDoc, TitleMeta
from app.library.service import Library
from app.library.vault import safe_id
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def lib(tmp_path):
    lib = Library(tmp_path / "lib")
    yield lib
    lib.close()


def _zip(path, pages=("001.jpg",), extra=None):
    with zipfile.ZipFile(path, "w") as z:
        for i, name in enumerate(pages):
            z.writestr(name, b"page-%d" % i)
        for name, blob in (extra or {}).items():
            z.writestr(name, blob)
    return path


def _stored_xml(lib, tid, cid):
    with zipfile.ZipFile(lib.vault.chapter_media_path(tid, cid)) as z:
        return comicinfo.parse(z.read(comicinfo.NAME))


def test_a_stored_chapter_describes_itself(lib, tmp_path):
    """Komga and Kavita read the library as it stands — no export step."""
    out = lib.create(DraftIn(meta=TitleMeta(
        title="Paper Lanterns", alt="Papierlaternen", desc="A summary.", year="2021",
        authors=["Aoi Mori"], artists=["Kenji Sato"], genres=["drama"], tags=["slow burn"],
        characters=["Rin"], studio=["Edge"])))
    lib.attach_chapter_media(out.id, num="12", lang="EN", group="dex",
                             src=_zip(tmp_path / "dl.zip"), sidecar={})
    cid = lib.get(out.id).chapters[0].id

    said = _stored_xml(lib, out.id, cid)
    assert said["title"] == "Paper Lanterns"
    assert said["num"] == "12"
    assert said["lang"] == "en"
    assert said["group"] == "dex"
    assert said["authors"] == ["Aoi Mori"] and said["artists"] == ["Kenji Sato"]
    assert said["genres"] == ["drama"] and said["tags"] == ["slow burn"]
    assert said["alt"] == "Papierlaternen" and said["year"] == "2021"


def test_the_mirror_never_disturbs_what_describes_the_file(lib, tmp_path):
    """The digest and size are stamped for the file as it ARRIVED, and writing
    the mirror rewrites it — leaving them stale would make the next revision
    pass call every freshly stored chapter corrupt."""
    out = lib.create(DraftIn(meta=TitleMeta(title="Work")))
    lib.attach_chapter_media(out.id, num="1", lang="", group="",
                             src=_zip(tmp_path / "dl.zip"), sidecar={})
    assert lib.verify(deep=True)["findings"] == []

    cid = lib.get(out.id).chapters[0].id
    side = lib.vault.chapter_sidecars(out.id)[safe_id(cid)]
    stored = lib.vault.chapter_media_path(out.id, cid)
    assert side["size"] == stored.stat().st_size
    assert side["sha256"] == media.digest_of(stored)


def test_a_metadata_edit_does_not_rewrite_archives_until_asked(lib, tmp_path):
    """Rewriting gigabytes because a tag changed is not a trade the app makes
    quietly — the refresh is explicit, and touches only what differs."""
    out = lib.create(DraftIn(meta=TitleMeta(title="Work")))
    lib.attach_chapter_media(out.id, num="1", lang="", group="",
                             src=_zip(tmp_path / "dl.zip"), sidecar={})
    cid = lib.get(out.id).chapters[0].id
    lib.commit(out.id, DraftIn(meta=TitleMeta(title="Work Renamed"),
                               chapters=[ChapterRow(id=cid, num="1")]))
    assert _stored_xml(lib, out.id, cid)["title"] == "Work"  # untouched so far

    assert lib.refresh_comicinfo()["written"] == 1
    assert _stored_xml(lib, out.id, cid)["title"] == "Work Renamed"
    # nothing differs now, so nothing is rewritten
    assert lib.refresh_comicinfo()["written"] == 0


def test_a_page_edit_carries_the_mirror_forward(lib, tmp_path):
    out = lib.create(DraftIn(meta=TitleMeta(title="Work")))
    lib.attach_chapter_media(out.id, num="1", lang="", group="",
                             src=_zip(tmp_path / "dl.zip"), sidecar={})
    cid = lib.get(out.id).chapters[0].id
    lib.commit(out.id, DraftIn(meta=TitleMeta(title="Renamed"),
                               chapters=[ChapterRow(id=cid, num="1")]))
    lib.add_chapter_pages(out.id, cid, [(b"another-page", ".jpg")])
    assert _stored_xml(lib, out.id, cid)["title"] == "Renamed"
    assert lib.verify(deep=True)["findings"] == []


def test_an_archive_that_describes_itself_names_its_own_entry(lib, tmp_path):
    """Someone else's cbz, dropped in with nothing typed: the file gets to say
    which chapter it is."""
    out = lib.create(DraftIn(meta=TitleMeta(title="Work")))
    xml = comicinfo.build(lib.vault.load(out.id),
                          ChapterRow(id="x", num="7", lang="UA", group="fansub"))
    lib.attach_chapter_media(out.id, num="", lang="", group="",
                             src=_zip(tmp_path / "dl.zip", extra={comicinfo.NAME: xml}),
                             sidecar={})
    row = lib.get(out.id).chapters[0]
    assert (row.num, row.lang, row.group) == ("7", "ua", "fansub")


def test_a_typed_label_is_never_overruled_by_the_file(lib, tmp_path):
    """A label typed by hand is a decision, and the provenance rule says a
    decision outranks anything automatic."""
    out = lib.create(DraftIn(meta=TitleMeta(title="Work")))
    xml = comicinfo.build(lib.vault.load(out.id), ChapterRow(id="x", num="7", lang="UA"))
    lib.attach_chapter_media(out.id, num="Extra", lang="EN", group="",
                             src=_zip(tmp_path / "dl.zip", extra={comicinfo.NAME: xml}),
                             sidecar={})
    row = lib.get(out.id).chapters[0]
    assert (row.num, row.lang) == ("Extra", "EN")


def test_an_existing_comicinfo_is_replaced_not_duplicated(lib, tmp_path):
    out = lib.create(DraftIn(meta=TitleMeta(title="Work")))
    lib.attach_chapter_media(
        out.id, num="1", lang="", group="",
        src=_zip(tmp_path / "dl.zip",
                 extra={comicinfo.NAME: b"<ComicInfo><Series>Old</Series></ComicInfo>"}),
        sidecar={})
    cid = lib.get(out.id).chapters[0].id
    with zipfile.ZipFile(lib.vault.chapter_media_path(out.id, cid)) as z:
        assert z.namelist().count(comicinfo.NAME) == 1
    assert _stored_xml(lib, out.id, cid)["title"] == "Work"


def test_an_empty_element_never_erases_what_the_library_holds():
    """A file that said nothing and a file that said empty are different
    answers, and only one of them may reach a field."""
    said = comicinfo.parse(b"<ComicInfo><Series>S</Series><Summary></Summary></ComicInfo>")
    assert said == {"title": "S"}
    assert comicinfo.parse(b"not xml at all") == {}
    assert comicinfo.parse(b"<Other><Series>S</Series></Other>") == {}


def test_a_label_with_no_number_stays_out_of_the_number_element():
    """`Number` holds a number; "Extra" there would arrive as junk elsewhere."""
    said = comicinfo.parse(comicinfo.build(TitleDoc(meta=TitleMeta(title="W")),
                                           ChapterRow(id="x", num="Extra")))
    assert "num" not in said


def test_duplicates_are_found_by_content_now_that_the_mirror_differs(lib, tmp_path):
    """Per-entry metadata means two copies of one release are no longer byte
    equal — so duplicates are found by what the chapter SHOWS, not by the file."""
    a = lib.create(DraftIn(meta=TitleMeta(title="A")))
    b = lib.create(DraftIn(meta=TitleMeta(title="B")))
    lib.attach_chapter_media(a.id, num="1", lang="EN", group="x",
                             src=_zip(tmp_path / "a.zip"), sidecar={})
    lib.attach_chapter_media(b.id, num="99", lang="UA", group="y",
                             src=_zip(tmp_path / "b.zip"), sidecar={})
    ca = lib.vault.chapter_media_path(a.id, lib.get(a.id).chapters[0].id)
    cb = lib.vault.chapter_media_path(b.id, lib.get(b.id).chapters[0].id)
    assert media.digest_of(ca) != media.digest_of(cb)            # the files differ
    assert media.content_digest(ca) == media.content_digest(cb)  # the pages do not
    assert len(lib.duplicates()["groups"]) == 1


def test_an_unchanged_chapter_is_not_rewritten_or_even_opened(lib, tmp_path, monkeypatch):
    """The answer to "will it rewrite what is already there": no — and after the
    first pass it does not open the archive to find that out either. Comparing
    inside the zip would be correct too, and would cost a read of every archive
    in the library to discover that nothing needs doing."""
    out = lib.create(DraftIn(meta=TitleMeta(title="Work")))
    lib.attach_chapter_media(out.id, num="1", lang="", group="",
                             src=_zip(tmp_path / "dl.zip"), sidecar={})
    cid = lib.get(out.id).chapters[0].id
    stored = lib.vault.chapter_media_path(out.id, cid)
    before = stored.stat().st_mtime_ns

    opened: list[str] = []
    real = comicinfo.write_into
    monkeypatch.setattr(comicinfo, "write_into",
                        lambda p, d: (opened.append(p.name), real(p, d))[1])

    assert lib.refresh_comicinfo()["written"] == 0
    assert opened == [], "the archive was opened to learn what was already recorded"
    assert stored.stat().st_mtime_ns == before

    # a metadata change does reach it, exactly once
    lib.commit(out.id, DraftIn(meta=TitleMeta(title="Renamed"),
                               chapters=[ChapterRow(id=cid, num="1")]))
    assert lib.refresh_comicinfo()["written"] == 1
    assert opened == ["dl.zip".replace("dl", safe_id(cid))] or len(opened) == 1
    assert lib.refresh_comicinfo()["written"] == 0
    assert len(opened) == 1, "the settled chapter was opened again"


def test_the_mirror_reads_a_title_sidecars_once_not_once_per_chapter(lib, tmp_path, monkeypatch):
    """Reading them is a glob and a JSON parse per chapter, so asking for them
    inside the per-chapter loop makes a title of forty chapters do forty times
    the work it needs — squared, over the library."""
    out = lib.create(DraftIn(meta=TitleMeta(title="Work")))
    for n in ("1", "2", "3", "4", "5"):
        lib.attach_chapter_media(out.id, num=n, lang="EN", group="g",
                                 src=_zip(tmp_path / f"c{n}.zip"), sidecar={})

    reads = 0
    original = lib.vault.chapter_sidecars

    def counted(tid):
        nonlocal reads
        reads += 1
        return original(tid)

    # a metadata change, so the pass has something to do at all
    lib.commit(out.id, DraftIn(meta=TitleMeta(title="Renamed"), chapters=[
        ChapterRow(id=c.id, num=c.num, lang=c.lang, group=c.group)
        for c in lib.get(out.id).chapters]))

    monkeypatch.setattr(lib.vault, "chapter_sidecars", counted)
    assert lib.refresh_comicinfo()["written"] == 5
    assert reads == 1, f"five chapters cost {reads} readings of the same sidecars"
