"""The second media kind: an episode is the file itself, not a zip of pages.

Everything a chapter already is — identity, provenance, translations, progress
— stays the same; only how the media is stored and read differs.
"""
from __future__ import annotations

import json
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


def test_an_older_import_learns_its_codec_at_first_play(tmp_path):
    """Episodes stored before the app looked inside them carry no codec. That
    is filled in at first play — not on a listing, which touches no files, and
    not on the serving path."""
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Series")))
    moov = b"moov" + b"\x00" * 4 + b"hvc1" + b"\x00" * 64
    src = tmp_path / "ep.mp4"
    src.write_bytes(
        b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
        + (8 + 2048).to_bytes(4, "big") + b"mdat" + b"\x00" * 2048
        + (len(moov) + 4).to_bytes(4, "big") + moov)
    lib.attach_chapter_media(out.id, num="1", lang="", group="", src=src,
                             sidecar={"filename": "ep.mp4"})
    cid = lib.get(out.id).chapters[0].id

    side_path = lib.vault.chapters_dir(out.id) / f"{cid}.json"
    side = json.loads(side_path.read_text(encoding="utf-8"))
    del side["codec"], side["faststart"]
    side_path.write_text(json.dumps(side), encoding="utf-8")
    lib._sidecar_cache.clear()
    lib._index(out.id, lib.vault.load(out.id))
    assert lib.get(out.id).chapters[0].codec == ""

    with TestClient(create_app(lib)) as c:
        c.post(f"/api/titles/{out.id}/chapters/{cid}/video/meta", json={"duration": 89.1})
        chapter = c.get(f"/api/titles/{out.id}").json()["chapters"][0]
        assert chapter["codec"] == "hevc" and chapter["faststart"] is False
    lib.close()


def test_an_episode_is_not_pushed_into_the_browser_cache(tmp_path):
    """Episodes are streamed by range off a disk the app owns. Storing one in
    the browser cache evicts every cover and page preview — the things a cache
    that size is actually for — and churns on the disk the stream reads."""
    lib, tid, cid = _title_with_episode(tmp_path / "v", tmp_path)
    with TestClient(create_app(lib)) as c:
        r = c.get(f"/api/titles/{tid}/chapters/{cid}/video")
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["accept-ranges"] == "bytes"   # seeking still works
    lib.close()


def test_an_open_ended_ask_is_answered_with_a_window(tmp_path):
    """`bytes=N-` is not a promise to read to the end: the player takes a slice
    and drops the connection. Committing to the whole tail costs a reconnect and
    a fresh open() per fragment, and throws away everything already read."""
    from app.routers.library import VIDEO_WINDOW

    big = tmp_path / "big.mp4"
    _mp4(big, b"\x2a" * (VIDEO_WINDOW + 4096))
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Series", type="anime")))
    lib.attach_chapter_media(out.id, num="1", lang="", group="", src=big,
                             sidecar={"filename": "big.mp4"})
    cid = lib.get(out.id).chapters[0].id
    size = lib.vault.chapter_media_path(out.id, cid).stat().st_size

    with TestClient(create_app(lib)) as c:
        url = f"/api/titles/{out.id}/chapters/{cid}/video"
        r = c.get(url, headers={"Range": "bytes=0-"})
        assert r.status_code == 206
        assert r.headers["content-range"] == f"bytes 0-{VIDEO_WINDOW - 1}/{size}"
        assert len(r.content) == VIDEO_WINDOW

        # the window never runs past the file
        near_end = size - 100
        r = c.get(url, headers={"Range": f"bytes={near_end}-"})
        assert r.headers["content-range"] == f"bytes {near_end}-{size - 1}/{size}"
        assert len(r.content) == 100

        # an explicit range is still answered exactly as asked
        r = c.get(url, headers={"Range": "bytes=10-19"})
        assert r.status_code == 206 and len(r.content) == 10
        assert r.headers["content-range"] == f"bytes 10-19/{size}"

        # so is a suffix ask — the tail is where a non-faststart index lives
        r = c.get(url, headers={"Range": "bytes=-50"})
        assert r.status_code == 206 and len(r.content) == 50
    lib.close()
