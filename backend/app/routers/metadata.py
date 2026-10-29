"""The Metadata view's API (design/metadata-management.md §9): what a field
holds across a scope, operations over many titles — previewed from the index,
run as a vault pass, journaled and revertible — and the rules that keep values
in order on their way in.

A scope travels in the request BODY: a selection can be thousands of ids."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..library import fields
from ..library import operations as ops
from ..library import pages as P
from ..library import rules as R
from ..library.operations import Action, InvalidAction, Preview
from ..library.rules import InvalidRule, Rule
from ..library.service import Library
from ..library.vault import UnreadableDocument
from ..passes import _pass
from .library import _selection

router = APIRouter(prefix="/api/metadata")


def _lib(request: Request) -> Library:
    return request.app.state.library


class Scope(BaseModel):
    """`ids` — exactly these titles; otherwise the library's own filters."""
    ids: list[str] | None = None
    search: str | None = None
    progress: str | None = None
    fav: bool = False
    min_rating: int | None = None
    f: list[str] = []
    nf: list[str] = []


def _docs(lib: Library, scope: Scope):
    sel = _selection(scope.search, scope.progress, scope.fav, scope.min_rating, scope.f, scope.nf)
    return ops.in_scope(lib, scope.ids, sel), sel


def _checked(actions: list[Action]) -> list[Action]:
    try:
        return ops.check(actions)
    except InvalidAction as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ---- fields and their values ---------------------------------------------------

class FieldStat(BaseModel):
    id: str
    label: str
    kind: Literal["title", "chapter"]
    shape: str
    type: str
    builtin: bool
    distinct: int


class ScopeIn(BaseModel):
    scope: Scope = Scope()


class FieldsOut(BaseModel):
    inScope: int  # how many titles the scope reaches
    fields: list[FieldStat]


@router.post("/fields", response_model=FieldsOut)
def field_stats(body: ScopeIn, request: Request) -> FieldsOut:
    docs, _ = _docs(_lib(request), body.scope)
    counts = ops.field_counts(docs)
    out: list[FieldStat] = []
    for f in fields.registry():
        sh = ops.shape(f.id)
        if sh is not None and sh != "chapter":
            out.append(FieldStat(id=f.id, label=f.label, kind="title", shape=sh, type=f.type,
                                 builtin=f.builtin, distinct=counts.get(f.id, 0)))
    labels = {"language": "Language", "group": "Group", "section": "Section"}
    out += [FieldStat(id=fid, label=labels[fid], kind="chapter", shape="chapter", type="text",
                      builtin=True, distinct=counts.get(fid, 0)) for fid in ops.CHAPTER_FIELDS]
    return FieldsOut(inScope=len(docs), fields=out)


class ValuesIn(BaseModel):
    scope: Scope = Scope()
    field: str


class ValueOut(BaseModel):
    value: str
    titles: int
    typed: int
    rule: int = 0        # the position of the enabled rule that already handles it
    ruleText: str = ""   # what that rule makes of it
    page: bool = False   # something is written on its page (design/value-pages.md)


def _rule_text(r: Rule) -> str:
    if r.action == "rename":
        return f"→ {r.to}"
    if r.action == "remove":
        return "removed"
    if r.action == "move":
        return f"→ {r.toField}" + (f": {r.to}" if r.to else "")
    return f"+ {r.toField}: {r.to}"


@router.post("/values", response_model=list[ValueOut])
def field_values(body: ValuesIn, request: Request) -> list[ValueOut]:
    lib = _lib(request)
    if ops.shape(body.field) is None:
        raise HTTPException(status_code=404, detail="no such field")
    docs, _ = _docs(lib, body.scope)
    try:
        cover = R.covering(lib.rules.rules(), body.field)
    except UnreadableDocument:
        cover = {}  # the Rules tab says what is wrong with the file
    vocab = P.vocab_of(body.field)
    try:
        written = lib.pages.pages(vocab) if vocab else {}
    except UnreadableDocument:
        written = {}
    out = []
    for row in ops.field_values(docs, body.field):
        k = P.key(row["value"])
        hit = cover.get(k)
        out.append(ValueOut(**row, rule=hit[0] if hit else 0,
                            ruleText=_rule_text(hit[1]) if hit else "", page=k in written))
    return out


class HeldIn(BaseModel):
    scope: Scope = Scope()
    fields: list[str]


class HeldValue(BaseModel):
    value: str
    titles: int


class HeldField(BaseModel):
    titles: int  # how many hold anything in it at all
    values: list[HeldValue]


class HeldOut(BaseModel):
    inScope: int
    fields: dict[str, HeldField]


@router.post("/held", response_model=HeldOut)
def held(body: HeldIn, request: Request) -> HeldOut:
    """What a selection holds in each of these fields, with how many of its
    titles hold each value — what the library's bulk editor draws."""
    docs, _ = _docs(_lib(request), body.scope)
    out: dict[str, HeldField] = {}
    for fid in dict.fromkeys(body.fields):
        if ops.shape(fid) not in ("list", "single", "flags"):
            continue
        out[fid] = HeldField(
            titles=sum(1 for _tid, doc in docs if ops.values_of(doc, fid)),
            values=[HeldValue(value=r["value"], titles=r["titles"]) for r in ops.field_values(docs, fid)])
    return HeldOut(inScope=len(docs), fields=out)


# ---- operations ---------------------------------------------------------------------

class OperationIn(BaseModel):
    scope: Scope = Scope()
    actions: list[Action]
    remember: bool = False  # also keep rename / merge / remove / move as rules
    own: bool = False       # the owner's edit of these titles: what it changes becomes manual


@router.post("/preview", response_model=Preview)
def preview(body: OperationIn, request: Request) -> Preview:
    lib = _lib(request)
    acts = _checked(body.actions)
    docs, _ = _docs(lib, body.scope)
    return ops.plan(lib, docs, acts).preview


