"""Playable copies (design/playable-copies.md): an episode the window cannot
show gets a copy it can, beside the original, made in the background.

Most tests here replace the conversion with one that writes a known file, so
what they test is everything AROUND it: what gets a copy, where it lands, when
it may land, and what serves it. The few that run the shipped ffmpeg skip
where there is none."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.library import media, transcode
from app.library.copies import needs_copy
from app.library.models import DraftIn, TitleMeta, UserPatch
from app.library.service import Library
from app.main import create_app
from app.settings import get_settings

COPY = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"COPY" * 256


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def ffmpeg(monkeypatch):
    """An ffmpeg that is there, and a conversion that writes COPY."""
    calls: list[Path] = []

    def convert(src: Path, dst: Path, *, progress, stop) -> dict:
        calls.append(src)
        progress(0.5)
        dst.write_bytes(COPY)
        return {"codec": "h264", "audio": "aac", "remuxed": False}

    monkeypatch.setattr(transcode, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(transcode, "convert", convert)
    return calls


def _mov(path: Path) -> Path:
    path.write_bytes(b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  " + b"\x00" * 4096)
    return path


def _hevc_mp4(path: Path, profile_idc: int) -> Path:
    """An mp4 whose `hvcC` names a profile — 1 Main, 2 Main10, 4 Range Extensions."""
    hvcc = b"hvcC" + bytes([1, profile_idc]) + b"\x00" * 21
    moov = b"moov" + b"\x00" * 4 + b"hev1" + b"\x00" * 32 + (len(hvcc) + 4).to_bytes(4, "big") + hvcc
    path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
                     + (len(moov) + 4).to_bytes(4, "big") + moov + b"\x00" * 64)
    return path


def _episode(lib: Library, src: Path) -> tuple[str, str]:
    out = lib.create(DraftIn(meta=TitleMeta(title="Series", type="anime")))
    lib.attach_chapter_media(out.id, num="1", lang="EN", group="", src=src,
                             sidecar={"filename": src.name})
    return out.id, lib.get(out.id).chapters[0].id


def _side(lib: Library, tid: str, cid: str) -> dict:
    return lib.vault.chapter_sidecar(tid, cid) or {}


def _run_all(lib: Library) -> None:
    """Drive the queue the way its worker does, here and now."""
    for job in [j for j in lib.copies._jobs.values() if j.state == "queued"]:
        job.state = "running"
        lib.copies.work(job)


def test_hevc_beyond_main10_is_known_not_to_play_before_anyone_opens_it(tmp_path):
    """Chromium decodes HEVC only through the graphics, and Range Extensions
    (4:4:4) on almost none — the sound then plays over black, with no error.
    The profile is in the file's own header."""
    assert media.probe_video(_hevc_mp4(tmp_path / "rext.mp4", 4))["playable"] is False
    assert media.probe_video(_hevc_mp4(tmp_path / "main.mp4", 1))["playable"] is True
    assert media.probe_video(_hevc_mp4(tmp_path / "main10.mp4", 2))["profile"] == "main10"


def test_an_episode_the_window_cannot_show_gets_a_copy_beside_it(tmp_path, ffmpeg):
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    before = lib.get(tid).chapters[0]
    assert before.playable is False and before.playableCopy is False
    assert [j["state"] for j in lib.copies.jobs()] == ["queued"]  # offered at ingest

    _run_all(lib)
    chapter = lib.get(tid).chapters[0]
    assert chapter.playable is True and chapter.playableCopy is True
    assert chapter.codec == "h264" and chapter.v != before.v
    assert lib.copies.jobs() == []
    original = lib.vault.chapter_media_path(tid, cid)
    assert original is not None and original.suffix == ".mov"  # never replaced
    assert lib.vault.chapter_copy_path(tid, cid).read_bytes() == COPY

    with TestClient(create_app(lib)) as c:
        served = c.get(f"/api/titles/{tid}/chapters/{cid}/video", headers={"Range": "bytes=0-"})
        assert served.content == COPY and served.headers["content-type"] == "video/mp4"
    lib.close()


