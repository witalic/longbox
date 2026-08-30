"""Integrity: what the vault stores and what it can still prove about it.

Size alone catches a truncated download and nothing else. A digest is what
lets an archive answer "these are still the bytes that were put here" — and
the revision pass is the one place that goes looking on purpose.
"""
from __future__ import annotations

import threading
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.library import media
from app.library.models import ChapterRow, DraftIn, TitleMeta
from app.library.service import Library
from app.library.vault import safe_id
from app.main import create_app
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


def _chapter(lib, tmp_path, *, num="1", pages=("001.jpg",)):
    """A title with one downloaded page chapter."""
    out = lib.create(DraftIn(meta=TitleMeta(title="Work")))
    src = tmp_path / f"dl-{num}.zip"
    with zipfile.ZipFile(src, "w") as z:
        for i, name in enumerate(pages):
            z.writestr(name, b"page-bytes-%d" % i)
    lib.attach_chapter_media(out.id, num=num, lang="EN", group="g", src=src,
                             sidecar={"filename": src.name})
    return out.id, lib.get(out.id).chapters[0].id


def _sidecar(lib, tid, cid):
    return lib.vault.chapter_sidecars(tid)[safe_id(cid)]


def _write_sidecar(lib, tid, cid, side):
    lib.vault.write_chapter_sidecar(tid, cid, side)


def test_a_stored_chapter_carries_the_digest_of_what_was_stored(lib, tmp_path):
    tid, cid = _chapter(lib, tmp_path)
    side = _sidecar(lib, tid, cid)
    stored = lib.vault.chapter_media_path(tid, cid)
    assert side["sha256"] == media.digest_of(stored)
    assert len(side["sha256"]) == 64


def test_a_page_edit_restamps_the_digest(lib, tmp_path):
    """The zip invariant means every page op rewrites the archive — a digest
    left over from before would report healthy content as corrupt."""
    tid, cid = _chapter(lib, tmp_path)
    before = _sidecar(lib, tid, cid)["sha256"]
    lib.add_chapter_pages(tid, cid, [(b"added-page", ".jpg")])
    after = _sidecar(lib, tid, cid)
    assert after["sha256"] != before
    assert after["sha256"] == media.digest_of(lib.vault.chapter_media_path(tid, cid))


def test_the_deep_pass_finds_bytes_that_changed_under_it(lib, tmp_path):
    """Bit rot, a bad copy, a dropped write on a network vault: the file is
    still there, still a zip, still the right size — and no longer the same."""
    tid, cid = _chapter(lib, tmp_path, pages=("001.jpg", "002.jpg", "003.jpg"))
    stored = lib.vault.chapter_media_path(tid, cid)
    raw = bytearray(stored.read_bytes())
    raw[40] ^= 0xFF  # inside the data, so the central directory still parses
    stored.write_bytes(bytes(raw))

    report = lib.verify(deep=True)
    assert [f["kind"] for f in report["findings"]] == ["corrupt"]
    assert report["findings"][0]["num"] == "1"
    assert report["findings"][0]["title"] == "Work"


def test_a_shallow_pass_never_reads_the_media(lib, tmp_path):
    """`deep` gates gigabytes of reading. Without it the pass is structural, so
    the same flipped byte goes unnoticed — that is the trade, and it has to be
    the trade, or nobody would run the cheap pass."""
    tid, cid = _chapter(lib, tmp_path, pages=("001.jpg", "002.jpg", "003.jpg"))
    stored = lib.vault.chapter_media_path(tid, cid)
    raw = bytearray(stored.read_bytes())
    raw[40] ^= 0xFF
    stored.write_bytes(bytes(raw))

    report = lib.verify()
    assert report["findings"] == []
    assert report["hashed"] == 0 and report["checked"] == 1


def test_content_from_before_digests_is_reported_not_blessed(lib, tmp_path):
    """A file stored before digests existed cannot be verified — only baselined.
    The report says which of the two happened, because a digest first taken
    today proves stability since today, not that the bytes are what arrived."""
    tid, cid = _chapter(lib, tmp_path)
    side = _sidecar(lib, tid, cid)
    rev = side["rev"]
    _write_sidecar(lib, tid, cid, {k: v for k, v in side.items() if k != "sha256"})

    report = lib.verify(deep=True)
    assert [f["kind"] for f in report["findings"]] == ["noDigest"]
    assert "sha256" not in _sidecar(lib, tid, cid)  # a report changes nothing

    report = lib.verify(deep=True, backfill=True)
    assert [f["kind"] for f in report["findings"]] == ["stamped"]
    after = _sidecar(lib, tid, cid)
    assert after["sha256"] == media.digest_of(lib.vault.chapter_media_path(tid, cid))
    # nothing about the CONTENT changed, so no cache may miss on account of it
    assert after["rev"] == rev


