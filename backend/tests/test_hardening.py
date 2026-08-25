"""Data-safety fixes from the refactor pass: reconcile adoption edge cases,
non-zip archive guards, junk-entry preservation, and migration corner cases."""
from __future__ import annotations

import io
import json
import os
import shutil
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.library import media
from app.library.models import ChapterRow, DraftIn, TitleMeta
from app.library.service import Library
from app.library.vault import Vault
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


def _img(name="p.jpg"):
    return ("files", (name, io.BytesIO(b"img-bytes"), "image/jpeg"))


# ---- reconcile: one old row must never satisfy two adoptions ----

def test_recapture_with_two_uploads_keeps_both_rows(lib):
    """Old row (url=u1) + a re-captured draft carrying BOTH u1 and a genuinely
    different upload u2 of the same number: u1 adopts the old id, u2 survives
    as its own row — it must not be dropped as a phantom twin."""
    lib.create(DraftIn(meta=TitleMeta(title="X"),
                       chapters=[ChapterRow(id="old-1", num="1", url="u1")]))
    out = lib.commit("x", DraftIn(meta=TitleMeta(title="X"), chapters=[
        ChapterRow(id="fresh-a", num="1", url="u1"),
        ChapterRow(id="fresh-b", num="1", url="u2"),
    ]))
    assert [(c.id, c.url) for c in out.chapters] == [("old-1", "u1"), ("fresh-b", "u2")]


def test_reconcile_restores_row_with_archive_but_no_sidecar(lib, tmp_path):
    """A crash can leave a chapter's zip without its sidecar — a commit from a
    stale draft must still treat that row as media-backed and put it back."""
    lib.create(DraftIn(meta=TitleMeta(title="X"),
                       chapters=[ChapterRow(id="ch-1", num="1")]))
    src = tmp_path / "dl.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("001.jpg", b"img")
    lib.attach_chapter_media("x", num="1", lang="", group="",
                             src=src, sidecar={"pages": 1})
    lib.vault._chapters_dir("x").joinpath("ch-1.json").unlink()  # the crash
    out = lib.commit("x", DraftIn(meta=TitleMeta(title="X"), chapters=[]))
    assert [c.id for c in out.chapters] == ["ch-1"]


# ---- page ops refuse to edit what they can't rewrite ----

@pytest.fixture
def client(tmp_path):
    lib = Library(tmp_path / "lib")
    with TestClient(create_app(lib)) as c:
        c.post("/api/titles", json={
            "meta": {"title": "Berserk"},
            "chapters": [{"id": "b-5-en", "num": "5", "lang": "EN", "group": "dex"}],
        })
        yield c
    lib.close()


def _chapters_dir(tmp_path):
    return tmp_path / "lib" / "other" / "berserk" / "chapters"


def test_page_ops_refuse_non_zip_archive(client, tmp_path):
    d = _chapters_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "b-5-en.rar").write_bytes(b"not-a-zip-at-all")
    (d / "b-5-en.json").write_text(json.dumps({"pages": 0}), encoding="utf-8")
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/add", files=[_img()])
    assert r.status_code == 409
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/reorder", json={"order": []})
    assert r.status_code == 409
    # the opaque archive is untouched
    assert (d / "b-5-en.rar").read_bytes() == b"not-a-zip-at-all"


def test_page_rewrites_preserve_non_image_entries(client, tmp_path):
    d = _chapters_dir(tmp_path)
    d.mkdir(parents=True)
    with zipfile.ZipFile(d / "b-5-en.zip", "w") as z:
        z.writestr("p1.jpg", b"one")
        z.writestr("ComicInfo.xml", b"<info/>")
    (d / "b-5-en.json").write_text(json.dumps({"pages": 1}), encoding="utf-8")
    assert client.post("/api/titles/berserk/chapters/b-5-en/pages/add", files=[_img()]).status_code == 200
    assert client.post("/api/titles/berserk/chapters/b-5-en/pages/reorder", json={"order": [1, 0]}).status_code == 200
    with zipfile.ZipFile(d / "b-5-en.zip") as z:
        names = z.namelist()
    assert "ComicInfo.xml" in names
    assert len([n for n in names if n.endswith(".jpg")]) == 2


def test_deleting_every_page_removes_the_archive(client, tmp_path):
    d = _chapters_dir(tmp_path)
    d.mkdir(parents=True)
    with zipfile.ZipFile(d / "b-5-en.zip", "w") as z:
        z.writestr("p1.jpg", b"one")
    (d / "b-5-en.json").write_text(json.dumps({"pages": 1}), encoding="utf-8")
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/delete", json={"indices": [0]})
    assert r.status_code == 200
    ch = next(c for c in r.json()["chapters"] if c["id"] == "b-5-en")
    assert ch["dl"] is False and ch["pages"] == 0
    assert not (d / "b-5-en.zip").exists() and not (d / "b-5-en.json").exists()


