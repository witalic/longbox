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
- **Lists hold cards, never whole titles.** A listing sends `TitleCard`s (no chapters):
  `store.cards` holds every title the window has listed (what a picker or a name match searches),
  `store.byId` only the titles it has opened. Ask for a whole title with `loadTitle` /
  `loadTitles` — never assume `store.byId` holds one because a list shows it. Something a list
  needs about a title belongs in the card (backend `cards.py`), not in a fetch per row.
- The user layer (fav/rating/read) is instant optimistic write-through — no draft, no confirm.
  Everything else commits explicitly through the draft.
- Key bindings are physical (`e.code`, via `keys.ts`) so they survive keyboard layouts.
- The window's preferences (theme, folds, defaults, keys, open tabs) go through `local.ts`
  (`readLocal`/`writeLocal` for JSON, `readLocalOne`/`writeLocalOne` for plain strings) — the one
  place that validates a read and swallows a failed write. They live in the vault
  (`settings.json`, loaded before the app mounts, written back in the background), never in
  `localStorage`; anything keyed by a title or describing the library belongs in its own vault
  document, through the API.
- A long-running activity (page capture) CARRIES its target with the work, never reading it
  back off reactive state across awaits — a Finish mid-flight would otherwise file results
  into the wrong entry. Queued page images each hold their own title/chapter, so the queue
  survives finishing a chapter and drains into the one it was scanned for.
- Navigation is the app tabs in `store.ts` (design/navigation.md): every place is a tab with
  its own Back / Forward. A view drawn in a tab reads its place through `useTab()`, never off
  global state, and a view kept alive behind another tab gates its loads with `useShown()`. A
  title's tabs go with it (`forgetTitleTabs`). Opening another vault is a restart into it
  (`switchVault` → the shell), so no view ever has to forget the vault it was drawn from.
- Covers/pages in grids load through `coverAt(...)` / the `?w=` endpoints — never decode
  full-size originals in a grid.
- Scoped-CSS note: shared form-row grammar rendered by child components (e.g. `EntryFields`) is
  styled from the host via `:deep()` so the CSS keeps one source.
- Shell: the app's folder holds `config.json` — and, for a vault that keeps its sign-ins on each
  computer, its browser profile under `browsers/<vault id>/` (design/sign-ins.md); otherwise the
  browser's profile is the vault's (`session.fromPath`, for `<webview>`s; `shell/profile.js` says
  which), the window's session is in memory, Chromium's default
  profile goes to temp. What the shell writes into the vault names nothing by the vault's own
  absolute path; the paths it hands the sidecar are made relative there (CLAUDE.md, "The vault
  is home"). Guard every `ipcMain` handler with an `e.sender` check, and remember
  frameless drag regions live in GLOBAL css (`html.frameless`), not scoped styles.
