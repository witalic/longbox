"""Stored episodes: the index of an mp4 in front of its media, and what the app
records about each file.

An mp4 whose index (`moov`) sits behind its media plays only once the player
has fetched the file's tail, and every far seek pays for it again. Moving the
index forward writes the whole file once more (media.faststart_copy).

- A download does it at ingest, while the file is arriving anyway.
- An import does not: on a network vault it would double what an import
  writes, and a move onto the vault's own drive, which writes nothing at all,
  would become a copy. The episode is stored as it is, its sidecar marked
  (`LATER`), and `fill` rewrites what is marked — after an import, and on each
  launch once the library has verified itself — one episode at a time:
  - the rearranged copy is written WITHOUT the title lock, so the title stays
    editable and playable for the minutes a big episode takes on a share; it
    lands under the lock, and only over the very file it was made from;
  - its sha256 is taken as it is written: the digest stays true, unread again;
  - a close, or a write that needs the file (`release`), stops the copy at its
    next chunk and keeps the mark — a close that waited would leave the session
    unsettled, and the next launch would verify the whole vault;
  - an episode the player holds open cannot be replaced, and keeps its mark
    for the next pass; one the rewrite refuses, or fails on, plays as it is.
- What was stored before the app did either gets a one-time pass (`refresh`).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from . import media

if TYPE_CHECKING:
    from .service import Library

log = logging.getLogger("longbox.library")

LATER = "faststartLater"  # the sidecar mark: an index an import left behind the media


def stale(side: dict) -> bool:
    """Whether reading an episode again could change what its sidecar says. The
    one-time pass opens only these, so a bump of FASTSTART_VERSION that teaches
    the probe something new widens this to the episodes it is new for."""
    ext = f".{side.get('container') or ''}"
    return ("container" not in side or "codec" not in side
            or (ext in media.MATROSKA_EXTS and "audio" not in side)  # by its name before version 2
            or (ext in media.FASTSTART_EXTS and not side.get("faststart"))
            or (side.get("codec") == "hevc" and "profile" not in side)  # version 3
            or (ext in media.MATROSKA_EXTS and side.get("codec") == "h264" and "profile" not in side)
            or (ext in media.MATROSKA_EXTS and side.get("audio") == "pcm" and not side.get("playable")))


def _same(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_ino, a.st_size, a.st_mtime_ns) == (b.st_ino, b.st_size, b.st_mtime_ns)


class Faststart:
    def __init__(self, lib: Library, closing: threading.Event) -> None:
        self._lib = lib
        self._closing = closing
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._again = False
        # the episode being copied, and what stops its copy
        self._copying: tuple[str, str] | None = None
        self._stop = threading.Event()
        self._idle = threading.Event()  # no copy is reading an episode
        self._idle.set()

    def refresh(self) -> int:
        """Bring stored episodes up to what the app records about them, once per
        vault. Anything arriving now gets it at ingest."""
        lib = self._lib
        # the index knows every episode already; an empty one (a first open, a
        # dropped cache) knows nothing, and the vault is walked instead
        known = lib.index.episodes() if lib.index.count() else None
        changed, touched = lib.vault.refresh_stored_episodes(known, stop=self._closing)
        if not self._closing.is_set():
            lib.vault.mark_faststart()
        lib.reindex(touched)
        return changed

    def fill(self) -> None:
        """Rewrite every episode the index holds marked."""
        with self._lock:
            self._again = True
        self._start()

    def _start(self) -> None:
        with self._lock:
            if self._thread is None and not self._closing.is_set():
                self._thread = threading.Thread(target=self._loop, name="lb-faststart", daemon=True)
                self._thread.start()

    def release(self, title_id: str, chapter_id: str | None = None) -> None:
        """Stop copying an episode's file — or any of a title's — before it is
        deleted, replaced or moved: Windows refuses that to a file open for
        reading. Waits for the copy only, never for its landing: that takes the
        title lock the caller holds, and finds the file changed anyway."""
        from .vault import safe_id  # the vault builds on this module

        with self._lock:
            held = self._copying
            mine = held is not None and safe_id(held[0]) == safe_id(title_id) \
                and (chapter_id is None or held[1] == safe_id(chapter_id))
            if mine:
                self._stop.set()
        if mine:
            self._idle.wait(timeout=15)

    def close(self) -> bool:
        """Stop the worker; say whether it has (it writes sidecars and the index)."""
        with self._lock:  # never between the worker's check of closing and its next copy
            self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)
        return thread is None or not thread.is_alive()

    def _loop(self) -> None:
        while True:
            # the worker leaves under the same lock `fill` starts one under, so
            # nothing marked as it goes is left without one
            with self._lock:
                if not self._again or self._closing.is_set():
                    self._thread = None
                    return
                self._again = False
            marked = [(title_id, stem) for title_id, sides in self._lib.index.episodes().items()
                      for stem, side in sides.items() if side.get(LATER)]
            if marked:
                log.info("moving the index to the front: %d episode(s)", len(marked))
            for title_id, stem in marked:
                if self._closing.is_set():
                    break
                try:
                    self._rewrite(title_id, stem)
                except Exception:  # noqa: BLE001 — one episode must not stop the rest
                    log.exception("the index of %s/%s was not moved", title_id, stem)

    def _rewrite(self, title_id: str, stem: str) -> None:
        path = self._lib.vault.chapter_media_path(title_id, stem)
        if path is None:
            return
        with self._lock:  # copying from before the file is first looked at: `release` waits
            if self._closing.is_set():
                return
            self._copying, self._stop = (title_id, stem), threading.Event()
            self._idle.clear()
        was = None
        started = time.perf_counter()
        try:
            was = path.stat()
            made = media.faststart_copy(path, self._stop)
        except InterruptedError:
            log.info("the index of %s/%s — stopped after %.1fs; it stays marked", title_id, stem,
                     time.perf_counter() - started)
            return  # closing, or the file is wanted: the mark stays
        except OSError as err:
            log.warning("the index of %s/%s was not moved: %s", title_id, stem, err)
            made = None  # it plays as it is
        finally:
            with self._lock:
                self._copying = None
                self._idle.set()
        if was is not None:
            ended = self._land(title_id, stem, path, was, made)
            log.info("the index of %s/%s (%.0f MB) — %s after %.1fs", title_id, stem, was.st_size / 1e6,
                     ended, time.perf_counter() - started)

    def _land(self, title_id: str, stem: str, path: Path, was: os.stat_result,
              made: tuple[Path, str] | None) -> str:
        """Says how it ended, for the log."""
        vault = self._lib.vault
        with vault.title_lock(title_id):
            side = vault.chapter_sidecar(title_id, stem)
            try:
                same = side is not None and bool(side.get(LATER)) and _same(was, path.stat())
            except OSError:
                same = False
            if not same:  # replaced, removed or rewritten meanwhile: what was copied is not it
                if made is not None:
                    made[0].unlink(missing_ok=True)
                return "dropped: the episode changed meanwhile"
            if made is not None:
                try:
                    media.replace_atomically(made[0], path)
                except media.MediaInUseError:
                    return "left: the player holds it"  # the mark stays for the next pass
                # new bytes: a new revision (a repeated one serves a stale range
                # back), and the digest they were written under
                side = {**side, **media.probe_video(path), "sha256": made[1], "content": made[1],
                        "rev": int(side.get("rev") or 0) + 1}
            side.pop(LATER, None)
            vault.write_chapter_sidecar(title_id, stem, side)
            self._lib.reindex([title_id])
        return "moved" if made is not None else "left as it is: it cannot be moved"