def test_a_sidecar_without_its_file_is_a_loss_but_a_bare_row_is_not(lib, tmp_path):
    """A chapter listed and never downloaded is the normal state of half a
    library. A chapter whose sidecar survived its media is a hole."""
    tid, cid = _chapter(lib, tmp_path)
    assert lib.verify()["findings"] == []

    lib.vault.chapter_media_path(tid, cid).unlink()
    report = lib.verify()
    assert [f["kind"] for f in report["findings"]] == ["missing"]
    assert report["findings"][0]["chapterId"] == cid

    lib.vault._chapters_dir(tid).joinpath(f"{safe_id(cid)}.json").unlink()
    assert lib.verify()["findings"] == []  # now it is just an undownloaded row


def test_an_archive_that_will_not_open_is_named_before_it_is_hashed(lib, tmp_path):
    """No point comparing digests of something no reader could open anyway —
    and the human needs the entry named, not a hash mismatch."""
    tid, cid = _chapter(lib, tmp_path)
    lib.vault.chapter_media_path(tid, cid).write_bytes(b"not a zip at all")
    report = lib.verify(deep=True)
    kinds = {f["kind"] for f in report["findings"]}
    assert "unreadable" in kinds and "corrupt" not in kinds


def test_a_sidecar_for_a_chapter_the_title_no_longer_lists_is_surfaced(lib, tmp_path):
    """Left behind by a crash between deleting the row and deleting the files:
    invisible in the UI, still occupying the vault."""
    tid, cid = _chapter(lib, tmp_path)
    d = lib.vault._chapters_dir(tid)
    (d / "ghost-9-en.json").write_text('{"filename": "ghost.zip"}', encoding="utf-8")
    report = lib.verify()
    assert [f["kind"] for f in report["findings"]] == ["orphan"]
    assert report["findings"][0]["chapterId"] == "ghost-9-en"


# ---- duplicates: the same bytes filed twice ----

def _add_chapter(lib, tid, *, num, lang="EN", group="g", payload=b"same-bytes", tmp_path=None):
    src = tmp_path / f"dl-{num}-{lang}-{group}.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("001.jpg", payload)
    lib.attach_chapter_media(tid, num=num, lang=lang, group=group, src=src,
                             sidecar={"filename": src.name})


def test_the_same_release_filed_twice_is_found_by_its_bytes(lib, tmp_path):
    """A re-download landing beside the original, or one file under two
    languages — identical content, different labels, invisible any other way."""
    tid, _ = _chapter(lib, tmp_path)
    _add_chapter(lib, tid, num="2", lang="UA", payload=b"page-bytes-0", tmp_path=tmp_path)

    report = lib.duplicates()
    assert len(report["groups"]) == 1
    group = report["groups"][0]
    assert {c["num"] for c in group["copies"]} == {"1", "2"}
    assert group["wasted"] == group["size"]  # one of the two is the waste


def test_distinct_content_is_never_grouped(lib, tmp_path):
    tid, _ = _chapter(lib, tmp_path)
    _add_chapter(lib, tid, num="2", payload=b"different-entirely", tmp_path=tmp_path)
    assert lib.duplicates()["groups"] == []


def test_content_without_a_digest_is_left_out_rather_than_guessed_at(lib, tmp_path):
    """Two files of the same size are not the same file. A vault that predates
    digests reports nothing here until it is baselined."""
    tid, cid = _chapter(lib, tmp_path)
    _add_chapter(lib, tid, num="2", payload=b"page-bytes-0", tmp_path=tmp_path)
    for ch in lib.get(tid).chapters:
        side = _sidecar(lib, tid, ch.id)
        _write_sidecar(lib, tid, ch.id, {k: v for k, v in side.items() if k != "content"})
    lib.sync()  # duplicates reads the index, so the sidecars have to land in it
    assert lib.duplicates()["groups"] == []


# ---- numbering gaps: silent unless the entries provably form a sequence ----

def _numbered(lib, labels, *, lang="EN", order="auto"):
    out = lib.create(DraftIn(meta=TitleMeta(title="Run", chapterOrder=order)))
    lib.commit(out.id, DraftIn(
        meta=TitleMeta(title="Run", chapterOrder=order),
        chapters=[ChapterRow(id="", num=n, lang=lang, group="g") for n in labels]))
    return out.id


def test_a_hole_in_a_real_run_is_reported(lib):
    tid = _numbered(lib, ["1", "2", "3", "4", "6", "7"])
    assert lib.gaps() == [{"titleId": tid, "title": "Run", "lang": "EN",
                          "group": "g", "missing": [5]}]


def test_a_run_with_no_holes_says_nothing(lib):
    _numbered(lib, ["1", "2", "3", "4", "5"])
    assert lib.gaps() == []


def test_two_translations_are_two_sequences(lib):
    """1-6 in English beside 4-5 in Ukrainian is not a hole in either."""
    out = lib.create(DraftIn(meta=TitleMeta(title="Run")))
    rows = [ChapterRow(id="", num=str(n), lang="EN", group="g") for n in range(1, 7)]
    rows += [ChapterRow(id="", num=str(n), lang="UA", group="g") for n in (4, 5)]
    lib.commit(out.id, DraftIn(meta=TitleMeta(title="Run"), chapters=rows))
    assert lib.gaps() == []


