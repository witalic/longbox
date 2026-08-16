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
  on every launch (that is what once made the sidecar miss the shell's health timeout).
- **Layer separation.** A meta commit merges layers in the vault and never touches the user layer
  (fav/rating/read). `_reconcile_chapters` guards the no-orphan rule: re-captured rows adopt old
  ids (URL first, then num+lang+group; an adopted id leaves BOTH lookup maps), media-backed rows
  missing from a stale draft are restored (a bare archive without a sidecar still counts).
- **Type shelves.** `<root>/<type-shelf>/<title-id>/`; a type change physically relocates the
  directory under the title lock; empty shelves are swept; legacy layouts migrate on open and
  migration failures must never block startup.
- Sidecar `pages`/`size` are stamped by the vault from the file actually stored — routers never
  precompute them.
