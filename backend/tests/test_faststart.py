"""An mp4's index moved to the front after an import stored it (library/faststart.py).

The worker's thread is quieted for every test (conftest); these run its pass
in place, or let the real thread go where the thread is the point.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.library import copies, faststart, media
from app.library import folder_import as fi
from app.library.service import Library
from app.settings import get_settings
from .test_video import _index_last_mp4, _mkv

_REAL_START = faststart.Faststart._start


@pytest.fixture(autouse=True)
def _clean_settings(tmp_path, monkeypatch):
    from app import config_store
    monkeypatch.setattr(config_store, "config_dir", lambda: tmp_path / "cfg")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def lib(tmp_path):
    lib = Library(tmp_path / "lib")
    yield lib
    lib.close()


def _clips(src: Path, **kw) -> Path:
    (src / "Clips").mkdir(parents=True)
    _index_last_mp4(src / "Clips" / "beach day.mp4", b"\xab" * 8192, **kw)
    return src


def _import(lib: Library, src: Path, files: str = "copy") -> fi.Report:
    profile = fi.Profile(levels=[fi.Level(role="collection"), fi.Level(role="work")], entryWorks=True,
                         workTitle="{file}", files=files)
    return fi.run(lib, fi.scan(src), profile)


def _pass(lib: Library) -> None:
    """The worker's pass, here and now."""
    lib.faststart.fill()
    lib.faststart._loop()


def _episode(lib: Library) -> tuple[str, str, Path]:
    [(tid, sides)] = lib.index.episodes().items()
    [stem] = sides
    return tid, stem, lib.vault.chapter_media_path(tid, stem)


def _side(lib: Library) -> dict:
    tid, stem, _ = _episode(lib)
    return lib.vault.chapter_sidecar(tid, stem)


def _leftovers(path: Path) -> list[str]:
    return [p.name for p in path.parent.iterdir() if p.suffix == ".tmp"]


def test_an_import_leaves_the_index_to_the_background(lib, tmp_path):
    report = _import(lib, _clips(tmp_path / "src"), files="move")
    assert (report.later, report.inPlace) == (1, 1), report

    # stored as it came: a move onto the vault's drive reads and writes nothing
    tid, stem, path = _episode(lib)
    side = _side(lib)
    assert media.probe_mp4(path)["faststart"] is False
    assert side[faststart.LATER] is True and "sha256" not in side and "content" not in side

    _pass(lib)
    after = _side(lib)
    assert media.probe_mp4(path)["faststart"] is True
    assert faststart.LATER not in after and after["faststart"] is True
    # new bytes: a new revision, and the digest they were written under
    assert after["rev"] == side["rev"] + 1
    assert after["sha256"] == after["content"] == media.digest_of(path)
    held = lib.index.episodes()[tid][stem]
    assert held["faststart"] is True and faststart.LATER not in held
    assert _leftovers(path) == []


def test_a_copied_episode_keeps_a_true_digest_through_its_rewrite(lib, tmp_path):
    _import(lib, _clips(tmp_path / "src"))
    _, _, path = _episode(lib)
    assert _side(lib)["sha256"] == media.digest_of(path)  # of the bytes as they came

    _pass(lib)
    assert media.probe_mp4(path)["faststart"] is True
    assert _side(lib)["sha256"] == _side(lib)["content"] == media.digest_of(path)


def test_a_file_the_rewrite_refuses_plays_as_it_is(lib, tmp_path):
    _import(lib, _clips(tmp_path / "src", fragmented=True))
    _, _, path = _episode(lib)
    before = path.read_bytes()

    _pass(lib)
    assert path.read_bytes() == before
    assert faststart.LATER not in _side(lib)  # tried: no pass tries it again


def test_an_episode_changed_while_it_was_copied_is_not_overwritten(lib, tmp_path, monkeypatch):
    """The copy is made without the title lock; it lands only over the very
    file it was made from."""
    _import(lib, _clips(tmp_path / "src"))
    _, _, path = _episode(lib)
    real = media.faststart_copy

    def meanwhile(p, stop=None):
        made = real(p, stop)
        p.write_bytes(p.read_bytes() + b"\x00")
        return made
    monkeypatch.setattr(media, "faststart_copy", meanwhile)

    _pass(lib)
    assert media.probe_mp4(path)["faststart"] is False
    assert _side(lib)[faststart.LATER] is True  # still owed
    assert _leftovers(path) == []


