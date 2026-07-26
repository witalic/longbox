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
│  • transport: local (loopback) | remote (TLS + token)         │
└──────────────┬────────────────────────────────────────────────┘
               │ HTTP (localhost, or remote host)
┌──────────────▼─ FastAPI sidecar (Python) ─────────────────────┐
│  routers/   library · downloads · recipes · settings          │
│  library/   layered vault docs · chapter media (zip) ·        │
│             SQLite index (rebuildable)                        │
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
  index.db                   rebuildable cache
  authors.json               author favorites (user layer, vault-level)
  <type-shelf>/              one directory per TYPE (manga/, image-set/, …; typeless → other/)
    <title-id>/
      title.json             schema-versioned, layered:
                               meta        — written ONLY via draft → commit
                               provenance  — per-field origin (auto|manual) + source URL
                               chapters[]  — one row per translation; id = dedup key + file stem
                               user        — fav/rating/read; instant write-through
      cover.<ext>            captured cover bytes
      chapters/
        <chapter-id>.zip     the chapter's media — ALWAYS a plain zip (see below)
        <chapter-id>.json    download provenance sidecar (source, pages, size, date)
```

- **Type shelves.** A title lives on the shelf its `type` dictates; changing the type physically
  moves the directory (and sweeps the emptied shelf). Legacy flat layouts migrate on open.
- **The zip invariant.** Every stored chapter archive is a plain zip: cbz keeps its bytes under
  the `.zip` name, rar/7z are repacked at ingest, single downloaded images append as pages into
  the chapter's zip, and an unreadable file is rejected — never stored opaque. A startup pass
  normalizes pre-existing content. So every page operation (add / delete / move / reorder) works
  on every stored chapter.
- **No-orphan commits.** A meta commit reconciles chapters: a re-captured row adopts the old row's
  id (by URL, else by num+lang+group), and media-backed rows missing from a stale draft are
  restored — downloaded chapters are removed only by the explicit delete endpoints.

## Concurrency & durability

Endpoints run sync on FastAPI's threadpool, so races are real and handled structurally:

- Every write path is atomic (`tmp → rename`, unique temp names) and serialized by a per-title
  **reentrant lock keyed by the normalized id**; service-level load→mutate→commit sequences take
  the same lock end-to-end (`Vault.title_lock`).
- The meta layers and the user layer merge in the vault, so a commit can never roll back a star
  or read progress.
- App config uses `config_transaction()` (one process-wide lock + atomic save); the armed-download
  slot is consumed atomically; the index rebuild is single-flight.
- A corrupt index cannot lose content — it never owned any; rebuild rescans the files.

## Media & downloads

- Covers and chapter pages are served with cached LANCZOS downscales (`?w=`), keyed by file mtime
  so edits invalidate automatically. Tall webtoon pages crop in previews only, never in the reader.
- Downloads use the **armed flow** (`state-model.md` §9): the user arms ONE next download for a
  specific chapter; the shell intercepts `will-download`, streams progress, and hands the finished
  temp file to the sidecar for ingest (conversion to zip + sidecar provenance + row binding).
  Unarmed downloads are rejected at start.
- **The app never handles passwords.** The user signs in on the site inside the embedded browser;
  only the resulting session cookies persist (Electron session), per domain, revocable from the UI.
- Remote/S3/WebDAV storage stays a roadmap item; the seam would be a storage adapter behind `Vault`.

## Module map

```
backend/app/
  main.py security.py settings.py config_store.py
  library/  models.py (layers + flat DTO) · vault.py (dirs, locks, media ingest)
            media.py (zip ops, conversion, thumbnails) · index.py · service.py
  scraper/  models.py (recipe v2) · recipes.py (per-domain store) · covers.py (fallback fetch)
  routers/  library.py · downloads.py · recipes.py · settings.py
frontend/src/
  store.ts (app state, facets, user layer) · draft.ts (THE draft) · browser.ts (tabs)
  sessions.ts (tab-session restore) · keys.ts (physical-key bindings) · normalize.ts · data.ts
  views/      LibraryView · AuthorsView · TitleView · ReaderView · BrowserView ·
              SourcesView · SettingsView
  components/ MetadataEditor (the one editor) · CapturePanel (draft + downloads dock) ·
              PickInspector · EntryFields · Pager · Combo · …
shell/
  main.js (sidecar spawn, auth cookie, frameless chrome, will-download, session hardening)
  app-preload.js (folder picker, titlebar, browsing-data bridge) · pick-preload.js (pick/snapshot)
```
