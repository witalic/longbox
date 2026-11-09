"""The vault is home (design/vault-home.md): a vault is created on purpose and
never by opening a path, it knows which vault it is, and while it does not
answer nothing is read from it or written anywhere."""
from __future__ import annotations

import io
import json
import threading
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config_store import config_path
from app.library.models import DraftIn, TitleMeta
from app.library.service import Library
from app.library.vault import Vault, VaultGone
from app.main import create_app
from app.passes import stop_event
from app.settings import get_settings
from app.vault_watch import VaultWatch


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setenv("LONGBOX_CONFIG_DIR", str(tmp_path / "cfg"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    stop_event().clear()


def _meta(root) -> dict:
    return json.loads((root / "vault.json").read_text(encoding="utf-8"))


# ---- a vault is made on purpose ---------------------------------------------

@pytest.mark.bare_root
def test_opening_a_path_that_is_not_there_makes_nothing(tmp_path):
    """A drive that dropped leaves its path behind; opening it must fail, not
    build a new library wherever the path now leads."""
    with pytest.raises(VaultGone):
        Library(tmp_path / "Y" / "LongBox")
    assert not (tmp_path / "Y").exists()


def test_a_vault_knows_which_vault_it_is(tmp_path):
    lib = Library(tmp_path / "v")
    first = lib.vault.id
    assert first and _meta(tmp_path / "v")["id"] == first
    lib.vault.mark_normalized()  # the markers live beside it and never drop it
    lib.close()
    again = Library(tmp_path / "v")
    assert again.vault.id == first and _meta(tmp_path / "v")["zipNormalized"]
    assert again.vault.still_here()
    again.close()


def test_a_marker_written_over_a_read_that_failed_never_drops_the_id(tmp_path, monkeypatch):
    lib = Library(tmp_path / "v")
    before = (tmp_path / "v" / "vault.json").read_bytes()
    real = type(tmp_path).read_text

    def blinking(self, *a, **kw):
        if self.name == "vault.json":
            raise OSError(64, "The specified network name is no longer available")
        return real(self, *a, **kw)

    monkeypatch.setattr(type(tmp_path), "read_text", blinking)
    with pytest.raises(OSError):
        lib.vault.mark_faststart()
    monkeypatch.undo()
    assert (tmp_path / "v" / "vault.json").read_bytes() == before
    lib.close()


def test_another_folder_at_the_vaults_path_is_not_the_vault(tmp_path):
    lib = Library(tmp_path / "v")
    (tmp_path / "v" / "vault.json").write_text(json.dumps({"id": "someone-else"}), encoding="utf-8")
    assert not lib.vault.still_here()
    (tmp_path / "v" / "vault.json").unlink()
    assert not lib.vault.still_here()
    lib.close()


def test_nothing_is_written_under_a_root_that_is_no_longer_the_vault(tmp_path):
    """An emptied mount point: the folder is there, the vault is not. Neither a
    title's folders nor a document may be made in it."""
    lib = Library(tmp_path / "v")
    (tmp_path / "v" / "vault.json").unlink()
    with pytest.raises(VaultGone):
        lib.create(DraftIn(meta=TitleMeta(title="Lost", type="manga")))
    assert not (tmp_path / "v" / "manga").exists()
    with pytest.raises(VaultGone):
        lib.settings.set_homepage("https://example.org")
    assert not (tmp_path / "v" / "settings.json").exists()
    lib.close()


def test_the_sidecar_never_invents_a_vault(tmp_path, monkeypatch):
    # nothing named, nothing configured: no default library appears
    with pytest.raises(RuntimeError, match="no vault named"):
        with TestClient(create_app()):
            pass
    # a folder that is not a vault is not adopted as one
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "holiday.jpg").write_bytes(b"x")
    monkeypatch.setenv("LONGBOX_LIBRARY_PATH", str(tmp_path / "plain"))
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="not a vault"):
        with TestClient(create_app()):
            pass
    assert not (tmp_path / "plain" / "vault.json").exists()
    assert not (tmp_path / "cfg" / "library").exists()


def test_a_named_vault_is_served_and_watched(tmp_path, monkeypatch):
    Library(tmp_path / "v").close()  # created first, as the launcher does
    monkeypatch.setenv("LONGBOX_LIBRARY_PATH", str(tmp_path / "v"))
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        assert c.get("/health").json()["vault"] == "ok"
        assert c.get("/api/settings").json()["library_path"] == str(tmp_path / "v")
        assert c.app.state.vault_watch is not None


