'use strict'
// Knowing a release is out (design/update-check.md): one request to GitHub for
// the latest release of longbox, and whether it is newer than the one running.
// The shell asks only when the window does — whether, and how often, is the
// user's answer, kept with the window's preferences in the vault.

const LATEST = 'https://api.github.com/repos/witalic/longbox/releases/latest'
// every page a check may send the user to lies under this one
const RELEASES = 'https://github.com/witalic/longbox/releases/'

// "v3.4.1" or "3.4.1" → [3, 4, 1]; null for anything else (a pre-release tag too)
function parse(v) {
  const m = /^v?(\d+)\.(\d+)\.(\d+)$/.exec(String(v ?? '').trim())
  return m ? m.slice(1).map(Number) : null
}

function newer(a, b) {
  const x = parse(a), y = parse(b)
  if (!x || !y) return false
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i]
  return false
}

// What the window is told about GitHub's answer. A draft, a pre-release or a
// tag that is not a plain version is no news.
function describe(release, current) {
  const latest = String(release?.tag_name ?? '').replace(/^v/, '')
  if (!release || release.draft || release.prerelease || !parse(latest)) {
    return { latest: '', newer: false, published: '', security: false, notes: '' }
  }
  const page = String(release.html_url ?? '')
  return {
    latest,
    newer: newer(latest, current),
    published: String(release.published_at ?? ''),
    // the notes are the changelog's section: Keep a Changelog names this one
    security: /^###\s+Security\b/m.test(String(release.body ?? '')),
    notes: page.startsWith(RELEASES) ? page : `${RELEASES}tag/v${latest}`,
  }
}

// `fetch` is the main process's net.fetch (the system's proxy and certificates);
// never throws — a failure is an answer the window shows
async function check(fetch, current, timeoutMs = 15000) {
  try {
    const res = await fetch(LATEST, {
      headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'longbox' },
      signal: AbortSignal.timeout(timeoutMs),
    })
    if (!res.ok) return { ok: false, error: `GitHub answered ${res.status}` }
    return { ok: true, ...describe(await res.json(), current) }
  } catch (e) {
    const timedOut = e?.name === 'TimeoutError' || e?.name === 'AbortError'
    return { ok: false, error: timedOut ? 'the connection timed out' : String(e?.message ?? e) }
  }
}

// a page the window may open in the system's browser: a release of longbox
function isReleasePage(url) {
  return typeof url === 'string' && url.startsWith(RELEASES) && !/[\s"'<>]/.test(url)
}

module.exports = { check, describe, newer, isReleasePage, RELEASES }
