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


# ---- a deleted field definition is not a licence to shred what was typed ----

@pytest.fixture
def client(tmp_path):
    lib = Library(tmp_path / "fields")
    with TestClient(create_app(lib)) as c:
        yield c
    lib.close()


def test_removing_a_field_keeps_its_values_through_the_next_save(client):
    """The editor builds itself from the registry, so a definition that was
    deleted stops being asked for — and a commit that replaced meta wholesale
    would take every title's values with it on the next save."""
    client.put("/api/fields/shelf", json={"label": "Shelf", "type": "text"})
    client.post("/api/titles", json={
        "meta": {"title": "Kept", "custom": {"shelf": "box 3"}}})
    assert client.get("/api/titles/kept").json()["custom"]["shelf"] == "box 3"

    client.delete("/api/fields/shelf")
    assert "shelf" not in {f["id"] for f in client.get("/api/fields").json()}

    # the editor now knows nothing about `shelf`, so it commits without it
    r = client.put("/api/titles/kept", json={"meta": {"title": "Kept", "custom": {}}})
    assert r.status_code == 200

    # re-defining it must bring the value back, as the removal promised
    client.put("/api/fields/shelf", json={"label": "Shelf", "type": "text"})
    assert client.get("/api/titles/kept").json()["custom"]["shelf"] == "box 3"


def test_clearing_a_field_that_is_still_offered_still_clears_it(client):
    """The rule is "absent means never asked", not "never delete anything": a
    field the editor DOES offer arrives empty when you empty it."""
    client.put("/api/fields/shelf", json={"label": "Shelf", "type": "text"})
    client.post("/api/titles", json={
        "meta": {"title": "Cleared", "custom": {"shelf": "box 3"}}})
    client.put("/api/titles/cleared", json={
        "meta": {"title": "Cleared", "custom": {"shelf": ""}}})
    assert client.get("/api/titles/cleared").json()["custom"]["shelf"] == ""


def test_changing_a_field_type_converts_what_titles_already_hold(client):
    """A definition that changed without its data leaves the vault holding a
    list under a field that now says text — and every screen then renders that
    by whatever its own accident is."""
    client.put("/api/fields/mood", json={"label": "Mood", "type": "list"})
    client.post("/api/titles", json={
        "meta": {"title": "Two", "custom": {"mood": ["tense", "warm"]}}})

    client.put("/api/fields/mood", json={"label": "Mood", "type": "text"})
    assert client.get("/api/titles/two").json()["custom"]["mood"] == "tense, warm"

    # and back, on the same separator
    client.put("/api/fields/mood", json={"label": "Mood", "type": "list"})
    assert client.get("/api/titles/two").json()["custom"]["mood"] == ["tense", "warm"]


def test_the_separator_is_the_callers_because_only_they_know_the_values(client):
    """A comma is right for tags and wrong for names that contain commas."""
    client.put("/api/fields/people", json={"label": "People", "type": "list"})
    client.post("/api/titles", json={
        "meta": {"title": "Names", "custom": {"people": ["Ito, Junji", "Mori, Kaoru"]}}})

    client.put("/api/fields/people", json={"label": "People", "type": "text", "join": " | "})
    assert client.get("/api/titles/names").json()["custom"]["people"] == "Ito, Junji | Mori, Kaoru"

    client.put("/api/fields/people", json={"label": "People", "type": "list", "join": " | "})
    assert client.get("/api/titles/names").json()["custom"]["people"] == ["Ito, Junji", "Mori, Kaoru"]


def test_a_type_change_touches_nothing_else_about_the_title(client):
    """A retype has no draft: it must not go near the user layer or the rows."""
    client.put("/api/fields/mood", json={"label": "Mood", "type": "list"})
    client.post("/api/titles", json={
        "meta": {"title": "Kept", "tags": ["x"]},
        "chapters": [{"id": "", "num": "1", "lang": "EN", "group": "g"}]})
    client.patch("/api/titles/kept/user", json={"fav": True, "rating": 4})
    client.put("/api/titles/kept", json={
        "meta": {"title": "Kept", "tags": ["x"], "custom": {"mood": ["tense"]}},
        "chapters": [{"id": "", "num": "1", "lang": "EN", "group": "g"}]})

    client.put("/api/fields/mood", json={"label": "Mood", "type": "text"})
    got = client.get("/api/titles/kept").json()
    assert got["custom"]["mood"] == "tense"
    assert got["fav"] is True and got["rating"] == 4
    assert got["tags"] == ["x"] and len(got["chapters"]) == 1


def test_prose_is_never_offered_as_a_filter(client):
    """A paragraph has no vocabulary to tick, so "description" is not a text
    field with a box someone has to remember to clear — it cannot be a facet."""
    client.put("/api/fields/notes", json={"label": "Notes", "type": "description",
                                          "facet": True})
    got = next(f for f in client.get("/api/fields").json() if f["id"] == "notes")
    assert got["type"] == "description" and got["control"] == "multiline"
    assert got["facet"] is False

    counts = client.get("/api/library/facets").json()
    assert "notes" not in counts