# ---- the watch ----------------------------------------------------------------

def test_the_vault_is_lost_on_two_failed_answers_and_back_on_two_good_ones(tmp_path):
    lib = Library(tmp_path / "v")
    watch = VaultWatch(lib)
    watch.heard(False)
    assert watch.state == "ok"  # one slow read does not take the window away
    watch.heard(True)
    watch.heard(False)
    assert watch.state == "ok"
    watch.heard(False)
    assert watch.state == "lost" and watch.since
    deadline = time.monotonic() + 5
    while not lib._closing.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert lib._closing.is_set() and stop_event().is_set()  # the work on it stopped
    watch.heard(True)
    watch.heard(False)
    assert watch.state == "lost"
    watch.heard(True)
    watch.heard(True)
    assert watch.state == "back"
    watch.heard(False)
    assert watch.state == "back"  # final: the app restarts into it
    lib.close()


def test_an_answer_that_does_not_come_counts_as_none(tmp_path, monkeypatch):
    lib = Library(tmp_path / "v")
    held = threading.Event()
    monkeypatch.setattr(lib.vault, "still_here", lambda: held.wait(10) or True)
    watch = VaultWatch(lib, deadline=0.05)
    started = time.monotonic()
    assert watch.ask() is False
    assert watch.ask() is False  # the stuck read is waited on, not piled on
    assert time.monotonic() - started < 2
    held.set()
    lib.close()


def test_while_the_vault_is_lost_nothing_waits_on_it(tmp_path):
    lib = Library(tmp_path / "v")
    lib.create(DraftIn(meta=TitleMeta(title="Kept")))
    with TestClient(create_app(lib)) as c:
        watch = VaultWatch(lib)
        watch.state = "lost"
        c.app.state.vault_watch = watch
        r = c.get("/api/library")
        assert r.status_code == 503 and r.json()["vault"] == "lost"
        assert c.put("/api/settings/homepage", json={"homepage": "https://x.example"}).status_code == 503
        assert c.get("/health").json()["vault"] == "lost"
        watch.state = "back"
        assert c.get("/health").json()["vault"] == "back"
    assert lib.settings.homepage() == "https://www.google.com"
    lib.close()


def test_a_write_refused_by_a_vanished_vault_answers_unreachable(tmp_path):
    lib = Library(tmp_path / "v")
    with TestClient(create_app(lib)) as c:
        (tmp_path / "v" / "vault.json").unlink()
        r = c.put("/api/settings/homepage", json={"homepage": "https://x.example"})
        assert r.status_code == 503 and r.json()["vault"] == "lost"
    lib.close()


# ---- what used to stay on this computer -----------------------------------------

def test_previews_are_cached_in_the_vault(tmp_path):
    from PIL import Image
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Berserk")))
    src = tmp_path / "in.zip"
    buf = io.BytesIO()
    Image.new("RGB", (300, 450), (200, 0, 0)).save(buf, "JPEG")
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("01.jpg", buf.getvalue())
    lib.attach_chapter_media(out.id, num="1", lang="EN", group="", src=src, sidecar={})
    cid = lib.get(out.id).chapters[0].id
    with TestClient(create_app(lib)) as c:
        assert c.get(f"/api/titles/{out.id}/chapters/{cid}/pages/0?w=160").status_code == 200
    assert list((tmp_path / "v" / ".longbox" / "cache" / "thumbs").glob("*.jpg"))
    assert not (tmp_path / "cfg" / "cache").exists()
    lib.close()


def test_an_interrupted_transfer_is_kept_in_the_vault(tmp_path):
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Series")))
    with TestClient(create_app(lib)) as c:
        c.post("/api/downloads/arm", json={"titleId": out.id, "num": "1", "lang": "EN", "group": ""})
        did = c.post("/api/downloads/start", json={"filename": "ep1.mp4"}).json()["id"]
        c.post(f"/api/downloads/{did}/interrupted",
               json={"reason": "the app was closed", "resume": {"path": "x", "urlChain": ["https://a/b"]}})
    assert [r["id"] for r in lib.state.rows("interrupted_downloads")] == [did]
    assert not config_path().exists()
    lib.close()
    again = Library(tmp_path / "v")
    with TestClient(create_app(again)) as c:
        assert [i["id"] for i in c.get("/api/downloads").json()["items"]] == [did]
    again.close()
