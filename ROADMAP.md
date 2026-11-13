# Roadmap

Where longbox is going, release by release. Every larger item starts as a design note and, for
anything on screen, a mockup in [design/](design/) that the owner approves first; what has shipped
moves to the [changelog](CHANGELOG.md). What longbox will not do is at the end.

## 3.6.0 — password manager

Built: the store and the key, the save bar, Sources → Logins, Settings → Passwords, ad blocking from
the shield, and the quality step (navigation out of `store.ts` into `nav.ts`).

Logins for the sites in the embedded browser, encrypted in the vault under a master password,
filled on click ([draft](design/password-manager.md), to be checked against 3.5.0). Only the key is
held while unlocked; each password is decrypted at the moment it is used. With sessions
kept on each computer, it is what makes signing in again on another one quick. The key is a button
beside the star and the shield, and ad blocking moves from the ⋯ menu to a popover of the shield's
in the same format ([mockup](design/password-manager-mockup.html)).

## Then

- **OPDS.** The vault served to readers on other devices — KOReader, Panels, Chunky and the rest —
  over the local network.
- **Saved filter sets.** A combination of filters kept under a name, a click away.

## Alongside, a step per release

- **Linters in CI**: ruff for the sidecar, eslint for the UI and the shell — first a rule set the
  code already passes, then tightened.
- **A UI smoke test in CI**: the off-screen Electron run that takes the project page's screenshots
  (`tools/screenshots.py`) already drives the real app through thirteen scenes over a generated
  vault; it becomes a test that fails when a scene does.
- **Unit tests for the window's logic** (Vitest, run in CI with the type-check). First the pure
  modules where a mistake costs most: `data.ts` (chapter order, title matching, grouping),
  `normalize.ts`, `tabstrip.ts`, `local.ts` (what a stored preference is accepted as) and
  `updates.ts`. From then on a change to one of them comes with its test, as the sidecar's do.
- **The monoliths, split by plan — one cut a release.** A file over 1,500 lines does not grow: new
  code for it goes into the module it is being split into. The first cut — the vault's health out
  of `library/service.py` into `library/health.py` — landed in 3.4.0. 3.5.0 took smaller ones out
  of `store.ts` (the confirmation dialog into `confirm.ts`, a restart's guards into `restart.ts`)
  and gave the shell its first tests, run in CI. 3.6.0 took navigation — places, tabs, Back and
  Forward, the openers — out of `store.ts` into `nav.ts` (1,610 → 1,140 lines). The rest, in order:
  1. `views/TitleView.vue` (1,700): the contents column — tree, edit mode, adding entries — into
     `TitleContents.vue`, and the pages pane into `PagesPane.vue`;
  2. `library/service.py` (2,060): chapter media — downloads, page capture, folder entries, the
     media sidecar's one writer — into `library/chapters.py`, and episodes' subtitles and stills
     into `library/episodes.py`;
  3. `library/vault.py` (1,600): the vault's documents (`read_doc`, `doc_transaction`, the
     library's own files) into `library/docs.py`;
  4. `store.ts`: the activity pollers (downloads, the vault pass, paused imports) into
     `activity.ts`, and the user layer's write-through into `userlayer.ts`.

  `views/BrowserView.vue` (1,250) and `shell/main.js` (1,250) are next in line once these land.
- **Accessibility**: a name for every icon-only button, visible keyboard focus.

## Later

- **Signed builds** — Windows through the SignPath Foundation's program for open-source projects or
  a Certum open-source certificate, macOS through the Apple Developer Program — and with them,
  updating in place.
- **Storage beyond a folder** (S3, WebDAV), behind the vault's own interface.

## Not planned

longbox ships no content and no code for any particular website: no built-in scrapers, source
catalogs, shared recipe packs, online metadata services or tracker sync. It is a vault, not a
media server — nothing streams to a TV or a phone beyond OPDS above. The interface stays in
English for now.
