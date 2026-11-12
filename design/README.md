# Design

How longbox came to be shaped the way it is: the design document behind each larger feature, and
the mockup approved before it was built — a UI change starts as a self-contained HTML mockup here
([CONTRIBUTING.md](../CONTRIBUTING.md)). They are records: each says at the top whether it was
built, and none is updated as the app moves on. What the app is now is in the code,
[ARCHITECTURE.md](../ARCHITECTURE.md) and the [user guide](../docs/guide.md).

The mockups open in any browser.

## Read these first

- [state-model.md](state-model.md) — the capture / edit / storage model: the browser, the draft
  and the vault kept apart, per-field provenance, recipes. The canonical architecture.
- [vault-home.md](vault-home.md) — everything the app keeps lives in the vault, every path in it is
  relative, one app opens a vault at a time.
- [metadata-model.md](metadata-model.md) — the standard and custom fields, and what each surface
  shows of them.

## Features

| Document | What it settles | Mockups |
|---|---|---|
| [browser-refactor.md](browser-refactor.md) | the capture browser: the page, the draft and the vault apart | [browser-refactor](browser-refactor-mockup.html), [pick-inspector](pick-inspector-mockup.html) |
| [navigation.md](navigation.md) | places, app tabs, Back and Forward | [navigation](navigation-mockup.html), [tab-strip](tab-strip-mockup.html) |
| [metadata-management.md](metadata-management.md) | operations on values across the library, and rules | [metadata-management](metadata-management-mockup.html) |
| [bulk-edit.md](bulk-edit.md) | editing many titles at once | [bulk-edit](bulk-edit-mockup.html) |
| [value-pages.md](value-pages.md) | a page for every person, studio, character, genre and tag | [value-pages](value-pages-mockup.html), [value-page-works](value-page-works-mockup.html) |
| [folder-import.md](folder-import.md) | bringing in a folder already on disk | [folder-import](folder-import-mockup.html), [import-structure](import-structure-mockup.html) |
| [subtitles.md](subtitles.md) | an episode's subtitle tracks, and downloads nobody armed | [subtitles](subtitles-mockup.html) |
| [playable-copies.md](playable-copies.md) | a copy the window can play, for an episode it cannot | — |

## Other mockups

[activity](activity-mockup.html) (the activity corner) ·
[chrome](chrome-mockup.html) (the window's frame) ·
[contents-editor](contents-editor-mockup.html) and [contents-views](contents-views-mockup.html)
(a title's contents, and their four views) ·
[covers-sources](covers-sources-mockup.html) (several covers; your own order of sources) ·
[edit-target-picker](edit-target-picker-mockup.html) ·
[reader](reader-mockup.html) ·
[settings](settings-mockup.html) ·
[sources](sources-mockup.html) ·
[vault-health](vault-health-mockup.html) ·
[vault-launcher](vault-launcher-mockup.html) ·
[mockup](mockup.html) (the first full interface)

## History

- [spec.md](spec.md) — the original specification (July 2026), kept as written.
- [scraping-problem-and-requirements.md](scraping-problem-and-requirements.md) — the problem the
  state model answers.
- [canvas/](canvas/) — the design canvas the 2.0 interface was built from.
