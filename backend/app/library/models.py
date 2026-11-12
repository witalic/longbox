"""Domain models for the layered vault document (design/state-model.md §8).

`TitleDoc` is the exact on-disk shape of `title.json`: a `meta` layer written only
via draft → commit, a per-field `provenance` map (so automatic capture can never
clobber a manual edit — I3), the scraped `chapters` rows, and a `user` layer
written only via instant write-through. `TitleOut` is the flat camelCase DTO the
frontend consumes, composed from those layers.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .migrations import CURRENT_SCHEMA
from .versions import chapter_version, stills_version

# type/status are OPEN vocabularies: the UI suggests the common values, capture
# normalizes known synonyms onto them, but a user-entered value is stored as-is.
MediaType = str
Status = str
ReadState = Literal["read", "reading", "unread"]
Origin = Literal["auto", "manual"]


class Flags(BaseModel):
    adult: bool = False
    ai: bool = False
    censored: bool = False


class SourceRef(BaseModel):
    """Where this title is captured from (one binding; multi-source merge is out of scope)."""
    domain: str = ""
    url: str = ""


class CustomFieldDef(BaseModel):
    """A field the user defined. Stored in the VAULT (it describes this library's
    data, not this machine), and turned into a registry Field at load."""
    id: str
    label: str
    type: str = "text"        # text | description | number | list | date
    facet: bool = True        # offer it as a library filter
    # Kept for vaults written before `description` was a type of its own: a
    # multi-line text field IS a description, and is read back as one.
    multiline: bool = False
    placeholder: str = ""


class TitleMeta(BaseModel):
    """The metadata layer — written ONLY through draft → commit."""
    title: str = ""
    alt: str = ""
    authors: list[str] = []  # who made the work — writer, artist, both
    characters: list[str] = []
    studio: list[str] = []  # a list: co-productions are normal
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
    # User-defined fields, keyed by field id (library/fields.py). A value the
    # registry no longer describes is KEPT, not dropped: deleting a field must
    # not shred data the user typed, and re-adding it brings the values back.
    custom: dict[str, str | list[str]] = {}


class FieldProvenance(BaseModel):
    origin: Origin = "manual"
    url: str = ""           # page the auto value came from
    recipeVersion: int = 0  # recipe version that extracted it


class ChapterRow(BaseModel):
    """One scraped chapter row — one row per translation (language + group).

    `id` is the dedup key for accumulative capture AND the stem of every file the
    chapter owns (`chapters/<id>.zip`, its sidecar, …), so it never changes.
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
    # What part of the work the entry belongs to — a volume, a season, a year.
    # The title page groups by it; it is never part of the entry's identity.
    section: str = ""


class UserLayer(BaseModel):
    """The user layer — instant write-through, never touched by a meta commit."""
    fav: bool = False
    rating: int = 0  # 0 = unrated, else 1..5
    read: dict[str, ReadState] = {}  # chapter id → read state
    # chapter id → seconds. Where a page chapter remembers WHICH page, a video
    # chapter remembers WHERE in the file — "read" alone cannot resume an
    # episode. Both kinds can appear in one title, so both live here.
    position: dict[str, float] = {}
    # How the reader shows THIS title (ReaderPrefs; empty = never set). Kept
    # loose on the way in from disk: a hand-edited value must not cost the whole
    # title — the reader clamps what it reads, and a PATCH is validated.
    reader: dict[str, Any] = {}

    @field_validator("reader", mode="before")
    @classmethod
    def _any_reader(cls, v: object) -> dict:
        return v if isinstance(v, dict) else {}


class ReaderPrefs(BaseModel):
    mode: Literal["pages", "strip"] = "pages"
    fit: Literal["height", "width", "custom"] = "height"
    width: int = Field(default=720, ge=480, le=1400)
    margins: int = Field(default=0, ge=0, le=200)


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
    reader: ReaderPrefs | None = None


# ---- wire DTOs (flat camelCase, composed from the layers) ----

class SubtitleOut(BaseModel):
    """One of an episode's subtitle tracks (library/subtitles.py)."""
    id: str
    lang: str = ""
    label: str = ""
    default: bool = False
    origName: str = ""   # the file it was made from
    format: str = ""     # what that file was: srt / vtt / ass / ssa
    v: str = ""          # what its URL is cached under: the stored WebVTT's digest


def subtitles_of(side: dict | None) -> list[SubtitleOut]:
    """An episode's tracks as its sidecar lists them — one written by hand or
    by an older build that does not read is left out, never a failed title."""
    out = []
    for raw in (side or {}).get("subtitles") or []:
        try:
            out.append(SubtitleOut(**{k: raw.get(k) for k in ("id", "lang", "label", "default",
                                                            "origName", "format") if raw.get(k) is not None},
                                   v=str(raw.get("sha") or "")))
        except (AttributeError, ValueError):
            continue
    return out