# ---- the zip invariant: everything stored in the vault is a plain zip ----

def _make_7z(path, entries):
    py7zr = pytest.importorskip("py7zr")
    with py7zr.SevenZipFile(path, "w") as z:
        for name, data in entries:
            import io as _io
            z.writef(_io.BytesIO(data), name)
    return path


def test_ingest_converts_7z_to_zip(lib, tmp_path):
    lib.create(DraftIn(meta=TitleMeta(title="X"), chapters=[ChapterRow(id="ch-1", num="1")]))
    src = _make_7z(tmp_path / "dl.7z", [("p1.jpg", b"one"), ("p2.jpg", b"two"), ("info.txt", b"junk")])
    out = lib.attach_chapter_media("x", num="1", lang="", group="", src=src, sidecar={"filename": "dl.7z"})
    ch = next(c for c in out.chapters if c.id == "ch-1")
    assert ch.dl is True and ch.pages == 2
    stored = lib.vault.chapter_media_path("x", "ch-1")
    assert stored is not None and stored.suffix == ".zip"
    with zipfile.ZipFile(stored) as z:
        assert sorted(z.namelist()) == ["info.txt", "p1.jpg", "p2.jpg"]


def test_ingest_stores_cbz_under_the_zip_name(lib, tmp_path):
    lib.create(DraftIn(meta=TitleMeta(title="X"), chapters=[ChapterRow(id="ch-1", num="1")]))
    src = tmp_path / "dl.cbz"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("p1.jpg", b"one")
    out = lib.attach_chapter_media("x", num="1", lang="", group="", src=src, sidecar={})
    ch = next(c for c in out.chapters if c.id == "ch-1")
    assert ch.pages == 1
    assert lib.vault.chapter_media_path("x", "ch-1").name == "ch-1.zip"


def test_import_rejects_unreadable_archive(client, tmp_path):
    r = client.post("/api/titles/berserk/chapters/import?num=5&lang=EN&group=dex&filename=x.rar",
                    content=b"garbage-that-is-no-archive")
    assert r.status_code == 422
    # nothing opaque was stored
    assert not _chapters_dir(tmp_path).exists() or not list(_chapters_dir(tmp_path).glob("*.rar"))


def _settle(lib):
    if lib._normalize_thread is not None:
        lib._normalize_thread.join()
    return lib


def test_unmarked_vault_is_normalized_once_on_open(tmp_path):
    lib = _settle(Library(tmp_path))
    lib.create(DraftIn(meta=TitleMeta(title="X"), chapters=[
        ChapterRow(id="ch-1", num="1"), ChapterRow(id="ch-2", num="2")]))
    d = lib.vault._chapters_dir("x")
    d.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(d / "ch-1.cbz", "w") as z:  # legacy cbz
        z.writestr("p1.jpg", b"one")
    (d / "ch-1.json").write_text(json.dumps({"pages": 0}), encoding="utf-8")
    _make_7z(d / "ch-2.7z", [("p1.jpg", b"one"), ("p2.jpg", b"two")])  # legacy 7z
    (d / "ch-2.json").write_text(json.dumps({"pages": 0}), encoding="utf-8")
    lib.close()
    (tmp_path / "vault.json").unlink(missing_ok=True)  # a vault from before the sweep existed

    lib = _settle(Library(tmp_path))  # the one-time pass runs in the background
    assert (d / "ch-1.zip").is_file() and not (d / "ch-1.cbz").exists()
    assert (d / "ch-2.zip").is_file() and not (d / "ch-2.7z").exists()
    chapters = {c.id: c for c in lib.get("x").chapters}
    assert chapters["ch-1"].pages == 1 and chapters["ch-1"].dl is True
    assert chapters["ch-2"].pages == 2 and chapters["ch-2"].dl is True
    lib.close()


def test_normalized_vault_is_not_swept_again(tmp_path):
    """Ingest keeps the invariant, so the sweep is a migration — a marked vault
    must not re-scan every launch. Hand-dropped files wait for the manual run."""
    lib = _settle(Library(tmp_path))
    lib.create(DraftIn(meta=TitleMeta(title="X"), chapters=[ChapterRow(id="ch-1", num="1")]))
    d = lib.vault._chapters_dir("x")
    d.mkdir(parents=True, exist_ok=True)
    lib.close()
    with zipfile.ZipFile(d / "ch-1.cbz", "w") as z:  # dropped in from outside
        z.writestr("p1.jpg", b"one")

    lib = Library(tmp_path)
    assert lib._normalize_thread is None      # the marker says this vault is done
    assert (d / "ch-1.cbz").is_file()         # startup left it alone
    assert lib.normalize_archives(force=True) == 1  # the Settings action converts it
    assert (d / "ch-1.zip").is_file() and not (d / "ch-1.cbz").exists()
    lib.close()


