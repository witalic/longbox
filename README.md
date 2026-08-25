# longbox

A local-first desktop **media vault** for manga, manhwa, manhua, comics, image sets and **episodes**
— with an **embedded capture browser**. Your library is plain files on disk; metadata is captured
visually: open the source site inside the app, click the elements that hold the title, authors,
tags or cover, and longbox learns a reusable per-site recipe.

## Features

- **Library** — cover grid, dense list and expanded views with linked faceted filters (type,
  status, genres, tags, languages, flags, authors, artists, characters, studio), full-text search
  over titles and alternate names, favorites, ratings, reading progress, bulk select/delete, and
  footer pagination. Shelves narrow the library by type; **Browse by** groups it along any list
  field, and each shelf keeps its own list of axes.
- **Capture browser** — a real embedded browser: tabs (pinning, reordering, background tabs,
  per-tab zoom and audio mute), a floating find bar, a page context menu (copy or save an image,
  open a link in a background tab), and the shortcuts a browser is expected to have — `F5`,
  `Ctrl+R` (`Shift` bypasses the cache), `Ctrl+F`, `Ctrl+L`, `Ctrl+T`/`W`/`Shift+T`, `Alt+←/→`,
  `Ctrl+0/±`, `Ctrl+1…9` — working over the page itself, not only over the app's own chrome.
- **Visual capture** — pages are captured into an explicit **draft**: auto-fill from a learned
  recipe, or pick any element and refine the selector in the inspector (scope, position, cleanup,
  and the separator a list picked into a one-value field is joined with). Per-field provenance
  guarantees an automatic capture never overwrites a manual edit; list fields can be **merged**
  across pages for works whose tags are spread over several of them. Auto-filling a NEW draft over
  a page the library may already hold asks first, showing what each candidate record *is*.
- **Per-site recipes** — taught once per domain, stored versioned with ordered fallback candidates
  (picked selector → structural fallback → page metadata), reused for every next title on that
  site. Each source decides which metadata fields it offers, and keeps its own bookmarks.
- **Fields you define** — the metadata registry is per library: add your own fields (text, list,
  number, date, boolean), group them, and choose which appear on title pages, in the filters and
  in the capture dock.
- **Downloads** — arm the next download for a specific chapter, then just download the file in the
  embedded browser; longbox intercepts it and files it into the vault. Archives (zip/cbz/7z/rar)
  are normalized to zip, single images accumulate into the chapter page by page, and a video is
  stored as itself. A panel over the app shows every transfer with its title, chapter and
  progress — and closing the window with transfers running warns first: each one keeps its place
  (partial file, byte offset, validators) and can be picked up later or started over.
- **Page capture** — for sources that never serve a file: teach which images on a reader page are
  the pages (once per site), arm a chapter, and read. Every page you open is fetched with the
  browser's own session and filed into that chapter; pages already stored are never fetched again,
  so flipping back costs nothing.
- **Episodes** — video is a kind of content, not an attachment: an episode is an entry like any
  other, with its own language, group, source and reading state. See below for what it does.
- **Contents editor** — entries with free-form labels, translation groups (language + group per
  row), numbering that follows the translation it belongs to, drag-to-reorder, attach/replace
  archives, add loose images or whole folders, move pages between entries, manual page reordering.
- **Reader** — page and strip modes, fit controls, a hideable rail of page thumbnails, label-wise
  chapter navigation that keeps your current translation, per-title remembered settings, and
  rebindable (layout-independent) hotkeys. Reading progress writes through instantly.
- **People & sources** — authors and artists aggregated from the library with roles, works and
  favorites; source sites grouped as you like, each with its recipe status and saved links.
- **Local-first storage** — one directory per title on a per-type shelf; a rebuildable SQLite index
  (deleting it never loses content); atomic writes; multiple switchable library locations.

## Episodes (video)

An episode is a chapter whose media is a video file. Everything a chapter has — the free-form
label, the language and group of a translation, its source link, read state — applies unchanged;
what differs is how it is stored, previewed and played.

**Getting one in.** All three lanes accept video:

- *Downloaded from a source* — arm the entry and download the file in the embedded browser, the
  same armed flow archives use. A video is stored **as the file it arrived as** (an episode gains
  nothing from a zip and would pay a full rewrite for every edit).
