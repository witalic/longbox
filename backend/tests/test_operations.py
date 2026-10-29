"""Operations over many titles (design/metadata-management.md): one function
decides what happens to a document, the preview runs it over the index and the
run over the vault, only titles that change are written, the shelf order holds,
and every run can be taken back."""
from __future__ import annotations

import json

import pytest

from app.library import operations as ops
from app.library.models import ChapterRow, DraftIn, FieldProvenance, TitleMeta
from app.library.operations import Action, InvalidAction
from app.library.service import Library


def make(lib: Library, title: str, *, type_: str = "manga", tags=(), status: str = "",
         prov: dict | None = None, chapters=()) -> str:
    draft = DraftIn(meta=TitleMeta(title=title, type=type_, tags=list(tags), status=status),
                    provenance={k: FieldProvenance(origin=v) for k, v in (prov or {}).items()})
    tid = lib.create(draft).id
    if chapters:  # the way the app adds them: rows with no id, committed — the vault names them
        draft.chapters = [ChapterRow(id="", num=n, lang=lang, group=g) for n, lang, g in chapters]
        lib.commit(tid, draft)
    return tid


def stamp(lib: Library, tid: str) -> int:
    return lib.vault.touched_at(tid)


def everything(lib: Library):
    return ops.in_scope(lib, None, {})


def run(lib: Library, actions: list[Action], docs=None):
    acts = ops.check(actions)
    return ops.run(lib, docs if docs is not None else everything(lib), acts,
                   summary=ops.describe(acts), scope={})


@pytest.fixture
def lib(tmp_path):
    lib = Library(tmp_path / "lib")
    yield lib
    lib.close()


def test_a_merge_writes_only_the_titles_that_change(lib):
    a = make(lib, "A", tags=["Romance", "action"], prov={"tags": "auto"})
    b = make(lib, "B", tags=["романтика"], prov={"tags": "manual"})
    c = make(lib, "C", tags=["romance"])
    d = make(lib, "D", tags=["horror"])
    before = {t: stamp(lib, t) for t in (c, d)}

    acts = [Action(kind="rename", field="tags", values=["Romance", "романтика"], to="romance")]
    p = ops.plan(lib, everything(lib), ops.check(acts)).preview
    assert (p.inScope, p.titles, p.manual, p.auto, p.untouched) == (4, 2, 1, 1, 1)
    assert {(s.title, s.before, s.after) for s in p.sample} == {
        ("A", "Romance, action", "romance, action"), ("B", "романтика", "romance")}

    out = run(lib, acts)
    assert out["changed"] == 2 and not out["stopped"]
    assert lib.vault.load(a).meta.tags == ["romance", "action"]
    assert lib.vault.load(b).meta.tags == ["romance"]
    assert {t: stamp(lib, t) for t in (c, d)} == before          # never opened for writing
    assert {r.id for r in lib.query(include={"tags": ("romance",)})} == {a, b, c}


def test_a_bulk_change_keeps_the_updated_order(lib):
    old = make(lib, "Old", tags=["x"])
    new = make(lib, "New", tags=["y"])
    path = next(p for p in (lib.root).rglob("title.json") if json.loads(p.read_text("utf-8"))["meta"]["title"] == "Old")
    import os
    hour_ago = stamp(lib, old) - 3600 * 10**9
    os.utime(path, ns=(hour_ago, hour_ago))
    lib.sync()

    run(lib, [Action(kind="rename", field="tags", values=["x"], to="z")])
    assert hour_ago < stamp(lib, old) <= hour_ago + 2 * 10**9
    assert [t.id for t in lib.query(sort="updated")][0] == new   # still behind the newer title


def test_a_move_fills_an_empty_slot_and_reports_a_taken_one(lib):
    a = make(lib, "A", tags=["completed", "drama"], prov={"tags": "auto"})
    b = make(lib, "B", tags=["completed"], status="ongoing")
    acts = [Action(kind="move", field="tags", values=["completed"], toField="status")]
    p = ops.plan(lib, everything(lib), ops.check(acts)).preview
    assert p.titles == 1 and p.conflicts == 1

    run(lib, acts)
    da, db = lib.vault.load(a), lib.vault.load(b)
    assert da.meta.status == "completed" and da.meta.tags == ["drama"]
    assert da.provenance["status"].origin == "auto"           # the value keeps its origin
    # one slot, two answers: nothing is decided — the status stays, and the tag
    # does not leave for a place it never reached
    assert db.meta.status == "ongoing" and db.meta.tags == ["completed"]


