"""Authors and artists are one field (owner, 2026-11-11): who made the work.

What was written before the fold — titles, recipes, rules, import profiles, a
paused run, the operations journal — is read as the field that holds those
names now, and nothing of it is lost."""
from __future__ import annotations

import json
import os

from app.library import comicinfo
from app.library import folder_import as fi
from app.library import operations as ops
from app.library.migrations import migrate
from app.library.models import DraftIn, TitleMeta
from app.library.rules import Rule, RuleStore, apply
from app.library.service import Library
from app.scraper.models import Recipe


def v5(meta: dict, provenance: dict | None = None) -> dict:
    return {"schema": 5, "meta": {"title": "T", **meta}, "provenance": provenance or {},
            "chapters": [], "user": {}}


def test_the_artists_join_the_authors_each_name_once():
    doc, changed = migrate(v5({"authors": ["Kei Yamada", "Mitsuki Arata"],
                               "artists": ["mitsuki arata", "Rin Sato", ""]}))
    assert changed and doc["schema"] == 6
    assert doc["meta"]["authors"] == ["Kei Yamada", "Mitsuki Arata", "Rin Sato"]
    assert "artists" not in doc["meta"]


def test_artists_the_owner_typed_keep_the_joined_list_theirs():
    typed = {"origin": "manual", "url": "", "recipeVersion": 0}
    caught = {"origin": "auto", "url": "https://x.org/t", "recipeVersion": 3}
    doc, _ = migrate(v5({"authors": ["A"], "artists": ["B"]},
                        {"authors": caught, "artists": typed}))
    assert doc["provenance"] == {"authors": typed}
    # an artists list emptied by hand had nothing to protect
    doc, _ = migrate(v5({"authors": ["A"], "artists": []}, {"authors": caught, "artists": typed}))
    assert doc["provenance"] == {"authors": caught}
    # where the authors had no mark, the artists' fills in
    doc, _ = migrate(v5({"artists": ["B"]}, {"artists": caught}))
    assert doc["meta"]["authors"] == ["B"] and doc["provenance"] == {"authors": caught}


def test_a_title_written_before_shows_its_artists_as_authors(tmp_path):
    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk", authors=["Kentaro Miura"])))
    lib.close()
    doc_path = next(tmp_path.rglob("berserk/title.json"))
    was = doc_path.stat().st_mtime_ns
    raw = json.loads(doc_path.read_text(encoding="utf-8"))
    raw["schema"] = 5
    raw["meta"]["artists"] = ["Studio Gaga"]
    doc_path.write_text(json.dumps(raw), encoding="utf-8")
    # an edit the open finds: a rewrite in the same clock tick would carry the stamp it had
    os.utime(doc_path, ns=(was + 10**9, was + 10**9))

    lib = Library(tmp_path)
    assert lib.get(out.id).authors == ["Kentaro Miura", "Studio Gaga"]
    assert [g.value for g in lib.browse("authors")] == ["Kentaro Miura", "Studio Gaga"]
    lib.close()


def test_comicinfo_credits_whoever_drew_it_as_an_author():
    said = comicinfo.parse(b"<ComicInfo><Writer>Aoi Mori</Writer>"
                           b"<Penciller>Kenji Sato, aoi mori</Penciller></ComicInfo>")
    assert said["authors"] == ["Aoi Mori", "Kenji Sato"]
    said = comicinfo.parse(b"<ComicInfo><Penciller>Kenji Sato</Penciller></ComicInfo>")
    assert said["authors"] == ["Kenji Sato"]


def _rule(selector: str) -> dict:
    return {"mode": "list", "candidates": [{"kind": "css", "selector": selector}]}


def test_a_recipe_that_captured_artists_captures_them_as_authors():
    r = Recipe.model_validate({"domain": "x.org", "fields": {"artists": _rule("a.artist")},
                               "hidden": ["authors", "studio"]})
    assert list(r.fields) == ["authors"]
    assert [c.selector for c in r.fields["authors"].candidates] == ["a.artist"]
    assert r.hidden == ["studio"]  # the site fills the people row after all


def test_a_recipe_with_both_tries_the_artists_selector_behind_the_authors():
    r = Recipe.model_validate({"domain": "x.org", "fields": {
        "authors": _rule("a.author"), "artists": {
            "mode": "list", "candidates": [{"kind": "css", "selector": "a.author"},
                                           {"kind": "css", "selector": "a.artist"}]}},
        "hidden": ["artists"]})
    assert [c.selector for c in r.fields["authors"].candidates] == ["a.author", "a.artist"]
    assert "artists" not in r.fields and r.hidden == []
    # a recipe that never knew artists keeps the rows it hides
    assert Recipe.model_validate({"domain": "y.org", "hidden": ["authors"]}).hidden == ["authors"]


def test_a_rule_about_artists_now_works_on_authors(tmp_path):
    lib = Library(tmp_path)
    (tmp_path / "rules.json").write_text(json.dumps({"schema": 2, "rules": [
        {"id": "r1", "field": "artists", "match": ["shindo l"], "action": "rename", "to": "shindol"}]}),
        encoding="utf-8")
    rules = RuleStore(lib.vault).rules()
    assert [(r.field, r.to) for r in rules] == [("authors", "shindol")]
    values, _ = apply(rules, {"authors": ["Shindo L"]})
    assert values["authors"] == ["shindol"]
    assert Rule.model_validate({"field": "tags", "match": ["x"], "action": "move",
                                "toField": "artists"}).toField == "authors"
    lib.close()


def test_an_import_profile_that_filled_artists_fills_authors():
    p = fi.Profile.model_validate({"creatorTo": ["artists", "authors"],
                                   "meta": {"artists": ["Kumo"], "authors": ["Rin"]},
                                   "hidden": ["authors", "artists", "studio"]})
    assert p.creatorTo == ["authors"]
    assert p.meta == {"authors": ["Rin", "Kumo"]}
    assert p.hidden == ["studio"]
    # one that only hid artists keeps the authors row it showed, and hides nothing else
    assert fi.Profile.model_validate({"hidden": ["artists"]}).hidden == []
    run = fi.Overrides.model_validate({"meta": {"w1": {"artists": ["Kumo"]}}})
    assert run.meta == {"w1": {"authors": ["Kumo"]}}


def test_a_run_journaled_on_artists_is_put_back_into_authors(tmp_path):
    lib = Library(tmp_path)
    out = lib.create(DraftIn(meta=TitleMeta(title="Pink Album", authors=["shindol"])))
    run = lib.index.journal_open("2026-10-24T19:56:14+00:00", "merged artists shindo l → shindol", {}, [])
    lib.index.apply_meta([], run, [(out.id, "artists", ["shindo l"], ["shindol"])])
    lib.index.journal_close(run, 1, 0.1, False)

    done = ops.revert(lib, run)
    assert done["reverted"] == 1
    assert lib.get(out.id).authors == ["shindo l"]
    lib.close()
