# Architecture

The canonical capture / edit / storage model — the three state domains, the snapshot, per-field
provenance, recipes and the invariant map — lives in **`design/state-model.md`**. This file covers
the process shape, transport/auth, the vault layout as implemented, and what each module owns.

## Shape

```
┌─ Electron shell ──────────────────────────────────────────────┐
│  • main UI  (Vue, served by the backend at /app/)             │
│  • embedded browser <webview> — source sites + pick/snapshot  │
│  • will-download interception → armed-download ingest         │
│  • browser keys, page context menu, fullscreen for OUR pages  │
│  • transport: local (loopback) | remote (TLS + token)         │
└──────────────┬────────────────────────────────────────────────┘
               │ HTTP (localhost, or remote host)
┌──────────────▼─ FastAPI sidecar (Python) ─────────────────────┐
│  routers/   library · downloads · recipes · settings · import │
│  library/   layered vault docs · chapter media (zip/episode)  │
│             · SQLite index (rebuildable)                      │
│  scraper/   recipe storage (client extracts; server stores)   │
└───────────────────────────────────────────────────────────────┘
```

State splits into three domains with one-way flow (`state-model.md` §2):

- **Browser** — the live webview; belongs to the site. The app reads it only via explicit
  snapshots, never directly into UI state.
- **Draft** — the working copy being built/edited (`frontend/src/draft.ts`); independent of
  navigation; merges snapshots under the provenance rule (auto never overwrites manual).
- **Vault** — committed files on disk; the only source of truth. SQLite is a rebuildable cache.

Extraction runs **client-side against the rendered DOM** (the webview preload): recipes are
ordered candidate lists (picked CSS selector → structural fallback → og/JSON-LD metadata), and
cover bytes are fetched **through the page's context** (its cookies + referer) so gated and
hotlink-protected images capture exactly what the user sees. The backend stores, validates and
serves; it never scrapes HTML itself (the only server-side fetch is the cover-URL fallback).

## Two transport modes (decided up front)

| Mode     | Bind        | Credential                        | Transport |
|----------|-------------|-----------------------------------|-----------|
| `local`  | 127.0.0.1   | per-launch cookie secret          | plain HTTP on loopback |
| `remote` | host:port   | persistent bearer token (keychain)| **TLS required** |

The backend guard accepts the matching credential and rejects non-loopback `Host` in `local` mode
(anti DNS-rebinding). The shell verifies it reached *its own* sidecar via the `/health` token-hash
echo before loading the UI. **Never send the library or a token over a non-TLS remote connection.**
`local` is what runs today; `remote` lands with the remote phase.

## The vault on disk (full schema in state-model.md §8)

```
library/
  index.db                   rebuildable cache — plus the journal of bulk metadata runs and
                             the rules' fired counts, which a rebuild leaves alone
  vault.json                 one-time migration markers (zipNormalized, videoFaststart)
  fields.json                the library's own metadata fields (registry additions)
  authors.json               author favorites (user layer, vault-level)
  recipes.json               per-site recipes (versioned per domain) + the page-capture filter
  sources.json               the owner's layer over the sites: group order, per-site group,
                             bookmarks, frozen; sites removed from Sources
  rules.json                 what a value becomes on its way into a title (ordered)
  import-profiles.json       how folders already on disk are read into works (no paths)
  health.json                what was done to this library + the last check report — its
                             own file, so startup never parses it
  <type-shelf>/              one directory per TYPE (manga/, image-set/, …; typeless → other/)
    <title-id>/
      title.json             schema-versioned, layered:
                               meta        — written ONLY via draft → commit
                               provenance  — per-field origin (auto|manual) + source URL
                               chapters[]  — one row per translation; id = dedup key + file stem
                               user        — fav/rating/read/position; instant write-through
      cover.<ext>            cover bytes; further covers as cover-2.<ext> … cover-8.<ext>
      chapters/
        <chapter-id>.zip     page media — ALWAYS a plain zip (see below)
        <chapter-id>.<video-ext>  episode media (mp4 m4v webm mkv mov avi ts) — stored
                             as the file it arrived as
        <chapter-id>.json    download provenance sidecar (source, the name the file
                             ARRIVED under, pages, size, revision, page keys, sha256 of
                             the file + content digest of the pages, the ComicInfo stamp,
                             and for an episode: container, playable, codec, faststart,
                             duration, stills)
        <chapter-id>.poster.jpg  the one frame a tile wears
        <chapter-id>.sheet.jpg   the 3x3 contact sheet a preview shows
```

- **The vault carries everything that describes the library; the app config carries the
  machine.** Recipes, the source layer, fields, author favorites and how the reader shows each
  title all live in the vault, so a library moved to another machine arrives whole. The config
  dir keeps only what cannot travel: where this machine's libraries are, transfers it was in
  the middle of, the thumbnail cache — and the Electron session (sign-ins). An older build kept
  recipes and the source layer in the config; on first start this build moves them into the
  library it opens and removes them there (`legacy_config.py`).