- *Files you already have* — drop one on the entry form of the contents editor, or pick it with
  *Choose files…*; whole folders work too. `mp4 · m4v · webm · mkv · mov · avi · ts` are
  recognised.
- *Replacing one* — attaching a new file to an entry replaces its media and carries the entry's
  identity, so read state and translation rows survive.

**What is stored beside it.** At ingest longbox records the duration, container, codec and
whether the file's index sits before the media; for `mp4`/`m4v` it moves the index to the front
when it does not (that is what makes playback start at once instead of fetching the tail first).
The **contact sheet** — nine frames laid out 3×3, plus the single frame a tile wears — is cut
once, by the window that plays the file (the app ships no decoder), and kept in the vault beside
the media. Nothing re-reads the video to draw a thumbnail again.

**Watching.** The title page previews the episode as that sheet, with each tile a link to the
second it came from, and plays it **in place** when asked — nothing is streamed before you press
Play. Arrow keys scrub it, the player's own controls (including fullscreen) work, and the resume
point survives leaving the page: reopening the title, or opening the episode in the full-window
player, carries on where you stopped. Reaching the end clears the point, so the next play starts
over.

**Files a browser cannot open.** `mkv`, `avi`, `mov` and `ts` are stored, listed and catalogued
like any other episode — they simply have no player here, and say so, with the file offered for
saving elsewhere. Nothing is silently dropped or hidden.

**Streaming, not copying.** An episode is served out of the vault with Range support and read in
windows, so seeking does not drag the whole file across a network drive, and a file that is
currently playing is never replaced or deleted under the player — the app says which entry to
close first.

## Install

Download a build for your system from the [releases page](https://github.com/witalic/longbox/releases/latest),
or read what it does first on the [project page](https://witalic.github.io/longbox/). Nothing else
is needed — Python and Node are only required to work on the source.

macOS builds are **ad-hoc signed**, not notarized: the first launch needs
right-click → *Open* to get past the unidentified-developer warning.

## Running (development)

Requires Python ≥ 3.11 (with a `.venv` at the repo root), Node.js and npm.

```bash
python run.py             # build the frontend, launch the Electron shell (spawns the sidecar)
python run.py --no-build  # skip the frontend rebuild
python run.py --backend   # only the FastAPI sidecar on 127.0.0.1:8787 (dev, no auth token)
```

Tests and checks:

```bash
cd backend && python -m pytest        # offline API + vault tests
cd frontend && npx vue-tsc --noEmit   # type-check
cd frontend && npm run build          # production build (served by the sidecar at /app/)
node --check shell/main.js            # after touching shell scripts
```

## Packaging

Builds run on GitHub Actions (`.github/workflows/build.yml`) for Windows, macOS (arm64 and x64)
and Linux: push a `v*` tag matching the version in `shell/package.json` to publish a release, or
run the workflow by hand for artifacts. Locally:

```bash
cd frontend && npm run build                                   # the UI the sidecar serves
pyinstaller packaging/sidecar.spec --noconfirm --distpath dist-sidecar --workpath build-sidecar
cd shell && npm run pack                                       # installers into dist-app/
```

A packaged app carries the sidecar as a frozen binary (with the built UI inside it) in the app's
resources, so it needs neither Python nor a checkout. `rar` ingest still needs an `unrar`/`bsdtar`
on the machine; every other archive format is handled in-process.

## Architecture

Electron shell → spawns a **FastAPI** (Python) sidecar → owns the vault (per-title directories +
SQLite index) and serves the built **Vue 3** UI at `/app/`. The embedded browser (`<webview>`)
hosts source sites; its preload does pick mode, live selector previews, one-shot page snapshots
and page-context cover fetches. See [ARCHITECTURE.md](ARCHITECTURE.md) for the process shape and
module map, and [design/state-model.md](design/state-model.md) for the full capture / edit /
storage model and its invariants.

## Repository layout

```
backend/    FastAPI sidecar: vault (layered title.json), SQLite index, chapter media, recipes, API
frontend/   Vue 3 + Vite + TS UI: library, title page, reader, capture browser, settings
shell/      Electron: sidecar spawn + auth, embedded browser, download interception, preloads
design/     design docs (state-model.md is the canonical architecture) + mockups and the GUI canvas
docs/       the project page published at witalic.github.io/longbox
```