def test_a_copy_is_never_taken_for_the_episode_and_goes_with_it(tmp_path, ffmpeg):
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    _run_all(lib)

    report = lib.health.verify()
    assert not [f for f in report["findings"] if f["kind"] in ("stray", "orphan")]
    assert lib.vault.chapter_files(tid)[cid].suffix == ".mov"

    # a new file for the entry: the copy was of the old one
    lib.attach_chapter_media(tid, num="1", lang="EN", group="", chapter_id=cid,
                             src=_mov(tmp_path / "again.mov"), sidecar={"filename": "again.mov"})
    assert not lib.vault.chapter_copy_path(tid, cid).exists()
    assert "copy" not in _side(lib, tid, cid)
    assert lib.get(tid).chapters[0].playable is False

    _run_all(lib)
    lib.vault.delete_chapter_media(tid, cid)
    assert not lib.vault.chapter_copy_path(tid, cid).exists()
    lib.close()


def test_a_copy_of_bytes_replaced_meanwhile_never_lands(tmp_path, ffmpeg, monkeypatch):
    """The conversion takes minutes with no lock held. A re-download that lands
    in between wins, and the copy made of what it replaced is thrown away — the
    new bytes get one of their own."""
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    plain = transcode.convert

    def replaced_meanwhile(src: Path, dst: Path, **kw) -> dict:
        lib.attach_chapter_media(tid, num="1", lang="EN", group="", chapter_id=cid,
                                 src=_mov(tmp_path / "new.mov"), sidecar={"filename": "new.mov"})
        return plain(src, dst, **kw)

    monkeypatch.setattr(transcode, "convert", replaced_meanwhile)
    _run_all(lib)
    assert not lib.vault.chapter_copy_path(tid, cid).exists()
    assert "copy" not in _side(lib, tid, cid)
    assert not list(lib.vault.chapters_dir(tid).glob("*.tmp"))
    assert [j["state"] for j in lib.copies.jobs()] == ["queued"]  # the new bytes, offered again
    lib.close()


def test_a_copy_that_cannot_be_made_is_not_tried_again_on_its_own(tmp_path, ffmpeg, monkeypatch):
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))

    def broken(src: Path, dst: Path, **kw) -> dict:
        dst.write_bytes(b"half")
        raise transcode.ConvertError("Invalid data found when processing input")

    monkeypatch.setattr(transcode, "convert", broken)
    _run_all(lib)
    [job] = lib.copies.jobs()
    assert job["state"] == "failed" and "Invalid data" in job["error"]
    assert not needs_copy(_side(lib, tid, cid))
    assert not list(lib.vault.chapters_dir(tid).glob("*.tmp"))
    lib.close()

    again = Library(tmp_path / "v", defer_sync=True)  # a later launch
    again.sync_in_background()
    again._sync_thread.join(timeout=15)
    assert again.copies.jobs() == []
    assert again.copies.request(tid, cid) is not None  # the human can still ask
    again.close()


def test_a_copy_stopped_halfway_leaves_nothing_behind(tmp_path, ffmpeg, monkeypatch):
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    [job] = lib.copies._jobs.values()

    def stopped(src: Path, dst: Path, *, progress, stop: threading.Event) -> dict:
        dst.write_bytes(b"half")
        lib.copies._running = job
        lib.copies.drop(job.id)
        assert stop.is_set()
        raise transcode.Cancelled()

    monkeypatch.setattr(transcode, "convert", stopped)
    job.state = "running"
    lib.copies.work(job)
    assert lib.copies.jobs() == []
    assert not list(lib.vault.chapters_dir(tid).glob("*.tmp"))
    assert needs_copy(_side(lib, tid, cid))  # nothing was decided about the file
    lib.close()


def test_a_window_that_decodes_no_picture_asks_for_a_copy(tmp_path, ffmpeg):
    """What the file says about itself can be right and the machine still
    unable to draw it (HEVC Main with no decoder for it). The window is the one
    that knows, and it asks."""
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _hevc_mp4(tmp_path / "ep.mp4", 1))
    assert lib.copies.jobs() == []
    with TestClient(create_app(lib)) as c:
        job = c.post(f"/api/titles/{tid}/chapters/{cid}/copy").json()
        assert job["state"] == "queued" and job["label"] == "1 · EN"
        assert c.get("/api/copies").json()["jobs"][0]["id"] == job["id"]
        _run_all(lib)
        assert c.get(f"/api/titles/{tid}").json()["chapters"][0]["playableCopy"] is True
    lib.close()


