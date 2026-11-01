# longbox — navigation: places, tabs and the way back

Status: **built** — mockup `design/navigation-mockup.html`, approved by the owner (2026-10-29).
Builds on chrome 2b (one titlebar strip) and `design/value-pages.md`.

## 1. What goes wrong today

Only a TITLE can be come back to: it has a tab. Everything else is one screen that the next
click replaces.

- **A value page is lost the moment you leave it.** It is drawn inside the browse view; opening
  one of its works, clicking a shelf or another section closes it and drops its trail. Getting
  back means opening some title again and finding the person on it.
- **A list forgets.** Browse by's search, role filter and page, and everything in Metadata — the
  field, the values picked, an action half set up — start over on every visit.
- **The library has one selection.** A genre chip on a title page replaces the filters you had,
  with no way back to them.
- **The app's strip holds only titles.** A page, the library under its filters, Metadata,
  Import have no tab; Settings shows no strip at all.
- **One click, three destinations.** A value on a title page filters the library, a person on a
  library card opens Browse by with a search, a name in a browse list opens its page.
- **Two "Browse".** The rail's *Browse* is the web browser; *Browse by* is the library grouped by
  a field.
- **No Back.** Except the value page's own button, nothing goes back a step.

## 2. The model

A **place** is anything the app can show: the library under a selection (a shelf, filters,
search, a Browse by axis), a title, the reader at a page, a value's page, Metadata, Import,
Sources, Settings, a web page.

A **tab** holds one place and remembers:

- how it got there — **Back / Forward**, per tab;
- what you did there — scroll, page, search, picked values, a half-set action;
- until you close it. Closing a tab is the only thing that forgets — Import alone keeps the
  folder it is reading for the run of the app, as it always has.

## 3. Where a click goes

One principle: **a click stays in the tab when it continues the same kind of thing; it opens a
tab when it reaches another record.** Middle-click (or Ctrl+click) always opens a new tab in
the background.

| You click | It goes |
|---|---|
| a title (card, row, work on a page) | its tab — switched to if already open (as today) |
| a value (person, tag, genre, character, studio, your list) on a title, a card, a list | its **page tab** — switched to if already open |
| a related value on a page (works with, appears beside) | the same page tab: Back returns |
| a shelf, a filter, the search, a Browse by axis | the library tab you are in; from any other tab, the Library tab — each change a step Back undoes |
| Read / a page tile | the title's tab, as the reader (Back → the title page) |
| Metadata, Import, Settings in the rail | its tab in the app strip, opened if needed — one each |
| Web, Sources in the rail | the web world, as today — its own strip |
| *Edit in Metadata* / *In Metadata* (with a selection or a value) | a Metadata tab of its own for exactly that ("Metadata · 37 titles") |
| *Show in library* (on a page, a peek card) | the Library tab, filtered to that value — one step, Back returns |

Filtering by a value stays one click away: the hover card and the page both carry *Show in
library*.

## 4. The strips

The app and the web stay apart (D1): each has its own strip, as today, and a strip never holds
the other's tabs.

```
app  (library, a title, a page, Metadata, Import, Settings)
     [Library] [▣ Lantern Witch] [● Mitsuki Arata] [# isekai] [≡ Metadata · 37 titles] +
web  (Web, Sources)
     [M mangadex.org] [P pixiv.net] [G Google] +
```

- **Library** is the app strip's first tab and is always there: it is home. Its label says what
  it shows — *Library*, *Manga*, *Tags*, *Manga · 2 filters*. A second library tab (middle-click
  a shelf or *Library*) is a real tab and can be closed.
- **Titles** keep their cover swatch, **pages** wear their face (a person's initials or image, a
  value's colour dot), **sections** their icon. The web strip is unchanged.
- The app strip's **+** opens a new library tab; the web strip's, a new web tab.
- Settings shows the app strip, with Settings as a tab in it — no screen is left without one.
- Pin, drag to reorder and the all-tabs menu work as today, for every kind; the menu lists both
  worlds, and *Restore session* brings back every kind of tab.

## 5. Back and Forward

At the start of every view's head band — where the web view already has its own — `‹ ›`, plus
Alt+← / Alt+→ and the mouse's side buttons, for whatever tab is in front. A right-click on `‹`
lists the steps, as a browser does. A step is a place, not a keystroke: typing in the search box
is one step when you stop, not one per letter.

## 6. The rail

The rail is the way INTO places and shows where you are: the section of the tab in front is lit
(a title tab lights Library). Shelves and Browse by stay where they are and act on the library
tab. The web browser's item is renamed **Web** (D2), so *Browse by* means one thing.

## 7. How it is built

- The navigation section of `store.ts` owns the app tabs: `{ id, place, back[], forward[],
  pinned, hold }`. `store.view`, `activeTitle`, `reader` and `library` are what the tab in front
  holds (`project`); a view drawn in a tab reads its own place through `useTab()`.
- A library tab owns ONE selection object for its life; `store.library` points at the one in
  focus (the last library tab in front), so the filter block and the results keep one source.
  Its steps are recorded as the selection settles (700 ms), and a step back is copied INTO that
  object rather than swapping it.
- `TabHost` draws a tab's screen and is kept alive (`<KeepAlive :max="8">`); it notes every box
  its view scrolled and scrolls it back. A view behind another tab gates its loads with
  `useShown()` — it catches up when it comes back. Titles and the reader are not kept: they
  redraw from the vault's copy, and the reader's spot rides in the tab's place.
- A tab holding unsaved work (a page being edited) sets `hold`: Back waits, closing asks.
- Sessions snapshot every tab's place (`sessions.ts`); the browser's tabs stay in `browser.ts`
  and keep their own strip.

## 8. Decisions (owner, 2026-10-29)

- **D1 — the strip.** *Decided (owner, 2026-10-29):* the app and the web keep separate strips;
  the app strip gains pages, library tabs and sections.
- **D2 — naming.** *Decided:* the web browser's rail item is **Web**.
- **D3 — a value's click.** *Decided:* it opens the value's page; filtering is *Show in library*
  on the page and the hover card.