def test_unreadable_file_is_left_alone_without_faking_a_sidecar(tmp_path):
    """A file nothing can read must stay untouched AND stay invisible: writing a
    sidecar for it would make the UI report a downloaded chapter with no pages."""
    lib = Library(tmp_path)
    lib.create(DraftIn(meta=TitleMeta(title="X"), chapters=[ChapterRow(id="ch-1", num="1")]))
    d = lib.vault._chapters_dir("x")
    d.mkdir(parents=True, exist_ok=True)
    (d / "ch-1.rar").write_bytes(b"unreadable-garbage")
    assert lib.vault.normalize_chapter_archives() == 0
    assert not (d / "ch-1.json").exists()
    assert (d / "ch-1.rar").read_bytes() == b"unreadable-garbage"
    assert next(c for c in lib.get("x").chapters if c.id == "ch-1").dl is False
    # replacing it with something readable converts it on the next pass
    with zipfile.ZipFile(d / "ch-1.rar", "w") as z:  # actually zip bytes now
        z.writestr("p1.jpg", b"img")
    assert lib.vault.normalize_chapter_archives() == 1
    assert (d / "ch-1.zip").is_file() and not (d / "ch-1.rar").exists()
    lib.close()


def test_failed_conversion_is_remembered_for_a_known_chapter(tmp_path):
    """When the chapter HAS a sidecar, the failure is recorded against the exact
    file so later passes don't re-attempt the same hopeless conversion."""
    lib = Library(tmp_path)
    lib.create(DraftIn(meta=TitleMeta(title="X"), chapters=[ChapterRow(id="ch-1", num="1")]))
    d = lib.vault._chapters_dir("x")
    d.mkdir(parents=True, exist_ok=True)
    (d / "ch-1.rar").write_bytes(b"unreadable-garbage")
    (d / "ch-1.json").write_text(json.dumps({"pages": 0}), encoding="utf-8")
    assert lib.vault.normalize_chapter_archives() == 0
    side = json.loads((d / "ch-1.json").read_text(encoding="utf-8"))
    assert side["convertFailed"]
    assert lib.vault.normalize_chapter_archives() == 0  # skipped, not retried
    lib.close()


def _webp(mode="RGB", color=(200, 30, 90)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new(mode, (4, 6), color).save(buf, "WEBP")
    return buf.getvalue()


def test_added_webp_pages_convert_to_standard_format(client, tmp_path):
    from PIL import Image
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/add", files=[
        ("files", ("p1.webp", io.BytesIO(_webp()), "image/webp")),
        ("files", ("p2.webp", io.BytesIO(_webp("RGBA", (10, 20, 30, 0))), "image/webp")),
    ])
    assert r.status_code == 200
    with zipfile.ZipFile(_chapters_dir(tmp_path) / "b-5-en.zip") as z:
        names = z.namelist()
        assert names == ["001.jpg", "002.png"]  # opaque → jpg, alpha → png
        assert Image.open(io.BytesIO(z.read("001.jpg"))).format == "JPEG"
        assert Image.open(io.BytesIO(z.read("002.png"))).format == "PNG"


# ---- unpacked media: single-image downloads accumulate into the chapter zip ----

def _complete_download(c, tmp_path, *, num, filename, data):
    src = tmp_path / filename
    src.write_bytes(data)
    assert c.post("/api/downloads/arm",
                  json={"titleId": "berserk", "num": num, "lang": "EN", "group": "dex"}).status_code == 200
    started = c.post("/api/downloads/start", json={
        "filename": filename, "fileUrl": f"https://cdn.site/{filename}", "pageUrl": "https://site/p",
    })
    assert started.status_code == 200
    return c.post(f"/api/downloads/{started.json()['id']}/complete", json={"path": str(src)})


def test_image_downloads_append_as_pages(client, tmp_path):
    r1 = _complete_download(client, tmp_path, num="5", filename="page-1.jpg", data=b"img-one")
    assert r1.status_code == 200
    r2 = _complete_download(client, tmp_path, num="5", filename="page-2.png", data=b"img-two")
    assert r2.status_code == 200
    ch = next(c for c in r2.json()["chapters"] if c["num"] == "5")
    assert ch["dl"] is True and ch["pages"] == 2
    stored = _chapters_dir(tmp_path) / "b-5-en.zip"
    with zipfile.ZipFile(stored) as z:
        names = sorted(z.namelist())
    assert names == ["001.jpg", "002.png"]  # appended = last, renumbered
    # the sidecar records the LATEST source
    side = json.loads((_chapters_dir(tmp_path) / "b-5-en.json").read_text(encoding="utf-8"))
    assert side["fileUrl"].endswith("page-2.png") and side["pages"] == 2