def test_a_handful_of_entries_is_not_a_sequence(lib):
    """An image set with three parts must never be told it is missing part 2."""
    _numbered(lib, ["1", "3", "4"])
    assert lib.gaps() == []


def test_labels_that_are_not_numbers_keep_the_detector_quiet(lib):
    _numbered(lib, ["Prologue", "Extra", "Omake", "Bonus", "1", "3"])
    assert lib.gaps() == []


def test_arbitrary_numbers_are_not_a_run_with_eight_hundred_holes(lib):
    _numbered(lib, ["1", "5", "40", "112", "900", "1200"])
    assert lib.gaps() == []


def test_a_hand_made_order_is_never_second_guessed(lib):
    """`chapterOrder: manual` is the owner saying the sequence is theirs."""
    _numbered(lib, ["1", "2", "3", "4", "6", "7"], order="manual")
    assert lib.gaps() == []


def test_an_extra_between_chapters_is_not_a_missing_one(lib):
    """10.5 is a real entry; it must not read as a missing 11."""
    tid = _numbered(lib, ["8", "9", "10", "10.5", "11", "12"])
    assert lib.gaps() == []
    assert lib.gaps(tid) == []


def test_nothing_is_claimed_beyond_the_highest_number_held(lib):
    """What the series is up to is not ours to know — only what is between."""
    tid = _numbered(lib, ["1", "2", "3", "4", "5", "7"])
    assert lib.gaps(tid)[0]["missing"] == [6]


# ---- the passes report themselves and can be put down ----

def _library_of(lib, tmp_path, n):
    for i in range(n):
        out = lib.create(DraftIn(meta=TitleMeta(title=f"Work {i}")))
        src = tmp_path / f"dl-{i}.zip"
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("001.jpg", b"page-%d" % i)
        lib.attach_chapter_media(out.id, num="1", lang="EN", group="g", src=src,
                                 sidecar={"filename": src.name})


def test_every_pass_counts_itself_out_loud(lib, tmp_path):
    """A pass over a network vault takes as long as it takes; what it must never
    do is take that long in silence."""
    _library_of(lib, tmp_path, 3)
    # EVERY pass, including the ones that only delete: one left out here is one
    # that shows "starting…" forever on a library big enough to notice
    for run in (lambda p: lib.verify(progress=p),
                lambda p: lib.duplicates(progress=p),
                lambda p: lib.gaps(progress=p),
                lambda p: lib.delete_leftovers(progress=p)):
        ticks: list[tuple[int, int]] = []
        run(lambda done, total: ticks.append((done, total)))
        assert ticks, "a pass reported nothing at all"
        assert ticks[-1] == (3, 3)
        assert all(total == 3 and 0 <= done <= 3 for done, total in ticks)


def test_a_stopped_pass_says_it_covered_only_part(lib, tmp_path):
    """"Nothing wrong" and "nothing wrong in the first one" are very different
    claims, and a report that was cut short must not make the first one."""
    _library_of(lib, tmp_path, 3)
    stop = threading.Event()

    def after_one(done, _total):
        if done >= 1:
            stop.set()

    report = lib.verify(progress=after_one, stop=stop)
    assert report["stopped"] is True
    assert report["checked"] < 3

    stop2 = threading.Event()
    stop2.set()
    assert lib.duplicates(stop=stop2)["stopped"] is True

    # the mirror needs something to mirror before being stopped means anything:
    # a pass with no work did not cover "part" of it
    for tid, doc, _c in lib.index.all_docs():
        lib.commit(tid, DraftIn(meta=TitleMeta(title=f"{doc.meta.title} II"), chapters=[
            ChapterRow(id=c.id, num=c.num, lang=c.lang, group=c.group) for c in doc.chapters]))
    assert lib.refresh_comicinfo(stop=stop2)["stopped"] is True


def test_a_pass_that_finished_never_claims_it_was_stopped(lib, tmp_path):
    """The event may be set by the time the walk ends — that is a pass that
    finished, not one that was cut short."""
    _library_of(lib, tmp_path, 2)
    stop = threading.Event()
    report = lib.verify(progress=lambda done, total: stop.set() if done == total else None,
                        stop=stop)
    assert report["stopped"] is False
    assert report["checked"] == 2


def test_one_title_asking_about_its_own_gaps_is_never_interrupted(lib):
    """The title page asks for itself while a full pass may be running; that
    read is index-only and has nothing to stop."""
    tid = _numbered(lib, ["1", "2", "3", "4", "6", "7"])
    stop = threading.Event()
    stop.set()
    assert lib.gaps(tid, stop=stop)[0]["missing"] == [5]


# ---- what a person is told, as opposed to what the pass found ----

