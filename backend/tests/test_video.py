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
from app.library.models import ChapterRow, DraftIn, TitleMeta
from app.library.service import Library
from app.main import create_app
from app.settings import get_settings
from .conftest import settled


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


JPEG = b"\xff\xd8\xff"  # the bytes that make a still a still




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


def _box(name: bytes, payload: bytes) -> bytes:
    return (len(payload) + 8).to_bytes(4, "big") + name + payload


def _index_last_mp4(path: Path, payload: bytes, *, fragmented: bool = False,
                    stray_offset: bool = False) -> Path:
    """An mp4 shaped the way a muxer leaves one: media first, index behind it,
    and a chunk offset that is an ABSOLUTE position inside the media."""
    ftyp = _box(b"ftyp", b"isomiso2")
    mdat = _box(b"mdat", payload)
    first_sample = 0 if stray_offset else len(ftyp) + 8
    stco = _box(b"stco", b"\x00" * 4 + (1).to_bytes(4, "big")
                + first_sample.to_bytes(4, "big"))
    moov = _box(b"moov", _box(b"trak", _box(b"mdia", _box(b"minf", _box(b"stbl", stco)))))
    parts = [ftyp, mdat] + ([_box(b"moof", b"")] if fragmented else []) + [moov]
    path.write_bytes(b"".join(parts))
    return path


def _stco_entry(raw: bytes) -> int:
    at = raw.index(b"stco") + 4 + 4 + 4  # past the name, version+flags, the count
    return int.from_bytes(raw[at:at + 4], "big")


def test_the_index_moves_in_front_and_the_media_moves_with_it(tmp_path):
    """The rewrite is a rearrangement, not a re-encode: same bytes, same
    length. What makes it delicate is that every chunk offset is an absolute
    file position, so the index has to be corrected as the media slides."""
    src = _index_last_mp4(tmp_path / "tail.mp4", b"\xab" * 4096)
    before = src.stat().st_size

    assert media.probe_mp4(src)["faststart"] is False
    assert media.remux_faststart(src) is True
    assert src.stat().st_size == before
    assert media.probe_mp4(src)["faststart"] is True

    raw = src.read_bytes()
    assert raw.index(b"moov") < raw.index(b"mdat")
    # the offset still lands on the first sample, not where it used to be
    assert raw[_stco_entry(raw):_stco_entry(raw) + 4] == b"\xab" * 4


def test_a_file_the_rewrite_cannot_prove_it_understands_is_left_alone(tmp_path):
    """Refusing costs a slow start. Guessing costs the episode."""
    fragmented = _index_last_mp4(tmp_path / "frag.mp4", b"\xab" * 512, fragmented=True)
    stray = _index_last_mp4(tmp_path / "stray.mp4", b"\xab" * 512, stray_offset=True)
    for path in (fragmented, stray):
        before = path.read_bytes()
        assert media.remux_faststart(path) is False
        assert path.read_bytes() == before

    # nothing to do for a file that already leads with its index
    done = _index_last_mp4(tmp_path / "done.mp4", b"\xab" * 512)
    assert media.remux_faststart(done) is True
    assert media.remux_faststart(done) is False


def test_an_arriving_episode_is_stored_ready_to_play(tmp_path):
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Series", type="anime")))
    lib.attach_chapter_media(out.id, num="1", lang="", group="",
                             src=_index_last_mp4(tmp_path / "ep.mp4", b"\xab" * 8192),
                             sidecar={"filename": "ep.mp4"})
    assert lib.get(out.id).chapters[0].faststart is True
    lib.close()


