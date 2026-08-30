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
"""
from __future__ import annotations

import pytest

from app.library.service import Library


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "migrations: let the one-time vault migrations run for this test")


@pytest.fixture(autouse=True)
def _quiet_migrations(request, monkeypatch):
    if "migrations" in request.keywords:
        return
    monkeypatch.setattr(Library, "_migrations_bg", lambda self: None)


def settled(lib: Library) -> Library:
    """Wait for the one-time migrations of a library that asked for them."""
    thread = lib._normalize_thread
    if thread is not None:
        thread.join(timeout=15)
    return lib