def test_one_pass_answers_all_three_questions(lib, tmp_path):
    """Duplicates and gaps read the index and cost nothing on top of the walk,
    so asking for them separately was a choice the app made the person take."""
    _library_of(lib, tmp_path, 3)
    r = lib.check()
    assert set(r) >= {"broken", "leftovers", "duplicates", "gaps", "systemic", "withDigest"}
    assert r["broken"]["total"] == 0 and r["leftovers"]["files"] == 0
    # nothing had to be asked for twice
    assert r["duplicates"]["sets"] == 0 and r["gaps"]["titles"] == 0


def test_broken_chapters_are_counted_per_title_not_per_chapter(lib, tmp_path):
    """Forty broken chapters in one title are ONE problem with that title —
    forty rows would hide every other title under them."""
    tid, _ = _chapter(lib, tmp_path, num="1")
    for n in ("2", "3"):
        _add_chapter(lib, tid, num=n, payload=b"p-%s" % n.encode(), tmp_path=tmp_path)
    for ch in lib.get(tid).chapters:
        lib.vault.chapter_media_path(tid, ch.id).unlink()

    r = lib.check()
    assert r["broken"]["total"] == 3
    assert r["broken"]["titles"] == 1
    assert len(r["broken"]["rows"]) == 1
    row = r["broken"]["rows"][0]
    assert row["what"] == "the file is gone" and row["count"] == 3
    assert row["num"] == ""  # no longer one entry, so it counts instead of naming


def test_a_single_broken_chapter_names_the_entry(lib, tmp_path):
    tid, cid = _chapter(lib, tmp_path)
    lib.vault.chapter_media_path(tid, cid).unlink()
    row = lib.check()["broken"]["rows"][0]
    assert row["count"] == 1 and row["num"] == "1" and row["lang"] == "EN"


def test_a_library_wide_failure_is_named_as_one(lib, tmp_path):
    """A drive that is not mounted is not three thousand damaged files, and a
    report that lists them as such buries the only useful thing it knows."""
    _library_of(lib, tmp_path, 5)
    for tid, doc, _c in lib.index.all_docs():
        for ch in doc.chapters:
            p = lib.vault.chapter_media_path(tid, ch.id)
            if p:
                p.unlink()
    assert lib.check()["systemic"] is True


def test_one_damaged_chapter_is_not_a_library_wide_failure(lib, tmp_path):
    _library_of(lib, tmp_path, 4)
    tid, doc, _c = lib.index.all_docs()[0]
    lib.vault.chapter_media_path(tid, doc.chapters[0].id).unlink()
    r = lib.check()
    assert r["broken"]["total"] == 1 and r["systemic"] is False


def test_bookkeeping_is_a_number_not_a_list_of_problems(lib, tmp_path):
    """Content stored before checksums existed is a fact about the vault's age.
    It belongs beside the button as a count, never in the list of things wrong."""
    tid, cid = _chapter(lib, tmp_path)
    side = _sidecar(lib, tid, cid)
    _write_sidecar(lib, tid, cid, {k: v for k, v in side.items() if k != "sha256"})
    r = lib.check(deep=True)
    assert r["broken"]["total"] == 0
    assert r["withDigest"] == 0 and r["checked"] == 1


def test_leftovers_carry_what_they_cost(lib, tmp_path):
    tid, cid = _chapter(lib, tmp_path)
    d = lib.vault._chapters_dir(tid)
    (d / "ghost-9-en.zip.abc123.tmp").write_bytes(b"x" * 4096)
    (d / "ghost-9-en.json").write_text('{"filename": "ghost.zip"}', encoding="utf-8")

    left = lib.check()["leftovers"]
    assert left["files"] == 2 and left["titles"] == 1
    assert left["bytes"] >= 4096
    assert left["rows"][0]["name"].endswith(".tmp")  # biggest first


def test_deleting_leftovers_touches_nothing_that_belongs_to_an_entry(lib, tmp_path):
    tid, cid = _chapter(lib, tmp_path)
    d = lib.vault._chapters_dir(tid)
    (d / "ghost-9-en.zip").write_bytes(b"x" * 2048)
    (d / "ghost-9-en.json").write_text("{}", encoding="utf-8")
    media_path = lib.vault.chapter_media_path(tid, cid)
    sidecar = d / f"{safe_id(cid)}.json"

    out = lib.delete_leftovers()
    assert out["deleted"] == 2 and out["failed"] == 0 and out["bytes"] >= 2048
    assert media_path.is_file() and sidecar.is_file()   # the real chapter is untouched
    assert lib.check()["leftovers"]["files"] == 0
    assert lib.check()["broken"]["total"] == 0


def test_deleting_leftovers_recomputes_rather_than_trusting_a_list(lib, tmp_path):
    """The set is worked out at deletion time, so nothing can hand this a path
    to remove — and a file that stopped being a leftover survives."""
    tid, cid = _chapter(lib, tmp_path)
    assert lib.delete_leftovers() == {"deleted": 0, "failed": 0, "bytes": 0}
    assert lib.vault.chapter_media_path(tid, cid).is_file()


