"""The armed-download flow and chapter media ops, offline with real zip files."""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.library.service import Library
from app.main import create_app
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


def make_zip(tmp_path, name="dl.zip", pages=("p1.jpg", "p2.jpg", "p10.jpg")):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as z:
        for p in pages:
            z.writestr(p, b"img-" + p.encode())
        z.writestr("info.txt", b"junk")  # non-image entries are not pages
    return path


def download(c, tmp_path, *, num, lang="EN", group="dex", name="dl.zip"):
    """The full lifecycle: arm → start (claims the binding) → complete (ingest)."""
    src = make_zip(tmp_path, name)
    assert c.post("/api/downloads/arm", json={"titleId": "berserk", "num": num, "lang": lang, "group": group}).status_code == 200
    started = c.post("/api/downloads/start", json={
        "filename": name, "fileUrl": "https://cdn.site/f.zip", "pageUrl": "https://site/title/berserk",
    })
    assert started.status_code == 200
    r = c.post(f"/api/downloads/{started.json()['id']}/complete", json={"path": str(src)})
    assert r.status_code == 200
    return r.json()


# ---- arm / start handshake ----

def test_arm_requires_existing_title_and_num(client):
    assert client.post("/api/downloads/arm", json={"titleId": "nope", "num": "1"}).status_code == 404
    assert client.post("/api/downloads/arm", json={"titleId": "berserk", "num": " "}).status_code == 422


def test_arm_state_disarm(client):
    assert client.get("/api/downloads").json()["armed"] is None
    client.post("/api/downloads/arm", json={"titleId": "berserk", "num": "5"})
    assert client.get("/api/downloads").json()["armed"]["num"] == "5"
    assert client.delete("/api/downloads/arm").status_code == 204
    assert client.get("/api/downloads").json()["armed"] is None


def test_start_without_arm_is_rejected(client):
    assert client.post("/api/downloads/start", json={"filename": "x.zip"}).status_code == 409


def test_start_consumes_the_arm_and_allows_parallel(client, tmp_path):
    client.post("/api/downloads/arm", json={"titleId": "berserk", "num": "5", "lang": "EN", "group": "dex"})
    first = client.post("/api/downloads/start", json={"filename": "a.zip"}).json()
    # the arm is consumed at START — the next chapter can be armed while a.zip streams
    assert client.get("/api/downloads").json()["armed"] is None
    client.post("/api/downloads/arm", json={"titleId": "berserk", "num": "6", "lang": "UA"})
    second = client.post("/api/downloads/start", json={"filename": "b.zip"}).json()
    assert first["id"] != second["id"]
    # progress streams per item
    client.post(f"/api/downloads/{first['id']}/progress", json={"received": 50, "total": 100})
    items = {i["id"]: i for i in client.get("/api/downloads").json()["items"]}
    assert items[first["id"]]["received"] == 50 and items[first["id"]]["state"] == "downloading"
    # both complete independently
    for item, num in ((first, "5"), (second, "6")):
        src = make_zip(tmp_path, f"{num}.zip")
        assert client.post(f"/api/downloads/{item['id']}/complete", json={"path": str(src)}).status_code == 200
    items = {i["id"]: i for i in client.get("/api/downloads").json()["items"]}
    assert items[first["id"]]["state"] == "done" and items[second["id"]]["state"] == "done"


def test_failed_download_is_reported(client):
    client.post("/api/downloads/arm", json={"titleId": "berserk", "num": "9"})
    started = client.post("/api/downloads/start", json={"filename": "x.zip"}).json()
    client.post(f"/api/downloads/{started['id']}/failed", json={"reason": "interrupted"})
    item = next(i for i in client.get("/api/downloads").json()["items"] if i["id"] == started["id"])
    assert item["state"] == "failed" and item["error"] == "interrupted"


# ---- attaching media ----

def test_download_attaches_to_the_matching_row(client, tmp_path):
    t = download(client, tmp_path, num="5")
    ch = next(c for c in t["chapters"] if c["id"] == "b-5-en")  # matched, not duplicated
    assert len(t["chapters"]) == 1
    assert ch["dl"] is True and ch["pages"] == 3
    assert ch["dlSource"] == "https://site/title/berserk"       # download source, kept


