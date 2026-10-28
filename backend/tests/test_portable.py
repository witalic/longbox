"""Everything a library knows travels with it.

Recipes, bookmarks, source groups, the page-capture filter and how the reader
shows each title live IN THE VAULT, so a library carried to another machine
arrives whole. The app config keeps only what belongs to the machine.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config_store import config_path, save_config
from app.legacy_config import move_site_data_into
from app.library.migrations import CURRENT_SCHEMA, migrate
from app.library.models import DraftIn, TitleMeta
from app.library.service import Library
from app.library.vault import DOC_SCHEMA
from app.main import create_app
from app.scraper.models import Candidate, FieldRule, Recipe
from app.settings import get_settings

TITLE_RULE = {"mode": "single", "candidates": [{"kind": "css", "selector": "h1", "note": "picked"}]}


def recipe(domain: str, version: int = 1, selector: str = "h1") -> Recipe:
    return Recipe(domain=domain, version=version,
                  fields={"title": FieldRule(candidates=[Candidate(selector=selector)])})


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("LONGBOX_CONFIG_DIR", str(tmp_path / "cfg"))
    get_settings.cache_clear()
    yield tmp_path / "cfg"
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path, cfg):
    lib = Library(tmp_path / "lib")
    with TestClient(create_app(lib)) as c:
        yield c
    lib.close()


def _doc(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---- where it lives -------------------------------------------------------

def test_site_knowledge_is_written_into_the_vault_not_the_app_config(client, tmp_path, cfg):
    client.put("/api/recipes/example.org", json={"domain": "example.org", "fields": {"title": TITLE_RULE}})
    client.put("/api/sources/example.org", json={
        "group": "Manga", "bookmarks": [{"name": "Feed", "url": "https://example.org/feed"}]})
    client.put("/api/source-groups", json={"groups": ["Manga", "Art"]})
    client.put("/api/page-filter", json={"minPx": 320, "widthRatio": 0.4})

    recipes = _doc(tmp_path / "lib" / "recipes.json")
    assert recipes["recipes"]["example.org"]["version"] == 1
    assert recipes["pageFilter"] == {"minPx": 320, "widthRatio": 0.4}
    sources = _doc(tmp_path / "lib" / "sources.json")
    assert sources["groups"] == ["Manga", "Art"]
    assert sources["sources"]["example.org"]["bookmarks"] == [{"name": "Feed", "url": "https://example.org/feed"}]

    assert not (cfg / "recipes").exists()
    assert not config_path().exists() or not {"sources", "source_groups"} & set(_doc(config_path()))


def test_each_library_has_its_own_sites(client, tmp_path):
    client.put("/api/recipes/example.org", json={"domain": "example.org", "fields": {"title": TITLE_RULE}})
    client.put("/api/sources/example.org", json={"group": "Manga"})
    client.put("/api/source-groups", json={"groups": ["Manga"]})

    client.put("/api/settings/library-path", json={"path": str(tmp_path / "other")})
    assert client.get("/api/recipes/example.org").status_code == 404
    assert client.get("/api/sources").json() == []
    assert client.get("/api/source-groups").json() == []

    client.put("/api/settings/library-path", json={"path": str(tmp_path / "lib")})
    assert client.get("/api/recipes/example.org").json()["version"] == 1
    assert client.get("/api/source-groups").json() == ["Manga"]


def test_a_removed_source_stays_removed_until_it_is_taught_again(client, tmp_path):
    client.put("/api/recipes/example.org", json={"domain": "example.org", "fields": {"title": TITLE_RULE}})
    client.delete("/api/sources/example.org")
    assert _doc(tmp_path / "lib" / "sources.json")["removed"] == ["example.org"]
    assert client.get("/api/sources").json() == []

    client.put("/api/recipes/example.org", json={"domain": "example.org", "fields": {"title": TITLE_RULE}})
    assert _doc(tmp_path / "lib" / "sources.json")["removed"] == []
    assert [s["domain"] for s in client.get("/api/sources").json()] == ["example.org"]


# ---- a document it cannot read is never written over ----------------------
#
# "There is none" is the one answer a read may give without the document: a
# client told "nothing saved" builds its next write on it, and that write
# replaces what is there.

def test_an_unreadable_recipe_file_says_so_and_refuses_writes(client, tmp_path):
    broken = tmp_path / "lib" / "recipes.json"
    broken.write_text('{"recipes": {"example.org": ', encoding="utf-8")

    assert client.get("/api/recipes/example.org").status_code == 409
    r = client.put("/api/recipes/example.org", json={"domain": "example.org", "fields": {"title": TITLE_RULE}})
    assert r.status_code == 409 and "recipes.json" in r.json()["detail"]
    assert broken.read_text(encoding="utf-8") == '{"recipes": {"example.org": '


def test_a_broken_recipe_file_does_not_take_the_sources_down(client, tmp_path):
    (tmp_path / "lib" / "recipes.json").write_text("{", encoding="utf-8")
    r = client.put("/api/sources/example.org", json={
        "bookmarks": [{"name": "Feed", "url": "https://example.org/feed"}]})
    assert r.status_code == 200
    site = next(s for s in r.json() if s["domain"] == "example.org")
    assert site["bookmarks"] and site["hasRecipe"] is False
    # …and a recipe it cannot read is not deleted by removing the site
    assert client.delete("/api/sources/example.org").status_code == 409
    assert [s["domain"] for s in client.get("/api/sources").json()] == ["example.org"]


def test_an_unreadable_sources_file_says_so_and_refuses_writes(client, tmp_path):
    broken = tmp_path / "lib" / "sources.json"
    broken.write_text("[1, 2", encoding="utf-8")
    assert client.get("/api/sources").status_code == 409
    assert client.put("/api/source-groups", json={"groups": ["Manga"]}).status_code == 409
    assert broken.read_text(encoding="utf-8") == "[1, 2"


def test_a_read_that_fails_is_not_an_empty_document(tmp_path, monkeypatch):
    lib = Library(tmp_path / "lib")
    lib.recipes.save(recipe("example.org"))
    lib.source_prefs.set_groups(["Manga"])
    real = Path.read_bytes

    def blink(self):
        if self.name in ("recipes.json", "sources.json"):
            raise PermissionError("the share blinked")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", blink)
    with pytest.raises(PermissionError):
        lib.recipes.get("example.org")
    with pytest.raises(PermissionError):
        lib.source_prefs.snapshot()
    with pytest.raises(PermissionError):
        lib.source_prefs.set_groups([])
    monkeypatch.undo()
    assert lib.recipes.get("example.org") is not None
    assert lib.source_prefs.snapshot().groups == ["Manga"]
    lib.close()


def test_a_document_from_a_newer_build_is_read_but_never_written(client, tmp_path):
    newer = tmp_path / "lib" / "recipes.json"
    body = json.dumps({"schema": DOC_SCHEMA + 1, "recipes": {"example.org": {
        "domain": "example.org", "version": 3, "fields": {"title": TITLE_RULE}}}})
    newer.write_text(body, encoding="utf-8")

    assert client.get("/api/recipes/example.org").json()["version"] == 3
    r = client.put("/api/recipes/example.org", json={"domain": "example.org", "fields": {"title": TITLE_RULE}})
    assert r.status_code == 409 and "newer" in r.json()["detail"]
    assert newer.read_text(encoding="utf-8") == body


def test_a_recipe_this_build_cannot_read_is_neither_applied_nor_replaced(client, tmp_path):
    doc = tmp_path / "lib" / "recipes.json"
    body = json.dumps({"schema": 1, "recipes": {
        "example.org": {"domain": "example.org", "fields": "a shape from elsewhere"}}})
    doc.write_text(body, encoding="utf-8")

    assert client.get("/api/recipes/example.org").status_code == 409
    assert client.put("/api/recipes/example.org",
                      json={"domain": "example.org", "fields": {"title": TITLE_RULE}}).status_code == 409
    assert doc.read_text(encoding="utf-8") == body
    # the listing still shows the site — it just has nothing to apply
    site = next(s for s in client.get("/api/sources").json() if s["domain"] == "example.org")
    assert site["hasRecipe"] is False


def test_two_sites_taught_at_once_both_stay_taught(tmp_path):
    lib = Library(tmp_path / "lib")
    domains = [f"site{i}.example" for i in range(8)]
    threads = [threading.Thread(target=lib.recipes.save, args=(recipe(d),)) for d in domains]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(lib.recipes.list_domains()) == domains
    lib.close()


# ---- the page-capture filter ----------------------------------------------

def test_the_page_filter_defaults_and_refuses_what_it_cannot_mean(client):
    assert client.get("/api/page-filter").json() == {"minPx": 200, "widthRatio": 0.5}
    assert client.put("/api/page-filter", json={"minPx": 5000, "widthRatio": 0.5}).status_code == 422
    assert client.put("/api/page-filter", json={"minPx": 100, "widthRatio": 1.5}).status_code == 422
    client.put("/api/page-filter", json={"minPx": 100, "widthRatio": 0.25})
    assert client.get("/api/page-filter").json() == {"minPx": 100, "widthRatio": 0.25}


# ---- how the reader shows a title -----------------------------------------

def test_reader_settings_live_in_the_title_and_survive_a_meta_commit(client, tmp_path):
    tid = client.post("/api/titles", json={"meta": {"title": "Akira"}, "provenance": {}, "chapters": []}).json()["id"]
    prefs = {"mode": "strip", "fit": "width", "width": 900, "margins": 24}
    assert client.patch(f"/api/titles/{tid}/user", json={"reader": prefs}).json()["reader"] == prefs

    on_disk = _doc(next((tmp_path / "lib").rglob("title.json")))
    assert on_disk["user"]["reader"] == prefs

    client.put(f"/api/titles/{tid}", json={"meta": {"title": "Akira (1988)"}, "provenance": {}, "chapters": []})
    assert client.get(f"/api/titles/{tid}").json()["reader"] == prefs


def test_reader_settings_outside_what_the_reader_can_draw_are_refused(client):
    tid = client.post("/api/titles", json={"meta": {"title": "Akira"}, "provenance": {}, "chapters": []}).json()["id"]
    bad = {"mode": "strip", "fit": "width", "width": 50, "margins": 0}
    assert client.patch(f"/api/titles/{tid}/user", json={"reader": bad}).status_code == 422
    assert client.get(f"/api/titles/{tid}").json()["reader"] == {}


def test_a_reader_tweak_does_not_lift_the_title_in_the_updated_order(client, tmp_path):
    tid = client.post("/api/titles", json={"meta": {"title": "Akira"}, "provenance": {}, "chapters": []}).json()["id"]
    doc = next((tmp_path / "lib").rglob("title.json"))
    # an hour ago — a write in the same clock tick would carry the same stamp
    stamp = doc.stat().st_mtime_ns - 3600 * 10**9
    os.utime(doc, ns=(stamp, stamp))

    client.patch(f"/api/titles/{tid}/user", json={"reader": {"mode": "strip", "fit": "width",
                                                              "width": 900, "margins": 0}})
    # moved — a backup or another index must see the file changed — but barely
    moved = doc.stat().st_mtime_ns
    assert stamp < moved <= stamp + 2 * 10**9
    assert _doc(doc)["user"]["reader"]["mode"] == "strip"

    client.patch(f"/api/titles/{tid}/user", json={"fav": True})  # a star IS a touch
    assert doc.stat().st_mtime_ns > moved + 3000 * 10**9


def test_reader_settings_nobody_expected_do_not_cost_the_title(tmp_path):
    lib = Library(tmp_path / "lib")
    tid = lib.create(DraftIn(meta=TitleMeta(title="Akira"))).id
    for odd in ({"width": None, "rtl": True, "zoom": {"x": 1}}, None, "strip"):
        path = next((tmp_path / "lib").rglob("title.json"))
        raw = _doc(path)
        raw["user"]["reader"] = odd
        path.write_text(json.dumps(raw), encoding="utf-8")
        assert lib.vault.load(tid) is not None
    lib.close()


def test_a_schema_3_document_opens_with_no_reader_settings():
    raw = {"schema": 3, "meta": {"title": "Lain"}, "user": {"fav": True, "position": {}}}
    upgraded, changed = migrate(dict(raw))
    assert changed is True and upgraded["schema"] == CURRENT_SCHEMA
    assert upgraded["user"]["reader"] == {} and upgraded["user"]["fav"] is True


# ---- what an older build left in the app config ----------------------------

def _legacy(cfg, recipes: list[Recipe], **keys) -> None:
    (cfg / "recipes").mkdir(parents=True, exist_ok=True)
    for r in recipes:
        (cfg / "recipes" / f"{r.domain}.json").write_text(r.model_dump_json(), encoding="utf-8")
    save_config({"library_path": "kept", "homepage": "https://start.example", **keys})


def test_the_config_hands_its_site_data_to_the_open_library_and_forgets_it(tmp_path, cfg):
    _legacy(cfg, [recipe("a.example", version=4), recipe("b.example")],
            sources={"a.example": {"group": "Manga", "bookmarks": [{"name": "x", "url": "https://a.example/x"}]}},
            source_groups=["Manga", "Art"], hidden_sources=["gone.example"])
    lib = Library(tmp_path / "lib")

    assert move_site_data_into(lib) is True
    assert lib.recipes.get("a.example").version == 4      # learned elsewhere, kept as it was
    assert lib.recipes.get("b.example") is not None
    layer = lib.source_prefs.snapshot()
    assert layer.prefs["a.example"]["group"] == "Manga"
    assert layer.groups == ["Manga", "Art"]
    assert layer.removed == {"gone.example"}

    # the config keeps what belongs to this machine, and nothing else
    assert _doc(config_path()) == {"library_path": "kept", "homepage": "https://start.example"}
    assert not (cfg / "recipes").exists()
    assert move_site_data_into(lib) is False  # once
    lib.close()


def test_what_the_vault_already_knows_wins_over_the_config(tmp_path, cfg):
    lib = Library(tmp_path / "lib")
    lib.recipes.save(recipe("a.example", selector="h1.mine"))
    lib.source_prefs.put("a.example", group="Mine")
    lib.source_prefs.set_groups(["Mine"])
    _legacy(cfg, [recipe("a.example", version=9, selector="h1.theirs")],
            sources={"a.example": {"group": "Theirs"}}, source_groups=["Theirs", "Mine"])

    assert move_site_data_into(lib) is True
    kept = lib.recipes.get("a.example")
    assert kept.version == 1 and kept.fields["title"].candidates[0].selector == "h1.mine"
    layer = lib.source_prefs.snapshot()
    assert layer.prefs["a.example"]["group"] == "Mine"
    assert layer.groups == ["Mine", "Theirs"]
    lib.close()


def test_the_config_fills_only_what_the_vault_leaves_empty(tmp_path, cfg):
    """The two-machine case: the vault already knows a site, but only part of
    what this machine's config says about it."""
    lib = Library(tmp_path / "lib")
    lib.source_prefs.put("a.example", group="Mine",
                         bookmarks=[{"name": "Feed", "url": "https://a.example/feed"}])
    lib.source_prefs.set_removed("restored.example", True)
    lib.source_prefs.set_removed("restored.example", False)  # put back on purpose
    _legacy(cfg, [], sources={"a.example": {
        "group": "Theirs", "frozen": True,
        "bookmarks": [{"name": "Feed", "url": "https://a.example/feed"},
                      {"name": "New", "url": "https://a.example/new"}]}},
        hidden_sources=["restored.example"])

    assert move_site_data_into(lib) is True
    mine = lib.source_prefs.snapshot().prefs["a.example"]
    assert mine["group"] == "Mine" and mine["frozen"] is True
    assert [b["url"] for b in mine["bookmarks"]] == ["https://a.example/feed", "https://a.example/new"]
    assert lib.source_prefs.snapshot().removed == set()
    lib.close()