def test_download_of_unsupported_file_fails_cleanly(client, tmp_path):
    r = _complete_download(client, tmp_path, num="5", filename="video.mp4", data=b"not-media")
    assert r.status_code == 422
    items = client.get("/api/downloads").json()["items"]
    assert items[0]["state"] == "failed" and "unsupported" in items[0]["error"]
    assert not list(_chapters_dir(tmp_path).glob("*")) if _chapters_dir(tmp_path).exists() else True


# ---- page capture: reader pages accumulate, revisits cost nothing ----

def _png(color=(9, 9, 9)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 6), color).save(buf, "PNG")
    return buf.getvalue()


def _capture(c, *, images, chapter="b-5-en", page_url="https://site/read/5"):
    import base64
    return c.post(f"/api/titles/berserk/chapters/{chapter}/pages/capture", json={
        "pageUrl": page_url,
        "images": [{"key": k, "url": u, "data": base64.b64encode(d).decode(), "contentType": ct}
                   for k, u, d, ct in images],
    })


def _known(c, keys, chapter="b-5-en"):
    return c.post(f"/api/titles/berserk/chapters/{chapter}/pages/known", json={"keys": keys})


def test_page_capture_accumulates_and_skips_known_pages(client, tmp_path):
    r = _capture(client, images=[
        ("01.png", "https://cdn/5/01.png?t=aaa", _png(), "image/png"),
        ("02.webp", "https://cdn/5/02.webp?t=aaa", _webp(), "image/webp"),
    ])
    assert r.status_code == 200
    ch = next(c for c in r.json()["chapters"] if c["id"] == "b-5-en")
    assert ch["dl"] is True and ch["pages"] == 2

    # the client asks what is already stored BEFORE fetching anything
    assert _known(client, ["01.png", "03.png"]).json() == ["01.png"]

    # a revisit hands the SAME names behind rotated CDN tokens — nothing is
    # stored twice; only the genuinely new page appends
    r = _capture(client, images=[
        ("01.png", "https://cdn/5/01.png?t=zzz", _png(), "image/png"),
        ("03.png", "https://cdn/5/03.png?t=zzz", _png(), "image/png"),
    ])
    ch = next(c for c in r.json()["chapters"] if c["id"] == "b-5-en")
    assert ch["pages"] == 3
    with zipfile.ZipFile(_chapters_dir(tmp_path) / "b-5-en.zip") as z:
        # webp converted on the way in; order is capture order
        assert z.namelist() == ["001.png", "002.jpg", "003.png"]
    side = json.loads((_chapters_dir(tmp_path) / "b-5-en.json").read_text(encoding="utf-8"))
    assert side["pageKeys"] == ["01.png", "02.webp", "03.png"]
    assert side["importedFrom"] == "page-capture"


def test_page_capture_needs_an_existing_row(client):
    r = _capture(client, chapter="no-such-row", images=[("1.png", "", _png(), "image/png")])
    assert r.status_code == 404


def test_known_pages_ignores_damaged_media(client, tmp_path):
    _capture(client, images=[("01.png", "", _png(), "image/png")])
    (_chapters_dir(tmp_path) / "b-5-en.zip").write_bytes(b"corrupted")
    assert _known(client, ["01.png"]).json() == []  # unreadable → re-capture, don't trust


# ---- the sidecar's page keys travel with the pages ----

def _add_row(c, chapter_id, num):
    """A bare chapter row — the UI creates one through a meta commit."""
    rows = [{k: ch[k] for k in ("id", "num", "title", "url", "lang", "group", "date")}
            for ch in c.get("/api/titles/berserk").json()["chapters"]]
    rows.append({"id": chapter_id, "num": num, "title": "", "url": "",
                 "lang": "EN", "group": "dex", "date": ""})
    r = c.put("/api/titles/berserk", json={"meta": {"title": "Berserk"}, "chapters": rows})
    assert r.status_code == 200
    return next(ch for ch in r.json()["chapters"] if ch["id"] == chapter_id)


def test_page_keys_follow_deleted_and_moved_pages(client, tmp_path):
    other = _add_row(client, "b-6-en", "6")
    _capture(client, images=[
        ("01.png", "", _png(), "image/png"),
        ("02.png", "", _png(), "image/png"),
        ("03.png", "", _png(), "image/png"),
    ])
    side = lambda ch: json.loads((_chapters_dir(tmp_path) / f"{ch}.json").read_text(encoding="utf-8"))

    # deleting a page drops ITS key, so re-reading that page captures it again
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/delete", json={"indices": [1]})
    assert r.status_code == 200
    assert side("b-5-en")["pageKeys"] == ["01.png", "03.png"]
    assert _known(client, ["02.png"]).json() == []

    # moving a page carries its key to the destination
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/move",
                    json={"to": other["id"], "indices": [0]})
    assert r.status_code == 200
    assert side("b-5-en")["pageKeys"] == ["03.png"]
    assert side(other["id"])["pageKeys"] == ["01.png"]
    assert _known(client, ["01.png"]).json() == []  # the source no longer claims it


