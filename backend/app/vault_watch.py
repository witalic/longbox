"""Whether the vault still answers (design/vault-home.md, "When the vault drops").

A vault on a network drive can go while the app is open. Nothing then may read
from it — every read waits out the network's own timeout — and nothing may be
written anywhere: an emptied mount point or another share under the same drive
letter would take the writes. So one thread keeps asking, and its answer gates
the whole API.

States: `ok`; `lost` — two answers in a row failed, every /api request is refused
and the work on the vault has stopped; `back` — two answers in a row were good
again. `back` is final here: the shell restarts the app into the vault, because
the handles this process holds to its index cannot be trusted after a drop.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from .library.service import Library
from .passes import stop_event

log = logging.getLogger("longbox")

INTERVAL = 5.0   # seconds between two questions
DEADLINE = 5.0   # an answer slower than this counts as none
IN_A_ROW = 2     # answers that agree before the state changes
# A failed answer is asked again sooner than the next tick, but not at once: a
# share that is reconnecting refuses twice in the same millisecond, and one blip
# must not take the window away.
CONFIRM_AFTER = 1.5


class VaultWatch:
    def __init__(self, lib: Library, *, interval: float = INTERVAL, deadline: float = DEADLINE) -> None:
        self.lib = lib
        self.interval = interval
        self.deadline = deadline
        self.state = "ok"
        self.since = ""
        self._run = 0  # answers in a row that disagree with the state
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._probe: threading.Thread | None = None
        self._answer: dict[str, bool] = {}
        self._thread = threading.Thread(target=self._loop, name="vault-watch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()

    def poke(self) -> None:
        """Something failed on the vault: ask now, not at the next tick."""
        self._wake.set()

    def ask(self) -> bool:
        """One question, with a deadline. A read stuck on a dead drive keeps its
        thread until the network gives up; the next question waits on that one
        instead of piling another thread on top."""
        if self._probe is None or not self._probe.is_alive():
            answer: dict[str, bool] = {}
            self._answer = answer

            def probe() -> None:
                answer["ok"] = self.lib.vault.still_here()

            self._probe = threading.Thread(target=probe, name="vault-probe", daemon=True)
            self._probe.start()
        self._probe.join(self.deadline)
        return self._answer.get("ok", False)

    def heard(self, ok: bool) -> None:
        """One answer. The state moves only on IN_A_ROW answers that agree, so a
        single slow read does not take the window away."""
        if self.state == "back":
            return
        disagrees = (not ok) if self.state == "ok" else ok
        self._run = self._run + 1 if disagrees else 0
        if self._run < IN_A_ROW:
            return
        self._run = 0
        if self.state == "ok":
            self.state = "lost"
            self.since = datetime.now(timezone.utc).isoformat(timespec="seconds")
            log.warning("the vault at %s stopped answering — the app stops using it", self.lib.root)
            # off this thread: stopping ffmpeg and the passes may take a moment,
            # and the watch must keep asking meanwhile
            threading.Thread(target=self._stop_work, name="vault-lost", daemon=True).start()
        else:
            self.state = "back"
            log.warning("the vault at %s answers again", self.lib.root)

    def _stop_work(self) -> None:
        stop_event().set()  # an import, a check, any pass, at its next step
        self.lib.lose()

    def _loop(self) -> None:
        while not self._stop.is_set():
            ok = self.ask()
            self.heard(ok)
            self._wake.wait(CONFIRM_AFTER if self._run else self.interval)
            self._wake.clear()