def test_an_unreadable_config_is_neither_moved_from_nor_written(tmp_path, cfg):
    _legacy(cfg, [recipe("a.example")])
    torn = '{"library_path": "D:/Comics", "libraries": ["D:/Comics"], "sources": '
    config_path().write_text(torn, encoding="utf-8")
    lib = Library(tmp_path / "lib")  # the fallback library a torn config leaves the app with

    assert move_site_data_into(lib) is False
    assert config_path().read_text(encoding="utf-8") == torn
    assert (cfg / "recipes" / "a.example.json").exists()
    assert not (tmp_path / "lib" / "recipes.json").exists()
    lib.close()


def test_a_library_opened_by_override_is_not_the_one_to_move_into(tmp_path, cfg, monkeypatch):
    _legacy(cfg, [recipe("a.example")], source_groups=["Manga"])
    monkeypatch.setenv("LONGBOX_LIBRARY_PATH", str(tmp_path / "lib"))
    get_settings.cache_clear()
    lib = Library(tmp_path / "lib")

    assert move_site_data_into(lib) is False
    assert _doc(config_path())["source_groups"] == ["Manga"]
    lib.close()


def test_a_recipe_file_the_build_cannot_read_stays_where_it_was(tmp_path, cfg):
    _legacy(cfg, [recipe("a.example")])
    (cfg / "recipes" / "broken.json").write_text("{", encoding="utf-8")
    lib = Library(tmp_path / "lib")

    assert move_site_data_into(lib) is True
    assert lib.recipes.get("a.example") is not None
    assert not (cfg / "recipes" / "a.example.json").exists()
    assert (cfg / "recipes" / "broken.json").read_text(encoding="utf-8") == "{"
    lib.close()


def test_a_vault_that_refuses_the_move_leaves_the_config_whole(tmp_path, cfg):
    _legacy(cfg, [recipe("a.example")], source_groups=["Manga"])
    lib = Library(tmp_path / "lib")
    (tmp_path / "lib" / "recipes.json").write_text("{", encoding="utf-8")  # unreadable: never written over

    assert move_site_data_into(lib) is False
    assert (cfg / "recipes" / "a.example.json").exists()
    assert _doc(config_path())["source_groups"] == ["Manga"]
    lib.close()


def test_nothing_to_move_writes_nothing(tmp_path, cfg):
    lib = Library(tmp_path / "lib")
    assert move_site_data_into(lib) is False
    assert not (tmp_path / "lib" / "recipes.json").exists()
    assert not (tmp_path / "lib" / "sources.json").exists()
    lib.close()
