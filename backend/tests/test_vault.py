"""Vault unit tests: ids, atomic layered writes, and the merge rules that make
invariants I3/I7 structural (see design/state-model.md §5, §8)."""
from __future__ import annotations

from app.library.migrations import CURRENT_SCHEMA
from app.library.models import ChapterRow, DraftIn, FieldProvenance, TitleMeta, UserPatch
from app.library.vault import Vault, safe_id


def test_safe_id_transliterates_and_falls_back():
    assert safe_id("Я Хочу Це") == "ia-khochu-tse"   # Cyrillic → Latin slug
    assert safe_id("我想要").startswith("t-")          # CJK → stable hash fallback
    assert safe_id("  Berserk!  ") == "berserk"


def _draft(**meta) -> DraftIn:
    return DraftIn(meta=TitleMeta(**{"title": "X", **meta}))


def test_commit_requires_existing_unless_create(tmp_path):
    v = Vault(tmp_path)
    assert v.commit_meta("nope", _draft()) is None
    assert v.commit_meta("yes", _draft(), create=True) is not None
    assert v.exists("yes")


def test_commit_preserves_user_layer(tmp_path):
    v = Vault(tmp_path)
    v.commit_meta("t", _draft(), create=True)
    v.patch_user("t", UserPatch(fav=True, rating=4, read={"c1": "reading"}))
    doc = v.commit_meta("t", _draft(desc="edited"))
    assert doc is not None
    assert doc.user.fav is True and doc.user.rating == 4
    assert doc.user.read == {"c1": "reading"}
    assert doc.meta.desc == "edited"


def test_patch_user_preserves_meta_layers(tmp_path):
    v = Vault(tmp_path)
    draft = DraftIn(
        meta=TitleMeta(title="X", desc="original"),
        provenance={"desc": FieldProvenance(origin="manual")},
        chapters=[ChapterRow(id="c1", num="1")],
    )
    v.commit_meta("t", draft, create=True)
    doc = v.patch_user("t", UserPatch(rating=2))
    assert doc is not None
    assert doc.meta.desc == "original"
    assert doc.provenance["desc"].origin == "manual"
    assert [c.id for c in doc.chapters] == ["c1"]


def test_on_disk_document_shape(tmp_path):
    v = Vault(tmp_path)
    v.commit_meta("t", _draft(), create=True)
    raw = next(tmp_path.rglob("title.json")).read_text(encoding="utf-8")  # wherever its type shelf is
    assert f'"schema": {CURRENT_SCHEMA}' in raw   # serialized by alias
    for section in ('"meta"', '"provenance"', '"chapters"', '"user"'):
        assert section in raw
    assert not list(tmp_path.rglob("*.tmp"))  # atomic writes leave no droppings


def test_load_roundtrip_and_listing(tmp_path):
    v = Vault(tmp_path)
    v.commit_meta("b", _draft(title="B"), create=True)
    v.commit_meta("a", _draft(title="A"), create=True)
    (tmp_path / "not-a-title").mkdir()          # stray dir without title.json
    (tmp_path / "index.db").write_bytes(b"x")   # the index file itself
    assert v.list_ids() == ["a", "b"]
    doc = v.load("a")
    assert doc is not None and doc.meta.title == "A"


def test_delete_removes_directory(tmp_path):
    v = Vault(tmp_path)
    v.commit_meta("t", _draft(), create=True)
    v.write_cover("t", b"bytes", "jpg")
    assert v.delete("t") is True
    assert not (tmp_path / "t").exists()
    assert v.delete("t") is False


def test_cover_replaces_previous_extension(tmp_path):
    v = Vault(tmp_path)
    v.commit_meta("t", _draft(), create=True)
    v.write_cover("t", b"one", "jpg")
    v.write_cover("t", b"two", "png")
    path = v.cover_path("t")
    assert path is not None and path.suffix == ".png"
    assert path.read_bytes() == b"two"
    assert not (tmp_path / "t" / "cover.jpg").exists()


# ---- the per-type shelf layout ----

def test_titles_live_on_type_shelves(tmp_path):
    from app.library.service import Library
    from app.library.models import DraftIn, TitleMeta
    lib = Library(tmp_path)
    lib.create(DraftIn(meta=TitleMeta(title="Berserk", type="manga")))
    lib.create(DraftIn(meta=TitleMeta(title="Nameless")))
    assert (tmp_path / "manga" / "berserk" / "title.json").is_file()
    assert (tmp_path / "other" / "nameless" / "title.json").is_file()
    # a TYPE change physically moves the directory (and its media) …
    t = lib.get("berserk")
    lib.commit("berserk", DraftIn(meta=TitleMeta(title="Berserk", type="image set")))
    assert (tmp_path / "image-set" / "berserk" / "title.json").is_file()
    assert not (tmp_path / "manga").exists()  # the emptied shelf is swept
    # … and reads keep working transparently
    assert lib.get("berserk").type == "image set"
    lib.close()


def test_flat_layout_migrates_on_open(tmp_path):
    import json
    from app.library.service import Library
    legacy = tmp_path / "old-one"
    legacy.mkdir(parents=True)
    (legacy / "title.json").write_text(json.dumps({
        "schema": 1, "meta": {"title": "Old One", "type": "manhwa"},
        "provenance": {}, "chapters": [], "user": {},
    }), encoding="utf-8")
    lib = Library(tmp_path)
    assert (tmp_path / "manhwa" / "old-one" / "title.json").is_file()
    assert not legacy.exists()
    assert lib.get("old-one").title == "Old One"
    lib.close()


def test_characters_field_and_facet(tmp_path):
    from app.library.service import Library
    from app.library.models import DraftIn, TitleMeta
    lib = Library(tmp_path)
    lib.create(DraftIn(meta=TitleMeta(title="A", characters=["Guts", "Griffith"])))
    lib.create(DraftIn(meta=TitleMeta(title="B", characters=["Guts"])))
    assert sorted(t.id for t in lib.query(include={"characters": ("Guts",)})) == ["a", "b"]
    assert [t.id for t in lib.query(include={"characters": ("Griffith",)})] == ["a"]
    assert [t.id for t in lib.query(exclude={"characters": ("Griffith",)})] == ["b"]
    chars = {x["v"]: x["n"] for x in lib.facets()["characters"]}
    assert chars == {"Guts": 2, "Griffith": 1}
    lib.close()
