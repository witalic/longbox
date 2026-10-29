# longbox — value pages: a page for a person, a tag, a character

Status: **built** — mockup `design/value-pages-mockup.html` approved with D1–D3 as proposed (§10).
Companion to `design/metadata-management.md` (values, operations, rules) and
`design/metadata-model.md` (the field registry). The user layer of titles is untouched.

## 1. Why

A value is only a string titles share. Clicking it filters the library, and nothing can be
said about it. Two needs:

- **an unfamiliar tag** — what it means, where to read about it, and the works that carry it,
  seen at once;
- **a person** — who they are, where they publish (their socials, their shop, their site),
  and what of theirs is here.

Browse already groups the library by any list field. A page is the next step: the group gets
a face, and the owner gets a place to write.

## 2. What has a page

Every value of a **list** field — the set Browse can group by: authors and artists, studio,
characters, genres, tags, and the owner's own list fields. Single fields (type, status, year)
and chapter values are vocabularies, not things, and get none.

**One page per value of one vocabulary.** Authors and artists share the people vocabulary
(`vocab: authors`), so a person has ONE page whether they wrote, drew, or both — the page says
which. Genres and tags are separate vocabularies, so `romance` the genre and `romance` the tag
are two pages, as they are two facets.

A page is keyed by the value **case-folded**: `Romance` and `romance` are one page, as they are
one value to a rule. It shows the spelling most titles use.

Every value HAS a page from the start: it shows what the library already knows — the works,
the counts, the values it travels with, the rules about it. Only what the owner writes is
stored, so a value nobody wrote about costs nothing.

## 3. What a page holds

**Written by the owner (stored):**

- **Description** — plain text in paragraphs; a URL in it is a link (D3).
- **Links** — an ordered list of `{url, label}`. The label defaults to the site, and a known
  site names itself (X, pixiv, FANBOX, Patreon, Wikipedia…). A link opens in the embedded
  browser — the same place a source is captured from.
- **Image** — the page's face: a file the owner drops or pastes, or one of its works' covers
  picked as the face (stored as a copy, so it outlives the title). Without one, the page shows
  the covers of its works.

The favourite mark people already have (`authors.json`) is shown on a person's page, unchanged
(D2).

**Worked out every time (never stored):**

- **Works** — every title holding the value; for a person split by role (wrote / drew); sorted
  by recently added, rating or title; *Show all in the library* is the old click.
- **In your library** — titles, chapters, how many are read, the average of your ratings,
  first and last added, and the sources that brought the value in (`mangadex.org 6 · typed 2`)
  — for an unknown tag, often the best clue to what it means.
- **Often together** — the values most often on the same titles: tags beside a tag;
  collaborators and studios beside a person; series and fellow characters beside a character.
  Each opens its own page.
- **Rules about it** (`rules.json`) — rename rules that land on this value are its **other
  spellings** ("also written as"); add rules that fire on it are what it **implies**; rules
  that remove it or move it elsewhere are shown as such. Adding another spelling on the page
  creates a rename rule: aliases ARE rules, not a second mechanism that could disagree with
  the one capture runs.

## 4. Where it lives

In the vault, so it travels with the library like recipes and rules:

```
<root>/.pages/<vocabulary>.json            authors.json (people), tags.json, characters.json, …
<root>/.pages/images/<vocabulary>/<value's safe_id>-<8 hex>.<ext>
```

```jsonc
{ "schema": 2,
  "pages": [
    { "value": "Kentaro Miura",                // its key is this, case-folded
      "desc": "…",
      "links": [{ "url": "https://example.com", "label": "Official site" }],
      "image": "kentaro-miura-3f9a1c2e.jpg",   // a name of its own per image ever written
      "updated": "2026-10-28T12:00:00Z" } ] }
```

- `.pages` is a name no shelf can take (`safe_id` never yields a leading dot); the vault scan
  skips it.
- One document per vocabulary, through `Vault.read_doc()` / `Vault.doc_transaction()` (the
  list read with `Vault.doc_items`) — absent is the only empty answer, an unreadable one
  refuses to be written over, a newer build's is never written. A page left with nothing
  written is not stored.
- An image is written atomically beside it under a name of its own (the value's `safe_id` and
  a random tail), never reused — so the name is the version its URL and its cached downscales
  are keyed by (`versions.page_image_version`). A replaced or removed image's file goes.
