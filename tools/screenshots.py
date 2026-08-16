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

TITLES = [
    dict(title="Paper Lanterns", alt="紙提灯", type="manga", status="ongoing", year="2021",
         authors=["Rin Aoyama"], artists=["Rin Aoyama"], characters=["Hana", "Toshi"],
         genres=["drama", "slice of life"], tags=["school", "friendship"], rating=5, fav=True,
         desc="A quiet coastal town, a paper lantern festival, and two friends who keep missing "
              "each other by a single street."),
    dict(title="Iron Meridian", type="manhwa", status="ongoing", year="2023",
         authors=["Seo Ha-eun"], artists=["Studio Kestrel"], characters=["Captain Vale"],
         genres=["action", "sci-fi"], tags=["mecha", "war"], rating=4,
         desc="A cartographer is drafted to map the only corridor through a storm that eats machines."),
    dict(title="The Quiet Bakery", type="manga", status="completed", year="2019",
         authors=["Mei Tanaka"], artists=["Mei Tanaka"], characters=["Oba-san", "Ken"],
         genres=["slice of life", "comedy"], tags=["food", "family"], rating=5,
         desc="Nothing happens in this bakery, gloriously, for twelve volumes."),
    dict(title="Salt & Ember", type="comic", status="ongoing", year="2022",
         authors=["A. Ferreira"], artists=["N. Okonkwo"], characters=["Wren"],
         genres=["fantasy", "adventure"], tags=["magic", "travel"], rating=4, fav=True,
         desc="A salt trader walks a caravan road that only exists at dusk."),
    dict(title="Glass Orchard", type="manhua", status="paused", year="2020",
         authors=["Lin Wei"], artists=["Lin Wei"], characters=["Xiao Yu"],
         genres=["mystery"], tags=["supernatural"], rating=3,
         desc="Every tree in the orchard remembers a different version of the same night."),
    dict(title="Northbound 404", type="comic", status="completed", year="2018",
         authors=["J. Halvorsen"], artists=["J. Halvorsen"],
         genres=["thriller"], tags=["road trip"], rating=4,
         desc="A courier, a highway that renumbers itself, and a package nobody ordered."),
    dict(title="Summer Static", type="manhwa", status="ongoing", year="2024",
         authors=["Park Jiwon"], artists=["Park Jiwon"], characters=["Nari"],
         genres=["romance", "drama"], tags=["music", "college"], rating=5, fav=True,
         desc="Two people who only talk during power cuts."),
    dict(title="Cartographer's Widow", type="manga", status="completed", year="2017",
         authors=["Yuki Sasaki"], artists=["Ken Mori"],
         genres=["historical", "drama"], tags=["sea"], rating=4,
         desc="She finishes the maps he started, and finds the coastline has moved."),
    dict(title="Neon Fieldwork", type="image set", status="", year="2024",
         authors=["Studio Halcyon"], genres=["artbook"], tags=["colour study"],
         desc="A colour study set: eighty frames of the same street at different hours."),
    dict(title="Tin Whistle Blues", type="manga", status="ongoing", year="2022",
         authors=["Ayaka Nomura"], artists=["Ayaka Nomura"],
         genres=["music", "drama"], tags=["band"], rating=3,
         desc="A busker's notebook, one song per chapter."),
    dict(title="The Long Commute", type="comic", status="ongoing", year="2023",
         authors=["R. Villanueva"], genres=["comedy"], tags=["office"], rating=3,
         desc="Forty minutes each way, forever, and it is somehow a love story."),
    dict(title="Pale Harbour", type="manhwa", status="completed", year="2016",
         authors=["Choi Minseo"], artists=["Choi Minseo"], characters=["Eun"],
         genres=["horror", "mystery"], tags=["fog"], rating=4,
         desc="The lighthouse keeps its light on for something that is not a ship."),
]

CHAPTERS = {
    "paper-lanterns": [("1", "EN", "Lantern Scans", 8), ("2", "EN", "Lantern Scans", 10),
                       ("2", "UA", "Ліхтарі", 10), ("3", "EN", "Lantern Scans", 9)],
    "iron-meridian": [("1", "EN", "Kestrel", 12), ("2", "EN", "Kestrel", 11)],
    "summer-static": [("1", "EN", "Static Club", 10)],
    "neon-fieldwork": [("2024 Set A", "", "Halcyon", 14)],
}

PALETTES = [
    ((32, 48, 58), (43, 65, 80), (26, 39, 48)), ((58, 37, 48), (81, 43, 58), (51, 27, 36)),
    ((35, 58, 47), (45, 81, 66), (26, 51, 39)), ((47, 42, 58), (67, 58, 88), (36, 31, 49)),
    ((58, 52, 31), (82, 72, 41), (51, 45, 23)), ((43, 33, 64), (58, 43, 82), (36, 27, 51)),
]


def _font(size: int):
    from PIL import ImageFont
    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def cover_bytes(title: str, seed: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFilter
    rnd = random.Random(seed)
    w, h = 800, 1200
    a, b, c = PALETTES[seed % len(PALETTES)]
    img = Image.new("RGB", (w, h), a)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a[i] + (c[i] - a[i]) * t) for i in range(3)))
    for _ in range(rnd.randint(3, 5)):
        x, y = rnd.randint(-100, w), rnd.randint(-100, h)
        r = rnd.randint(180, 460)
        d.ellipse([x, y, x + r, y + r], fill=b)
    img = img.filter(ImageFilter.GaussianBlur(48))
    d = ImageDraw.Draw(img)
    initials = "".join(word[0] for word in title.split()[:2]).upper()
    f = _font(260)
    box = d.textbbox((0, 0), initials, font=f)
    d.text(((w - box[2]) / 2, (h - box[3]) / 2 - 60), initials, font=f, fill=(255, 255, 255))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)
    return out.getvalue()


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
    out = io.BytesIO()
    img.save(out, "JPEG", quality=82)
    return out.getvalue()