def test_the_rest_of_the_vocabulary(lib):
    t = make(lib, "Lain (Official)", tags=["a", "b"])
    run(lib, [Action(kind="add", field="tags", values=["c", "a"]),
              Action(kind="remove", field="tags", values=["b"]),
              Action(kind="replace", field="title", find=" (Official)", to=""),
              Action(kind="set", field="flags", values=["adult"]),
              Action(kind="set", field="status", to="Ongoing")])
    doc = lib.vault.load(t)
    assert doc.meta.tags == ["a", "c"] and doc.meta.title == "Lain"
    assert doc.meta.flags.adult and doc.meta.status == "ongoing"  # the vocabulary is folded
    run(lib, [Action(kind="clear", field="tags")])
    assert lib.vault.load(t).meta.tags == []


def test_a_scope_is_a_filter_or_a_selection(lib):
    a = make(lib, "A", type_="manga", tags=["x"])
    b = make(lib, "B", type_="manhwa", tags=["x"])
    c = make(lib, "C", type_="manhwa", tags=["x"])
    rename = [Action(kind="rename", field="tags", values=["x"], to="y")]

    run(lib, rename, docs=ops.in_scope(lib, None, {"include": {"type": ("manhwa",)}}))
    assert [lib.vault.load(t).meta.tags for t in (a, b, c)] == [["x"], ["y"], ["y"]]
    run(lib, [Action(kind="rename", field="tags", values=["y"], to="z")],
        docs=ops.in_scope(lib, [c], {}))
    assert lib.vault.load(b).meta.tags == ["y"] and lib.vault.load(c).meta.tags == ["z"]


def test_renaming_a_whole_shelf_is_one_directory_rename(lib):
    ids = [make(lib, f"W{i}", type_="manhwa", tags=["t"]) for i in range(3)]
    make(lib, "M", type_="manga")
    acts = ops.check([Action(kind="rename", field="type", values=["manhwa"], to="Webtoon")])
    assert ops.plan(lib, everything(lib), acts).preview.shelfRename == ["manhwa", "webtoon"]

    run(lib, acts)
    assert not (lib.root / "manhwa").exists() and (lib.root / "webtoon").is_dir()
    for t in ids:
        assert lib.vault.load(t).meta.type == "webtoon"
        assert (lib.root / "webtoon" / t / "title.json").is_file()
    assert {r.id for r in lib.query(include={"type": ("webtoon",)})} == set(ids)


def test_a_partial_type_change_moves_titles_and_sweeps_the_empty_shelf(lib):
    w = make(lib, "W", type_="manhwa")
    m = make(lib, "M", type_="manhua")
    acts = ops.check([Action(kind="rename", field="type", values=["manhwa"], to="manhua")])
    assert ops.plan(lib, everything(lib), acts).preview.shelfRename == []  # manhua/ exists
    run(lib, acts)
    assert (lib.root / "manhua" / w / "title.json").is_file()
    assert (lib.root / "manhua" / m / "title.json").is_file()
    assert not (lib.root / "manhwa").exists()


def test_chapter_values_change_without_touching_chapter_ids(lib):
    t = make(lib, "T", chapters=[("1", "English", "TeamX"), ("1", "en", "Team X"), ("2", "en", "Team X")])
    ids = [c.id for c in lib.vault.load(t).chapters]
    acts = ops.check([Action(kind="rename", field="language", values=["English", "en"], to="EN"),
                      Action(kind="rename", field="group", values=["TeamX"], to="Team X")])
    p = ops.plan(lib, everything(lib), acts).preview
    assert p.titles == 1 and p.chapterTwins == 2  # chapter 1 twice as EN · Team X

    run(lib, acts)
    doc = lib.vault.load(t)
    assert [c.id for c in doc.chapters] == ids
    assert {(c.lang, c.group) for c in doc.chapters} == {("EN", "Team X")}


def test_sections_are_renamed_across_titles_and_reverted(lib):
    a = make(lib, "A", chapters=[("1", "", ""), ("2", "", "")])
    b = make(lib, "B", chapters=[("1", "", "")])
    for tid, parts in ((a, ["Vol. 1", "Vol. 2"]), (b, ["vol 1"])):
        draft = DraftIn(meta=lib.vault.load(tid).meta, chapters=[
            c.model_copy(update={"section": s}) for c, s in zip(lib.vault.load(tid).chapters, parts)])
        lib.commit(tid, draft)
    assert {v["value"]: v["titles"] for v in ops.field_values(everything(lib), "section")} == {
        "Vol. 1": 1, "Vol. 2": 1, "vol 1": 1}

    acts = ops.check([Action(kind="rename", field="section", values=["Vol. 1", "vol 1"], to="Volume 1")])
    p = ops.plan(lib, everything(lib), acts).preview
    assert (p.titles, p.chapterTwins) == (2, 0)  # a section names no entry
    out = run(lib, acts)
    assert [c.section for c in lib.vault.load(a).chapters] == ["Volume 1", "Vol. 2"]
    assert [c.section for c in lib.vault.load(b).chapters] == ["Volume 1"]

    ops.revert(lib, out["run"])
    assert [c.section for c in lib.vault.load(b).chapters] == ["vol 1"]


