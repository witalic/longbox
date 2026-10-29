"""Value pages (design/value-pages.md): every value of a list field has a page —
what the library knows about it, and what the owner wrote — kept in the vault,
following its value through a rename and back through a revert."""
from __future__ import annotations

import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

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


def title(c, name, *, tags=(), genres=(), authors=(), artists=(), source="", prov=None):
    return c.post("/api/titles", json={
        "meta": {"title": name, "type": "manga", "tags": list(tags), "genres": list(genres),
                 "authors": list(authors), "artists": list(artists),
                 "source": {"domain": source, "url": f"https://{source}/x" if source else ""}},
        "provenance": {k: {"origin": v} for k, v in (prov or {"tags": "auto"}).items()},
        "chapters": [],
    }).json()["id"]


def page(c, field, value):
    return c.get(f"/api/pages/{field}/page", params={"value": value}).json()


def save(c, field, value, **body):
    return c.put(f"/api/pages/{field}/page", params={"value": value}, json=body)


def jpeg(color=(200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, "JPEG")
    return buf.getvalue()


def test_a_value_nobody_wrote_about_still_has_a_page(client):
    title(client, "A", tags=["time loop", "mystery"], genres=["sci-fi"], source="mangadex.org")
    title(client, "B", tags=["Time Loop", "mystery"], source="mangadex.org")
    title(client, "C", tags=["time loop"], prov={"tags": "manual"})
    title(client, "D", tags=["romance"])

    p = page(client, "tags", "TIME LOOP")
    assert (p["kind"], p["value"], p["stored"], p["titles"]) == ("Tag", "time loop", False, 3)
    assert {w["title"] for w in p["works"]} == {"A", "B", "C"}
    near = {(n["field"], n["value"]): n["n"] for g in p["groups"] for n in g["items"]}
    assert near == {("tags", "mystery"): 2, ("genres", "sci-fi"): 1}
    assert [(s["source"], s["typed"], s["n"]) for s in p["sources"]] == [
        ("mangadex.org", False, 2), ("", True, 1)]


def test_a_person_has_one_page_whether_they_wrote_or_drew(client, tmp_path):
    title(client, "A", authors=["Mitsuki Arata"])
    title(client, "B", artists=["mitsuki arata"], authors=["Kei Yamada"])
    client.post("/api/authors/mitsuki-arata/favorite", params={"value": True})

    p = page(client, "artists", "Mitsuki Arata")
    assert (p["kind"], p["vocab"], p["titles"], p["roles"]) == ("Person", "authors", 2,
                                                                {"authors": 1, "artists": 1})
    assert p["fav"] is True
    assert [n["value"] for g in p["groups"] if g["label"] == "Worked with" for n in g["items"]] == ["Kei Yamada"]
    save(client, "authors", "Mitsuki Arata", desc="Draws fantasy.")
    assert page(client, "artists", "MITSUKI ARATA")["desc"] == "Draws fantasy."


def test_what_the_owner_writes_lives_in_the_vault(client, tmp_path):
    title(client, "A", tags=["iyashikei"])
    r = save(client, "tags", "iyashikei", desc="  Healing slice of life.  ",
             links=[{"url": "https://en.wikipedia.org/wiki/Iyashikei", "label": "Wikipedia"}])
    assert r.status_code == 200 and r.json()["desc"] == "Healing slice of life."
    doc = json.loads((tmp_path / "lib" / ".pages" / "tags.json").read_text("utf-8"))
    assert [p["value"] for p in doc["pages"]] == ["iyashikei"]

    # another library object on the same folder reads it, and the folder is no shelf
    other = Library(tmp_path / "lib")
    try:
        assert other.pages.get("tags", "Iyashikei").desc == "Healing slice of life."
        assert ".pages" not in {t.id for t in other.query()} and other.count() == 1
    finally:
        other.close()

    listed = client.get("/api/pages/tags").json()
    assert [(s["value"], s["held"], s["excerpt"]) for s in listed] == [
        ("iyashikei", True, "Healing slice of life.")]
    # a page left with nothing written is not kept
    save(client, "tags", "iyashikei", desc="", links=[])
    assert page(client, "tags", "iyashikei")["stored"] is False
    assert client.get("/api/pages/tags").json() == []


def test_a_link_is_a_web_address_and_flags_have_no_pages(client):
    title(client, "A", tags=["x"])
    assert save(client, "tags", "x", links=[{"url": "javascript:alert(1)"}]).status_code == 422
    assert client.get("/api/pages/flags/page", params={"value": "adult"}).status_code == 404
    assert client.get("/api/pages/type/page", params={"value": "manga"}).status_code == 404


def test_other_spellings_are_rename_rules(client):
    title(client, "A", authors=["Mitsuki Arata"])
    title(client, "B", authors=["Arata M."])
    r = save(client, "authors", "Mitsuki Arata", desc="", spellings=["Arata M.", "新田みつき"])
    p = r.json()
    assert sorted(p["spellings"]) == ["Arata M.", "新田みつき"]
    assert p["held"] == {"Arata M.": 1, "新田みつき": 0}
    rules = client.get("/api/metadata/rules").json()["rules"]
    # a person's spelling holds wherever a person is written — as author and as artist
    assert sorted((r["field"], tuple(r["match"]), r["to"]) for r in rules) == [
        ("artists", ("Arata M.", "新田みつき"), "Mitsuki Arata"),
        ("authors", ("Arata M.", "新田みつき"), "Mitsuki Arata")]
    # what arrives from now on is renamed by them
    out = client.post("/api/metadata/rules/apply", json={"values": {"authors": ["arata m."]}}).json()
    assert out["values"]["authors"] == ["Mitsuki Arata"]

    # taking a spelling off leaves the rule; a rule left with nothing goes
    save(client, "authors", "Mitsuki Arata", spellings=["Arata M."])
    rules = client.get("/api/metadata/rules").json()["rules"]
    assert {tuple(r["match"]) for r in rules} == {("Arata M.",)}
    save(client, "authors", "Mitsuki Arata", spellings=[])
    assert client.get("/api/metadata/rules").json()["rules"] == []


def test_the_rules_about_a_value_show_on_its_page(client):
    title(client, "A", tags=["time loop"])
    client.put("/api/metadata/rules", json={"rules": [
        {"field": "tags", "match": ["timeloop"], "action": "rename", "to": "time loop"},
        {"field": "tags", "match": ["time loop"], "action": "add", "toField": "genres", "to": "sci-fi"},
        {"field": "genres", "match": ["loop"], "action": "add", "toField": "tags", "to": "time loop",
         "domains": ["example.org"]}]})
    kinds = [(n["kind"], n["n"]) for n in page(client, "tags", "time loop")["rules"]]
    assert kinds == [("spelling", 1), ("implies", 2), ("impliedBy", 3)]


def run_rename(c, field, values, to):
    return c.post("/api/metadata/run", json={"actions": [
        {"kind": "rename", "field": field, "values": values, "to": to}]}).json()


def test_a_page_follows_its_value_through_a_rename_and_back(client):
    title(client, "A", tags=["timeloop"])
    title(client, "B", tags=["time loop"])
    save(client, "tags", "timeloop", desc="Old notes.", links=[{"url": "https://a.example/"}])
    save(client, "tags", "time loop", desc="The day repeats.", links=[{"url": "https://b.example/"}])

    run = run_rename(client, "tags", ["timeloop"], "time loop")
    merged = page(client, "tags", "time loop")
    assert merged["desc"] == "The day repeats.\n\ntimeloop: Old notes."  # nothing written is dropped
    assert [l["url"] for l in merged["links"]] == ["https://b.example/", "https://a.example/"]
    assert page(client, "tags", "timeloop")["stored"] is False

    client.post(f"/api/metadata/runs/{run['run']}/revert")
    assert page(client, "tags", "timeloop")["desc"] == "Old notes."
    assert page(client, "tags", "time loop")["desc"] == "The day repeats."


def test_a_value_still_held_elsewhere_keeps_its_page(client):
    a = title(client, "A", tags=["timeloop"])
    title(client, "B", tags=["timeloop"])
    save(client, "tags", "timeloop", desc="Mine.")
    client.post("/api/metadata/run", json={"scope": {"ids": [a]}, "actions": [
        {"kind": "rename", "field": "tags", "values": ["timeloop"], "to": "time loop"}]})
    assert page(client, "tags", "timeloop")["desc"] == "Mine."
    assert page(client, "tags", "time loop")["stored"] is False


def test_a_page_whose_value_is_gone_stays_until_deleted(client):
    title(client, "A", tags=["iyashikei"])
    save(client, "tags", "iyashikei", desc="Healing.")
    assert client.delete("/api/pages/tags/page", params={"value": "iyashikei"}).status_code == 409

    client.post("/api/metadata/run", json={"actions": [
        {"kind": "remove", "field": "tags", "values": ["iyashikei"]}]})
    listed = client.get("/api/pages/tags").json()
    assert [(s["value"], s["held"]) for s in listed] == [("iyashikei", False)]
    assert page(client, "tags", "iyashikei")["desc"] == "Healing."

    assert client.delete("/api/pages/tags/page", params={"value": "iyashikei"}).status_code == 200
    assert client.get("/api/pages/tags").json() == []
    assert client.delete("/api/pages/tags/page", params={"value": "iyashikei"}).status_code == 404


def test_an_image_is_a_copy_with_a_name_of_its_own(client, tmp_path):
    tid = title(client, "A", tags=["iyashikei"])
    put = client.put("/api/pages/tags/image", params={"value": "iyashikei"}, content=jpeg(),
                     headers={"content-type": "image/jpeg"})
    assert put.status_code == 200
    first = put.json()["image"]
    d = tmp_path / "lib" / ".pages" / "images" / "tags"
    assert len(list(d.iterdir())) == 1
    assert client.get(first).content[:3] == b"\xff\xd8\xff"
    assert client.get(first + "&w=32").status_code == 200

    # a cover of one of its works, copied — the old image file goes
    client.post(f"/api/titles/{tid}/cover", json={"data": base64.b64encode(jpeg((0, 90, 0))).decode(),
                                                  "contentType": "image/jpeg"})
    second = client.post("/api/pages/tags/image/from-title", params={"value": "iyashikei"},
                         json={"titleId": tid}).json()["image"]
    assert second != first and len(list(d.iterdir())) == 1

    assert client.put("/api/pages/tags/image", params={"value": "iyashikei"}, content=b"not an image",
                      headers={"content-type": "image/jpeg"}).status_code == 422
    assert client.delete("/api/pages/tags/image", params={"value": "iyashikei"}).json()["image"] == ""
    assert list(d.iterdir()) == []


def test_listings_say_which_values_are_described(client):
    title(client, "A", tags=["iyashikei", "comedy"])
    save(client, "tags", "Iyashikei", desc="Healing slice of life.")
    groups = {g["value"]: g for g in client.get("/api/browse/tags").json()}
    assert groups["iyashikei"]["described"] and groups["iyashikei"]["excerpt"] == "Healing slice of life."
    assert not groups["comedy"]["described"]
    vals = {v["value"]: v["page"] for v in client.post("/api/metadata/values", json={"field": "tags"}).json()}
    assert vals == {"iyashikei": True, "comedy": False}


def test_a_peek_is_the_page_in_brief(client):
    title(client, "A", tags=["iyashikei"])
    save(client, "tags", "iyashikei", desc="word " * 200)
    pk = client.get("/api/pages/tags/peek", params={"value": "iyashikei"}).json()
    assert pk["titles"] == 1 and pk["excerpt"].endswith("…") and len(pk["excerpt"]) <= 281


def test_an_unreadable_pages_file_is_never_written_over(client, tmp_path):
    title(client, "A", tags=["x"])
    (tmp_path / "lib" / ".pages").mkdir()
    (tmp_path / "lib" / ".pages" / "tags.json").write_text("{ broken", "utf-8")
    assert save(client, "tags", "x", desc="y").status_code == 409
    assert (tmp_path / "lib" / ".pages" / "tags.json").read_text("utf-8") == "{ broken"
    # Browse still lists the values; it only cannot say which are described
    assert [g["value"] for g in client.get("/api/browse/tags").json()] == ["x"]


# ---- what the review of the first cut caught ----------------------------------

def test_saving_a_page_never_takes_a_case_merge_rule_apart(client):
    """A rule merging spellings that differ only in case (`romance` → `Romance`)
    is no "other spelling" the editor shows — saving the page keeps it whole."""
    title(client, "A", tags=["Romance"])
    client.put("/api/metadata/rules", json={"rules": [
        {"field": "tags", "match": ["romance", "romantic"], "action": "rename", "to": "Romance"},
        {"field": "tags", "match": ["ROMANCE"], "action": "rename", "to": "Romance"}]})
    shown = page(client, "tags", "Romance")["spellings"]
    assert shown == ["romantic"]
    assert save(client, "tags", "Romance", desc="Love.", spellings=shown).status_code == 200
    rules = client.get("/api/metadata/rules").json()["rules"]
    assert [r["match"] for r in rules] == [["romance", "romantic"], ["ROMANCE"]]


def test_a_rule_left_behind_by_a_deleted_field_never_blocks_a_page(client):
    title(client, "A", tags=["x"])
    client.put("/api/fields/mood", json={"label": "Mood", "type": "list"})
    client.put("/api/metadata/rules", json={"rules": [
        {"field": "mood", "match": ["sad"], "action": "rename", "to": "melancholy"}]})
    client.delete("/api/fields/mood")
    r = save(client, "tags", "x", desc="Something.", spellings=["ex"])
    assert r.status_code == 200 and r.json()["spellings"] == ["ex"]
    assert {r["field"] for r in client.get("/api/metadata/rules").json()["rules"]} == {"mood", "tags"}


def test_a_spelling_lands_on_the_value_as_titles_write_it(client):
    title(client, "A", tags=["Romance"])
    title(client, "B", tags=["Romance"])
    title(client, "C", tags=["ROMANCE"])
    save(client, "tags", "ROMANCE", spellings=["romcom"])
    rules = client.get("/api/metadata/rules").json()["rules"]
    assert [(r["match"], r["to"]) for r in rules] == [(["romcom"], "Romance")]


def test_a_blank_value_has_no_page(client):
    assert save(client, "tags", " ", desc="x").status_code == 422
    assert client.put("/api/pages/tags/image", params={"value": " "}, content=jpeg(),
                      headers={"content-type": "image/jpeg"}).status_code == 422


def test_a_title_holding_a_name_twice_counts_once(client):
    title(client, "A", authors=["Arata M."], artists=["Arata M."])
    title(client, "B", authors=["Mitsuki Arata"])
    p = save(client, "authors", "Mitsuki Arata", spellings=["Arata M."]).json()
    assert p["held"] == {"Arata M.": 1}


def test_a_pages_file_that_cannot_be_read_never_fails_a_run(client, tmp_path):
    title(client, "A", tags=["timeloop"], prov={"tags": "auto"})
    c = client.post("/api/titles", json={"meta": {"title": "B", "type": "manga", "characters": ["Gutz"]},
                                         "provenance": {}, "chapters": []}).json()["id"]
    save(client, "tags", "timeloop", desc="Mine.")
    (tmp_path / "lib" / ".pages" / "characters.json").write_text('{"schema": 99, "pages": []}', "utf-8")
    run = client.post("/api/metadata/run", json={"actions": [
        {"kind": "rename", "field": "tags", "values": ["timeloop"], "to": "time loop"},
        {"kind": "rename", "field": "characters", "values": ["Gutz"], "to": "Guts"}]})
    assert run.status_code == 200 and run.json()["changed"] == 2
    assert page(client, "tags", "time loop")["desc"] == "Mine."
    assert client.get(f"/api/titles/{c}").json()["characters"] == ["Guts"]

    # a revert that cannot put a page back is not a finished revert
    (tmp_path / "lib" / ".pages" / "tags.json").write_text("{ broken", "utf-8")
    client.post(f"/api/metadata/runs/{run.json()['run']}/revert")
    runs = {r["id"]: r for r in client.get("/api/metadata/runs").json()}
    assert runs[run.json()["run"]]["revertedAt"] == ""


def test_a_custom_field_cannot_take_a_chapter_fields_name(client):
    assert client.put("/api/fields/section", json={"label": "Section", "type": "text"}).status_code == 409


def test_a_page_can_be_read_while_its_document_is_being_written(client):
    """No lock is held across a read: looking a page up from inside a write of
    the same document (a merge reading a neighbour) must not hang."""
    title(client, "A", tags=["x", "y"])
    save(client, "tags", "y", desc="Why.")
    lib = client.app.state.library
    with lib.pages.edit("tags") as pages:
        assert lib.pages.get("tags", "y").desc == "Why."  # the document as it is on disk
        pages["x"] = pages["y"].model_copy(update={"value": "x"})
    # and the copy kept in memory is the new one, not what was read mid-write
    assert lib.pages.get("tags", "x").desc == "Why."


def test_editing_a_written_page_does_not_walk_the_library_for_its_spelling(client, monkeypatch):
    from app.library import pages as P
    title(client, "A", tags=["x"])
    save(client, "tags", "x", desc="First.")
    calls: list[str] = []
    real = P.spelling_of
    monkeypatch.setattr(P, "spelling_of", lambda *a: calls.append("walk") or real(*a))
    save(client, "tags", "x", desc="Second.")
    assert calls == [] and page(client, "tags", "x")["desc"] == "Second."
    save(client, "tags", "x", desc="Third.", spellings=["ex"])  # rules land on the spelling titles use
    assert calls == ["walk"]
