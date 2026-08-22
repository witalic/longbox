"""Versions and cache keys for everything the app serves out of the vault.

Pages and covers are cached twice — by the browser (the version rides in the
URL) and on disk (downscaled previews) — so a version that survives an edit
shows the user a file they already deleted. That is not a hypothetical: it has
happened three times, each time because a NEW cached thing invented its own
scheme. Page count said nothing (delete two pages, add two), and a file
timestamp lies (a copy carries it over, a share rounds it, two writes share a
tick).

So there is one rule here, and `cache_key` refuses to build a key without a
version — a future cached artifact (a video poster, an extracted subtitle
track, another preview size) cannot quietly opt out of it.
"""
from __future__ import annotations


class UnversionedCacheKey(ValueError):
    """Raised when something asks to be cached without saying which version of
    it is being cached."""


def chapter_version(sidecar: dict | None) -> str:
    """What a chapter's stored archive is cached under: the revision counter
    every page operation bumps, plus the size and page count. The counter alone
    is not enough — it restarts at 1 for a chapter written before it existed,
    which can land on the version that chapter is already cached under."""
    if not sidecar:
        return ""
    return (f"{int(sidecar.get('rev') or 0)}"
            f".{sidecar.get('size', 0)}.{sidecar.get('pages', 0)}")


def cover_version(name: str, mtime: int, size: int) -> str:
    """What a cover file is cached under. A cover has no revision counter, so
    the write itself guarantees a fresh stamp (`Vault.write_cover` moves the
    timestamp when a replacement would otherwise reuse this exact version)."""
    return f"{mtime:x}.{size}.{name.rsplit('.', 1)[-1]}" if name else ""


def cache_key(kind: str, version: str, *parts: object) -> str:
    """A disk-cache key. `version` MUST identify the exact bytes being cached:
    an empty one means the caller has nothing that changes when the file does,
    and a cache under such a key can only ever serve stale content."""
    if not version:
        raise UnversionedCacheKey(f"{kind}: refusing to cache without a version")
    tail = "-".join(str(p) for p in parts if p != "" and p is not None)
    return f"{kind}-{version}{'-' + tail if tail else ''}"
