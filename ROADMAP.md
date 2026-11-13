# Roadmap

Where longbox is going, release by release. Every larger item starts as a design note and, for
anything on screen, a mockup in [design/](design/) that the owner approves first; what has shipped
moves to the [changelog](CHANGELOG.md). What longbox will not do is at the end.

The first two releases answer an independent review of the code (2026-11-13).

## 3.4.0 — hardening, and knowing a release is out

- **DevTools only in a development run** — done: a released build opens them only when started
  with `LONGBOX_DEVTOOLS=1`.
- **A cover fetched by its address never reaches the local network** — done: every redirect is
  checked, and loopback, private and link-local addresses are refused.
- **Archives are converted entry by entry** — done: converting an archive, and reordering or
  moving a chapter's pages, holds one page in memory, not the archive.
- **The runtime's back doors closed.** Electron's build-time switches that let the app run as plain
  Node (`ELECTRON_RUN_AS_NODE`), take `NODE_OPTIONS` or open an `--inspect` port are turned off.
  Cookie encryption, another such switch, stays off on purpose: it cannot be undone once on, and
  where sign-ins live is the user's choice (3.5.0).
- **Knowing a release is out.** The app asks once — on the first launch, and once after updating
  to 3.4.0 — whether it may check for new versions. If yes, it asks GitHub for the latest release
  at most once a day (GitHub sees the address and nothing else) and says when one is out, with
  what is new and where to get it, and whether it fixes the built-in browser's security; nothing
  is downloaded by itself. If no, it never asks. Settings → About changes the answer and checks on
  a click. The project page's "no update check" is rewritten in the same release.
- **Dependencies are watched.** Dependabot for Electron (each Chromium security fix becomes a
  pull request, then a release), the Python and npm dependencies and the workflows' actions.

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
- **Smaller modules**, split by feature when they are being changed anyway: `library/service.py`,
  `library/vault.py`, `views/TitleView.vue`, `store.ts`.
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
