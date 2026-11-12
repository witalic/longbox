"""Shared test setup.

The one thing every test here has in common is that it opens a `Library`, and
opening one starts a BACKGROUND thread: the one-time vault migrations (the zip
sweep, the episode pass). That thread writes to the same vault the test is about
to inspect, so a test that asserts anything touched by it is racing — it wins
while the passes stay fast, and the passes are allowed to get slower.

Four different tests have been caught losing that race. They were not flaky
tests; they were tests with a concurrent writer nobody asked for. So the
migrations do not run unless a test says it is about them:

    @pytest.mark.migrations
    def test_episodes_already_in_the_vault_are_fixed_once(...):

Those tests then wait for the thread themselves — `settled(lib)`.

The playable-copy worker is the same kind of writer (library/copies.py): any
episode a test stores that the window cannot play would start an ffmpeg behind
it. It never starts here — a test about copies drives `CopyQueue.work` itself.
So is the tile-preview worker (library/previews.py), which every write feeds,
and the once-per-vault preview pass after a launch's verification: a test about
them drives the queue itself and asks for the pass with `@pytest.mark.previews`.

A vault is never made by opening it (design/vault-home.md): the launch window
creates the folder first. A test's `Library(path)` opens a folder made the same
way — unless the test is about a folder that is not there:

    @pytest.mark.bare_root
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.library.copies import CopyQueue
from app.library.previews import Previews
from app.library.service import Library


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "migrations: let the one-time vault migrations run for this test")
    config.addinivalue_line(
        "markers", "previews: let the once-per-vault tile preview pass run for this test")
    config.addinivalue_line(
        "markers", "bare_root: a Library opened on a path nobody created")


@pytest.fixture(autouse=True)
def _quiet_migrations(request, monkeypatch):
    monkeypatch.setattr(CopyQueue, "_start", lambda self: None)
    monkeypatch.setattr(Previews, "_start", lambda self: None)
    if "previews" not in request.keywords:
        monkeypatch.setattr(Previews, "backfill", lambda self: None)
    if "migrations" in request.keywords:
        return
    monkeypatch.setattr(Library, "_migrations_bg", lambda self: None)


@pytest.fixture(autouse=True)
def _app_folder_of_its_own(tmp_path, monkeypatch):
    """No test reads or writes the app's real folder on this computer (its
    config.json lists the owner's vaults)."""
    monkeypatch.setenv("LONGBOX_CONFIG_DIR", str(tmp_path / "app-data"))


@pytest.fixture(autouse=True)
def _vaults_are_created_first(request, monkeypatch):
    if "bare_root" in request.keywords:
        return
    opening = Library.__init__

    def created_then_opened(self, root: Path, **kw) -> None:
        root.mkdir(parents=True, exist_ok=True)
        opening(self, root, **kw)

    monkeypatch.setattr(Library, "__init__", created_then_opened)


def settled(lib: Library) -> Library:
    """Wait for the one-time migrations of a library that asked for them."""
    thread = lib._normalize_thread
    if thread is not None:
        thread.join(timeout=15)
    return lib


def listed(lib: Library, **selection) -> list:
    """The whole titles a selection lists, in its order — the listing itself
    sends cards (TitleCard)."""
    return lib.get_many([json.loads(c)["id"] for c in lib.index.cards(**selection)])
