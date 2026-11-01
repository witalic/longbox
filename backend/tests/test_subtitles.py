"""An episode's subtitles, and the downloads nobody armed (design/subtitles.md)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.library import subtitles
from app.library.models import DraftIn, TitleMeta
from app.library.service import Library
from app.main import create_app
from app.settings import get_settings


@pytest.fixture(autouse=True)
def _clean_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


SRT = ("1\r\n00:00:01,000 --> 00:00:02,500\r\nHello <i>there</i>\r\n\r\n"
       "2\r\n00:00:03,000 --> 00:00:04,000\r\n{\\an8}Top line\r\n")
ASS = """[Script Info]
Title: x

[V4+ Styles]
Format: Name, Fontname
Style: Default,Arial

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:05.20,0:00:07.00,Default,,0,0,0,,{\\i1}You came back,\\Nfor the lantern?
Comment: 0,0:00:08.00,0:00:09.00,Default,,0,0,0,,not shown
Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\pos(10,10)}First & <last>
"""


def _mp4(path: Path) -> Path:
    path.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 4096)
    return path


# ---- what a subtitle file says ----

def test_srt_becomes_webvtt():
    vtt = subtitles.to_webvtt(SRT.encode(), ".srt")
    assert vtt.startswith("WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nHello <i>there</i>\n")
    assert "00:00:03.000 --> 00:00:04.000\nTop line" in vtt


def test_ass_becomes_plain_cues_in_time_order():
    vtt = subtitles.to_webvtt(ASS.encode(), ".ass")
    first = vtt.split("\n\n")[1]
    assert first == "00:00:01.000 --> 00:00:02.000\nFirst &amp; &lt;last&gt;"
    assert "00:00:05.200 --> 00:00:07.000\nYou came back,\nfor the lantern?" in vtt
    assert "not shown" not in vtt


def test_a_windows_cyrillic_srt_is_read():
    data = "1\n00:00:01,000 --> 00:00:02,000\nПривіт\n".encode("cp1251")
    assert "Привіт" in subtitles.to_webvtt(data, ".srt")


def test_a_file_with_no_cue_is_refused():
    with pytest.raises(subtitles.UnreadableSubtitle):
        subtitles.to_webvtt(b"just some text", ".srt")


@pytest.mark.parametrize("name, lang", [
    ("Ep05.uk.srt", "uk"), ("[Erai-raws] Show - 05 [1080p][RUS].ass", "ru"), ("show_06.en.srt", "en"),
    ("english subs.vtt", "en"), ("It Follows.srt", ""),
])
def test_the_language_a_name_says(name, lang):
    assert subtitles.guess_lang(name) == lang


# ---- an episode's tracks ----

@pytest.fixture
def episode(tmp_path):
    lib = Library(tmp_path / "lib")
    tid = lib.create(DraftIn(meta=TitleMeta(title="Series", type="anime"))).id
    lib.attach_chapter_media(tid, num="5", lang="EN", group="sub",
                             src=_mp4(tmp_path / "ep5.mp4"), sidecar={"filename": "ep5.mp4"})
    cid = lib.get(tid).chapters[0].id
    with TestClient(create_app(lib)) as c:
        yield c, lib, tid, cid
    lib.close()


def _upload(c, tid, cid, name, body, **form):
    return c.post(f"/api/titles/{tid}/chapters/{cid}/subtitles",
                  files={"file": (name, body)}, data={k: str(v) for k, v in form.items()})


def test_tracks_are_added_served_changed_and_removed(episode):
    c, lib, tid, cid = episode
    r = _upload(c, tid, cid, "Ep05.en.srt", SRT.encode())
    assert r.status_code == 200
    subs = r.json()["chapters"][0]["subtitles"]
    assert [(s["lang"], s["label"], s["default"], s["format"]) for s in subs] == [("en", "English", True, "srt")]

    got = c.get(f"/api/titles/{tid}/chapters/{cid}/subtitles/{subs[0]['id']}")
    assert got.status_code == 200 and got.headers["content-type"].startswith("text/vtt")
    assert got.text.startswith("WEBVTT")

    r = _upload(c, tid, cid, "[Erai] 05 [UKR].ass", ASS.encode(), default=True)
    subs = r.json()["chapters"][0]["subtitles"]
    assert [(s["lang"], s["default"]) for s in subs] == [("en", False), ("uk", True)]

    r = c.patch(f"/api/titles/{tid}/chapters/{cid}/subtitles/{subs[1]['id']}", json={"label": "Українська (ASS)"})
    assert r.json()["chapters"][0]["subtitles"][1]["label"] == "Українська (ASS)"

    r = c.delete(f"/api/titles/{tid}/chapters/{cid}/subtitles/{subs[1]['id']}")
    left = r.json()["chapters"][0]["subtitles"]
    assert [(s["lang"], s["default"]) for s in left] == [("en", True)]  # the remaining one leads
    chapters = lib.vault._chapters_dir(tid)
    assert sorted(p.name.split(".", 1)[1] for p in chapters.glob("*.sub-*")) == ["sub-1.orig.srt", "sub-1.vtt"]


def test_an_unreadable_or_foreign_file_is_refused_and_nothing_is_stored(episode):
    c, lib, tid, cid = episode
    assert _upload(c, tid, cid, "notes.srt", b"no cues here").status_code == 422
    assert _upload(c, tid, cid, "notes.txt", SRT.encode()).status_code == 422
    assert not list(lib.vault._chapters_dir(tid).glob("*.sub-*"))


def test_tracks_survive_a_new_video_and_go_with_the_episode(episode, tmp_path):
    c, lib, tid, cid = episode
    _upload(c, tid, cid, "Ep05.en.srt", SRT.encode())
    # the episode downloaded again: the subtitles are still its own
    lib.attach_chapter_media(tid, num="5", lang="EN", group="sub", chapter_id=cid,
                             src=_mp4(tmp_path / "ep5-again.mp4"), sidecar={"filename": "ep5-again.mp4"})
    ch = lib.get(tid).chapters[0]
    assert [s.lang for s in ch.subtitles] == ["en"] and ch.kind == "video"
    assert lib.vault.chapter_media_path(tid, cid).suffix == ".mp4"  # a .vtt is never taken for the media
    assert lib.subtitle_file(tid, cid, "1") is not None
    # nor is it a stray for the vault check
    assert not lib._strays(tid, lib.vault.load(tid), {p.stem for p in lib.vault._chapters_dir(tid).glob("*.json")})

    c.delete(f"/api/titles/{tid}/chapters/{cid}/media")
    assert not list(lib.vault._chapters_dir(tid).glob("*.sub-*"))


def test_an_entry_without_video_takes_no_subtitles(tmp_path):
    lib = Library(tmp_path / "lib")
    tid = lib.create(DraftIn(meta=TitleMeta(title="Book"))).id
    with TestClient(create_app(lib)) as c:
        r = c.post(f"/api/titles/{tid}/chapters/import?num=1&lang=EN&filename=one.srt", content=SRT.encode())
        assert r.status_code == 422 and "video first" in r.json()["detail"]
    lib.close()


def test_an_armed_episode_takes_its_subtitle_file(episode, tmp_path):
    """D4: arm the episode, download its subtitles — the video stays."""
    c, lib, tid, cid = episode
    src = tmp_path / "dl.srt"
    src.write_bytes(SRT.encode())
    assert c.post("/api/downloads/arm", json={"titleId": tid, "num": "5", "lang": "EN", "group": "sub"}).status_code == 200
    started = c.post("/api/downloads/start", json={"filename": "Show - 05.uk.srt", "fileUrl": "https://subs.site/5.srt"})
    r = c.post(f"/api/downloads/{started.json()['id']}/complete", json={"path": str(src)})
    assert r.status_code == 200
    ch = r.json()["chapters"][0]
    assert ch["kind"] == "video" and [s["lang"] for s in ch["subtitles"]] == ["uk"]
    side = json.loads((lib.vault._chapters_dir(tid) / f"{cid}.json").read_text("utf-8"))
    assert side["subtitles"][0]["fileUrl"] == "https://subs.site/5.srt"


# ---- the downloads nobody armed ----

def test_a_download_nobody_armed_lands_in_the_library_downloads(episode, tmp_path):
    c, lib, tid, cid = episode
    for i in range(2):
        src = tmp_path / f"t{i}.srt"
        src.write_bytes(SRT.encode())
        started = c.post("/api/downloads/start", json={
            "filename": "Show - 05 [RUS].srt", "fileUrl": "https://subs.site/5.srt", "pageUrl": "https://subs.site/show"})
        assert started.status_code == 200 and started.json()["loose"]
        kept = c.post(f"/api/downloads/{started.json()['id']}/complete", json={"path": str(src)}).json()
    assert kept["name"] == "Show - 05 [RUS] (2).srt"  # a name already there is never replaced
    files = c.get("/api/downloads/files").json()
    assert {(f["name"], f["kind"], f["lang"], f["fileUrl"]) for f in files} == {
        ("Show - 05 [RUS].srt", "subtitle", "ru", "https://subs.site/5.srt"),
        ("Show - 05 [RUS] (2).srt", "subtitle", "ru", "https://subs.site/5.srt")}
    # the vault never reads its downloads as a type shelf
    assert [t["id"] for t in c.get("/api/library").json()] == [tid]
    assert Path(c.get("/api/downloads/folder").json()["path"]).name == "downloads"

    # filed under its episode, and gone from the downloads
    r = c.post(f"/api/titles/{tid}/chapters/{cid}/subtitles/from-downloads", json={"name": "Show - 05 [RUS].srt"})
    assert [s["lang"] for s in r.json()["chapters"][0]["subtitles"]] == ["ru"]
    assert [f["name"] for f in c.get("/api/downloads/files").json()] == ["Show - 05 [RUS] (2).srt"]

    assert c.delete("/api/downloads/files", params={"name": "Show - 05 [RUS] (2).srt"}).status_code == 204
    assert c.get("/api/downloads/files").json() == []
    assert c.delete("/api/downloads/files", params={"name": "../title.json"}).status_code == 404


def test_a_file_put_in_by_hand_is_listed(episode):
    c, lib, tid, cid = episode
    folder = lib.vault.root / "downloads"
    folder.mkdir()
    (folder / "extras.cbz").write_bytes(b"PK")
    (folder / "readme.txt").write_text("not for the library")
    assert [(f["name"], f["kind"]) for f in c.get("/api/downloads/files").json()] == [("extras.cbz", "archive")]


def test_a_download_the_library_cannot_use_is_refused_and_said(episode):
    c, *_ = episode
    r = c.post("/api/downloads/start", json={"filename": "setup_player.exe"})
    assert r.status_code == 409 and "not kept" in r.json()["detail"]
    item = c.get("/api/downloads").json()["items"][0]
    assert (item["filename"], item["state"], item["loose"]) == ("setup_player.exe", "failed", True)


def test_the_downloads_folder_is_never_a_shelf_and_no_type_takes_its_name(episode, tmp_path):
    """A plain name a file manager shows — so the vault reads past it, and a
    type called that gets a shelf of its own."""
    c, lib, tid, cid = episode
    (lib.vault.root / "downloads").mkdir(exist_ok=True)
    (lib.vault.root / "downloads" / "a.srt").write_text(SRT)
    other = lib.create(DraftIn(meta=TitleMeta(title="Odd", type="downloads"))).id
    assert (lib.vault.root / "downloads-shelf" / other / "title.json").is_file()
    assert sorted(t["id"] for t in c.get("/api/library").json()) == sorted([tid, other])
    assert [f["name"] for f in c.get("/api/downloads/files").json()] == ["a.srt"]
