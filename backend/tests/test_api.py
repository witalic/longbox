"""Offline API tests via Starlette's TestClient. The vault lives under a pytest
tmp_path, so nothing touches a real library and each test is isolated. The
fixture library is built through the public API (there is no seed module).
"""
from __future__ import annotations

import base64

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


def draft(**meta) -> dict:
    """A minimal valid draft commit body; meta fields overridable per test."""
    base = {"title": "Untitled"}
    base.update(meta)
    return {"meta": base, "provenance": {}, "chapters": []}


def seed(c: TestClient) -> None:
    """A small library exercising every filter axis, created via the API."""
    c.post("/api/titles", json={
        "meta": {"title": "Berserk", "authors": ["Kentaro Miura"], "artists": ["Kentaro Miura"],
                 "type": "manga", "status": "Paused", "year": "1989",
                 "genres": ["Action", "Dark Fantasy"], "tags": ["Revenge"],
                 "flags": {"adult": True, "ai": False, "censored": False},
                 "source": {"domain": "mangadex.org", "url": "https://mangadex.org/title/berserk"}},
        "provenance": {"title": {"origin": "auto", "url": "https://mangadex.org/title/berserk", "recipeVersion": 1}},
        "chapters": [
            {"id": "b-1-en", "num": "1", "title": "The Black Swordsman", "lang": "EN", "group": "dex"},
            {"id": "b-1-ua", "num": "1", "title": "Чорний мечник", "lang": "UA", "group": "ukr"},
        ],
    })
    c.post("/api/titles", json={
        "meta": {"title": "Solo Ascension", "authors": ["Bai Ye"], "type": "manhwa",
                 "status": "Ongoing", "genres": ["Action"], "tags": ["Cultivation"],
                 "source": {"domain": "toonily.example", "url": "https://toonily.example/solo"}},
        "chapters": [{"id": "s-1-en", "num": "1", "lang": "EN", "group": "g"}],
    })
    c.post("/api/titles", json={
        "meta": {"title": "Immortal Ledger", "authors": ["Bai Ye"], "type": "manhua",
                 "status": "Ongoing", "genres": ["Xianxia"], "tags": ["Cultivation"]},
    })
    c.patch("/api/titles/berserk/user", json={"fav": True, "rating": 5, "read": {"b-1-en": "reading"}})
    c.patch("/api/titles/immortal-ledger/user", json={"rating": 3, "read": {}})


@pytest.fixture
def client(tmp_path):
    lib = Library(tmp_path)
    with TestClient(create_app(lib)) as c:
        seed(c)
        yield c
    lib.close()


# ---- health & the wire contract ----