@pytest.mark.migrations
def test_episodes_already_in_the_vault_are_fixed_once(tmp_path):
    """What was stored before the app could do this gets a one-time pass. The
    bytes are rearranged without changing their number, so the media version
    would REPEAT — and a repeated version is what serves a stale range back."""
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Series")))
    lib.attach_chapter_media(out.id, num="1", lang="", group="",
                             src=_mp4(tmp_path / "ep.mp4"), sidecar={"filename": "ep.mp4"})
    cid = lib.get(out.id).chapters[0].id

    # plant the pre-invariant shape under the stored name
    stored = lib.vault.chapter_media_path(out.id, cid)
    _index_last_mp4(stored, b"\xab" * 4096)
    lib._sidecar_cache.clear()
    lib._index(out.id, lib.vault.load(out.id))
    before = lib.get(out.id).chapters[0].v

    # the pass is consumed when a vault is first opened; this one is the
    # re-run, the way Settings would ask for it
    assert settled(lib).vault.needs_faststart() is False
    assert lib.refresh_episodes() == 1

    chapter = lib.get(out.id).chapters[0]
    assert chapter.faststart is True
    assert chapter.v != before                        # the version cannot repeat
    assert lib.refresh_episodes() == 0
    lib.close()


def test_a_container_the_app_cannot_play_says_so_before_it_is_opened(tmp_path):
    """Storing it is right — the vault is an archive, not a player. Letting the
    human find out by clicking is not."""
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Series")))
    mkv = tmp_path / "ep.mkv"
    mkv.write_bytes(bytes((0x1A, 0x45, 0xDF, 0xA3)) + b"\x00" * 2048)  # EBML
    lib.attach_chapter_media(out.id, num="1", lang="", group="", src=mkv,
                             sidecar={"filename": "ep.mkv"})

    chapter = lib.get(out.id).chapters[0]
    assert chapter.kind == "video" and chapter.dl is True
    assert chapter.playable is False and chapter.container == "mkv"
    lib.close()


def test_learning_what_a_stored_episode_is_does_not_invalidate_it(tmp_path):
    """The one-time pass describes the same bytes. Bumping the version for that
    would throw away every cache of a file that did not change."""
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Series")))
    lib.attach_chapter_media(out.id, num="1", lang="", group="",
                             src=_mp4(tmp_path / "ep.mp4"), sidecar={"filename": "ep.mp4"})
    cid = lib.get(out.id).chapters[0].id

    side_path = lib.vault.chapters_dir(out.id) / f"{cid}.json"
    side = json.loads(side_path.read_text(encoding="utf-8"))
    del side["playable"], side["container"]
    side_path.write_text(json.dumps(side), encoding="utf-8")
    lib._sidecar_cache.clear()
    lib._index(out.id, lib.vault.load(out.id))
    before = lib.get(out.id).chapters[0].v

    assert lib.refresh_episodes() == 1
    chapter = lib.get(out.id).chapters[0]
    assert chapter.container == "mp4" and chapter.playable is True
    assert chapter.v == before                    # the bytes never moved
    assert lib.refresh_episodes() == 0            # and nothing is left to do
    lib.close()


def test_cutting_a_still_never_re_versions_the_episode(tmp_path):
    """A poster describes the same bytes it was cut from. If storing one bumped
    the MEDIA revision, the video URL would change under a player that is very
    likely running right then — the tile would refresh by restarting the
    episode. Stills carry a version of their own."""
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Ep"), chapters=[ChapterRow(id="c1", num="1")]))
    lib.attach_chapter_media(out.id, num="1", lang="", group="",
                             src=_mp4(tmp_path / "ep.mp4"),
                             sidecar={"filename": "ep.mp4", "duration": 60.0})
    before = next(c for c in lib.get(out.id).chapters if c.id == "c1")

    assert lib.save_chapter_frames(out.id, "c1", "poster", JPEG + b"frame")
    after = next(c for c in lib.get(out.id).chapters if c.id == "c1")
    assert after.v == before.v            # the media did not move
    assert after.stills != before.stills  # the stills did
    assert (after.poster, after.sheet) == (True, "")

    assert lib.save_chapter_frames(out.id, "c1", "sheet", JPEG + b"grid", grid="3x3")
    third = next(c for c in lib.get(out.id).chapters if c.id == "c1")
    assert third.v == before.v
    assert third.stills != after.stills
    lib.close()


