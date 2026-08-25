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

from pydantic import BaseModel

CandidateKind = Literal["css", "anchor", "meta"]


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
