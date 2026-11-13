"""Archives are rewritten an entry at a time: converting one on its way in, or
reordering and moving its pages, holds a page in memory — never the archive."""
from __future__ import annotations

import os
import tracemalloc
import zipfile

import pytest

from app.library import media

PAGE = 1024 * 1024   # 1 MB a page, random so nothing compresses it away
PAGES = 24           # an archive of 24 MB


def _archive(path, pages: int = PAGES, *, junk: bool = True):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for i in range(1, pages + 1):
            z.writestr(f"p{i}.jpg", os.urandom(PAGE))
        if junk:
            z.writestr("ComicInfo.xml", b"<ComicInfo/>")
    return path


def _peak(fn) -> int:
    tracemalloc.start()
    try:
        fn()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_converting_an_archive_holds_one_entry_not_all(tmp_path):
    src = _archive(tmp_path / "in.cbz")
    dst = tmp_path / "out.zip"
    peak = _peak(lambda: media.repack_to_zip(src, dst))
    assert peak < 6 * PAGE, f"held {peak / PAGE:.1f} MB converting a {PAGES} MB archive"
    assert len(media.image_entries(dst)) == PAGES
    with zipfile.ZipFile(dst) as z:
        assert z.read("ComicInfo.xml") == b"<ComicInfo/>"


def test_reordering_pages_holds_one_page_not_the_chapter(tmp_path):
    path = _archive(tmp_path / "ch.zip")
    before = {n: media.read_entry(path, n)[0] for n in media.image_entries(path)}
    order = list(reversed(range(PAGES)))
    peak = _peak(lambda: media.reorder_entries(path, order))
    assert peak < 6 * PAGE, f"held {peak / PAGE:.1f} MB reordering a {PAGES} MB chapter"
    names = media.image_entries(path)
    assert media.read_entry(path, names[0])[0] == before[f"p{PAGES}.jpg"]
    with zipfile.ZipFile(path) as z:
        assert z.read("ComicInfo.xml") == b"<ComicInfo/>"   # carried over


def test_moving_pages_holds_one_page_not_the_selection(tmp_path):
    src = _archive(tmp_path / "a.zip")
    dst = _archive(tmp_path / "b.zip", pages=2, junk=False)
    picked = set(media.image_entries(src)[:20])
    peak = _peak(lambda: media.move_images(src, dst, picked))
    assert peak < 6 * PAGE, f"held {peak / PAGE:.1f} MB moving 20 MB of pages"
    assert len(media.image_entries(src)) == PAGES - 20
    assert len(media.image_entries(dst)) == 22


def test_an_archive_that_breaks_halfway_leaves_nothing_behind(tmp_path):
    src = _archive(tmp_path / "in.zip", pages=4, junk=False)
    raw = bytearray(src.read_bytes())
    with zipfile.ZipFile(src) as z:
        third = z.getinfo("p3.jpg")
    # corrupt the third page's data: its CRC check fails when it is read
    at = third.header_offset + 30 + len(third.filename) + 10
    raw[at] ^= 0xFF
    src.write_bytes(bytes(raw))
    dst = tmp_path / "out" / "ch.zip"
    dst.parent.mkdir()
    with pytest.raises(zipfile.BadZipFile):
        media.repack_to_zip(src, dst)
    assert list(dst.parent.iterdir()) == []   # no half-written archive, no stray .tmp


def test_a_rewrite_that_cannot_land_leaves_no_copy_behind(tmp_path, monkeypatch):
    # the archive is held open elsewhere (a player, an antivirus): every replace
    # is refused, and the scratch copy must not stay behind as a leftover
    path = _archive(tmp_path / "ch.zip", pages=3)
    before = path.read_bytes()

    def refused(_src, _dst):
        raise PermissionError("in use")

    monkeypatch.setattr(media.os, "replace", refused)
    monkeypatch.setattr(media.time, "sleep", lambda _s: None)
    with pytest.raises(media.MediaInUseError):
        media.reorder_entries(path, [2, 1, 0])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ch.zip"]
    assert path.read_bytes() == before
