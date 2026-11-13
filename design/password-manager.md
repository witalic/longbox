# Password manager — design

Status: **technical design approved** by the owner (2026-11-13) — the crypto, the file, where it
runs. **Managing logins: proposal for approval** — mockup `design/password-manager-mockup.html`.

The embedded browser signs in to the sites the library is filled from. Their logins are kept by
longbox itself: encrypted in the vault, unlocked with a master password, offered on a sign-in form
when asked for, and saved after a sign-in when the owner says so.

## Decisions (owner, 2026-11-12)

- **A master password, once per app start.** No PIN: a PIN is only worth having to re-unlock a
  session that locked itself, and a PIN that survives a restart would leave a copied vault file
  protected by the PIN alone. The secrets stay unlocked until the app closes or the owner locks
  them.
- **No binding to the OS or the device.** No DPAPI, no keychain: the master password alone opens
  the secrets, on any computer the vault is opened from.
- **In the vault** (design/vault-home.md): the encrypted file travels with the vault.
- **Filled on click**, never by itself.
- **Only the key is held; a password is opened when it is used** (owner, 2026-11-13). Unlocking
  keeps the data key in memory and the list of logins (site, username, dates) — never the
  passwords: each stays sealed on its own and is decrypted at the moment it is filled, revealed
  or copied, then dropped. Encrypting them again in memory under a key of the app's own was
  weighed and left out: that key would sit beside them in the same process, so it would hide
  nothing from anything able to read that memory.
- **Managing logins** (owner, 2026-11-13): a changed password keeps the previous ones, **up to
  three**, each with all four actions — show, copy, use again, forget; a login is **edited right in
  the browser window, bound to the site that is open**; a login can be **deleted**, at once, with no
  bin. A mockup first (§ Managing logins).
- **The key is a button of the toolbar's** (owner, 2026-11-13), beside the star and the shield and
  in their format — not an icon in the address bar; its popover **hangs from it**, as every menu of
  the app does, never floating apart from its button.
- **Ad blocking in the same format** (owner, 2026-11-13): the shield opens a popover of its own,
  and the ⋯ menu gives up ad blocking (§ Ad blocking, from the shield).

## Threat model

| Who | Has | Defended by |
|---|---|---|
| someone who copies the vault (it lives on a network share) | the encrypted file, offline, unlimited time | a slow KDF over the master password; nothing else in the file helps |
| a web page in the embedded browser | the page it is on | exact-origin matching, fill only on click, never into another origin's frame, the choice made in the app's UI — not in the page |
| someone at the unlocked computer | the running app | out of scope (lock button; the app closes) |

The master password's strength IS the protection of a copied vault. Setting it shows a strength
estimate and refuses a short one (under 10 characters).

## Where it runs

In the **shell's main process** (Node `crypto`), not in the sidecar and not in the window:

- the data key lives in main-process memory while unlocked, and nowhere else; a password is
  decrypted into a `Buffer` at the moment of use and zeroed after it — what crosses into the
  window or a page as a string cannot be wiped, so it crosses only when it must;
- the app window gets what a list needs (site, username, dates) and a password only when the owner
  reveals or copies one;
- a guest page gets a credential only when the owner fills it, only for its own top-level origin.

The shell already writes into the vault (`.longbox/adblock`, partial downloads), so the file
belongs there with no sidecar round trip.

## On disk: `<vault>/.longbox/secrets.json`

```json
{ "schema": 1,
  "kdf":  { "alg": "scrypt", "N": 131072, "r": 8, "p": 1, "salt": "<16 bytes, b64>" },
  "key":  { "nonce": "<12 b64>", "data": "<wrapped 32-byte data key, b64>", "tag": "<16 b64>" },
  "data": { "nonce": "<12 b64>", "data": "<entries, AES-256-GCM, b64>", "tag": "<16 b64>" } }
```

- **Two keys.** scrypt(master password, salt) → the key-encryption key, which unwraps a random
  256-bit data key (AES-256-GCM). The data key encrypts the entries. Changing the master password
  re-wraps the data key only. A wrong password fails the wrapped key's authentication tag — no
  separate "check" value to attack.
- **scrypt, N = 2^17** (~128 MB, ~0.5 s per guess on a desktop CPU): memory-hard, built into Node,
  no native dependency. The parameters are stored, so they can be raised later.
- A fresh nonce on every write; the schema and kdf block are the GCM additional data, so a file
  whose header was edited does not decrypt.
- Written atomically (tmp → rename), as every vault write.
- The entries, once decrypted: `[{ id, origin, username, secret, previous, note, created, updated,
  used }]`, where `secret` is the password sealed on its own with the data key (`{ nonce, data, tag
  }`) — decrypting the list opens no password — and `previous` the passwords it replaced, newest
  first, at most three: `[{ secret, replaced }]`, each sealed the same way. `origin` is scheme +
  host + port, exactly as the sign-in page's.

