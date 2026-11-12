"""Playable copies (design/playable-copies.md).

An episode the window cannot show as stored — a container it does not open, or
a picture it does not decode (HEVC beyond Main10 on most graphics) — gets a
COPY it can, made by ffmpeg (library/transcode.py) and kept in the vault beside
the original, which is never touched. Wherever a copy exists, the player is
served the copy.

Copies are made one at a time, in the background, without being asked: every
write of a title offers its episodes here (`Library._index`), a library that has
verified itself offers every episode the index knows, and a window that opens an
episode and cannot show it asks for one. A copy that cannot be made is
remembered in the episode's sidecar against the bytes it was tried on, so no
launch retries a hopeless file on its own — the human can.
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from . import media, transcode
from .models import ChapterRow, TitleDoc
from .vault import safe_id

if TYPE_CHECKING:
    from .service import Library

log = logging.getLogger("longbox.library")


def original_of(side: dict) -> str:
    """The episode bytes a copy (or a failure) was made from. The revision moves
    with every re-ingest and remux; size and digest catch a file swapped by hand."""
    return f"{int(side.get('rev') or 0)}.{side.get('size', 0)}.{side.get('sha256', '')}"


def needs_copy(side: dict) -> bool:
    failed = side.get("copyFailed")
    return (side.get("kind") == "video" and side.get("playable") is False and not side.get("copy")
            and not (isinstance(failed, dict) and failed.get("of") == original_of(side)))


@dataclass
class CopyJob:
    id: str
    title_id: str
    chapter_id: str
    title: str
    label: str
    of: str  # the episode bytes it is for (original_of)
    requested: bool = False  # asked for by a window or by hand, whatever the sidecar says
    state: str = "queued"  # queued | running | failed
    progress: float = 0.0
    error: str = ""

    def out(self) -> dict:
        return {"id": self.id, "titleId": self.title_id, "chapterId": self.chapter_id,
                "title": self.title, "label": self.label, "state": self.state,
                "progress": round(self.progress, 3), "error": self.error}


def _label(row: ChapterRow) -> str:
    return " · ".join(x for x in (row.num, row.lang, row.title) if x)


class CopyQueue:
    def __init__(self, lib: Library, closing: threading.Event) -> None:
        self._lib = lib
        self._closing = closing
        self._jobs: dict[str, CopyJob] = {}
        self._lock = threading.Lock()
        # the RUNNING conversion's stop: a cancel, a release, or the library closing
        self._stop = threading.Event()
        # set whenever no ffmpeg has an original open — what a release waits for
        self._idle = threading.Event()
        self._idle.set()
        self._running: CopyJob | None = None
        self._writing: Path | None = None
        self._thread: threading.Thread | None = None
        # While a one-time pass re-describes the vault, sidecars may still say what
        # an older build thought (every mkv "unplayable"): no copy starts until it
        # has finished, or files that play would be copied for nothing.
        self._held = False
        # cancelled this session — (title, chapter, bytes) — so the next write of
        # the title (a read mark, a resume point) does not queue it straight back
        self._declined: set[tuple[str, str, str]] = set()
        # copies that landed, numbered, so a window that polls now and then still
        # learns which titles changed while it was not looking
        self._landed: deque[tuple[int, str]] = deque(maxlen=64)
        self._seq = 0

    @property
    def available(self) -> bool:
        return transcode.ffmpeg() is not None

    def state(self) -> dict:
        with self._lock:
            return {"available": self.available, "jobs": [j.out() for j in self._jobs.values()],
                    "landed": [{"seq": n, "titleId": t} for n, t in self._landed]}

    def jobs(self) -> list[dict]:
        return self.state()["jobs"]

    def offer(self, title_id: str, doc: TitleDoc, sides: dict[str, dict]) -> None:
        """Queue every episode of this title that needs a copy and is not already
        on its way. Called inside the title lock on every write, so it only ever
        reads what it is handed."""
        if not any(needs_copy(s) for s in sides.values()) or not self.available:
            return
        for row in doc.chapters:
            side = sides.get(safe_id(row.id))
            if side is not None and needs_copy(side):
                self._add(title_id, row, doc.meta.title, original_of(side), requested=False)

    def fill(self) -> None:
        """Offer every episode the index knows: once the library has verified
        itself, and once a one-time pass has re-described what it held."""
        for title_id, sides in self._lib.index.episodes().items():
            if any(needs_copy(s) for s in sides.values()):
                got = self._lib.index.get(title_id)
                if got is not None:
                    self.offer(title_id, got[0], sides)

    def request(self, title_id: str, chapter_id: str) -> CopyJob | Literal["landed"] | None:
        """A window opened this episode and could not show it, or the human asked:
        whatever the file says about itself, it needs a copy. "landed" when one
        is already there; None when it is not an episode (or ffmpeg is missing)."""
        if not self.available:
            return None
        got = self._lib.index.get(title_id)
        side = self._lib.vault.chapter_sidecar(title_id, chapter_id) or {}
        row = next((c for c in got[0].chapters if c.id == chapter_id), None) if got else None
        if got is None or row is None or side.get("kind") != "video":
            return None
        if side.get("copy") and self._lib.vault.chapter_copy_path(title_id, chapter_id).is_file():
            return "landed"
        of = original_of(side)
        with self._lock:
            self._declined.discard((title_id, chapter_id, of))
        job = self._add(title_id, row, got[0].meta.title, of, requested=True)
        if job is not None and job.state == "failed":
            self.retry(job.id)
        return job

    def retry(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state != "failed":
                return False
            job.state, job.progress, job.error = "queued", 0.0, ""
            job.requested = True  # tried again by hand: past a remembered failure
        self._start()
        return True

    def drop(self, job_id: str) -> bool:
        """Cancel a waiting or running copy, or dismiss a failed one."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            self._declined.add((job.title_id, job.chapter_id, job.of))
            if job is self._running:
                self._stop.set()  # the worker removes it as the process dies
            else:
                del self._jobs[job_id]
        return True

    def release(self, title_id: str, chapter_id: str | None = None) -> None:
        """Let go of an episode's file — or every one of a title's — before it is
        deleted, replaced or moved. On Windows a running ffmpeg holds the
        original without delete-sharing, so the change would fail half-way (a
        title delete stopping part-way, a re-download leaving its .tmp). Waits
        for the process only, never for a landing: that takes the title lock the
        caller holds, and would find the bytes changed anyway."""
        def mine(job: CopyJob) -> bool:
            return (safe_id(job.title_id) == safe_id(title_id)
                    and (chapter_id is None or safe_id(job.chapter_id) == safe_id(chapter_id)))

        with self._lock:
            for job in [j for j in self._jobs.values() if mine(j) and j is not self._running]:
                del self._jobs[job.id]
            running = self._running is not None and mine(self._running)
            if running:
                self._stop.set()
        if running:
            self._idle.wait(timeout=15)

    def hold(self) -> None:
        with self._lock:
            self._held = True

    def resume(self) -> None:
        with self._lock:
            self._held = False
        self.fill()
        self._start()

    def writing(self, path: Path) -> bool:
        """Whether this is the copy being written right now — not a leftover."""
        return self._writing is not None and path == self._writing

    def close(self) -> bool:
        """Stop the worker; say whether it has (it writes sidecars and the index)."""
        with self._lock:  # never between the worker's check of closing and its clearing of stop
            self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)
        return thread is None or not thread.is_alive()

    def _add(self, title_id: str, row: ChapterRow, title: str, of: str, *,
             requested: bool) -> CopyJob | None:
        with self._lock:
            for job in self._jobs.values():
                if job.title_id == title_id and job.chapter_id == row.id:
                    if job.of != of and job is not self._running:
                        # new bytes behind the entry: whatever happened to the old
                        # ones (a failure, a place in line) says nothing about these
                        job.of, job.state, job.progress, job.error = of, "queued", 0.0, ""
                    job.requested = job.requested or requested
                    break
            else:
                if not requested and (title_id, row.id, of) in self._declined:
                    return None
                job = CopyJob(uuid.uuid4().hex[:12], title_id, row.id, title, _label(row), of,
                              requested=requested)
                self._jobs[job.id] = job
        self._start()
        return job

    def _start(self) -> None:
        with self._lock:
            if self._thread is None and not self._held and not self._closing.is_set():
                self._thread = threading.Thread(target=self._loop, name="lb-copies", daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        try:
            while True:
                with self._lock:
                    job = next((j for j in self._jobs.values() if j.state == "queued"), None)
                    if job is None or self._held or self._closing.is_set():
                        return
                    self._running = job
                    job.state = "running"
                    self._stop.clear()
                try:
                    self.work(job)
                except Exception as e:  # noqa: BLE001 — one bad episode must not stop the queue
                    log.exception("playable copy of %s/%s failed", job.title_id, job.chapter_id)
                    job.state, job.error = "failed", str(e)[:400] or type(e).__name__
                finally:
                    with self._lock:
                        self._running = None
        finally:
            with self._lock:
                self._thread = None  # the next job starts a new worker

    def work(self, job: CopyJob) -> None:
        """Make one copy. The conversion runs with no lock held — it takes
        minutes — and lands only if the episode is still the bytes it read."""
        lib = self._lib
        side = lib.vault.chapter_sidecar(job.title_id, job.chapter_id) or {}
        src = lib.vault.chapter_media_path(job.title_id, job.chapter_id)
        # what the file is NOW decides: the job may have been queued off a sidecar
        # a pass has since corrected, or a copy may have landed meanwhile
        if src is None or side.get("kind") != "video" or not (needs_copy(side) or job.requested):
            self._forget(job)
            return
        of = original_of(side)
        final = lib.vault.chapter_copy_path(job.title_id, job.chapter_id)
        tmp = media.tmp_path(final)

        def moved(fraction: float) -> None:
            job.progress = fraction

        self._idle.clear()
        try:
            # what a conversion cut off by the app ending left behind: its ffmpeg
            # died with the app, and nothing else will ever finish that file
            for left in final.parent.glob(f"{final.name}.*.tmp"):
                try:
                    left.unlink()
                except OSError:
                    pass  # still held open — not ours to take
            self._writing = tmp
            held = transcode.convert(src, tmp, progress=moved, stop=self._stop)
        except transcode.Cancelled:
            tmp.unlink(missing_ok=True)
            self._forget(job)
            return
        except transcode.ConvertError as e:
            tmp.unlink(missing_ok=True)
            job.state, job.error = "failed", str(e)[:400] or "ffmpeg failed"
            lib.record_copy_failed(job.title_id, job.chapter_id, of, job.error)
            return
        except OSError as e:  # the vault, not the file: nothing to remember against it
            tmp.unlink(missing_ok=True)
            job.state, job.error = "failed", str(e)[:400] or type(e).__name__
            return
        finally:
            self._writing = None
            self._idle.set()
        try:
            landed = lib.commit_copy(job.title_id, job.chapter_id, tmp, of, held)
        except OSError as e:  # a copy held open by a player, a vault gone away
            tmp.unlink(missing_ok=True)
            job.state, job.error = "failed", str(e)[:400] or type(e).__name__
            return
        self._forget(job)
        if landed:
            with self._lock:
                self._seq += 1
                self._landed.append((self._seq, job.title_id))
            return
        # the episode changed under the conversion: the copy is of bytes that are
        # gone, and what replaced them was offered while this job stood in its place
        tmp.unlink(missing_ok=True)
        got = lib.index.get(job.title_id)
        if got is not None:
            self.offer(job.title_id, got[0], got[1])

    def _forget(self, job: CopyJob) -> None:
        with self._lock:
            self._jobs.pop(job.id, None)
