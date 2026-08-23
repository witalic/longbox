"""Chapter media helpers. VAULT INVARIANT: every stored chapter archive is a
plain zip — whatever the site served (cbz IS zip; rar/7z get repacked on the
way in), so every page operation works on every stored chapter. A sidecar
keeps the download provenance. Pages are the archive's image entries in
natural order; page edits rewrite the archive atomically.
"""
from __future__ import annotations

import os
import time
import re
import shutil
import uuid
import zipfile
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}

# The SECOND kind of chapter media. A page chapter is a zip of images; a video
# chapter is the file itself — putting a 2 GB episode inside a zip would buy
# nothing and cost a full rewrite on every edit. The zip invariant is a rule
# about PAGE media, and this set is where the two kinds part company.
VIDEO_EXTS = {".mp4", ".m4v", ".webm", ".mkv", ".mov", ".avi", ".ts"}
# what a browser can actually play; the rest is stored and offered to the
# system player until the app learns to remux (see design/state-model.md §13)
PLAYABLE_VIDEO_EXTS = {".mp4", ".m4v", ".webm"}
VIDEO_CT_BY_EXT = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".mov": "video/quicktime",
    ".avi": "video/x-msvideo", ".ts": "video/mp2t",
}


def is_video(name: str | Path) -> bool:
    return Path(name).suffix.lower() in VIDEO_EXTS


# fourcc -> what a browser calls it. Anything absent here is stored and listed
# like any other episode; the app simply does not claim it can play it.
_VIDEO_CODECS = {b"avc1": "h264", b"avc3": "h264", b"hvc1": "hevc", b"hev1": "hevc",
                 b"av01": "av1", b"vp09": "vp9", b"vp08": "vp8"}


def probe_mp4(path: Path) -> dict:
    """What an mp4 says about itself, without decoding it.

    Two things decide whether playback FEELS instant, and neither is visible
    from the file name:

    * where `moov` sits. A player cannot start until it has that index, so a
      file with `moov` behind the media makes the browser fetch the tail of a
      600 MB file before the first frame ("slow loading" that is not the
      network's fault).
    * the video codec. HEVC plays only where the platform decodes it in
      hardware; everywhere else the browser stutters or refuses.
    """
    out: dict = {"faststart": False, "codec": ""}
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            offset, moov = 0, None
            while offset < size:
                fh.seek(offset)
                header = fh.read(8)
                if len(header) < 8:
                    break
                box = int.from_bytes(header[:4], "big")
                name = header[4:8]
                if box == 1:  # 64-bit size follows the header
                    box = int.from_bytes(fh.read(8), "big")
                if box <= 0:
                    break
                if name == b"moov":
                    moov = (offset, min(box, 32 * 1024 * 1024))
                    out["faststart"] = out.get("_seen_mdat") is not True
                    break
                if name == b"mdat":
                    out["_seen_mdat"] = True
                offset += box
            if moov is not None:
                fh.seek(moov[0])
                blob = fh.read(moov[1])
                for code, label in _VIDEO_CODECS.items():
                    if code in blob:
                        out["codec"] = label
                        break
    except OSError:
        return {"faststart": False, "codec": ""}
    out.pop("_seen_mdat", None)
    return out


def looks_like_video(path: Path) -> bool:
    """Whether the file's own bytes agree with its video extension.

    The same rule the zip invariant applies to page media: never store an
    opaque file. A site that answers a download with an HTML error page names
    it `.mp4` just as readily, and an episode that turns out to be 400 bytes of
    markup should be refused at ingest, not discovered at play time."""
    try:
        with path.open("rb") as fh:
            head = fh.read(16)
    except OSError:
        return False
    ext = path.suffix.lower()
    if ext in {".mp4", ".m4v", ".mov"}:
        return head[4:8] == b"ftyp"
    if ext in {".webm", ".mkv"}:
        return head[:4] == bytes((0x1A, 0x45, 0xDF, 0xA3))  # EBML
    if ext == ".avi":
        return head[:4] == b"RIFF" and head[8:12] == b"AVI "
    if ext == ".ts":
        return head[:1] == bytes((0x47,))  # MPEG-TS sync byte
    return False
CT_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif", ".bmp": "image/bmp",
}