- **Bulk metadata changes** (`design/metadata-management.md`) cost the titles that CHANGE, not
  the library: the change is worked out from the index (that is the preview), each changed
  title is read once and written once (`Vault.rewrite_in_place`, its stamp stepped by two
  seconds so the "updated" order holds), and the index refreshes in batches without reading a
  chapter directory (`LibraryIndex.apply_meta`, guarded by the stamp so it never overwrites a
  newer row). A type renamed for a whole shelf into a new one is one directory rename. Every
  run is journaled in `index.db` and revertible; the rules that change values on their way in
  live in `rules.json` and run in the sidecar, for capture, typing and new chapters alike.
- **Vault-level documents** (`recipes.json`, `sources.json`, `rules.json`,
  `import-profiles.json`) go through
  `Vault.read_doc()` and `Vault.doc_transaction()`: one lock per document PATH (two Vault objects over one directory
  still take turns), atomic write. "There is none" is the only empty answer a read gives: a
  client told "nothing saved" builds its next write on it, and that write replaces what is
  there. So a file that cannot be parsed answers 409 to reads and writes alike, a read that
  failed (a share that blinked) raises as itself, and a document from a newer build is read
  but never written. The client refuses the same way: a recipe or a whole-list write
  (bookmarks, group order) is built only on what was actually read.
- **How the reader shows a title** lives in its `user` layer, but a write of only that moves
  the document's mtime — the library's "updated" order — by two seconds instead of to "now":
  a zoom tweak never lifts a title to the top of the shelf, yet the file still reads as
  changed to backups, mirrors and other indexes (two seconds outlast FAT's and a share's
  rounding).
- **Covers are a list, and its order is the file names.** A title may store up to eight; the first
  keeps the name every older vault already writes, so nothing has to be migrated and a rebuilt
  index reconstructs the order from the directory alone. A title that stores NONE shows
  stand-ins cut from its own media — the first page of each downloaded chapter, or an episode's
  stored poster, up to six — composed from the sidecars the index already carries, so a
  coverless title costs no disk. `covers` is what a tile shows; `ownCovers` is what somebody actually gave it,
  which is the question the editor asks.
- **A pass re-indexes what it touched.** The one-time vault passes (the zip sweep, the episode
  pass) return the title ids they rewrote, and those rows are refreshed directly. They do NOT
  fall back on `sync()`: sync finds changes nobody announced, and it finds them by comparing
  directory timestamps — which a filesystem records at the resolution of a clock tick, so a
  pass writing inside the same tick as the row it replaces is invisible to it. A pass knows
  what it changed; there is nothing to detect.
- **Type shelves.** A title lives on the shelf its `type` dictates; changing the type physically
  moves the directory (and sweeps the emptied shelf). Legacy flat layouts migrate on open.
- **The zip invariant.** Every stored chapter archive is a plain zip: cbz keeps its bytes under
  the `.zip` name, rar/7z are repacked at ingest, single downloaded images append as pages into
  the chapter's zip, and an unreadable file is rejected — never stored opaque. So every page
  operation (add / delete / move / reorder) works on every stored chapter. Because ingest enforces
  this, the whole-vault sweep is a **migration, not a startup chore**: it runs once per vault
  (marked by `zipNormalized` in `vault.json`), on a background thread, and Settings can re-run it
  for content dropped into the folder by hand. Its sibling, the episode pass (`videoFaststart`),
  moves an older mp4's index to the front once per vault, the way ingest does for new ones.
- **Episode stills.** The app ships no video decoder, so a frame cannot be cut on the backend —
  but the WINDOW decodes these files to play them. Episodes on screen get their stills one at a
  time, once each: a tile needs only a poster (three spots, the liveliest wins); a preview asks
  for the sheet — nine spots composed 3x3, which has exactly the aspect ratio of one frame, so
  the preview and the player share a box — and that job supplies the poster too if none is
  stored. Both go to the vault. They carry a version of their OWN (the `stills` counter plus a
  digest of the stored stills), never the media revision: bumping that re-points the `<video>`
  of an episode that may be playing right then.
- **No-orphan commits.** A meta commit reconciles chapters: a re-captured row adopts the old row's
  id (by URL, else by num+lang+group), and media-backed rows missing from a stale draft are
  restored — downloaded chapters are removed only by the explicit delete endpoints. A row keeps
  its id for life, so a new row whose derived id is still held by a renumbered one gets a
  suffixed id (`_free_id`) rather than being dropped as its twin.

## Concurrency & durability

Endpoints run sync on FastAPI's threadpool, so races are real and handled structurally:

- Every write path is atomic (`tmp → rename`, unique temp names) and serialized by a per-title
  **reentrant lock keyed by the normalized id**; service-level load→mutate→commit sequences take
  the same lock end-to-end (`Vault.title_lock`) — **including the index write**, so a doc read in
  a critical section can never be indexed after a newer writer's.
- The meta layers and the user layer merge in the vault, so a commit can never roll back a star
  or read progress.
- The SQLite index is one connection shared by the threadpool, so every statement goes through
  one lock: a reader can't observe a rebuild's half-emptied table, and no other writer can commit
  it. `sort=updated` reads the document's own mtime, so a rebuilt cache keeps the shelf order.
- App config uses `config_transaction()` (one process-wide lock + atomic save); the armed-download
  slot is consumed atomically; index rebuild and the archive sweep are each single-flight.
- Switching the library opens the new location BEFORE the config records it — an unreadable vault
  fails the request instead of stranding the next launch — and the old one closes on a delay.
- A corrupt index cannot lose content — it never owned any; rebuild rescans the files, skipping
  documents it cannot parse rather than aborting.
- **One vault pass at a time** (`backend/app/passes.py`). Checking the vault, converting
  archives, rewriting metadata, sweeping leftovers, retyping or clearing a field, rebuilding
  the index, and a bulk metadata run or its revert all walk the same library, so they share one slot, one progress record and one stop
  event. (The one-time startup migrations run outside it, single-flight on their own thread.)
  The SERVER names the running pass and
  which row started it, so leaving the settings page — or reloading it — cannot lose the progress
  of something still running. Every pass reports its size before its first slow step and says
  whether it was put down early; a report that covered part of the library never claims to have
  covered it.

## Integrity, and the passes that check it

- **Two digests, two jobs.** `sha256` covers every byte of the stored file: that is what
  integrity means, and the revision pass compares it. `content` covers only the ordered page
  images, so it survives a repack, a renumbering and the per-entry `ComicInfo.xml`; duplicate
  detection uses that one. Both are stamped wherever media is written (ingest, page ops, the zip
  sweep) and never by a pass that only reads — except a full check asked to *baseline*, which
  records a first `sha256` (never `content`) for media stored before digests existed. Writing
  the metadata mirror restamps `sha256` and `size` but never `rev` — the pages did not change,
  so no cached page may miss.
- **One check, three answers**: is anything broken, is anything wasting space, is anything
  missing. Duplicates and numbering gaps read the index, so they cost nothing on top of the walk.
  The composition from mechanism (`orphan`, `stray`, `noDigest`) to what a person is told lives in
  one pure function, which is what lets the mechanism keep its own vocabulary and its own tests.
- **At scale the report stays a report**: the row unit is (title × problem), lists are capped with
  the totals stated in full, and a library that fails all at once is named as a folder that moved
  rather than three thousand damaged files.
- **`health.json`** (its own file, so startup never parses it) records what has been done to this
  library — when, how long, and the outcome in one sentence — plus the last report in full, so a
  full check that took an hour survives closing the app.
- **`ComicInfo.xml` is a mirror**, written where the archive is being rewritten anyway; the
  sidecar records which one it last got, so a settled chapter is skipped without opening it. A
  metadata edit does not rewrite archives — `refresh_comicinfo()` is the explicit half, and it
  narrows to the titles whose stored mirror no longer matches. It is read back only when an
  import names no number, and only into entry fields the caller left empty — a typed label is
  a decision and outranks a file.
- **`Vault.chapter_files()`** reads a title's chapter directory ONCE. Asking per chapter costs up
  to ten filesystem round trips each, which is the difference between seconds and minutes on a
  network vault; anything walking the library uses the listing.

## Media & downloads

- Covers, chapter pages and episode stills are served with LANCZOS downscales (`?w=`), cached in
  the app's cache directory under the version from `library/versions.py` (a chapter's
  `rev.size.pages`, a cover's `mtime.size.ext`, an episode's `stills.digest`) — never by page
  count or a bare mtime, so an edit misses the cache by construction. Tall webtoon pages crop in previews only, never in the reader.
