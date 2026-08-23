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
