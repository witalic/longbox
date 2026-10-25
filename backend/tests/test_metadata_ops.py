"""The Metadata view's API end to end: fields and values across a scope,
operations previewed and run as a pass, the history and its revert, and rules —
kept, applied to what arrives, counted, and applied to titles already stored."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.library.service import Library
from app.main import create_app
from app.settings import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LONGBOX_CONFIG_DIR", str(tmp_path / "cfg"))
    get_settings.cache_clear()
    lib = Library(tmp_path / "lib")
    with TestClient(create_app(lib)) as c:
        yield c
    lib.close()
    get_settings.cache_clear()


def title(c, name, *, type_="manga", tags=(), status="", chapters=(), prov=None, source=""):
    return c.post("/api/titles", json={
        "meta": {"title": name, "type": type_, "tags": list(tags), "status": status,
                 "source": {"domain": "", "url": source}},
        "provenance": {k: {"origin": v} for k, v in (prov or {}).items()},
        "chapters": [{"id": "", "num": n, "lang": lang, "group": g} for n, lang, g in chapters],
    }).json()["id"]


def tags_of(c, tid):
    return c.get(f"/api/titles/{tid}").json()["tags"]


MERGE = [{"kind": "rename", "field": "tags", "values": ["Romance", "романтика"], "to": "romance"}]


def test_fields_and_values_in_a_scope(client):
    title(client, "A", tags=["romance", "action"], prov={"tags": "manual"})
    title(client, "B", type_="manhwa", tags=["Romance"], chapters=[("1", "en", "g")])
    got = client.post("/api/metadata/fields", json={}).json()
    assert got["inScope"] == 2
    fields = {f["id"]: f for f in got["fields"]}
    assert fields["tags"]["distinct"] == 3 and fields["tags"]["kind"] == "title"
    assert fields["group"]["kind"] == "chapter" and fields["language"]["distinct"] == 1
    assert "cover" not in fields

    manga = client.post("/api/metadata/values", json={
        "field": "tags", "scope": {"f": ["type:manga"]}}).json()
    assert [(v["value"], v["titles"], v["typed"]) for v in manga] == [("action", 1, 1), ("romance", 1, 1)]


def test_preview_then_run_then_revert(client):
    a = title(client, "A", tags=["Romance"])
    b = title(client, "B", tags=["романтика", "x"])
    title(client, "C", tags=["romance"])
    pv = client.post("/api/metadata/preview", json={"actions": MERGE}).json()
    assert (pv["inScope"], pv["titles"], pv["untouched"]) == (3, 2, 1)

    run = client.post("/api/metadata/run", json={"actions": MERGE, "remember": True}).json()
    assert run["changed"] == 2 and not run["stopped"]
    assert [r["match"] for r in run["rules"]] == [["Romance", "романтика"]]
    assert tags_of(client, a) == ["romance"] and tags_of(client, b) == ["romance", "x"]

    history = client.get("/api/metadata/runs").json()
    assert history[0]["summary"] == "merged tags Romance, романтика → romance"
    back = client.post(f"/api/metadata/runs/{run['run']}/revert").json()
    assert back["reverted"] == 2
    assert tags_of(client, a) == ["Romance"] and tags_of(client, b) == ["романтика", "x"]
    assert client.get("/api/metadata/runs").json()[0]["revertedAt"]


def test_a_selection_scope_and_what_cannot_mean_anything(client):
    a = title(client, "A", tags=["x"])
    b = title(client, "B", tags=["x"])
    client.post("/api/metadata/run", json={"scope": {"ids": [b]}, "actions": [
        {"kind": "rename", "field": "tags", "values": ["x"], "to": "y"}]})
    assert tags_of(client, a) == ["x"] and tags_of(client, b) == ["y"]
    bad = client.post("/api/metadata/preview", json={"actions": [
        {"kind": "rename", "field": "tags", "values": ["x"]}]})
    assert bad.status_code == 422 and "rename into" in bad.json()["detail"]
    assert client.post("/api/metadata/runs/999/revert").status_code == 404


def test_rules_are_kept_applied_and_counted(client, tmp_path):
    saved = client.put("/api/metadata/rules", json={"rules": [
        {"field": "tags", "match": ["Romance"], "action": "rename", "to": "romance"},
        {"field": "tags", "match": ["completed"], "action": "move", "toField": "status"},
    ]}).json()["rules"]
    assert len(saved) == 2 and all(r["id"] for r in saved)
    assert (tmp_path / "lib" / "rules.json").is_file()

    out = client.post("/api/metadata/rules/apply", json={
        "values": {"tags": ["ROMANCE", "Completed", "drama"]}}).json()
    assert out["values"] == {"tags": ["romance", "drama"], "status": "completed"}
    assert [(f["n"], f["field"]) for f in out["fired"]] == [(1, "tags"), (2, "tags")]
    stats = client.get("/api/metadata/rules").json()["stats"]
    assert stats[saved[0]["id"]]["fired"] == 1

    title(client, "A", tags=["Romance"])
    values = client.post("/api/metadata/values", json={"field": "tags"}).json()
    assert [(v["value"], v["rule"], v["ruleText"]) for v in values] == [("Romance", 1, "→ romance")]

    bad = client.put("/api/metadata/rules", json={"rules": [
        {"field": "tags", "match": [], "action": "remove"}]})
    assert bad.status_code == 422


def test_an_unreadable_rules_file_says_so(client, tmp_path):
    (tmp_path / "lib" / "rules.json").write_text("{", encoding="utf-8")
    assert client.get("/api/metadata/rules").status_code == 409
    assert client.post("/api/metadata/rules/apply", json={"values": {"tags": ["x"]}}).status_code == 409


def test_a_rule_applied_to_titles_already_stored(client):
    a = title(client, "A", tags=["ROMANCE"])
    b = title(client, "B", tags=["Romance", "x"])
    rid = client.put("/api/metadata/rules", json={"rules": [
        {"field": "tags", "match": ["romance"], "action": "rename", "to": "romance"}]}).json()["rules"][0]["id"]
    pv = client.post(f"/api/metadata/rules/{rid}/preview", json={}).json()
    assert pv["titles"] == 2
    client.post(f"/api/metadata/rules/{rid}/run", json={})
    assert tags_of(client, a) == ["romance"] and tags_of(client, b) == ["romance", "x"]
    assert client.post(f"/api/metadata/rules/{rid}/preview", json={}).status_code == 409  # nothing left


def test_chapter_rules_name_new_chapters_and_leave_stored_ones(client):
    client.put("/api/metadata/rules", json={"rules": [
        {"field": "language", "match": ["English"], "action": "rename", "to": "EN"},
        {"field": "group", "match": ["TeamX"], "action": "rename", "to": "Team X",
         "domains": ["scans.example"]}]})
    tid = title(client, "A", source="https://scans.example/a")
    meta = {"title": "A", "type": "manga", "source": {"domain": "scans.example", "url": "https://scans.example/a"}}

    # a chapter added the way the app adds one: a row with no id, committed
    t = client.put(f"/api/titles/{tid}", json={"meta": meta, "provenance": {}, "chapters": [
        {"id": "", "num": "1", "lang": "English", "group": "TeamX"}]}).json()
    assert [(c["lang"], c["group"]) for c in t["chapters"]] == [("EN", "Team X")]
    assert t["chapters"][0]["id"]

    # a later commit: the stored row stays as it is, a new row is named by the rules
    rows = [{**t["chapters"][0], "lang": "English"},
            {"id": "", "num": "2", "lang": "English", "group": "TeamX", "url": "https://other.example/2"}]
    after = client.put(f"/api/titles/{tid}", json={"meta": meta, "provenance": {}, "chapters": rows}).json()
    assert [(c["num"], c["lang"], c["group"]) for c in after["chapters"]] == [
        ("1", "English", "Team X"), ("2", "EN", "TeamX")]  # the site rule held for its site only


def test_type_rename_through_the_api_renames_the_shelf(client, tmp_path):
    ids = [title(client, f"W{i}", type_="manhwa") for i in range(2)]
    pv = client.post("/api/metadata/preview", json={"actions": [
        {"kind": "rename", "field": "type", "values": ["manhwa"], "to": "Webtoon"}]}).json()
    assert pv["shelfRename"] == ["manhwa", "webtoon"] and pv["shelfMoves"] == 2
    client.post("/api/metadata/run", json={"actions": [
        {"kind": "rename", "field": "type", "values": ["manhwa"], "to": "Webtoon"}]})
    assert not (tmp_path / "lib" / "manhwa").exists()
    assert all(client.get(f"/api/titles/{t}").json()["type"] == "webtoon" for t in ids)
    lib_types = {t["type"] for t in client.get("/api/library").json()}
    assert lib_types == {"webtoon"}
    assert json.loads((tmp_path / "lib" / "webtoon" / ids[0] / "title.json").read_text("utf-8"))["meta"]["type"] == "webtoon"


def test_a_rule_can_be_tried_before_it_is_saved(client):
    out = client.post("/api/metadata/rules/apply", json={
        "values": {"tags": ["Completed (12)", "x"]},
        "rules": [{"field": "tags", "match": ["completed (12)"], "action": "move", "toField": "status"}]}).json()
    assert out["values"] == {"tags": ["x"], "status": "completed (12)"}
    assert client.get("/api/metadata/rules").json() == {"rules": [], "stats": {}}  # nothing kept, nothing counted


def test_a_run_is_reverted_once(client):
    title(client, "A", tags=["x"])
    run = client.post("/api/metadata/run", json={"actions": [
        {"kind": "rename", "field": "tags", "values": ["x"], "to": "y"}]}).json()
    assert client.post(f"/api/metadata/runs/{run['run']}/revert").status_code == 200
    assert client.post(f"/api/metadata/runs/{run['run']}/revert").status_code == 409


def test_a_site_rule_applied_to_stored_titles_reaches_only_its_site(client):
    a = title(client, "A", tags=["Romance"], source="https://www.scans.example/a")
    b = title(client, "B", tags=["Romance"], source="https://other.example/b")
    rid = client.put("/api/metadata/rules", json={"rules": [
        {"field": "tags", "match": ["romance"], "action": "rename", "to": "romance",
         "domains": ["scans.example"]}]}).json()["rules"][0]["id"]
    assert client.post(f"/api/metadata/rules/{rid}/preview", json={}).json()["titles"] == 1
    client.post(f"/api/metadata/rules/{rid}/run", json={})
    assert tags_of(client, a) == ["romance"] and tags_of(client, b) == ["Romance"]


def test_a_chapter_stored_before_a_rule_is_still_found_by_its_spelling(client, tmp_path):
    tid = title(client, "A")
    meta = {"title": "A", "type": "manga"}
    t = client.put(f"/api/titles/{tid}", json={"meta": meta, "provenance": {}, "chapters": [
        {"id": "", "num": "1", "lang": "English", "group": "g"}]}).json()
    client.put("/api/metadata/rules", json={"rules": [
        {"field": "language", "match": ["English"], "action": "rename", "to": "EN"}]})
    # the same chapter, captured again: adopted, not doubled
    again = client.put(f"/api/titles/{tid}", json={"meta": meta, "provenance": {}, "chapters": [
        {"id": "", "num": "1", "lang": "English", "group": "g"}]}).json()
    assert [c["id"] for c in again["chapters"]] == [t["chapters"][0]["id"]]


def test_the_library_does_not_switch_under_a_running_pass(client, tmp_path):
    from app.passes import PASS
    PASS.update(running=True, op="metadata: something")
    try:
        r = client.put("/api/settings/library-path", json={"path": str(tmp_path / "other")})
        assert r.status_code == 409
    finally:
        PASS.update(running=False, op="")


def test_a_broken_rules_file_does_not_take_the_values_down(client, tmp_path):
    title(client, "A", tags=["x"])
    (tmp_path / "lib" / "rules.json").write_text("{", encoding="utf-8")
    values = client.post("/api/metadata/values", json={"field": "tags"})
    assert values.status_code == 200 and values.json()[0]["rule"] == 0
