#!/usr/bin/env python
"""Regenerate the project page's screenshots (docs/img/*.png).

Everything it shows is generated: a throwaway demo library with invented titles,
people and abstract cover/page art. It never opens — or even looks at — the real
library. Run it from the repo root:

    python tools/screenshots.py

Requires the dev setup (.venv + shell/node_modules), because it drives the app
itself: the sidecar serves the demo vault and Electron captures the real UI.
"""
from __future__ import annotations

import functools
import http.server
import io
import json
import os
import platform
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IS_WIN = platform.system() == "Windows"
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")
ELECTRON = ROOT / "shell" / "node_modules" / "electron" / "dist" / ("electron.exe" if IS_WIN else "electron")
OUT = ROOT / "docs" / "img"

# The backend lives in the project's virtualenv, so run there whatever the
# caller used — an IDE's Run button usually means the system interpreter.
if VENV_PY.exists() and Path(sys.executable).resolve() != VENV_PY.resolve():
    raise SystemExit(subprocess.run([str(VENV_PY), __file__, *sys.argv[1:]]).returncode)

sys.path.insert(0, str(ROOT / "backend"))

# ---- the demo library -------------------------------------------------------
# Every kind of thing a vault keeps — pages, video, images — listed in the order
# the library shows them: it opens on "recently updated", and build_vault
# touches the titles last-first. The media keys are popped before the metadata:
#   pages     [(label, lang, group, page count)]  a zip of drawn comic pages each
#   episodes  [(label, seconds)]                  an mp4 each, drawn from `scene`
#   images    (kind, count)                       one entry of photos or paintings
#   cover     "initials" | "poster" | "photo" | "swatches"; none, and the tile wears
#             stills cut from its own episodes
#   scene     what its episodes and poster show
#   subtitles True: the first episode carries an English track

TITLES = [
    dict(title="Harbour Lights", alt="港の灯り", type="series", status="completed", year="2024",
         authors=["Rin Aoyama"], studio=["Studio Kestrel"], characters=["Hana", "Toshi"],
         genres=["slice of life", "drama"], tags=["seaside", "adaptation"], rating=5, fav=True,
         desc="Paper Lanterns, animated: six evenings on a harbour that is closing for the season.",
         episodes=[("1", 1457), ("2", 1494), ("3", 1531), ("4", 1568)],
         cover="poster", scene="harbour", subtitles=True),
    dict(title="Paper Lanterns", alt="紙提灯", type="manga", status="ongoing", year="2021",
         authors=["Rin Aoyama"], characters=["Hana", "Toshi"],
         genres=["drama", "slice of life"], tags=["school", "friendship"], rating=5, fav=True,
         desc="A quiet coastal town, a paper lantern festival, and two friends who keep missing "
              "each other by a single street.",
         pages=[("1", "EN", "Lantern Scans", 8), ("2", "EN", "Lantern Scans", 10),
                ("2", "UA", "Ліхтарі", 10), ("3", "EN", "Lantern Scans", 9)],
         cover="initials"),
    dict(title="Low Tide", type="photo set", year="2025", authors=["Mara Lindqvist"],
         genres=["photography"], tags=["seaside", "dawn"], rating=4,
         desc="Sixteen exposures of one beach, from the first light to the last.",
         images=("photo", 16), cover="photo"),
    dict(title="Northbound 404", type="film", status="completed", year="2018",
         authors=["J. Halvorsen"], studio=["Fjordline Pictures"],
         genres=["thriller"], tags=["road trip", "night"], rating=4,
         desc="A courier, a highway that renumbers itself, and a package nobody ordered.",
         episodes=[("Film", 6097)], cover="poster", scene="road"),
    dict(title="Salt & Ember", type="comic", status="ongoing", year="2022",
         authors=["A. Ferreira", "N. Okonkwo"], characters=["Wren"],
         genres=["fantasy", "adventure"], tags=["magic", "travel"], rating=4,
         desc="A salt trader walks a caravan road that only exists at dusk.",
         pages=[("1", "EN", "", 12)], cover="initials"),
    dict(title="Painting Light", type="course", status="ongoing", year="2025",
         authors=["Ayaka Nomura"], genres=["art"], tags=["colour", "lectures"], rating=5,
         desc="Four lectures on painting light: warm and cool, edges, the hour before dark, "
              "and night.",
         episodes=[("1", 1102), ("2", 1260), ("3", 1187), ("4", 1335)], scene="slides",
         cover="swatches"),
    dict(title="The Art of Harbour Lights", type="artbook", year="2024",
         authors=["Rin Aoyama"], studio=["Studio Kestrel"], genres=["art"],
         tags=["seaside", "backgrounds"], rating=5,
         desc="Background paintings and colour keys from the series.",
         images=("painting", 12), cover="photo"),
    dict(title="Iron Meridian", type="manhwa", status="ongoing", year="2023",
         authors=["Seo Ha-eun"], characters=["Captain Vale"],
         genres=["action", "sci-fi"], tags=["mecha", "war"], rating=4,
         desc="A cartographer is drafted to map the only corridor through a storm that eats machines.",
         pages=[("1", "EN", "Kestrel", 12), ("2", "EN", "Kestrel", 11)], cover="initials"),
    dict(title="Pale Harbour", type="series", status="ongoing", year="2026",
         authors=["Choi Minseo"], studio=["Greywater Animation"], characters=["Eun"],
         genres=["horror", "mystery"], tags=["fog", "lighthouse"], rating=4,
         desc="The lighthouse keeps its light on for something that is not a ship.",
         episodes=[("1", 2832), ("2", 2790)], scene="fog"),
    dict(title="The Quiet Bakery", type="manga", status="completed", year="2019",
         authors=["Mei Tanaka"], characters=["Oba-san", "Ken"],
         genres=["slice of life", "comedy"], tags=["food", "family"], rating=5,
         desc="Nothing happens in this bakery, gloriously, for twelve volumes.", cover="initials"),
    dict(title="Summer Static", type="manhwa", status="ongoing", year="2024",
         authors=["Park Jiwon"], characters=["Nari"],
         genres=["romance", "drama"], tags=["music", "college"], rating=5,
         desc="Two people who only talk during power cuts.",
         pages=[("1", "EN", "Static Club", 10)], cover="initials"),
    dict(title="Neon Fieldwork", type="image set", year="2024", authors=["Studio Halcyon"],
         genres=["art"], tags=["colour study", "city"],
         desc="A colour study: the same street at fourteen hours of one day.",
         images=("city", 14), cover="photo"),
]

