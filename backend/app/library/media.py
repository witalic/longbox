"""Chapter media helpers. VAULT INVARIANT: every stored chapter archive is a
plain zip — whatever the site served (cbz IS zip; rar/7z get repacked on the
way in), so every page operation works on every stored chapter. A sidecar
keeps the download provenance. Pages are the archive's image entries in
natural order; page edits rewrite the archive atomically.
"""
from __future__ import annotations

import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp"}
CT_BY_EXT = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif", ".bmp": "image/bmp",
}


class UnsupportedArchiveError(ValueError):
    """An archive we can't read (unknown format, corrupt file, or no RAR
    backend available) — ingest rejects it and page edits refuse to touch a
    stored leftover rather than overwrite it with a fresh zip."""


def _tmp(path: Path) -> Path:
    # unique per call: a fixed ".tmp" name would let two concurrent rewrites of
    # one archive interleave into a corrupted file
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
    tmp = _tmp(dst)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for name, data in entries:
            if Path(name).suffix.lower() in _CONVERT_EXTS:
                data, ext = normalize_page(data, Path(name).suffix)
                if ext != Path(name).suffix:
                    name = Path(name).with_suffix(ext).as_posix()  # zip names use forward slashes
            z.writestr(name, data)
    os.replace(tmp, dst)
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
    tmp = _tmp(path)
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as dst:
        for info in src.infolist():
            if info.filename in names:
                continue
            dst.writestr(info, src.read(info.filename))
    os.replace(tmp, path)
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
    tmp = _tmp(path)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for i, (data, ext) in enumerate(pages, start=1):
            data, ext = normalize_page(data, ext)
            z.writestr(f"{i:0{width}d}.{_norm_ext(ext)}", data)
        for name, data in junk:
            z.writestr(name, data)
    os.replace(tmp, path)


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
        tmp = _tmp(path)
        shutil.copy2(path, tmp)
        try:
            with zipfile.ZipFile(tmp, "a", zipfile.ZIP_STORED) as z:
                for i, (data, ext) in enumerate(extra, start=len(names) + 1):
                    data, ext = normalize_page(data, ext)
                    z.writestr(f"{i:0{width}d}.{_norm_ext(ext)}", data)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, path)
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