def test_download_creates_a_row_when_none_matches(client, tmp_path):
    t = download(client, tmp_path, num="6", lang="UA", group="ukr")
    assert len(t["chapters"]) == 2
    row = next(c for c in t["chapters"] if c["num"] == "6")
    assert row["lang"] == "UA" and row["dl"] is True and row["pages"] == 3


def test_media_survives_meta_commit_and_reopen(client, tmp_path):
    download(client, tmp_path, num="5")
    client.put("/api/titles/berserk", json={
        "meta": {"title": "Berserk"}, "provenance": {},
        "chapters": [{"id": "b-5-en", "num": "5", "lang": "EN", "group": "dex"}],
    })
    t = client.get("/api/titles/berserk").json()
    assert t["chapters"][0]["dl"] is True and t["chapters"][0]["pages"] == 3


# ---- pages ----

def test_pages_listing_and_natural_order(client, tmp_path):
    download(client, tmp_path, num="5")
    assert client.get("/api/titles/berserk/chapters/b-5-en/pages").json() == {"count": 3}
    # p10 sorts AFTER p2 (natural order), so index 1 is p2
    body = client.get("/api/titles/berserk/chapters/b-5-en/pages/1").content
    assert body == b"img-p2.jpg"
    assert client.get("/api/titles/berserk/chapters/b-5-en/pages/9").status_code == 404


def test_delete_pages_rewrites_the_archive(client, tmp_path):
    download(client, tmp_path, num="5")
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/delete", json={"indices": [0, 2]})
    ch = next(c for c in r.json()["chapters"] if c["id"] == "b-5-en")
    assert ch["pages"] == 1
    assert client.get("/api/titles/berserk/chapters/b-5-en/pages/0").content == b"img-p2.jpg"


def test_delete_media_keeps_the_row(client, tmp_path):
    download(client, tmp_path, num="5")
    t = client.delete("/api/titles/berserk/chapters/b-5-en/media").json()
    ch = next(c for c in t["chapters"] if c["id"] == "b-5-en")
    assert ch["dl"] is False and ch["pages"] == 0
    assert client.get("/api/titles/berserk/chapters/b-5-en/pages").status_code == 404


def test_delete_row_removes_row_and_media(client, tmp_path):
    download(client, tmp_path, num="5")
    t = client.delete("/api/titles/berserk/chapters/b-5-en").json()
    assert t["chapters"] == []
    assert client.delete("/api/titles/berserk/chapters/b-5-en").status_code == 404


def test_import_local_archive(client, tmp_path):
    """The local twin of arming: raw archive bytes + chapter identity in the query."""
    src = make_zip(tmp_path, "local.zip")
    r = client.post("/api/titles/berserk/chapters/import",
                    params={"num": "7", "lang": "UA", "group": "ukr", "filename": "local.zip"},
                    content=src.read_bytes())
    assert r.status_code == 200
    ch = next(c for c in r.json()["chapters"] if c["num"] == "7")
    assert ch["dl"] is True and ch["pages"] == 3 and ch["lang"] == "UA"
    assert ch["dlSource"] == ""  # no web source — it came from disk
    assert client.get(f"/api/titles/berserk/chapters/{ch['id']}/pages").json() == {"count": 3}
    # identity is required; empty bodies are rejected
    assert client.post("/api/titles/berserk/chapters/import", params={"num": " "}, content=b"x").status_code == 422
    assert client.post("/api/titles/berserk/chapters/import", params={"num": "8"}, content=b"").status_code == 422


def test_read_state_survives_media_attach(client, tmp_path):
    client.patch("/api/titles/berserk/user", json={"read": {"b-5-en": "reading"}})
    t = download(client, tmp_path, num="5")
    assert next(c for c in t["chapters"] if c["id"] == "b-5-en")["read"] == "reading"


