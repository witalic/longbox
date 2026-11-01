"""An episode's subtitles (design/subtitles.md §3).

Stored as WebVTT — the one format a browser's player shows — beside the
episode: `chapters/<episode>.sub-<n>.vtt`, with the file as it arrived kept next
to it (`<episode>.sub-<n>.orig.<ext>`) for a renderer that can do more with an
ASS file later. The episode's sidecar lists them (`subtitles`); they belong to
the episode — replacing its video keeps them, removing its file or its entry
removes them."""
from __future__ import annotations

import re
from pathlib import Path

from . import media

SUBTITLE_EXTS = {".srt", ".vtt", ".ass", ".ssa"}
# a subtitle file in a chapters directory, and the episode stem it belongs to
_FILE = re.compile(r"^(?P<stem>.+)\.sub-(?P<n>\d+)\.(?:vtt|orig\.[a-z0-9]+)$", re.IGNORECASE)


class UnreadableSubtitle(media.UnsupportedArchiveError):
    """A subtitle the vault refuses — it is never stored as it came."""


def is_subtitle(name: str | Path) -> bool:
    return Path(name).suffix.lower() in SUBTITLE_EXTS


def owner_stem(name: str) -> str | None:
    """The episode stem a file in a chapters directory belongs to, when it is one
    of that episode's subtitles."""
    m = _FILE.match(name)
    return m.group("stem") if m else None


def vtt_name(stem: str, n: int) -> str:
    return f"{stem}.sub-{n}.vtt"


def orig_name(stem: str, n: int, ext: str) -> str:
    return f"{stem}.sub-{n}.orig{ext.lower()}"


# ---- what a subtitle file says ----------------------------------------------

def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        # a subtitle that is not UTF-8 is, overwhelmingly, a Cyrillic one saved
        # by a Windows tool
        return data.decode("cp1251", errors="replace")


_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.:](\d{1,3})")


def _ms(m: re.Match) -> int:
    h, mi, s, frac = m.groups()
    # ".5" is half a second, not five milliseconds; ASS counts hundredths
    return ((int(h) * 60 + int(mi)) * 60 + int(s)) * 1000 + int(frac.ljust(3, "0")[:3])


def _stamp(ms: int) -> str:
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02}:{m:02}:{s:02}.{ms:03}"


def _srt_cues(text: str) -> list[tuple[int, int, str]]:
    cues = []
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            if "-->" not in line:
                continue
            start, _, end = line.partition("-->")
            a, b = _TIME.search(start), _TIME.search(end)
            # an ASS override some SRT files carry ({\an8}) means nothing here
            body = re.sub(r"\{\\[^}]*\}", "", "\n".join(lines[i + 1:])).strip()
            if a and b and body:
                cues.append((_ms(a), _ms(b), body))
            break
    return cues


def _ass_text(raw: str) -> str:
    text = re.sub(r"\{[^}]*\}", "", raw)  # styling, positions, karaoke: flattened
    text = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    text = "\n".join(line.strip() for line in text.split("\n")).strip()
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ass_cues(text: str) -> list[tuple[int, int, str]]:
    cues: list[tuple[int, int, str]] = []
    fields: list[str] = []
    events = False
    for raw in text.split("\n"):
        line = raw.strip()
        if line.startswith("["):
            events = line.lower() == "[events]"
            continue
        if not events:
            continue
        head, _, rest = line.partition(":")
        if head.lower() == "format":
            fields = [f.strip().lower() for f in rest.split(",")]
        elif head.lower() == "dialogue" and fields:
            # the text is the last field and may hold commas of its own
            row = dict(zip(fields, rest.split(",", len(fields) - 1)))
            a, b = _TIME.search(row.get("start", "")), _TIME.search(row.get("end", ""))
            body = _ass_text(row.get("text", ""))
            if a and b and body:
                cues.append((_ms(a), _ms(b), body))
    return sorted(cues, key=lambda c: c[0])


def to_webvtt(data: bytes, ext: str) -> str:
    """The subtitle as WebVTT, or UnreadableSubtitle — before the vault is
    touched. A WebVTT file stays as it is (its positions and styles are the
    player's own); SRT is re-timed; ASS/SSA becomes plain text cues."""
    text = _decode(data).lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if ext.lower() == ".vtt" or text.lstrip().startswith("WEBVTT"):
        if "-->" not in text:
            raise UnreadableSubtitle("the subtitle file holds no cue that could be read")
        return text.strip() + "\n"
    cues = _ass_cues(text) if ext.lower() in (".ass", ".ssa") else _srt_cues(text)
    if not cues:
        raise UnreadableSubtitle("the subtitle file holds no cue that could be read")
    out = ["WEBVTT", ""]
    for a, b, body in cues:
        out += [f"{_stamp(a)} --> {_stamp(b)}", body, ""]
    return "\n".join(out)


# ---- the language a file's name says ------------------------------------------

LANGS: dict[str, tuple[str, set[str]]] = {
    "en": ("English", {"en", "eng", "english"}),
    "ru": ("Русский", {"ru", "rus", "russian"}),
    "uk": ("Українська", {"uk", "ukr", "ua", "ukrainian"}),
    "ja": ("日本語", {"ja", "jp", "jpn", "japanese"}),
    "de": ("Deutsch", {"de", "ger", "deu", "german"}),
    "fr": ("Français", {"fr", "fre", "fra", "french"}),
    "es": ("Español", {"es", "esp", "spa", "spanish"}),
    "pt": ("Português", {"pt", "por", "ptbr", "portuguese"}),
    "it": ("Italiano", {"it", "ita", "italian"}),
    "pl": ("Polski", {"pl", "pol", "polish"}),
    "zh": ("中文", {"zh", "zho", "chi", "chs", "cht", "chinese"}),
    "ko": ("한국어", {"ko", "kor", "korean"}),
}


def guess_lang(name: str) -> str:
    """A subtitle's language read off its name — `.en.srt`, `[RUS]`, `_ukr`.
    A two-letter word counts only right before the extension, where it is a
    code and not a word of the title."""
    words = re.findall(r"[a-z]+", Path(name).stem.lower())
    for i, word in enumerate(reversed(words)):
        if len(word) == 2 and i:
            continue
        for code, (_label, names) in LANGS.items():
            if word in names:
                return code
    return ""


def label_of(lang: str) -> str:
    return LANGS[lang][0] if lang in LANGS else lang.upper()
