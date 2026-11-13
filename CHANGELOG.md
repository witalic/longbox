# Changelog

What changed in each release of longbox. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); releases are tagged `vX.Y.Z` and built
for Windows, macOS and Linux on the [releases page](https://github.com/witalic/longbox/releases).

## [Unreleased]

## [3.6.3] — 2026-11-13

### Added
- **Copy the username or the password** straight from the key's popover in the browser, and from
  Sources → a site → Logins — for a form Fill cannot reach. A copied password still leaves the
  clipboard after 30 seconds.

### Changed
- **A login belongs to its site, not to one address.** One saved on mail.proton.me is offered on
  account.proton.me too, its row saying where it was saved — the first fill there asks, naming both
  addresses — and signing in there updates it rather than saving a second one. http and https,
  another port, and names many people own (each github.io page) stay sites of their own. "Never
  for this site" covers the whole site.

### Fixed
- **Copying a password no longer brings the app down** 30 seconds later, when longbox clears it
  from the clipboard ("A JavaScript error occurred in the main process").
- **A sign-in in steps** — the username on its own first, the password after, as Proton or Google
  ask — is filled a step at a time: **Fill** on the username step fills the username, on the
  password step the password. Saving after it keeps the username from the first step.

### Security
- **A page could raise the bar that offers to save a login by itself**, with a username and
  password of its choosing, by submitting its own form from its own script on any click of yours
  anywhere on it. A sign-in counts now only when you clicked in its form or pressed Enter in it.
- **A "Save…" answered while the logins were locked does what it said.** Once unlocked, a sign-in
  that turns out to be an update of a saved login is shown as that, to be answered again — never
  written over it unasked.

## [3.6.2] — 2026-11-13

### Fixed
- **longbox opens your vault again, every start.** Since 3.5.0 an installed longbox opened no vault
  after its first start: the launch window said "The library could not be opened: Cannot
  destructure property 'uiDir'…". The app took its own log in its folder on the computer for
  something an older version had left there, and the step meant to move such things failed.
  Nothing in the vault was touched. The log now lives in the vault (`.longbox/longbox.log`), and
  the one left in the app's folder is removed at the next start.

## [3.6.1] — 2026-11-13

### Added
- **Passwords lock by themselves** when the computer sleeps or its screen is locked, and after 15
  minutes with no use of it — Settings → Passwords → *Lock when idle* sets how long, or never.

### Security
- **Passwords:** the second copy of the logins file (`secrets.prev.json`) kept the file as it was
  before the last change — so a login just deleted, or every login under a master password just
  changed, stayed readable in it until the next change. It is now a copy of the file as it is,
  written just after it, and the next unlock writes it again if it is not.

## [3.6.0] — 2026-11-13

### Added
- **Passwords.** longbox keeps the logins of the sites you capture from, encrypted in the vault
  under a master password and filled on click. A key beside the star and the shield lists a
  site's logins and edits them in place; after a sign-in a bar offers to save the login or update
  its password; a new password keeps the previous ones (up to three); a password generator;
  Sources → a site → Logins and Settings → Passwords.

### Changed
- Ad blocking is switched from the shield's own popover — on every site or none, paused on this
  one, cookie banners and pop-ups, the lists — instead of the ⋯ menu.

### Fixed
- A confirmation that destroys something (clearing cookies, discarding an edit, deleting) rests the
  keyboard on Cancel, so a stray Enter no longer does it.

## [3.5.0] — 2026-11-13

### Added
- **Where sign-ins live.** Each vault chooses where the built-in browser keeps its sign-ins and
  site data: in the vault (they travel with it, and whoever can read the vault can use them) or on
  each computer (the vault holds none). Asked once of every vault, with nothing chosen for you;
  Settings → Browser changes it either way. The move happens the next time that computer opens the
  vault, and leaves nothing behind — the cache is deleted and refills; until it is done, the vault
  keeps its answer as it was. Forgetting a vault removes its sign-ins on this computer too, and a
  copied vault folder never takes or removes the sign-ins of the vault it was copied from.

### Changed
- The bundled ffmpeg, which makes playable copies of episodes, is FFmpeg 9.0 (was 7.1) — newer
  decoders, with their fixes.
- For contributors: the desktop shell has tests of its own (`node --test`), run in CI on every
  push.

### Fixed
- **Settings → Maintenance → Rebuild** reads a chapter's details off the disk even while the app
  runs: a chapter file edited by hand in place is picked up, not served from what the app held.
- An edit, a new title or a deletion made while **Rebuild** runs is no longer undone by it in the
  library's listing — and a window holding a title notices the rebuilt one.

### Security
- Vue 3.5.45, and the interface's build tools (vite 6.4.6 and what it carries), past their
  published advisories.

## [3.4.0] — 2026-11-13

### Added
- **Knowing a release is out.** The app asks once whether it may check GitHub for new versions —
  at most once a day, nothing sent but the request. A newer release shows as an arrow in the
  sidebar's footer, in the warning colour when it fixes security issues; What's new and Download
  open it in your browser, and nothing is downloaded by itself. Settings → About changes the
  answer and has Check now.

### Changed
- longbox is free software under the GPL-3.0-or-later.
- A short README; installing and the first-launch steps on the project page; a user guide
  (`docs/guide.md`) and `CONTRIBUTING.md`; screenshots of the app as it is now; a roadmap.
- For contributors: every push and pull request is checked on Windows and Linux; a release's
  notes are its section of this changelog; Dependabot watches Electron and the dependencies.

### Fixed
- An archive is converted on its way in, and a chapter's pages reordered or moved, one page at a
  time — a large one no longer has to fit in memory whole.

### Security
- The released app can no longer be started as plain Node (`ELECTRON_RUN_AS_NODE`), take
  `NODE_OPTIONS`, or open a debugging port (`--inspect`): those switches are off in every build.
- A released build no longer opens DevTools on F12, and will not start with Chromium's remote
  debugging (`--remote-debugging-port`); started with `LONGBOX_DEVTOOLS=1`, it does both.
- An image fetched for a page — a cover, a captured page — is refused when its address, or any
  redirect on the way, points at this computer or its network; a source served from that network
  may still show its own.

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

[Unreleased]: https://github.com/witalic/longbox/compare/v3.6.3...HEAD
[3.6.3]: https://github.com/witalic/longbox/compare/v3.6.2...v3.6.3
[3.6.2]: https://github.com/witalic/longbox/compare/v3.6.1...v3.6.2
[3.6.1]: https://github.com/witalic/longbox/compare/v3.6.0...v3.6.1
[3.6.0]: https://github.com/witalic/longbox/compare/v3.5.0...v3.6.0
[3.5.0]: https://github.com/witalic/longbox/compare/v3.4.0...v3.5.0
[3.4.0]: https://github.com/witalic/longbox/compare/v3.3.0...v3.4.0
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