def test_a_locked_episode_leaves_its_chapter_whole(tmp_path, monkeypatch):
    """Windows will not unlink a file the player still streams. The delete then
    has to leave the chapter EXACTLY as it was — the first attempt deleted in
    glob order, took the sidecar out first and left entries that had lost every
    fact about themselves while their video sat there untouched."""
    from app.library import vault as vault_mod

    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Ep"), chapters=[ChapterRow(id="c1", num="1")]))
    lib.attach_chapter_media(out.id, num="1", lang="", group="",
                             src=_mp4(tmp_path / "ep.mp4"),
                             sidecar={"filename": "ep.mp4", "duration": 60.0})
    side = lib.vault.chapter_sidecars(out.id)["c1"]

    real_unlink = Path.unlink

    def held(self, **kw):
        if self.suffix == ".mp4":
            raise PermissionError(32, "in use")
        return real_unlink(self, **kw)

    monkeypatch.setattr(Path, "unlink", held)
    monkeypatch.setattr(vault_mod.time, "sleep", lambda _s: None)

    with TestClient(create_app(lib)) as c:
        r = c.delete(f"/api/titles/{out.id}/chapters/c1")
        assert r.status_code == 409
        assert "open right now" in r.json()["detail"]

    monkeypatch.undo()
    assert lib.vault.chapter_media_path(out.id, "c1").suffix == ".mp4"
    assert lib.vault.chapter_sidecars(out.id)["c1"] == side  # nothing was taken
    assert [ch.num for ch in lib.get(out.id).chapters] == ["1"]
    lib.close()


def test_episode_stills_are_stored_in_the_vault_and_served_back(tmp_path):
    """The app has no decoder, so the WINDOW cuts the frames — but it does it
    once: both stills land in the vault beside the episode, survive a reopen,
    and are never confused for the media itself."""
    lib = Library(tmp_path / "v")
    out = lib.create(DraftIn(meta=TitleMeta(title="Ep"), chapters=[ChapterRow(id="c1", num="1")]))
    lib.attach_chapter_media(out.id, num="1", lang="", group="",
                             src=_mp4(tmp_path / "ep.mp4"),
                             sidecar={"filename": "ep.mp4", "duration": 60.0})

    jpeg = JPEG + b"frame" * 32
    sheet = JPEG + b"grid" * 64
    with TestClient(create_app(lib)) as c:
        base = f"/api/titles/{out.id}/chapters/c1/frames"
        assert c.get(f"{base}/poster").status_code == 404      # nothing cut yet
        assert c.put(f"{base}/poster", content=b"not a jpeg").status_code == 400
        assert c.put(f"{base}/nonsense", content=jpeg).status_code == 400

        saved = c.put(f"{base}/poster", content=jpeg).json()
        row = next(ch for ch in saved["chapters"] if ch["id"] == "c1")
        assert (row["poster"], row["sheet"]) == (True, "")
        # a sheet is only readable by a build that slices it the way it was cut,
        # so it carries its grid and a build expecting another one re-cuts it
        saved = c.put(f"{base}/sheet?grid=3x3", content=sheet).json()
        assert next(ch for ch in saved["chapters"] if ch["id"] == "c1")["sheet"] == "3x3"

        assert c.get(f"{base}/poster").content == jpeg
        assert c.get(f"{base}/sheet").content == sheet
        # the tile asks for a downscaled one, and THAT is what was 404-ing: the
        # cache key becomes a file name, so it may not carry a colon
        assert c.get(f"{base}/poster", params={"w": 240}).status_code == 200
    lib.close()

    # they are files in the vault, and NEITHER is the chapter's media
    assert lib.vault.chapter_frames_path(out.id, "c1", "poster").is_file()
    assert lib.vault.chapter_frames_path(out.id, "c1", "sheet").is_file()
    again = Library(tmp_path / "v")
    try:
        assert again.vault.chapter_media_path(out.id, "c1").suffix == ".mp4"
        assert again.chapter_frames(out.id, "c1", "poster")[0] == jpeg
        assert again.chapter_frames(out.id, "c1", "sheet")[0] == sheet
    finally:
        again.close()