# ---- the wire: a report the UI can actually read ----
#
# The service-level tests above prove what the pass FINDS. These prove the
# report survives the response model — a field the composer stopped producing,
# or a shape the model refuses, shows up as an empty screen and nothing else.

@pytest.fixture
def client(tmp_path):
    lib = Library(tmp_path / "wire")
    with TestClient(create_app(lib)) as c:
        yield c
    lib.close()


def test_the_check_endpoint_answers_all_three_questions(client, tmp_path):
    lib = Library(tmp_path / "wire")
    try:
        _library_of(lib, tmp_path, 3)
    finally:
        lib.close()
    client.post("/api/settings/rebuild")

    r = client.post("/api/settings/check")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("checked", "expected", "withDigest", "systemic", "stopped",
                "broken", "leftovers", "duplicates", "gaps"):
        assert key in body, key
    assert body["broken"] == {"total": 0, "titles": 0, "rows": []}
    assert body["leftovers"]["files"] == 0
    assert body["checked"] == 3 and body["withDigest"] == 3


def test_the_check_endpoint_carries_every_kind_of_row(client, tmp_path):
    """One of each, so a row shape the model would refuse cannot reach the UI
    as an empty screen instead of an error."""
    lib = Library(tmp_path / "wire")
    try:
        tid = _numbered(lib, ["1", "2", "3", "4", "6", "7"])
        src = tmp_path / "a.zip"
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("001.jpg", b"same")
        lib.attach_chapter_media(tid, num="10", lang="EN", group="g", src=src, sidecar={})
        other = lib.create(DraftIn(meta=TitleMeta(title="Twin")))
        src2 = tmp_path / "b.zip"
        with zipfile.ZipFile(src2, "w") as z:
            z.writestr("001.jpg", b"same")
        lib.attach_chapter_media(other.id, num="1", lang="UA", group="g", src=src2, sidecar={})
        broken = lib.get(other.id).chapters[0]
        d = lib.vault._chapters_dir(tid)
        (d / "ghost.zip.9f3c.tmp").write_bytes(b"x" * 1024)
        lib.vault.chapter_media_path(other.id, broken.id).unlink()
    finally:
        lib.close()
    client.post("/api/settings/rebuild")

    body = client.post("/api/settings/check").json()
    assert body["broken"]["rows"][0]["what"] == "the file is gone"
    assert body["leftovers"]["rows"][0]["name"].endswith(".tmp")
    # 1,2,3,4,6,7 plus a 10 attached below: one run, three holes
    assert body["gaps"]["rows"][0]["missing"] == [5, 8, 9]
    assert body["gaps"]["rows"][0]["what"] == "missing 5, 8, 9"


def test_only_one_vault_pass_runs_at_a_time(client):
    """Two passes over one library on a network share only get in each other's
    way, so the second is refused rather than queued."""
    from app import passes
    passes._LOCK.acquire()
    passes.PASS.update(running=True, op="quick check")
    try:
        r = client.post("/api/settings/check")
        assert r.status_code == 409
        assert "quick check" in r.json()["detail"]
    finally:
        passes.PASS.update(running=False, op="")
        passes._LOCK.release()


def test_the_sweep_endpoint_reports_what_it_removed(client, tmp_path):
    lib = Library(tmp_path / "wire")
    try:
        tid, _cid = _chapter(lib, tmp_path)
        (lib.vault._chapters_dir(tid) / "ghost.zip.abc.tmp").write_bytes(b"x" * 2048)
    finally:
        lib.close()
    client.post("/api/settings/rebuild")

    # the sweep works in the scope the last check found; without one it refuses
    # rather than silently walking the whole library
    blind = client.post("/api/settings/leftovers")
    assert blind.status_code == 409 and "run a check first" in blind.json()["detail"]

    client.post("/api/settings/check")
    out = client.post("/api/settings/leftovers").json()
    assert out["deleted"] == 1 and out["failed"] == 0 and out["bytes"] == 2048
    assert client.post("/api/settings/check").json()["leftovers"]["files"] == 0


# ---- the record: when something ran, and what came of it ----

def test_every_vault_operation_leaves_a_line(client, tmp_path):
    """"Has this library ever been checked" must have an answer, and a report
    must not have to be re-earned every time the panel is opened."""
    lib = Library(tmp_path / "wire")
    try:
        _library_of(lib, tmp_path, 2)
        (lib.vault._chapters_dir(lib.index.all_docs()[0][0]) / "g.zip.a1.tmp").write_bytes(b"x" * 1024)
    finally:
        lib.close()
    client.post("/api/settings/rebuild")

    # nothing has been CHECKED yet, though the one-time archive sweep that runs
    # on a vault's first open has already left its own line
    assert client.get("/api/settings/health").json()["lastCheck"] is None

    client.post("/api/settings/check")
    client.post("/api/settings/leftovers")
    client.post("/api/settings/comicinfo")

    got = client.get("/api/settings/health").json()
    ops = [h["op"] for h in got["history"]]
    assert ops[:3] == ["update metadata", "delete leftovers", "quick check"]  # newest first
    assert all(h["at"] and h["seconds"] >= 0 for h in got["history"])
    assert "1 file(s) deleted" in got["history"][1]["outcome"]