def build_vault(root: Path, tmp: Path) -> None:
    from app.library.models import DraftIn, TitleMeta, UserPatch
    from app.library.service import Library

    random.seed(7)
    lib = Library(root)
    for i, spec in enumerate(TITLES):
        spec = dict(spec)
        rating, fav = spec.pop("rating", 0), spec.pop("fav", False)
        meta = TitleMeta(**spec)
        out = lib.create(DraftIn(meta=meta))
        lib.set_cover(out.id, cover_bytes(meta.title, i + 3), "jpg", "")
        if rating or fav:
            lib.patch_user(out.id, UserPatch(rating=rating, fav=fav))
        for j, (num, lang, group, pages) in enumerate(CHAPTERS.get(out.id, [])):
            z = tmp / f"{out.id}-{j}.zip"
            with zipfile.ZipFile(z, "w") as zf:
                for k in range(1, pages + 1):
                    zf.writestr(f"{k:03d}.jpg", page_bytes(k, pages, i + j))
            lib.attach_chapter_media(out.id, num=num, lang=lang, group=group, src=z,
                                     sidecar={"filename": z.name, "importedFrom": "local",
                                              "downloadedAt": "2026-08-01T10:00:00+00:00"})
    t = lib.get("paper-lanterns")
    lib.patch_user("paper-lanterns", UserPatch(
        read={t.chapters[0].id: "read", t.chapters[1].id: "reading"}))
    lib.close()


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
async function until(win, js, what) {
  for (let i = 0; i < 30; i++) {
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
                      sandbox: true, webviewTag: true } })
  win.webContents.setFrameRate(30)
  await withTimeout(win.loadURL(ORIGIN + '/app/'), 20000, 'loading the app')
  await shot(win, '01-library', `document.querySelector('.lib .grid')`)

  await run(win, `document.querySelector('.seg .opt[title*="Dense" i]')?.click()`)
  await shot(win, '02-library-dense', `document.querySelector('.lib .dense')`)
  await run(win, `document.querySelector('.seg .opt[title*="Covers" i]')?.click()`)
  await until(win, `document.querySelector('.lib .grid')`, 'back to grid')

  await run(win, `const c = [...document.querySelectorAll('*')].find((e) => e.className && String(e.className).includes('card') && /Paper Lanterns/.test(e.textContent)); (c || document.querySelector('.grid > *')).click()`)
  await shot(win, '03-title', `document.querySelector('.tv .ptile')`)

  await run(win, `document.querySelector('.tv > .viewscroll')?.scrollBy({ top: 430 })`)
  await shot(win, '04-contents', `document.querySelector('.tv .chrow')`)

  await run(win, `document.querySelector('.ptile')?.click()`)
  await shot(win, '05-reader', `document.querySelector('.reader .stage img')`)

  await run(win, `[...document.querySelectorAll('.rtop .iconbtn')].find((x) => /thumbnail/i.test(x.title || ''))?.click()`)
  await shot(win, '06-reader-rail', `document.querySelector('.reader .lrail .lthumb')`)

  await run(win, `document.querySelector('.rback')?.click()`)
  await until(win, `document.querySelector('.tv')`, 'back on the title')

  // the capture browser: a local demo page in the webview, a fresh draft in the dock
  if (DEMO_PAGE) {
    await run(win, `[...document.querySelectorAll('.navitem')].find((x) => /Browse/.test(x.textContent))?.click()`)
    await until(win, `document.querySelector('.bw webview')`, 'the browser view')
    await run(win, `document.querySelector('.bw webview').src = ${JSON.stringify(DEMO_PAGE)}`)
    await wait(2500)
    await run(win, `[...document.querySelectorAll('.cp button, .cp .newrow, .cp .btn')].find((b) => /New (manga|title)/i.test(b.textContent))?.click()`)
    await shot(win, '10-capture', `document.querySelector('.cp .fieldrow, .cp .body')`)
  }

  await run(win, `[...document.querySelectorAll('.navitem')].find((x) => /Authors/.test(x.textContent))?.click()`)
  await shot(win, '07-authors', `document.querySelector('.au .list')`)

  await run(win, `[...document.querySelectorAll('.navitem')].find((x) => /Settings/.test(x.textContent))?.click()`)
  await shot(win, '08-settings', `[...document.querySelectorAll('h1')].some((h) => h.textContent.trim() === 'Settings')`)

  await run(win, `document.querySelector('.themebtn')?.click()`)
  await run(win, `[...document.querySelectorAll('.navitem')].find((x) => /Library/.test(x.textContent))?.click()`)
  // the readiness check reads the PAINTED colour, not the attribute driving it
  await shot(win, '09-library-light', `getComputedStyle(document.body).backgroundColor === 'rgb(240, 238, 234)' && document.querySelector('.lib .grid')`)
  app.quit()
}
// a last-resort watchdog: never leave a window sitting on the user's desktop
app.whenReady().then(() => { setTimeout(() => { logln('watchdog: giving up'); app.exit(3) }, 240000) })
app.whenReady().then(main).catch((e) => { logln('FAILED: ' + String((e && e.message) || e)); app.exit(1) })
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    if not VENV_PY.exists():
        print(f"no virtualenv at {VENV_PY} — see the README", file=sys.stderr)
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
        demo_page = work / "demo-site.html"
        demo_page.write_text(DEMO_SITE, encoding="utf-8")
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
                            "LB_SHOT_DEMO_URL": demo_page.as_uri(),
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
