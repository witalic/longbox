# Changelog

What changed in each release of longbox. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); releases are tagged `vX.Y.Z` and built
for Windows, macOS and Linux on the [releases page](https://github.com/witalic/longbox/releases).

## [Unreleased]

### Changed
- longbox is free software under the GPL-3.0-or-later.
- A short README; installing and the first-launch steps on the project page; a user guide
  (`docs/guide.md`) and `CONTRIBUTING.md`; screenshots of the app as it is now.

### Fixed
- An archive is converted on its way in, and a chapter's pages reordered or moved, one page at a
  time — a large one no longer has to fit in memory whole.

### Security
- A released build no longer opens DevTools on F12; started with `LONGBOX_DEVTOOLS=1`, it does.
- A cover fetched by its address never reaches this computer or its network — not by the address,
  not by a redirect.

## [3.3.0] — 2026-11-12

### Added
- **Sources page**: the sites the browser keeps, in your own groups and order — drag a site or a
  whole group, pick several at once, rename or delete a group in one step. Each site has a panel
  with its homepage, whether the browser shows it, and its saved links (dated when saved), and can
  count and clear its own cookies, storage and cached pages. Every link of every site also reads
  as one table.
- A download can name its entry after the file it downloads — beside the number you typed, or as
  the whole label.
- A cover can be added by typing the image's address in the metadata editor.

### Changed
- A launch reads the index, not the vault: only title folders added or removed by hand are
  checked, and the whole vault is verified only after a session that never closed properly. The
  app closes its library before it quits.
- The library is listed as light cards and its filters and facets are counted by the index — for a
  library of thousands of titles, tens of megabytes became a couple; browsing by a field, value
  pages and a hovered chip read no title at all.
- Saving a title reads it once; a read mark, a rating or playing an episode reads no chapter file;
  the vault's documents are read again only when they change; the open tabs are saved at most
  every 15 seconds.

### Fixed
- A title open in a tab shows what changed elsewhere (a Metadata run, a pass) when it is shown
  again.

## [3.2.0] — 2026-11-12

### Added
- Rules can be searched, are numbered, can be filtered by what they do, and say when they saved.
- The capture dock's **Edit existing** picker finds a title by name, author, type or any field,
  and opens it in the library.
- Sign-in pop-ups work in the built-in browser; a download nobody armed is kept whatever it is.

### Changed
- Electron 45; the browser presents one Chrome identity, and captures ask as the page does.
- Tile previews are made ahead of the grid, one at a time, never by re-reading a cached one.

## [3.1.0] — 2026-11-11

### Added
- A title's contents in four views — list, tiles, wall, pages — chosen per type.
- Both tab strips behave like Chrome's.

### Changed
- Authors and artists are one field: who made the work.
- The source is unlinked from the editor; Delete sits in the title's head.

### Fixed
- Every path the vault stores is relative to it, so a vault opens from any drive letter or share.
- A value typed into a list field and never confirmed is kept; a title's language and group
  filters are its own.

## [3.0.0] — 2026-11-09

### Changed
- **The vault is home**: everything the app keeps — recipes, sources, settings, the browser's
  sign-ins and cache, transfers — lives in the vault. A vault is opened on purpose from the launch
  window, by one app at a time, and a vault that drops is waited for, not replaced.

## [2.9.1] — 2026-11-09

### Fixed
- A video is stored under the name of what its bytes hold; PCM sound in an mkv plays.

## [2.9.0] — 2026-11-09

### Added
- An episode the window cannot show gets a playable copy, made in the background by the bundled
  ffmpeg.
- The reader's chapter rail carries the work's sections.

## [2.8.0] — 2026-11-08

### Added
- Media given to an entry is queued and added in the background.
- A value page lists its works a page at a time, filtered like the library.

### Changed
- Library, from the web, goes back to the app tab you left.

### Fixed
- The player's stutter warning shows only when frames drop, under the picture.

## [2.7.1] — 2026-11-01

### Changed
- Builds for Apple silicon run on macOS 15 runners.

## [2.7.0] — 2026-11-01

### Added
- Ad and tracker blocking in the browser's pages, with EasyList, EasyPrivacy and uBlock Origin's
  filters.

## [2.6.0] — 2026-11-01

### Added
- Every place is an app tab with its own Back and Forward.
- Subtitle tracks for episodes; downloads nobody armed land in the vault's downloads folder.
- Downloads and imports can be paused and carried on after a restart.
- Links that open a new tab work; a title lists every site it came from.

### Fixed
- A site's video player fills the screen and takes clicks everywhere.

## [2.5.0] — 2026-10-29

### Added
- **Value pages** for every person, studio, character, genre and tag.
- **Editing many titles at once** from the library's select mode.
- **Folder import**: bring in what is already on disk, read the way it is sorted, with a preview
  and a kept session; entries can be their file names.
- Entries can belong to a section of their work (a volume, a season, a year).
- The activity corner shows the library's running job beside the downloads.

### Fixed
- A type change never leaves a title on two shelves; media imported under a freed number lands on
  the right chapter.

## [2.4.0] — 2026-10-25

### Added
- **Metadata**: library-wide operations on values — rename, merge, move, replace — previewed and
  revertible, and **rules** for values on their way in.

## [2.3.0] — 2026-10-09

### Changed
- Recipes, sources and reader settings travel with the library.
- One source for the version, written everywhere by one tool.

## [2.2.0] — 2026-09-06

### Added
- A title may wear several covers; one with none shows its own media.
- Your own order for source groups and links; a source you are done with can be frozen.
- The name a chapter's file arrived under is kept.

### Fixed
- A renumbered chapter no longer blocks the number it freed.

## [2.1.0] — 2026-08-30

### Added
- Every stored file carries a checksum, and the vault can be asked whether it is whole.
- Every chapter describes itself in a `ComicInfo.xml`.

### Changed
- A field's type is a promise, and every field draws itself; Settings is a rail of sections.

## [2.0.1] — 2026-08-26

### Added
- Source groups fold in the browser.

## [2.0.0] — 2026-08-25

### Added
- **Episodes**: a chapter can be a video — stored ready to play, previewed as a contact sheet,
  played in place.
- **Fields you define**, and a studio field; one field registry for the whole app.
- The keys, menus and downloads a browser is expected to have.

### Changed
- A new interface, built from the approved design canvas.

## [1.2.1] — 2026-08-22

### Fixed
- Every cached thing is versioned by something that cannot repeat.
- The defects an independent audit of the whole app turned up.

## [1.2.0] — 2026-08-19

### Changed
- The library is served from the index and verified against the vault behind the window; a
  library being read says so instead of looking empty.

## [1.1.0] — 2026-08-17

### Added
- Installers for Windows, macOS and Linux.
- Page capture, the reader's page rail, and a one-time migration of older archives.

### Changed
- Listings are backed by the index, and a launch syncs only what changed.

## [1.0.0] — 2026-07-26

### Added
- The first release: a local-first media vault with an embedded capture browser — per-site
  recipes, an explicit draft with per-field provenance, armed downloads, a library with linked
  faceted filters, and a reader.

[Unreleased]: https://github.com/witalic/longbox/compare/v3.3.0...HEAD
[3.3.0]: https://github.com/witalic/longbox/compare/v3.2.0...v3.3.0
[3.2.0]: https://github.com/witalic/longbox/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/witalic/longbox/compare/v3.0.0...v3.1.0
[3.0.0]: https://github.com/witalic/longbox/compare/v2.9.1...v3.0.0
[2.9.1]: https://github.com/witalic/longbox/compare/v2.9.0...v2.9.1
[2.9.0]: https://github.com/witalic/longbox/compare/v2.8.0...v2.9.0
[2.8.0]: https://github.com/witalic/longbox/compare/v2.7.1...v2.8.0
[2.7.1]: https://github.com/witalic/longbox/compare/v2.7.0...v2.7.1
[2.7.0]: https://github.com/witalic/longbox/compare/v2.6.0...v2.7.0
[2.6.0]: https://github.com/witalic/longbox/compare/v2.5.0...v2.6.0
[2.5.0]: https://github.com/witalic/longbox/compare/v2.4.0...v2.5.0
[2.4.0]: https://github.com/witalic/longbox/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/witalic/longbox/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/witalic/longbox/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/witalic/longbox/compare/v2.0.1...v2.1.0
[2.0.1]: https://github.com/witalic/longbox/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/witalic/longbox/compare/v1.2.1...v2.0.0
[1.2.1]: https://github.com/witalic/longbox/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/witalic/longbox/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/witalic/longbox/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/witalic/longbox/releases/tag/v1.0.0
