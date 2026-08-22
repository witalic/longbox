# longbox — the capture / edit / storage state model

Status: **approved architecture**. Supersedes the tab-bound capture prototype (the old
`ScraperView` and everything that made the capture panel mirror "whatever page I'm on").
Answers `design/scraping-problem-and-requirements.md`, in particular the open questions in §7.
Every decision here exists so that one or more of the invariants I1–I8 holds **by
construction**, not by discipline in code; the invariant → mechanism map is at the end.

## 1. Diagnosis

Invariants I1–I4 read as four separate bug classes, but they are four symptoms of one
architectural disease: **the capture UI was bound directly to the live page in the embedded
browser**. While the live DOM is the source of truth for the capture form, navigation,
reloads and lazy-loading can always corrupt the user's work — such bugs can be patched
forever but never excluded.

The central decision of the whole model: **sever the link between the page and the user's
work**. Everything else in this document is a consequence.

## 2. Three state domains

Application state splits into three domains. Each has one owner and one lifetime (this is
I1 verbatim: no UI element represents two domains at once).

- **Browser** — the live webview with a persistent per-domain session. Its state belongs to
  the site; the app stores nothing in it, expects nothing from it, and never reads it
  directly into the UI. Login happens here, on the site itself; the app never sees
  passwords (I8) — it owns only the domain's session (cookies).
- **Draft** — the working copy of one record (a title and its chapter list) the user is
  building or editing right now. Lives in app state, outside the browser. It is a
  *universal* mechanism: a draft is not "capture state" but an editable copy of a record
  regardless of where it came from (§4).
- **Vault** — committed files on disk. The only source of truth for the library. The index
  is a secondary cache over it (§8).

Data flows one way: `Browser → Snapshot → Draft → Vault`, with an explicit boundary at
each arrow. There is no reverse edge (nothing in the vault or draft ever writes into a page).

## 3. The snapshot — the bridge between browser and draft

When the user initiates a capture, the app takes an **immutable snapshot** of the page:

- field values extracted by the Recipe (§5) from the **rendered** DOM — not the initial
  HTML, because dynamic sites paint content after XHR;
- cover bytes fetched **through the page's context** (its cookies and referer), taking the
  `currentSrc` of the already-rendered `<img>` after lazy-load has finished. This is the
  structural fix for the blank/placeholder-cover class of bugs: we capture what the human
  sees, not what an attribute says. This is mandatory, not an optimization — hotlink
  protection and gated images only work through the page session;
- origin metadata: URL, domain, recipe version, timestamp.

The snapshot is created instantly and never changes. The draft is built from the snapshot —
**never from the page**. Navigation, reloads, redirects and any site activity are therefore
physically unable to touch the draft: I2 holds through absence of a link, not through checks.

## 4. The draft — a universal working copy

A draft is seeded in one of two ways:

1. **from a snapshot** — capturing a new title off a page;
2. **from the vault** (`title.json`) — editing an already-saved title.

From then on the two scenarios are identical: same editor, same field widgets, same
preview, same explicit commit. "Editing a saved title" is not a separate screen with its
own logic — it is the same draft with a different seed. This gives I5 automatically: the
controls are identical for every field in every scenario because there is one screen.

**The field widget** is the same for every field: value + provenance badge
(`auto`/`manual`) + actions *[recapture] [edit] [clear]*. A chapter row in the list is the
same mini-record with the same controls.

**The preview** renders from the draft — i.e. from the same bytes and strings that will be
written on commit. A mismatch between shown and stored (an I4 violation) is impossible
because there is one source. Consequently all value normalization (status vocabulary, year
extraction, count-stripping, lowercasing) happens **in the draft, client-side** — the
backend only validates.

**Commit** is an explicit user action; an atomic write (`tmp → rename`). Until commit the
vault does not change; a draft closed without commit simply disappears without a trace.

## 5. Provenance — where each field came from

Every field carries an origin mark: `auto` (filled by capture) or `manual` (typed or
corrected by a human).

One rule: **automatic capture may write only into fields whose origin is `auto` or which
are empty; `manual` fields are untouchable.** That is the whole of I3 — not an `if` in the
right place, but a merge invariant.

Critically, **provenance is persisted in the vault**, inside `title.json`, not only in the
draft. Otherwise I3 would only hold within one session. The real scenario: the user
captures a title, hand-fixes a bad name, commits; a week later opens the same page and
recaptures to fill the empty fields. The new snapshot merges into a draft seeded from the
vault — and the week-old manual fix is protected exactly like a fresh one. Along with
origin, a field stores its source URL and recipe version — cheap and useful for diagnosis.