class UnsupportedArchiveError(ValueError):
    """An archive we can't read (unknown format, corrupt file, or no RAR
    backend available) — ingest rejects it and page edits refuse to touch a
    stored leftover rather than overwrite it with a fresh zip."""


# A rename ONTO a file someone is reading is DENIED on Windows: reads are
# lock-free by design, and Python opens a file without sharing the right to
# delete it. Over a network vault that window is wide enough to lose writes —
# a page rewrite lands on an archive the reader may be serving from. The reader
# always finishes, so wait it out instead of failing a write.
_REPLACE_ATTEMPTS = 8


def replace_atomically(tmp: Path, final: Path) -> None:
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, final)  # atomic on the same filesystem
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


def tmp_path(path: Path) -> Path:
    """The scratch name a `tmp → rename` write goes through. Unique per call:
    a fixed ".tmp" would let two concurrent rewrites of one file interleave
    into a corrupted result — and two PROCESSES on one vault (a second window,
    a dev sidecar) share a fixed name even when each is locked internally."""
    return path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")


def _require_editable(path: Path) -> None:
    if not path.is_file():
        return
    if not zipfile.is_zipfile(path):
        raise UnsupportedArchiveError(
            "this chapter's archive is not a zip — page edits would destroy it")
    try:  # an encrypted or CRC-broken zip passes is_zipfile but reads nowhere
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
        if bad:
            raise UnsupportedArchiveError(f"this chapter's archive is damaged ({bad})")
    except (RuntimeError, zipfile.BadZipFile) as exc:
        raise UnsupportedArchiveError(f"this chapter's archive cannot be read: {exc}")


# The bytes decide the format — a CDN's content-type (or the name it serves an
# image under) is routinely wrong, and storing WebP bytes as `.jpg` would break
# every external reader.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"), (b"GIF89a", "gif"), (b"BM", "bmp"),
)


def sniff_ext(data: bytes) -> str:
    """The image extension the BYTES imply, '' when they are not an image."""
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12].lower().startswith((b"avif", b"avis")):
        return "avif"
    return ""


def _read_all_entries(src: Path) -> list[tuple[str, bytes]]:
    """Every FILE entry (name, bytes) of a zip/cbz, 7z or rar archive.
    Raises UnsupportedArchiveError when the format can't be read."""
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            return [(i.filename, z.read(i.filename)) for i in z.infolist() if not i.filename.endswith("/")]
    try:
        import tempfile

        import py7zr
        if py7zr.is_7zfile(src):
            # py7zr 1.x dropped in-memory readall — extract to a temp dir
            with py7zr.SevenZipFile(src) as z, tempfile.TemporaryDirectory(prefix="lb-7z-") as td:
                z.extractall(td)
                root = Path(td)
                return [(f.relative_to(root).as_posix(), f.read_bytes())
                        for f in sorted(root.rglob("*")) if f.is_file()]
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 — corrupt/encrypted 7z
        raise UnsupportedArchiveError(f"cannot read the 7z archive: {exc}")
    try:
        import rarfile
        if rarfile.is_rarfile(src):
            with rarfile.RarFile(src) as z:
                return [(i.filename, z.read(i)) for i in z.infolist() if not i.is_dir()]
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 — usually "no unrar/bsdtar backend"
        raise UnsupportedArchiveError(
            f"cannot read the RAR archive ({exc}) — install unrar, or convert the file to zip")
    raise UnsupportedArchiveError(
        "unsupported file — expected a zip/cbz/7z/rar archive or a single image")


def repack_to_zip(src: Path, dst: Path) -> int:
    """Repack ANY supported archive as a plain STORED zip at `dst` (atomic),
    entry names preserved (WebP/AVIF pages convert to a standard format, the
    name keeping its stem). Returns the image-page count. This is how non-zip
    content enters the vault — nothing opaque is ever stored."""
    entries = _read_all_entries(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = tmp_path(dst)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for name, data in entries:
            if Path(name).suffix.lower() in _CONVERT_EXTS:
                data, ext = normalize_page(data, Path(name).suffix)
                if ext != Path(name).suffix:
                    name = Path(name).with_suffix(ext).as_posix()  # zip names use forward slashes
            z.writestr(name, data)
    replace_atomically(tmp, dst)
    return len(image_entries(dst))


def _natkey(s: str) -> list:
    """Natural sort: 'page2' before 'page10'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def image_entries(path: Path) -> list[str]:
    """The archive's image entries, natural-sorted — the chapter's page order.
    Empty for anything that isn't a readable zip (rar/7z are stored but opaque)."""
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist()
                     if not n.endswith("/") and Path(n).suffix.lower() in IMAGE_EXTS]
    except (zipfile.BadZipFile, OSError):
        return []
    return sorted(names, key=_natkey)


