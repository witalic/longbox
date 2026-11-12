# The vault is home

Status: **approved** (owner, 2026-11-09), with
[vault-launcher-mockup.html](vault-launcher-mockup.html); implemented.

Two things are wrong with how the app finds its library today:

- **A vault that does not answer holds the launch hostage.** The shell spawns the sidecar, the
  sidecar opens the configured path, and a network drive that is down makes that open wait for the
  network's own timeout — with no window, until the shell's 60-second health wait gives up.
- **A vault appears by itself.** With nothing configured, the sidecar falls back to
  `<app data>/library` and creates it. A fresh install quietly builds a library in a folder nobody
  chose, and nobody is told.

And a third the owner asked for in the same pass: **everything the app keeps lives in the vault.**
Today a good deal of it lives in the app's folder on this computer — the embedded browser's
sign-ins, site storage and cache, the window's preferences, the homepage, the ad blocker, transfers
a quit cut short, paused imports, the preview cache. A vault moved to another computer arrives
without them, and two vaults on one computer share them.

## Decisions (owner, 2026-11-09)

- **The app's folder keeps only where the vaults are.** `config.json` holds the registered vault
  paths and the one opened last. Nothing else is written there — not by the sidecar, not by the
  shell, not by Chromium.
- **Everything else is bound to its vault**, the embedded browser included: each vault has its own
  sign-ins, site data and cache.
- **Switching vaults restarts the app into the other one.** The browser's data lives in the vault,
  and a window cannot change the browser profile it was made with; a restart takes 2–3 s.
- **A vault in use is locked.** A vault open on another computer (or in a second copy of the app)
  is reported, and opened only when the human says so.