class ChapterOut(ChapterRow):
    read: ReadState = "unread"
    dl: bool = False       # a downloaded archive exists in the vault
    pages: int = 0         # image pages inside the archive (0 for non-zip)
    # The page the DOWNLOAD came from (independent of meta.source) — never the
    # file's own address: that is a file host's, with a token that expires.
    dlSource: str = ""
    # The name the media ARRIVED under. The vault renames every file to the
    # chapter's id, so this is the only record of what the episode or chapter
    # was called at its source.
    origName: str = ""
    dlAt: str = ""         # when it was downloaded (ISO, from the sidecar)
    v: str = ""            # media version — what a page/video URL is cached under
    position: float = 0.0  # seconds into a video chapter (0 for page media)
    # "pages" (a zip of images) or "video" (the episode file itself). The two
    # kinds are stored differently and READ differently; everything else about a
    # chapter — identity, provenance, translations, progress — is the same.
    kind: str = "pages"
    duration: float = 0.0  # seconds, learned from the player on first play
    playable: bool = True  # the window plays it: as stored, or through its playable copy
    playableCopy: bool = False  # a playable copy stands in for it (library/copies.py)
    container: str = ""    # mp4 / mkv / avi … — named in the list when it cannot play
    codec: str = ""        # h264 / hevc / av1 … — what the PLAYED file holds
    audio: str = ""        # opus / aac / ac3 … — read from an mkv's tracks or a copy, "" elsewhere
    faststart: bool = True  # the index sits before the media, so playback starts at once
    poster: bool = False    # the one frame a tile wears is cut and stored
    sheet: str = ""         # the contact grid's geometry ("3x3"), "" when none
    stills: str = "0"       # the version those two are cached under
    subtitles: list[SubtitleOut] = []  # an episode's subtitle tracks


# How many stand-ins a title with no stored cover is worth composing. The grid
# rotates them on hover, and past a handful nobody ever sees the rest.
STAND_INS = 6


def chapter_out(c: ChapterRow, side: dict | None, user: UserLayer) -> ChapterOut:
    """One chapter row as the window sees it: the row, its sidecar, its progress."""
    copied = (side or {}).get("copy") or {}
    # what the window is served: the playable copy wherever one exists
    played = copied or side or {}
    return ChapterOut(
        **c.model_dump(), read=user.read.get(c.id, "unread"),
        dl=side is not None, pages=(side or {}).get("pages", 0),
        v=chapter_version(side),
        kind=(side or {}).get("kind", "pages"),
        position=float(user.position.get(c.id, 0.0)),
        duration=float((side or {}).get("duration") or 0.0),
        playable=bool((side or {}).get("playable", True)) or bool(copied),
        playableCopy=bool(copied),
        container=(side or {}).get("container", ""),
        codec=played.get("codec", ""),
        audio=played.get("audio", ""),
        faststart=bool(played.get("faststart", True)),
        poster=bool((side or {}).get("poster", False)),
        sheet=str((side or {}).get("sheet") or ""),
        stills=stills_version(side),
        dlSource=(side or {}).get("pageUrl") or "",
        dlAt=(side or {}).get("downloadedAt", ""),
        origName=(side or {}).get("origName", ""),
        subtitles=subtitles_of(side),
    )


class TitleCard(BaseModel):
    """What a list shows of a title — the library, a value page, a picker — and
    never its chapters: a library of thousands is listed as cards and a title is
    opened whole. A TitleOut IS a card, so whatever a window receives of a
    title refreshes the card it lists too."""
    id: str
    title: str
    alt: str
    authors: list[str]
    year: str
    type: MediaType
    status: Status
    genres: list[str]
    tags: list[str]
    flags: Flags
    source: SourceRef
    # Three answers, because there are three different questions.
    #
    # What a tile SHOWS, in rotation order: the title's own covers, or — when it
    # has none — stand-ins cut from its own media (cards.stand_ins).
    covers: list[str] = []
    # The first of those. Almost every surface wants one image, and asking each
    # of them to index a list is how they end up disagreeing about which.
    cover: str
    # What is actually STORED for this title. Empty means nobody has given it a
    # cover and `covers` is standing in — which is what the editor needs to know
    # before offering to remove one.
    ownCovers: list[str] = []
    fav: bool
    rating: int
    unread: int  # chapters not marked read
    chapterCount: int = 0
    pageCount: int = 0  # the pages of every downloaded chapter
    # every language and every group its chapters came in, as they first appear
    chapterLangs: list[str] = []
    chapterGroups: list[str] = []
    # What a tile with no cover of its own may wear: its first downloaded
    # entries — a page, or an episode whose frame is cut or can be.
    standIns: list[ChapterOut] = []
    # Moves with every write of the title (index.title_cards_v1): what a window
    # holds of a title, whole, is current while its rev is.
    rev: int = 0

    @classmethod
    def from_doc(cls, title_id: str, doc: TitleDoc, covers: list[str] | None = None,
                 media: dict[str, dict] | None = None,
                 own: list[str] | None = None, rev: int = 0) -> "TitleCard":
        return cls(**_card_fields(title_id, doc, covers, media or {}, own, rev))


