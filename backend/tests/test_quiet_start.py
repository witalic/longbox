"""A launch checks only what no write of longbox can have told the index: the
titles added to or removed from the shelves by hand. The whole vault is read
again only when the index is empty or the last session never settled."""
from __future__ import annotations

import os
import shutil
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from app import main as main_mod
from app.library.index import LibraryIndex
from app.library.models import DraftIn, TitleMeta, UserPatch
from app.library.service import SESSION_DOC, SESSION_SCHEMA, Library
from app.library.vault import Vault
from app.main import create_app

from .conftest import listed


def _launch(root) -> Library:
    """Opened the way the sidecar opens a vault: served first, checked behind."""
    lib = Library(root, defer_sync=True)
    lib.sync_in_background()
    lib._sync_thread.join(timeout=15)
    return lib


def _open(root) -> object:
    return Vault(root).read_doc(SESSION_DOC).get("open")


@pytest.fixture
def scans(monkeypatch):
    """Every whole-vault scan, by vault — one per title folder on a network disk."""
    seen: list = []
    real = Vault.scan

    def counted(self):
        seen.append(self.root)
        return real(self)

    monkeypatch.setattr(Vault, "scan", counted)
    return seen


def test_a_launch_after_a_clean_close_reads_no_title(tmp_path, scans, monkeypatch):
    root = tmp_path / "v"
    lib = _launch(root)
    for name in ("One", "Two", "Three"):
        lib.create(DraftIn(meta=TitleMeta(title=name)))
    assert _open(root) is True  # serving: a crash from here on is noticed
    lib.close()
    assert _open(root) is False

    reads: list[str] = []
    real_load = Vault.load
    monkeypatch.setattr(Vault, "load", lambda self, tid: (reads.append(tid), real_load(self, tid))[1])
    scans.clear()
    lib = _launch(root)
    assert scans == [] and reads == []
    assert sorted(t.title for t in listed(lib)) == ["One", "Three", "Two"]
    lib.close()


def test_titles_added_or_removed_by_hand_are_picked_up_quietly(tmp_path, scans):
    root = tmp_path / "v"
    lib = _launch(root)
    lib.create(DraftIn(meta=TitleMeta(title="Stays")))
    gone = lib.create(DraftIn(meta=TitleMeta(title="Gone")))
    gone_dir = lib.vault.title_dir(gone.id)
    lib.close()
    other = _launch(tmp_path / "other")
    came = other.create(DraftIn(meta=TitleMeta(title="Copied In")))
    came_dir = other.vault.title_dir(came.id)
    other.close()

    shutil.rmtree(gone_dir)
    shutil.copytree(came_dir, root / came_dir.parent.name / came_dir.name)
    scans.clear()
    lib = _launch(root)
    assert scans == []
    assert sorted(t.title for t in listed(lib)) == ["Copied In", "Stays"]
    assert lib.sync_state["changed"] == 2  # the window reloads its list on this
    lib.close()


def test_a_session_that_never_closed_reads_the_whole_vault(tmp_path, scans):
    root = tmp_path / "v"
    _launch(root).close()
    with Vault(root).doc_transaction(SESSION_DOC, schema=SESSION_SCHEMA) as doc:
        doc["open"] = True  # what a crash leaves behind
    scans.clear()
    lib = _launch(root)
    assert scans == [root]
    lib.close()
    assert _open(root) is False  # verified, and closed: the next launch is quiet


def test_an_empty_index_reads_the_whole_vault(tmp_path, scans):
    root = tmp_path / "v"
    lib = _launch(root)
    lib.create(DraftIn(meta=TitleMeta(title="Kept")))
    lib.close()
    (root / "index.db").unlink()  # the cache dropped: the vault is still all there
    scans.clear()
    lib = _launch(root)
    assert scans == [root]
    assert [t.title for t in listed(lib)] == ["Kept"]
    lib.close()


def test_a_vault_that_stopped_answering_is_read_whole_next_time(tmp_path, scans):
    root = tmp_path / "v"
    lib = _launch(root)
    lib.lose()  # a write in flight may never have reached the index
    lib.close()
    assert _open(root) is True
    scans.clear()
    _launch(root).close()
    assert scans == [root]


def test_a_failed_index_write_is_read_whole_next_time(tmp_path, scans, monkeypatch):
    root = tmp_path / "v"
    lib = _launch(root)

    def broken(*_a, **_kw):
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr(LibraryIndex, "_row", staticmethod(broken))
    with pytest.raises(RuntimeError):
        lib.create(DraftIn(meta=TitleMeta(title="On disk, not in the index")))
    monkeypatch.undo()
    lib.close()
    assert _open(root) is True
    lib = _launch(root)
    assert [t.title for t in listed(lib)] == ["On disk, not in the index"]
    lib.close()


def test_a_shelf_that_cannot_be_listed_drops_nothing(tmp_path, monkeypatch):
    root = tmp_path / "v"
    lib = _launch(root)
    lib.create(DraftIn(meta=TitleMeta(title="Still Here")))
    lib.close()
    monkeypatch.setattr(Vault, "title_dirs", lambda self: ({"elsewhere"}, False))
    lib = _launch(root)
    assert [t.title for t in listed(lib)] == ["Still Here"]
    lib.close()
    assert _open(root) is True  # the check is owed: the next launch makes it whole


def test_a_vault_no_session_has_marked_yet_is_read_whole_once(tmp_path, scans):
    """An older build kept no marker: nothing says its last session settled."""
    root = tmp_path / "v"
    lib = Library(root)  # opened the way an older build did: no session kept
    lib.create(DraftIn(meta=TitleMeta(title="Kept")))
    lib.close()
    assert _open(root) is None
    scans.clear()
    _launch(root).close()
    assert scans == [root]
    scans.clear()
    _launch(root).close()
    assert scans == []