def test_page_thumbnail_and_aspect_cap(client, tmp_path, monkeypatch):
    """`w` serves a downscaled JPEG; `cap` additionally crops a very TALL page
    (webtoon strip) to width x cap from the top — preview-only behavior."""
    from io import BytesIO

    from PIL import Image

    monkeypatch.setenv("LONGBOX_CONFIG_DIR", str(tmp_path / "cfg"))  # thumb cache → tmp
    buf = BytesIO()
    Image.new("RGB", (100, 600), (200, 30, 30)).save(buf, "PNG")
    path = tmp_path / "tall.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("p1.png", buf.getvalue())
    assert client.post("/api/downloads/arm", json={"titleId": "berserk", "num": "5", "lang": "EN", "group": "dex"}).status_code == 200
    started = client.post("/api/downloads/start", json={"filename": "tall.zip", "fileUrl": "https://cdn/x.zip", "pageUrl": "https://site/b"})
    assert client.post(f"/api/downloads/{started.json()['id']}/complete", json={"path": str(path)}).status_code == 200

    plain = client.get("/api/titles/berserk/chapters/b-5-en/pages/0?w=50")
    assert plain.headers["content-type"] == "image/jpeg"
    # no cap → nothing cropped: the page keeps its 1:6 aspect, fitted into the
    # w × 4w box (the guard that keeps webtoon-strip thumbnails byte-bounded)
    assert Image.open(BytesIO(plain.content)).size == (33, 200)

    capped = client.get("/api/titles/berserk/chapters/b-5-en/pages/0?w=50&cap=1.5")
    assert Image.open(BytesIO(capped.content)).size == (50, 75)  # cropped to 2:3 from the top

    full = client.get("/api/titles/berserk/chapters/b-5-en/pages/0")
    assert full.headers["content-type"] == "image/png"  # the reader gets the original


def test_meta_commit_never_orphans_downloads(client, tmp_path):
    """Re-captured rows carry fresh ids; a stale draft may even DROP a saved
    row. Neither may disconnect a downloaded archive (or its read state)."""
    download(client, tmp_path, num="5")  # b-5-en now has media
    client.patch("/api/titles/berserk/user", json={"read": {"b-5-en": "reading"}})

    # 1) the same chapter re-captured under a fresh id → adopts the old id
    r = client.put("/api/titles/berserk", json={
        "meta": {"title": "Berserk"},
        "chapters": [{"id": "ch-5-recap", "num": "5", "lang": "EN", "group": "dex", "url": "https://s/ch5"}],
    })
    ch = r.json()["chapters"]
    assert [c["id"] for c in ch] == ["b-5-en"]
    assert ch[0]["dl"] is True and ch[0]["pages"] == 3 and ch[0]["read"] == "reading"
    assert ch[0]["url"] == "https://s/ch5"  # the re-captured detail still lands

    # 2) a stale draft without the row at all → the media-backed row comes back
    r = client.put("/api/titles/berserk", json={"meta": {"title": "Berserk"}, "chapters": []})
    ch = r.json()["chapters"]
    assert [c["id"] for c in ch] == ["b-5-en"] and ch[0]["dl"] is True

    # 3) a duplicate twin in one commit collapses instead of forking the id
    r = client.put("/api/titles/berserk", json={
        "meta": {"title": "Berserk"},
        "chapters": [
            {"id": "b-5-en", "num": "5", "lang": "EN", "group": "dex"},
            {"id": "ch-5-twin", "num": "5", "lang": "EN", "group": "dex"},
        ],
    })
    assert [c["id"] for c in r.json()["chapters"]] == ["b-5-en"]


def test_import_attaches_to_exact_row_and_records_url(client, tmp_path):
    """chapter_id pins the attach to one row (replace included); url lands on it."""
    src = make_zip(tmp_path, "row.zip")
    r = client.post("/api/titles/berserk/chapters/import",
                    params={"chapter_id": "b-5-en", "url": "https://s/ch5", "filename": "row.zip"},
                    content=src.read_bytes())
    assert r.status_code == 200
    ch = next(c for c in r.json()["chapters"] if c["id"] == "b-5-en")
    assert ch["dl"] is True and ch["pages"] == 3 and ch["url"] == "https://s/ch5"
    assert len(r.json()["chapters"]) == 1  # attached, not duplicated
    # unknown row → 404; missing both num and chapter_id → 422
    assert client.post("/api/titles/berserk/chapters/import",
                       params={"chapter_id": "nope"}, content=b"x").status_code == 404
    assert client.post("/api/titles/berserk/chapters/import", params={}, content=b"x").status_code == 422


# ---- contents editor: add loose images / move pages between entries ----

def _img_files(*names):
    return [("files", (n, b"img-" + n.encode(), "image/jpeg")) for n in names]


