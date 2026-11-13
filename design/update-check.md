# longbox — knowing a release is out

Status: **built** (3.4.0) — mockup `design/update-check-mockup.html`, approved by the owner with
D1 (the answer lives in the vault) and D2 (daily) as proposed.

## 1. Why

longbox ships a browser people sign in with. A release that carries a Chromium security fix does
nothing for someone who never hears of it, and today nothing tells them: the app makes no request
of its own, and the project page promises exactly that ("no update check"). Updating in place
needs signed builds (ROADMAP, later); until then the app can at least say that a release is out.

## 2. What the user is asked

Once — on the first launch, and once after updating to the version that brings this — a card
asks: **"Check for new versions?"**, with two buttons of equal weight and neither chosen for them:

- **Check for new versions.** At most once a day, the app asks GitHub for the latest release of
  longbox. GitHub sees the computer's address and that someone runs longbox — nothing else is
  sent: no vault, no identifier, no version beyond what any request to a public page carries.
- **Don't check.** The app never makes the request.

The card sits over the foot of the sidebar — where the notice of a release appears later — says
where the answer can be changed (Settings → About), and stays until it is answered; it never
blocks the window.

## 3. When a release is out

A button in the sidebar's footer — one arrow in the accent colour, beside the activity button and
the theme switch, its tooltip naming the version — opens a panel above it: the
version, how old it is, the version running now, and two buttons that open the system's browser
(never the embedded one): **What's new** (the release page, whose notes are the changelog) and
**Download** (the latest release). **Not now** takes the button away until a newer version than
that one; Settings → About still shows it. (A chip in the title bar was tried first, and was too
loud for news that can wait.)

When the release's notes have a **Security** section, the arrow takes the warning colour and the
panel says so: *"It fixes security issues — in the built-in browser, among others."*

Nothing is downloaded or installed by itself.

## 4. Settings → About

Under the app's identity: **Check for new versions** (a switch), the last check and what it found
("you have the latest", "3.4.1 is out", or why it failed), and **Check now** — which makes one
request when clicked, whatever the switch says.

## 5. Decisions to take

- **D1 — where the answer lives.** Proposed: in the vault, with the window's other preferences
  (`settings.json`), as the vault-home rule asks of everything the app keeps. A second vault
  therefore asks once more. The alternative — `config.json` on the computer, asked once per
  computer — would add a second kind of thing to the file that today only says where the vaults
  are.
- **D2 — how often.** Proposed: at launch when the last check is more than a day old, then every
  24 hours while the app runs. The time of the last check is kept with the answer, so restarting
  the app does not ask again.

## 6. How it works

- The **shell** makes the request (`net.fetch` from the main process: the system's proxy and
  certificates, as the ad blocker's lists already do) to
  `https://api.github.com/repos/witalic/longbox/releases/latest`, reads `tag_name`,
  `published_at`, `html_url` and whether `body` has a `### Security` heading, and compares the tag
  with `app-meta.json`'s version. A draft, a pre-release or a version not newer is no news. A
  failure is reported in About and otherwise ignored; the next attempt is the next day.
- The window asks through the preload (`window.longbox.checkUpdates()`), and keeps the answer, the
  last check and the version that made it, what it found and the version dismissed with **Not
  now** in `settings.json` (`lb.updates`). A check is due a day after the last one — or at once
  when the last was made by another version (the vault is shared between computers) or is dated
  in the future (a clock put back).
- The window opens a release by naming its version (`openRelease('3.4.1')`, or `'latest'` for the
  download); the shell makes the address. Nothing from the vault is ever handed on as a URL.
- **Docs, in the same release:** the project page's "What it sends" note, the user guide
  (Settings), SECURITY.md (supported versions: how a user learns of a fix).

## 7. Not in this

Downloading or installing an update (waits for signed builds), a channel for pre-releases, any
other request of the app's own.