def test_a_run_is_journaled_and_reverted_without_undoing_later_edits(lib):
    a = make(lib, "A", tags=["Romance"])
    b = make(lib, "B", tags=["Romance"])
    w = make(lib, "W", type_="manhwa")
    out = run(lib, [Action(kind="rename", field="tags", values=["Romance"], to="romance")])
    moved = run(lib, [Action(kind="rename", field="type", values=["manhwa"], to="webtoon")])

    # the owner edits B afterwards: the revert must leave that alone
    lib.vault.save_meta_value(b, lambda d: setattr(d.meta, "tags", ["romance", "drama"]))
    runs = lib.index.journal_runs()
    assert [r["titles"] for r in runs] == [1, 2] and runs[1]["summary"].startswith("renamed tags")

    res = ops.revert(lib, out["run"])
    assert res == {"reverted": 1, "kept": 1, "failed": 0, "stopped": False}
    assert lib.vault.load(a).meta.tags == ["Romance"]
    assert lib.vault.load(b).meta.tags == ["romance", "drama"]
    assert lib.index.journal_runs()[1]["revertedAt"]

    ops.revert(lib, moved["run"])
    assert lib.vault.load(w).meta.type == "manhwa"
    assert (lib.root / "manhwa" / w / "title.json").is_file()


def test_a_batched_index_write_never_overwrites_a_newer_row(lib):
    t = make(lib, "T", tags=["x"])
    doc = lib.vault.load(t)
    doc.meta.tags = ["stale"]
    lib.index.apply_meta([(t, doc, 2, 1)])  # read at a stamp older than the row's
    assert lib.get(t).tags == ["x"]


def test_the_journal_keeps_the_last_fifty_runs(lib):
    from app.library.index import JOURNAL_KEEP
    for i in range(JOURNAL_KEEP + 3):
        rid = lib.index.journal_open("now", f"run {i}", {}, [])
        lib.index.apply_meta([], rid, [("t", "tags", [], [str(i)])])
        lib.index.journal_close(rid, 1, 0.1, False)
    runs = lib.index.journal_runs()
    assert len(runs) == JOURNAL_KEEP and runs[-1]["summary"] == "run 3"
    assert lib.index.journal_changes(runs[0]["id"] - JOURNAL_KEEP) == []


def test_the_journal_survives_a_rebuild(lib):
    make(lib, "A", tags=["x"])
    run(lib, [Action(kind="rename", field="tags", values=["x"], to="y")])
    lib.rescan()
    assert len(lib.index.journal_runs()) == 1


def test_what_cannot_mean_anything_is_refused():
    for bad in (Action(kind="rename", field="tags", values=["a"]),           # into nothing
                Action(kind="remove", field="title", values=["x"]),           # every title needs one
                Action(kind="rename", field="flags", values=["adult"], to="x"),
                Action(kind="move", field="tags", values=["a"], toField="tags"),
                Action(kind="add", field="status", values=["x"]),
                Action(kind="rename", field="cover", values=["a"], to="b"),
                Action(kind="replace", field="tags")):
        with pytest.raises(InvalidAction):
            ops.check([bad])


def test_the_values_of_a_field_with_their_counts(lib):
    make(lib, "A", tags=["romance", "action"], prov={"tags": "manual"})
    make(lib, "B", tags=["romance"], prov={"tags": "auto"})
    make(lib, "C", chapters=[("1", "en", "g")])
    docs = everything(lib)
    assert ops.field_values(docs, "tags") == [
        {"value": "romance", "titles": 2, "typed": 1}, {"value": "action", "titles": 1, "typed": 1}]
    counts = ops.field_counts(docs)
    assert counts["tags"] == 2 and counts["language"] == 1 and counts["group"] == 1


# ---- what the review found: nothing removed that is not placed, nothing lost on a failure ----

