"""Download a chosen cover image into the vault. Bounded (size + timeout) and
image-only, sending the source page's origin as Referer so hotlink-protected hosts
(e.g. MangaDex) actually serve the file instead of a placeholder."""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlsplit

import httpx

log = logging.getLogger("longbox")

_MAX_BYTES = 8 * 1024 * 1024  # 8 MB — cover art, not a chapter
_MAX_REDIRECTS = 5
_EXT_BY_TYPE = {
    "image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
    "image/webp": "webp", "image/gif": "gif", "image/avif": "avif",
}


def is_remote(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def _client() -> httpx.Client:
    return httpx.Client(follow_redirects=False, timeout=15.0)


def on_public_internet(url: str) -> bool:
    """Whether every address the URL's host resolves to is a public one. A cover's
    address can come from a page, and the sidecar fetches it from this computer:
    it must never reach the computer itself or its network (loopback, private,
    link-local — a router's admin page included). Asked again at every redirect;
    a name that resolves differently between this answer and the connection is
    the gap left."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        found = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError, ValueError):
        return False
    addresses = set()
    for *_, sockaddr in found:
        ip = ipaddress.ip_address(str(sockaddr[0]).split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        addresses.add(ip)
    return bool(addresses) and all(ip.is_global for ip in addresses)


def fetch_cover(url: str, referer: str = "") -> tuple[bytes, str] | None:
    """Return (bytes, file-extension) for an image URL, or None if it can't be fetched
    as an image (wrong content-type, too big, network error, non-remote URL, an
    address off the public internet)."""
    if not is_remote(url):
        return None
    headers = {"User-Agent": "longbox/1.0 (+local)", "Accept": "image/*,*/*"}
    ref = urlsplit(referer)
    if ref.scheme and ref.netloc:
        headers["Referer"] = f"{ref.scheme}://{ref.netloc}/"
    try:
        with _client() as client:
            for _hop in range(_MAX_REDIRECTS + 1):
                if not on_public_internet(url):
                    log.warning("cover address is not on the public internet, refused: %s", url)
                    return None
                with client.stream("GET", url, headers=headers) as resp:
                    if resp.is_redirect:
                        url = urljoin(url, resp.headers.get("location", ""))
                        continue
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
                    break
            else:
                log.warning("cover address redirects more than %d times: %s", _MAX_REDIRECTS, url)
                return None
    except httpx.HTTPError as exc:
        log.warning("cover download failed (%s): %s", exc, url)
        return None
    ext = _EXT_BY_TYPE.get(ctype)
    if ext is None:  # fall back to the URL suffix, else jpg
        tail = urlsplit(url).path.rsplit(".", 1)
        ext = tail[1].lower() if len(tail) == 2 and 1 <= len(tail[1]) <= 5 else "jpg"
    return bytes(buf), ext
