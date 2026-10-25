"""Rules: what a value becomes on its way into a title. They match whole values
regardless of case, run top to bottom in one pass, live in the vault, and turn
into an operation — with the stored spellings — when applied to titles that
already hold the values."""
from __future__ import annotations

import json

import pytest

from app.library import operations as ops
from app.library import rules as R
from app.library.models import DraftIn, TitleMeta
from app.library.rules import InvalidRule, Rule
from app.library.service import Library
from app.library.vault import UnreadableDocument


def rule(**kw) -> Rule:
    return R.check(Rule(**kw))


def test_a_rename_ignores_case_and_merges_into_one():
    rs = [rule(field="tags", match=["Romance", "романтика"], action="rename", to="romance")]
    out, fired = R.apply(rs, {"tags": ["ROMANCE", "action", "Романтика", "romance"]})
    assert out["tags"] == ["romance", "action"]
    assert [f.rule for f in fired] == [rs[0].id]


def test_rules_run_once_in_order_and_never_loop():
    rs = [rule(field="tags", match=["a"], action="rename", to="b"),
          rule(field="tags", match=["b"], action="rename", to="c"),
          rule(field="tags", match=["c"], action="rename", to="a")]
    out, fired = R.apply(rs, {"tags": ["a"]})
    assert out["tags"] == ["a"] and len(fired) == 3  # a → b → c → a, and it stops there
    out, _ = R.apply(rs[:2], {"tags": ["a"]})
    assert out["tags"] == ["c"]


def test_a_move_fills_only_what_is_empty_and_a_status_is_folded():
    rs = [rule(field="tags", match=["Completed"], action="move", toField="status", to="Completed")]
    out, _ = R.apply(rs, {"tags": ["completed", "drama"]})
    assert out == {"tags": ["drama"], "status": "completed"}
    out, _ = R.apply(rs, {"tags": ["completed"], "status": "ongoing"})
    assert out == {"tags": ["completed"], "status": "ongoing"}  # nowhere to go: it stays


def test_seeing_a_value_can_add_another_and_keep_it():
    rs = [rule(field="tags", match=["yaoi"], action="add", toField="flags", to="adult")]
    out, _ = R.apply(rs, {"tags": ["Yaoi"]})
    assert out == {"tags": ["Yaoi"], "flags": ["adult"]}


def test_a_site_condition_and_a_disabled_rule():
    rs = [rule(field="tags", match=["x"], action="remove", domains=["Example.org"]),
          rule(field="tags", match=["y"], action="remove", enabled=False)]
    assert R.apply(rs, {"tags": ["x", "y"]}, "other.site")[0]["tags"] == ["x", "y"]
    assert R.apply(rs, {"tags": ["x", "y"]}, "example.org")[0]["tags"] == ["y"]


def test_chapter_values_and_one_typed_value():
    rs = [rule(field="language", match=["English", "eng"], action="rename", to="EN")]
    assert R.apply(rs, {"language": "english", "group": "g"})[0] == {"language": "EN", "group": "g"}
    assert R.apply(rs, {"tags": ["english"]})[0] == {"tags": ["english"]}  # another field, untouched


def test_what_cannot_mean_anything_is_refused():
    for bad in (Rule(field="tags", match=[], action="remove"),
                Rule(field="tags", match=["a"], action="rename"),
                Rule(field="language", match=["a"], action="move", toField="tags"),
                Rule(field="tags", match=["a"], action="move", toField="tags"),
                Rule(field="tags", match=["a"], action="add", toField="flags", to="spicy"),
                Rule(field="cover", match=["a"], action="remove")):
        with pytest.raises(InvalidRule):
            R.check(bad)


@pytest.fixture
def lib(tmp_path):
    lib = Library(tmp_path / "lib")
    yield lib
    lib.close()


