"""Data-safety fixes from the refactor pass: reconcile adoption edge cases,
non-zip archive guards, junk-entry preservation, and migration corner cases."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.library import media
from app.library.models import ChapterRow, DraftIn, TitleMeta
from app.library.service import Library
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


def test_startup_normalizes_existing_archives(tmp_path):
    lib = Library(tmp_path)
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

    lib = Library(tmp_path)  # reopening runs the consistency pass
    assert (d / "ch-1.zip").is_file() and not (d / "ch-1.cbz").exists()
    assert (d / "ch-2.zip").is_file() and not (d / "ch-2.7z").exists()
    chapters = {c.id: c for c in lib.get("x").chapters}
    assert chapters["ch-1"].pages == 1 and chapters["ch-1"].dl is True
    assert chapters["ch-2"].pages == 2 and chapters["ch-2"].dl is True
    lib.close()


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