def test_reordering_pages_keeps_keys_aligned(client, tmp_path):
    _capture(client, images=[("01.png", "", _png(), "image/png"), ("02.png", "", _png(), "image/png")])
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/reorder", json={"order": [1, 0]})
    assert r.status_code == 200
    side = json.loads((_chapters_dir(tmp_path) / "b-5-en.json").read_text(encoding="utf-8"))
    assert side["pageKeys"] == ["02.png", "01.png"]


def test_capture_stores_the_format_the_bytes_have(client, tmp_path):
    """A CDN mislabelling WebP as image/jpeg must not produce a broken .jpg."""
    import base64
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (4, 6), (1, 2, 3)).save(buf, "WEBP")
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/capture", json={
        "images": [{"key": "p.jpg", "url": "https://cdn/p.jpg",
                    "data": base64.b64encode(buf.getvalue()).decode(), "contentType": "image/jpeg"}],
    })
    assert r.status_code == 200
    with zipfile.ZipFile(_chapters_dir(tmp_path) / "b-5-en.zip") as z:
        # sniffed as webp → converted to the standard format, never stored as a lying .jpg
        assert z.namelist() == ["001.jpg"]
        assert Image.open(io.BytesIO(z.read("001.jpg"))).format == "JPEG"


def test_capture_refuses_an_oversized_batch(client):
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/capture", json={
        "images": [{"key": f"{i}.png", "data": "", "contentType": "image/png"} for i in range(40)],
    })
    assert r.status_code == 413


# ---- migration corner case: a legacy title dir named like its own shelf ----

def test_migration_handles_title_named_like_its_shelf(tmp_path):
    legacy = tmp_path / "manga"
    legacy.mkdir(parents=True)
    (legacy / "title.json").write_text(json.dumps({
        "schema": 1, "meta": {"title": "Manga", "type": "manga"},
        "provenance": {}, "chapters": [], "user": {},
    }), encoding="utf-8")
    lib = Library(tmp_path)
    assert (tmp_path / "manga" / "manga" / "title.json").is_file()
    assert lib.get("manga").title == "Manga"
    lib.close()


# ---- startup cost: the index SYNCS, it never re-reads the whole library ----

