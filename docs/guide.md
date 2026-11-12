# longbox user guide

Everything longbox does, screen by screen. For installing it, see
[Get started](https://witalic.github.io/longbox/#start) on the project page; for what changed in
each release, the [changelog](https://github.com/witalic/longbox/blob/main/CHANGELOG.md).

longbox keeps what you collect — books of pages, image sets, video — in a vault: a folder you own,
which it organizes, searches, reads and plays, with a browser built in for bringing things home.

- [The vault](#the-vault)
- [Library](#library)
- [Capture browser](#capture-browser)
- [Sources](#sources)
- [Downloads and page capture](#downloads-and-page-capture)
- [Import](#import)
- [Titles and their contents](#titles-and-their-contents)
- [Reader](#reader)
- [Episodes (video)](#episodes-video)
- [Metadata](#metadata)
- [Value pages](#value-pages)
- [Fields you define](#fields-you-define)
- [Keeping the vault honest](#keeping-the-vault-honest)
- [Settings](#settings)

## The vault

The library is a folder — the **vault** — that you choose: on this disk, another one, or a
network drive. Nothing is created unasked: the first launch asks where to make a vault or which
one to open, and a folder that holds anything else is never taken for one.

Inside it, every title is one folder on a shelf named after its type (`manga/`, `comic/`, …):
its `title.json` holds the metadata, its chapters are ordinary zip archives (each carrying a
`ComicInfo.xml`, so Komga, Kavita and other readers understand them too), and its episodes are
the video files they arrived as. A SQLite index next to them makes the app fast; it is a cache —
deleting it never loses anything, and it is rebuilt from the folders.

**Everything the app keeps lives in the vault**: recipes, sources with their groups and links,
your fields, how the reader shows each title, the window's settings, the embedded browser's
sign-ins and cache, unfinished transfers, previews. Every path stored inside it is relative, so
copying the folder to another machine — or opening it from a different drive letter — moves the
whole library, and two vaults never share a thing. The app's own folder keeps only where your
vaults are. Several vaults can be registered; one is open at a time.

**Opening a vault is quick.** The app is served from the index at once and checks behind the
window only what the index cannot know: title folders added or removed by hand. The whole vault
is read again only when the index is empty or the last session never closed properly. A change
made by hand *inside* a title's folder is picked up by **Settings → Maintenance → Rebuild**.

**One app per vault.** When the last vault answers at once the app opens straight into it; when it
does not — a network drive still waking up, or gone — a small window says which and keeps trying,
opening the vault the moment it answers, with the other vaults one click away. A vault open on
another computer is reported rather than shared: two apps writing into one vault would damage its
index and the browser data in it.

If the vault drops while the app is open, the app stops using it: nothing is read from it and
nothing is written anywhere until it is back, the browser's pages and running transfers are
stopped, and the window says what stopped. When the vault answers again the app restarts into it
and reopens your tabs.

## Library

- **Views** — a cover grid, a dense list, or detailed rows that preview each title's first pages
  or episodes. Covers, ratings, favorites, reading progress and footer pagination.
- **Linked faceted filters** — type, status, genres, tags, languages, flags, authors, characters,
  studio and any field of your own. Each value can be **included or excluded**; the counts
  beside every value follow the rest of the selection. Search covers titles and alternate names.
- **Shelves** narrow the library by type; **Browse by** groups it along any list field (authors,
  studios, characters, tags…), and each shelf keeps its own list of axes.
- **Covers** — a title may wear several; the grid fades through them while you hover it. One you
  never gave a cover shows what it actually holds: the first page of each downloaded chapter, or
  an episode's own preview frame.
- **Select mode** — pick titles to delete them, or to **edit them at once**: a panel shows every
  value the picked titles hold and how many hold it — add a tag to all, take a character from all,
  give a value some have to the rest, set a status — and one Apply writes it, undoable. What you
  change there becomes yours: capture will not overwrite it.
- **Tabs** — every place is a tab with its own Back and Forward: a library under its filters, a
  title, the reader, a value page. Middle-click opens one behind the current tab; the tabs come
  back with the next launch, and the last sessions can be reopened from the tab menu.

## Capture browser

A real browser inside the app: tabs (pinning, reordering, background tabs, per-tab zoom and audio
mute), a floating find bar, a page context menu (copy or save an image, open a link in a
background tab), and the shortcuts a browser is expected to have — `F5`, `Ctrl+R` (`Shift`
bypasses the cache), `Ctrl+F`, `Ctrl+L`, `Ctrl+T`/`W`/`Shift+T`, `Alt+←/→`, `Ctrl+0/±`,
`Ctrl+1…9` — working over the page itself, not only over the app's own chrome. Its sign-ins and
cache are the vault's own.

**Visual capture.** A page is captured into an explicit **draft**. Auto-fill it from the site's
recipe, or pick any element on the page and refine its selector in the inspector (scope, position,
cleanup, and the separator a list picked into a one-value field is joined with). Per-field
provenance guarantees that an automatic capture never overwrites a manual edit; list fields can be
**merged** across pages for works whose tags are spread over several. Filling a new draft over a
page the library may already hold asks first, showing what each candidate record *is*; the dock's
**Edit existing** picker finds a title by name, alternate name, author, type or any field, and
opens it in the library if that is what you meant.

**Per-site recipes.** Taught once per domain — by clicking the elements that hold the title,
authors, tags or cover — and stored versioned with ordered fallbacks (the picked selector, a
structural fallback, the page's own metadata), then reused for every next title on that site.
Each site decides which metadata fields it offers. Recipes are yours: longbox ships none.

**Ad blocking** with Adblock Plus's lists (EasyList, EasyPrivacy) and uBlock Origin's filters,
fetched from where they are made and refreshed every four days: ads, trackers, badware and miners
are blocked and ad slots hidden. The toolbar's shield turns green with a count of what it blocked
on the page; its menu switches it off everywhere, pauses it on one site (subdomains included),
hides cookie banners and pop-ups too if asked, and says how fresh the lists are.

## Sources

The **Sources** page arranges the sites the browser keeps — the same tree its side rail draws.

- **Groups and order** — sites are listed in groups, in your order. Drag a site or a whole group
  into place; rename or delete a group (its sites stay, ungrouped).
- **Several at once** — `Ctrl`/`Shift`-click picks several sites, then group, show, hide or remove
  them together.
- **A site's panel** — its group, its homepage (where a new tab on it opens), whether the browser
  shows it, and its **saved links**, each dated when it was saved. A site becomes a source the
  moment you bookmark a page on it, and sits under *no recipe* until you teach it one.
- **Browser data** — how many cookies a site keeps, and clearing them alone or together with its
  storage and cached pages, for the site and every subdomain. Offered too when a site is removed.
- **All links** — every saved link of every site as one table, for finding duplicates and dead
  ends.

A site you are done with can be hidden from the browser: it keeps its recipe and its links.

## Downloads and page capture

**Armed downloads.** Arm the next download for a chapter, then download the file in the built-in
browser as you would anywhere: longbox intercepts it and files it into that chapter. Archives
(zip, cbz, 7z, rar) are normalized to zip, single images accumulate into the chapter page by page,
a video is stored as itself, and a subtitle file for a downloaded episode becomes its subtitles.
Tick **Name after the downloaded file** to let the file name the entry — beside the number you
typed (**With a number**), or as its whole label.

The vault renames every stored file to its chapter's id, so the name it arrived under is recorded
with it and shown on the entry. A panel lists every transfer with its title, chapter and progress.
Any one can be paused, and closing the window with transfers running warns first: each keeps its
place and is picked up later in its own row — after a restart too — or started over.

**Downloads nobody armed** are kept: a subtitle, an archive, an image or a video lands in the
vault's own `downloads/` folder (anything else is refused, and the Activity panel says so). The
activity corner counts what waits there; the panel lists it, with where it came from.

**Page capture** — for sites that never serve a file. Teach once which images on a reader page are
the pages, arm a chapter, and read: every page you open is fetched with the browser's own session
and filed into that chapter. Pages already stored are never fetched again, so flipping back costs
nothing.

## Import

Bring in what is already on disk, sorted the way you sorted it. Say what each level of a folder
tree means — a collection, a creator, a work — and how names become titles, entries and fields: a
pattern reads `vidhub @kumo Kumo Works` as site, handle and creator, a template makes
`Kumo Works 2025.1` the work's title, entries are numbered in order, from their names, or not at
all (the file's name is the label), and folders below a work become the **sections** its entries
are grouped by. Videos become episodes, archives and folders of images become chapters, and the
metadata editor fills in every work at once (or one work on its own).

The preview shows exactly what will be created before anything is written; the run copies or
moves, keeps what you typed by hand, and skips what an earlier run already stored. Profiles keep
the answer and travel with the library.

## Titles and their contents

A title page shows its metadata, its covers and its entries — as a list, as tiles beside the open
one, as a wall of cards, or as every page of every entry — chosen per type.

- **Metadata editor** — every field in the order the registry gives it, with suggestions from the
  library's own vocabulary. Covers can be added from a file, from the page being captured, or by
  typing an image's address.
- **Contents editor** — entries with free-form labels, translation rows (language and group per
  row), sections (a volume, a season, a year) the page groups entries by, numbering that follows
  the translation it belongs to, drag-to-reorder, attach or replace archives, add loose images or
  whole folders, move pages between entries, reorder pages by hand.

## Reader

Page and strip modes, fit controls, a hideable rail of page thumbnails, chapter navigation that
keeps your current translation, per-title remembered settings, and rebindable hotkeys that work
whatever the keyboard layout. Reading progress is written as you read.

## Episodes (video)

An episode is a chapter whose media is a video file. Everything a chapter has — the free-form
label, the language and group of a translation, its source link, read state — applies unchanged;
what differs is how it is stored, previewed and played.

**Getting one in.** Arm the entry and download the file in the built-in browser, drop a file on
the entry form (or pick one with *Choose files…*; whole folders work too), or replace an entry's
media — its identity, read state and translation rows survive. `mp4 · m4v · webm · mkv · mov · avi
· ts` are recognised. What you give an entry is queued and added in the background, one after
another; Activity lists what is still on its way.

**Subtitles.** An episode keeps any number of subtitle tracks — one of them the default — and the
player's own CC menu switches them. Drop or pick an `srt`, `vtt`, `ass` or `ssa` file in the
episode's **CC** editor, take one the browser downloaded earlier, or arm the episode and download
its subtitles. Everything is stored as WebVTT, the format the player shows (ASS styling is
flattened); the original file is kept beside it.

**What is stored beside it.** At ingest longbox records the duration, container and codecs, and for
`mp4`/`m4v` moves the index to the front when it is not there already, so playback starts at once.
A file is stored under the name of what its bytes hold: an mkv that arrived called `.mp4` is kept
as `.mkv`, and one that is no video at all is refused. A **contact sheet** — nine frames, plus the
single frame a tile wears — is cut once, by the window that plays the file, and kept beside it.

**Watching.** The title page previews the episode as that sheet, each tile a link to the second it
came from, and plays it in place when asked. Arrow keys scrub it, the player's controls (including
fullscreen) work, and the resume point survives leaving the page.

**Files a browser cannot open** — every `avi`, `mov` and `ts`, an `mkv` whose tracks the window
cannot decode, HEVC beyond Main10 — get a **playable copy**, made in the background by the ffmpeg
that ships with the app and kept beside the original, which is never touched. A picture the window
already decodes is only re-containered (lossless, seconds); anything else is encoded to H.264,
on the graphics card where it can be.

**Streaming, not copying.** An episode is served out of the vault with Range support and read in
windows, so seeking does not drag the whole file across a network drive, and a file that is
playing is never replaced or deleted under the player.

## Metadata

One view for the library's vocabulary: every field's values (chapters' language and group
included) with how many titles hold each — over the whole library, the library's current filters,
or the titles picked in select mode. Rename, merge, remove or move values to another field, set
or clear a field, replace text, or rename a type (which renames its shelf on disk). Every change is
previewed before anything is written, costs only the titles that actually change, keeps the
library's "updated" order, and can be reverted from its history.

**Rules** do the same to values on their way in — from capture, from your own typing (visibly, with
"keep as typed"), from a new chapter's language, group and section — in the order you give them.
Rules can be searched and filtered by what they do, and any rule can be applied to the titles
already stored.

## Value pages

Every person, studio, character, genre, tag and list value of your own has a page: its works (all
of them, a page at a time, searched, sorted and narrowed with the library's own filter block), how
many you have read and how you rated them, the sites it came from, the values it travels with, and
the rules about it. Add a description, links (they open in the built-in browser) and an image; a
person's other names are kept as rules, so every spelling lands as one. Hovering a chip on a title
page shows its page in brief; a rename in Metadata takes the page along.

## Fields you define

The metadata registry is per library: add your own fields (text, description, number, list, date)
and choose which appear on title pages, in the filters and in the capture dock. A field's type is
a promise about its data, so number and date can be left but never entered, and a list only folds
into text on a separator no value contains. Every filled field draws itself on the title page by
its type.

## Keeping the vault honest

The vault is the source of truth, so the app can be asked to check it — nothing here runs by
itself, and nothing changes your files.

**Checksums.** Every chapter is hashed twice as it is stored: once over the whole file (what
integrity means) and once over its ordered page images (what "the same chapter" means). The second
survives a repack, a renumbering and the metadata mirror, which is why duplicate detection uses it
and corruption detection does not.

**One check, three answers** (Settings → Health): is anything broken, is anything wasting space,
is anything missing. Answers are sentences about files — *the file is gone*, *the file changed
since it was stored* — and each says what to do about it. A quick check compares the disk against
the record; a full check re-reads every byte; either can be stopped. Content stored before
checksums existed can be baselined.

**Leftovers** — files belonging to no entry, and the debris of interrupted writes — can be deleted
here, after listing every one with its size. Duplicate copies are never deleted for you: which one
to keep is a judgement, so the report opens each instead.

**Numbering gaps** are reported only where entries prove a run: a translation of its own, at least
five entries, almost all numbered, densely covering their span, and never a hand-made order.

**What has been done** is recorded with the library: every operation over the vault leaves when it
ran, how long it took and what came of it, and the last report is kept in full.

**ComicInfo.xml** is a mirror, not a second source of truth: it is written where an archive is being
rewritten anyway — at ingest and on every page edit — and Settings brings the rest in line on
demand, rewriting only what differs.

## Settings

One section at a time: **Storage** (your vaults — opening another restarts the app into it; create
one, add one, forget one), **Health** (above), **Maintenance** (rebuild the index, convert
archives, write metadata into them), **Fields**, **Browser**, **Appearance**, **Keyboard**,
**About**. Everything that walks the library — a check, a conversion, a rebuild — takes the same
single slot, shows the same progress and can be stopped from where it was started.
