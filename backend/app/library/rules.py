"""Rules: what a value becomes on its way INTO a title
(design/metadata-management.md §4).

A rule matches whole values of one field, case-insensitively, after the
cleanup capture already does, and renames, removes or moves them — or adds a
value somewhere when it sees them. Rules run top to bottom in ONE pass: each
sees what the earlier ones made, none re-fires on its own output, so two rules
can never loop. A value a rule moves leaves its field only once it has a place
to be — the same rule an operation follows.

They live in the vault (`rules.json`): they describe this library's vocabulary
and travel with it. Saving one changes nothing already stored — applying it to
existing titles is an operation (`as_actions`), with a preview.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from urllib.parse import urlsplit
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .models import TitleDoc
from .operations import FLAG_NAMES, FOLDED, Action, place, shape, values_of
from .vault import Vault

DOC = "rules.json"

RuleAction = Literal["rename", "remove", "move", "add"]


class Rule(BaseModel):
    # a key this build does not know is a condition it cannot honour: such a
    # rule must not be applied as if the condition were not there
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    enabled: bool = True
    field: str
    match: list[str] = []        # whole values; empty only for `add` = whenever the rule applies
    action: RuleAction
    to: str = ""                 # rename / move: what the value becomes; add: the value added
    toField: str = ""            # move / add: the field it goes to
    domains: list[str] = []      # only for values captured from these sites; empty = any


class InvalidRule(ValueError):
    pass


def site(domain: str) -> str:
    """A site as a rule names it: `www.example.org` and `example.org` are one."""
    d = domain.strip().lower()
    return d[4:] if d.startswith("www.") else d


def check(rule: Rule) -> Rule:
    sh = shape(rule.field)
    if sh is None:
        raise InvalidRule(f"{rule.field} holds no values a rule can change")
    match = [m.strip() for m in rule.match if m.strip()]
    if not match and rule.action != "add":
        raise InvalidRule("a rule needs the values it matches")
    if rule.action == "rename" and not rule.to.strip():
        raise InvalidRule("rename needs the value to rename into")
    if sh == "flags" and rule.action != "remove":
        raise InvalidRule("flags are a fixed set — a rule can only remove one")
    if sh == "chapter" and rule.action not in ("rename", "remove"):
        raise InvalidRule(f"{rule.field} values are renamed or removed, nothing else")
    to = rule.to.strip()
    if rule.action in ("move", "add"):
        to_shape = shape(rule.toField)
        if to_shape not in ("list", "single", "flags") or rule.toField in (rule.field, "title"):
            raise InvalidRule(f"a rule cannot {rule.action} into {rule.toField or 'nothing'}")
        if rule.action == "add" and not to:
            raise InvalidRule("add needs the value it adds")
        if to_shape == "flags":
            to = to.lower()
            if to and to not in FLAG_NAMES:
                raise InvalidRule(f"flags are {', '.join(FLAG_NAMES)}")
    if (rule.toField or rule.field) in FOLDED:
        to = to.lower()  # the vocabulary a type or status is stored in
    return rule.model_copy(update={
        "id": rule.id or uuid.uuid4().hex[:10], "match": match, "to": to,
        "domains": [site(d) for d in rule.domains if d.strip()]})


def _parse(doc: dict) -> list[Rule]:
    return Vault.doc_items(doc, DOC, "rules", Rule, "a rule")


class RuleStore:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault

    def rules(self) -> list[Rule]:
        return _parse(self.vault.read_doc(DOC))

    def save(self, rules: list[Rule]) -> list[Rule]:
        """Replace the list — order and all — with every rule checked."""
        checked = [check(r) for r in rules]
        with self.vault.doc_transaction(DOC) as doc:
            doc["rules"] = [r.model_dump() for r in checked]
        return checked

    def add(self, rules: list[Rule]) -> list[Rule]:
        """Append rules (an operation remembered as one) after the existing ones,
        in one step with reading them — a list saved meanwhile is not lost."""
        checked = [check(r) for r in rules]
        with self.vault.doc_transaction(DOC) as doc:
            doc["rules"] = [r.model_dump() for r in [*_parse(doc), *checked]]
        return checked


# ---- applying them -------------------------------------------------------------

@dataclass
class Fired:
    rule: str
    field: str
    before: object
    after: object


def _key(v: str) -> str:
    return v.strip().casefold()


def apply(rules: list[Rule], values: dict[str, object], domain: str = "",
          context: dict[str, object] | None = None, locked: Iterable[str] = ()) -> tuple[dict, list[Fired]]:
    """The values as the rules leave them, and which rules changed something.

    `values` maps a field to what is ARRIVING in it — a list for a list field, a
    string for a single value: the fields a capture produced, the one value the
    owner typed, a chapter's language and group. `context` is what the title
    already holds in fields a rule may move or add into — a single field is
    filled only where it is empty, a list gains values — and `locked` names
    fields nothing may write (the owner's own, when capture is asking). The
    result holds the arriving fields, and every field a rule wrote into."""
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in values.items()}
    ctx = context or {}
    shut = set(locked)
    fired: list[Fired] = []
    here = site(domain)
    for r in rules:
        if not r.enabled or (r.domains and here not in r.domains) or r.field not in out:
            continue
        keys = {_key(m) for m in r.match}
        cur = out[r.field]
        as_list = list(cur) if isinstance(cur, list) else ([cur] if cur else [])
        hits = [v for v in as_list if _key(v) in keys] if keys else as_list
        if not hits:
            continue
        before = {r.field: cur}
        if r.action == "rename":
            renamed = []
            for v in as_list:
                v = r.to if v in hits else v
                if v not in renamed:
                    renamed.append(v)
            out[r.field] = renamed if isinstance(cur, list) else renamed[0]
        elif r.action == "remove":
            kept = [v for v in as_list if v not in hits]
            out[r.field] = kept if isinstance(cur, list) else (kept[0] if kept else "")
        elif r.toField not in shut:
            to_shape = shape(r.toField)
            fold = r.toField in FOLDED or to_shape == "flags"
            given = [r.to or v for v in hits] if r.action == "move" else [r.to]
            given = [g.strip().lower() if fold else g for g in given]
            dest = out[r.toField] if r.toField in out else ctx.get(r.toField)
            if to_shape in ("list", "flags"):
                dest = list(dest) if isinstance(dest, list) else ([dest] if dest else [])
            else:
                dest = dest if isinstance(dest, str) else ""
            before[r.toField] = out.get(r.toField, ctx.get(r.toField))
            new_dest, took = place(dest, given, to_shape)
            if not took:
                continue
            out[r.toField] = new_dest
            if r.action == "move":
                placed = {v for v, g in zip(hits, given) if g in took}
                kept = [v for v in as_list if v not in placed]
                out[r.field] = kept if isinstance(cur, list) else (kept[0] if kept else "")
        after = {k: out.get(k) for k in before}
        if after != before:
            fired.append(Fired(r.id, r.field, before, after))
    return out, fired


def covering(rules: list[Rule], field_id: str) -> dict[str, tuple[int, Rule]]:
    """For one field: which enabled rule (by its 1-based position) handles each
    matched value, keyed by the value folded — what the Values list shows
    beside a value a rule already catches. The first rule to match wins, as it
    does when the rules run."""
    out: dict[str, tuple[int, Rule]] = {}
    for i, r in enumerate(rules, 1):
        if r.enabled and r.field == field_id:
            for m in r.match:
                out.setdefault(_key(m), (i, r))
    return out


def as_actions(rule: Rule, docs: list[tuple[str, TitleDoc]]) -> list[Action]:
    """The operation that applies a rule to titles ALREADY stored: the values
    it matches, spelled as the scope actually holds them (an operation matches
    exactly; a rule ignores case). `docs` are already narrowed to the rule's
    sites (`from_sites`)."""
    keys = {_key(m) for m in rule.match}
    held = sorted({v for _tid, doc in docs for v in values_of(doc, rule.field) if _key(v) in keys})
    if rule.action == "rename":
        held = [v for v in held if v != rule.to]  # already spelled the way the rule spells it
    if rule.action == "add":
        if not keys:
            return [Action(kind="add", field=rule.toField, values=[rule.to])]
        raise InvalidRule("a rule that adds when it sees a value is applied as titles are captured")
    if not held:
        return []
    if rule.action == "rename":
        return [Action(kind="rename", field=rule.field, values=held, to=rule.to)]
    if rule.action == "remove":
        return [Action(kind="remove", field=rule.field, values=held)]
    return [Action(kind="move", field=rule.field, values=held, toField=rule.toField, to=rule.to)]


def from_sites(rule: Rule, docs: list[tuple[str, TitleDoc]]) -> list[tuple[str, TitleDoc]]:
    """The titles a rule with a site condition reaches: those captured from one
    of its sites. A rule for any site reaches them all."""
    if not rule.domains:
        return docs
    return [(tid, doc) for tid, doc in docs
            if site(doc.meta.source.domain or host_of(doc.meta.source.url)) in rule.domains]


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "") if "//" in url else url


def from_action(a: Action) -> Rule | None:
    """An operation remembered as a rule — rename, merge, remove and move."""
    if a.kind not in ("rename", "remove", "move"):
        return None
    try:
        return check(Rule(field=a.field, match=a.values, action=a.kind, to=a.to, toField=a.toField))
    except InvalidRule:
        return None