def test_several_candidates_for_one_slot_are_a_conflict_and_stay(lib):
    t = make(lib, "T", tags=["completed", "finished"])
    acts = ops.check([Action(kind="move", field="tags", values=["completed", "finished"], toField="status",
                             to="")])
    assert ops.plan(lib, everything(lib), acts).preview.conflicts == 1
    run(lib, [Action(kind="move", field="tags", values=["completed", "finished"], toField="status")])
    doc = lib.vault.load(t)
    assert doc.meta.status == "" and doc.meta.tags == ["completed", "finished"]


def test_a_move_into_flags_takes_only_flag_names(lib):
    t = make(lib, "T", tags=["Adult", "spicy"])
    run(lib, [Action(kind="move", field="tags", values=["Adult", "spicy"], toField="flags")])
    doc = lib.vault.load(t)
    assert doc.meta.flags.adult and doc.meta.tags == ["spicy"]


def test_a_value_a_field_cannot_hold_is_left_out(lib):
    from app.library import fields
    from app.library.models import CustomFieldDef
    lib.vault.save_custom_fields([CustomFieldDef(id="released", label="Released", type="date")])
    fields.set_custom(lib.vault.custom_fields())
    t = make(lib, "Lain")
    lib.vault.save_meta_value(t, lambda d: d.meta.custom.update({"released": "1998-07-06"}))
    lib.sync()
    acts = ops.check([Action(kind="replace", field="released", find="-", to=""),
                      Action(kind="replace", field="title", find="Lain", to="")])
    p = ops.plan(lib, everything(lib), acts).preview
    assert p.titles == 0 and p.conflicts == 1
    with pytest.raises(InvalidAction):
        ops.check([Action(kind="set", field="title", to="")])


def test_one_title_that_cannot_be_read_does_not_stop_the_others(lib):
    ids = [make(lib, f"T{i}", tags=["x"]) for i in range(5)]
    broken = next(p for p in lib.root.rglob("title.json") if ids[2] in str(p))
    broken.write_text("{", encoding="utf-8")
    out = run(lib, [Action(kind="rename", field="tags", values=["x"], to="y")])
    assert out["changed"] == 4 and out["failed"] == 1 and not out["stopped"]
    changed = {tid for tid, _f, _b, _a in lib.index.journal_changes(out["run"])}
    assert changed == set(ids) - {ids[2]}                      # every write is journaled
    assert all(lib.get(t).tags == ["y"] for t in changed)      # …and indexed
    assert lib.index.journal_runs()[0]["titles"] == 4


def test_a_document_from_a_newer_build_is_left_alone(lib):
    t = make(lib, "T", tags=["x"])
    path = next(lib.root.rglob("title.json"))
    raw = json.loads(path.read_text("utf-8"))
    raw["schema"], raw["meta"]["fromTheFuture"] = 99, "kept"
    path.write_text(json.dumps(raw), encoding="utf-8")
    out = run(lib, [Action(kind="rename", field="tags", values=["x"], to="y")])
    assert out["failed"] == 1 and json.loads(path.read_text("utf-8"))["meta"]["fromTheFuture"] == "kept"
    assert lib.vault.touched_at(t)  # still where it was


def test_a_shelf_holding_a_title_the_index_has_not_seen_moves_title_by_title(lib):
    ids = [make(lib, f"W{i}", type_="manhwa") for i in range(2)]
    stranger = lib.root / "manhwa" / "not-indexed"
    stranger.mkdir()
    (stranger / "title.json").write_text(json.dumps({"schema": 4, "meta": {"title": "S", "type": "manhwa"}}),
                                         encoding="utf-8")
    run(lib, [Action(kind="rename", field="type", values=["manhwa"], to="webtoon")])
    assert all((lib.root / "webtoon" / t / "title.json").is_file() for t in ids)
    assert (stranger / "title.json").is_file()  # never moved without its type changing


def test_a_run_that_renamed_its_shelf_finishes_even_when_asked_to_stop(lib):
    from threading import Event
    ids = [make(lib, f"W{i}", type_="manhwa") for i in range(3)]
    stop = Event()
    stop.set()
    acts = ops.check([Action(kind="rename", field="type", values=["manhwa"], to="webtoon")])
    out = ops.run(lib, everything(lib), acts, summary="s", scope={}, stop=stop)
    assert out["changed"] == 3 and not out["stopped"]
    assert all(lib.vault.load(t).meta.type == "webtoon" for t in ids)