def test_a_multiline_text_field_from_an_older_vault_reads_as_a_description(tmp_path):
    """`description` was a checkbox before it was a type; a vault written then
    still describes prose, and is read back as prose."""
    from app.library.models import CustomFieldDef
    from app.library import fields as registry
    registry.set_custom([CustomFieldDef(id="notes", label="Notes", type="text",
                                        multiline=True, facet=True)])
    f = registry.by_id()["notes"]
    assert f.type == "description" and f.control == "multiline" and f.facet is False
    registry.set_custom([])


def test_only_a_list_moves_data_between_types(client):
    """Text, description, number and date all store one string, so a change
    among them has nothing to convert — and asking the library otherwise means
    deserialising every document to be told so."""
    from app.library.service import Library
    assert Library.retype_moves_data("list", "text") is True
    assert Library.retype_moves_data("text", "list") is True
    assert Library.retype_moves_data("text", "date") is False
    assert Library.retype_moves_data("description", "number") is False
    assert Library.retype_moves_data("list", "list") is False


def test_a_field_cannot_be_declared_a_number_or_a_date_after_the_fact(client):
    """A field is a number because it was created as one. Letting arbitrary text
    be declared one is the same category error as calling a list of names a
    number — and no check of the current values makes the type honest later."""
    client.put("/api/fields/when", json={"label": "When", "type": "text"})
    client.post("/api/titles", json={"meta": {"title": "Ok", "custom": {"when": "2024-11-03"}}})

    # even a value that reads perfectly as a date does not earn the change
    r = client.put("/api/fields/when", json={"label": "When", "type": "date"})
    assert r.status_code == 409 and "cannot become a date" in r.json()["detail"]
    assert next(f for f in client.get("/api/fields").json() if f["id"] == "when")["type"] == "text"

    # leaving one is always allowed: text, description and list convert freely
    client.put("/api/fields/day", json={"label": "Day", "type": "date"})
    assert client.put("/api/fields/day",
                      json={"label": "Day", "type": "text"}).status_code == 200


def test_removing_a_field_with_its_values_takes_both(client):
    """The other thing someone might mean by "remove" — and it has to be asked
    for, because the default is that a definition is not a licence to shred."""
    client.put("/api/fields/junk", json={"label": "Junk", "type": "list"})
    client.post("/api/titles", json={
        "meta": {"title": "Dirty", "custom": {"junk": ["None"]}}})
    client.post("/api/titles", json={"meta": {"title": "Clean"}})

    client.delete("/api/fields/junk?values=true")
    assert "junk" not in client.get("/api/titles/dirty").json()["custom"]

    # and re-defining it brings nothing back, because nothing was kept
    client.put("/api/fields/junk", json={"label": "Junk", "type": "list"})
    assert client.get("/api/titles/dirty").json()["custom"].get("junk") in (None, [])


def test_prose_suspends_the_filter_flag_rather_than_erasing_it(client):
    """Turning a field into a description and back must not quietly cost it the
    filtering it had — the definition remembers, the registry decides."""
    client.put("/api/fields/note", json={"label": "Note", "type": "text", "facet": True})
    assert next(f for f in client.get("/api/fields").json() if f["id"] == "note")["facet"]

    client.put("/api/fields/note", json={"label": "Note", "type": "description", "facet": True})
    assert not next(f for f in client.get("/api/fields").json() if f["id"] == "note")["facet"]

    lib = client.app.state.library
    stored = next(d for d in lib.vault.custom_fields() if d.id == "note")
    assert stored.facet is True, "the stored definition forgot what was asked for"

    client.put("/api/fields/note", json={"label": "Note", "type": "text", "facet": True})
    assert next(f for f in client.get("/api/fields").json() if f["id"] == "note")["facet"]


def test_saving_a_field_that_changed_nothing_reads_no_titles(client, monkeypatch):
    """Every save used to walk the library and open a title.json per title
    holding the field — to discover there was nothing to do. The index already
    carries every document, so the question is answered without any disk."""
    client.put("/api/fields/mood", json={"label": "Mood", "type": "list"})
    for n in range(4):
        client.post("/api/titles", json={
            "meta": {"title": f"T{n}", "custom": {"mood": ["calm"]}}})

    lib = client.app.state.library
    reads = 0
    original = lib.vault.load

    def counted(tid):
        nonlocal reads
        reads += 1
        return original(tid)

    monkeypatch.setattr(lib.vault, "load", counted)
    client.put("/api/fields/mood", json={"label": "Mood renamed", "type": "list"})
    assert reads == 0, f"a no-op save opened {reads} title(s)"

    # a real change still reaches exactly the titles that need it
    monkeypatch.setattr(lib.vault, "load", counted)
    client.put("/api/fields/mood", json={"label": "Mood", "type": "text"})
    assert client.get("/api/titles/t0").json()["custom"]["mood"] == "calm"
    assert reads <= 8  # four titles, read once to check and once under the lock


