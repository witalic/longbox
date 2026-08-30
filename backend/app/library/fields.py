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

from .models import CustomFieldDef, TitleDoc

# How a value is stored and filtered. The five the app can render.
# `description` stores exactly what `text` stores. It is a separate TYPE
# because what it is FOR is different: prose, not a value. Prose has no
# vocabulary to tick, so it is never a filter and never an axis — which is a
# fact about the field, not a checkbox someone has to remember to clear.
FieldType = Literal["text", "description", "number", "list", "date"]
# Which widget the editor draws. Derived from `type` for custom fields; built-ins
# say it outright, because `desc` is a textarea and `type` is a vocabulary combo
# while both are stored as plain text.
Control = Literal["line", "multiline", "vocab", "chips", "number", "date",
                  "cover", "flags"]

_CONTROL_FOR: dict[str, Control] = {
    "text": "line", "description": "multiline", "number": "number",
    "list": "chips", "date": "date",
}


@dataclass(frozen=True)
class Field:
    """One metadata field, built-in or user-defined."""

    id: str                     # the key EVERYWHERE: query, facets, provenance, custom map
    label: str
    type: FieldType
    control: Control
    builtin: bool = True
    # Which block of the editor draws it. A user-defined field lands in "yours"
    # by default, which is exactly where the user expects to find it.
    group: str = "yours"
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
          placeholder="Title (required)", group="identity"),
    Field("alt", "Alt titles", "text", "line", attr="alt", placeholder="Alternate titles", group="identity"),
    Field("cover", "Cover", "text", "cover", group="identity"),
    Field("type", "Type", "text", "vocab", facet=True, column="type", attr="type",
          placeholder="manga / manhwa / your own…", values=_attr_values("type"), group="about"),
    Field("status", "Status", "text", "vocab", facet=True, column="status", attr="status",
          placeholder="ongoing / completed / your own…", values=_attr_values("status"), group="about"),
    Field("year", "Year", "text", "line", attr="year", placeholder="Year", group="about"),
    Field("flags", "Flags", "list", "flags", facet=True, attr="flags", values=_flag_values, group="about"),
    Field("authors", "Authors", "list", "chips", facet=True, attr="authors",
          placeholder="add author…",
          values=lambda d: {n for n in (*d.meta.authors, *d.meta.artists) if n}, group="people"),
    # Artists share the people VOCABULARY with authors — one person is often
    # both — but they are counted on their own: without that they can be typed
    # onto a title and then never browsed to or filtered by, because the joint
    # `authors` facet is the only one that ever had a count.
    Field("artists", "Artists", "list", "chips", facet=True, attr="artists",
          placeholder="add artist…", vocab="authors", values=_attr_values("artists"),
          group="people"),
    Field("characters", "Characters", "list", "chips", facet=True, attr="characters",
          placeholder="add character…", values=_attr_values("characters"), group="topics"),
    Field("studio", "Studio", "list", "chips", facet=True, attr="studio",
          placeholder="add studio…", values=_attr_values("studio"), group="people"),
    Field("genres", "Genres", "list", "chips", facet=True, attr="genres",
          placeholder="add genre…", values=_attr_values("genres"), group="topics"),
    Field("tags", "Tags", "list", "chips", facet=True, attr="tags",
          placeholder="add tag…", values=_attr_values("tags"), group="topics"),
    Field("desc", "Description", "text", "multiline", attr="desc", placeholder="Description", group="topics"),
    # Facet-only: a language is a property of the chapters that arrived, not
    # something a human types into a title.
    Field("language", "Language", "list", "chips", editable=False, facet=True,
          values=lambda d: {c.lang for c in d.chapters if c.lang}, group="about"),
)

def control_for(type_: str) -> Control:
    return _CONTROL_FOR.get(type_, "line")


def facet_values(f: Field, doc: TitleDoc) -> set[str]:
    """The values of `f` on this document, for filtering and counting."""
    return f.values(doc) if f.values is not None else set()


# ---- user-defined fields -------------------------------------------------
#
# Their definitions live in the vault, so the registry is per-LIBRARY and can
# change while the app runs: switching the library path swaps this set. That is
# why the built-ins are a constant but the registry is a function — a caller
# that cached the tuple would keep filtering by a field this vault never had.
_custom: tuple[Field, ...] = ()


def _custom_values(fid: str) -> Callable[[TitleDoc], set[str]]:
    def read(doc: TitleDoc) -> set[str]:
        v = doc.meta.custom.get(fid)
        if isinstance(v, list):
            return {str(x) for x in v if x}
        return {str(v)} if v else set()
    return read


def as_field(d: CustomFieldDef) -> Field:
    """A stored definition as the registry entry every consumer reads."""
    # a vault written before `description` existed says text + multiline
    type_ = "description" if d.type == "text" and d.multiline else d.type
    control = control_for(type_)
    # A chips control draws a borderless input: with no placeholder it is a row
    # with nothing in it, and the field reads as broken. Say what it takes.
    placeholder = d.placeholder or (
        f"add {d.label.lower()}…" if type_ == "list" else d.label)
    return Field(
        id=d.id, label=d.label, type=type_, control=control,  # type: ignore[arg-type]
        # prose is never a filter, whatever the stored definition says
        builtin=False, facet=d.facet and type_ != "description", placeholder=placeholder,
        values=_custom_values(d.id),
    )  # group stays the default: a field you defined belongs with the others you did


def set_custom(defs: list[CustomFieldDef]) -> None:
    """Point the registry at THIS library's user-defined fields."""
    global _custom
    taken = {f.id for f in BUILTIN}
    _custom = tuple(as_field(d) for d in defs if d.id not in taken)


def registry() -> tuple[Field, ...]:
    """Every field this library has, in editor order: built-ins, then yours."""
    return BUILTIN + _custom


def by_id() -> dict[str, Field]:
    return {f.id: f for f in registry()}


def facets() -> tuple[Field, ...]:
    return tuple(f for f in registry() if f.facet)