def _one_page_zip(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("001.jpg", b"one")
    return path


def test_launch_reindexes_only_what_changed(tmp_path, monkeypatch):
    lib = Library(tmp_path)
    for name in ("One", "Two", "Three"):
        lib.create(DraftIn(meta=TitleMeta(title=name)))
    lib.close()

    reads: list[str] = []
    real_load = Vault.load

    def counting_load(self, title_id):
        reads.append(title_id)
        return real_load(self, title_id)

    monkeypatch.setattr(Vault, "load", counting_load)
    lib = Library(tmp_path)  # nothing moved since the index was written
    assert reads == []
    assert lib.count() == 3
    lib.close()

    # a document edited behind the app's back is the ONE title read again
    doc_path = next(tmp_path.rglob("two/title.json"))
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    doc["meta"]["desc"] = "edited on disk"
    doc_path.write_text(json.dumps(doc), encoding="utf-8")

    reads.clear()
    lib = Library(tmp_path)
    assert reads == ["two"]
    assert lib.get("two").desc == "edited on disk"
    lib.close()


def test_launch_forgets_a_title_deleted_on_disk(tmp_path):
    lib = Library(tmp_path)
    lib.create(DraftIn(meta=TitleMeta(title="Gone")))
    lib.create(DraftIn(meta=TitleMeta(title="Stays")))
    lib.close()

    shutil.rmtree(next(tmp_path.rglob("gone/title.json")).parent)
    lib = Library(tmp_path)
    assert [t.title for t in lib.query()] == ["Stays"]
    lib.close()


def test_launch_notices_chapter_media_added_on_disk(tmp_path):
    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk")))
    lib.attach_chapter_media(out.id, num="5", lang="EN", group="dex",
                             src=_one_page_zip(tmp_path / "in.zip"), sidecar={})
    lib.close()
    # the chapter directory moving is half of "did anything change" — a document
    # untouched since the last launch must not hide new media
    lib = Library(tmp_path)
    assert lib.query()[0].chapters[0].pages == 1
    lib.close()


def test_a_listing_reads_no_chapter_sidecars(tmp_path, monkeypatch):
    """Page counts in a listing come from the index, not from one JSON file per
    chapter — that pass is what made a big library take seconds to appear."""
    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk")))
    lib.attach_chapter_media(out.id, num="5", lang="EN", group="dex",
                             src=_one_page_zip(tmp_path / "in.zip"), sidecar={})
    lib.close()

    lib = Library(tmp_path)
    calls: list[str] = []
    real = Vault.chapter_sidecars
    monkeypatch.setattr(Vault, "chapter_sidecars",
                        lambda self, tid: (calls.append(tid), real(self, tid))[1])
    rows = lib.query()
    assert rows[0].chapters[0].pages == 1  # composed from the indexed media
    assert calls == []
    lib.close()


def test_background_sync_never_overwrites_a_newer_write(tmp_path):
    """The verification pass does not hold the title locks, so it writes only
    while the stamps it SAW still hold: a row rewritten in the meantime (a
    chapter deleted, say, which leaves title.json untouched) must survive."""
    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk", desc="current")))
    doc, media, cover = lib.index.get(out.id)
    touched, media_at, cover_stamp = lib.index.stamps()[out.id]
    assert cover_stamp == cover

    stale = doc.model_copy(deep=True)
    stale.meta.desc = "what the scan read before the change"
    # the scan saw different stamps than the row now carries
    lib.index.upsert_many([(out.id, stale, touched, media, media_at, cover,
                            (touched - 1, media_at, cover))])
    assert lib.get(out.id).desc == "current"
    lib.index.upsert_many([(out.id, stale, touched, media, media_at, cover,
                            (touched, media_at - 1, cover))])
    lib.index.upsert_many([(out.id, stale, touched, media, media_at, cover,
                            (touched, media_at, "a-different-cover"))])
    assert lib.get(out.id).desc == "current"
    assert lib.get(out.id).desc == "current"

    # …and applies when nothing moved under it
    lib.index.upsert_many([(out.id, stale, touched, media, media_at, cover,
                            (touched, media_at, cover))])
    assert lib.get(out.id).desc == "what the scan read before the change"
    lib.close()


def test_a_vault_replaced_with_older_files_is_still_picked_up(tmp_path, monkeypatch):
    """Restoring a backup (or swapping the folder for another copy) leaves files
    whose mtimes are OLDER than the ones indexed. That is still a change."""
    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk", desc="first")))
    lib.close()

    doc_path = next(tmp_path.rglob("berserk/title.json"))
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    doc["meta"]["desc"] = "restored from a backup"
    doc_path.write_text(json.dumps(doc), encoding="utf-8")
    old = 10**9  # an mtime far in the past, exactly as a restored copy carries
    os.utime(doc_path, ns=(old, old))

    lib = Library(tmp_path)
    assert lib.get(out.id).desc == "restored from a backup"
    lib.close()


def test_a_write_is_never_composed_from_a_cached_sidecar(tmp_path):
    """Two changes can land inside one directory-mtime tick, so a cache keyed on
    that mtime can answer a write with the state from before it."""
    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk")))
    lib.attach_chapter_media(out.id, num="5", lang="EN", group="dex",
                             src=_one_page_zip(tmp_path / "in.zip"), sidecar={})
    chapter_id = lib.get(out.id).chapters[0].id
    # freeze the directory clock: without invalidation the cache would hold
    stamp = lib.vault.chapters_stamp(out.id)
    monkey = lambda self, tid: stamp  # noqa: E731
    original = type(lib.vault).chapters_stamp
    type(lib.vault).chapters_stamp = monkey
    try:
        after = lib.delete_chapter_media(out.id, chapter_id)
        assert after.chapters[0].dl is False and after.chapters[0].pages == 0
    finally:
        type(lib.vault).chapters_stamp = original
    lib.close()


def _solid(color: tuple[int, int, int]) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (300, 450), color).save(buf, "JPEG")
    return buf.getvalue()


def test_edited_pages_are_never_served_from_the_previous_version(tmp_path):
    """The pages a chapter serves are cached — in the browser by URL, on disk by
    the same version. A chapter whose sidecar predates the revision counter
    starts it at 1, which can equal the version it is ALREADY cached under, and
    the reader then shows pages that were deleted."""
    from PIL import Image

    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk")))
    src = tmp_path / "in.zip"
    colors = [(200, 0, 0), (0, 200, 0), (0, 0, 200), (200, 200, 0)]
    with zipfile.ZipFile(src, "w") as z:  # source names, as page capture leaves them
        for name, color in zip(("10.png", "37.png", "20.png", "31.png"), colors):
            z.writestr(name, _solid(color))
    lib.attach_chapter_media(out.id, num="1", lang="EN", group="dex", src=src, sidecar={})
    cid = lib.get(out.id).chapters[0].id

    side_path = lib.vault.chapters_dir(out.id) / f"{cid}.json"
    side = json.loads(side_path.read_text(encoding="utf-8"))
    del side["rev"]  # the shape every chapter in a real vault has today
    side_path.write_text(json.dumps(side), encoding="utf-8")
    lib._sidecar_cache.clear()
    lib.sync()

    with TestClient(create_app(lib)) as c:
        def shown() -> list[tuple[int, int, int]]:
            pages = c.get(f"/api/titles/{out.id}").json()["chapters"][0]["pages"]
            out_px = []
            for i in range(pages):
                r = c.get(f"/api/titles/{out.id}/chapters/{cid}/pages/{i}?w=160&cap=1.5")
                out_px.append(Image.open(io.BytesIO(r.content)).convert("RGB").getpixel((80, 40)))
            return out_px

        before = shown()
        assert len(before) == 4
        c.post(f"/api/titles/{out.id}/chapters/{cid}/pages/delete", json={"indices": [0, 1]})
        after = shown()
        assert len(after) == 2
        # what is served now is what SURVIVED — the pages that were at 2 and 3,
        # not the ones the cache holds for index 0 and 1
        near = lambda a, b: all(abs(x - y) < 12 for x, y in zip(a, b))  # noqa: E731
        assert near(after[0], before[2]) and near(after[1], before[3])
    lib.close()


