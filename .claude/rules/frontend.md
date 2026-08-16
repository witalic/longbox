---
paths:
  - "frontend/**"
  - "shell/**"
---

# Frontend (Vue 3 + TS) & shell (Electron)

- **Three stores, one-way flow** (`design/state-model.md`): `store.ts` (app/library/user layer),
  `draft.ts` (THE draft — seeded on purpose, never mirrors "whatever page I'm on"),
  `browser.ts` (tabs only). The merge invariant: auto capture writes only into `auto`/empty
  fields; manual is untouchable. `pagecapture.ts` holds the page-capture ACTIVITY (not persisted
  state) so browser.ts stays free of capture concerns.
- The user layer (fav/rating/read) is instant optimistic write-through — no draft, no confirm.
  Everything else commits explicitly through the draft.
- Key bindings are physical (`e.code`, via `keys.ts`) so they survive keyboard layouts.
- Never touch `localStorage` directly: `local.ts` (`readLocal`/`writeLocal` for JSON,
  `readLocalOne`/`writeLocalOne` for plain string prefs) is the one place that validates a read
  and swallows a denied write — a stale key from an older build must not crash a view.
- A long-running activity (page capture) CARRIES its target with the work, never reading it
  back off reactive state across awaits — a Finish mid-flight would otherwise file results
  into the wrong entry. Queued page images each hold their own title/chapter, so the queue
  survives finishing a chapter and drains into the one it was scanned for.
- Cleanup discipline for per-title state: forget `openTabs` / `pinnedTabs` / `readerTabs`
  together (`forgetTab`), and reset them all when the library path switches.
- Covers/pages in grids load through `coverAt(...)` / the `?w=` endpoints — never decode
  full-size originals in a grid.
- Scoped-CSS note: shared form-row grammar rendered by child components (e.g. `EntryFields`) is
  styled from the host via `:deep()` so the CSS keeps one source.
- Shell: keep the sidecar port stable (`shell.json`), guard every `ipcMain` handler with an
  `e.sender` check, and remember frameless drag regions live in GLOBAL css (`html.frameless`),
  not scoped styles.
