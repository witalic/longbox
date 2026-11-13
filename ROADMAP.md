# Roadmap

Where longbox is going, release by release. Every larger item starts as a design note and, for
anything on screen, a mockup in [design/](design/) that the owner approves first; what has shipped
moves to the [changelog](CHANGELOG.md). What longbox will not do is at the end.

The next release finishes answering an independent review of the code (2026-11-13); 3.4.0
answered the rest.

## 3.5.0 — where sign-ins live

The embedded browser keeps its profile — sign-ins, site storage, caches — in the vault, where
anyone who can read the vault can read the sessions too: a share, a removable disk, a backup, a
synced folder. Where it lives becomes the user's choice, per vault:

- **in the vault** — sign-ins travel with it to every computer that opens it, and are as safe as
  the vault is;
- **on each computer** — the profile stays in the app's folder on the computer that opened the
  vault; another computer signs in again.

The choice is asked when a vault is made, asked once of every existing vault when this version
first opens it (nothing moves until it is answered), and can be changed in Settings either way at
any time: the profile moves on the next start, before the browser opens it. Caches are not moved —
they refill. This is the one exception to "the vault is home"
([design/vault-home.md](design/vault-home.md) records it). The first tests for the shell come
with it: the move, the lock, the vault checks (`node:test`).

## 3.6.0 — password manager

Logins for the sites in the embedded browser, encrypted in the vault under a master password,
filled on click ([draft](design/password-manager.md), to be checked against 3.5.0). With sessions
kept on each computer, it is what makes signing in again on another one quick.

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
  of `library/service.py` into `library/health.py` — landed in 3.4.0. The rest, in order:
  1. `store.ts` (1,650): navigation — places, tabs, Back and Forward, the openers — into `nav.ts`;
  2. `views/TitleView.vue` (1,700): the contents column — tree, edit mode, adding entries — into
     `TitleContents.vue`, and the pages pane into `PagesPane.vue`;
  3. `library/service.py` (2,060): chapter media — downloads, page capture, folder entries, the
     media sidecar's one writer — into `library/chapters.py`, and episodes' subtitles and stills
     into `library/episodes.py`;
  4. `library/vault.py` (1,600): the vault's documents (`read_doc`, `doc_transaction`, the
     library's own files) into `library/docs.py`;
  5. `store.ts`: the activity pollers (downloads, the vault pass, paused imports) into
     `activity.ts`, and the user layer's write-through into `userlayer.ts`.

  `views/BrowserView.vue` (1,250) and `shell/main.js` (1,160) are next in line once these land.
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