def read_entry(path: Path, name: str) -> tuple[bytes, str]:
    with zipfile.ZipFile(path) as z:
        data = z.read(name)
    return data, CT_BY_EXT.get(Path(name).suffix.lower(), "application/octet-stream")


def thumbnail(data: bytes, width: int, cap: float | None = None) -> bytes | None:
    """A JPEG preview downscaled to `width` — full-size chapter pages are MBs
    each and would choke a 54-tile grid. `cap` crops very TALL pages (webtoon
    strips) to `width × cap` FROM THE TOP so previews keep sane proportions —
    the reader itself never uses it. None when the format can't be decoded
    (the caller falls back to the original bytes)."""
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            img = img.convert("RGB")
            if cap and img.height > img.width * cap:
                img = img.crop((0, 0, img.width, int(img.width * cap)))
            # LANCZOS: big downscales (covers, pages) stay crisp — the browser's
            # fast-path resampling of full-size originals is what looks pixelated
            img.thumbnail((width, int(width * (cap or 4))), Image.Resampling.LANCZOS)
            out = BytesIO()
            img.save(out, "JPEG", quality=82)
            return out.getvalue()
    except Exception:  # noqa: BLE001 — any decode failure means "serve the original"
        return None


def junk_entry_names(path: Path) -> list[str]:
    """Non-image entry names, without reading their bytes — used to decide
    whether an archive with no pages left still holds something worth keeping."""
    if not path.is_file() or not zipfile.is_zipfile(path):
        return []
    try:
        with zipfile.ZipFile(path) as z:
            return [n for n in z.namelist()
                    if not n.endswith("/") and Path(n).suffix.lower() not in IMAGE_EXTS]
    except (zipfile.BadZipFile, OSError, RuntimeError):
        return []


def _junk_entries(path: Path) -> list[tuple[str, bytes]]:
    """Non-image entries (ComicInfo.xml, notes, …) — every rewrite carries them
    over unchanged; editing pages must never silently drop a translator's files."""
    if not path.is_file():
        return []
    out: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if not info.filename.endswith("/") and Path(info.filename).suffix.lower() not in IMAGE_EXTS:
                out.append((info.filename, z.read(info.filename)))
    return out


def remove_entries(path: Path, names: set[str]) -> int:
    """Rewrite the archive without `names` (atomic replace); returns the number
    of image pages left."""
    _require_editable(path)
    tmp = tmp_path(path)
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as dst:
        for info in src.infolist():
            if info.filename in names:
                continue
            dst.writestr(info, src.read(info.filename))
    replace_atomically(tmp, path)
    return len(image_entries(path))


def _norm_ext(ext: str) -> str:
    e = (ext or "").lower().lstrip(".")
    return e if f".{e}" in IMAGE_EXTS else "jpg"


# formats that get converted to a universal one on the way into an archive
_CONVERT_EXTS = {".webp", ".avif"}


def normalize_page(data: bytes, ext: str) -> tuple[bytes, str]:
    """WebP/AVIF pages convert to a standard format as they enter an archive
    (jpg; png when the image carries alpha) — stored chapters stay readable by
    any external reader. Anything undecodable passes through unchanged."""
    e = f".{(ext or '').lower().lstrip('.')}"
    if e not in _CONVERT_EXTS:
        return data, ext
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
            out = BytesIO()
            if alpha:
                img.convert("RGBA").save(out, "PNG", optimize=True)
                return out.getvalue(), ".png"
            img.convert("RGB").save(out, "JPEG", quality=92)
            return out.getvalue(), ".jpg"
    except Exception:  # noqa: BLE001 — undecodable bytes are stored as-is
        return data, ext


