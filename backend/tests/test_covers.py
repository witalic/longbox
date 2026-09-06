"""Covers: a title may wear several, and one that wears none shows its own media.

Both halves answer the same question — "what does this tile put on screen?" —
so they share one DTO field (`covers`). `ownCovers` answers the narrower one the
editor asks: did anybody give this title an image of its own.
"""
from __future__ import annotations

import base64
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.library.models import DraftIn, TitleMeta
from app.library.service import Library
from app.library.vault import MAX_COVERS
from app.main import create_app

PNG = b"\x89PNG\r\n\x1a\n-cover-one"
JPG = b"\xff\xd8\xff-cover-two"


@pytest.fixture
def lib(tmp_path):
    library = Library(tmp_path / "vault")
    yield library
    library.close()


@pytest.fixture
def client(lib):
    with TestClient(create_app(lib)) as c:
        yield c


def _title(client, **meta) -> str:
    return client.post("/api/titles", json={"meta": {"title": "T", **meta}}).json()["id"]


def _post_cover(client, tid: str, data: bytes, ct: str, append: bool = False):
    return client.post(f"/api/titles/{tid}/cover", json={
        "data": base64.b64encode(data).decode(), "contentType": ct, "append": append})


def _zip(path, pages=3):
    with zipfile.ZipFile(path, "w") as z:
        for i in range(pages):
            z.writestr(f"{i + 1:03}.jpg", f"page{i}".encode())
    return path


# ---- several covers ----

def test_a_second_cover_is_appended_not_a_replacement(client):
    tid = _title(client)
    _post_cover(client, tid, PNG, "image/png")
    t = _post_cover(client, tid, JPG, "image/jpeg", append=True).json()
    assert len(t["covers"]) == 2
    assert t["cover"] == t["covers"][0]
    assert client.get(t["covers"][0]).content == PNG
    assert client.get(t["covers"][1]).content == JPG


def test_the_first_cover_keeps_the_url_it_always_had(client, lib):
    """Every build before this one addressed the cover without a slot, and wrote
    exactly this string into the index. It has to stay that string.

    Two things ride on it. Every cached tile in the app is keyed by the URL, and
    — because a launch decides what to re-read by comparing the index's stored
    value against the one this build composes — a title whose cover URL changed
    shape would be re-read on the first launch after an update. On a library of
    a thousand titles on a network share, that is the difference between opening
    the app and waiting for it."""
    tid = _title(client)
    t = _post_cover(client, tid, PNG, "image/png").json()
    assert t["covers"][0] == f"/api/titles/{tid}/cover?v={t['covers'][0].split('v=')[1]}"
    assert "&i=" not in t["covers"][0]
    assert client.get(f"/api/titles/{tid}/cover").content == PNG
    # the index stores that same one URL — a list of one, which is what an older
    # build's single value already is
    assert lib.index.stamps()[tid][2] == t["covers"][0]


def test_appending_does_not_rewrite_the_first_covers_provenance(client):
    tid = _title(client)
    client.post(f"/api/titles/{tid}/cover", json={
        "data": base64.b64encode(PNG).decode(), "contentType": "image/png",
        "sourceUrl": "https://site/first.png"})
    t = _post_cover(client, tid, JPG, "image/jpeg", append=True).json()
    assert t["coverSource"] == "https://site/first.png"


def test_deleting_one_cover_closes_the_gap(client):
    tid = _title(client)
    _post_cover(client, tid, PNG, "image/png")
    _post_cover(client, tid, JPG, "image/jpeg", append=True)
    t = client.delete(f"/api/titles/{tid}/cover", params={"i": 0}).json()
    assert len(t["covers"]) == 1
    # the survivor moved into the first slot: "the cover" is one file, always
    assert client.get(f"/api/titles/{tid}/cover").content == JPG
    assert client.get(t["covers"][0]).content == JPG


def test_deleting_with_no_index_removes_every_cover(client):
    tid = _title(client)
    _post_cover(client, tid, PNG, "image/png")
    _post_cover(client, tid, JPG, "image/jpeg", append=True)
    t = client.delete(f"/api/titles/{tid}/cover").json()
    assert t["covers"] == [] and t["cover"] == "" and t["coverSource"] == ""


def test_reordering_moves_the_bytes_not_just_a_list(client):
    tid = _title(client)
    _post_cover(client, tid, PNG, "image/png")
    _post_cover(client, tid, JPG, "image/jpeg", append=True)
    t = client.put(f"/api/titles/{tid}/covers", json={"order": [1, 0]}).json()
    assert client.get(t["covers"][0]).content == JPG
    # and the plain URL follows, because it means "the first cover"
    assert client.get(f"/api/titles/{tid}/cover").content == JPG


def test_a_reorder_that_names_too_few_positions_still_loses_nothing(client):
    tid = _title(client)
    for blob, ct in ((PNG, "image/png"), (JPG, "image/jpeg"), (b"GIF89a-three", "image/gif")):
        _post_cover(client, tid, blob, ct, append=bool(client.get(f"/api/titles/{tid}").json()["covers"]))
    t = client.put(f"/api/titles/{tid}/covers", json={"order": [2]}).json()
    assert len(t["covers"]) == 3
    assert client.get(t["covers"][0]).content == b"GIF89a-three"