## 6. The recipe — teaching a site

"Teaching" is a **recipe per domain**: a map `field → extractor`, stored separately from
titles (it is app-level knowledge about sites), versioned. One recipe covers a title page's
fields; a separate rule covers the chapter list (a "chapter row" selector + sub-rules
`id / title / url / language / group / date` relative to the row).

**Interaction:** teach-me mode — the user clicks an element on the page; the app
generalizes how to find it (the pick inspector: DOM path, match-on toggles, scope,
position, cleanup, live truthful preview). Teaching happens once per site and applies to
all of its titles; recipes live on disk, so they survive reload and restart.

**Resilience** (§7.2 — "independent of any single technique"): a field's extractor is not
one rule but an **ordered list of candidates** — e.g. stable CSS selector → structural
path → OpenGraph/microdata fallback. Capture tries candidates in order and records which
one worked. When a site's markup changes, the field arrives in the draft *empty* — an
expected, normal state, not an app error. The user re-teaches one field; the recipe
updates; every title on that site benefits.

## 7. Capturing the chapter list on dynamic pages

Automatic pagination crawling is rejected on principle: it is brittle and conflicts with
I6. Instead — **accumulative capture**:

- the draft's chapter list is a set deduplicated by chapter id;
- the user pages through the site themselves (pagination, infinite scroll, "show all" —
  irrelevant); at each step the action **"add visible"** merges the currently visible rows
  into the draft; duplicates are ignored;
- a counter shows "N chapters collected"; commit happens when the user decides it's enough.

A human drives the pace → tight request loops are impossible by construction. The model is
identical for any pagination mechanism because it knows nothing about pagination — only
about "rows visible now".

## 8. Vault: shape on disk

```
library/
  index.db                # rebuildable cache (never authoritative)
  <title-id>/
    title.json            # layered: meta + provenance + chapters + user
    cover.<ext>           # the captured cover bytes
    chapters/
      <chapter-id>.cbz    # pages; ComicInfo.xml inside        (next phase)
      <chapter-id>.json   # per-chapter state: download, source (next phase)
```

`title.json` (schema 1):

```jsonc
{
  "schema": 1,
  "meta": {                       // written ONLY via draft → commit
    "title": "…", "alt": "…", "authors": [], "artists": [],
    "type": "manga", "status": "Ongoing", "year": "",
    "genres": [], "tags": [], "flags": {"adult": false, "ai": false, "censored": false},
    "desc": "",
    "coverSource": "",            // URL the cover bytes were captured from
    "source": {"domain": "", "url": ""}   // where this title is captured from
  },
  "provenance": {                 // per meta-field origin
    "title": {"origin": "manual"},
    "desc":  {"origin": "auto", "url": "https://…", "recipeVersion": 3}
  },
  "chapters": [                   // scraped records; one row per translation
    {"id": "ch-93-en-group", "num": "93", "title": "…", "url": "…",
     "lang": "EN", "group": "…", "date": ""}
  ],
  "user": {                       // written ONLY via instant write-through
    "fav": false, "rating": 0,
    "read": {"<chapter-id>": "read|reading|unread"}
  }
}
```

**Two layers with different write semantics:** metadata (+ chapters) goes only through
draft → commit; the **user layer** (favorite, rating, read progress) is **instant
write-through** with no draft — a star tap that demanded "commit your changes" would be a
mockery, and the reader will write progress in the background. The write code enforces the
consequence: a metadata commit merges *its* layer and never touches the user layer, and
both layers go through one serialized write point per title — otherwise editing a
description could roll back progress updated in parallel.

**The chapter id is special.** It is at once the dedup key (§7) and the on-disk file name
(next phase). Changing an id is therefore not a field edit but a small migration; in the
minimal version editing it is forbidden outright.

**The index** is SQLite as a pure cache: updated incrementally after commit; a rebuild
command scans the disk and reconstructs it from scratch. A corrupt index cannot lose
content by construction (I7), because it never owned any.
It carries everything a listing needs — each title's **chapter media** (the sidecars) and its
**cover URL** — plus the mtimes it saw them at, for one reason: **a listing must never touch
the filesystem**. On a vault that lives on a network share, one stat per title is the
difference between a screen that appears and a screen that arrives.

**The app is served from the index and verified against the vault afterwards.** A launch
opens the database and answers immediately; a background pass then reads the vault in ONE
directory scan (a directory entry already carries its stat data) and re-reads only the titles
whose document, chapter directory or cover moved since they were indexed. Nothing waits for
that answer, but it is never skipped either: the vault is the source of truth, and files can
be added, edited or deleted while the app is closed — or by another machine sharing the
drive. A first-ever open finds an empty index and fills it there, which the UI shows as
progress instead of an empty library.