class RunOut(BaseModel):
    run: int
    changed: int
    failed: int        # titles left as they were: unreadable, written by a newer build, in use
    stopped: bool
    seconds: float
    rules: list[Rule] = []


def _run(lib: Library, docs, scope: dict, acts: list[Action], own: bool = False) -> dict:
    summary = ops.describe(acts)
    with _pass(f"metadata: {summary}", "metadata") as (progress, stop):
        return ops.run(lib, docs, acts, summary=summary, scope=scope, own=own,
                       progress=progress, stop=stop)


@router.post("/run", response_model=RunOut)
def run_operation(body: OperationIn, request: Request) -> RunOut:
    lib = _lib(request)
    acts = _checked(body.actions)
    remembered = [r for r in (R.from_action(a) for a in acts) if r] if body.remember else []
    if remembered:
        lib.rules.rules()  # a rules.json that cannot take them refuses BEFORE anything is written
    docs, sel = _docs(lib, body.scope)
    out = _run(lib, docs, ops.scope_json(body.scope.ids, sel), acts, body.own)
    return RunOut(**out, rules=lib.rules.add(remembered) if remembered else [])


@router.get("/runs")
def runs(request: Request) -> list[dict]:
    return _lib(request).index.journal_runs()


@router.post("/runs/{run_id}/revert")
def revert(run_id: int, request: Request) -> dict:
    lib = _lib(request)
    found = next((r for r in lib.index.journal_runs() if r["id"] == run_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="no such run")
    if found["revertedAt"]:
        # a second revert could roll back a later change that happens to equal
        # what this run wrote
        raise HTTPException(status_code=409, detail="this run has already been reverted")
    with _pass(f"metadata: revert run {run_id}", "metadata") as (progress, stop):
        return ops.revert(lib, run_id, progress=progress, stop=stop)


# ---- rules ------------------------------------------------------------------------

class RulesOut(BaseModel):
    rules: list[Rule]
    stats: dict[str, dict]


@router.get("/rules", response_model=RulesOut)
def list_rules(request: Request) -> RulesOut:
    lib = _lib(request)
    return RulesOut(rules=lib.rules.rules(), stats=lib.index.rule_stats())


class RulesIn(BaseModel):
    rules: list[Rule]


@router.put("/rules", response_model=RulesOut)
def save_rules(body: RulesIn, request: Request) -> RulesOut:
    lib = _lib(request)
    try:
        saved = lib.rules.save(body.rules)
    except InvalidRule as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    lib.index.rule_stats_keep([r.id for r in saved])
    return RulesOut(rules=saved, stats=lib.index.rule_stats())


class ApplyIn(BaseModel):
    values: dict[str, list[str] | str]
    domain: str = ""
    # rules to TRY instead of the stored ones — the editor's "try a value" box,
    # for a rule not saved yet; nothing is counted for them
    rules: list[Rule] | None = None
    # what the title already holds in fields a rule may move or add into, and
    # the fields nothing may write (the owner's own, when capture is asking)
    context: dict[str, list[str] | str] = {}
    locked: list[str] = []


class FiredOut(BaseModel):
    rule: str
    n: int
    field: str


class ApplyOut(BaseModel):
    values: dict[str, list[str] | str]
    fired: list[FiredOut]


@router.post("/rules/apply", response_model=ApplyOut)
def apply_rules(body: ApplyIn, request: Request) -> ApplyOut:
    """Values on their way into a draft — captured or typed — as the rules leave
    them, and which rules did it."""
    lib = _lib(request)
    trial = body.rules is not None
    try:
        rules = [R.check(r) for r in body.rules] if trial else lib.rules.rules()
    except InvalidRule as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    out, fired = R.apply(rules, body.values, body.domain, body.context, body.locked)
    if fired and not trial:
        lib.index.rule_fired([f.rule for f in fired],
                             datetime.now(timezone.utc).isoformat(timespec="seconds"))
    pos = {r.id: i for i, r in enumerate(rules, 1)}
    return ApplyOut(values=out, fired=[FiredOut(rule=f.rule, n=pos.get(f.rule, 0), field=f.field)
                                       for f in fired])


def _rule(lib: Library, rule_id: str) -> Rule:
    found = next((r for r in lib.rules.rules() if r.id == rule_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="no such rule")
    return found


def _rule_actions(lib: Library, rule: Rule, docs) -> list[Action]:
    try:
        acts = R.as_actions(rule, docs)
    except InvalidRule as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not acts:
        raise HTTPException(status_code=409, detail="no title in reach holds what this rule matches")
    return _checked(acts)


def _rule_scope(lib: Library, rule_id: str, scope: Scope):
    """A rule applied to stored titles: its actions, over the titles of the
    scope it can reach — a rule for one site reaches only that site's titles."""
    rule = _rule(lib, rule_id)
    docs, sel = _docs(lib, scope)
    docs = R.from_sites(rule, docs)
    return docs, sel, _rule_actions(lib, rule, docs)


@router.post("/rules/{rule_id}/preview", response_model=Preview)
def preview_rule(rule_id: str, body: ScopeIn, request: Request) -> Preview:
    lib = _lib(request)
    docs, _, acts = _rule_scope(lib, rule_id, body.scope)
    return ops.plan(lib, docs, acts).preview


@router.post("/rules/{rule_id}/run", response_model=RunOut)
def run_rule(rule_id: str, body: ScopeIn, request: Request) -> RunOut:
    lib = _lib(request)
    docs, sel, acts = _rule_scope(lib, rule_id, body.scope)
    return RunOut(**_run(lib, docs, ops.scope_json(body.scope.ids, sel), acts))
