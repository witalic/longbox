"""The second media kind: an episode is the file itself, not a zip of pages.

Everything a chapter already is — identity, provenance, translations, progress
— stays the same; only how the media is stored and read differs.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.library import media
from app.library.models import DraftIn, TitleMeta
from app.library.service import Library
from app.main import create_app
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _mp4(path: Path, payload: bytes = b"\x00" * 4096) -> Path:
    """A file whose header says mp4 the way a real one does: `ftyp` at byte 4."""
    path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + payload)
    return path


def _title_with_episode(root: Path, tmp_path: Path) -> tuple[Library, str, str]:
    lib = Library(root)
    out = lib.create(DraftIn(meta=TitleMeta(title="Series", type="anime")))
    lib.attach_chapter_media(out.id, num="1", lang="EN", group="sub",
                             src=_mp4(tmp_path / "ep1.mp4"), sidecar={"filename": "ep1.mp4"})
    return lib, out.id, lib.get(out.id).chapters[0].id


def test_an_episode_is_stored_as_itself(tmp_path):
    lib, tid, cid = _title_with_episode(tmp_path / "v", tmp_path)
    stored = lib.vault.chapter_media_path(tid, cid)
    assert stored is not None and stored.suffix == ".mp4"
    assert not list(stored.parent.glob("*.zip"))  # the zip invariant is about PAGE media

    chapter = lib.get(tid).chapters[0]
    assert chapter.kind == "video" and chapter.dl is True and chapter.pages == 0
    lib.close()


def test_a_video_that_is_not_one_is_refused_at_ingest(tmp_path):
    """The same rule the zip invariant applies: never store an opaque file. A
    site answering a download with an HTML error page names it `.mp4` too."""
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Series")))
    fake = tmp_path / "ep1.mp4"
    fake.write_bytes(b"<html>not found</html>")
    with pytest.raises(media.UnsupportedArchiveError):
        lib.attach_chapter_media(out.id, num="1", lang="", group="", src=fake,
                                 sidecar={"filename": "ep1.mp4"})
    assert lib.get(out.id).chapters == [] or lib.get(out.id).chapters[0].dl is False
    lib.close()


def test_the_episode_is_served_with_seeking(tmp_path):
    """Playback needs range requests, or the player can only ever stream from
    the start — no scrubbing, no resume."""
    lib, tid, cid = _title_with_episode(tmp_path / "v", tmp_path)
    with TestClient(create_app(lib)) as c:
        whole = c.get(f"/api/titles/{tid}/chapters/{cid}/video")
        assert whole.status_code == 200
        assert whole.headers["content-type"] == "video/mp4"

        part = c.get(f"/api/titles/{tid}/chapters/{cid}/video",
                     headers={"Range": "bytes=100-199"})
        assert part.status_code == 206
        assert part.headers["content-range"] == f"bytes 100-199/{len(whole.content)}"
        assert part.content == whole.content[100:200]
    lib.close()


def test_learning_the_duration_does_not_invalidate_the_file(tmp_path):
    """The player measures what the app cannot read without ffprobe. It
    describes the SAME bytes, so it must not bump the media version — that
    would throw away every cached thing about a file that did not change."""
    lib, tid, cid = _title_with_episode(tmp_path / "v", tmp_path)
    with TestClient(create_app(lib)) as c:
        before = c.get(f"/api/titles/{tid}").json()["chapters"][0]
        c.post(f"/api/titles/{tid}/chapters/{cid}/video/meta", json={"duration": 1423.5})
        after = c.get(f"/api/titles/{tid}").json()["chapters"][0]
        assert after["duration"] == 1423.5
        assert after["v"] == before["v"]
    lib.close()


def test_page_endpoints_do_not_pretend_an_episode_has_pages(tmp_path):
    lib, tid, cid = _title_with_episode(tmp_path / "v", tmp_path)
    with TestClient(create_app(lib)) as c:
        assert c.get(f"/api/titles/{tid}/chapters/{cid}/pages").json() == {"count": 0}
        assert c.get(f"/api/titles/{tid}/chapters/{cid}/pages/0").status_code == 404
    lib.close()


def test_a_page_chapter_has_no_video_to_serve(tmp_path):
    import zipfile

    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Manga")))
    src = tmp_path / "ch.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("001.jpg", b"page")
    lib.attach_chapter_media(out.id, num="1", lang="EN", group="dex", src=src, sidecar={})
    cid = lib.get(out.id).chapters[0].id
    assert lib.get(out.id).chapters[0].kind == "pages"
    with TestClient(create_app(lib)) as c:
        assert c.get(f"/api/titles/{out.id}/chapters/{cid}/video").status_code == 404
    lib.close()


def test_an_episode_can_be_imported_the_same_way_an_archive_is(tmp_path):
    """The picker posts a file and the chapter identity; nothing about the flow
    is video-specific, so attaching an episode is attaching a file."""
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Series", type="anime")))
    payload = _mp4(tmp_path / "Episode 01.mp4").read_bytes()

    with TestClient(create_app(lib)) as c:
        r = c.post(f"/api/titles/{out.id}/chapters/import",
                   params={"num": "1", "lang": "EN", "group": "sub",
                           "filename": "Episode 01.mp4"},
                   content=payload)
        assert r.status_code == 200
        chapter = r.json()["chapters"][0]
        assert chapter["kind"] == "video" and chapter["dl"] is True
        assert c.get(f"/api/titles/{out.id}/chapters/{chapter['id']}/video").status_code == 200
    lib.close()


def test_the_vault_records_why_a_file_may_start_slowly(tmp_path):
    """Two properties decide whether playback feels instant, and neither shows
    in the file name: where the index sits, and which codec it holds. The app
    reads both at ingest so it can say WHY instead of looking broken."""
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Series", type="anime")))

    # moov AFTER the media, as a file straight off a muxer usually is
    src = tmp_path / "tail.mp4"
    moov = b"moov" + b"\x00" * 4 + b"hvc1" + b"\x00" * 64
    src.write_bytes(
        b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
        + (len(b"mdat") + 4 + 2048).to_bytes(4, "big") + b"mdat" + b"\x00" * 2048
        + (len(moov) + 4).to_bytes(4, "big") + moov)
    lib.attach_chapter_media(out.id, num="1", lang="", group="", src=src,
                             sidecar={"filename": "tail.mp4"})
    chapter = lib.get(out.id).chapters[0]
    assert chapter.codec == "hevc"
    assert chapter.faststart is False
    lib.close()