def test_health_open_and_counts(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["sha256"] is None
    assert body["titles"] == 3


def test_dto_is_flat_and_composed_from_layers(client):
    t = client.get("/api/titles/berserk").json()
    assert t["authors"] == ["Kentaro Miura"]
    assert t["fav"] is True and t["rating"] == 5              # user layer
    assert t["provenance"]["title"]["origin"] == "auto"       # provenance exposed
    assert t["source"]["domain"] == "mangadex.org"
    chapters = {c["id"]: c for c in t["chapters"]}
    assert chapters["b-1-en"]["read"] == "reading"            # composed read state
    assert chapters["b-1-ua"]["read"] == "unread"
    assert t["unread"] == 2                                   # nothing marked fully read


# ---- create ----

def test_create_slugs_and_uniquifies(client):
    a = client.post("/api/titles", json=draft(title="Dup")).json()
    b = client.post("/api/titles", json=draft(title="Dup")).json()
    assert a["id"] == "dup" and b["id"] == "dup-2"


def test_create_requires_a_title(client):
    assert client.post("/api/titles", json=draft(title="  ")).status_code == 422


def test_create_transliterates_cyrillic(client):
    t = client.post("/api/titles", json=draft(title="Я Хочу Це")).json()
    assert t["id"] == "ia-khochu-tse"


# ---- commits vs the user layer ----

def test_commit_replaces_meta_and_keeps_user_layer(client):
    body = draft(title="Berserk (Deluxe)", desc="new desc")
    r = client.put("/api/titles/berserk", json=body)
    assert r.status_code == 200
    t = r.json()
    assert t["title"] == "Berserk (Deluxe)" and t["desc"] == "new desc"
    assert t["fav"] is True and t["rating"] == 5   # user layer untouched by the commit
    assert t["chapters"] == []                     # commit is truthful: replaces what the draft holds


def test_commit_unknown_404(client):
    assert client.put("/api/titles/nope", json=draft(title="X")).status_code == 404


def test_user_patch_is_partial_and_clamped(client):
    r = client.patch("/api/titles/solo-ascension/user", json={"rating": 99})
    assert r.json()["rating"] == 5
    r = client.patch("/api/titles/solo-ascension/user", json={"read": {"s-1-en": "read"}})
    t = r.json()
    assert t["rating"] == 5            # earlier patch survives — partial writes
    assert t["unread"] == 0            # the only chapter is now read


def test_provenance_persists_in_the_vault(client, tmp_path):
    from app.library.migrations import CURRENT_SCHEMA

    doc = next(tmp_path.rglob("berserk/title.json")).read_text(encoding="utf-8")
    assert '"origin": "auto"' in doc and '"recipeVersion": 1' in doc
    # the document says which shape it is in, whatever that shape currently is
    assert f'"schema": {CURRENT_SCHEMA}' in doc


# ---- filters, search, sort, facets ----

def test_filter_type(client):
    assert {t["id"] for t in client.get("/api/library", params={"types": "manga"}).json()} == {"berserk"}
    both = {t["id"] for t in client.get("/api/library", params={"types": ["manga", "manhwa"]}).json()}
    assert both == {"berserk", "solo-ascension"}  # single-valued facet: include = OR


def test_filter_status_and_excludes(client):
    # vocab values are folded to lowercase on write ("Paused" was seeded)
    assert {t["id"] for t in client.get("/api/library", params={"statuses": "paused"}).json()} == {"berserk"}
    rest = {t["id"] for t in client.get("/api/library", params={"statuses_not": "paused"}).json()}
    assert rest == {"solo-ascension", "immortal-ledger"}


def test_filter_genre_tag_language(client):
    assert {t["id"] for t in client.get("/api/library", params={"genres": "Xianxia"}).json()} == {"immortal-ledger"}
    assert {t["id"] for t in client.get("/api/library", params={"tags": "Cultivation"}).json()} == {"solo-ascension", "immortal-ledger"}
    assert {t["id"] for t in client.get("/api/library", params={"languages": "UA"}).json()} == {"berserk"}
    # multi-valued facet: include = AND (narrowing), exclude removes carriers
    narrowed = {t["id"] for t in client.get("/api/library", params={"tags": "Cultivation", "genres": "Action"}).json()}
    assert narrowed == {"solo-ascension"}
    excluded = {t["id"] for t in client.get("/api/library", params={"tags_not": "Cultivation"}).json()}
    assert excluded == {"berserk"}


def test_filter_flags(client):
    assert {t["id"] for t in client.get("/api/library", params={"flags": "adult"}).json()} == {"berserk"}
    rest = {t["id"] for t in client.get("/api/library", params={"flags_not": "adult"}).json()}
    assert rest == {"solo-ascension", "immortal-ledger"}
    f = client.get("/api/library/facets").json()
    assert {x["v"]: x["n"] for x in f["flags"]} == {"adult": 1}


def test_filter_fav_and_min_rating(client):
    assert {t["id"] for t in client.get("/api/library", params={"fav": "true"}).json()} == {"berserk"}
    assert {t["id"] for t in client.get("/api/library", params={"min_rating": 4}).json()} == {"berserk"}


def test_progress_is_reading_progress_not_manga_status(client):
    # berserk has a chapter in 'reading' → started; the others have no read marks
    assert {t["id"] for t in client.get("/api/library", params={"progress": "reading"}).json()} == {"berserk"}
    unread = {t["id"] for t in client.get("/api/library", params={"progress": "unread"}).json()}
    assert unread == {"solo-ascension", "immortal-ledger"}  # not started (no chapters counts too)
    # completed = every chapter read — independent of the manga's own status
    assert client.get("/api/library", params={"progress": "completed"}).json() == []
    client.patch("/api/titles/solo-ascension/user", json={"read": {"s-1-en": "read"}})
    done = {t["id"] for t in client.get("/api/library", params={"progress": "completed"}).json()}
    assert done == {"solo-ascension"}


def test_search_matches_title_and_people(client):
    assert {t["id"] for t in client.get("/api/library", params={"search": "solo"}).json()} == {"solo-ascension"}
    assert {t["id"] for t in client.get("/api/library", params={"search": "miura"}).json()} == {"berserk"}


def test_sort_rating_desc(client):
    ratings = [t["rating"] for t in client.get("/api/library", params={"sort": "rating"}).json()]
    assert ratings == sorted(ratings, reverse=True)


def _facet(f: dict, key: str) -> dict[str, int]:
    return {x["v"]: x["n"] for x in f[key]}


def test_facets_carry_counts(client):
    f = client.get("/api/library/facets").json()
    assert _facet(f, "types") == {"manga": 1, "manhwa": 1, "manhua": 1}
    assert _facet(f, "statuses") == {"paused": 1, "ongoing": 2}
    assert _facet(f, "tags")["Cultivation"] == 2
    assert _facet(f, "languages") == {"EN": 2, "UA": 1}
    assert _facet(f, "authors")["Bai Ye"] == 2


def test_facets_are_linked_to_the_selection(client):
    # with tag=Cultivation applied, the other facets count only its two titles —
    # while the single-valued type facet keeps showing the alternatives
    f = client.get("/api/library/facets", params={"tags": "Cultivation"}).json()
    assert _facet(f, "genres") == {"Action": 1, "Xianxia": 1}
    assert _facet(f, "authors") == {"Bai Ye": 2}
    assert _facet(f, "types") == {"manhwa": 1, "manhua": 1}
    assert "manga" not in _facet(f, "types")  # berserk has no Cultivation tag


# ---- delete ----

def test_delete_removes_the_title_dir(client, tmp_path):
    assert client.delete("/api/titles/berserk").status_code == 204
    assert client.get("/api/titles/berserk").status_code == 404
    assert client.delete("/api/titles/berserk").status_code == 404
    assert not (tmp_path / "berserk").exists()


# ---- covers ----

FAKE_PNG = b"\x89PNG\r\n\x1a\n-not-a-real-png-but-bytes"


def test_cover_upload_serve_delete(client):
    body = {"data": base64.b64encode(FAKE_PNG).decode(), "contentType": "image/png",
            "sourceUrl": "https://mangadex.org/covers/b.png"}
    r = client.post("/api/titles/berserk/cover", json=body)
    assert r.status_code == 200
    t = r.json()
    assert t["cover"].startswith("/api/titles/berserk/cover?v=")
    assert t["coverSource"] == "https://mangadex.org/covers/b.png"
    got = client.get("/api/titles/berserk/cover")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert got.content == FAKE_PNG                       # exact bytes back
    t = client.delete("/api/titles/berserk/cover").json()
    assert t["cover"] == "" and t["coverSource"] == ""
    assert client.get("/api/titles/berserk/cover").status_code == 404


def test_cover_upload_survives_a_later_commit_and_user_patch(client):
    body = {"data": base64.b64encode(FAKE_PNG).decode(), "contentType": "image/png"}
    client.post("/api/titles/berserk/cover", json=body)
    client.put("/api/titles/berserk", json=draft(title="Berserk"))
    client.patch("/api/titles/berserk/user", json={"rating": 1})
    assert client.get("/api/titles/berserk/cover").status_code == 200


def test_cover_url_fallback_fetch(client, monkeypatch):
    import app.routers.library as lr
    monkeypatch.setattr(lr, "fetch_cover", lambda url, referer="": (FAKE_PNG, "jpg"))
    r = client.post("/api/titles/berserk/cover",
                    json={"url": "https://x/c.jpg", "referer": "https://x/title"})
    assert r.status_code == 200
    assert client.get("/api/titles/berserk/cover").headers["content-type"] == "image/jpeg"


def test_cover_rejects_bad_payloads(client):
    assert client.post("/api/titles/berserk/cover", json={}).status_code == 422
    assert client.post("/api/titles/berserk/cover",
                       json={"data": "AAAA", "contentType": "text/html"}).status_code == 422
    assert client.post("/api/titles/berserk/cover",
                       json={"data": "not-base64!!", "contentType": "image/png"}).status_code == 422


# ---- derived collections ----

def test_authors_derived_with_roles_and_tags(client):
    authors = {a["name"]: a for a in client.get("/api/authors").json()}
    assert authors["Kentaro Miura"]["role"] == "both"      # in authors[] and artists[]
    assert authors["Bai Ye"]["role"] == "author"
    assert authors["Bai Ye"]["titles"] == 2
    assert authors["Bai Ye"]["works"][0]["id"] in {"solo-ascension", "immortal-ledger"}
    assert "Cultivation" in authors["Bai Ye"]["topTags"]


def test_sources_derived_from_source_bindings(client):
    sources = {s["domain"]: s for s in client.get("/api/sources").json()}
    assert sources["mangadex.org"]["titles"] == 1
    assert sources["mangadex.org"]["homepage"] == "https://mangadex.org/"
    assert "toonily.example" in sources


def test_author_favorite_is_persisted_in_the_vault(client, tmp_path):
    authors = client.post("/api/authors/bai-ye/favorite", params={"value": "true"}).json()
    assert next(a for a in authors if a["id"] == "bai-ye")["fav"] is True
    assert (tmp_path / "authors.json").is_file()  # user layer, vault-level
    lib2 = Library(tmp_path)  # survives a full reopen + index rebuild
    try:
        assert next(a for a in lib2.authors() if a.id == "bai-ye").fav is True
    finally:
        lib2.close()
    assert client.post("/api/authors/nobody/favorite").status_code == 404


def test_authors_and_sources_empty_when_library_empty(tmp_path):
    with TestClient(create_app(Library(tmp_path / "empty"))) as c:
        assert c.get("/api/authors").json() == []
        assert c.get("/api/sources").json() == []


# ---- the vault is the source of truth ----

def test_vault_survives_index_rebuild(client, tmp_path):
    lib2 = Library(tmp_path)  # fresh index built purely from the on-disk files
    try:
        ids = {tid for tid in lib2.vault.list_ids()}
        assert ids == {"berserk", "solo-ascension", "immortal-ledger"}
        t = lib2.get("berserk")
        assert t is not None and t.fav is True and t.rating == 5
        assert t.provenance["title"].origin == "auto"
    finally:
        lib2.close()


def test_atomic_write_leaves_no_tmp_files(client, tmp_path):
    leftovers = list(tmp_path.rglob("*.tmp"))
    assert leftovers == []


# ---- settings ----

def test_get_settings_reports_real_path(client, tmp_path):
    body = client.get("/api/settings").json()
    assert body["title_count"] == 3
    assert body["library_path"] == str(tmp_path)


def test_change_library_path_swaps_the_live_library(client, tmp_path, monkeypatch):
    monkeypatch.setenv("LONGBOX_CONFIG_DIR", str(tmp_path / "cfg"))  # isolate the config file
    new_dir = tmp_path / "other-library"
    r = client.put("/api/settings/library-path", json={"path": str(new_dir)})
    assert r.status_code == 200 and r.json()["title_count"] == 0
    assert client.get("/api/library").json() == []
    assert client.get("/api/settings").json()["library_path"] == str(new_dir)


def test_library_list_switch_and_forget(client, tmp_path, monkeypatch):
    """Multiple registered libraries: switching registers, forgetting only
    drops the entry (never files), the active one cannot be forgotten."""
    monkeypatch.setenv("LONGBOX_CONFIG_DIR", str(tmp_path / "cfg"))
    a, b = tmp_path / "lib-a", tmp_path / "lib-b"
    body = client.put("/api/settings/library-path", json={"path": str(a)}).json()
    # the library we LEFT (never registered before) must appear in the list too
    assert str(tmp_path) in body["libraries"] and str(a) in body["libraries"]
    body = client.put("/api/settings/library-path", json={"path": str(b)}).json()
    assert body["library_path"] == str(b)
    assert str(a) in body["libraries"] and str(b) in body["libraries"]
    # the ACTIVE library refuses to be forgotten
    assert client.request("DELETE", "/api/settings/libraries", json={"path": str(b)}).status_code == 409
    body = client.request("DELETE", "/api/settings/libraries", json={"path": str(a)}).json()
    assert str(a) not in body["libraries"]
    assert a.is_dir()  # forgetting never touches the folder


def test_rebuild_index(client):
    assert client.post("/api/settings/rebuild").json()["title_count"] == 3


# ---- the loopback guard ----

def test_guard_blocks_without_secret(monkeypatch, tmp_path):
    monkeypatch.setenv("LONGBOX_AUTH_TOKEN", "s3cret")
    get_settings.cache_clear()
    lib = Library(tmp_path)
    with TestClient(create_app(lib), base_url="http://127.0.0.1") as c:
        assert c.get("/api/library").status_code == 401
        c.cookies.set("lb_auth", "s3cret")
        assert c.get("/api/library").status_code == 200
        assert c.get("/health").status_code == 200
    lib.close()


def test_guard_rejects_non_loopback_host(monkeypatch, tmp_path):
    monkeypatch.setenv("LONGBOX_AUTH_TOKEN", "s3cret")
    get_settings.cache_clear()
    lib = Library(tmp_path)
    with TestClient(create_app(lib), base_url="http://evil.example") as c:
        c.cookies.set("lb_auth", "s3cret")
        assert c.get("/api/library").status_code == 403
    lib.close()