def test_page_edits_change_the_chapter_version(tmp_path):
    """Pages are cached in the browser by the version in their URL. Deleting two
    pages and adding two leaves the COUNT unchanged, so a count-based version
    served yesterday's images in the reader while the editor showed the new
    ones."""
    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk")))
    src = tmp_path / "in.zip"
    with zipfile.ZipFile(src, "w") as z:
        for i in range(4):
            z.writestr(f"{i:03d}.jpg", b"page-%d" % i)
    lib.attach_chapter_media(out.id, num="5", lang="EN", group="dex", src=src, sidecar={})
    chapter = lib.get(out.id).chapters[0]
    assert chapter.v and chapter.pages == 4
    before = chapter.v

    lib.delete_chapter_pages(out.id, chapter.id, [0, 1])
    after_delete = lib.get(out.id).chapters[0]
    assert after_delete.v != before

    lib.add_chapter_pages(out.id, chapter.id, [(b"new-a", ".jpg"), (b"new-b", ".jpg")])
    after_add = lib.get(out.id).chapters[0]
    # back to four pages — the count says nothing, the version must
    assert after_add.pages == 4
    assert after_add.v not in (before, after_delete.v)
    lib.close()


def test_a_replaced_cover_is_never_served_from_the_old_one(tmp_path, monkeypatch):
    """Covers are cached under (mtime, size). A replacement of the same size
    inside one filesystem tick — plausible on a share that rounds timestamps —
    would otherwise reuse the key and keep serving the picture that is gone."""
    from PIL import Image

    from app.library import vault as vault_mod

    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk")))
    lib.set_cover(out.id, _solid((200, 0, 0)), "jpg", "")
    frozen = lib.vault.cover_path(out.id).stat().st_mtime_ns

    real_write = vault_mod._atomic_write

    def write_and_freeze(path, data):
        real_write(path, data)
        os.utime(path, ns=(frozen, frozen))

    monkeypatch.setattr(vault_mod, "_atomic_write", write_and_freeze)
    with TestClient(create_app(lib)) as c:
        def shown():
            url = c.get(f"/api/titles/{out.id}").json()["cover"]
            r = c.get(f"{url}&w=160")
            return Image.open(io.BytesIO(r.content)).convert("RGB").getpixel((80, 80))

        assert shown()[0] > 150  # red
        lib.set_cover(out.id, _solid((0, 0, 200)), "jpg", "")
        lib._index(out.id, lib.vault.load(out.id))
        assert shown()[2] > 150  # blue — the new cover, not the cached one
    lib.close()


def test_the_sweep_never_replaces_a_stored_chapter(tmp_path):
    """A stray `ch-1.cbz` dropped beside the stored `ch-1.zip` is not this
    chapter's archive — converting it would swap the pages the user captured
    for whatever was dropped in."""
    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk")))
    src = tmp_path / "in.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("001.jpg", b"the captured page")
    lib.attach_chapter_media(out.id, num="1", lang="EN", group="dex", src=src, sidecar={})
    cid = lib.get(out.id).chapters[0].id
    stored = lib.vault.chapter_media_path(out.id, cid)

    stray = stored.with_suffix(".cbz")
    with zipfile.ZipFile(stray, "w") as z:
        z.writestr("001.jpg", b"something else entirely")

    lib.vault.normalize_chapter_archives(force=True)
    with zipfile.ZipFile(stored) as z:
        assert z.read("001.jpg") == b"the captured page"
    assert stray.is_file()  # left alone, not silently consumed
    lib.close()


# ---- the groundwork the next feature needs ----

def test_a_cache_key_cannot_be_built_without_a_version(tmp_path):
    """The rule that took three bugs to learn, as a function: anything cached
    has to say which version of itself is being cached."""
    from app.library.versions import UnversionedCacheKey, cache_key, chapter_version

    assert chapter_version(None) == ""
    with pytest.raises(UnversionedCacheKey):
        cache_key("poster", chapter_version(None), "some-title", 320)
    assert cache_key("page", "3.100.4", "berserk", "ch-1", 160) == "page-3.100.4-berserk-ch-1-160"