Because that pass does NOT hold the title locks, its writes are guarded: a row built from a
document (or a chapter directory) older than the one already indexed is refused, so a scan
that started before a commit can never put the old state back on top of it.


**Why CBZ** (next phase): atomic write of one file, standard enough that any third-party
reader opens it — the library outlives even longbox itself — and a simple contract for our
own reader.

## 9. Media download — the ARMED-DOWNLOAD handshake (landed)

Downloads follow the same principle as chapter capture: **the human drives the pace**
(I6 by construction — the user clicks the SITE'S OWN download button; the app never
fetches chapters itself):

1. In the capture panel's **Downloads tab** (a saved title only) the user fills the
   chapter identity (number, language, group) and **arms the next download**.
2. The Electron main process intercepts the next `will-download` from the embedded
   browser (so the file comes through the site's own session — I8), saves it to a temp
   path and hands it to the sidecar.
3. The sidecar attaches the archive to the matching chapter row (creating one when the
   captured list doesn't have it), recording the **download source per chapter**
   (`fileUrl` + `pageUrl`) — deliberately independent of the title's metadata source:
   different chapters and languages may come from different sites.
4. One arm = one download; unarmed downloads are rejected and discarded. Arms expire.

Storage: every chapter is a **plain zip** (`chapters/<chapter-id>.zip` — CBZ is the same
thing, so any third-party reader opens it), converted at ingest when the site served
rar/7z and refused when nothing can read it, plus a provenance sidecar
(`<chapter-id>.json`: fileUrl, pageUrl, filename, size, pages, date). Pages are the
zip's image entries in natural order; deleting pages rewrites the archive atomically.
Read state stays a separate axis in the user layer. An automated politeness queue is
deliberately NOT built — the armed handshake makes bulk fetching impossible.

**Page capture (the second lane, landed).** Sources that never serve a file are read the
same way, page by page: the user teaches once per domain which images on a reader page
are the pages (pick mode → the recipe's `pages` rule), arms ONE entry exactly as above,
and reads. Each page view is scanned; the vault is asked which page keys the armed entry
already holds and only the rest are fetched — with the browser session's cookies and the
reader page as referer, in the main process (a fetch from inside the page is bound by the
site's CORS). Keys are the images' **file names**, kept in the sidecar (`pageKeys`),
because CDN URLs carry rotating tokens; so re-reading a chapter downloads nothing. The
human still drives the pace — nothing is crawled ahead.

**Scanning and storing are decoupled**, because a reader flips faster than a page stores:
a scan reports as soon as the page's images have a size and again until the set holds
still, and everything it reports is QUEUED. Each queued page carries the entry it was
scanned for, so a queue outlives the chapter being finished and drains into the right
one; only a failed capture (the entry is gone) discards it. Nothing is skipped because a
previous page is still downloading — that is what left gaps.

**A document's shape is versioned.** `title.json` carries a `schema` number, and a build
that reads an older one upgrades it on the way IN (`library/migrations.py`), never on disk —
the next commit persists the new shape. A document from a NEWER build is left untouched: the
user may still open that library elsewhere. The vault outlives any single build, so changing
its shape is a migration step, not an edit.

**Everything cached is keyed by a version that cannot repeat.** Page images and covers are
served with a long cache lifetime and are cached again on disk as downscaled previews, so a
version that stays equal across an edit shows the user the file they just deleted — which
reads as the app losing their work. Page COUNT cannot serve as that version (delete two
pages, add two), nor can a file timestamp (a copy carries it over, a share rounds it, two
writes share a tick). A chapter is versioned by `revision.size.pages` from its sidecar, where
the revision is a counter every page operation bumps; a cover by `mtime.size.ext`, and a
cover write that would land on the previous stamp moves the timestamp itself. ONE version
serves the URL and the server-side cache, so the editor, the reader and the library grid can
never disagree about what a page is. Both live in `library/versions.py`, whose `cache_key`
REFUSES a key without a version — a new cached artifact cannot quietly opt out of the rule.

**A chapter id is assigned by the vault, never proposed by a client.** A commit can adopt an
existing row's id (§7), so an id derived on the client is a guess that the vault may not
share — and media addressed by that guess lands nowhere. The client sends the identity
(num + language + group) with an empty id; `Library.chapter_id_for` is the only derivation
in the app.

## 10. The reader (landed)

The reader reads **only the vault** — opens the chapter zip, writes progress into the user
layer write-through. The network does not exist as a dependency in the reader, so "reading
offline" is not a mode but the only way it works (I7).

## 11. Automatic vs explicit

One rule: **ephemeral — automatic; persistent — explicit.**

| Action | Mode |
|---|---|
| Page snapshot, filling the draft via recipe | automatic (cheap, self-reverting) |
| Commit to the vault | explicit button |
| Binding a download (archive or page capture) to a chapter | explicit arm; one arm = one chapter |
| Grabbing the pages of a view once capture is armed | automatic (the human still turns the pages) |
| User layer (star, rating, progress) | instant, no confirmation |

## 12. API surface (this phase)

```
GET    /api/library?…            filtered list (flat DTO, composed from layers)
GET    /api/library/facets
GET    /api/titles/{id}          flat DTO + provenance map
POST   /api/titles               commit a NEW draft   {meta, chapters, provenance}
PUT    /api/titles/{id}          commit a draft into an existing title (meta layers only)
DELETE /api/titles/{id}
PATCH  /api/titles/{id}/user     write-through user layer {fav?, rating?, read?}
POST   /api/titles/{id}/cover    cover bytes captured in page context (base64 + source URL);
                                 or {url, referer} → server-side fetch as a fallback
DELETE /api/titles/{id}/cover
GET    /api/titles/{id}/cover    serve the stored bytes
GET    /api/authors              derived from titles
GET    /api/sources              derived from titles + recipe store
GET/PUT /api/recipes/{domain}    versioned candidate-based recipes
GET    /api/settings, PUT /api/settings/…   (unchanged)
```

Gone: `/api/import` and the server-side HTML extraction engine. Extraction happens in the
page (rendered DOM, candidate order, lazy-load-resolved images); the backend stores and
validates. The wire DTO stays flat/camelCase so the library UI consumes it directly.

## 13. Module layout

```
backend/app/
  main.py  security.py  settings.py  config_store.py
  library/
    models.py     # layers: TitleMeta, Provenance, ChapterRow, UserLayer, TitleDoc + flat DTO
    vault.py      # per-title dirs, atomic writes, serialized per-title write point
    index.py      # SQLite cache (kept, adapted)
    service.py    # queries, commits (meta), write-through (user), covers
  scraper/
    models.py     # Recipe v2: candidates per field, chapter rule
    recipes.py    # per-domain versioned store (kept)
    covers.py     # server-side URL fetch — fallback only
  routers/
    library.py  settings.py  recipes.py

frontend/src/
  store.ts        # app store: views, library data, filters, user-layer mutations
  draft.ts        # THE draft: seed (blank | vault | snapshot), provenance merge, commit
  browser.ts      # pure browsing tabs (no capture state)
  views/BrowserView.vue          # tab strip, toolbar, webview host, capture panel dock
  components/CapturePanel.vue    # draft header, auto-fill, MetadataEditor, commit footer
  components/MetadataEditor.vue  # the ONE editor (Title edit + capture panel)
  components/PickInspector.vue   # chain, match-on, scope, position, cleanup, live preview
shell/
  main.js         # kept: sidecar spawn, auth cookie, session hardening, webview preload
  pick-preload.js # v2: pick chain, live preview, snapshot extract, cover bytes
```

## 14. Invariant → mechanism map

| Invariant | Guaranteed by |
|---|---|
| **I1** single-purpose state | three domains (§2): every piece of state has one owner and one lifetime |
| **I2** navigation is inert | the draft is built from a snapshot and holds no reference to the live page (§3) |
| **I3** no clobbering | per-field provenance persisted in the vault (§5); auto-merge cannot write into `manual` |
| **I4** truthful preview | the preview renders from the draft — the same bytes that get committed (§4); normalization lives client-side |
| **I5** consistent controls | one universal field widget; capture and edit are one screen with different seeds (§4) |
| **I6** good citizen | a human drives pagination (§7); the download queue has per-domain concurrency, backoff and budget (§9) |
| **I7** offline & rebuildable | the vault is primary, the index a cache with rebuild (§8); the reader has no network dependency (§10) |
| **I8** trust boundary | login only on the site, the app owns only the session (§2); media through the browser session (§9); loopback-only API |

## 15. Phases

1. **This refactor:** vault v2 + layered `title.json` + provenance; recipes v2; draft
   store + shared MetadataEditor; real browser + snapshot capture + pick inspector v2;
   accumulative chapter-list capture. No legacy migration — the prototype's data format is
   abandoned.
2. **Download:** per-chapter media into `chapters/<id>.cbz`, the politeness queue, the
   chapter state machine.
3. **Reader:** vault-only, write-through progress.
