"""Tile previews (library/previews.py): made ahead of the grid that asks for
them, one at a time, and never by re-reading an original a cached one covers."""
from __future__ import annotations

import threading
import time
import zipfile
from io import BytesIO

import pytest
from PIL import Image

from app.library import media
from app.library.models import DraftIn, TitleMeta
from app.library.previews import TILE_W
from app.library.service import Library
from app.library.vault import APP_DIR


@pytest.fixture
def lib(tmp_path):
    lib = Library(tmp_path / "lib")
    yield lib
    lib.close()


def _jpeg(size=(900, 1300), color=(180, 40, 40)) -> bytes:
    out = BytesIO()
    Image.new("RGB", size, color).save(out, "JPEG")
    return out.getvalue()


def _zip(path, pages=2):
    with zipfile.ZipFile(path, "w") as z:
        for i in range(pages):
            z.writestr(f"{i + 1:03}.jpg", _jpeg(color=(40 * i, 90, 120)))
    return path


def _thumbs(lib: Library) -> list[str]:
    d = lib.vault.root / APP_DIR / "cache" / "thumbs"
    return sorted(p.name for p in d.glob("*.jpg")) if d.is_dir() else []


def _coverless_title(lib: Library, tmp_path, name="Glass Ward") -> tuple[str, str]:
    out = lib.create(DraftIn(meta=TitleMeta(title=name)))
    lib.attach_chapter_media(out.id, num="1", lang="", group="", src=_zip(tmp_path / f"{name}.zip"), sidecar={})
    return out.id, lib.get(out.id).chapters[0].id


def test_a_cached_page_preview_never_opens_its_archive(lib, tmp_path, monkeypatch):
    """A coverless tile shows its first page: once made, serving it again must
    not read the zip — on a spinning network disk that read is the request."""
    tid, cid = _coverless_title(lib, tmp_path)
    made = lib.chapter_page(tid, cid, 0, TILE_W)
    assert made is not None and made[1] == "image/jpeg"

    def no_archive(*_a, **_kw):
        raise AssertionError("the archive was opened for a cached preview")

    monkeypatch.setattr(media, "image_entries", no_archive)
    monkeypatch.setattr(media, "read_entry", no_archive)
    assert lib.chapter_page(tid, cid, 0, TILE_W) == made


def test_a_write_offers_the_tile_its_preview(lib, tmp_path):
    """What a write leaves a tile to show is made behind it, before any grid
    asks — an import of hundreds of titles leaves nothing for the first look."""
    covered = lib.create(DraftIn(meta=TitleMeta(title="Iron Harbor")))
    lib.set_cover(covered.id, _jpeg(), "jpg")
    coverless, _cid = _coverless_title(lib, tmp_path)
    assert _thumbs(lib) == []  # nothing made on the request path of a write

    lib.previews._loop()  # the worker, driven here (conftest)

    made = _thumbs(lib)
    assert any(n.startswith("cover-") and covered.id in n and n.endswith(f"-{TILE_W}.jpg") for n in made)
    assert any(n.startswith("page-") and coverless in n for n in made)
    # …and the grid's own request is now a hit: the same files, nothing new
    lib.cover_thumb(covered.id, TILE_W)
    assert _thumbs(lib) == made


