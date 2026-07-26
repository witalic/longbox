# Browser page — full refactor (design)

Status: **proposal for approval**. Nothing implemented yet. Replaces the current `ScraperView`
entirely (interface + logic).

## Why we're redoing it

The current Browse page conflates three different things into one tangled panel:

1. **A browser** (navigate source sites).
2. **The page you're looking at** (its live DOM/metadata).
3. **The manga you're building/editing** (the draft record).

Because #2 and #3 were the same panel, navigation "lied": going to another manga left the
old draft's title/cover/tags in the header, or silently overwrote your picks. The picker's
controls also changed shape per field (position sometimes there, sometimes not), and
auto-apply either clobbered edits or went stale. The root cause is the **conflation**, not any
single bug.

## Principles (the whole redesign follows from these)

- **The browser is a browser.** Browsing NEVER changes your draft. The URL bar always shows the
  real live page.
- **Capture is an explicit action** that pulls FROM the current page INTO a draft. It never runs
  silently on navigation.
- **One draft, chosen on purpose.** The right panel is a *draft manga* (new) or a *target manga*
  (edit) that you picked — not "whatever page I'm on."
- **One metadata editor**, shared by the library's Title edit and the browser's capture panel.
- **Nothing auto-overwrites.** Auto-fill only fills empty fields, and only when you ask.

---

## A. A real browser

Top **tab strip** + **toolbar**, center **page**, optional right **capture panel**, transient
**find bar** and **downloads shelf**. One persistent session (per-domain cookies; logins survive
restarts and are shared across tabs).

Toolbar, left→right:
- **Back / Forward / Reload / Home** (+ mouse buttons 3/4 = back/forward).
- **Omnibox**: shows the real URL; typing a non-URL searches (the active source's search template,
  else the default engine). Lock icon reflects https.
- **Zoom** −／level／＋ (Ctrl+−/＝/0), per-tab.
- **Find in page** (Ctrl+F) → a find bar with match count + next/prev.
- **DevTools** (Inspect) → opens the page's dev tools.
- **Downloads** → a shelf when active + a small manager (list, open, show-in-folder).
- **Capture panel toggle** (show/hide the right panel so you can browse full-width).

Tabs: new (`+`) / close / activate; middle-click & `target=_blank` open a new **in-app** tab (never
an OS window). Each tab keeps its own page, history and zoom.

Electron surface used (for reference, not part of approval): `webview.setZoomFactor`,
`findInPage`/`stopFindInPage`, `openDevTools`, `goBack/goForward/reload`, main-process
`session.on('will-download')`, `setWindowOpenHandler`.

Decision to confirm: **tabs on top** (standard browser) vs the earlier left rail. Recommendation:
top strip — it reads as a real browser and leaves the right edge for the capture panel.

---

## B. Capture & the draft (the core rethink)

The right panel is **the draft**, independent of navigation. It is one of:

- **New manga** — an empty draft you're filling. Title required to save.
- **Editing "X"** — a draft bound to an existing library title; captures/edits update X.

It never changes because you navigated. The browser is just "where I can capture from right now".

Panel contents (top→bottom):
- **Header**: `NEW MANGA` (draft) or `EDITING · «title»`, with the target's cover/title, and a way
  to switch target (or start a fresh draft).
- **Auto-fill from this page** — one button. Runs, for the *current* page: the domain **recipe**
  selectors + **structured metadata** (`og:` / JSON-LD). Fills **only empty fields** (never
  clobbers your work). This is the "learned site → instant fill" path, on demand.
- **Metadata form** (the shared editor): Title*, Alt, Authors, Artists, Status, Year, Genres,
  Tags, Description, Cover. Every field is directly editable AND has a **Capture** button that
  opens the picker on the current page.
- **Footer**: `Save` (create/update, open the title) · `Save as new` (create, stay in browser for
  fast library-filling) · `Discard` (with an unsaved-changes warning).

What this fixes: navigation can never make the panel lie (it's your draft, on purpose); auto-fill
is explicit and non-destructive; refresh keeps everything (you never re-run capture unless you
click it).

---

## C. The picker (inspector) — same controls every time

Invoked by a field's **Capture** button. Identical control set on every pick (this was the
inconsistency you flagged):

- **DOM path** breadcrumb — retarget any ancestor.
- **Match on** — `tag`, each `class` (dynamic/state classes flagged + off by default), `id`,
  each attribute (`href`/`src` as `off / starts-with / exact`, others on/off), `:nth` (marked
  fragile). Always rendered from the element's real attributes.
- **Scope** — This one / All matching.
- **Position** — **always shown when the selector matches >1**, for every field. Stepping it
  selects a single match (implies "This one"). This is what was missing on list-default fields.
- **Cleanup** (per field) — `strip counts`, `lowercase` toggles.
- **Live**: composed selector (editable), match count, on-page **highlight**, and a **truthful
  preview** (shows exactly what will be stored, e.g. `26 June 2026 → 2026`).
- **Images**: resolve lazy/`srcset`, skip placeholders, wait for load; cover can also come from
  `og:image` (clean, never a black placeholder).

Committing stores the selector + cleanup flags + position into the **source recipe**, so
Auto-fill can re-apply it on the next title.

Full inspector layout: see `design/pick-inspector-mockup.html` (kept, still valid).

---

## D. Sources — rethought

A **Source** is a saved site, not just a derived name:

- `domain`, `homepage` (entry URL), `searchTemplate` (e.g. `https://hentainexus.com/?q={q}`),
- a **recipe** (per-field selectors + cleanup + position, versioned, learned from captures),
- a **session** (login state — it's a real browser, so you log in on the site; we only read
  cookie presence and can clear it),
- stats (titles using it).

Sources tab:
- **Add / edit** a source (name, homepage, search template).
- **Open in browser** (new tab at the homepage).
- **Recipe**: view the mapped fields; open the picker to re-map/repair one; version shown.
- **Session**: logged-in status (cookie probe) · `Log in` (opens the site) · `Sign out / clear
  cookies` for that domain. (No password handling — that stays on the site.)
- General **Clear browsing data** lives in Settings (non-source domains like Google).

---

## E. Edit vs Create — one editor, two entries

- **Create**: `Add title` (Library) → a **new draft** (empty, in-memory, nothing written) → fill by
  hand and/or open the browser to capture → `Save` writes it. Title required; unsaved-changes
  warning on leave.
- **Edit**: from a Title → `Capture from source` opens the browser with the draft **targeting that
  title**; captures update it; `Save` updates. Or edit the fields inline in the Title view.
- The **metadata form component is shared** between the Title view's edit mode and the browser's
  capture panel — same fields, same validation, no duplication.

---

## Deliverables of this refactor

1. A full-browser shell (tabs, toolbar, zoom, find, devtools, downloads) — `views/BrowserView`.
2. A reusable **MetadataEditor** component (fields + per-field Capture) used by both the capture
   panel and the Title edit.
3. The **picker/inspector** with consistent controls (per C).
4. A **draft store** decoupled from tab navigation.
5. Sources with recipe + session management (per D).

## Open decisions to confirm before build

- Tabs top vs left rail (recommend top).
- Capture panel: docked-right (recommend) vs a slide-over.
- DevTools docked vs detached window (recommend detached — simplest, native).
- Downloads default folder (recommend the vault's parent, configurable in Settings).
