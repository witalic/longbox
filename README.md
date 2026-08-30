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
- **Fields you define** — the metadata registry is per library: add your own fields (text,
  description, number, list, date) and choose which appear on title pages, in the filters and in
  the capture dock. A field's type is a promise about its data, so number and date can be left but
  never entered, and a list only folds into text on a separator no value contains. Every filled
  field draws itself on the title page by its type — nothing to wire up per screen.
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
  favorites; source sites grouped as you like, each with its recipe status and saved links. A site
  becomes a source the moment you bookmark a page on it, and sits under **no recipe** until you
  teach it one.
- **Local-first storage** — one directory per title on a per-type shelf; a rebuildable SQLite index
  (deleting it never loses content); atomic writes; multiple switchable library locations.
- **Integrity** — every stored file is checksummed as it lands, and a revision pass checks the vault
  against its own records: damaged archives, files gone missing, records without media, leftovers
  from an interrupted write. Content stored twice is found by what the pages *are*, not by the file
  that holds them. Numbering gaps are reported only where entries provably form a run.
- **Portable metadata** — each archive carries a `ComicInfo.xml` describing its work, so the library
  reads correctly in Komga, Kavita and anything else that speaks the format, and outlives this app.
  An archive that already describes itself names its own entry on the way in.

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

## Settings

One section at a time, chosen from the rail the rest of the app already uses, each carrying its
own state so you can see what needs you without opening it: **Storage** (library folders),
**Health** (below), **Maintenance** (rebuild the index, convert archives, write metadata into
them), **Fields**, **Browser**, **Appearance**, **Keyboard**, **About**.

Everything that walks the library — a check, a conversion, a sweep, a field retype — takes the
same single slot, shows the same progress and can be stopped from where it was started. The
sidecar names what is running, so leaving the page does not lose it.

## Keeping the vault honest

The vault is the source of truth, so the app can be asked to check it — nothing here runs by
itself, and nothing changes your files.

**Checksums.** Every chapter is hashed twice as it is stored: once over the whole file (what
integrity means) and once over its ordered page images (what "the same chapter" means). The
second survives a repack, a renumbering and the metadata mirror, which is why duplicate detection
uses it and corruption detection does not.

**One check, three answers** (Settings → Health): is anything broken, is anything wasting space,
is anything missing. Duplicates and numbering gaps read the index, so they cost nothing on top of
the walk and are never a separate thing to ask for. Answers are sentences about files — *the file
is gone*, *the file changed since it was stored* — and each says what to do about it: a broken row
opens its title, where the contents editor can replace or remove it. A quick check compares disk
against the record; a full check re-reads every byte; either can be stopped, and a report cut
short says so. Content stored before checksums existed can be baselined — reported as exactly
that, because a checksum first taken today proves stability from today, not that the bytes are
what arrived.

**Leftovers** — files belonging to no entry at all, and the debris of interrupted writes — are the
one thing only this screen can reach, so it is the one place they can be deleted, after listing
every one with its size. Duplicate copies are never deleted for you: which one to keep is a
judgement, so the report opens each instead.

At scale the report stays a report: one row per *title and problem*, so forty broken chapters in
one title read as one problem with that title, lists are capped with the totals stated in full,
and a library that fails all at once is named as what it is — a folder that moved, not three
thousand damaged files.

**Numbering gaps** are deliberately quiet. A vault holds image sets, one-shots and hand-ordered
contents, so a run is only claimed when the entries prove it: a translation of its own, at least
five of them, almost all numbered, densely covering their span, and never a hand-made order.
Fractions (`10.5`) are entries, not missing ones, and nothing is ever claimed beyond the highest
number held.

**What has been done** is recorded with the library: every operation over the vault leaves when
it ran, how long it took and what came of it, and the last report is kept in full — so a check
that took an hour is still there after a restart, and "has this library ever been checked" has an
answer.

**ComicInfo.xml** is a mirror, not a second source of truth. It is written where the archive is
being rewritten anyway — at ingest and on every page edit — so a metadata edit never silently
rewrites gigabytes; Settings brings the rest in line on demand, rewriting only what differs.

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