def test_previews_are_made_one_at_a_time(lib, monkeypatch):
    """Forty tiles asking at once must not become forty reads of forty
    originals at once — the disk seeks between them and every tile waits."""
    ids = []
    for i in range(6):
        t = lib.create(DraftIn(meta=TitleMeta(title=f"Title {i}")))
        lib.set_cover(t.id, _jpeg(color=(i * 30, 20, 20)), "jpg")
        ids.append(t.id)
    running, peak, guard = 0, 0, threading.Lock()
    real = media.thumbnail

    def slow_thumbnail(data, width, cap=None):
        nonlocal running, peak
        with guard:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        try:
            return real(data, width, cap)
        finally:
            with guard:
                running -= 1

    monkeypatch.setattr(media, "thumbnail", slow_thumbnail)
    out: dict[str, tuple[bytes, str] | None] = {}
    threads = [threading.Thread(target=lambda tid=tid: out.__setitem__(tid, lib.cover_thumb(tid, 300)))
               for tid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert peak == 1
    assert all(out[tid] and out[tid][1] == "image/jpeg" for tid in ids)


def test_a_tile_asked_for_twice_at_once_is_made_once(lib, monkeypatch):
    t = lib.create(DraftIn(meta=TitleMeta(title="Paper Moons")))
    lib.set_cover(t.id, _jpeg(), "jpg")
    calls = []
    real = media.thumbnail

    def counted(data, width, cap=None):
        calls.append(width)
        time.sleep(0.05)
        return real(data, width, cap)

    monkeypatch.setattr(media, "thumbnail", counted)
    threads = [threading.Thread(target=lambda: lib.cover_thumb(t.id, 200)) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=10)
    assert calls == [200]


@pytest.mark.previews
def test_content_stored_before_is_given_its_previews_once(lib, tmp_path):
    """A vault filled before writes made previews gets them in one pass after
    the launch's verification, and the vault remembers it was done."""
    covered = lib.create(DraftIn(meta=TitleMeta(title="Hollow Crown")))
    lib.set_cover(covered.id, _jpeg(), "jpg")
    coverless, _cid = _coverless_title(lib, tmp_path)
    lib.create(DraftIn(meta=TitleMeta(title="Saltwind")))  # nothing to show: nothing to make
    assert lib.vault.needs_previews()

    lib.previews.backfill()

    made = _thumbs(lib)
    assert len(made) == 2
    assert any(covered.id in n for n in made) and any(coverless in n for n in made)
    assert not lib.vault.needs_previews()


@pytest.mark.previews
def test_the_pass_reads_nothing_for_previews_already_made(lib, tmp_path, monkeypatch):
    """A library whose previews exist must not have them read back, nor every
    title's folder listed, to find that out: on a network disk that is the
    whole library re-read at launch. The cache is listed once; the keys come
    from the index."""
    covered = lib.create(DraftIn(meta=TitleMeta(title="Hollow Crown")))
    lib.set_cover(covered.id, _jpeg(), "jpg")
    _coverless_title(lib, tmp_path)
    lib.previews.backfill()
    lib.vault._update_vault_meta(tilePreviews=0)  # as if this vault had never had the pass

    touched: list[str] = []
    for name in ("cover_thumb", "chapter_page", "chapter_frames", "_cached"):
        monkeypatch.setattr(lib, name, lambda *a, _n=name, **kw: touched.append(_n))
    monkeypatch.setattr(lib.vault, "cover_paths", lambda *a, **kw: touched.append("cover_paths") or [])

    lib.previews.backfill()

    assert touched == []
    assert not lib.vault.needs_previews()


@pytest.mark.previews
def test_a_pass_cut_short_is_not_marked_done(lib, tmp_path):
    covered = lib.create(DraftIn(meta=TitleMeta(title="Night Shift")))
    lib.set_cover(covered.id, _jpeg(), "jpg")
    lib._closing.set()  # the app closing as the pass starts
    lib.previews.backfill()
    assert lib.vault.needs_previews()


@pytest.mark.parametrize(("size", "cap", "want"), [
    ((4000, 3000), None, (420, 315)),   # a wide cover: width decides
    ((2000, 3000), None, (420, 630)),   # a tall cover
    ((1000, 9000), 1.5, (420, 630)),    # a webtoon strip, cropped from the top
    ((300, 200), None, (300, 200)),     # never enlarged
])
def test_a_large_jpeg_is_previewed_at_the_size_asked(size, cap, want):
    """Decoded at a fraction of its size (Pillow's draft) — and still exactly
    the preview the full decode made."""
    out = media.thumbnail(_jpeg(size), 420, cap)
    assert out is not None
    assert Image.open(BytesIO(out)).size == want
