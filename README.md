# longbox

A local-first desktop **media vault** for manga, manhwa, manhua, comics and image sets — with an
**embedded capture browser**. Your library is plain files on disk; metadata is captured visually:
open the source site inside the app, click the elements that hold the title, authors, tags or
cover, and longbox learns a reusable per-site recipe.

## Features

- **Library** — cover grid, dense list and expanded views with linked faceted filters (type,
  status, genres, tags, languages, flags, authors, characters), full-text search, favorites,
  ratings, reading progress, bulk select/delete, and footer pagination.
- **Capture browser** — a real embedded browser (tabs, zoom, find-in-page, per-domain sessions).
  Pages are captured into an explicit **draft**: auto-fill from a learned recipe, or pick any
  element visually and refine the selector in the inspector. Per-field provenance guarantees an
  automatic capture never overwrites a manual edit.
- **Per-site recipes** — taught once per domain, stored versioned with ordered fallback
  candidates (picked selector → structural fallback → page metadata), reused for every next title
  on that site.
- **Downloads** — arm the next download for a specific chapter, then just download the file in
  the embedded browser; longbox intercepts it and files it into the vault. Archives (zip/cbz/7z/rar)
  are normalized to zip; single images accumulate into the chapter page by page.
- **Page capture** — for sources that never serve a file: teach which images on a reader page are
  the pages (once per site), arm a chapter, and read. Every page you open is fetched with the
  browser's own session and filed into that chapter; pages already stored are never fetched
  again, so flipping back costs nothing.
- **Contents editor** — entries with free-form labels, translation groups (language + group per
  row), drag-to-reorder, attach/replace archives, add loose images or whole folders, move pages
  between entries, and manual page reordering.
- **Reader** — page and strip modes, fit controls, a hideable rail of page thumbnails, label-wise
  chapter navigation that keeps your current translation, per-title remembered settings, and
  rebindable (layout-independent) hotkeys. Reading progress writes through instantly.
- **Authors & sources** — people aggregated from the library with roles, works and favorites;
  source sites with their recipe status.
- **Local-first storage** — one directory per title on a per-type shelf; a rebuildable SQLite
  index (deleting it never loses content); atomic writes; multiple switchable library locations.

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
design/     design docs (state-model.md is the canonical architecture) + HTML mockups
```