def test_without_ffmpeg_nothing_is_queued_and_the_window_is_told(tmp_path, monkeypatch):
    monkeypatch.setattr(transcode, "ffmpeg", lambda: None)
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    assert lib.copies.jobs() == []
    with TestClient(create_app(lib)) as c:
        assert c.get("/api/copies").json() == {"available": False, "jobs": [], "landed": []}
        assert c.post(f"/api/titles/{tid}/chapters/{cid}/copy").status_code == 409
    lib.close()


def test_a_library_that_has_verified_itself_queues_what_still_needs_a_copy(tmp_path, ffmpeg):
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    lib.close()
    again = Library(tmp_path / "v", defer_sync=True)
    assert again.copies.jobs() == []  # nothing on the way to the first paint
    again.sync_in_background()
    again._sync_thread.join(timeout=15)
    assert [(j["titleId"], j["chapterId"]) for j in again.copies.jobs()] == [(tid, cid)]
    again.close()


def test_ffmpeg_output_is_read_for_the_picture_not_the_cover_art():
    held = transcode.parse_streams("""
  Duration: 00:01:33.78, start: 0.000000, bitrate: 19466 kb/s
  Stream #0:0[0x1](und): Video: mjpeg (Baseline), yuvj420p(pc), 600x600 (attached pic)
  Stream #0:1[0x2](und): Video: hevc (Rext) (hev1 / 0x31766568), yuv444p(tv, bt709), 3840x2160, 30 fps
  Stream #0:2[0x3](eng): Audio: ac3, 48000 Hz, 5.1(side), fltp, 448 kb/s (default)
""")
    assert held == transcode.Streams(93.78, "hevc", "yuv444p", "ac3")


def test_the_episode_sidecar_records_the_copy_it_was_made_into(tmp_path, ffmpeg):
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    _run_all(lib)
    side = json.loads((lib.vault.chapters_dir(tid) / f"{cid}.json").read_text(encoding="utf-8"))
    made = side["copy"].pop("made")
    assert side["copy"] == {"codec": "h264", "audio": "aac", "remuxed": False, "size": len(COPY)}
    assert lib.get(tid).chapters[0].v.endswith(f".c{len(COPY)}.{made}")
    assert side["playable"] is False  # the original still does not play — the copy does
    lib.close()


def test_a_stored_4_4_4_episode_is_found_by_the_pass_and_queued(tmp_path, ffmpeg):
    """Episodes stored before the profile was read say they play. The episode
    pass re-reads every HEVC mp4 once, and what it finds goes to the queue."""
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _hevc_mp4(tmp_path / "ep.mp4", 4))
    side_path = lib.vault.chapters_dir(tid) / f"{cid}.json"
    side = json.loads(side_path.read_text(encoding="utf-8"))
    side["playable"] = True
    del side["profile"]
    side_path.write_text(json.dumps(side), encoding="utf-8")
    lib.copies._jobs.clear()
    lib._sidecar_cache.clear()
    lib._index(tid, lib.vault.load(tid))
    assert lib.get(tid).chapters[0].playable is True and lib.copies.jobs() == []

    assert lib.refresh_episodes() == 1
    assert lib.get(tid).chapters[0].playable is False
    assert [j["chapterId"] for j in lib.copies.jobs()] == [cid]
    lib.close()