def test_a_retype_only_visits_titles_that_hold_the_field(client):
    """One title holds the value; the other four do not. A pass over all five is
    not just wasted work — the shape check has to say "nothing to do" for a
    title with no value at all, or the conversion invents one for it."""
    client.put("/api/fields/mood", json={"label": "Mood", "type": "text"})
    client.post("/api/titles", json={
        "meta": {"title": "Has", "custom": {"mood": "calm, warm"}}})
    for n in range(4):
        client.post("/api/titles", json={"meta": {"title": f"Without {n}"}})

    lib = client.app.state.library
    assert lib.retype_candidates("mood", "list") == ["has"]

    client.put("/api/fields/mood", json={"label": "Mood", "type": "list"})
    assert client.get("/api/titles/has").json()["custom"]["mood"] == ["calm", "warm"]
    for n in range(4):
        got = client.get(f"/api/titles/without-{n}").json()["custom"]
        assert "mood" not in got, f"the retype invented a value: {got}"


def test_an_empty_value_is_never_turned_into_one(client):
    """`str(None)` is the string "None", and a field cleared to "" is still
    cleared: neither is a value the vault should start holding."""
    client.put("/api/fields/mood", json={"label": "Mood", "type": "text"})
    client.post("/api/titles", json={"meta": {"title": "Blank", "custom": {"mood": ""}}})
    client.put("/api/fields/mood", json={"label": "Mood", "type": "list"})
    assert client.get("/api/titles/blank").json()["custom"]["mood"] == ""





def test_a_join_that_could_not_be_undone_is_refused(client):
    """`["Ito, Junji", "Mori"]` joined on ", " reads back as three names, and
    nothing afterwards can tell it was two. The separator is the caller's to
    choose, so the refusal names the value that rules this one out."""
    client.put("/api/fields/people", json={"label": "People", "type": "list"})
    client.post("/api/titles", json={
        "meta": {"title": "Names", "custom": {"people": ["Ito, Junji", "Mori"]}}})

    r = client.put("/api/fields/people", json={"label": "People", "type": "text"})
    assert r.status_code == 409
    assert "could not be undone" in r.json()["detail"] and "Ito, Junji" in r.json()["detail"]
    assert client.get("/api/titles/names").json()["custom"]["people"] == ["Ito, Junji", "Mori"]

    # a separator none of them contains is accepted, and the round trip holds
    assert client.put("/api/fields/people",
                      json={"label": "People", "type": "text", "join": " | "}).status_code == 200
    assert client.get("/api/titles/names").json()["custom"]["people"] == "Ito, Junji | Mori"
    client.put("/api/fields/people", json={"label": "People", "type": "list", "join": " | "})
    assert client.get("/api/titles/names").json()["custom"]["people"] == ["Ito, Junji", "Mori"]


def test_a_conversion_that_changes_nothing_never_rewrites_a_title(client, monkeypatch):
    """Deciding from the value found under the lock, in one read — loading the
    document to look and again to write is a read per title for nothing."""
    client.put("/api/fields/mood", json={"label": "Mood", "type": "list"})
    client.post("/api/titles", json={
        "meta": {"title": "Same", "custom": {"mood": ["calm"]}}})

    lib = client.app.state.library
    writes: list[str] = []
    original = lib.vault._save
    monkeypatch.setattr(lib.vault, "_save",
                        lambda tid, doc: (writes.append(tid), original(tid, doc))[1])

    # list -> list is not a change, so nothing is written
    client.put("/api/fields/mood", json={"label": "Mood renamed", "type": "list"})
    assert writes == []


def test_a_refused_change_leaves_the_definition_exactly_as_it_was(client):
    """A refusal that has already written the definition is the incoherent state
    the rule exists to prevent: the field would say text over a list, and the
    next attempt would see nothing left to convert."""
    client.put("/api/fields/people", json={"label": "People", "type": "list"})
    client.post("/api/titles", json={
        "meta": {"title": "Names", "custom": {"people": ["Ito, Junji", "Mori"]}}})

    assert client.put("/api/fields/people",
                      json={"label": "Renamed", "type": "text"}).status_code == 409
    got = next(f for f in client.get("/api/fields").json() if f["id"] == "people")
    assert got["type"] == "list" and got["label"] == "People"

    assert client.put("/api/fields/day", json={"label": "Day", "type": "date"}).status_code == 200
    assert client.put("/api/fields/day",
                      json={"label": "Renamed", "type": "number"}).status_code == 409
    got = next(f for f in client.get("/api/fields").json() if f["id"] == "day")
    assert got["type"] == "date" and got["label"] == "Day"