def test_add_images_creates_archive_on_bare_row(client):
    """A row with no file becomes an image set on first add (multi-image upload)."""
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/add",
                    files=_img_files("b.jpg", "a.png", "notes.txt"))
    assert r.status_code == 200
    ch = next(c for c in r.json()["chapters"] if c["id"] == "b-5-en")
    assert ch["dl"] is True and ch["pages"] == 2  # the .txt is skipped
    # upload ORDER is preserved via renumbering, not the filenames' sort order
    assert client.get("/api/titles/berserk/chapters/b-5-en/pages/0").content == b"img-b.jpg"
    assert client.get("/api/titles/berserk/chapters/b-5-en/pages/1").content == b"img-a.png"


def test_add_images_appends_after_alien_names(client, tmp_path):
    """Appending to a site-served archive (p1/p2/p10 naming) renumbers the whole
    archive so the new pages land LAST regardless of name sorting."""
    download(client, tmp_path, num="5")  # p1, p2, p10
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/add", files=_img_files("000.jpg"))
    ch = next(c for c in r.json()["chapters"] if c["id"] == "b-5-en")
    assert ch["pages"] == 4
    assert client.get("/api/titles/berserk/chapters/b-5-en/pages/0").content == b"img-p1.jpg"
    assert client.get("/api/titles/berserk/chapters/b-5-en/pages/3").content == b"img-000.jpg"
    # junk uploads are rejected outright
    assert client.post("/api/titles/berserk/chapters/b-5-en/pages/add",
                       files=_img_files("junk.txt")).status_code == 422
    assert client.post("/api/titles/berserk/chapters/nope/pages/add",
                       files=_img_files("a.jpg")).status_code == 404


def test_move_pages_between_entries(client, tmp_path):
    download(client, tmp_path, num="5")  # b-5-en: p1, p2, p10
    client.put("/api/titles/berserk", json={
        "meta": {"title": "Berserk"},
        "chapters": [
            {"id": "b-5-en", "num": "5", "lang": "EN", "group": "dex"},
            {"id": "extra", "num": "Extra", "lang": "", "group": ""},
        ],
    })
    # move pages 0 and 2 into the bare 'extra' row → its archive is created
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/move",
                    json={"to": "extra", "indices": [0, 2]})
    assert r.status_code == 200
    by = {c["id"]: c for c in r.json()["chapters"]}
    assert by["b-5-en"]["pages"] == 1 and by["extra"]["pages"] == 2 and by["extra"]["dl"] is True
    assert client.get("/api/titles/berserk/chapters/extra/pages/0").content == b"img-p1.jpg"
    assert client.get("/api/titles/berserk/chapters/extra/pages/1").content == b"img-p10.jpg"
    # moving the LAST page out empties the source archive → its file is removed
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/move",
                    json={"to": "extra", "indices": [0]})
    by = {c["id"]: c for c in r.json()["chapters"]}
    assert by["b-5-en"]["dl"] is False and by["extra"]["pages"] == 3
    # unknown target / same-entry moves are rejected
    assert client.post("/api/titles/berserk/chapters/extra/pages/move",
                       json={"to": "nope", "indices": [0]}).status_code == 404
    assert client.post("/api/titles/berserk/chapters/extra/pages/move",
                       json={"to": "extra", "indices": [0]}).status_code == 404


def test_reorder_pages(client, tmp_path):
    download(client, tmp_path, num="5")  # natural order: p1, p2, p10
    r = client.post("/api/titles/berserk/chapters/b-5-en/pages/reorder", json={"order": [2, 0, 1]})
    assert r.status_code == 200
    assert client.get("/api/titles/berserk/chapters/b-5-en/pages/0").content == b"img-p10.jpg"
    assert client.get("/api/titles/berserk/chapters/b-5-en/pages/1").content == b"img-p1.jpg"
    assert client.get("/api/titles/berserk/chapters/b-5-en/pages/2").content == b"img-p2.jpg"
    # a partial or out-of-range order is rejected — manual arrangement is explicit
    assert client.post("/api/titles/berserk/chapters/b-5-en/pages/reorder", json={"order": [0, 1]}).status_code == 422
    assert client.post("/api/titles/berserk/chapters/b-5-en/pages/reorder", json={"order": [0, 1, 9]}).status_code == 422
    assert client.post("/api/titles/berserk/chapters/nope/pages/reorder", json={"order": [0]}).status_code == 404