@pytest.mark.skipif(transcode.ffmpeg() is None, reason="no ffmpeg on this machine")
def test_a_real_ffmpeg_makes_a_copy_the_window_can_play(tmp_path):
    """The one test that runs the shipped ffmpeg: an mkv of MPEG-4 Part 2 with
    AC-3 sound — neither of which a browser decodes — becomes H.264 with AAC,
    index in front, beside the untouched original."""
    import subprocess

    src = tmp_path / "ep.mkv"
    subprocess.run([transcode.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=321x241:rate=25:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:v", "mpeg4", "-c:a", "ac3", str(src)], check=True)
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, src)
    assert lib.get(tid).chapters[0].playable is False
    _run_all(lib)

    chapter = lib.get(tid).chapters[0]
    assert chapter.playableCopy is True and (chapter.codec, chapter.audio) == ("h264", "aac")
    copy = lib.vault.chapter_copy_path(tid, cid)
    assert media.probe_video(copy) == {"container": "mp4", "playable": True, "codec": "h264",
                                       "faststart": True, "profile": "high"}
    assert transcode.inspect(copy).pix_fmt == "yuv420p"  # and even-sized: 320x240
    assert lib.vault.chapter_media_path(tid, cid).suffix == ".mkv"
    lib.close()


def test_closing_the_app_mid_copy_stops_it_and_the_next_launch_starts_over(tmp_path, ffmpeg, monkeypatch):
    """Nothing about the file was decided, so nothing is remembered against it:
    the conversion is simply done again from the start next time."""
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    [job] = lib.copies._jobs.values()

    def closed_meanwhile(src: Path, dst: Path, *, progress, stop: threading.Event) -> dict:
        dst.write_bytes(b"half")
        lib.copies.close()  # what Library.close does first
        assert stop.is_set()
        raise transcode.Cancelled()

    monkeypatch.setattr(transcode, "convert", closed_meanwhile)
    job.state = "running"
    lib.copies.work(job)
    assert not list(lib.vault.chapters_dir(tid).glob("*.tmp"))
    lib.close()

    again = Library(tmp_path / "v", defer_sync=True)
    again.sync_in_background()
    again._sync_thread.join(timeout=15)
    assert [j["chapterId"] for j in again.copies.jobs()] == [cid]
    again.close()


def test_what_a_copy_cut_off_by_a_hard_stop_left_is_cleared_by_the_next_one(tmp_path, ffmpeg):
    """A sidecar killed outright runs no shutdown at all: its ffmpeg dies with it
    and the half-written copy stays. The next attempt at that episode clears it."""
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    left = media.tmp_path(lib.vault.chapter_copy_path(tid, cid))
    left.write_bytes(b"cut off")
    _run_all(lib)
    assert not left.exists()
    assert lib.vault.chapter_copy_path(tid, cid).read_bytes() == COPY
    lib.close()


@pytest.mark.skipif(os.name != "nt" or transcode.ffmpeg() is None, reason="Windows, with ffmpeg")
def test_an_ffmpeg_dies_with_the_process_that_started_it_however_that_ends(tmp_path):
    """The shell stops the sidecar with TerminateProcess. A Windows child outlives
    that unless something ties them — and a conversion would go on loading the
    GPU and writing into the vault after the app is gone."""
    import subprocess
    import sys
    import time

    child = tmp_path / "child.py"
    child.write_text(
        "import subprocess, sys, time\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from app.library import transcode\n"
        "p = subprocess.Popen([transcode.ffmpeg(), '-nostdin', '-re', '-f', 'lavfi', '-i',"
        " 'testsrc=size=160x120:rate=10', '-f', 'null', '-'], stdout=subprocess.DEVNULL,"
        " stderr=subprocess.DEVNULL, **transcode._SPAWN)\n"
        "transcode._tie_to_this_process(p)\n"
        "print(p.pid, flush=True)\n"
        "time.sleep(60)\n", encoding="utf-8")
    parent = subprocess.Popen([sys.executable, str(child)], stdout=subprocess.PIPE, text=True,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    pid = int(parent.stdout.readline())
    parent.kill()
    parent.wait()
    for _ in range(30):
        listed = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                capture_output=True, text=True).stdout
        if str(pid) not in listed:
            return
        time.sleep(0.1)
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    pytest.fail("ffmpeg outlived the process that started it")


# ---- what the independent review found ----

def _mkv_with(path: Path, codec_id: str, private: bytes) -> Path:
    """A Matroska file whose one video track carries a decoder configuration."""
    from .test_video import _ebml
    entry = (_ebml(0xD7, b"\x01") + _ebml(0x83, b"\x01") + _ebml(0x86, codec_id.encode())
             + _ebml(0x63A2, private))
    tracks = _ebml(0x1654AE6B, _ebml(0xAE, entry))
    body = _ebml(0x1549A966, b"") + tracks + _ebml(0x1F43B675, b"\x00" * 64)
    path.write_bytes(_ebml(0x1A45DFA3, _ebml(0x4282, b"matroska")) + _ebml(0x18538067, body))
    return path


def test_high_10_h264_is_known_not_to_play_in_an_mkv_or_an_mp4(tmp_path):
    """The usual anime encode: the browser's H.264 decoder stops at 8-bit."""
    hi10 = media.probe_video(_mkv_with(tmp_path / "a.mkv", "V_MPEG4/ISO/AVC", bytes([1, 110, 0, 30])))
    assert (hi10["codec"], hi10["profile"], hi10["playable"]) == ("h264", "high10", False)
    high = media.probe_video(_mkv_with(tmp_path / "b.mkv", "V_MPEG4/ISO/AVC", bytes([1, 100, 0, 30])))
    assert high["playable"] is True
    avcc = b"avcC" + bytes([1, 110, 0, 30])
    moov = b"moov" + b"\x00" * 4 + b"avc1" + b"\x00" * 32 + (len(avcc) + 4).to_bytes(4, "big") + avcc
    mp4 = tmp_path / "c.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
                    + (len(moov) + 4).to_bytes(4, "big") + moov)
    assert media.probe_video(mp4)["playable"] is False


