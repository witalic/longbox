# longbox — functional & architectural specification

What the product does, what each capability is for, the data it manages, and how it is built.
**This document deliberately contains no UI, layout, or visual-design decisions** — navigation,
screens, and controls are for the designer to determine from the functionality below.

---

## 1. Product & purpose

**longbox** is a desktop application for keeping a **local, organized library of comics** — manga,
manhua, manhwa, and Western comics. Its defining capability is a **built-in visual scraper**: the
app embeds a web browser, the user opens a source site, and by clicking blocks on the page they
capture metadata (titles, authors, tags, chapters, cover) into their library. What was clicked is
remembered per source site so the next import from that site is largely automatic.

The library is **the user's own data on disk** — the source of truth — with a rebuildable search
index on top. The purpose is to let one person catalog, organize, and maintain a large personal
comic collection sourced from many sites, entirely under their control and available offline.

## 2. Users & jobs to be done

A single power user managing hundreds to thousands of titles. Their jobs:

1. **Import** titles from source sites (assisted by the visual scraper) and keep them up to date.
2. **Organize** the collection — find, filter, group, prioritize, track what they've read.
3. **Maintain** each title's metadata — correct fields, merge data from several sources, manage
   chapters and translations.
4. **Read** (later phase) and remember their position.

## 3. Architecture

- **Electron desktop shell.** Windows-first; also macOS/Linux. A single desktop application window.
- **Python backend (FastAPI) as a local sidecar.** The shell launches it; the UI talks to it over
  local HTTP/WebSocket. The backend owns all logic and data: the library, the search index, the
  scraping engine, and source sessions.
- **Embedded browser.** A real web view inside the app renders source sites directly, so the user
  browses and scrapes real pages (including ones behind a login) without leaving the app.
- **Storage.** Media (cover images now; page images later) is written through a storage abstraction
  so the backing store can be a local folder now and a remote/NAS store later. The catalog lives as
  files on disk; a **SQLite index** provides search and filtering and is **rebuildable from disk** —
  losing the index never loses content.
- **Local or remote backend.** The app connects to a backend running locally (default) or on
  another host (a home server / NAS). The transport supports both.
- **Scraping is recipe-driven.** For each source domain the app stores a **versioned recipe**: the
  mapping from page structure to metadata fields, plus how to page through dynamic content. Recipes
  are reused across imports and re-versioned when a site changes its markup.

## 4. Domain model

```
Series      title, alternate_titles[], type (manga | manhua | manhwa | comic), status,
            authors[], artists[], year, description, cover,
            genres[]   — broad classifications from the source
            tags[]     — granular descriptors from the source   (genres and tags are DISTINCT,
                          both scraped; neither is a user-defined tag)
            sources[]  — a title may be tracked from several source sites

UserData    per Series: favorite (flag), rating (personal score), collection memberships
            — a user-owned layer kept SEPARATE from scraped metadata, so re-scraping never
              overwrites the user's own judgments

Collection  a user-defined, customizable list ("shelf") a title can belong to; plus built-in
            ones. Favorite and rating are independent of collections.

Chapter     number (supports decimals, e.g. 84.5), volume?, title,
            language, translator/group,          — TRANSLATIONS ARE FIRST-CLASS: one chapter
            source_url, source_domain,             number can exist in several translations, each
            page_count, downloaded?, read_state    with its own language and translator/group

Page        order, image                          — pages are indexed now; image files are an
                                                     optional later download

Recipe      domain, version, per-field mapping (with extraction kind: single value / list /
            attribute / list-of-records for chapters), paging strategy

SourceSession  domain, stored login cookies, signed-in?, last-used
```

The relationships that matter functionally: **genres vs tags are two distinct scraped taxonomies**;
**translations are first-class** (a chapter number may hold several, distinguished by language +
group); **the user layer (favorite / rating / collections) is separate** from scraped metadata and
survives re-scrapes; **a title can draw from multiple sources**.

## 5. Functionality

### 5.1 Library & organization
- Hold the whole collection and open any title from it.
- **Search** by title/author and **filter** by: type, language, status, genre, tag, rating, author,
  artist, **favorite**, and **reading progress** (unread / in progress / completed). Favorite and
  reading progress are independent filters, not collections.