LECTURES = ["Warm and cool", "Edges", "The hour before dark", "Night"]

LINES = [
    "The last ferry leaves at six.", "Then we have until six.", "You said that last year.",
    "Last year I meant it.", "Will they really close the harbour?", "Only for the winter.",
    "Every winter is the last one, to hear her tell it.", "Hold the lantern higher.",
    "I can't see the steps.", "Nine hundred of them. I counted once.", "And?",
    "Eight hundred and ninety-nine. One was missing.",
]

PALETTES = [
    ((32, 48, 58), (43, 65, 80), (26, 39, 48)), ((58, 37, 48), (81, 43, 58), (51, 27, 36)),
    ((35, 58, 47), (45, 81, 66), (26, 51, 39)), ((47, 42, 58), (67, 58, 88), (36, 31, 49)),
    ((58, 52, 31), (82, 72, 41), (51, 45, 23)), ((43, 33, 64), (58, 43, 82), (36, 27, 51)),
]


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    names = ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ()
    for name in (*names, "segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _vgradient(img, top, bottom, y0: int = 0, y1: int | None = None) -> None:
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    y1 = img.height if y1 is None else y1
    for y in range(y0, y1):
        d.line([(0, y), (img.width, y)], fill=_mix(top, bottom, (y - y0) / max(1, y1 - y0 - 1)))


def _jpeg(img, quality: int = 86) -> bytes:
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality)
    return out.getvalue()


