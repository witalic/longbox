"""Download a chosen cover image into the vault. Bounded (size + timeout) and
image-only, sending the source page's origin as Referer so hotlink-protected hosts
(e.g. MangaDex) actually serve the file instead of a placeholder."""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("longbox")

_MAX_BYTES = 8 * 1024 * 1024  # 8 MB — cover art, not a chapter
_EXT_BY_TYPE = {
    "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
    "image/webp": "webp", "image/gif": "gif", "image/avif": "avif",
}


def is_remote(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def fetch_cover(url: str, referer: str = "") -> tuple[bytes, str] | None:
    """Return (bytes, file-extension) for an image URL, or None if it can't be fetched
    as an image (wrong content-type, too big, network error, non-remote URL)."""
    if not is_remote(url):
        return None
    headers = {"User-Agent": "longbox/1.0 (+local)", "Accept": "image/*,*/*"}
    ref = urlsplit(referer)
    if ref.scheme and ref.netloc:
        headers["Referer"] = f"{ref.scheme}://{ref.netloc}/"
    try:
        with httpx.Client(follow_redirects=True, timeout=15.0) as client:
            with client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if not ctype.startswith("image/"):
                    log.warning("cover url is not an image (%s): %s", ctype or "?", url)
                    return None
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf += chunk
                    if len(buf) > _MAX_BYTES:
                        log.warning("cover exceeds %d bytes, skipping: %s", _MAX_BYTES, url)
                        return None
    except httpx.HTTPError as exc:
        log.warning("cover download failed (%s): %s", exc, url)
        return None
    ext = _EXT_BY_TYPE.get(ctype)
    if ext is None:  # fall back to the URL suffix, else jpg
        tail = urlsplit(url).path.rsplit(".", 1)
        ext = tail[1].lower() if len(tail) == 2 and 1 <= len(tail[1]) <= 5 else "jpg"
    return bytes(buf), ext
