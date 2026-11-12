"""Previews: the downscaled covers and pages a grid shows.

A preview is cached in the vault (`.longbox/cache/thumbs`) the first time it is
made, and making one reads its original — a whole cover, a page out of a zip.
On a spinning disk, a grid asking for forty at once turned forty reads into
forty seeks at once, and every tile waited on all of them. So:

- making a preview is ONE AT A TIME per library (`making`); a request whose
  preview is cached never waits for it, and the window's requests go before
  the background's;
- a tile's preview is made as soon as there is something to show: every write
  of a title offers it here (`Library._index`), and one worker makes them in
  order — a large import leaves nothing for the first look at the grid to pay;
- what was stored before that is made once per vault, after the launch has
  verified the vault (`backfill`).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .service import Library

log = logging.getLogger("longbox.library")

# The width a library grid tile asks for (frontend CoverArt.vue's default `w`):
# the preview made ahead is the one the grid shows first. Changing it means
# bumping vault.PREVIEWS_VERSION, or older content keeps only the old width.
TILE_W = 420

# What a tile shows first — its own cover, the first page of an entry, or the
# frame the window cut for an episode — the entry it comes from, and the version
# it is cached under: with that, whether it is made needs no disk.
Source = tuple[Literal["cover", "page", "poster"], str, str]


class Previews:
    def __init__(self, lib: Library, closing: threading.Event) -> None:
        self._lib = lib
        self._closing = closing
        self._make = threading.Lock()
        self._lock = threading.Lock()
        self._asking = 0  # requests waiting to make one: the background steps aside
        self._local = threading.local()
        self._queue: OrderedDict[str, Source] = OrderedDict()
        self._thread: threading.Thread | None = None

    @contextmanager
    def making(self) -> Iterator[None]:
        """Held while one preview is read and made."""
        if getattr(self._local, "background", False):
            while self._asking and not self._closing.is_set():
                time.sleep(0.05)
            with self._make:
                yield
            return
        with self._lock:
            self._asking += 1
        try:
            self._make.acquire()
        finally:
            with self._lock:
                self._asking -= 1
        try:
            yield
        finally:
            self._make.release()

    @contextmanager
    def _background(self) -> Iterator[None]:
        self._local.background = True
        try:
            yield
        finally:
            self._local.background = False

    def offer(self, title_id: str, source: Source | None) -> None:
        """Make this title's tile preview, unless it is made already."""
        if source is None:
            return
        with self._lock:
            self._queue.pop(title_id, None)  # the newest write knows what the tile shows
            self._queue[title_id] = source
        self._start()

    def _start(self) -> None:
        with self._lock:
            if self._thread is None and self._queue and not self._closing.is_set():
                self._thread = threading.Thread(target=self._loop, name="lb-previews", daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while True:
            # the worker leaves under the same lock `offer` starts one under, so
            # nothing offered as it goes is left without one
            with self._lock:
                if not self._queue or self._closing.is_set():
                    self._thread = None
                    return
                title_id, source = self._queue.popitem(last=False)
            self._made(title_id, source)

    def _made(self, title_id: str, source: Source, made: set[str] | None = None) -> None:
        with self._background():
            try:
                self._lib.tile_preview(title_id, source, made)
            except Exception:  # noqa: BLE001 — one unreadable title must not stop the rest
                log.exception("the tile preview of %s could not be made", title_id)

    def backfill(self) -> None:
        """Every title's tile preview, once per vault: what was stored before
        writes made them. What is made already costs nothing — the cache is
        listed once and each tile's key comes from the index — so only the
        missing ones touch the disk. Marked done only when it went through."""
        made = self._lib.thumb_names()
        for title_id, source in self._lib.tile_sources():
            if self._closing.is_set():
                return
            self._made(title_id, source, made)
        if not self._closing.is_set():
            self._lib.vault.mark_previews()

    def close(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)
