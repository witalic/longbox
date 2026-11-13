# longbox — where sign-ins live

Status: **built** (3.5.0) — mockup `design/sign-ins-mockup.html`, approved by the owner with D1
and D2 as proposed.

## 1. Why

The embedded browser keeps its profile — cookies (the sign-ins), site storage, service workers,
caches — in the vault, at `.longbox/browser/` (design/vault-home.md). So whoever can read the vault
can read the sessions: a network share, a removable disk, a backup, a synced folder. An
independent review of the code (2026-11-13) named it.

Encrypting the profile is not the answer here: Electron's cookie encryption is a switch set when
the app is built, and once on it can never be turned off — it could not be a choice. An encrypted
copy under a master password was weighed and left out (the owner, 2026-11-13). What can be a
choice is **where the profile lives**, and that covers site storage too, not only cookies.

## 2. The choice, per vault

- **In the vault** — what longbox does today. The sign-ins travel with the vault to every
  computer that opens it, and are as safe as the vault is: an encrypted disk protects its disks,
  not the folder from whoever can open it.
- **On each computer** — the profile lives in the app's folder on the computer that opened the
  vault, under the vault's id (`<app folder>/browsers/<vault id>/`). Nothing of the sessions is in
  the vault, its backups or its synced copies; another computer starts signed out and signs in on
  its own.

Each vault on a computer has a profile of its own: the folder is named by the vault's **id** (the
random one in its `vault.json`), never by its path — a vault opened from another drive letter or a
share keeps its sign-ins, and two vaults never share a profile. (A vault folder copied to start a
second vault carries the same id, so on one computer the two would share their sign-ins; a vault
restored from a backup is the same vault, and that is right for it.)

The choice belongs to the vault, so every computer that opens it does the same: it is kept in
`.longbox/browser.json` (`{ "schema": 1, "keep": "vault" | "computer" }`), the shell's own file,
read before the browser opens. **The app never chooses for the user:** both answers are offered
with equal weight, and nothing is preselected.

## 3. When it is asked

- **Whenever a vault that has not answered is opened** — one screen of the launch window, before
  the browser opens. That is every existing vault, once, the first time this version opens it;
  and a new one, straight after **Create vault**, since a vault just made has not answered either
  (one closed at that moment is asked again next time). Two cards of equal weight, each with its
  own button: nothing to preselect. Nothing moves until it is answered, and **In the vault**
  changes nothing on disk; an existing vault's card says how much would move.
- **Settings → Browser, any time, either way.** A row says where the sign-ins are, with
  **Change…**: a confirmation says what the move does and that it happens on the next start, and
  **Restart now** applies it.

## 4. The move

At start, in the launch window, before the browser opens — the same mechanism as the one-time move
of 3.0 (shell/vaults.js): the profile is copied beside its destination, renamed into place when
the copy is whole, and only then removed from where it was. A copy that fails stops there; the app
does not open with half a profile, and **Try again** starts it over. The window shows the progress.

- What moves is the sign-ins and site data. **The caches are deleted, not moved:** once the copy
  is whole, the old profile is removed entirely, its caches with it, and nothing of it is left
  where it was. The new place starts with an empty cache, which refills as pages are opened.
- **vault → computer:** `<vault>/.longbox/browser/` → `<app folder>/browsers/<vault id>/`.
- **computer → vault:** the reverse. The vault cannot already hold a profile then: moving out
  removed it.
- A computer that opens a vault keeping sign-ins **on each computer**, with no profile of its own
  for it yet, starts with an empty one — the sites ask to sign in.

## 5. Decisions to take

- **D1 — a computer's leftover profile.** Computer A switches a vault back to **in the vault**;
  computer B still holds its own profile for that vault. Proposed: B removes it the next time it
  opens the vault, and the launch window says so in one line — two profiles for one vault would
  leave it unclear which sign-ins are in use, and the vault's answer is the owner's.
- **D2 — forgetting a vault.** Proposed: Settings → Storage's **Forget** also removes this
  computer's sign-ins for that vault, and its confirmation says so — the vault itself is never
  touched.

## 6. The rule it changes

"The vault is home" gains one exception, written where the rule is (design/vault-home.md,
CLAUDE.md, ARCHITECTURE.md): the app's folder holds `config.json` — and the browser profiles of
the vaults that keep their sign-ins on each computer.

## 7. Tests: the shell's own

The first tests of the shell beyond the update check (`node:test`, run in CI):

- the move: planned, copied, renamed into place; a copy that fails leaves the source whole; caches
  skipped;
- `browser.json`: absent means asked; a newer schema is read, never written;
- the vault's checks and its lock (`shell/vaults.js`): what answers, what is a vault, a lock left
  by this computer, a lock held by another.

## 8. Docs

The user guide (the vault; Settings → Browser), the project page (the storage table and "the vault
is home" paragraph say the sign-ins live in the vault — they say where now), SECURITY.md (the
profile and its place in scope), the changelog.

## 9. Not in this

Encrypting the profile in any form; carrying the browser's caches across (they are deleted); a
per-site choice.