def _pages(side: dict | None) -> int:
    """A sidecar's page count as a number — one written by hand may say anything."""
    try:
        return max(0, int((side or {}).get("pages") or 0))
    except (TypeError, ValueError):
        return 0


def _card_fields(title_id: str, doc: TitleDoc, covers: list[str] | None, media: dict[str, dict],
                 own: list[str] | None, rev: int, chapters: list[ChapterOut] | None = None) -> dict:
    """A card's fields, with the chapters already composed when there are any."""
    m, u = doc.meta, doc.user
    covers = list(covers or [])
    own = list(own if own is not None else covers)
    stand: list[ChapterOut] = []
    if not own:
        for i, c in enumerate(doc.chapters):
            if len(stand) >= STAND_INS:
                break
            side = media.get(c.id)
            if side is None:
                continue
            row = chapters[i] if chapters is not None else chapter_out(c, side, u)
            if (row.poster or row.playable) if row.kind == "video" else row.pages:
                stand.append(row)
    return dict(
        id=title_id, title=m.title, alt=m.alt, authors=m.authors, year=m.year,
        type=m.type, status=m.status, genres=m.genres, tags=m.tags, flags=m.flags,
        source=m.source, covers=covers, cover=covers[0] if covers else "", ownCovers=own,
        fav=u.fav, rating=u.rating,
        unread=sum(1 for c in doc.chapters if u.read.get(c.id, "unread") != "read"),
        chapterCount=len(doc.chapters),
        pageCount=sum(_pages(media.get(c.id)) for c in doc.chapters),
        chapterLangs=list(dict.fromkeys(c.lang for c in doc.chapters if c.lang)),
        chapterGroups=list(dict.fromkeys(c.group for c in doc.chapters if c.group)),
        standIns=stand, rev=rev,
    )


class TitleOut(TitleCard):
    studio: list[str]
    characters: list[str]
    desc: str
    coverSource: str
    chapterOrder: str
    # user-defined field values, keyed by field id — served flat so the editor
    # reads them the same way it reads a built-in
    custom: dict[str, str | list[str]] = {}
    reader: dict[str, Any] = {}
    provenance: dict[str, FieldProvenance]
    chapters: list[ChapterOut]

    @classmethod
    def from_doc(cls, title_id: str, doc: TitleDoc, covers: list[str] | None = None,
                 media: dict[str, dict] | None = None,
                 own: list[str] | None = None, rev: int = 0) -> "TitleOut":
        m, u = doc.meta, doc.user
        media = media or {}
        chapters = [chapter_out(c, media.get(c.id), u) for c in doc.chapters]
        return cls(
            **_card_fields(title_id, doc, covers, media, own, rev, chapters),
            studio=m.studio, characters=m.characters, desc=m.desc, coverSource=m.coverSource,
            chapterOrder=m.chapterOrder, custom=dict(m.custom), reader=dict(u.reader),
            provenance=doc.provenance, chapters=chapters,
        )


class AuthorWork(BaseModel):
    id: str
    title: str
    cover: str = ""


class BrowseGroup(BaseModel):
    """Titles grouped by ONE value of ONE list field: a person, a studio, a
    character, or a value of a field the user defined. People carry one extra,
    a favourite mark; nothing else needs it, so it is optional."""
    id: str
    field: str
    value: str
    works: list[AuthorWork]
    titles: int = 0
    chapters: int = 0
    topTags: list[str] = []  # most common tags across this group's titles
    fav: bool = False        # user layer — persisted in the vault's authors.json
    # what the owner wrote on the value's page (design/value-pages.md)
    described: bool = False
    excerpt: str = ""
    image: str = ""


class Bookmark(BaseModel):
    """A link the user saved under a source. Not a scraping fact — it is where
    they like to start on that site."""
    name: str
    url: str
    added: str = ""  # when it was saved (ISO); "" for a link saved before that was kept


class Source(BaseModel):
    """A site aggregated from the titles that reference it, joined with its recipe
    (what longbox learned) and with the user's own take on it (group, bookmarks)."""
    id: str
    domain: str
    homepage: str
    titles: int
    hasRecipe: bool = False
    recipeVer: int = 0
    fields: list[str] = []  # recipe field names mapped for this domain
    group: str = ""         # the user's grouping; "" = ungrouped
    bookmarks: list[Bookmark] = []
    # Frozen: still a source, still listed here, but kept out of the browser's
    # rail. A site you have finished with clutters the place you browse from
    # long before you are willing to forget what longbox learned about it.
    frozen: bool = False


class FacetValue(BaseModel):
    v: str
    n: int  # titles matching, with the other facets' filters applied


# Counts keyed by FIELD ID (see library/fields.py). A fixed class here would
# mean a field the user defines could never have a facet.
FacetCounts = dict[str, list[FacetValue]]