@pytest.mark.migrations
def test_no_copy_starts_on_what_an_older_build_said_before_the_pass_corrects_it(tmp_path, ffmpeg):
    """Older builds called every mkv unplayable. Copies wait for the pass, and a
    job queued off the old word is dropped once the file says otherwise."""
    from .conftest import settled
    from .test_video import _as_an_older_build_left_it, _mkv

    root = tmp_path / "v"
    lib = settled(Library(root))
    tid, cid = _episode(lib, _mkv(tmp_path / "ep.mkv", (1, "V_AV1"), (2, "A_OPUS")))
    _as_an_older_build_left_it(lib, tid, cid)
    assert [j["chapterId"] for j in lib.copies.jobs()] == [cid]  # queued off the old word
    lib.refresh_episodes()
    _run_all(lib)
    assert ffmpeg == [] and lib.copies.jobs() == []
    assert not lib.vault.chapter_copy_path(tid, cid).exists()
    _as_an_older_build_left_it(lib, tid, cid)
    lib.close()

    (root / "vault.json").write_text(json.dumps({"zipNormalized": 1, "videoFaststart": 1}),
                                     encoding="utf-8")
    again = Library(root)
    assert again.copies._held is True
    settled(again)
    assert again.copies._held is False and again.copies.jobs() == []
    again.close()


def test_a_copy_lets_go_of_the_file_before_it_is_deleted_or_replaced(tmp_path, ffmpeg, monkeypatch):
    """On Windows a running ffmpeg holds the original without delete-sharing:
    a delete, a re-download or a move would fail half-way through."""
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    [job] = lib.copies._jobs.values()
    started = threading.Event()
    seen: list[bool] = []

    def held_open(src: Path, dst: Path, *, progress, stop: threading.Event) -> dict:
        started.set()
        seen.append(stop.wait(5))
        raise transcode.Cancelled()

    monkeypatch.setattr(transcode, "convert", held_open)
    job.state, lib.copies._running = "running", job
    worker = threading.Thread(target=lib.copies.work, args=(job,))
    worker.start()
    assert started.wait(5)
    lib.delete_chapter_media(tid, cid)  # waits for the process, then deletes
    worker.join(5)
    assert seen == [True]
    assert lib.vault.chapter_media_path(tid, cid) is None
    assert lib.copies.jobs() == []
    lib.close()


def test_one_episode_that_breaks_the_worker_does_not_stop_the_queue(tmp_path, ffmpeg, monkeypatch):
    lib = Library(tmp_path / "v")
    first, _ = _episode(lib, _mov(tmp_path / "a.mov"))
    second, cid = _episode(lib, _mov(tmp_path / "b.mov"))
    plain = transcode.convert

    def one_breaks(src: Path, dst: Path, **kw) -> dict:
        if src.parent.parent.name == first:
            raise RuntimeError("unexpected")
        return plain(src, dst, **kw)

    monkeypatch.setattr(transcode, "convert", one_breaks)
    lib.copies._held = False  # the one-time pass these tests never run
    lib.copies._loop()  # the worker itself, here and now
    assert [(j["titleId"], j["state"]) for j in lib.copies.jobs()] == [(first, "failed")]
    assert lib.vault.chapter_copy_path(second, cid).is_file()
    assert lib.copies._thread is None  # the next job can start a worker again
    lib.close()


