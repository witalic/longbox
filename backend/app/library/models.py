"""Domain models for the layered vault document (design/state-model.md §8).

`TitleDoc` is the exact on-disk shape of `title.json`: a `meta` layer written only
via draft → commit, a per-field `provenance` map (so automatic capture can never
clobber a manual edit — I3), the scraped `chapters` rows, and a `user` layer
written only via instant write-through. `TitleOut` is the flat camelCase DTO the
frontend consumes, composed from those layers.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .migrations import CURRENT_SCHEMA
from .versions import chapter_version

# type/status are OPEN vocabularies: the UI suggests the common values, capture
# normalizes known synonyms onto them, but a user-entered value is stored as-is.
MediaType = str
Status = str
ReadState = Literal["read", "reading", "unread"]
Role = Literal["author", "artist", "both"]
Origin = Literal["auto", "manual"]


class Flags(BaseModel):
    adult: bool = False
    ai: bool = False
    censored: bool = False


class SourceRef(BaseModel):
    """Where this title is captured from (one binding; multi-source merge is out of scope)."""
    domain: str = ""
    url: str = ""


class TitleMeta(BaseModel):
    """The metadata layer — written ONLY through draft → commit."""
    title: str = ""
    alt: str = ""
    authors: list[str] = []
    artists: list[str] = []
    characters: list[str] = []
    # empty = the user hasn't chosen yet; nothing is ever auto-substituted
    type: MediaType = ""
    status: Status = ""

    # One folding rule for the open vocabularies: stored lowercase (so custom
    # values can't fork on case), capitalized only at display time in the UI.
    @field_validator("type", "status")
    @classmethod
    def _fold_vocab(cls, v: str) -> str:
        return (v or "").strip().lower()
    year: str = ""
    genres: list[str] = []
    tags: list[str] = []
    flags: Flags = Flags()
    desc: str = ""
    coverSource: str = ""  # URL the stored cover bytes were captured from
    source: SourceRef = SourceRef()
    # chapter display order: "auto" = smart by number; "manual" = the doc's
    # array order, exactly as the user arranged it
    chapterOrder: str = "auto"


class FieldProvenance(BaseModel):
    origin: Origin = "manual"
    url: str = ""           # page the auto value came from
    recipeVersion: int = 0  # recipe version that extracted it


class ChapterRow(BaseModel):
    """One scraped chapter row — one row per translation (language + group).

    `id` is the dedup key for accumulative capture AND the future on-disk file
    name (`chapters/<id>.cbz`), so it is not editable like a normal field.
    Capture-merge into a draft is add-only by id: an existing row is never
    overwritten by a re-capture (I3 for chapters, by construction).
    """
    id: str
    num: str = ""
    title: str = ""
    url: str = ""
    lang: str = ""
    group: str = ""
    date: str = ""


class UserLayer(BaseModel):
    """The user layer — instant write-through, never touched by a meta commit."""
    fav: bool = False
    rating: int = 0  # 0 = unrated, else 1..5
    read: dict[str, ReadState] = {}  # chapter id → read state
    # chapter id → seconds. Where a page chapter remembers WHICH page, a video
    # chapter remembers WHERE in the file — "read" alone cannot resume an
    # episode. Both kinds can appear in one title, so both live here.
    position: dict[str, float] = {}


class TitleDoc(BaseModel):
    """The exact `title.json` document (schema versioned for future migration)."""
    model_config = ConfigDict(populate_by_name=True)

    # the shape THIS build writes; migrations.py owns the number and the steps
    schema_version: int = Field(default=CURRENT_SCHEMA, alias="schema")
    meta: TitleMeta = TitleMeta()
    provenance: dict[str, FieldProvenance] = {}
    chapters: list[ChapterRow] = []
    user: UserLayer = UserLayer()


class DraftIn(BaseModel):
    """A draft commit: the meta-side layers only. The user layer is absent by
    design — a commit merges its own layers and can never roll back user state."""
    meta: TitleMeta
    provenance: dict[str, FieldProvenance] = {}
    chapters: list[ChapterRow] = []


class UserPatch(BaseModel):
    """Write-through user-layer changes; only the supplied keys are applied."""
    fav: bool | None = None
    rating: int | None = None
    read: dict[str, ReadState] | None = None
    position: dict[str, float] | None = None


# ---- wire DTOs (flat camelCase, composed from the layers) ----

class ChapterOut(ChapterRow):
    read: ReadState = "unread"
    dl: bool = False       # a downloaded archive exists in the vault
    pages: int = 0         # image pages inside the archive (0 for non-zip)
    dlSource: str = ""     # where the DOWNLOAD came from (independent of meta.source)
    dlAt: str = ""         # when it was downloaded (ISO, from the sidecar)
    v: str = ""            # media version — what a page/video URL is cached under
    position: float = 0.0  # seconds into a video chapter (0 for page media)
    # "pages" (a zip of images) or "video" (the episode file itself). The two
    # kinds are stored differently and READ differently; everything else about a
    # chapter — identity, provenance, translations, progress — is the same.
    kind: str = "pages"
    duration: float = 0.0  # seconds, learned from the player on first play
    playable: bool = True  # a browser can open this container as it stands
    codec: str = ""        # h264 / hevc / av1 … — what the file actually holds
    faststart: bool = True # the index sits before the media, so playback starts at once


class TitleOut(BaseModel):
    id: str
    title: str
    alt: str
    authors: list[str]
    artists: list[str]
    year: str
    type: MediaType
    status: Status
    characters: list[str]
    genres: list[str]
    tags: list[str]
    flags: Flags
    desc: str
    source: SourceRef
    coverSource: str
    chapterOrder: str
    cover: str  # local cover endpoint URL when bytes exist, else ""
    fav: bool
    rating: int
    unread: int  # chapters not marked read
    provenance: dict[str, FieldProvenance]
    chapters: list[ChapterOut]

    @classmethod
    def from_doc(cls, title_id: str, doc: TitleDoc, cover_url: str = "",
                 media: dict[str, dict] | None = None) -> "TitleOut":
        m, u = doc.meta, doc.user
        media = media or {}
        chapters = []
        for c in doc.chapters:
            side = media.get(c.id)
            chapters.append(ChapterOut(
                **c.model_dump(), read=u.read.get(c.id, "unread"),
                dl=side is not None, pages=(side or {}).get("pages", 0),
                v=chapter_version(side),
                kind=(side or {}).get("kind", "pages"),
                position=float(u.position.get(c.id, 0.0)),
                duration=float((side or {}).get("duration") or 0.0),
                playable=bool((side or {}).get("playable", True)),
                codec=(side or {}).get("codec", ""),
                faststart=bool((side or {}).get("faststart", True)),
                dlSource=(side or {}).get("pageUrl") or (side or {}).get("fileUrl") or "",
                dlAt=(side or {}).get("downloadedAt", ""),
            ))
        return cls(
            id=title_id, title=m.title, alt=m.alt, authors=m.authors, artists=m.artists,
            year=m.year, type=m.type, status=m.status, genres=m.genres, tags=m.tags,
            characters=m.characters,
            flags=m.flags, desc=m.desc, source=m.source, coverSource=m.coverSource,
            chapterOrder=m.chapterOrder, cover=cover_url, fav=u.fav, rating=u.rating,
            unread=sum(1 for c in chapters if c.read != "read"),
            provenance=doc.provenance, chapters=chapters,
        )


class AuthorWork(BaseModel):
    id: str
    title: str
    cover: str = ""


class Author(BaseModel):
    id: str
    name: str
    role: Role
    works: list[AuthorWork]
    fav: bool = False  # user layer — persisted in the vault's authors.json
    titles: int = 0
    chapters: int = 0
    topTags: list[str] = []  # most common tags across this person's titles


class Source(BaseModel):
    """A site aggregated from the titles that reference it, joined with its recipe."""
    id: str
    domain: str
    homepage: str
    titles: int
    hasRecipe: bool = False
    recipeVer: int = 0
    fields: list[str] = []  # recipe field names mapped for this domain


class FacetValue(BaseModel):
    v: str
    n: int  # titles matching, with the other facets' filters applied


class Facets(BaseModel):
    types: list[FacetValue]
    statuses: list[FacetValue]
    genres: list[FacetValue]
    tags: list[FacetValue]
    languages: list[FacetValue]
    flags: list[FacetValue]
    characters: list[FacetValue]
    authors: list[FacetValue]