- **Sort** by recency, title, rating, date added, unread count.
- **Collections**: create and manage customizable lists; a title can be in several. Separately, mark
  a title **favorite** and give it a **personal rating**.
- Track **reading progress** per title; surface **recently updated** titles (new chapters).
- The collection can be large — the user needs to move between quick cover-forward scanning and
  denser metadata-rich scanning. (How this is presented is a design decision.)

### 5.2 Source browsing & the visual scraper
- **Browse source sites** in the embedded browser: navigate freely, sign in, find titles.
- **Pick mode**: point at and select blocks on the current page to capture them as fields. The app
  generates a stable selector for each pick and infers its **kind** (single value, a list of items,
  an attribute like an image URL, or a repeating record like the chapter list).
- **Recipes**: each source domain accumulates a reusable, **versioned** recipe from these picks; a
  later import from the same site is mostly automatic. When the site changes and a pick breaks, the
  recipe is re-captured and versioned.
- **Multiple sources per title**: a record can be assembled from several sites (e.g. description from
  one, chapter list from another). Each captured field remembers **which source it came from**, and
  any field can be **entered or overridden manually**.
- **Import** turns the captured record into a Series (with its chapters) in the library.

### 5.3 Adding titles & chapters
- **Add a new title**: from a source (search/open it in the browser and scrape), by pasting a URL, by
  entering it manually, or by importing an existing local folder.
- **Add / update chapters**: pull new chapters from a title's source(s) ("update from source"), or
  re-run the scraper's chapter step to append; fix chapter numbers/order.

### 5.4 Viewing & maintaining a title
- **View** a title: its full metadata, and its chapters filtered by **language** and **translation
  group** (with multi-translation chapters distinguished). Selecting a chapter lets the user see /
  open its pages.
- **Edit** metadata: change any field, add/remove authors/artists/genres/tags, replace the cover,
  edit the description. The **stored record is the single source of truth**; when the user re-fills a
  field from a source, the scraper writes **into that record** — there is never a second, parallel
  copy of the metadata to reconcile.
- **Work on several titles at once** and switch between them (a multi-title working context).

### 5.5 Reading (later phase)
- Read a chapter in one of two modes suited to the medium: **paged** (manga / comics) and
  **continuous vertical scroll** (manhwa / manhua). Remember the reading position, per translation.

### 5.6 Sources & sessions
- Manage the known **source domains**: each has its recipe (and version) and its **login session**.
- For sites behind a login, the user **signs in on the site itself** inside the embedded browser; the
  app persists the resulting session **per domain** so it stays signed in. The user can sign out /
  clear a domain's session. The app never asks for or handles the site password.

### 5.7 Settings
- Choose storage (local folder or a remote/NAS backend), rebuild the search index, set defaults.

## 6. Key flows

1. **Import a title**: choose to add → open the source in the browser → pick fields (optionally from
   several sources, optionally manual) → import → the title is in the library.
2. **Read**: open a title → pick a chapter → open its pages → read → progress is remembered.
3. **Organize**: filter/sort the library; add to collections; set favorite and rating.
4. **Maintain**: open a title → edit fields, re-fill from a source, add/update chapters.

## 7. Non-functional constraints

- **Local-first and offline-capable.** The catalog and everything already imported work without a
  network; only scraping/updating needs one.
- **Be a good citizen of source sites** (they are unofficial/undocumented): respect rate limits,
  back off on errors, never scrape in tight loops or bulk-hammer a site.
- **Security & privacy.**
  - The library (metadata and any downloaded media) is the user's private content; it is never sent
    anywhere except back to the source it is bound to.
  - Any API tokens live in the OS keychain, never in files or logs.
  - Per-domain login **cookies are stored encrypted**; the app never handles the user's password
    (the user authenticates on the site themselves).
  - The local backend is reachable only on loopback and guarded by a per-launch secret; a remote
    backend requires an authenticated, encrypted (TLS) connection.
- **Data integrity.** The on-disk library is authoritative; the search index is rebuildable from it,
  so a corrupt or deleted index is recoverable without data loss.

## 8. Scope

- **v1**: metadata + cover images; local library; import via the visual scraper (multi-source +
  manual); organization (filters, collections, favorite, rating, progress); per-domain sources &
  sessions.
- **Later**: downloading page images; the reader; remote/NAS storage.