def test_rules_live_in_the_vault_in_order(lib):
    saved = lib.rules.save([Rule(field="tags", match=[" A "], action="rename", to="b"),
                            Rule(field="tags", match=["c"], action="remove")])
    assert all(r.id for r in saved) and saved[0].match == ["A"]
    on_disk = json.loads((lib.root / "rules.json").read_text(encoding="utf-8"))
    assert [r["match"] for r in on_disk["rules"]] == [["A"], ["c"]]
    assert [r.id for r in lib.rules.rules()] == [r.id for r in saved]
    lib.rules.add([Rule(field="tags", match=["d"], action="remove")])
    assert len(lib.rules.rules()) == 3


def test_one_rule_this_build_cannot_read_makes_the_list_unreadable(lib):
    (lib.root / "rules.json").write_text(json.dumps({"schema": 1, "rules": [
        {"field": "tags", "match": ["a"], "action": "remove"},
        {"field": "tags", "match": ["b"], "action": "teleport"}]}), encoding="utf-8")
    with pytest.raises(UnreadableDocument):
        lib.rules.rules()


def test_a_rule_applied_to_stored_titles_uses_their_spellings(lib):
    lib.create(DraftIn(meta=TitleMeta(title="A", tags=["Romance"])))
    lib.create(DraftIn(meta=TitleMeta(title="B", tags=["ROMANCE", "x"])))
    r = rule(field="tags", match=["romance"], action="rename", to="romance")
    acts = R.as_actions(r, ops.in_scope(lib, None, {}))
    assert acts == [ops.Action(kind="rename", field="tags", values=["ROMANCE", "Romance"], to="romance")]


def test_an_operation_remembered_as_a_rule():
    r = R.from_action(ops.Action(kind="rename", field="tags", values=["Romance"], to="romance"))
    assert (r.field, r.match, r.action, r.to) == ("tags", ["Romance"], "rename", "romance")
    assert R.from_action(ops.Action(kind="clear", field="tags")) is None


def test_which_rule_covers_a_value():
    rs = [rule(field="tags", match=["x"], action="remove"),
          rule(field="tags", match=["X", "y"], action="rename", to="z")]
    cover = R.covering(rs, "tags")
    assert cover["x"][0] == 1 and cover["y"][0] == 2


def test_a_rule_that_fires_is_counted(lib):
    lib.index.rule_fired(["r1", "r1", "r2"], "now")
    stats = lib.index.rule_stats()
    assert stats["r1"]["fired"] == 2 and stats["r2"]["fired"] == 1


def test_a_rename_into_a_value_already_there_does_not_double_it():
    rs = [rule(field="tags", match=["Love"], action="rename", to="romance")]
    assert R.apply(rs, {"tags": ["Love", "romance"]})[0]["tags"] == ["romance"]


def test_what_the_title_already_holds_decides_where_a_value_may_go():
    move = [rule(field="tags", match=["completed"], action="move", toField="status")]
    out, fired = R.apply(move, {"tags": ["completed"]}, context={"status": "ongoing"})
    assert out == {"tags": ["completed"]} and not fired          # the slot is taken
    out, _ = R.apply(move, {"tags": ["completed"]}, locked=["status"])
    assert out == {"tags": ["completed"]}                        # the owner's own field
    people = [rule(field="tags", match=["Miura"], action="move", toField="authors", to="Kentaro Miura")]
    out, _ = R.apply(people, {"tags": ["Miura"]}, context={"authors": ["A", "B"]})
    assert out == {"tags": [], "authors": ["A", "B", "Kentaro Miura"]}  # a list gains, keeps the rest


def test_a_site_is_named_with_or_without_www():
    rs = [rule(field="tags", match=["x"], action="remove", domains=["www.Example.org"])]
    assert rs[0].domains == ["example.org"]
    assert R.apply(rs, {"tags": ["x"]}, "www.example.org")[0]["tags"] == []
    assert R.apply(rs, {"tags": ["x"]}, "example.org")[0]["tags"] == []


def test_a_rule_or_a_list_from_a_newer_build_is_not_applied(lib):
    for doc in ({"schema": 2, "rules": []},
                {"schema": 1, "rules": [{"field": "tags", "match": ["a"], "action": "remove",
                                         "onlyOnFridays": True}]}):
        (lib.root / "rules.json").write_text(json.dumps(doc), encoding="utf-8")
        with pytest.raises(UnreadableDocument):
            lib.rules.rules()