Forgetting the master password loses the secrets. Setup says so, plainly, once.

## Unlocking

- Asked for at the first thing that needs the secrets: a sign-in form's key, the Logins tab, the
  save prompt. One dialog in the app window, over everything; not in a page.
- First use: set the master password (twice), with the strength estimate and the warning above.
- **Lock now** in the browser's ⋯ menu and in Settings; the app quitting, or the vault dropping,
  locks too.
- Settings → Passwords: change the master password; lock.

## In the browser

- **Fill on click.** The key is one of the page's buttons right of the address, with the star and
  the shield: always there, quiet when the page has no sign-in form and nothing is saved for it,
  normal on a page with a password field (the guest preload looks, top frame only), lit with a
  count when logins are saved for that exact origin (known once unlocked). Its popover hangs from
  it — right under the toolbar, its right edge on the button's, the button lit while it is open —
  and lists the accounts: **Fill** puts the username and password into that form. With no saved
  login, it offers to generate a password (a sign-up form's new-password field).
- The menu is the app's, not the page's: a page cannot draw a fake one or click it for the owner.
- The fill goes to the top frame only, and only if its origin is still the entry's at the moment of
  filling (a page that navigated meanwhile gets nothing). http pages get only logins saved on http.
- **Save after sign-in.** When a form with a password is submitted, the preload reports the origin,
  username and password to the shell; the shell shows a bar in the app: *Save login for x.com?* —
  Save · Never for this site · Not now. An existing login with another password offers *Update*.
  Nothing is kept unless saved.

## Ad blocking, from the shield

What the ⋯ menu holds of ad blocking today moves into a popover of the shield's, in the key's
format and hanging from the shield the same way:

- its head: **Ad blocking** and the switch for every site;
- **this site**: what was blocked on the page, **Pause here** — or **Resume**, when it is paused;
- **Hide cookie banners, pop-ups**, with what it can cost (a page waiting for a consent it never
  got);
- **Paused on**: the sites it is paused on, × to block there again;
- its foot: when the lists were fetched, and **Update the lists** (or *Try again*).

The ⋯ menu keeps what has no button of its own: zoom, find in page, DevTools. The shield itself is
as it is: green with the count of what it blocked on this page, grey where it is paused or off.

## Generator

Length 20 by default (8–64), letters / digits / symbols toggles, look-alike characters left out;
`crypto.randomInt` over the allowed set, at least one of each chosen kind. Offered in the save bar,
the key menu of a sign-up form, and the login editor.

## Managing logins

One **login editor** — site, username, password (show, copy, generate), note, previous passwords,
Delete — drawn in two places: the key's popover in the browser, and Sources → a site → Logins.

- **The key's popover** lists the logins of the page's exact origin: **Fill**, and a pencil that
  turns the popover into the editor, in place. **Add a login for this site** opens it empty.
- **Bound to the open site.** The editor's site is the origin the tab was on when it opened —
  shown at its head, never typed. A tab that goes to another site meanwhile leaves the editor
  with its own site (it says so; what was typed is kept): a login never moves to another site by
  navigating.
- **Previous passwords, up to three.** Saving a new password (typed, generated, or **Update** after
  a sign-in) puts the current one at the top of the previous ones, with the day it was replaced;
  with three there already, the oldest goes — and the editor shows it struck through, *dropped on
  Save*, before it happens. Each can be shown, copied, **used again** (it becomes the password; the
  current one takes its place) or **forgotten** (one row; the editor's Cancel takes it back).
  Changing a password here changes it in longbox only — the editor says so; the site's own form
  changes it there.
- **Delete** sits at the editor's foot, apart from Save, and always asks: the account, the site,
  and how many previous passwords go with it. There is no bin — gone from the vault's file at
  once.
- **Sources → a site → Logins** (the tab the Sources mockup reserves): one card per login —
  username, origin, last used, how many previous; copy on the closed card, the pencil opens the
  same editor in place; **Add login** asks for the origin, prefilled with the site's homepage.
- A copied password leaves the clipboard after 30 seconds, if the clipboard still holds it.
- Settings → Passwords: the lock state, change the master password, how many logins there are.

## Out of scope (for now)

Sync, passkeys, one-time codes (TOTP), importing from another browser or manager (a CSV import could
follow), auto-lock after idle (can be added later: it would ask for the master password again).

## Verification

The crypto and file format are a pure module (`shell/secrets.js`) with `node --test` tests:
round trip, wrong password, a tampered header or body refused, a re-wrap on password change keeps
the entries, a file from a newer schema refused; a fourth change of one login's password drops
its oldest previous one, **use again** swaps two, deleting a login takes its previous passwords. The fill / save paths are checked off-screen with
a local sign-in page in an Electron harness, on a test vault only.
