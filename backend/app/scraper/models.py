"""Recipe v2 — per-domain, versioned "how to read this site" knowledge
(design/state-model.md §6).

A field's extractor is an ORDERED LIST of candidates, tried in order by the
client against the rendered DOM (the backend stores and validates, it does not
extract): a stable picked CSS selector first, then generated fallbacks, then
page metadata (OpenGraph / microdata). When a site's markup changes the field
simply comes back empty — an expected state the user fixes by re-teaching one
field.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..library.migrations import FOLDED_FIELDS

CandidateKind = Literal["css", "anchor", "meta"]


def domain_key(domain: str) -> str:
    """How a site is keyed wherever the vault stores something about it."""
    return domain.strip().lower()


class Candidate(BaseModel):
    kind: CandidateKind = "css"
    # css → a CSS selector; anchor → a row-label text ("Автор", "Status") whose
    # sibling holds the value (position-independent — survives reordered info
    # rows); meta → a metadata key ("og:title", "description", "ld:name")
    selector: str
    attr: str | None = None  # attribute to read (src/href/content); None → text content
    note: str = ""           # provenance of the candidate: "picked" | "label" | "structural" | "fallback"


class FieldRule(BaseModel):
    mode: Literal["single", "list"] = "single"
    index: int = 0             # for single: which of several matches to take
    # per-field cleanup flags (explicit, so each field decides its own normalization)
    lower: bool = False        # lowercase the value(s)
    stripCounts: bool = False  # strip a trailing match-count, e.g. "Romance (255)"
    # what goes BETWEEN several matches when the field keeps one value — a list
    # picked into a text field (four alternative titles, one ALT TITLES box)
    join: str = ""             # "" = the field's own default
    candidates: list[Candidate] = []


class ChapterRule(BaseModel):
    """The chapter-list rule: `item` selects each visible chapter row; the rest
    are sub-rules applied relative to that row. Used by accumulative capture —
    there is no pagination strategy, the user drives paging (state-model §7)."""
    item: str
    id: FieldRule | None = None    # explicit id column when the site has one
    num: FieldRule | None = None
    title: FieldRule | None = None
    url: FieldRule | None = None
    lang: FieldRule | None = None
    group: FieldRule | None = None
    date: FieldRule | None = None


class Recipe(BaseModel):
    domain: str
    version: int = 1
    # e.g. title/alt/desc → single; authors/genres/tags → list; cover → attr src
    fields: dict[str, FieldRule] = {}
    chapters: ChapterRule | None = None
    # Registry field ids this source does not offer. A site that never shows a
    # studio should not show a STUDIO row in the 344px dock — and that is a fact
    # about the SITE, so it belongs to the recipe, not to the user's settings.
    hidden: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _now_named(cls, raw: object) -> object:
        """A recipe learned before a field was folded into another: the folded
        field's rule becomes the joined field's when that has none, else stands
        behind it — its candidates tried when the joined field's find nothing.
        The joined field's row shows wherever the site filled the folded one."""
        if not isinstance(raw, dict):
            return raw
        rules = raw.get("fields")
        hidden = raw.get("hidden")
        if not ((isinstance(rules, dict) and FOLDED_FIELDS.keys() & rules.keys())
                or (isinstance(hidden, list) and FOLDED_FIELDS.keys() & set(hidden))):
            return raw
        rules = dict(rules) if isinstance(rules, dict) else {}
        filled: set[str] = set()

        def sig(c: dict) -> tuple:
            return c.get("kind", "css"), c.get("selector"), c.get("attr")

        for old, into in FOLDED_FIELDS.items():
            rule = rules.pop(old, None)
            if not isinstance(rule, dict):
                continue
            filled.add(into)
            have = rules.get(into)
            if not isinstance(have, dict) or not have.get("candidates"):
                rules[into] = rule
                continue
            known = {sig(c) for c in have["candidates"] if isinstance(c, dict)}
            behind = [c for c in rule.get("candidates") or [] if isinstance(c, dict) and sig(c) not in known]
            rules[into] = {**have, "candidates": [*have["candidates"], *behind]}
        out = {**raw, "fields": rules}
        if isinstance(hidden, list):
            out["hidden"] = [f for f in hidden if f not in FOLDED_FIELDS and f not in filled]
        return out


class PageFilter(BaseModel):
    """What page capture accepts as a PAGE among the images its selector
    matches: a selector wide enough to catch every page also catches the
    reader's icons and ads, so a match must be past an absolute floor and not
    much narrower than the widest match on the same view."""
    minPx: int = Field(default=200, ge=0, le=2000)
    widthRatio: float = Field(default=0.5, ge=0, le=1)