def test_a_document_from_an_older_build_is_upgraded_on_read(tmp_path):
    """A shape change is a migration, not an edit — and the read that upgrades
    must not write to the user's files."""
    from app.library import migrations

    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk", desc="original")))
    doc_path = next(tmp_path.rglob("berserk/title.json"))
    raw = json.loads(doc_path.read_text(encoding="utf-8"))
    raw.pop("schema", None)  # written before documents were versioned
    doc_path.write_text(json.dumps(raw), encoding="utf-8")
    before = doc_path.stat().st_mtime_ns

    marker = {"ran": False}

    def to_v2(d: dict) -> dict:
        marker["ran"] = True
        return {**d, "user": {**d.get("user", {}), "positions": {}}}

    monkey = {1: to_v2}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(migrations, "CURRENT_SCHEMA", 2)
        mp.setattr(migrations, "_STEPS", monkey)
        lib.vault._loc.clear()
        doc = lib.vault.load(out.id)
    assert marker["ran"] and doc is not None and doc.meta.desc == "original"
    assert doc_path.stat().st_mtime_ns == before  # a read never rewrites the vault
    lib.close()


def test_a_write_through_survives_a_reader_holding_the_document(tmp_path, monkeypatch):
    """Reads are lock-free on purpose, and Windows denies a rename ONTO a file
    someone has open — over a network vault that window is wide enough to hit.
    The reader always finishes, and a resume point written mid-playback has no
    second chance, so the write waits it out instead of failing."""
    from app.library import media as media_mod
    from app.library.models import UserPatch

    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Series")))

    real, calls = os.replace, []

    def denied_at_first(src, dst):
        calls.append(dst)
        if len(calls) <= 3:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(media_mod.os, "replace", denied_at_first)
    monkeypatch.setattr(media_mod.time, "sleep", lambda _s: None)

    assert lib.patch_user(out.id, UserPatch(rating=4)) is not None
    assert len(calls) == 4                      # three denials, then the write lands
    assert lib.vault.load(out.id).user.rating == 4
    lib.close()


def test_a_rename_that_never_happens_leaves_no_scratch_file(tmp_path, monkeypatch):
    """A temp that outlives its write would be swept as a stray, or worse,
    mistaken for media. Uniqueness alone is not enough — it has to be cleaned."""
    from app.library import media as media_mod
    from app.library.models import UserPatch

    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Series")))
    title_dir = lib.vault.chapters_dir(out.id).parent

    def always_denied(_src, _dst):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(media_mod.os, "replace", always_denied)
    monkeypatch.setattr(media_mod.time, "sleep", lambda _s: None)

    with pytest.raises(PermissionError):
        lib.patch_user(out.id, UserPatch(rating=4))
    assert not list(title_dir.glob("*.tmp"))
    lib.close()


def test_two_writes_never_share_a_scratch_name(tmp_path):
    """One vault can have more than one writer — a second app window, a dev
    sidecar — and a per-process lock does not reach across them."""
    target = tmp_path / "title.json"
    assert media.tmp_path(target) != media.tmp_path(target)
    assert media.tmp_path(target).name.endswith(".tmp")


def test_the_built_ui_document_is_never_cached_but_its_assets_are(tmp_path, monkeypatch):
    """A rebuilt frontend has to reach an open window. The entry document carries
    no content hash, so served without a Cache-Control it falls to the browser's
    heuristic cache and pins the window to the bundle it saw last."""
    import app.main as main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    (dist / "assets" / "index-abc123.js").write_text("//", encoding="utf-8")
    monkeypatch.setattr(main, "_FRONTEND_DIST", dist)

    lib = Library(tmp_path / "lib")
    with TestClient(main.create_app(lib)) as c:
        assert c.get("/app/index.html").headers["cache-control"] == "no-cache"
        asset = c.get("/app/assets/index-abc123.js")
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    lib.close()


def test_a_damaged_page_is_a_missing_page_not_a_500(tmp_path):
    """A truncated download leaves an entry that will not decompress. The grid
    asks for eight thumbnails at once — answering each with a traceback turns one
    bad file into a wall of noise and an error toast."""
    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Broken"),
                             chapters=[ChapterRow(id="c1", num="1")]))
    # a real zip whose stored bytes do not match the recorded CRC
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("001.jpg", b"\xff\xd8\xff" + b"x" * 64)
    raw = bytearray(buf.getvalue())
    raw[raw.index(b"\xff\xd8\xff") + 4] ^= 0xFF  # corrupt the payload, keep the CRC
    src = tmp_path / "broken.cbz"
    src.write_bytes(bytes(raw))
    lib.attach_chapter_media(out.id, num="1", lang="", group="", src=src, sidecar={})

    with TestClient(create_app(lib)) as c:
        for url in (f"/api/titles/{out.id}/chapters/c1/pages/0",
                    f"/api/titles/{out.id}/chapters/c1/pages/0?w=240&cap=1.5"):
            r = c.get(url)
            assert r.status_code == 404, f"{url} answered {r.status_code}"
    lib.close()