def test_a_write_that_failed_before_the_index_is_read_whole_next_time(tmp_path, scans, monkeypatch):
    """The title is on disk; the index never heard of it — whatever step failed."""
    root = tmp_path / "v"
    lib = _launch(root)

    def unreadable(self, _tid):
        raise PermissionError(13, "the share refused")

    monkeypatch.setattr(Library, "_covers_url", unreadable)
    with pytest.raises(PermissionError):
        lib.create(DraftIn(meta=TitleMeta(title="On disk")))
    monkeypatch.undo()
    lib.close()
    assert _open(root) is True


def test_a_chapter_folder_that_will_not_answer_is_not_read_as_empty(tmp_path, monkeypatch):
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Kept")))

    class Refusing:
        def stat(self):
            raise PermissionError(13, "the share refused")

    monkeypatch.setattr(Vault, "chapters_dir", lambda self, tid: Refusing())
    with pytest.raises(PermissionError):
        lib._sidecars(out.id)
    lib.close()


def test_a_read_mark_reaches_the_index_whatever_the_file_clock_says(tmp_path, monkeypatch):
    """A shared drive's clock, a stat that failed: the stamp a user-layer write
    reads back can be OLDER than the row's. The mark lands all the same."""
    lib = _launch(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Kept")))
    monkeypatch.setattr(Vault, "touched_at", lambda self, tid: 0)
    lib.patch_user(out.id, UserPatch(fav=True, rating=4))
    monkeypatch.undo()
    doc = lib.index.get(out.id)[0]
    assert (doc.user.fav, doc.user.rating) == (True, 4)
    assert listed(lib)[0].fav is True
    lib.close()


def test_a_title_missing_from_one_listing_but_on_disk_is_kept(tmp_path):
    """Moved to another shelf while the shelves were listed: not gone."""
    root = tmp_path / "v"
    lib = _launch(root)
    out = lib.create(DraftIn(meta=TitleMeta(title="Moving")))
    lib.close()
    real = Vault.title_dirs

    def listed_without_it(self):
        ids, complete = real(self)
        return ids - {out.id}, complete

    lib = Library(root, defer_sync=True)
    Vault.title_dirs = listed_without_it
    try:
        lib.sync_in_background()
        lib._sync_thread.join(timeout=15)
    finally:
        Vault.title_dirs = real
    assert [t.title for t in listed(lib)] == ["Moving"]
    lib.close()


def test_a_title_created_while_the_shelves_are_listed_is_kept(tmp_path, monkeypatch):
    root = tmp_path / "v"
    first = _launch(root)
    first.create(DraftIn(meta=TitleMeta(title="Old")))
    first.close()
    lib = Library(root, defer_sync=True)
    real = Vault.title_dirs

    def created_meanwhile(self):
        listed = real(self)
        lib.create(DraftIn(meta=TitleMeta(title="Just Made")))
        return listed

    monkeypatch.setattr(Vault, "title_dirs", created_meanwhile)
    lib.sync_in_background()
    lib._sync_thread.join(timeout=15)
    monkeypatch.undo()
    assert sorted(t.title for t in listed(lib)) == ["Just Made", "Old"]
    assert lib.sync_state["changed"] == 0  # the quiet path, and nothing dropped
    lib.close()


def test_an_index_changed_after_a_clean_close_is_read_whole(tmp_path, scans):
    """An older build keeps no marker: if it opened the vault since, the index
    is not the one this marker describes."""
    root = tmp_path / "v"
    lib = _launch(root)
    lib.create(DraftIn(meta=TitleMeta(title="Kept")))
    lib.close()
    db = sqlite3.connect(root / "index.db")
    with db:
        db.execute("UPDATE titles SET rating = 5")  # any other writer's commit
    db.close()
    # later, as another build's session is: not inside this clock tick
    st = (root / "index.db").stat()
    os.utime(root / "index.db", ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
    scans.clear()
    _launch(root).close()
    assert scans == [root]


def test_the_shell_closes_the_library_before_it_stops_the_sidecar(tmp_path):
    """On Windows the shell's stop is a kill: no shutdown runs. Asked first, the
    library closes and the next launch is quiet."""
    root = tmp_path / "v"
    lib = _launch(root)
    with TestClient(create_app(lib)) as c:
        assert c.post("/api/close").json() == {"settled": True}
        assert c.get("/api/library").status_code == 503  # nothing after the close
    assert _open(root) is False


def test_a_write_still_on_its_way_at_close_leaves_the_session_open(tmp_path, monkeypatch):
    root = tmp_path / "v"
    lib = _launch(root)
    out = lib.create(DraftIn(meta=TitleMeta(title="Busy")))
    entered, release = threading.Event(), threading.Event()
    real = Library.patch_user

    def slow(self, *a, **kw):
        entered.set()
        release.wait(10)
        return real(self, *a, **kw)

    monkeypatch.setattr(Library, "patch_user", slow)
    monkeypatch.setattr(main_mod, "CLOSE_WAIT_S", 0.2)
    def write() -> None:
        try:
            c.patch(f"/api/titles/{out.id}/user", json={"fav": True})
        except Exception:  # noqa: BLE001 — cut off by the close, as the kill would cut it
            pass

    with TestClient(create_app(lib)) as c:
        writer = threading.Thread(target=write)
        writer.start()
        assert entered.wait(10)
        assert c.post("/api/close").json() == {"settled": False}
        release.set()
        writer.join(10)
    assert _open(root) is True
