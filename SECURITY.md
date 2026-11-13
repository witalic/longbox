# Security

longbox runs on your own computer: a local sidecar serves the vault to the app's window, and an
embedded browser holds your sign-ins to the sites you use. A flaw in either matters, so reports
are welcome and taken seriously.

## Reporting a vulnerability

Report it privately through GitHub: the repository's **Security** tab → **Report a
vulnerability**. Please do not open a public issue for it. Say what you found, how to reproduce
it and which version you ran (Settings → About). longbox is maintained by one person, so an answer
may take a few days; a fix lands in the next release, and the report is credited unless you would
rather it were not.

## What is in scope

- **The local API.** The sidecar listens on the loopback address only, answers only its own window
  (a per-launch secret) and refuses a request whose `Host` is not local. A way around either — from
  a web page, another local user or another program — is a vulnerability.
- **The embedded browser.** Pages run in their own browser session, apart from the app's window.
  A page that can reach the app's API, its files or another site's session is a vulnerability.
  The session's sign-ins are stored unencrypted, in the vault or on each computer as the vault
  chose (Settings → Browser): whoever can read that folder can use them — that is the choice, not
  a flaw.
- **The password manager.** The logins it keeps (`.longbox/secrets.json`) are encrypted under the
  master password; the app's main process alone unlocks them, and a page gets a login only when
  you fill it, for its own site — the registrable domain, by the Public Suffix List, so a
  `github.io` page is never another's site. The list misses some names many people own (wildcard
  DNS such as nip.io), so filling a login into an address other than its own asks first, naming
  both. A page that can read a login, have one filled into another site without that question, or
  save one by itself — or a copy of the vault that gives a login up without the master password —
  is a vulnerability. Someone at the computer while the logins are unlocked is out of scope: they
  lock when the computer sleeps, its screen is locked or it goes unused for a while (Settings →
  Passwords), and with Lock. The app's own window is trusted with them — it can show any login —
  so script that gets into it, through anything the app displays, is a vulnerability in itself.
- **What arrives from outside.** Archives, images, videos and subtitles come from the web and from
  folders you import. A file that writes outside its title's folder or runs code when it is read
  is a vulnerability; one that merely fails to open is a bug — open an issue for that.

The content of the sites you visit, and of the vault itself, is out of scope: longbox ships no
code for any particular site and no content (see the README).

## Supported versions

Only the latest release is fixed; updating to it is the remedy for an older one. The app tells you
when a release is out — and says so when it fixes security issues — if you let it check for new
versions (asked on the first launch; Settings → About).
