# longbox — ingest from source sites: problem & requirements

Purpose: state **the problem** and **what a solution must achieve** — as input for a deeper
architectural model. Solution-**agnostic**: it says *what must be true*, never *how* (no selectors,
no page-metadata tricks, no specific pipeline — those are for the model to choose).

## 1. Context

longbox is a desktop app that builds a **local, offline-readable** library of manga / comics from
**third-party source websites**, via an **embedded browser**. The vault is files on disk plus a
rebuildable index. One persistent, per-domain browser session.

Today: a user can capture a title's **metadata + cover** from a page, but the capture/edit
interaction falls out of sync with browsing (see §4). **Chapters, media download, and the reader
are not built yet.**

## 2. The problem to solve

From a source website, let a user reliably produce a library title — its **metadata + cover**, and
its **chapters** — **download** those chapters, and **read** them offline; and edit any of it.
Crucially, the whole thing must hold together as **one coherent model** in which the act of
capturing/editing never conflicts with the act of browsing.

We have **not** solved even the minimal case cleanly. So the goal is: get the minimal, whole flow
right, not to add breadth.

## 3. Scope — what the model must cover (and nothing more)

1. **Browse** a source site.
2. **Teach** the app, once per site, which part of a page is which field — reusable across that
   site's titles — for both the **title's fields** and the **chapter list**.
3. **Capture** a title's metadata + cover from a page → a library record.
4. **Capture** a title's chapter list from a page.
5. **Download** a chapter's media into the vault.
6. **Read** downloaded chapters offline.
7. **Edit** a title's metadata and its chapters.
8. **Log in** to gated sites (on the site itself; the app never handles passwords).

Explicitly **out of scope for now** (do not design for these; the minimal case isn't solved yet):
keeping the library "in sync" with sources / re-scraping for changes; merging conflicting data from
multiple sources into one title.

## 4. What each capability must achieve (still solution-agnostic)

- **Reliable values.** Given a page, obtain the *correct* value a human sees for each field
  (including a real cover image, not a blank/placeholder) and for each chapter (its identifier,
  title, and the link to its media). *How* the value is obtained is the model's choice.
- **Reusable teaching.** However the app is "taught" to read a site, that teaching is reusable
  across that site's other titles and survives a page reload.
- **Human correction.** The user can override any captured value by hand.
- **Media & reading.** A captured chapter can be downloaded and then read with no network.

## 5. Invariants — the bugs that must be structurally impossible

These are the exact failures we kept hitting; the model must make them impossible by construction,
not patch them.

- **I1 — Single-purpose state.** No UI element represents two things at once (e.g. "the live page"
  and "the title I'm building"). Every piece of state has one owner and one lifetime.
- **I2 — Navigation is inert.** Browsing never silently changes, overwrites, or staleness-corrupts
  any captured/edited data. A reload keeps the user's work; navigating never shows stale data as if
  it were current.
- **I3 — No clobbering.** Automatic capture never overwrites a value the user edited by hand.
- **I4 — Truthful preview.** What the user is shown while capturing equals what gets stored.
- **I5 — Consistent capture.** The capture controls are the same for every field — none appear for
  one field and vanish for another.
- **I6 — Good citizen.** Don't hammer sources: back off on errors, respect cost/limits, bound the
  work; never bulk-fetch in tight loops.
- **I7 — Offline & rebuildable.** Downloaded media + metadata are usable with no network; the index
  is rebuildable from the on-disk vault; a corrupt index never loses content.
- **I8 — Trust boundary.** Vault content leaves the machine only for the service it's bound to; the
  local API is loopback-only; passwords are never handled by the app.

## 6. Entities (minimal)

- **Title** — the on-disk library record: metadata, cover, chapters, and the user layer
  (favorite, rating, read progress).
- **Source** — a site (domain) the app knows how to read, plus its login session.
- **Chapter** — an identifier + title + translation (language, group), with read state and
  download state and its media.

## 7. Open questions for the deeper model

- The **interaction / state model**: where capturing, reviewing, and editing live, and how state
  flows between *browsing*, *how-to-read-this-site*, and *the library* — such that the invariants in
  §5 hold.
- How to obtain **reliable values** from heterogeneous, changing pages, independent of any single
  technique.
- Capturing a **chapter list** on dynamic / paginated pages.
- The **download** and on-disk **storage** shape, and the **reader**.
- How much happens **automatically** vs by **explicit user confirmation**.

## 8. Non-goals

No syncing / re-scraping for changes. No multi-source merge. No in-app account creation or password
handling. No mass scraping.
