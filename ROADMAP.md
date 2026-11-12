# Roadmap

Where longbox is going, in the order it is being done. Every larger item starts as a design note
and, for anything on screen, a mockup in [design/](design/) that the owner approves first; what has
shipped moves to the [changelog](CHANGELOG.md). What longbox will not do is at the end.

## Now: hardening

From an independent review of the code (2026-11-13). Small, and nothing here waits on a decision.

- **DevTools only in a development run.** F12 opens them in a released build today.
- **A cover fetched by its address never reaches the local network.** The sidecar follows
  redirects with no check, and the address can come from a page; a loopback, private or
  link-local address is to be refused at every hop.
- **Archives are converted entry by entry.** A rar, 7z or zip is read into memory whole on its way
  in; a large one should stream.
- **Dependencies are watched.** Dependabot for Electron (each Chromium security fix becomes a
  pull request, then a release), the Python and npm dependencies, and the workflows' actions.

## Next

- **Sign-ins encrypted at rest, by default.** The embedded browser keeps its cookies in the vault
  unencrypted today, so anyone who can read the vault — a share, a removable disk, a backup, a
  synced folder — can take the sessions. Electron's cookie encryption (a switch set when the app
  is built, using the system's keys: DPAPI, Keychain, libsecret) becomes the default, with the
  other hardening switches of the same kind (no `ELECTRON_RUN_AS_NODE`, no `NODE_OPTIONS`, no
  `--inspect`). Encrypted sign-ins stay with the computer and its user account; whoever wants
  them to travel with the vault chooses that in Settings, and the app keeps a copy that opens on
  any computer — said plainly as the trade-off it is, and sealed by the password manager's master
  password once that exists. Design first: how an existing vault's sign-ins carry over.
- **Password manager.** Logins for the sites in the embedded browser, encrypted in the vault under
  a master password, filled on click ([draft](design/password-manager.md)).
- **Knowing a release is out.** On first launch the app asks whether to check for new versions;
  if yes, it says when one is out and links to it — nothing is downloaded by itself. (Updating
  itself waits for signed builds, below.)
- **OPDS.** The vault served to readers on other devices — KOReader, Panels, Chunky and the rest —
  over the local network.
- **Saved filter sets.** A combination of filters kept under a name, a click away.

## Ongoing: quality

Done a step at a time, alongside the work above.

- **Linters in CI**: ruff for the sidecar, eslint for the UI and the shell — first a rule set the
  code already passes, then tightened.
- **A UI smoke test in CI**: the off-screen Electron run that takes the project page's screenshots
  (`tools/screenshots.py`) already drives the real app through thirteen scenes over a generated
  vault; it becomes a test that fails when a scene does.
- **Tests for the shell**: the vault checks, the lock and the move (`shell/vaults.js`) with
  `node:test`.
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