def test_the_journal_keeps_only_the_chapters_a_run_changed(lib):
    t = make(lib, "T", chapters=[("1", "English", "g"), ("2", "en", "g")])
    out = run(lib, [Action(kind="rename", field="language", values=["English"], to="EN")])
    (_tid, _f, before, after), = lib.index.journal_changes(out["run"])
    assert len(before) == len(after) == 1
    # a later edit to the chapter the run did not touch is not "changed since"
    second = lib.vault.load(t).chapters[1].id
    lib.vault.save_meta_value(t, lambda d: setattr(next(c for c in d.chapters if c.id == second), "lang", "ja"))
    assert ops.revert(lib, out["run"])["kept"] == 0


def test_a_person_merged_into_another_keeps_their_favourite_mark(lib):
    from app.library.vault import safe_id
    lib.create(DraftIn(meta=TitleMeta(title="Berserk", authors=["Miura Kentarou"])))
    lib.vault.set_author_favorite(safe_id("Miura Kentarou"), True)
    run(lib, [Action(kind="rename", field="authors", values=["Miura Kentarou"], to="Kentaro Miura")])
    assert safe_id("Kentaro Miura") in lib.vault.author_favorites()


def test_nothing_to_change_is_not_a_run(lib):
    make(lib, "T", tags=["x"])
    assert run(lib, [Action(kind="rename", field="tags", values=["nope"], to="y")])["run"] == 0
    assert lib.index.journal_runs() == []


def test_a_revert_that_breaks_off_is_not_a_revert(lib, monkeypatch):
    """An index write that fails mid-revert must leave the run revertible —
    marking it reverted would make the second, real attempt impossible."""
    make(lib, "A", tags=["x"])
    out = run(lib, [Action(kind="rename", field="tags", values=["x"], to="y")])

    def broken(*_a, **_k):
        raise RuntimeError("the index said no")

    monkeypatch.setattr(lib.index, "apply_meta", broken)
    with pytest.raises(RuntimeError):
        ops.revert(lib, out["run"])
    monkeypatch.undo()
    assert lib.index.journal_runs()[0]["revertedAt"] == ""
    ops.revert(lib, out["run"])
    assert lib.index.journal_runs()[0]["revertedAt"]


def test_the_owners_own_edit_makes_the_fields_it_changes_manual_and_a_revert_puts_them_back(lib):
    """The library's bulk editor (design/bulk-edit.md §5): what it changes is the
    owner's — capture leaves it alone — and the run still reverts in full."""
    a = make(lib, "A", tags=["x"], status="ongoing", prov={"tags": "auto", "status": "auto"})
    b = make(lib, "B", tags=["isekai"], prov={"tags": "auto"})
    acts = ops.check([Action(kind="add", field="tags", values=["isekai"]),
                      Action(kind="set", field="status", to="completed")])
    out = ops.run(lib, ops.in_scope(lib, [a, b], {}), acts, summary=ops.describe(acts),
                  scope={"ids": 2}, own=True)
    assert out["changed"] == 2
    da, db = lib.vault.load(a), lib.vault.load(b)
    assert (da.meta.tags, da.meta.status) == (["x", "isekai"], "completed")
    assert da.provenance["tags"].origin == da.provenance["status"].origin == "manual"
    assert db.provenance["tags"].origin == "auto"  # B already held the tag: its tags were not touched
    assert db.provenance["status"].origin == "manual"

    ops.revert(lib, out["run"])
    da, db = lib.vault.load(a), lib.vault.load(b)
    assert (da.meta.tags, da.meta.status) == (["x"], "ongoing")
    assert da.provenance["tags"].origin == da.provenance["status"].origin == "auto"
    assert "status" not in db.provenance  # it had no origin before the edit


def test_a_revert_keeps_the_manual_mark_of_a_value_typed_since(lib):
    a = make(lib, "A", tags=["x"], prov={"tags": "auto"})
    acts = ops.check([Action(kind="add", field="tags", values=["y"])])
    out = ops.run(lib, everything(lib), acts, summary=ops.describe(acts), scope={}, own=True)
    lib.vault.save_meta_value(a, lambda d: setattr(d.meta, "tags", ["x", "y", "z"]))
    ops.revert(lib, out["run"])
    doc = lib.vault.load(a)
    assert doc.meta.tags == ["x", "y", "z"] and doc.provenance["tags"].origin == "manual"


def test_a_metadata_operation_keeps_the_origin(lib):
    a = make(lib, "A", tags=["x"], prov={"tags": "auto"})
    run(lib, [Action(kind="add", field="tags", values=["y"])])
    assert lib.vault.load(a).provenance["tags"].origin == "auto"
