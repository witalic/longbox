"""The ONE description of what a metadata field is.

Everything that has to iterate fields reads this: filtering, facet counting, the
query API, the editor, the filter sidebar, the vocabulary of suggestions. A field
named anywhere else is a field one of them will forget — which is exactly what
adding `studio` by hand cost, thirty edits across ten files.

The registry is also SERVED to the client (`GET /api/fields`), so a field the
user defines is a field the UI renders without a line of code.

What is deliberately NOT here: the spine. `title` decides identity and search,
`type` decides which shelf a title physically lives on, `cover` is bytes, `flags`
is a fixed triple, `source` anchors provenance and `chapterOrder` is behaviour.
Those carry logic, not just a value, and pretending otherwise would fill this
file with exceptions. They appear below only where they are also a FIELD — a
thing with a label, a value and a place in the editor.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field as dataclass_field
from typing import Literal

from .models import TitleDoc

# How a value is stored and filtered. The five the app can render.
FieldType = Literal["text", "number", "list", "date", "boolean"]
# Which widget the editor draws. Derived from `type` for custom fields; built-ins
# say it outright, because `desc` is a textarea and `type` is a vocabulary combo
# while both are stored as plain text.
Control = Literal["line", "multiline", "vocab", "chips", "number", "date", "toggle",
                  "cover", "flags"]

_CONTROL_FOR: dict[str, Control] = {
    "text": "line", "number": "number", "list": "chips",
    "date": "date", "boolean": "toggle",
}


@dataclass(frozen=True)
class Field:
    """One metadata field, built-in or user-defined."""

    id: str                     # the key EVERYWHERE: query, facets, provenance, custom map
    label: str
    type: FieldType
    control: Control
    builtin: bool = True
    required: bool = False
    editable: bool = True       # appears in the metadata editor and carries provenance
    facet: bool = False         # the library can filter by it
    column: str = ""            # an indexed SQL column, for single-valued built-ins
    attr: str = ""              # the TitleMeta attribute holding it (built-ins only)
    placeholder: str = ""       # UI copy: the editor's empty-state text
    vocab: str = ""             # whose suggestions to offer ("" = this field's own)
    # How to read the field's filterable values off a document. Not always the
    # attribute: `authors` folds in artists, `language` comes off the chapters,
    # `flags` is computed. Custom fields get this generated.
    values: Callable[[TitleDoc], set[str]] | None = dataclass_field(
        default=None, compare=False, repr=False)


def _flag_values(doc: TitleDoc) -> set[str]:
    f = doc.meta.flags
    return {name for name in ("adult", "ai", "censored") if getattr(f, name, False)}


def _attr_values(attr: str) -> Callable[[TitleDoc], set[str]]:
    def read(doc: TitleDoc) -> set[str]:
        v = getattr(doc.meta, attr, None)
        if isinstance(v, list):
            return {str(x) for x in v if x}
        return {str(v)} if v else set()
    return read


# The order is the order the EDITOR draws them in.
#
# `cover` and `flags` are here because they are rows in that editor — a label, a
# body, a place in the sequence. Their `type` says "text" only because the field
# types describe how a CUSTOM value is stored and filtered, and these two are
# neither stored nor filtered that way; their control is bespoke and the
# component knows how to draw it.
BUILTIN: tuple[Field, ...] = (
    Field("title", "Title", "text", "line", required=True, attr="title",
          placeholder="Title (required)"),
    Field("alt", "Alt titles", "text", "line", attr="alt", placeholder="Alternate titles"),
    Field("cover", "Cover", "text", "cover"),
    Field("type", "Type", "text", "vocab", facet=True, column="type", attr="type",
          placeholder="manga / manhwa / your own…", values=_attr_values("type")),
    Field("status", "Status", "text", "vocab", facet=True, column="status", attr="status",
          placeholder="ongoing / completed / your own…", values=_attr_values("status")),
    Field("year", "Year", "text", "line", attr="year", placeholder="Year"),
    Field("flags", "Flags", "list", "flags", facet=True, attr="flags", values=_flag_values),
    Field("authors", "Authors", "list", "chips", facet=True, attr="authors",
          placeholder="add author…",
          values=lambda d: {n for n in (*d.meta.authors, *d.meta.artists) if n}),
    # artists share the people vocabulary: one person is both, often on one title
    Field("artists", "Artists", "list", "chips", attr="artists",
          placeholder="add artist…", vocab="authors"),
    Field("characters", "Characters", "list", "chips", facet=True, attr="characters",
          placeholder="add character…", values=_attr_values("characters")),
    Field("studio", "Studio", "list", "chips", facet=True, attr="studio",
          placeholder="add studio…", values=_attr_values("studio")),
    Field("genres", "Genres", "list", "chips", facet=True, attr="genres",
          placeholder="add genre…", values=_attr_values("genres")),
    Field("tags", "Tags", "list", "chips", facet=True, attr="tags",
          placeholder="add tag…", values=_attr_values("tags")),
    Field("desc", "Description", "text", "multiline", attr="desc", placeholder="Description"),
    # Facet-only: a language is a property of the chapters that arrived, not
    # something a human types into a title.
    Field("language", "Language", "list", "chips", editable=False, facet=True,
          values=lambda d: {c.lang for c in d.chapters if c.lang}),
)

BY_ID: dict[str, Field] = {f.id: f for f in BUILTIN}
FACETS: tuple[Field, ...] = tuple(f for f in BUILTIN if f.facet)
EDITABLE_IDS: frozenset[str] = frozenset(f.id for f in BUILTIN if f.editable)


def control_for(type_: str) -> Control:
    return _CONTROL_FOR.get(type_, "line")


def facet_values(f: Field, doc: TitleDoc) -> set[str]:
    """The values of `f` on this document, for filtering and counting."""
    return f.values(doc) if f.values is not None else set()