- **What this computer holds now moves into the open vault, once — caches included**
  (the browser's HTTP and code caches are ~650 MB today).
- **The launch window appears only when it is needed:** no vault yet, the vault does not answer, or
  it is in use. A vault that answers opens straight into the app, as today.

## Where everything lives

```
<app data>/                  this computer
  config.json                { "libraries": [...], "library_path": "<the one opened last>" }

<vault>/
  ...                        titles, recipes.json, sources.json, rules.json, … (as today)
  settings.json              what the human set: the homepage, the window's preferences
                             (theme, density, keys, reader, panel sizes, folds, the tabs it had)
  .longbox/                  the app's own working files (a dot folder: never a shelf)
    lock.json                who has the vault open: computer, process, since, last seen
    browser/                 the embedded browser's profile: sign-ins (cookies), site storage,
                             service workers, HTTP and code cache
    state.json               transfers a quit cut short, paused imports, import reports
    partial-downloads/       transfers being written (an ingest is then a rename, not a copy)
    adblock/                 the ad blocker's switch and paused sites, and its compiled lists
    cache/thumbs/            downscaled previews
```

| What                                   | Today (app data)                         | Moves to                      |
|----------------------------------------|------------------------------------------|-------------------------------|
| registered vaults, the last one        | `config.json`                            | stays                         |
| homepage                               | `config.json` `homepage`                 | `settings.json`               |
| window preferences (`lb.*`, 25 keys)   | Chromium localStorage, keyed by origin   | `settings.json`               |
| open tabs / session history            | localStorage `lb.session.*`              | `settings.json`               |
| interrupted transfers                  | `config.json` `interrupted_downloads`    | `.longbox/state.json`         |
| paused imports, import reports         | `config.json` `paused_imports`, `import_runs` | `.longbox/state.json`    |
| ad blocker on/off, paused sites        | `adblock.json`                           | `.longbox/adblock/prefs.json` |
| compiled filter lists                  | `adblock-engine.bin`, `.json`            | `.longbox/adblock/engine.*`   |
| partial downloads                      | `partial-downloads/`                     | `.longbox/partial-downloads/` |
| preview cache                          | `cache/thumbs/`                          | dropped; made again in `.longbox/cache/thumbs/` |
| cookies, site storage, caches          | Chromium profile (`Network/`, `Local Storage/`, `IndexedDB/`, `Cache/`, …) | `.longbox/browser/` |
| the sidecar's port                     | `shell.json`                             | gone — see below              |
| the default library                    | `library/`, made on first launch         | gone — nothing is made unasked |

`settings.json` and `state.json` are vault documents like `recipes.json`: read and written through
`Vault.read_doc()` / `Vault.doc_transaction()`, each with its own `SCHEMA`.

**Every path the vault keeps is relative to it** (owner, 2026-11-11). A vault is a folder that can
be moved, or opened from another computer under another drive letter or a UNC path, and must work
as it is: so nothing in it names the vault by its own absolute path. A path is written relative to
the vault, with forward slashes (`.longbox/partial-downloads/123-ep1.mp4`), and resolved against
the vault open now when it is read; a record an older build wrote with a whole path is re-pointed
by what it names. Only references to places outside the vault — an import's source folder — are
absolute. The browser's profile is Chromium's own and relocatable as a folder (it was moved here
from the app's folder with every sign-in intact).

**The window's preferences leave localStorage.** `local.ts` stays the one door to them, but behind
it is a copy of `settings.json` the window loads before it mounts and writes back (debounced)
through the API. Two things follow: the preferences no longer depend on the page's origin, so the
sidecar's port no longer has to be stable and `shell.json` goes; and a vault opened on another
computer brings its theme, keys and reader settings along.

**Chromium is kept out of the app's folder.** The browser profile is a session at a path
(`session.fromPath(<vault>/.longbox/browser)`, tested on Electron 33 and 45: a `<webview>` takes it).
Only the embedded browser's pages use it; the app window runs on an in-memory session, since
nothing of its own is kept in the browser any more. Chromium's own default profile, which nothing
uses, is pointed at a temporary folder before the app starts, and so are crash dumps — so the
app's folder holds `config.json` and nothing else.

## The launch

The shell decides which vault opens, **before** any sidecar exists, and never waits on a vault
without a window up.

1. Read `config.json`. No vault registered → the launch window, *first run*.
2. Ask the vault whether it answers: a `stat` of `<vault>/vault.json`, off the main thread. An
   answer inside ~1 s opens it with no launch window at all. Past that the window shows *opening*;
   past ~8 s, *can't reach*. The question keeps being asked every few seconds while the window is
   up, and the vault opens by itself the moment the drive answers.
3. Is it a vault? `vault.json` (every vault opened by a recent build has one), or an older vault's
   index (`index.db`) — such a vault is given its `vault.json` before anything is written beside
   it. A folder that is neither is not opened.
4. Is it in use? `.longbox/lock.json` names another computer seen in the last two minutes, or a
   process still running on this one (another copy of the app: a second build, a second user) →
   *in use*. Otherwise the shell writes its own lock — before anything else is written into the
   vault — refreshes "last seen" every 30 s, and removes it on quit. A lock left by a crash goes
   stale and stops counting; one taken over by another computer is not fought for.
5. The one-time move of this computer's data (below), when there is any.
6. Spawn the sidecar **naming the vault** it serves. The sidecar opens exactly that folder and
   creates nothing; with no vault named (a bare `run.py --backend` with nothing configured) it
   refuses to start and says why.
7. The app window, with the vault's browser session.

Anything that fails between the vault being chosen and the window coming up — the sidecar exiting,
the move, the vault refusing its lock — brings the app back to the launch window, which says what
went wrong, instead of a quiet exit the next launch would repeat. An older build's default library
(`<app data>/library`, served whenever nothing was chosen and never listed) is listed when it holds
titles, and removed when it holds nothing but the files that build made for itself.

The launch window is a second page of the frontend build (`launcher.html`, the same tokens from
`styles.css`), loaded from disk by the shell — there is no sidecar to serve it yet. It talks to the
shell over its own preload. Its theme follows the system: the theme the human chose lives in the
vault it has not opened yet.

### The launch window's states

- **Opening** — the vault's path and what it is waiting on; *Choose another vault*, *Quit*.
- **Can't reach** — what did not answer (a network drive names its share); it keeps trying; *Try
  again*, *Choose another…*, the other registered vaults with whether each answers, *Quit*.
- **First run** — *Create a new vault* (an empty folder, or a new one made inside the folder
  picked) or *Open an existing vault* (a folder that is one).
- **Create** — the folder and the name, the path that will be made, and whether it can be: a folder
  that holds other files is refused, not adopted.
- **In use** — which computer, since when, last seen when; *Open anyway* (a warning, not the
  default, while the lock is fresh), *Choose another…*, *Quit*.
- **Moving this computer's data** — once, with progress per part; a copy that fails says so,
  removes nothing, and offers *Try again*.

## Switching vaults

Settings → Storage lists the registered vaults as today. Opening another one — or creating one —
asks first (*longbox restarts into Archive — its browser, recipes and settings come with it*), then
the shell records it as the last vault and relaunches. Every check of the launch applies to it; a
vault that does not answer or is in use opens the launch window instead of the app. The sidecar's
on-the-fly library switch goes.

## The one-time move

On the first launch of this build, whatever the app's folder still holds besides `config.json`
moves into the vault being opened:

- `config.json`'s `homepage`, `interrupted_downloads`, `paused_imports`, `import_runs` → the vault's
  documents; the keys leave `config.json` only once the vault holds them.
- The Chromium profile's folders → `.longbox/browser/`, caches included, copied **before** the
  vault's session is opened.
- The window's localStorage → `settings.json`: the shell reads the old keys out of the profile it
  moved (a hidden page at the old origin — `shell.json` still names the old port — that never
  reaches the network) and hands them to the sidecar in `.longbox/legacy-window.json`, which takes
  them in where the vault has nothing of its own.
- `adblock*`, `partial-downloads/` → their places under `.longbox/`.
- `shell.json`, Chromium's profile-wide files, the old previews (`cache/thumbs/` — a cache, made
  again on demand; on Windows the same folder as Chromium's `Cache`, whose `thumbs` is never
  copied), the empty legacy `recipes/` → deleted.

Copy first, delete last. Nothing is deleted until everything is copied and the old window
preferences are in the vault; a copy cut short or failed leaves the app's folder as it was, the app
does not open a vault half-moved into, and the next try starts over. The browser's folders are
copied beside their place and renamed into it as soon as they are all there, so a half-copied
profile is never taken for the vault's own, and a later part that fails does not cost it. Only
names known to be an older build's are deleted — never whatever else the folder holds. A vault that
already has a browser profile of its own keeps it, and the old one is dropped — the old data goes
into the FIRST vault that opens, and only that one. A partial transfer's record follows its file
into the vault.

## When the vault drops while the app is open

Today nothing notices. Every read and write either fails at once or waits out the network's own
timeout (tens of seconds a call), so the window freezes and fills with errors while every
background job fails file by file. Worse, two cases write where they must not: a vault on a share
mounted at a folder (`/mnt/nas`, the usual Linux setup) that unmounts leaves the folder behind, and
`mkdir(parents=True)` then quietly builds titles and a fresh `index.db` on the local disk inside
it; and a drive letter re-mapped to another share takes the writes there. The answer is one rule —
**while the vault is gone, nothing is read from it or written anywhere** — and a predictable way
back.

- **The vault has an identity.** `vault.json` gains an `id`, made when the vault is created (and
  once, by this build, for a vault that predates it). The sidecar remembers the id it opened.
- **Nothing creates a vault's root, or anything above it.** `Vault` no longer `mkdir`s its root;
  only *Create* in the launcher does. Every directory the vault makes is made under a root that
  still holds `vault.json` — so an emptied mount point, or another share under the same letter,
  refuses the write instead of taking it.
- **A watchdog asks.** Every 5 s the sidecar reads `<vault>/vault.json` on a thread of its own,
  with a 5 s deadline. Missing, unreadable, another `id`, or no answer in time — twice in a row,
  the second asked 1.5 s after the first — and the vault is **lost**. Lost is the watch's to say:
  one request that failed only makes it ask sooner. A vault operation that fails with an I/O
  error asks at once instead of waiting for the next tick.
- **Lost, the sidecar goes quiet.** Every `/api` request answers 503 *the vault is unreachable* at
  once — nothing waits on the drive, nothing writes. Background work stops where it is: the vault
  verification and one-time passes, playable copies (ffmpeg is stopped), a folder import (running
  it again later skips what was stored), a vault check.
- **The window says so, and lets go of the drive.** An overlay covers the app: which vault, since
  when, and what stopped. The browser's tabs are unloaded, so no site keeps writing into the
  vault's browser profile, and transfers in flight are stopped — their partial files are on the
  vault, so they start over once it is back. The app window itself never wrote to the vault's
  browser profile: it runs on an in-memory session of its own (its preferences live in
  `settings.json` now, and its auth cookie never needs to reach a disk); only the embedded
  browser's pages use the vault's profile.
- **Back, the app restarts into it.** Two good answers in a row (the same `id`) and the app
  restarts into the vault — a fresh sidecar, a fresh index connection, a fresh browser profile —
  and the window reopens the tabs it had. A restart, not a patch-up: after a drop, the open
  handles to the index and to the browser's databases cannot be trusted, and re-using them is how
  a cookie store or a site's storage gets thrown away as corrupt. An edit that was not saved when
  the vault went cannot be saved anywhere; the overlay says so while it waits.
- The overlay offers *Choose another vault…* (a restart into the launcher) and *Quit*. A quit while
  the vault is gone leaves the lock behind (nothing more is asked of a dead drive); the next launch
  finds it stale. Transfers cut off by a restart into another vault are put down with their place
  kept, in the vault being left.

## What it costs

- **The browser works off the network drive.** Sign-ins, site storage and the HTTP cache are read
  and written on the vault's drive, so pages in the embedded browser load a little slower from a NAS
  than from a local disk, and a drive that drops mid-session can lose the last few writes of a
  site's storage.
- **Sign-ins are only as private as the share.** Electron stores cookies unencrypted, so anyone who
  can read `<vault>/.longbox/browser/` can use the sites' sessions. On a drive only the owner can
  read, nothing changes.
- **Two computers cannot use one vault at once** — the lock makes that visible instead of letting
  the index and the browser profile be damaged.
- Switching vaults is a restart, not an instant swap.

- On Linux, if the share unmounts and leaves its mount point behind, the browser can still flush a
  few seconds of cookies into that local folder before its tabs are unloaded. They are hidden when
  the share mounts again, and the vault itself is never touched.

## Not in this pass

- Read-only opening of a vault in use elsewhere.