def test_the_stored_report_survives_a_restart(client, tmp_path):
    """The panel reads the last answer off disk, so closing the app does not
    throw away a full check that took an hour."""
    lib = Library(tmp_path / "wire")
    try:
        _library_of(lib, tmp_path, 2)
    finally:
        lib.close()
    client.post("/api/settings/rebuild")
    fresh = client.post("/api/settings/check?deep=true").json()

    # a NEW Library over the same vault — the record is the vault's, not the app's
    again = Library(tmp_path / "wire")
    try:
        stored = again.health()
    finally:
        again.close()
    assert stored["lastCheck"]["checked"] == fresh["checked"]
    assert stored["lastCheck"]["at"] == fresh["at"]
    assert stored["history"][0]["op"] == "full check"
    assert stored["history"][0]["outcome"].startswith("nothing wrong with 2")


def test_the_record_keeps_only_what_happened_lately(lib, tmp_path):
    """A log, not an audit trail: it answers "when was this last done", and a
    vault file that grows without bound answers nothing better."""
    for i in range(lib.vault.HISTORY_KEEP + 5):
        lib.vault.write_health(entry={"op": f"run {i}", "at": "now", "outcome": ""})
    history = lib.vault.health()["history"]
    assert len(history) == lib.vault.HISTORY_KEEP
    assert history[0]["op"] == f"run {lib.vault.HISTORY_KEEP + 4}"


def test_a_stopped_pass_is_recorded_as_stopped(lib, tmp_path):
    """A report cut short is a partial answer, and the log must not remember it
    as a clean bill of health."""
    _library_of(lib, tmp_path, 5)
    stop = threading.Event()
    lib.check(progress=lambda done, _t: stop.set() if done >= 1 else None, stop=stop)
    assert lib.health()["history"][0]["stopped"] is True


def test_field_counts_are_the_whole_library_not_a_filtered_view(client):
    """The browser only ever holds the page the library is showing, so counting
    there made every field report whatever the library was filtered to."""
    client.post("/api/titles", json={"meta": {"title": "A", "type": "manga",
                                              "genres": ["drama"], "tags": ["x"]}})
    client.post("/api/titles", json={"meta": {"title": "B", "type": "manhwa",
                                              "genres": ["drama"]}})
    used = client.get("/api/fields/usage").json()
    assert used["title"] == 2 and used["type"] == 2 and used["genres"] == 2
    assert used["tags"] == 1
    assert used["year"] == 0

    # a filter over the library must not move these numbers
    assert len(client.get("/api/library?f=type:manga").json()) == 1
    assert client.get("/api/fields/usage").json() == used


def test_the_sweep_only_visits_the_titles_the_check_named(client, tmp_path):
    """Deleting two files must not mean a lock and a directory listing for every
    title in the library — on a network vault that is minutes of nothing."""
    lib = Library(tmp_path / "wire")
    try:
        _library_of(lib, tmp_path, 6)
        dirty = lib.index.all_docs()[0][0]
        (lib.vault._chapters_dir(dirty) / "g.zip.a1.tmp").write_bytes(b"x" * 512)
    finally:
        lib.close()
    client.post("/api/settings/rebuild")

    left = client.post("/api/settings/check").json()["leftovers"]
    assert left["titleIds"] == [dirty]  # the scope, uncapped, ids only

    seen: list[str] = []
    real = Library(tmp_path / "wire")
    try:
        original = real._strays
        real._strays = lambda tid, doc, stems: (seen.append(tid), original(tid, doc, stems))[1]
        out = real.delete_leftovers(left["titleIds"])
    finally:
        real.close()
    assert out["deleted"] == 1
    assert seen == [dirty]  # five untouched titles were never opened


def test_a_sweep_with_no_scope_still_covers_the_whole_library(lib, tmp_path):
    """The scope is an optimisation, not the guarantee — asked with nothing to
    go on, the sweep is still right."""
    _library_of(lib, tmp_path, 3)
    for tid, _doc, _c in lib.index.all_docs():
        (lib.vault._chapters_dir(tid) / "g.zip.b2.tmp").write_bytes(b"x" * 64)
    assert lib.delete_leftovers()["deleted"] == 3


def test_the_scope_names_titles_to_look_in_not_files_to_delete(lib, tmp_path):
    """A title handed in whose files all still belong to entries loses nothing:
    what goes is worked out inside, from the entries the title actually has."""
    tid, cid = _chapter(lib, tmp_path)
    out = lib.delete_leftovers([tid])
    assert out == {"deleted": 0, "failed": 0, "bytes": 0}
    assert lib.vault.chapter_media_path(tid, cid).is_file()


