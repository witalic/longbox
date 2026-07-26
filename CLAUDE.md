# longbox — project guide for Claude

Local-first desktop media vault (manga/manhwa/comics/image sets) with an embedded capture
browser. Electron shell → FastAPI sidecar (owns the vault + SQLite index, serves the built Vue 3
UI at `/app/`).

Detailed conventions live in `.claude/rules/` (loaded by scope). Canonical design docs:
`design/state-model.md` (capture/edit/storage model), `ARCHITECTURE.md` (process shape, vault
layout, module map).

## Commands

```bash
python run.py                                  # build frontend + launch the Electron shell
python run.py --backend                        # sidecar only, 127.0.0.1:8787 (dev, no auth)
cd backend && ../.venv/Scripts/python.exe -m pytest   # offline tests (Windows venv path)
cd frontend && npx vue-tsc --noEmit && npm run build  # type-check + build
node --check shell/main.js                     # after touching shell scripts
```

## Non-negotiable invariants

- The **vault is the source of truth**; the SQLite index is a rebuildable cache.
- **Provenance rule:** automatic capture may write only into `auto`/empty fields — a manual edit
  is untouchable.
- A meta commit **never** touches the user layer (fav/rating/read), and never orphans downloaded
  chapters (id adoption + media-backed restore in `_reconcile_chapters`).
- **Zip invariant:** every stored chapter archive is a plain zip; convert at ingest, refuse what
  can't be read, never store opaque files.
- Vault writes are atomic (`tmp → rename`) and run under the per-title lock
  (`Vault.title_lock` for load→mutate→commit sequences).

## Process

- Conversation with the owner is in Ukrainian; **code, comments, UI copy and docs are English**.
- The owner does all live in-app testing; verify changes with the test suite, type-check and
  build only.
- UI redesigns go mockup-first (`design/*.html`) and need approval before implementation.
- Do not commit or push unless explicitly asked.
