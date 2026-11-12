"""A title on the wire, composed from what its index row holds — the document,
the chapter sidecars (keyed by file stem) and the cover URLs: the whole title a
window opens (TitleOut), and the card a list shows of it (TitleCard), which the
index keeps ready at every write. One place, so the two can never disagree
about a field they share."""
from __future__ import annotations

from .models import STAND_INS, TitleCard, TitleDoc, TitleOut
from .vault import safe_id
from .versions import chapter_version, stills_version


def media_of(doc: TitleDoc, sidecars: dict[str, dict]) -> dict[str, dict]:
    """The sidecars by chapter id."""
    return {c.id: sidecars[safe_id(c.id)] for c in doc.chapters if safe_id(c.id) in sidecars}


def stand_in_sources(doc: TitleDoc, sidecars: dict[str, dict]) -> list[tuple[str, str, dict]]:
    """What a title with no cover of its own can show instead: the first page
    of each downloaded chapter, or the frame the window already cut for an
    episode's tile — (kind, chapter id, sidecar). Composed from the sidecars
    the caller is already holding, so a coverless title costs no disk at all."""
    out: list[tuple[str, str, dict]] = []
    for c in doc.chapters:
        if len(out) >= STAND_INS:
            break
        side = sidecars.get(safe_id(c.id))
        if not side:
            continue
        if side.get("kind") == "video":
            if side.get("poster"):
                out.append(("poster", c.id, side))
        elif side.get("pages"):
            out.append(("page", c.id, side))
    return out


def stand_ins(title_id: str, doc: TitleDoc, sidecars: dict[str, dict]) -> list[str]:
    out = []
    for kind, chapter_id, side in stand_in_sources(doc, sidecars):
        base = f"/api/titles/{title_id}/chapters/{chapter_id}"
        out.append(f"{base}/frames/poster?v={stills_version(side)}" if kind == "poster"
                   else f"{base}/pages/0?v={chapter_version(side)}")
    return out


def shown_covers(title_id: str, doc: TitleDoc, sidecars: dict[str, dict], covers: str) -> list[str]:
    """What a tile shows: the title's own covers, or stand-ins."""
    return covers.split() or stand_ins(title_id, doc, sidecars)


def title_out(title_id: str, doc: TitleDoc, sidecars: dict[str, dict], covers: str,
              rev: int) -> TitleOut:
    own = covers.split()
    return TitleOut.from_doc(title_id, doc, own or stand_ins(title_id, doc, sidecars),
                             media_of(doc, sidecars), own=own, rev=rev)


def card(title_id: str, doc: TitleDoc, sidecars: dict[str, dict], covers: str, rev: int) -> TitleCard:
    own = covers.split()
    return TitleCard.from_doc(title_id, doc, own or stand_ins(title_id, doc, sidecars),
                              media_of(doc, sidecars), own=own, rev=rev)