def test_a_pass_reads_each_title_directory_once(lib, tmp_path, monkeypatch):
    """Asking for one chapter's file costs up to ten filesystem round trips —
    right for opening a chapter, ruinous for anything walking the library. This
    is the rule the vault already learned once ("one scan, not three stats")."""
    _library_of(lib, tmp_path, 4)
    # rows with no media at all: the expensive case, and the common one
    for tid, doc, _c in lib.index.all_docs():
        lib.commit(tid, DraftIn(meta=doc.meta, chapters=[
            *doc.chapters, ChapterRow(id="", num="9", lang="EN", group="g")]))

    per_chapter = 0
    original = lib.vault.chapter_media_path

    def counted(*a, **k):
        nonlocal per_chapter
        per_chapter += 1
        return original(*a, **k)

    monkeypatch.setattr(lib.vault, "chapter_media_path", counted)
    lib.verify()
    assert per_chapter == 0, "the pass went back to the disk chapter by chapter"


def test_the_listing_picks_the_same_file_the_reader_would(lib, tmp_path):
    """One listing replaces the per-chapter lookup only if it resolves media the
    same way: a zip wins over a leftover of a former extension."""
    tid, cid = _chapter(lib, tmp_path)
    d = lib.vault._chapters_dir(tid)
    stem = safe_id(cid)
    (d / f"{stem}.mp4").write_bytes(b"stale")
    (d / f"{stem}.poster.jpg").write_bytes(b"still")
    (d / f"{stem}.zip.old.tmp").write_bytes(b"debris")
    assert lib.vault.chapter_files(tid)[stem] == lib.vault.chapter_media_path(tid, cid)
    assert lib.vault.chapter_files(tid)[stem].suffix == ".zip"


def test_a_sweep_corrects_the_report_instead_of_re_earning_it(client, tmp_path):
    """The sweep just recomputed the leftovers of every title it visited, so the
    stored report is corrected from that — running the check again would be
    another full pass over the library for an answer already in hand."""
    lib = Library(tmp_path / "wire")
    try:
        _library_of(lib, tmp_path, 4)
        dirty = lib.index.all_docs()[0][0]
        (lib.vault._chapters_dir(dirty) / "g.zip.a1.tmp").write_bytes(b"x" * 900)
    finally:
        lib.close()
    client.post("/api/settings/rebuild")

    before = client.post("/api/settings/check").json()
    assert before["leftovers"]["files"] == 1 and before["leftovers"]["bytes"] == 900

    client.post("/api/settings/leftovers")

    # read back WITHOUT checking again
    after = client.get("/api/settings/health").json()["lastCheck"]["leftovers"]
    assert after["files"] == 0 and after["bytes"] == 0
    assert after["rows"] == [] and after["titleIds"] == []
    # and everything else the check found is still standing
    stored = client.get("/api/settings/health").json()["lastCheck"]
    assert stored["checked"] == before["checked"]


def test_a_sweep_that_finds_them_already_gone_still_clears_the_report(client, tmp_path):
    """The exact state that left a Delete button offering to remove files that
    were not there: the sweep found nothing, and the report kept saying two."""
    lib = Library(tmp_path / "wire")
    try:
        _library_of(lib, tmp_path, 3)
        dirty = lib.index.all_docs()[0][0]
        junk = lib.vault._chapters_dir(dirty) / "g.zip.a1.tmp"
        junk.write_bytes(b"x" * 700)
    finally:
        lib.close()
    client.post("/api/settings/rebuild")
    assert client.post("/api/settings/check").json()["leftovers"]["files"] == 1

    junk.unlink()  # something outside the app got there first
    out = client.post("/api/settings/leftovers").json()
    assert out["deleted"] == 0

    left = client.get("/api/settings/health").json()["lastCheck"]["leftovers"]
    assert (left["files"], left["bytes"], left["titles"], left["rows"]) == (0, 0, 0, [])


def test_a_sweep_put_down_early_corrects_nothing(lib, tmp_path):
    """A partial sweep knows only part of the answer. Subtracting from a capped
    list is how a report ends up claiming two files in zero titles — so a
    stopped sweep leaves the numbers exactly as the check measured them."""
    _library_of(lib, tmp_path, 4)
    ids = [tid for tid, _d, _c in lib.index.all_docs()]
    for tid in ids:
        (lib.vault._chapters_dir(tid) / "g.zip.b2.tmp").write_bytes(b"x" * 100)
    before = lib.check()["leftovers"]
    assert before["files"] == 4

    stop = threading.Event()
    lib.delete_leftovers(before["titleIds"],
                         progress=lambda done, _t: stop.set() if done >= 1 else None,
                         stop=stop)
    after = lib.health()["lastCheck"]["leftovers"]
    assert after["files"] == before["files"] and after["titleIds"] == before["titleIds"]
    assert lib.health()["history"][0]["stopped"] is True


