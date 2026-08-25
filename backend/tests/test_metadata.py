"""The metadata layer as it grows: standard fields, and the rules any new one
has to obey (see design/metadata-model.md)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.library.migrations import CURRENT_SCHEMA, migrate
from app.library.models import DraftIn, TitleMeta
from app.library.service import Library
from app.main import create_app
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_a_document_written_before_studio_existed_still_opens(tmp_path):
    """A shape change is a migration: the reader upgrades what it finds, and the
    document on disk is left alone until the next commit writes it back."""
    raw = {"schema": 2, "meta": {"title": "Serial Experiments Lain", "type": "anime"},
           "user": {"fav": True, "position": {}}, "chapters": []}
    upgraded, changed = migrate(dict(raw))

    assert changed is True and upgraded["schema"] == CURRENT_SCHEMA
    assert upgraded["meta"]["studio"] == []
    assert upgraded["user"]["fav"] is True     # the user layer is never touched


def test_a_studio_already_recorded_survives_the_upgrade(tmp_path):
    raw = {"schema": 2, "meta": {"title": "Akira", "studio": ["Akira Committee"]}}
    upgraded, _ = migrate(dict(raw))
    assert upgraded["meta"]["studio"] == ["Akira Committee"]


def test_studio_is_stored_committed_and_read_back(tmp_path):
    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(
        title="Violet Evergarden", type="anime", studio=["Kyoto Animation"])))

    doc_path = next(tmp_path.rglob("*/title.json"))
    on_disk = json.loads(doc_path.read_text(encoding="utf-8"))
    assert on_disk["schema"] == CURRENT_SCHEMA
    assert on_disk["meta"]["studio"] == ["Kyoto Animation"]
    assert lib.get(out.id).studio == ["Kyoto Animation"]
    lib.close()


def test_studio_filters_and_counts_like_any_other_facet(tmp_path):
    """A co-production belongs to every studio that made it, so studio is a
    list and an include filter means ALL of them, as tags do."""
    lib = Library(tmp_path)
    lib.create(DraftIn(meta=TitleMeta(title="A", type="anime", studio=["kyoani"])))
    lib.create(DraftIn(meta=TitleMeta(title="B", type="anime", studio=["kyoani", "aniplex"])))
    lib.create(DraftIn(meta=TitleMeta(title="C", type="manga")))

    with TestClient(create_app(lib)) as c:
        both = c.get("/api/library", params={"f": ["studio:kyoani", "studio:aniplex"]}).json()
        assert [t["title"] for t in both] == ["B"]

        either = c.get("/api/library", params={"f": "studio:kyoani"}).json()
        assert sorted(t["title"] for t in either) == ["A", "B"]

        without = c.get("/api/library", params={"nf": "studio:kyoani"}).json()
        assert [t["title"] for t in without] == ["C"]

        counts = {f["v"]: f["n"] for f in c.get("/api/library/facets").json()["studio"]}
        assert counts == {"kyoani": 2, "aniplex": 1}
    lib.close()


def test_an_edit_to_studio_is_untouchable_by_capture(tmp_path):
    """The provenance rule is per FIELD and knows nothing about which fields
    exist — a new one must inherit it without a second mechanism."""
    lib = Library(tmp_path)
    out = lib.create(DraftIn(
        meta=TitleMeta(title="Ghost in the Shell", studio=["Production I.G"]),
        provenance={"studio": {"origin": "manual"}}))
    assert lib.get(out.id).provenance["studio"].origin == "manual"
    lib.close()


# ---- user-defined fields -------------------------------------------------
#
# The registry is per LIBRARY: the definitions live in the vault beside the data
# they describe, so they travel with it and a second library never inherits them.


def test_a_defined_field_is_served_stored_and_filtered(tmp_path):
    lib = Library(tmp_path)
    with TestClient(create_app(lib)) as c:
        served = c.put("/api/fields/shelf",
                       json={"label": "Shelf", "type": "list"}).json()
        assert [f["id"] for f in served if not f["builtin"]] == ["shelf"]
        shelf = next(f for f in served if f["id"] == "shelf")
        assert (shelf["control"], shelf["facet"]) == ("chips", True)

        for title, values in (("A", ["finished"]), ("B", ["finished", "boxed"]), ("C", [])):
            lib.create(DraftIn(meta=TitleMeta(title=title, custom={"shelf": values})))

        only = c.get("/api/library", params={"f": "shelf:boxed"}).json()
        assert [t["title"] for t in only] == ["B"]

        counts = {f["v"]: f["n"] for f in c.get("/api/library/facets").json()["shelf"]}
        assert counts == {"finished": 2, "boxed": 1}
    lib.close()


def test_deleting_a_field_keeps_the_values_it_held(tmp_path):
    """Removing a definition stops OFFERING the field; shredding what the user
    typed would make the delete button a data-loss trap."""
    lib = Library(tmp_path)
    with TestClient(create_app(lib)) as c:
        c.put("/api/fields/shelf", json={"label": "Shelf", "type": "list"})
        out = lib.create(DraftIn(meta=TitleMeta(title="Berserk", custom={"shelf": ["boxed"]})))

        left = c.delete("/api/fields/shelf").json()
        assert [f["id"] for f in left if not f["builtin"]] == []
        assert lib.vault.load(out.id).meta.custom == {"shelf": ["boxed"]}

        # and re-defining it brings them back, filters included
        c.put("/api/fields/shelf", json={"label": "Shelf", "type": "list"})
        again = c.get("/api/library", params={"f": "shelf:boxed"}).json()
        assert [t["title"] for t in again] == ["Berserk"]
    lib.close()


def test_a_field_id_cannot_shadow_a_builtin_or_be_junk(tmp_path):
    lib = Library(tmp_path)
    with TestClient(create_app(lib)) as c:
        assert c.put("/api/fields/genres", json={"label": "Genres"}).status_code == 409
        assert c.put("/api/fields/Shelf 1", json={"label": "x"}).status_code == 400
        assert c.put("/api/fields/rank", json={"label": "Rank", "type": "colour"}).status_code == 400
        # boolean is a type the editor cannot draw and the filter cannot ask
        # about yet, so it is not offered (design/metadata-model.md §8)
        assert c.put("/api/fields/done", json={"label": "Done", "type": "boolean"}).status_code == 400
        assert c.put("/api/fields/rank", json={"label": "  "}).status_code == 400
        assert c.delete("/api/fields/nope").status_code == 404
    lib.close()


def test_definitions_live_with_the_library_they_describe(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    lib = Library(one)
    with TestClient(create_app(lib)) as c:
        c.put("/api/fields/malscore", json={"label": "MAL score", "type": "number"})
    lib.close()

    other = Library(two)
    with TestClient(create_app(other)) as c:
        assert [f["id"] for f in c.get("/api/fields").json() if not f["builtin"]] == []
    other.close()

    back = Library(one)
    with TestClient(create_app(back)) as c:
        mine = [f for f in c.get("/api/fields").json() if not f["builtin"]]
        assert [(f["id"], f["control"]) for f in mine] == [("malscore", "number")]
    back.close()


def test_a_number_field_is_drawn_as_one_and_stored_as_text(tmp_path):
    """`year` already proves the shape: a number is edited and stored as text so
    that "not set" stays different from 0."""
    lib = Library(tmp_path)
    with TestClient(create_app(lib)) as c:
        served = c.put("/api/fields/malscore",
                       json={"label": "MAL score", "type": "number", "facet": False}).json()
        f = next(x for x in served if x["id"] == "malscore")
        assert (f["control"], f["facet"], f["editable"]) == ("number", False, True)

        out = lib.create(DraftIn(meta=TitleMeta(title="Pluto", custom={"malscore": "8.86"})))
        assert lib.vault.load(out.id).meta.custom == {"malscore": "8.86"}
        # not a facet: it is not offered as one either
        assert "malscore" not in c.get("/api/library/facets").json()
    lib.close()


def test_browse_groups_titles_by_any_list_field(tmp_path):
    """The axis is whatever the registry says is a list — people are not a
    special kind of grouping, only a grouping with two extras."""
    lib = Library(tmp_path)
    with TestClient(create_app(lib)) as c:
        c.put("/api/fields/shelf", json={"label": "Shelf", "type": "list"})
        lib.create(DraftIn(meta=TitleMeta(title="A", studio=["Kyoani"], tags=["slice"],
                                          custom={"shelf": ["boxed"]})))
        lib.create(DraftIn(meta=TitleMeta(title="B", studio=["Kyoani"], authors=["Naoko"],
                                          artists=["Naoko"])))

        studios = c.get("/api/browse/studio").json()
        assert [(g["value"], g["titles"]) for g in studios] == [("Kyoani", 2)]
        assert sorted(w["title"] for w in studios[0]["works"]) == ["A", "B"]
        assert studios[0]["role"] is None and studios[0]["fav"] is False

        # a user-defined list field is an axis like any other
        assert [g["value"] for g in c.get("/api/browse/shelf").json()] == ["boxed"]

        # people keep what only people have
        people = c.get("/api/browse/authors").json()
        assert [(g["value"], g["role"]) for g in people] == [("Naoko", "both")]
        # the favourite endpoint answers with the same groups, not a twin shape
        starred = c.post(f"/api/authors/{people[0]['id']}/favorite?value=true").json()
        assert [(g["value"], g["fav"]) for g in starred] == [("Naoko", True)]

        # what can be an axis comes off the registry the UI already has:
        # list-shaped fields. Asking for anything else answers with nothing.
        types = {f["id"]: f["type"] for f in c.get("/api/fields").json()}
        assert {types[k] for k in ("authors", "studio", "genres", "shelf")} == {"list"}
        assert types["year"] == "text"
        assert c.get("/api/browse/year").json() == []
        assert c.get("/api/browse/nope").status_code == 404
    lib.close()


def test_a_filtered_browse_narrows_the_groups_and_their_contents(tmp_path):
    """Filtering here means: which groups survive, and what is left inside them."""
    lib = Library(tmp_path)
    with TestClient(create_app(lib)) as c:
        lib.create(DraftIn(meta=TitleMeta(title="A", studio=["Kyoani"], genres=["slice"])))
        lib.create(DraftIn(meta=TitleMeta(title="B", studio=["Kyoani"], genres=["action"])))
        lib.create(DraftIn(meta=TitleMeta(title="C", studio=["Bones"], genres=["action"])))

        both = {g["value"]: g["titles"] for g in c.get("/api/browse/studio").json()}
        assert both == {"Bones": 1, "Kyoani": 2}

        slice_only = c.get("/api/browse/studio", params={"f": "genres:slice"}).json()
        # Bones made nothing in that genre, so it is not a row with zero — it is gone
        assert [(g["value"], g["titles"]) for g in slice_only] == [("Kyoani", 1)]
        assert [w["title"] for w in slice_only[0]["works"]] == ["A"]
    lib.close()


def test_a_custom_field_always_says_what_it_takes(tmp_path):
    """A chips control has no border of its own: with an empty placeholder the
    row renders as a label over nothing, and the field reads as broken."""
    lib = Library(tmp_path)
    with TestClient(create_app(lib)) as c:
        served = c.put("/api/fields/shelf", json={"label": "Shelf", "type": "list"}).json()
        shelf = next(f for f in served if f["id"] == "shelf")
        assert shelf["placeholder"] == "add shelf…"

        served = c.put("/api/fields/isbn", json={"label": "ISBN", "type": "text"}).json()
        assert next(f for f in served if f["id"] == "isbn")["placeholder"] == "ISBN"

        # an explicit one still wins
        served = c.put("/api/fields/isbn",
                       json={"label": "ISBN", "type": "text", "placeholder": "978-…"}).json()
        assert next(f for f in served if f["id"] == "isbn")["placeholder"] == "978-…"
    lib.close()