def test_a_title_stops_taking_covers_at_the_cap(client):
    tid = _title(client)
    _post_cover(client, tid, PNG, "image/png")
    for i in range(MAX_COVERS + 3):
        _post_cover(client, tid, f"\x89PNG\r\n\x1a\n-{i}".encode(), "image/png", append=True)
    assert len(client.get(f"/api/titles/{tid}").json()["covers"]) == MAX_COVERS


def test_covers_survive_a_rebuilt_index(client, lib):
    """The order is the file names, so nothing about it lives in the cache."""
    tid = _title(client)
    _post_cover(client, tid, PNG, "image/png")
    _post_cover(client, tid, JPG, "image/jpeg", append=True)
    lib.rescan()
    t = client.get(f"/api/titles/{tid}").json()
    assert len(t["covers"]) == 2
    assert client.get(t["covers"][1]).content == JPG


def test_a_gap_in_the_slots_is_filled_not_written_over(client, lib):
    """The vault is the source of truth, so covers can arrive by hand — and an
    interrupted reorder can leave a gap too. "How many are there" would then
    name a slot that already holds one, and adding a cover would delete it."""
    tid = _title(client)
    _post_cover(client, tid, PNG, "image/png")          # slot 0
    d = lib.vault.cover_path(tid).parent
    (d / "cover-3.jpg").write_bytes(JPG)                # a gap: slots 0 and 2
    t = _post_cover(client, tid, b"GIF89a-added", "image/gif", append=True).json()
    assert len(t["covers"]) == 3
    assert (d / "cover-3.jpg").read_bytes() == JPG      # untouched
    assert sorted(p.name for p in lib.vault.cover_paths(tid)) == [
        "cover-2.gif", "cover-3.jpg", "cover.png"]


def test_two_covers_of_one_title_are_never_served_from_one_cache_entry(client):
    """Same title, same extension, same size, and — on Windows — the same
    timestamp to the nanosecond. Everything the old cover version was built
    from is identical between them; only which one it is differs."""
    tid = _title(client)
    red, blue = JPG + b"R" * 64, JPG + b"B" * 64
    _post_cover(client, tid, red, "image/jpeg")
    _post_cover(client, tid, blue, "image/jpeg", append=True)
    shown = client.get(f"/api/titles/{tid}").json()["covers"]
    assert (client.get(f"{shown[0]}&w=120").content
            != client.get(f"{shown[1]}&w=120").content)


def test_a_title_nobody_touched_is_not_re_read_on_the_next_launch(client, lib):
    """Two places compose a title's cover URLs: the write path, from the files
    it just wrote, and the vault SCAN every launch runs. They have to produce
    the same string. If they disagree the launch finds this title stale — every
    launch, forever — and re-reads a document nothing has changed."""
    tid = _title(client)
    _post_cover(client, tid, PNG, "image/png")
    _post_cover(client, tid, JPG, "image/jpeg", append=True)
    assert lib.sync() == 0
    client.put(f"/api/titles/{tid}/covers", json={"order": [1, 0]})
    assert lib.sync() == 0
    client.delete(f"/api/titles/{tid}/cover", params={"i": 0})
    assert lib.sync() == 0


# ---- a title with no cover of its own ----

def test_a_coverless_chapter_title_stands_in_with_its_first_page(client, lib, tmp_path):
    tid = _title(client)
    lib.attach_chapter_media(tid, num="1", lang="EN", group="g",
                             src=_zip(tmp_path / "a.zip"), sidecar={})
    t = client.get(f"/api/titles/{tid}").json()
    cid = t["chapters"][0]["id"]
    assert t["covers"] == [f"/api/titles/{tid}/chapters/{cid}/pages/0?v={t['chapters'][0]['v']}"]
    assert client.get(t["covers"][0]).status_code == 200
    # …but nobody GAVE it a cover, and the editor must still be able to tell
    assert t["ownCovers"] == []


def test_a_stored_cover_wins_over_what_the_media_could_show(client, lib, tmp_path):
    tid = _title(client)
    lib.attach_chapter_media(tid, num="1", lang="EN", group="g",
                             src=_zip(tmp_path / "a.zip"), sidecar={})
    t = _post_cover(client, tid, PNG, "image/png").json()
    assert t["covers"] == t["ownCovers"]
    assert len(t["covers"]) == 1 and t["covers"][0].startswith(f"/api/titles/{tid}/cover?v=")


def test_each_chapter_offers_one_stand_in_and_the_list_is_capped(client, lib, tmp_path):
    tid = _title(client)
    for n in range(9):
        lib.attach_chapter_media(tid, num=str(n + 1), lang="EN", group="g",
                                 src=_zip(tmp_path / f"{n}.zip"), sidecar={})
    covers = client.get(f"/api/titles/{tid}").json()["covers"]
    assert 1 < len(covers) <= 6
    assert len(set(covers)) == len(covers)


def test_a_chapter_with_no_media_stands_in_with_nothing(client):
    tid = client.post("/api/titles", json={
        "meta": {"title": "T"}, "chapters": [{"id": "", "num": "1"}]}).json()["id"]
    assert client.get(f"/api/titles/{tid}").json()["covers"] == []