def test_a_cancelled_copy_stays_cancelled_until_asked_for(tmp_path, ffmpeg):
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    [job] = lib.copies.jobs()
    lib.copies.drop(job["id"])
    lib.patch_user(tid, UserPatch(fav=True))  # any write of the title offers its episodes
    assert lib.copies.jobs() == []
    assert lib.copies.request(tid, cid) is not None
    assert [j["state"] for j in lib.copies.jobs()] == ["queued"]
    lib.close()


def test_new_bytes_are_not_held_back_by_a_failure_of_the_old_ones(tmp_path, ffmpeg, monkeypatch):
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))

    def broken(src: Path, dst: Path, **kw) -> dict:
        raise transcode.ConvertError("Invalid data found when processing input")

    monkeypatch.setattr(transcode, "convert", broken)
    _run_all(lib)
    assert [j["state"] for j in lib.copies.jobs()] == ["failed"]
    lib.attach_chapter_media(tid, num="1", lang="EN", group="", chapter_id=cid,
                             src=_mov(tmp_path / "good.mov"), sidecar={"filename": "good.mov"})
    [job] = lib.copies.jobs()
    assert job["state"] == "queued" and job["error"] == ""
    lib.close()


def test_a_copy_that_already_landed_is_not_made_again(tmp_path, ffmpeg):
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    _run_all(lib)
    assert [x["titleId"] for x in lib.copies.state()["landed"]] == [tid]
    with TestClient(create_app(lib)) as c:
        assert c.post(f"/api/titles/{tid}/chapters/{cid}/copy").json() == {"state": "landed"}
    assert len(ffmpeg) == 1
    lib.close()


def test_a_remux_ffmpeg_refuses_is_made_by_encoding_instead(tmp_path, monkeypatch):
    """An avi or ts with broken timestamps will not re-container; the picture
    is then encoded rather than the file given up on."""
    ran: list[list[str]] = []

    def run(cmd: list[str], *rest) -> tuple[int, str]:
        ran.append(cmd)
        return (1, "non-monotonic DTS") if len(ran) == 1 else (0, "")

    monkeypatch.setattr(transcode, "ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(transcode, "inspect", lambda src: transcode.Streams(10.0, "h264", "yuv420p", "mp3"))
    monkeypatch.setattr(transcode, "encoders", lambda: (transcode.Encoder("libx264", ()),))
    monkeypatch.setattr(transcode, "_run", run)
    held = transcode.convert(tmp_path / "ep.avi", tmp_path / "out", progress=lambda f: None,
                             stop=threading.Event())
    assert held == {"codec": "h264", "audio": "mp3", "remuxed": False}
    assert ran[0][ran[0].index("-c:v") + 1] == "copy" and "libx264" in ran[1]


def test_the_copy_being_written_is_not_reported_as_a_leftover(tmp_path, ffmpeg):
    lib = Library(tmp_path / "v")
    tid, cid = _episode(lib, _mov(tmp_path / "ep.mov"))
    writing = media.tmp_path(lib.vault.chapter_copy_path(tid, cid))
    writing.write_bytes(b"half")
    assert [f["name"] for f in lib.health.verify()["findings"] if f["kind"] == "stray"] == [writing.name]
    lib.copies._writing = writing
    assert not [f for f in lib.health.verify()["findings"] if f["kind"] == "stray"]
    lib.close()


def test_a_matroska_file_described_by_its_tracks_is_not_reopened_by_a_later_pass():
    from app.library.vault import _episode_may_be_stale

    described = {"kind": "video", "container": "mkv", "codec": "av1", "audio": "opus", "faststart": True}
    assert not _episode_may_be_stale(described)
    assert _episode_may_be_stale({k: v for k, v in described.items() if k != "audio"})
    assert _episode_may_be_stale({**described, "codec": "h264"})  # its profile is not known yet
    assert not _episode_may_be_stale({**described, "codec": "h264", "profile": "high"})
    # version 4: PCM sound plays, and the pass reads it again only where it was called unplayable
    assert _episode_may_be_stale({**described, "audio": "pcm", "playable": False})
    assert not _episode_may_be_stale({**described, "audio": "pcm", "playable": True})