- Listings — Browse cards, the Metadata values list — need "is it described", a first line and
  the image: one document per vocabulary, kept in memory while its file's stamp holds, so a
  listing costs one stat, never a file per value.

## 5. A page follows its value

- **Rename / merge in the Metadata view.** When the old value is gone from the whole library
  after the run, its page moves to the new spelling. On a merge into a value that already has
  a page, that page keeps its description and image, gains the other's links, and the other's
  description is appended under its old name — nothing the owner wrote is dropped. The move is
  in the run's journal, so a revert puts the pages back.
- A value renamed in only part of the library keeps its page; the new spelling starts empty.
- A merge never deletes the image of the page merged in: the journal still names it, so a
  revert can put that page back whole.
- **A value no title holds any more keeps its page** — it is the owner's writing. The Metadata
  view lists such pages (*Pages without titles*), where one can be deleted, explicitly and with
  a confirm. A title that gets the value again brings the page back to life.
- Remove and clear operations never delete a page.

## 6. Surfaces

1. **The page** — opens inside Browse (*Browse › Tags › time loop*, with Back to the list), from
   anywhere a value is shown. The head band speaks the title page's grammar: image, kind badge,
   name, other spellings, counts, links, description. Actions: **Edit**, **Show in library**,
   **In Metadata** (that value selected, for rename / merge), ★ for a person. Below: works and
   often-together; a side column with the numbers and the rules.
2. **Edit** — explicit, like a title: Save / Cancel in the band. Description; links (paste a
   URL and the site names itself; drag to order); image (drop or paste a file, or pick a cover
   of its works); other spellings as chips — each one a rename rule, offered to apply to the
   titles that still hold that spelling.
3. **Peek** — hovering a value's chip for a beat (the title page, Browse's tag chips) shows a
   small card: name, kind, the first lines of the description or "nothing written yet", the
   count and a few covers, *Open page* and *Show in library*. A click keeps doing what it does
   today (D1). Not in the metadata editor: *Open page* would walk away from an unsaved draft.
4. **Browse** — a group's name opens its page; its card shows the image and the description's
   first line. A filter **Not described** lists the values with nothing written, most-used
   first — the queue for filling pages in.
5. **Metadata view** — a PAGE column (written / nothing), the filter *Not described*, *Open page*
   on a row, and *Pages without titles*.

## 7. The unknown value

The case this starts from: a tag nobody explained. Its page opens WITH the answer the library
already has — the works that carry it, the tags it travels with, the sites it came from — and a
description that says it is empty: *Nothing written about "iyashikei" yet*, with **Write what it
means** and **Look it up** (a web search for the value in the embedded browser — a button,
never automatic).

## 8. API (sketch)

```
GET    /api/pages/{field}/page?value=          the page: what is stored + works, counts, neighbours, rules
GET    /api/pages/{field}/peek?value=          the small card
PUT    /api/pages/{field}/page?value=          {desc, links, spellings} — spellings are rename rules
DELETE /api/pages/{field}/page?value=          only a page whose value no title holds (409 otherwise)
GET    /api/pages/{field}                      every page written in the vocabulary, and whether
                                               a title still holds its value
GET    /api/pages/{field}/image?value=&v=&w=   the image (`v` its file name; `w` a cached downscale)
PUT    /api/pages/{field}/image?value=         the bytes, typed by Content-Type
POST   /api/pages/{field}/image/from-title?value=   {titleId, slot} — a cover copied
DELETE /api/pages/{field}/image?value=
```

The value travels as a query parameter — values hold slashes. `{field}` is the field the value
was met in (`artists`); the server resolves its vocabulary (`authors`). Works, counts and
neighbours are computed in one pass over the index. Browse groups carry `described`, `excerpt`
and `image`; Metadata values carry `page`.

## 9. Not now

- Capturing a person's links from a source's author page (a recipe field) — the natural next
  step once pages exist.
- Favourites for every kind of value (D2).
- A hierarchy of tags beyond what add rules already express ("implies").
- A global search across pages.

## 10. Decisions for the owner

- **D1 — a chip's click.** Keep filtering, peek on hover *(proposed)* / a click opens the page.
- **D2 — the favourite mark.** People only, as now *(proposed)* / every page.
- **D3 — the description.** Plain text with links *(proposed)* / Markdown.