def test_the_maintenance_passes_report_and_stop_like_the_rest(lib, tmp_path):
    """These two predate the pass slot, and each grew its own progress — which
    is how one of them ended up saying "starting…" with a Stop that did nothing.
    They answer the same way as everything else or they do not belong here."""
    _library_of(lib, tmp_path, 3)
    for run in (lambda p: lib.normalize_archives(force=True, progress=p),
                lambda p: lib.rescan(p)):
        ticks: list[tuple[int, int]] = []
        run(lambda done, total: ticks.append((done, total)))
        assert ticks, "a pass reported nothing at all"
        assert ticks[0][1] > 0, "the size was not known before the slow part started"
        assert ticks[-1] == (3, 3)


def test_a_rebuild_put_down_halfway_leaves_the_index_alone(lib, tmp_path):
    """Half a table is worse than a stale one, and the pass is re-runnable."""
    _library_of(lib, tmp_path, 4)
    before = lib.count()
    stop = threading.Event()
    stop.set()
    lib.rescan(stop=stop)
    assert lib.count() == before


def test_the_mirror_counts_chapters_because_it_rewrites_archives(lib, tmp_path):
    """A per-title tick leaves the bar still while a title of gigabytes is
    rewritten — and leaves Stop with nowhere to take effect until it finishes."""
    out = lib.create(DraftIn(meta=TitleMeta(title="Big")))
    for n in ("1", "2", "3"):
        src = tmp_path / f"c{n}.zip"
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("001.jpg", b"page")
        lib.attach_chapter_media(out.id, num=n, lang="EN", group="g", src=src, sidecar={})

    # nothing to mirror straight after ingest: the pass narrows to the titles
    # whose stored mirror no longer matches, which is what the index is for
    assert lib.refresh_comicinfo()["written"] == 0

    lib.commit(out.id, DraftIn(meta=TitleMeta(title="Renamed"), chapters=[
        ChapterRow(id=c.id, num=c.num, lang=c.lang, group=c.group)
        for c in lib.get(out.id).chapters]))
    ticks: list[tuple[int, int]] = []
    lib.refresh_comicinfo(progress=lambda done, total: ticks.append((done, total)))
    assert ticks[0] == (0, 3) and ticks[-1] == (3, 3)  # ONE title, three ticks


def test_the_mirror_can_be_stopped_inside_a_title(lib, tmp_path):
    out = lib.create(DraftIn(meta=TitleMeta(title="Big")))
    for n in ("1", "2", "3", "4"):
        src = tmp_path / f"d{n}.zip"
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("001.jpg", b"page")
        lib.attach_chapter_media(out.id, num=n, lang="EN", group="g", src=src, sidecar={})
    lib.commit(out.id, DraftIn(meta=TitleMeta(title="Renamed"),
                               chapters=[ChapterRow(id=c.id, num=c.num, lang=c.lang,
                                                    group=c.group)
                                         for c in lib.get(out.id).chapters]))

    stop = threading.Event()
    r = lib.refresh_comicinfo(progress=lambda done, _t: stop.set() if done >= 2 else None,
                              stop=stop)
    assert r["stopped"] is True
    assert r["written"] < 4  # it did not finish the title it was inside


def test_converting_does_not_re_open_what_it_already_normalised(lib, tmp_path, monkeypatch):
    """Pressing Convert again must not cost a read of every archive in the
    library. It never re-CONVERTED them — it opened each one to find that out,
    which on a network folder is the same bill."""
    _library_of(lib, tmp_path, 4)
    lib.normalize_archives(force=True)

    opened: list[str] = []
    real = zipfile.is_zipfile
    monkeypatch.setattr(zipfile, "is_zipfile", lambda p: (opened.append(str(p)), real(p))[1])
    assert lib.normalize_archives(force=True) == 0
    assert opened == [], f"{len(opened)} archive(s) were opened to learn nothing"


def test_converting_never_tries_to_repack_an_episode_or_a_still(lib, tmp_path):
    """A video is stored as the file it arrived as and a still is a still —
    neither can become a page archive. Attempting it was a failed repack per
    file per run, and `force` meant even the remembered failure did not stop it."""
    out = lib.create(DraftIn(meta=TitleMeta(title="Series", type="anime")))
    src = tmp_path / "ep.mp4"
    src.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 2048)
    lib.attach_chapter_media(out.id, num="1", lang="EN", group="sub", src=src,
                             sidecar={"filename": "ep.mp4"})
    cid = lib.get(out.id).chapters[0].id
    stem = safe_id(cid)
    d = lib.vault._chapters_dir(out.id)
    (d / f"{stem}.poster.jpg").write_bytes(b"\xff\xd8\xff still")
    video = lib.vault.chapter_media_path(out.id, cid)
    before = video.read_bytes()

    assert lib.normalize_archives(force=True) == 0
    assert video.is_file() and video.read_bytes() == before
    assert (d / f"{stem}.poster.jpg").is_file()
    # and nothing was branded as a failed conversion
    assert "convertFailed" not in lib.vault.chapter_sidecars(out.id)[stem]