def _write_renumbered(path: Path, pages: list[tuple[bytes, str]], junk: list[tuple[str, bytes]]) -> None:
    """Atomically rewrite the archive: pages as sequential zero-padded names
    (001.jpg …) with WebP/AVIF converted to a standard format, non-image
    entries carried over as-is."""
    width = max(3, len(str(len(pages))))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tmp_path(path)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for i, (data, ext) in enumerate(pages, start=1):
            data, ext = normalize_page(data, ext)
            z.writestr(f"{i:0{width}d}.{_norm_ext(ext)}", data)
        for name, data in junk:
            z.writestr(name, data)
    replace_atomically(tmp, path)


_SEQ_NAME = re.compile(r"^(\d+)\.([A-Za-z0-9]+)$")


def _canonical_width(names: list[str]) -> int:
    """The zero-pad width if `names` is ALREADY this module's own numbering
    (001.jpg, 002.png, … in order), else 0."""
    if not names:
        return 0
    m = _SEQ_NAME.match(names[0])
    if not m:
        return 0
    width = len(m.group(1))
    for i, name in enumerate(names, start=1):
        m = _SEQ_NAME.match(name)
        if not m or len(m.group(1)) != width or int(m.group(1)) != i:
            return 0
    return width


def renumber_and_append(path: Path, extra: list[tuple[bytes, str]]) -> int:
    """THE way pages are added: the archive ends up with sequential zero-padded
    names (001.jpg …) in the current order, with `extra` (bytes, ext) appended —
    so 'appended' always MEANS last, whatever naming scheme the source site
    used. Creates the archive when missing. Returns the final page count.

    When the archive is already in that canonical form the existing pages are
    NOT decoded and re-written: the file is copied and the new pages appended to
    the copy, which is then swapped in. Same atomicity, but capturing page 300
    of a chapter no longer costs reading 299 pages into memory first."""
    _require_editable(path)
    names = image_entries(path) if path.is_file() else []
    width = _canonical_width(names)
    # a wider page count (999 → 1000) needs the full renumber to stay sorted
    if width and len(names) + len(extra) < 10 ** width:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = tmp_path(path)
        # copyfile, NOT copy2: copy2 carries the source's timestamps over, and
        # an archive that keeps its old mtime after an edit is indistinguishable
        # from one that never changed
        shutil.copyfile(path, tmp)
        try:
            with zipfile.ZipFile(tmp, "a", zipfile.ZIP_STORED) as z:
                for i, (data, ext) in enumerate(extra, start=len(names) + 1):
                    data, ext = normalize_page(data, ext)
                    z.writestr(f"{i:0{width}d}.{_norm_ext(ext)}", data)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        replace_atomically(tmp, path)
        return len(names) + len(extra)
    existing: list[tuple[bytes, str]] = []
    for name in names:
        data, _ = read_entry(path, name)
        existing.append((data, Path(name).suffix))
    pages = [*existing, *extra]
    _write_renumbered(path, pages, _junk_entries(path))
    return len(pages)


def reorder_entries(path: Path, order: list[int]) -> int:
    """Rewrite the archive with its pages permuted into `order` (indices into
    the current natural page order), renumbered sequentially. `order` must be a
    full permutation — a manual arrangement is explicit, never partial."""
    _require_editable(path)
    names = image_entries(path)
    if sorted(order) != list(range(len(names))):
        raise ValueError("order must be a permutation of the current page indices")
    pages = [(read_entry(path, names[i])[0], Path(names[i]).suffix) for i in order]
    _write_renumbered(path, pages, _junk_entries(path))
    return len(pages)


def move_images(src: Path, dst: Path, names: set[str]) -> tuple[int, int]:
    """Move pages from src to dst (dst created/renumbered via
    renumber_and_append). Dst is written FIRST, then src drops the pages — a
    crash in between duplicates pages instead of losing them.
    Returns (pages left in src, pages now in dst)."""
    moved = [(read_entry(src, n)[0], Path(n).suffix) for n in image_entries(src) if n in names]
    dst_count = renumber_and_append(dst, moved)
    src_left = remove_entries(src, names)
    return src_left, dst_count
