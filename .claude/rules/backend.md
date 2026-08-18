---
paths:
  - "backend/**"
---

# Backend (FastAPI sidecar)

The vault is the source of truth; the SQLite index is a rebuildable cache (it may never own
content). Full model: `design/state-model.md`; layout: `ARCHITECTURE.md`.

- **Locking.** Endpoints run sync on a threadpool — races are real. Every vault write is atomic
  (`tmp → rename`, unique temp names) under the per-title reentrant lock, keyed by `safe_id`.
  Any service-level load→mutate→commit sequence takes `Vault.title_lock(title_id)` end-to-end.
  App-config read-modify-write goes through `config_transaction()` only.
- **Zip invariant.** Every stored chapter archive is a plain zip (`chapters/<id>.zip`). Ingest
  converts (cbz → rename, rar/7z → repack, single image → append as next page) and REJECTS what
  it can't read (`UnsupportedArchiveError` → 409/422) — never store opaque files, never let a
  page op rewrite a non-zip. Non-image entries (ComicInfo.xml, …) survive every rewrite.
- **Startup does the minimum.** Anything expensive that ingest already guarantees is a one-time
  migration keyed by a marker in `vault.json`, run on a background thread — never work repeated
  on every launch (that is what once made the sidecar miss the shell's health timeout). A launch
  serves from the index and verifies the vault on a BACKGROUND pass (`sync_in_background`);
  nothing user-facing waits for the disk. That pass holds no title locks, so its bulk write is
  guarded on both stamps — it may never overwrite a newer row.
- **A listing touches no files.** The index row carries the title's chapter sidecars, its cover
  URL and the mtimes they were read at, so `query()` is SQL plus composition. Anything a listing
  needs is written INTO the index at write time (`Library._index`), never fetched per title —
  on a network vault every stat is a round trip.
- **One scan, not three stats per title.** `Vault.scan()` reads the whole vault through
  `os.scandir`, whose entries already carry their stat data; per-title `is_file()` probing (six
  extensions for a cover, say) is what made opening a shared drive take twenty seconds.
- **A cache key must be unable to repeat.** Pages and covers are cached in the browser (by URL)
  and on disk (downscaled previews) under ONE version: `revision.size.pages` for a chapter,
  `mtime.size.ext` for a cover. Never version by page count (delete two, add two) or by a bare
  timestamp (copies carry it, shares round it) — a repeated version serves a deleted page back.
- **Layer separation.** A meta commit merges layers in the vault and never touches the user layer
  (fav/rating/read). `_reconcile_chapters` guards the no-orphan rule: re-captured rows adopt old
  ids (URL first, then num+lang+group; an adopted id leaves BOTH lookup maps), media-backed rows
  missing from a stale draft are restored (a bare archive without a sidecar still counts).
- **Type shelves.** `<root>/<type-shelf>/<title-id>/`; a type change physically relocates the
  directory under the title lock; empty shelves are swept; legacy layouts migrate on open and
  migration failures must never block startup.
- Sidecar `pages`/`size` are stamped from the file actually stored — routers never precompute
  them, and every page op writes the sidecar through `Library._write_media_sidecar` so `pageKeys`
  stays parallel to the pages (a key that outlives its page breaks capture dedup).
- The index write belongs INSIDE the title lock (`Library._index`), and all index statements go
  through the index's own lock — one shared SQLite connection means one transaction context.
