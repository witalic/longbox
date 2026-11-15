"""The log answers what a launch and the work behind it did (app/logs.py): why a
launch checked the whole vault, why a close left the session open, what each
job did and how long it took — never something to be guessed at."""
from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient

from app import logs
from app.library import session
from app.library.models import DraftIn, TitleMeta
from app.library.service import Library
from app.library.vault import Vault
from app.main import create_app
from app.passes import _pass
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _launch(root) -> Library:
    lib = Library(root, defer_sync=True)
    lib.sync_in_background()
    lib._sync_thread.join(timeout=15)
    return lib


def _said(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


def test_a_launch_says_why_it_checks_the_whole_vault(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    root = tmp_path / "v"
    lib = _launch(root)  # an empty index, and no record of a session before it
    lib.create(DraftIn(meta=TitleMeta(title="One")))
    assert "checking the whole vault: there is no record of the last session" in _said(caplog)
    lib.close()
    assert "closed; the session is marked closed" in _said(caplog)

    caplog.clear()
    lib = _launch(root)  # after a clean close: the shelves only
    assert not any(m.startswith("checking the whole vault") for m in _said(caplog))
    assert any(m.startswith(f"opened {root} quietly") for m in _said(caplog))
    lib._closed = True  # gone without a close, as a killed process is
    lib.index.close()

    caplog.clear()
    lib = _launch(root)
    assert "checking the whole vault: the last session did not close" in _said(caplog)
    lib.close()

    with open(root / "index.db", "ab") as fh:  # an older build wrote it meanwhile
        fh.write(b"\0" * 4096)
    caplog.clear()
    lib = _launch(root)
    assert any(m.startswith("checking the whole vault: the index file changed after the last session")
               for m in _said(caplog))
    lib.close()


def test_a_close_says_why_it_leaves_the_session_open(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    lib = _launch(tmp_path / "v")
    lib.close(settled=False)
    assert ("closed; the session is left open, so the next launch checks the whole vault: "
            "a write was still on its way") in _said(caplog)
    assert Vault(tmp_path / "v").read_doc(session.DOC)["open"] is True


def test_the_shell_and_the_window_write_into_the_one_log(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    lib = Library(tmp_path / "v")
    at = time.time() * 1000 - 60_000  # held from before the sidecar answered
    with TestClient(create_app(lib)) as c:
        r = c.post("/api/log", json={"lines": [{"at": at, "text": "vault chosen"},
                                                {"at": at + 5, "level": "error", "text": "x" * 5000}]})
        assert r.status_code == 204
        c.post("/api/log", json={"source": "window", "lines": [{"text": "TypeError: boom"}]})
    shell = [r for r in caplog.records if r.name == "longbox.shell"]
    assert [(r.levelname, r.getMessage()[:12]) for r in shell] == [("INFO", "vault chosen"), ("ERROR", "xxxxxxxxxxxx")]
    assert abs(shell[0].created - at / 1000) < 0.01  # its own time, not the time it arrived
    assert len(shell[1].getMessage()) == 2000
    assert [r.getMessage() for r in caplog.records if r.name == "longbox.window"] == ["TypeError: boom"]
    lib.close()


def test_a_slow_request_is_a_line_of_its_own(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(logs, "SLOW_S", 0.0)
    lib = Library(tmp_path / "v")
    with TestClient(create_app(lib)) as c:
        c.get("/api/library")
    assert any(m.startswith("slow request: GET /api/library took") for m in _said(caplog))
    lib.close()


def test_a_step_says_when_it_started_how_long_it_took_and_what_it_did(caplog):
    caplog.set_level(logging.INFO)
    with logs.step("counting") as said:
        said.append("3 found")
    with pytest.raises(OSError), logs.step("reading"):
        raise OSError("the share went away")
    lines = _said(caplog)
    assert lines[0] == "counting — started" and lines[1].startswith("counting — done in ")
    assert lines[1].endswith(": 3 found")
    assert lines[3].startswith("reading — failed after ") and lines[3].endswith(": the share went away")


def test_a_pass_says_how_far_it_got_and_whether_it_was_stopped(caplog):
    caplog.set_level(logging.INFO)
    with _pass("checking the library") as (progress, stop):
        progress(4, 10)
        stop.set()
    assert _said(caplog)[0] == "checking the library — started"
    assert _said(caplog)[1].endswith(": 4 of 10, stopped")
    stop.clear()


def test_a_run_opens_with_its_line_and_every_line_names_it(tmp_path, monkeypatch):
    import json
    from logging.handlers import RotatingFileHandler

    (tmp_path / ".longbox").mkdir()
    at = time.time() * 1000 - 2000
    monkeypatch.setenv("LONGBOX_RUN", "a1b2")
    monkeypatch.setenv("LONGBOX_LOG_FIRST", json.dumps([{"at": at, "text": "longbox 9.9.9 starting"},
                                                        {"at": at + 9, "text": "the vault: here"}]))
    logs.to_file(tmp_path / ".longbox")
    logging.getLogger("longbox").info("sidecar starting")
    for h in logging.getLogger().handlers:
        h.flush()
    lines = (tmp_path / ".longbox" / "longbox.log").read_text(encoding="utf-8").splitlines()
    assert [ln.split(": ", 1)[1] for ln in lines] == [
        "----- run a1b2 -----", "longbox 9.9.9 starting", "the vault: here", "sidecar starting"]
    assert all(" [a1b2] " in ln for ln in lines)
    assert " longbox.shell: " in lines[1]
    # three files of a megabyte: a run that said a lot leaves the one before it
    [handler] = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert (handler.maxBytes, handler.backupCount) == (1_000_000, 2)
