"""The app's own documents in the vault (design/vault-home.md).

Everything the app keeps lives in the vault it serves; the app's folder on this
computer holds only where the vaults are. Two documents carry what used to sit
in that folder:

- `settings.json` — what the human set: the browser's homepage, and the
  window's preferences (theme, density, keys, reader, layout — every `lb.*` key
  the window keeps, as the string it keeps it as).
- `.longbox/state.json` — the app's working state: transfers a quit cut short,
  imports put down to be carried on, the reports of the last imports.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from .vault import APP_DIR, UnreadableDocument, Vault

SETTINGS_DOC = "settings.json"
SETTINGS_SCHEMA = 1
STATE_DOC = f"{APP_DIR}/state.json"
STATE_SCHEMA = 1

DEFAULT_HOMEPAGE = "https://www.google.com"

# what the window's preferences may hold: its own keys, as strings
WINDOW_PREFIX = "lb."


class AppSettings:
    def __init__(self, vault: Vault) -> None:
        self.vault = vault

    def _doc(self) -> dict:
        doc = self.vault.read_doc(SETTINGS_DOC)
        got = doc.get("schema", SETTINGS_SCHEMA)
        if not isinstance(got, int) or got > SETTINGS_SCHEMA:
            raise UnreadableDocument(f"{SETTINGS_DOC} was written by a newer longbox — this one does not use it")
        return doc

    def homepage(self) -> str:
        page = self._doc().get("homepage")
        return page if isinstance(page, str) and page else DEFAULT_HOMEPAGE

    def set_homepage(self, url: str) -> str:
        with self.vault.doc_transaction(SETTINGS_DOC, schema=SETTINGS_SCHEMA) as doc:
            doc["homepage"] = url.strip() or DEFAULT_HOMEPAGE
            return doc["homepage"]

    def window(self) -> dict[str, str]:
        raw = self._doc().get("window")
        if not isinstance(raw, dict):
            return {}
        return {k: v for k, v in raw.items()
                if isinstance(k, str) and k.startswith(WINDOW_PREFIX) and isinstance(v, str)}

    def change_window(self, put: dict[str, str], drop: list[str]) -> None:
        with self.vault.doc_transaction(SETTINGS_DOC, schema=SETTINGS_SCHEMA) as doc:
            window = doc.get("window") if isinstance(doc.get("window"), dict) else {}
            for k in drop:
                window.pop(k, None)
            window.update({k: v for k, v in put.items() if k.startswith(WINDOW_PREFIX)})
            doc["window"] = window

    def adopt(self, *, homepage: str = "", window: dict[str, str] | None = None) -> None:
        """What another place kept, taken in only where this vault has nothing
        of its own: the vault's answer wins."""
        with self.vault.doc_transaction(SETTINGS_DOC, schema=SETTINGS_SCHEMA) as doc:
            if homepage and not doc.get("homepage"):
                doc["homepage"] = homepage
            if window:
                mine = doc.get("window") if isinstance(doc.get("window"), dict) else {}
                doc["window"] = {**{k: v for k, v in window.items() if k.startswith(WINDOW_PREFIX)}, **mine}


class AppState:
    """Lists of records the app keeps between runs, each under its own key.
    A record this build cannot read is the caller's to skip: an older shape is
    not worth a failed page."""

    def __init__(self, vault: Vault) -> None:
        self.vault = vault

    def rows(self, key: str) -> list:
        doc = self.vault.read_doc(STATE_DOC)
        got = doc.get("schema", STATE_SCHEMA)
        if not isinstance(got, int) or got > STATE_SCHEMA:
            raise UnreadableDocument(f"{STATE_DOC} was written by a newer longbox — this one does not use it")
        rows = doc.get(key)
        return rows if isinstance(rows, list) else []

    @contextmanager
    def edit(self, key: str) -> Iterator[list]:
        """Load → change → save one list, as one step. The caller changes the
        list it is handed in place."""
        with self.vault.doc_transaction(STATE_DOC, schema=STATE_SCHEMA) as doc:
            rows = doc.get(key) if isinstance(doc.get(key), list) else []
            yield rows
            doc[key] = rows