- Downloads come in two lanes, chosen in the capture dock's Downloads tab:
  - **Archive** — the **armed flow** (`state-model.md` §9): the user arms ONE next download for a
    specific chapter; the shell intercepts `will-download`, streams progress, and hands the
    finished temp file to the sidecar for ingest (conversion to zip + sidecar provenance + row
    binding). Unarmed downloads are rejected at start.
  - **Page capture** — for sources that only render pages. The same explicit binding: the user
    names ONE entry and arms it, then reads; finishing the chapter is explicit and steps the label
    forward. The page-image selector is taught once per domain (pick mode → the recipe's `pages`
    rule). Each page view is scanned, the vault is asked what the armed entry already holds
    (`pages/known`), and only the rest are fetched — by the shell's main process, with the
    session's cookies and the reader page as referer (the page context is only the fallback, for
    `blob:`/`data:` or refused images) — and appended (`pages/capture`). Dedup is by the image's **file name**, recorded in the sidecar
    (`pageKeys`) — CDN URLs carry rotating tokens, names don't — so flipping back re-downloads
    nothing; unreadable media is distrusted and re-captured. `pageKeys` runs parallel to the
    pages, so deleting, moving or reordering pages carries their keys with them. Stored format is
    decided by the BYTES (a CDN's content-type is routinely wrong).
- **Episodes stream, they do not copy.** The chapter's own file is served with Range support; an
  open-ended `bytes=N-` is answered with an 8MB WINDOW rather than the whole tail, because the
  player abandons the rest and reconnects per fragment (measured: 0.29x playback with 83 buffer
  fragments against 0.97x with 8). Deleting or replacing a file something still streams is a
  Windows `PermissionError`, so the media goes first and a refusal leaves the chapter untouched,
  answered as 409 with what to close.
- **Interrupted transfers outlive the run.** Stopping everything for a quit keeps each transfer's
  partial file and records — in the app config, not in memory — the chapter it was claiming, the
  byte offset, the URL chain and the `ETag`/`Last-Modified` it would have to match. A later launch
  lists them, re-arms the chapter and asks the session to re-open the file at that offset; whether
  the range is honoured is the source's call, and the app offers to start over when it is not.
- **The app never handles passwords.** The user signs in on the site inside the embedded browser;
  only the resulting session cookies persist (Electron session), per domain, revocable from the UI.
- Remote/S3/WebDAV storage stays a roadmap item; the seam would be a storage adapter behind `Vault`.

## Module map

```
backend/app/
  main.py security.py settings.py config_store.py (this machine's config)
  legacy_config.py (moves what an older build kept in the config into the vault, once)
  passes.py (the one vault pass at a time: progress, stop, who is running)
  library/  operations.py (bulk metadata: apply, preview from the index, run, revert) ·
            rules.py (what a value becomes on its way in; rules.json) ·
            folder_import.py (a folder already on disk: profiles, scan, plan, run) ·
            models.py (layers + flat DTO) · vault.py (shelves, locks, media ingest, covers,
            the vault/fields/authors/health files) · media.py (zip ops, conversion, thumbnails,
            mp4 probe + faststart, digests) · index.py · service.py ·
            migrations.py (title.json schema steps) · versions.py (versions + cache keys) ·
            fields.py (the metadata registry: builtin + per-library custom) ·
            comicinfo.py (the ComicInfo.xml mirror: build, parse, write into an archive)
  scraper/  models.py (recipe v2, page filter) · recipes.py (recipes.json) ·
            sources.py (sources.json) · covers.py (fallback fetch)
  routers/  library.py · downloads.py · recipes.py · settings.py · metadata.py · imports.py
frontend/src/
  App.vue (the frame: views, tabs, downloads panel, close guard) · styles.css (the design tokens)
  store.ts (app state, facets, user layer, downloads) · draft.ts (THE draft) · browser.ts (tabs)
  api.ts (the sidecar client) · local.ts (validated localStorage) · data.ts (domain helpers)
  sessions.ts (tab-session restore) · keys.ts (physical-key bindings) · normalize.ts
  pagecapture.ts (the page-capture activity) · frames.ts (episode stills, cut in the window)
  views/      LibraryView · AuthorsView (browse by any axis) · TitleView · ReaderView ·
              BrowserView · SourcesView · MetadataView (values, rules, history) ·
              ImportView (a folder on disk: structure, metadata, preview, run) · SettingsView
  components/ MetadataEditor (the one editor) · ImportMeta (its fields over an import's values) ·
              CapturePanel (draft + downloads dock) ·
              PassButton + ProgressBar (run/progress/stop, one grammar everywhere) ·
              Settings* (one component per settings section) ·
              VideoSurface (preview + player, one per app) · DownloadsPanel · PickInspector ·
              CoverArt (covers, rotating on hover) · EntryFields · FilterBlock · FieldVisibility ·
              MenuButton · RailSection · SearchBox · Pager · Combo · ConfirmModal · Dropdown ·
              Stars · Icon
shell/
  main.js (sidecar spawn on a stable port + /health handshake, auth cookie, frameless chrome,
           will-download + resume, page-image fetches, browser keys, page context menu,
           close guard, browsing data, session hardening)
  app-preload.js (folder picker, titlebar, browsing data, download control, close guard,
                  page-image fetch, page keys)
  pick-preload.js (pick/snapshot, page scan, page-context fetch fallback, background-tab links)
```