def test_an_episode_the_player_holds_keeps_its_mark(lib, tmp_path, monkeypatch):
    _import(lib, _clips(tmp_path / "src"))
    _, _, path = _episode(lib)

    def held(tmp, final):
        tmp.unlink()
        raise media.MediaInUseError(final.name)
    monkeypatch.setattr(media, "replace_atomically", held)

    _pass(lib)
    assert media.probe_mp4(path)["faststart"] is False
    assert _side(lib)[faststart.LATER] is True  # the next pass tries again


def test_a_stopped_copy_leaves_nothing_behind(tmp_path):
    path = _index_last_mp4(tmp_path / "ep.mp4", b"\xab" * 4096)
    before = path.read_bytes()
    stop = threading.Event()
    stop.set()

    with pytest.raises(InterruptedError):
        media.faststart_copy(path, stop)
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["ep.mp4"]


def test_a_close_stops_the_copy_and_keeps_the_mark(tmp_path, monkeypatch):
    """A close that waited for a big episode on a share would leave the session
    unsettled — and the next launch would verify the whole vault."""
    monkeypatch.setattr(faststart.Faststart, "_start", _REAL_START)
    copying = threading.Event()

    def endless(fh, out, offset, length):
        copying.set()
        while True:
            out.write(b"")  # where a stop is noticed
            time.sleep(0.01)
    monkeypatch.setattr(media, "_copy_range", endless)
    lib = Library(tmp_path / "lib")
    _import(lib, _clips(tmp_path / "src"))
    assert copying.wait(10)
    tid, stem, path = _episode(lib)

    assert lib._stop_work() is True  # the close did not wait the copy out
    assert lib.vault.chapter_sidecar(tid, stem)[faststart.LATER] is True
    assert _leftovers(path) == []
    lib.close()


def test_a_write_that_needs_the_file_stops_its_copy(tmp_path, monkeypatch):
    """Windows refuses to replace or delete a file open for reading: the vault
    asks the copy to let go first, as it asks a playable copy's ffmpeg."""
    monkeypatch.setattr(faststart.Faststart, "_start", _REAL_START)
    copying = threading.Event()

    def endless(fh, out, offset, length):
        copying.set()
        while True:
            out.write(b"")
            time.sleep(0.01)
    monkeypatch.setattr(media, "_copy_range", endless)
    lib = Library(tmp_path / "lib")
    _import(lib, _clips(tmp_path / "src"))
    assert copying.wait(10)
    tid, _, path = _episode(lib)

    lib.vault.let_go(tid, None)
    assert lib.faststart._idle.is_set() and _leftovers(path) == []
    lib.close()


def test_a_launch_carries_on_with_what_an_import_left(tmp_path, monkeypatch):
    lib = Library(tmp_path / "lib")
    _import(lib, _clips(tmp_path / "src"))  # its pass never runs: the worker is quiet
    lib.close()

    filled: list[bool] = []
    monkeypatch.setattr(faststart.Faststart, "_start", lambda self: filled.append(True))
    again = Library(tmp_path / "lib", defer_sync=True)
    again.sync_in_background()
    again._sync_thread.join(timeout=15)
    assert filled
    again.faststart._loop()
    _, _, path = _episode(again)
    assert media.probe_mp4(path)["faststart"] is True
    again.close()


def test_a_copy_waits_for_the_rewrite_its_episode_is_owed():
    side = {"kind": "video", "playable": False}
    assert copies.needs_copy(side)
    assert not copies.needs_copy({**side, faststart.LATER: True})


def test_what_a_pass_rewrote_reaches_the_index_even_as_the_library_closes(lib, tmp_path):
    _import(lib, _clips(tmp_path / "src"))
    tid, stem, _ = _episode(lib)
    lib.vault.write_chapter_sidecar(tid, stem, {**_side(lib), "duration": 12.5})

    lib.closing()
    lib.reindex([tid])
    assert lib.index.episodes()[tid][stem]["duration"] == 12.5


def test_a_linked_episode_gets_its_content_digest_from_health(lib, tmp_path):
    """Nothing of a file linked in is read at import; finding it stored twice
    needs its digest, which Health stamps when it reads it."""
    (tmp_path / "src" / "Clips").mkdir(parents=True)
    _mkv(tmp_path / "src" / "Clips" / "beach day.mkv", (1, "V_MPEG4/ISO/AVC"))
    _import(lib, tmp_path / "src", files="move")
    _, _, path = _episode(lib)
    assert not {"sha256", "content", faststart.LATER} & set(_side(lib))

    lib.health.verify(deep=True, backfill=True)
    assert _side(lib)["sha256"] == _side(lib)["content"] == media.digest_of(path)
