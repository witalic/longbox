---
paths:
  - "frontend/**"
  - "shell/**"
---

# Frontend (Vue 3 + TS) & shell (Electron)

- **Three stores, one-way flow** (`design/state-model.md`): `store.ts` (app/library/user layer),
  `draft.ts` (THE draft — seeded on purpose, never mirrors "whatever page I'm on"),
  `browser.ts` (tabs only). The merge invariant: auto capture writes only into `auto`/empty
  fields; manual is untouchable.
- The user layer (fav/rating/read) is instant optimistic write-through — no draft, no confirm.
  Everything else commits explicitly through the draft.
- Key bindings are physical (`e.code`, via `keys.ts`) so they survive keyboard layouts.
- Validate every `localStorage` read against expected values — a stale key from an older build
  must not crash a view.
- Cleanup discipline for per-title state: forget `openTabs` / `pinnedTabs` / `readerTabs`
  together (`forgetTab`), and reset them all when the library path switches.
- Covers/pages in grids load through `coverAt(...)` / the `?w=` endpoints — never decode
  full-size originals in a grid.
- Scoped-CSS note: shared form-row grammar rendered by child components (e.g. `EntryFields`) is
  styled from the host via `:deep()` so the CSS keeps one source.
- Shell: keep the sidecar port stable (`shell.json`), guard every `ipcMain` handler with an
  `e.sender` check, and remember frameless drag regions live in GLOBAL css (`html.frameless`),
  not scoped styles.
