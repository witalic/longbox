"""Recipe v2 store + API: candidate-based rules, per-domain versioning."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.library.service import Library
from app.main import create_app
from app.scraper.models import Candidate, FieldRule, Recipe
from app.settings import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LONGBOX_CONFIG_DIR", str(tmp_path / "cfg"))  # isolate the recipe store
    get_settings.cache_clear()
    lib = Library(tmp_path / "lib")
    with TestClient(create_app(lib)) as c:
        yield c
    lib.close()
    get_settings.cache_clear()


RECIPE = {
    "domain": "mangadex.org",
    "fields": {
        "title": {"mode": "single", "candidates": [
            {"kind": "css", "selector": "h1.series-title", "note": "picked"},
            {"kind": "meta", "selector": "og:title", "attr": "content", "note": "fallback"},
        ]},
        "genres": {"mode": "list", "lower": True, "stripCounts": True, "candidates": [
            {"kind": "css", "selector": "ul.tags li", "note": "picked"},
        ]},
        "cover": {"mode": "single", "candidates": [
            {"kind": "css", "selector": "img.cover", "attr": "src", "note": "picked"},
            {"kind": "meta", "selector": "og:image", "attr": "content", "note": "fallback"},
        ]},
    },
    "chapters": {
        "item": ".chapters .chapter",
        "num": {"candidates": [{"selector": ".num"}]},
        "title": {"candidates": [{"selector": "a.title"}]},
        "url": {"candidates": [{"selector": "a.title", "attr": "href"}]},
    },
}


def test_roundtrip_preserves_candidate_order(client):
    put = client.put("/api/recipes/mangadex.org", json=RECIPE)
    assert put.status_code == 200 and put.json()["version"] == 1
    got = client.get("/api/recipes/mangadex.org").json()
    title = got["fields"]["title"]
    assert [c["kind"] for c in title["candidates"]] == ["css", "meta"]
    assert title["candidates"][1]["selector"] == "og:title"
    assert got["fields"]["genres"]["mode"] == "list"
    assert got["fields"]["genres"]["lower"] is True
    assert got["chapters"]["item"] == ".chapters .chapter"


def test_resave_bumps_version(client):
    assert client.put("/api/recipes/mangadex.org", json=RECIPE).json()["version"] == 1
    assert client.put("/api/recipes/mangadex.org", json=RECIPE).json()["version"] == 2
    assert client.get("/api/recipes/mangadex.org").json()["version"] == 2


def test_path_domain_is_authoritative(client):
    body = {**RECIPE, "domain": "spoofed.example"}
    saved = client.put("/api/recipes/mangadex.org", json=body).json()
    assert saved["domain"] == "mangadex.org"
    assert client.get("/api/recipes").json() == ["mangadex.org"]


def test_unknown_domain_404(client):
    assert client.get("/api/recipes/nope.example").status_code == 404


def test_delete_recipe(client):
    client.put("/api/recipes/mangalib.me", json={**RECIPE, "domain": "mangalib.me"})
    assert client.delete("/api/recipes/mangalib.me").status_code == 204
    assert client.get("/api/recipes/mangalib.me").status_code == 404
    assert client.delete("/api/recipes/mangalib.me").status_code == 404


def test_delete_source_hides_it_but_keeps_title_links(client):
    client.put("/api/recipes/mangadex.org", json=RECIPE)
    created = client.post("/api/titles", json={
        "meta": {"title": "X", "source": {"domain": "mangadex.org", "url": "https://mangadex.org/title/x"}},
    }).json()
    r = client.delete("/api/sources/mangadex.org")
    assert r.status_code == 200
    assert r.json() == {"hidden": True, "recipeDeleted": True}
    # the title KEEPS its source link — removing that is a per-title action
    t = client.get(f"/api/titles/{created['id']}").json()
    assert t["source"]["domain"] == "mangadex.org"
    # the source disappears from the list, its recipe is forgotten
    assert client.get("/api/sources").json() == []
    assert client.get("/api/recipes/mangadex.org").status_code == 404
    # teaching the site again un-hides it
    client.put("/api/recipes/mangadex.org", json=RECIPE)
    assert [s["domain"] for s in client.get("/api/sources").json()] == ["mangadex.org"]


def test_sources_join_recipe_details(client):
    client.put("/api/recipes/mangadex.org", json=RECIPE)
    client.post("/api/titles", json={
        "meta": {"title": "X", "source": {"domain": "mangadex.org", "url": "https://mangadex.org/title/x"}},
    })
    s = next(x for x in client.get("/api/sources").json() if x["domain"] == "mangadex.org")
    assert s["titles"] == 1
    assert s["hasRecipe"] and s["recipeVer"] >= 1
    assert set(s["fields"]) == {"title", "genres", "cover"}


def test_anchor_candidates_roundtrip(client):
    body = {
        "domain": "mangalib.me",
        "fields": {"authors": {"mode": "list", "candidates": [
            {"kind": "anchor", "selector": "Автор", "note": "label"},
            {"kind": "css", "selector": ".info-row .value", "note": "picked"},
        ]}},
    }
    client.put("/api/recipes/mangalib.me", json=body)
    got = client.get("/api/recipes/mangalib.me").json()
    cands = got["fields"]["authors"]["candidates"]
    assert [c["kind"] for c in cands] == ["anchor", "css"]  # label anchor stays first
    assert cands[0]["selector"] == "Автор"


def test_legacy_pre_candidate_recipe_is_unlearned(client, tmp_path):
    # A prototype-era recipe file (selector/kind shape) parses into candidate-less
    # rules; the store must report it as absent, not as a recipe that no-ops.
    legacy = '{"domain": "old.example", "version": 3, "fields": {"title": {"selector": "h1", "kind": "single"}}}'
    (tmp_path / "cfg" / "recipes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cfg" / "recipes" / "old.example.json").write_text(legacy, encoding="utf-8")
    assert client.get("/api/recipes/old.example").status_code == 404


def test_model_defaults_are_lean():
    r = Recipe(domain="x", fields={"title": FieldRule(candidates=[Candidate(selector="h1")])})
    rule = r.fields["title"]
    assert rule.mode == "single" and rule.index == 0
    assert rule.candidates[0].kind == "css" and rule.candidates[0].attr is None
    assert r.chapters is None


def test_a_source_can_say_which_fields_it_does_not_offer(client):
    """Which rows the 344px dock shows for a site is a fact about the SITE, so it
    rides with its recipe — and a recipe that ONLY says that is still a recipe."""
    saved = client.put("/api/recipes/nhentai.net", json={
        "domain": "nhentai.net", "fields": {}, "hidden": ["studio", "status"],
    }).json()
    assert saved["hidden"] == ["studio", "status"]

    # nothing was learned yet, but the list must survive the "unlearned" guard
    back = client.get("/api/recipes/nhentai.net")
    assert back.status_code == 200
    assert back.json()["hidden"] == ["studio", "status"]

    # and it survives learning a field afterwards
    client.put("/api/recipes/nhentai.net", json={
        "domain": "nhentai.net", "hidden": ["studio"],
        "fields": {"title": {"mode": "single", "candidates": [
            {"kind": "css", "selector": "h1", "note": "picked"}]}},
    })
    now = client.get("/api/recipes/nhentai.net").json()
    assert now["hidden"] == ["studio"] and "title" in now["fields"]


def test_a_site_you_only_bookmarked_is_still_a_source(client):
    """Sources are derived from TITLES, so a domain you have not captured from
    would have its bookmarks saved and never shown — which reads as "the star
    did nothing"."""
    assert [s["domain"] for s in client.get("/api/sources").json()] == []

    client.put("/api/sources/example.org", json={
        "bookmarks": [{"name": "A page", "url": "https://example.org/x"}]})

    got = client.get("/api/sources").json()
    site = next(s for s in got if s["domain"] == "example.org")
    assert site["titles"] == 0 and site["hasRecipe"] is False
    assert [b["url"] for b in site["bookmarks"]] == ["https://example.org/x"]


def test_dropping_the_last_bookmark_takes_the_source_with_it(client):
    """An entry that says nothing is not worth keeping: a domain with no titles,
    no recipe and no links is not a source, it is a site you once visited."""
    client.put("/api/sources/example.org", json={
        "bookmarks": [{"name": "A page", "url": "https://example.org/x"}]})
    client.put("/api/sources/example.org", json={"bookmarks": []})
    assert [s["domain"] for s in client.get("/api/sources").json()] == []