def scene(kind: str, seed: int, w: int, h: int):
    """A painted place `w` wide — an episode pans across it — and `h` high."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h))
    horizon = int(h * 0.62)
    if kind == "harbour":
        a, b, c = PALETTES[seed % len(PALETTES)]
        _vgradient(img, a, c)
        d = ImageDraw.Draw(img)
        for _ in range(max(4, w // 180)):
            x, y, r = rnd.randint(-h // 4, w), rnd.randint(-h // 4, h // 2), rnd.randint(h // 4, h * 2 // 3)
            d.ellipse([x, y, x + r, y + r], fill=_mix(b, (255, 255, 255), .16))
        img = img.filter(ImageFilter.GaussianBlur(h / 12))
        d = ImageDraw.Draw(img)
        for k in range(9):
            y = horizon + h // 60 + k * h // 30
            d.line([(0, y), (w, y)], fill=_mix(b, (255, 255, 255), .28 - k * .025), width=max(1, h // 360))
        for _ in range(max(6, w // 100)):
            x, r = rnd.randint(0, w), rnd.randint(h // 120 + 2, h // 50 + 3)
            d.ellipse([x, horizon - r * 2, x + r * 2, horizon], fill=(250, 226, 170))
    elif kind == "fog":
        _vgradient(img, (126, 136, 140), (64, 74, 82))
        d = ImageDraw.Draw(img)
        for _ in range(max(4, w // 160)):
            x, y, r = rnd.randint(-h // 3, w), rnd.randint(-h // 4, h * 2 // 3), rnd.randint(h // 3, h)
            d.ellipse([x, y, x + r, y + r], fill=(168, 176, 178))
        img = img.filter(ImageFilter.GaussianBlur(h / 9))
        d = ImageDraw.Draw(img)
        d.rectangle([0, horizon, w, h], fill=(52, 60, 66))
        beams = Image.new("RGB", (w, h))
        bd = ImageDraw.Draw(beams)
        for x in range(w // 5, w, max(1, w * 2 // 5)):
            tw, th = max(4, h // 28), h // 3
            d.rectangle([x, horizon - th, x + tw, horizon], fill=(30, 34, 38))
            d.rectangle([x - tw // 3, horizon - th - tw, x + tw + tw // 3, horizon - th], fill=(30, 34, 38))
            bd.polygon([(x + tw // 2, horizon - th - tw // 2), (x + w // 4, horizon - th - h // 6),
                        (x + w // 4, horizon - th + h // 10)], fill=(150, 140, 100))
        img = ImageChops.add(img, beams.filter(ImageFilter.GaussianBlur(h / 40)))
    elif kind == "road":
        _vgradient(img, (8, 11, 24), (40, 42, 72), 0, horizon)
        _vgradient(img, (20, 20, 26), (10, 10, 13), horizon, h)
        d = ImageDraw.Draw(img)
        for _ in range(w // 12):
            x, y = rnd.randint(0, w), rnd.randint(0, horizon - 10)
            d.point((x, y), fill=(200, 205, 220))
        d.polygon([(0, h), (w, h), (w, horizon + h // 9), (0, horizon + h // 7)], fill=(30, 31, 36))
        glow = Image.new("RGB", (w, h))
        gd = ImageDraw.Draw(glow)
        step = max(60, h // 2)
        for x in range(rnd.randint(0, step), w, step):
            ph = h // 4
            d.line([(x, horizon + h // 8), (x, horizon + h // 8 - ph)], fill=(52, 54, 60), width=max(2, h // 160))
            r = h // 22
            gd.ellipse([x - r, horizon + h // 8 - ph - r, x + r, horizon + h // 8 - ph + r], fill=(255, 176, 92))
        img = ImageChops.add(img, glow.filter(ImageFilter.GaussianBlur(h / 30)))
        d = ImageDraw.Draw(img)
        for x in range(0, w, h // 3):
            y = horizon + h // 8 + (h - horizon) // 2
            d.line([(x, y), (x + h // 8, y)], fill=(170, 160, 120), width=max(2, h // 120))
    return img


def slide(lecture: int, k: int, w: int, h: int):
    """Slide `k` of a lecture: its topic, and the swatches it has put up so far."""
    from PIL import Image, ImageDraw
    rnd = random.Random(lecture * 31 + 7)
    img = Image.new("RGB", (w, h), (30, 32, 37))
    d = ImageDraw.Draw(img)
    d.text((w * .06, h * .08), f"LECTURE {lecture}", font=_font(h // 26, True), fill=(140, 146, 158))
    d.text((w * .06, h * .15), LECTURES[lecture - 1], font=_font(h // 11, True), fill=(236, 238, 242))
    warm, cool = (rnd.randint(170, 230), rnd.randint(90, 140), 60), (60, rnd.randint(100, 140), rnd.randint(170, 220))
    size = w * .1
    for i in range(k):
        col = _mix(warm, cool, i / 7) if lecture != 4 else _mix((24, 30, 60), (250, 210, 140), i / 7)
        x = w * .06 + (i % 4) * (size * 1.25)
        y = h * .4 + (i // 4) * (size * 1.15)
        d.rounded_rectangle([x, y, x + size, y + size], radius=int(size / 8), fill=col)
    d.text((w * .9, h * .9), f"{k}/8", font=_font(h // 30), fill=(120, 126, 138))
    return img


def picture(kind: str, k: int, count: int):
    """Image `k` of a set: a photo of a beach, a painting, or a street at one hour."""
    from PIL import Image, ImageDraw, ImageFilter
    t = k / max(1, count - 1)
    if kind == "painting":
        return scene("fog" if k % 4 == 3 else "harbour", 40 + k, 1500, 1000)
    w, h = 1000, 1500
    img = Image.new("RGB", (w, h))
    if kind == "photo":
        sky = _mix((34, 44, 86), (238, 170, 120), t * 2) if t < .5 else _mix((238, 170, 120), (54, 36, 72), t * 2 - 1)
        horizon = int(h * .56)
        _vgradient(img, _mix(sky, (10, 14, 30), .5), sky, 0, horizon)
        _vgradient(img, _mix(sky, (20, 40, 60), .55), (40, 52, 64), horizon, int(h * .8))
        _vgradient(img, (150, 132, 110), (96, 84, 72), int(h * .8), h)
        d = ImageDraw.Draw(img)
        sy = int(horizon - h * .3 * (1 - abs(t - .5) * 2)) - 40
        d.ellipse([w * .58, sy, w * .58 + 120, sy + 120], fill=_mix((255, 236, 200), sky, .2))
        for i in range(14):
            y = horizon + 18 + i * 22
            d.line([(0, y), (w, y)], fill=_mix(sky, (255, 255, 255), .18), width=2)
        return img.filter(ImageFilter.GaussianBlur(2))
    # a street at one hour: the sky darkens, the windows light
    sky = _mix((140, 180, 220), (16, 18, 40), t)
    _vgradient(img, _mix(sky, (0, 0, 0), .3), _mix(sky, (255, 150, 90), .35 * (1 - abs(t - .7))))
    d = ImageDraw.Draw(img)
    rnd = random.Random(99)
    x = 0
    while x < w:
        bw, bh = rnd.randint(140, 260), rnd.randint(500, 1100)
        d.rectangle([x, h - bh, x + bw, h], fill=_mix((70, 72, 80), (14, 14, 20), t))
        for wy in range(h - bh + 30, h - 40, 46):
            for wx in range(x + 18, x + bw - 30, 38):
                if rnd.random() < t * .9:
                    d.rectangle([wx, wy, wx + 18, wy + 24], fill=(250, 200, 120))
        x += bw + rnd.randint(4, 16)
    return img


def cover_image(spec: dict, seed: int):
    from PIL import Image, ImageDraw, ImageFilter
    kind = spec.get("cover")
    if kind == "photo":
        img = picture(spec["images"][0], 0, spec["images"][1])
        if img.width > img.height:
            img = img.crop(((img.width - img.height * 2 // 3) // 2, 0,
                            (img.width + img.height * 2 // 3) // 2, img.height))
        return img.resize((800, 1200))
    if kind == "swatches":
        img = Image.new("RGB", (800, 1200), (30, 32, 37))
        d = ImageDraw.Draw(img)
        for i in range(12):
            x, y = 70 + (i % 3) * 225, 110 + (i // 3) * 190
            d.rounded_rectangle([x, y, x + 200, y + 165], radius=18,
                                fill=_mix((226, 120, 64), (64, 112, 214), i / 11))
        d.text((66, 900), spec["title"], font=_font(82, True), fill=(240, 242, 246))
        d.text((70, 1010), spec["authors"][0].upper(), font=_font(30), fill=(150, 156, 168))
        return img
    if kind == "poster":
        img = scene(spec["scene"], seed, 800, 1200)
        d = ImageDraw.Draw(img)
        d.text((56, 900), spec["title"], font=_font(82, True), fill=(250, 250, 250))
        d.text((60, 1010), spec["authors"][0].upper(), font=_font(30), fill=(220, 222, 228))
        return img
    rnd = random.Random(seed)
    w, h = 800, 1200
    a, b, c = PALETTES[seed % len(PALETTES)]
    img = Image.new("RGB", (w, h), a)
    _vgradient(img, a, c)
    d = ImageDraw.Draw(img)
    for _ in range(rnd.randint(3, 5)):
        x, y = rnd.randint(-100, w), rnd.randint(-100, h)
        r = rnd.randint(180, 460)
        d.ellipse([x, y, x + r, y + r], fill=b)
    img = img.filter(ImageFilter.GaussianBlur(48))
    d = ImageDraw.Draw(img)
    initials = "".join(word[0] for word in spec["title"].split()[:2]).upper()
    f = _font(260)
    box = d.textbbox((0, 0), initials, font=f)
    d.text(((w - box[2]) / 2, (h - box[3]) / 2 - 60), initials, font=f, fill=(255, 255, 255))
    return img


def page_bytes(n: int, total: int, seed: int) -> bytes:
    from PIL import Image, ImageDraw
    rnd = random.Random(seed * 100 + n)
    w, h = 900, 1300
    img = Image.new("RGB", (w, h), (238, 236, 231))
    d = ImageDraw.Draw(img)
    m, y = 60, 60
    rows = rnd.choice([2, 3])
    for r in range(rows):
        rh = (h - m * 2 - (rows - 1) * 24) // rows
        cols = rnd.choice([1, 2])
        x = m
        for ci in range(cols):
            cw = (w - m * 2 - (cols - 1) * 24) // cols
            d.rectangle([x, y, x + cw, y + rh], outline=(28, 30, 34), width=4,
                        fill=(214, 212, 206) if (r + ci) % 2 else (224, 222, 216))
            x += cw + 24
        y += rh + 24
    d.text((w - 130, h - 62), f"{n}/{total}", font=_font(28), fill=(90, 92, 98))
    return _jpeg(img, 82)


def episode_file(out: Path, kind: str, seed: int, seconds: int, lecture: int = 0) -> None:
    """An episode `seconds` long. Its few drawn frames are held across the length
    and encoded at one frame a second, by the ffmpeg the app itself bundles — so
    a film costs seconds to make, and its contact sheet still shows nine moments."""
    from app.library.transcode import ffmpeg
    w, h = 640, 360
    if kind == "slides":
        frames = [slide(lecture, k, w, h) for k in range(1, 9)]
    else:
        pano = scene(kind, seed, w * 4, h)
        frames = [pano.crop((x, 0, x + w, h)) for x in range(0, w * 3 + 1, w * 3 // 59)]
    proc = subprocess.Popen(
        [ffmpeg() or "ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{w}x{h}", "-framerate", f"{len(frames)}/{seconds}", "-i", "-", "-r", "1",
         "-c:v", "libx264", "-preset", "veryfast", "-g", "30", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE)
    assert proc.stdin is not None
    for frame in frames:
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait():
        raise RuntimeError(f"ffmpeg could not encode {out.name}")


def subtitle_file(out: Path, seconds: int) -> None:
    def at(s: float) -> str:
        return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{int(s % 60):02d},000"
    cues = [f"{i + 1}\n{at(4 + i * 7)} --> {at(9 + i * 7)}\n{LINES[i % len(LINES)]}\n"
            for i in range((seconds - 10) // 7)]
    out.write_text("\n".join(cues), encoding="utf-8")


def build_vault(root: Path, tmp: Path) -> None:
    from app.library.models import DraftIn, SourceRef, TitleMeta, UserPatch
    from app.library.service import Library

    random.seed(7)
    root.mkdir(parents=True, exist_ok=True)  # made as the launch window makes a vault: opening never does
    lib = Library(root)
    stamp = {"importedFrom": "local", "downloadedAt": "2026-08-01T10:00:00+00:00"}
    made: list[str] = []
    for i, spec in enumerate(TITLES):
        meta = TitleMeta(**{k: v for k, v in spec.items() if k in TitleMeta.model_fields})
        out = lib.create(DraftIn(meta=meta))
        made.append(out.id)
        if spec.get("cover"):
            lib.set_cover(out.id, _jpeg(cover_image(spec, i + 3), 88), "jpg", "")
        if spec.get("rating") or spec.get("fav"):
            lib.patch_user(out.id, UserPatch(rating=spec.get("rating", 0), fav=spec.get("fav", False)))
        for j, (num, lang, group, pages) in enumerate(spec.get("pages", [])):
            z = tmp / f"{out.id}-{j}.zip"
            with zipfile.ZipFile(z, "w") as zf:
                for k in range(1, pages + 1):
                    zf.writestr(f"{k:03d}.jpg", page_bytes(k, pages, i + j))
            lib.attach_chapter_media(out.id, num=num, lang=lang, group=group, src=z,
                                     sidecar={"filename": z.name, **stamp})
        if spec.get("images"):
            kind, count = spec["images"]
            z = tmp / f"{out.id}.zip"
            with zipfile.ZipFile(z, "w") as zf:
                for k in range(count):
                    zf.writestr(f"{k + 1:03d}.jpg", _jpeg(picture(kind, k, count)))
            lib.attach_chapter_media(out.id, num="1", lang="", group="", src=z,
                                     sidecar={"filename": z.name, **stamp})
        for n, (num, seconds) in enumerate(spec.get("episodes", []), start=1):
            v = tmp / f"{out.id}-{n:02d}.mp4"
            episode_file(v, spec["scene"], i * 10 + n, seconds, lecture=n)
            done = lib.attach_chapter_media(out.id, num=num, lang="EN", group="", src=v,
                                            sidecar={"filename": v.name, **stamp})
            if spec.get("subtitles") and n == 1 and done:
                srt = tmp / f"{out.id}-{n:02d}.en.srt"
                subtitle_file(srt, seconds)
                row = next(c for c in done.chapters if c.num == num)
                lib.attach_chapter_media(out.id, num=num, lang="EN", group="", src=srt,
                                         chapter_id=row.id, sidecar={"filename": srt.name})
    t = lib.get("paper-lanterns")
    lib.patch_user("paper-lanterns", UserPatch(
        read={t.chapters[0].id: "read", t.chapters[1].id: "reading"}))
    t = lib.get("harbour-lights")
    lib.patch_user("harbour-lights", UserPatch(read={t.chapters[0].id: "read"}))
    # The Sources page: invented sites under the reserved .example domain, so no
    # shot ever names a real one — each title came from one of them. Written
    # last and last-first, so "recently updated" lists the titles as TITLES does.
    for k, tid in enumerate(reversed(made)):
        domain = SITES[k % len(SITES)][0]
        lib.amend(tid, lambda d, domain=domain, tid=tid: setattr(
            d.meta, "source", SourceRef(domain=domain, url=f"https://{domain}/title/{tid}")) or True)
        time.sleep(0.05)  # a file time ticks coarsely: two writes in one tick would tie
    lib.source_prefs.set_groups(["Reading", "Archives"])
    for domain, group, links in SITES:
        lib.source_prefs.put(domain, group=group, bookmarks=[
            {"name": name, "url": f"https://{domain}/{path}"} for name, path in links])
    lib.close()


SITES = [
    ("kestrel-reader.example", "Reading", [("Latest updates", "latest"), ("Following", "follows")]),
    ("inkwell.example", "Reading", [("New this week", "new")]),
    ("lantern-press.example", "Archives", [("Collections", "collections")]),
    ("moonprint.example", "", []),
]


# A stand-in "source site" for the capture screenshot: entirely fictional, served
# from a local file, so the shot never shows (or touches) a real site.
DEMO_SITE = """<!doctype html><html><head><meta charset="utf-8"><title>Paper Lanterns — Kestrel Reader</title>
<style>
 body{margin:0;background:#12141a;color:#dfe3ea;font:15px/1.6 system-ui}
 .top{background:#191c24;border-bottom:1px solid #262a33;padding:12px 24px;font-weight:700;letter-spacing:.4px}
 .wrap{max-width:900px;margin:0 auto;padding:26px 24px;display:flex;gap:26px}
 .cover{width:220px;height:320px;flex:none;border-radius:8px;
   background:linear-gradient(160deg,#3b4a72,#212a44 60%,#161d2e);display:flex;align-items:center;
   justify-content:center;font:700 64px system-ui;color:#fff}
 h1{margin:0 0 6px;font-size:26px}
 .alt{color:#8b93a3;margin-bottom:14px}
 .row{display:flex;gap:10px;margin:8px 0;font-size:14px}
 .k{color:#8b93a3;width:88px;flex:none}
 .chip{background:#222735;border:1px solid #2e3444;border-radius:999px;padding:3px 10px;font-size:13px}
 .ch{max-width:900px;margin:0 auto;padding:0 24px 40px}
 .ch h2{font-size:16px;margin:22px 0 10px;color:#aeb6c6}
 .ch a{display:flex;justify-content:space-between;padding:10px 14px;border:1px solid #262a33;
   border-radius:8px;margin-bottom:8px;color:#dfe3ea;text-decoration:none;background:#171a21}
 .ch span{color:#8b93a3;font-size:13px}
</style></head><body>
<div class="top">KESTREL READER</div>
<div class="wrap">
  <div class="cover">PL</div>
  <div>
    <h1>Paper Lanterns</h1>
    <div class="alt">紙提灯 · Kami Chouchin</div>
    <div class="row"><span class="k">Author</span><span>Rin Aoyama</span></div>
    <div class="row"><span class="k">Artist</span><span>Rin Aoyama</span></div>
    <div class="row"><span class="k">Status</span><span>Ongoing · 2021</span></div>
    <div class="row"><span class="k">Genres</span><span class="chip">Drama</span><span class="chip">Slice of life</span></div>
    <div class="row"><span class="k">Tags</span><span class="chip">School</span><span class="chip">Friendship</span></div>
    <p style="max-width:46ch;color:#aeb6c6">A quiet coastal town, a paper lantern festival, and two
      friends who keep missing each other by a single street.</p>
  </div>
</div>
<div class="ch">
  <h2>Chapters</h2>
  <a href="#"><span>Chapter 3 — The tide comes back</span><span>EN · 2 days ago</span></a>
  <a href="#"><span>Chapter 2 — Nine hundred steps</span><span>EN · 3 weeks ago</span></a>
  <a href="#"><span>Chapter 1 — Festival eve</span><span>EN · a month ago</span></a>
</div>
</body></html>"""


# ---- driving the app --------------------------------------------------------

SHOOTER = r"""
'use strict'
// NOTE: the origin arrives through the ENVIRONMENT, never as a CLI argument —
// Chromium's own command-line parser swallows a bare URL and the process dies
// before the script runs at all.
const { app, BrowserWindow } = require('electron')
const fs = require('node:fs'), path = require('node:path')
// OFFSCREEN rendering: the page is painted into a buffer instead of a desktop
// window, so a capture never depends on the window being visible, focused or
// even on there being a desktop at all — an on-screen window simply stops
// producing frames when something else covers it, and capturePage() then hangs.
app.disableHardwareAcceleration()
const ORIGIN = process.env.LB_SHOT_ORIGIN, OUT = process.env.LB_SHOT_OUT
// Electron on Windows never attaches to the parent console, so every diagnostic
// goes to a file the caller prints afterwards.
const LOG = process.env.LB_SHOT_LOG
const logln = (s) => fs.appendFileSync(LOG, s + String.fromCharCode(10))
const DEMO_PAGE = process.env.LB_SHOT_DEMO_URL || ''
// the shell's app preload: without it the window is a plain web page, and the
// browser view says it only runs in the desktop app
const PRELOAD = process.env.LB_SHOT_PRELOAD
const wait = (ms) => new Promise((r) => setTimeout(r, ms))
// Nothing here may hang: a window Chromium decided not to paint makes
// capturePage() wait forever, and a busy renderer does the same to
// executeJavaScript. Every step is raced against a deadline instead.
function withTimeout(p, ms, what) {
  return Promise.race([p, new Promise((_r, rej) => setTimeout(() => rej(new Error('timed out: ' + what)), ms))])
}
const run = (win, js) =>
  withTimeout(win.webContents.executeJavaScript('(() => {' + js + '})()'), 8000, 'page script')


// Wait until the UI is ACTUALLY in the state we are about to photograph — a
// window that paints late would otherwise be caught mid-transition, or showing
// whatever came next.
async function until(win, js, what, tries = 30) {
  for (let i = 0; i < tries; i++) {
    if (await run(win, 'return !!(' + js + ')')) return
    await wait(300)
  }
  throw new Error('never became ready: ' + what)
}
async function shot(win, name, ready) {
  if (ready) await until(win, ready, name)
  await wait(900)
  let png = Buffer.alloc(0), pixel = '?'
  for (let i = 0; i < 5 && !png.length; i++) {
    try {
      const img = await withTimeout(win.webContents.capturePage(), 8000, 'capture ' + name)
      const bm = img.getBitmap()
      pixel = bm.length ? [bm[2], bm[1], bm[0]].join(',') : 'empty'
      png = img.toPNG()
    } catch { /* no frame yet — give the renderer another moment */ }
    if (!png.length) await wait(900)
  }
  if (!png.length) throw new Error('capture came back empty for ' + name)
  fs.writeFileSync(path.join(OUT, name + '.png'), png)
  logln('  ' + name + '.png  dom=' + (await run(win, "return document.documentElement.getAttribute('data-theme') + ' ' + getComputedStyle(document.body).backgroundColor")) + '  pixel=' + pixel)
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true })
  const win = new BrowserWindow({ width: 1280, height: 800, show: false, backgroundColor: '#0a0b0d',
    webPreferences: { offscreen: true, contextIsolation: true, nodeIntegration: false,
                      sandbox: true, webviewTag: true, preload: PRELOAD } })
  win.webContents.setFrameRate(30)
  await withTimeout(win.loadURL(ORIGIN + '/app/'), 20000, 'loading the app')
  await until(win, `document.querySelector('.lib .grid')`, 'the library')

  // the library tab goes on to a title opened from it: the shelf brings it back
  const backToLibrary = async () => {
    await run(win, `[...document.querySelectorAll('.navitem')].find((x) => /Library/.test(x.textContent))?.click()`)
    await run(win, `[...document.querySelectorAll('button')].find((b) => /^All types/i.test(b.textContent.trim()))?.click()`)
    await until(win, `document.querySelector('.lib .grid')`, 'back to the library')
  }
  const openCard = async (name) => {
    const q = JSON.stringify(name)
    await run(win, `[...document.querySelectorAll('.lib .gcard')].find((c) => c.querySelector('.gtitle')?.textContent.trim() === ${q})?.click()`)
    await until(win, `document.querySelector('.tv h1.title')?.textContent.trim() === ${q}`, name)
  }
  const loaded = (sel, n) =>
    `[...document.querySelectorAll(${JSON.stringify(sel)})].filter((i) => i.complete && i.naturalWidth).length >= ${n}`

  // Video first: the window cuts an episode's stills the first time it shows
  // it, and a title with no cover of its own wears them in the library.
  await openCard('Harbour Lights')
  await run(win, `document.querySelector('.tv .vseg .opt[title="List"]')?.click()`)
  await until(win, `document.querySelector('.tv .vcells')`, 'the contact sheet', 100)
  await shot(win, 'episode')

  // the same episode in the full window, stopped on a line of its subtitles
  await run(win, `[...document.querySelectorAll('.tv .pghead button')].find((b) => /Open player/.test(b.textContent))?.click()`)
  await until(win, `document.querySelector('.reader video')?.readyState >= 1`, 'the player', 60)
  await wait(600)
  await run(win, `const v = document.querySelector('.reader video'); v.muted = true; v.pause(); v.currentTime = 286`)
  await until(win, `(() => { const v = document.querySelector('.reader video'); return v.readyState >= 2 && !v.seeking && Math.abs(v.currentTime - 286) < 2 })()`, 'the seek', 60)
  await run(win, `document.querySelector('.reader video').pause()`)
  await shot(win, 'player')
  await run(win, `document.querySelector('.rback')?.click()`)
  await until(win, `document.querySelector('.tv')`, 'back on the title')

  // one person, every medium: the manga, its series and the artbook
  await run(win, `document.querySelector('.tv [title="Rin Aoyama — open the page"]')?.click()`)
  await shot(win, 'value-page', `document.querySelector('.vp .pname')?.textContent.includes('Rin Aoyama')`)

  // a course: lectures as cards across the width (a title of episodes opens on the wall)
  await backToLibrary()
  await openCard('Painting Light')
  await until(win, loaded('.tv .cards .card img', 4), 'the lecture posters', 100)
  await shot(win, 'course')

  // a series with no cover of its own: its tile will wear the stills cut here
  await backToLibrary()
  await openCard('Pale Harbour')
  // the view is kept per type, and the series above was put in the list
  await run(win, `document.querySelector('.tv .vseg .opt[title^="Wall"]')?.click()`)
  await until(win, loaded('.tv .cards .card img', 2), 'the episode posters', 100)

  // a photo set: every picture of every entry
  await backToLibrary()
  await openCard('Low Tide')
  await run(win, `document.querySelector('.tv .vseg .opt[title^="Pages"]')?.click()`)
  await until(win, loaded('.tv .pwtile img', 6), 'the photos', 60)
  await shot(win, 'gallery')

  // the library on its own: the titles opened on the way here closed, the filters folded
  for (let i = 0; i < 12 && await run(win, `return !!document.querySelector('.tact[title="Close"]')`); i++) {
    await run(win, `document.querySelector('.tact[title="Close"]').click()`)
    await wait(150)
  }
  await backToLibrary()
  await run(win, `document.querySelector('.lib button.acton[title="Filters"]')?.click()`)
  await wait(1500)  // covers are CSS backgrounds: nothing to ask whether they arrived
  await shot(win, 'library', `document.querySelectorAll('.lib .gcard').length >= 8 && !document.querySelector('.lib button.acton[title="Filters"]')`)

  // the light theme, with the filters open
  await run(win, `document.querySelector('button[title="Toggle theme"]')?.click()`)
  await run(win, `document.querySelector('.lib button[title="Filters"]')?.click()`)
  // the readiness check reads the PAINTED colour, not the attribute driving it
  await shot(win, 'library-light', `getComputedStyle(document.body).backgroundColor === 'rgb(240, 238, 234)' && document.querySelector('.lib button.acton[title="Filters"]')`)
  await run(win, `document.querySelector('button[title="Toggle theme"]')?.click()`)
  await until(win, `getComputedStyle(document.body).backgroundColor === 'rgb(10, 11, 13)'`, 'the dark theme again')

  // pages: a title, its reader, and its contents in edit mode
  await openCard('Paper Lanterns')
  await shot(win, 'title', `document.querySelector('.tv .ptile')`)
  await run(win, `document.querySelector('.ptile')?.click()`)
  await shot(win, 'reader', `document.querySelector('.reader .stage img')`)
  await run(win, `document.querySelector('.rback')?.click()`)
  await until(win, `document.querySelector('.tv')`, 'back on the title')
  // pages 2-4 picked, with a click and a Shift+click
  await run(win, `document.querySelector('.tv .chedit')?.click()`)
  await until(win, `document.querySelector('.tv .ptile.selectable')`, 'edit mode')
  await run(win, `const ts = document.querySelectorAll('.tv .ptile'); ts[1].click(); ts[3].dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: true }))`)
  await shot(win, 'contents-edit', `document.querySelectorAll('.tv .ptile.sel').length === 3`)
  await run(win, `[...document.querySelectorAll('.tv .chhead button')].find((b) => b.textContent.trim() === 'Done')?.click()`)

  // the capture browser: a local demo page in the webview, a fresh draft in the dock
  if (DEMO_PAGE) {
    await run(win, `[...document.querySelectorAll('.navitem')].find((x) => /Web/.test(x.textContent))?.click()`)
    await until(win, `document.querySelector('.bw webview, .bw .notice .btn')`, 'the browser view')
    await run(win, `[...document.querySelectorAll('.bw .notice .btn')].find((b) => /New tab/.test(b.textContent))?.click()`)
    await until(win, `document.querySelector('.bw webview')`, 'a browser tab')
    await run(win, `document.querySelector('.bw webview').src = ${JSON.stringify(DEMO_PAGE)}`)
    await wait(2500)
    await run(win, `[...document.querySelectorAll('.cp button, .cp .newrow, .cp .btn')].find((b) => /New (manga|title)/i.test(b.textContent))?.click()`)
    await shot(win, 'capture', `document.querySelector('.cp .fieldrow, .cp .body')`)
  }

  await run(win, `[...document.querySelectorAll('.navitem')].find((x) => /Sources/.test(x.textContent))?.click()`)
  await shot(win, 'sources', `document.querySelector('.srow')`)

  // Metadata on the types: every kind of thing the vault holds, and how many of each
  await run(win, `[...document.querySelectorAll('.navitem')].find((x) => /Metadata/.test(x.textContent))?.click()`)
  const typeRow = `[...document.querySelectorAll('#siderail button')].find((b) => b.querySelector('.slbl')?.textContent.trim() === 'Type')`
  await until(win, typeRow, 'the metadata rail')
  await run(win, `${typeRow}.click()`)
  await shot(win, 'metadata', `document.querySelector('.meta .h1')?.textContent.trim() === 'Type'`)
  app.quit()
}
// a last-resort watchdog: never leave a window sitting on the user's desktop
app.whenReady().then(() => { setTimeout(() => { logln('watchdog: giving up'); app.exit(3) }, 360000) })
app.whenReady().then(main).catch((e) => { logln('FAILED: ' + String((e && e.message) || e)); app.exit(1) })
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _QuietFiles(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass


def main() -> int:
    if not VENV_PY.exists():
        print(f"no virtualenv at {VENV_PY} — see CONTRIBUTING.md", file=sys.stderr)
        return 1
    if not ELECTRON.exists():
        print("Electron is missing — run `npm install` in shell/", file=sys.stderr)
        return 1
    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        print("building the UI…")
        subprocess.run(["npm", "run", "build"], cwd=ROOT / "frontend", check=True, shell=IS_WIN)

    work = Path(tempfile.mkdtemp(prefix="longbox-shots-"))
    vault, tmp = work / "vault", work / "tmp"
    tmp.mkdir(parents=True)
    port = free_port()
    print(f"demo library: {vault}")
    build_vault(vault, tmp)

    env_port = str(port)
    sidecar = subprocess.Popen(
        [str(VENV_PY), "-m", "uvicorn", "app.main:app", "--app-dir", "backend",
         "--host", "127.0.0.1", "--port", env_port],
        cwd=ROOT, env={**os.environ, "LONGBOX_LIBRARY_PATH": str(vault)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        origin = f"http://127.0.0.1:{env_port}"
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"{origin}/health", timeout=1) as r:
                    if json.loads(r.read())["status"] == "ok":
                        break
            except Exception:  # noqa: BLE001 — still starting
                time.sleep(0.5)
        else:
            print("the sidecar did not start", file=sys.stderr)
            return 1

        shooter = work / "shoot.js"
        shooter.write_text(SHOOTER, encoding="utf-8")
        site = work / "site"
        (site / "title").mkdir(parents=True)
        (site / "title" / "paper-lanterns.html").write_text(DEMO_SITE, encoding="utf-8")
        # served over http, not opened as a file: the shot's address bar would
        # otherwise print this computer's temp path — its user name included
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(_QuietFiles, directory=str(site)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        demo_url = f"http://127.0.0.1:{server.server_address[1]}/title/paper-lanterns.html"
        OUT.mkdir(parents=True, exist_ok=True)
        log = work / "shoot.log"
        log.write_text("", encoding="utf-8")
        node = shutil.which("node")
        if node:
            check = subprocess.run([node, "--check", str(shooter)], capture_output=True, text=True)
            if check.returncode:
                print("the generated capture script is invalid:", file=sys.stderr)
                print(check.stderr, file=sys.stderr)
                return 1
        print("capturing (offscreen - no window will appear):")
        subprocess.run([str(ELECTRON), str(shooter)], check=True,
                       env={**os.environ, "LB_SHOT_ORIGIN": origin, "LB_SHOT_OUT": str(OUT),
                            "LB_SHOT_DEMO_URL": demo_url,
                            "LB_SHOT_PRELOAD": str(ROOT / "shell" / "app-preload.js"),
                            "LB_SHOT_LOG": str(log)})
    finally:
        if log.exists():
            print(log.read_text(encoding="utf-8").rstrip())
        sidecar.terminate()
        shutil.rmtree(work, ignore_errors=True)
    print(f"done: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
