# longbox — project guide for Claude

A local-first desktop media vault with an embedded capture browser — pages, image sets and video
in a folder the user owns. Electron shell → FastAPI sidecar (owns the vault + SQLite index, serves
the built Vue 3 UI at `/app/`). The repository is public (GPL-3.0-or-later) and developed by one
owner with Claude.

Detailed conventions live in `.claude/rules/` (loaded by scope). Canonical design docs:
`design/state-model.md` (capture/edit/storage model), `ARCHITECTURE.md` (process shape, vault
layout, module map). User-facing docs, kept true to the app: `README.md` (short — what it is, where
to get it), `docs/index.html` (the project page: features, install, first-launch warnings),
`docs/guide.md` (the user guide, feature by feature), `CHANGELOG.md` (a section per release),
`ROADMAP.md` (what is next, in order; an item that ships leaves it for the changelog),
`CONTRIBUTING.md` (setup, checks, conventions, releases).

## Commands

```bash
python -m venv .venv && .venv/Scripts/python.exe -m pip install -e "backend[dev]"   # setup
(cd frontend && npm ci) && (cd shell && npm ci)                                      # setup
python run.py                                  # build frontend + launch the Electron shell
python run.py --backend                        # sidecar only, 127.0.0.1:8787 (dev, no auth)
cd backend && ../.venv/Scripts/python.exe -m pytest   # offline tests (Windows venv path)
cd frontend && npx vue-tsc --noEmit && npm run build  # type-check + build
node --check shell/main.js                     # after touching shell scripts
python tools/version.py                        # every version copy agrees with app-meta.json
python tools/version.py X.Y.Z                  # release bump: app-meta.json + every copy
python tools/screenshots.py                    # the project page's images, from a demo library
# the built UI over the TEST vault, for a smoke load of /app/ in the browser pane:
LONGBOX_LIBRARY_PATH=.dev/vault LONGBOX_CONFIG_DIR=.dev/config \
  .venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --port 8799
```

The version has ONE source, `app-meta.json`; never hand-edit a version anywhere else.

## Non-negotiable invariants

- **Scope:** longbox ships no content and no code for any particular website — no built-in
  scrapers, source catalogs, shared recipe packs, online metadata services or tracker sync. The
  vault's content is the user's responsibility; recipes are what each user builds.
- The **vault is the source of truth**; the SQLite index is a rebuildable cache.
- **The vault is home** (`design/vault-home.md`): EVERYTHING the app keeps lives in the vault it
  serves (titles, settings, the browser's profile, transfers, caches); the app's folder on the
  computer holds only `config.json` — where the vaults are. **Every path stored in the vault is
  relative to the vault** (forward slashes) and resolved against the vault open now when read, so
  a vault moved, or opened from another computer under another drive letter or a UNC path, works
  as it is. Only references to places outside the vault (an import's source folder) are absolute.
- **A launch reads the index, not the vault:** only an empty index or a session that never settled
  (`.longbox/session.json`) verifies the whole vault; the shell closes the library through
  `POST /api/close` before it stops the sidecar.
- **Lists are cards:** every index write keeps the title's card and filter values with its row
  (`title_cards_v1`, `title_values_v1`); a listing or a facet never parses a document.
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
- **Never touch the owner's real vaults** — not even to list or read them. Everything runs against
  the test vault in `.dev/` (ignored by git).
- The owner does the live testing in the running app. Verify with the test suite, type-check and
  build; for UI changes also smoke-load the built UI over the test vault, or drive it in an
  off-screen Electron harness — never a visible window without the owner's go-ahead.
- UI redesigns go mockup-first (`design/*.html`) and need approval before implementation.
- Commit each logical block as soon as it is done and verified (`.claude/rules/git.md`); push only
  when the owner asks for a release.
- A change that alters what a user sees or does also updates the docs that describe it (the guide,
  the project page and, at release, the changelog).
