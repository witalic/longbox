'use strict'
// Ad blocking in the browser's pages (design/browser-refactor.md, "Ad blocking").
//
// The lists are Adblock Plus's own — EasyList and EasyPrivacy — with the uBlock
// Origin fixes that keep sites from breaking, read by Ghostery's engine. Its own
// way in (`enableBlockingInSession`) is not taken: Electron keeps ONE listener per
// webRequest event and session, so it would replace the shell's (the CSP of the
// app's own pages), and it registers its preload with an API this Electron does
// not have. The shell calls the engine from its own listeners instead — for a
// guest page only, and never on a site the blocking is paused for.
const { BrowserWindow, ipcMain, net, webContents } = require('electron')
const fs = require('node:fs')
const path = require('node:path')
const { ElectronBlocker } = require('@ghostery/adblocker-electron')

// The lists come from where they are made — never from a mirror: the engine's
// own (Ghostery's) stopped in August 2026 while EasyList changes daily. uBO's
// filters.txt brings its yearly files in with `!#include`, so next year's
// arrives by itself.
const UBO = 'https://ublockorigin.github.io/uAssets/filters/'
const EASYLIST = 'https://easylist.to/easylist/easylist.txt'
const LISTS = [
  EASYLIST,
  'https://easylist.to/easylist/easyprivacy.txt',
  `${UBO}filters.txt`, `${UBO}badware.txt`, `${UBO}privacy.txt`, `${UBO}quick-fixes.txt`, `${UBO}unbreak.txt`,
  'https://pgl.yoyo.org/adservers/serverlist.php?hostformat=adblockplus&showintro=1&mimetype=plaintext',
]
// Cookie banners and other pop-ups — only when asked for: hiding a consent
// banner can leave a page waiting for the consent it never got.
const ANNOYANCES = [
  'https://secure.fanboy.co.nz/fanboy-cookiemonster.txt',
  `${UBO}annoyances-cookies.txt`, `${UBO}annoyances-others.txt`,
]
// The scriptlets and stubs filters name, in the engine's own format: only its
// mirror has them, and they change far less often than the lists do.
const RESOURCES =
  'https://raw.githubusercontent.com/ghostery/adblocker/master/packages/adblocker/assets/ublock-origin/resources.json'
// What a list's `!#if` blocks test: this is desktop Chromium, and the styles go in
// as the user's own sheet. Anything else (Firefox, mobile, uBO Lite…) is false.
const ENV = new Map([['env_chromium', true], ['cap_user_stylesheet', true]])

// the engine's preload: the page asks it which elements to hide
const PRELOAD = require.resolve('@ghostery/adblocker-electron-preload',
  { paths: [path.dirname(require.resolve('@ghostery/adblocker-electron'))] })
// EasyList asks to be fetched again after four days; uBO after five
const STALE_MS = 4 * 24 * 60 * 60 * 1000
const CHECK_MS = 6 * 60 * 60 * 1000 // how often a running app looks
const FETCH_MS = 60 * 1000          // a list that has not come by then fails the update

// The vault's: its switch, its paused sites and the lists compiled for it live
// in `<vault>/.longbox/adblock/` (design/vault-home.md) — set by `wire`.
let home = ''
const prefsFile = () => path.join(home, 'prefs.json')
const engineFile = () => path.join(home, 'engine.bin')
const metaFile = () => path.join(home, 'engine.json')

// `paused`: sites, as the app names them; `annoyances`: cookie banners, pop-ups
let prefs = { on: true, annoyances: false, paused: [] }
let engine = null
let built = { fetchedAt: 0, listsAt: 0, annoyances: false } // what the engine in use was built from
let updating = null // the fetch in flight
let failed = ''     // why the last fetch did not land
let checkTimer
const blocked = new Map() // guest webContents id → requests blocked on its page
const told = new Set()    // ids whose count changed since the app was last told
let tellTimer

// `www.` is no site of its own: the app names a site as data.ts `hostOf` does
const siteOf = (url) => {
  try { return new URL(url).hostname.toLowerCase().replace(/^www\./, '') } catch { return '' }
}
function pausedOn(url) {
  const host = siteOf(url)
  return !!host && prefs.paused.some((d) => host === d || host.endsWith(`.${d}`))
}
function activeFor(wc) {
  return !!engine && prefs.on && !!wc && !wc.isDestroyed() && wc.getType() === 'webview' && !pausedOn(wc.getURL())
}

function state() {
  return {
    on: prefs.on, annoyances: prefs.annoyances, ready: !!engine, updating: !!updating,
    updatedAt: built.fetchedAt, listsAt: built.listsAt, failed, paused: [...prefs.paused],
  }
}
function tellApp(channel, payload) {
  for (const w of BrowserWindow.getAllWindows()) w.webContents.send(channel, payload)
}

async function writeAtomic(file, data) {
  const tmp = `${file}.${process.pid}-${Date.now()}.tmp` // its own: a vault is shared by computers
  await fs.promises.writeFile(tmp, data)
  await fs.promises.rename(tmp, file)
}
function readPrefs() {
  try {
    const raw = JSON.parse(fs.readFileSync(prefsFile(), 'utf8'))
    prefs = {
      on: typeof raw.on === 'boolean' ? raw.on : true,
      annoyances: raw.annoyances === true,
      paused: Array.isArray(raw.paused) ? raw.paused.filter((d) => typeof d === 'string' && d) : [],
    }
  } catch { /* none yet, or unreadable: blocking on, nothing paused */ }
}

// ---- what was blocked on each page: the app shows it on the tab's toolbar ----
function tellSoon(id) {
  told.add(id)
  if (tellTimer === undefined) tellTimer = setTimeout(tellCounts, 400)
}
function count(id) {
  if (!blocked.has(id)) webContents.fromId(id)?.once('destroyed', () => blocked.delete(id))
  blocked.set(id, (blocked.get(id) || 0) + 1)
  tellSoon(id)
}
function tellCounts() {
  tellTimer = undefined
  for (const id of told) tellApp('lb-adblock-blocked', { guestId: id, count: blocked.get(id) || 0 })
  told.clear()
}
function use(fresh) {
  fresh.updateEnv(ENV)
  const onBlocked = (request) => { if (request.tabId > 0) count(request.tabId) }
  fresh.on('request-blocked', onBlocked)
  fresh.on('request-redirected', onBlocked)
  engine = fresh
}

// ---- the lists ----
async function fetchText(url) {
  const res = await net.fetch(url, { signal: AbortSignal.timeout(FETCH_MS) })
  if (!res.ok) throw new Error(`${new URL(url).host} answered ${res.status}`)
  return res.text()
}
// A list with what it includes. An include under an `!#if` is another
// platform's (uBO's filters-mobile), and one from another site is not taken.
async function fetchList(url) {
  const text = await fetchText(url)
  const includes = []
  let depth = 0
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (line.startsWith('!#if ')) depth += 1
    else if (line === '!#endif') depth = Math.max(0, depth - 1)
    else if (depth === 0 && line.startsWith('!#include ')) {
      const sub = new URL(line.slice('!#include '.length).trim(), url)
      if (sub.origin === new URL(url).origin) includes.push(sub.href)
    }
  }
  return [text, ...await Promise.all(includes.map(fetchText))].join('\n')
}
// when EasyList itself was last changed — what the app shows as the lists' age
function lastModified(list) {
  const m = /^! Last modified:\s*(.+)$/m.exec(list.slice(0, 4000))
  const at = m ? Date.parse(m[1].trim()) : NaN
  return Number.isNaN(at) ? 0 : at
}

// Fetch the lists and build the engine anew; the one in use stays until the new
// one is ready, and stays for good when the fetch fails.
function update() {
  if (updating) return updating
  updating = (async () => {
    const withAnnoyances = prefs.annoyances
    const urls = withAnnoyances ? [...LISTS, ...ANNOYANCES] : LISTS
    const [lists, resources] = await Promise.all([Promise.all(urls.map(fetchList)), fetchText(RESOURCES)])
    const fresh = ElectronBlocker.parse(lists.join('\n'), { enableCompression: true, loadPreprocessors: true })
    fresh.updateResources(resources, String(resources.length))
    use(fresh)
    built = { fetchedAt: Date.now(), listsAt: lastModified(lists[urls.indexOf(EASYLIST)]), annoyances: withAnnoyances }
    failed = ''
    await writeAtomic(engineFile(), Buffer.from(fresh.serialize()))
    await writeAtomic(metaFile(), JSON.stringify(built))
  })().catch((e) => { failed = String(e && e.message || e) }).finally(() => {
    updating = null
    tellApp('lb-adblock-changed', state())
  })
  tellApp('lb-adblock-changed', state())
  return updating
}
const stale = () => !engine || Date.now() - built.fetchedAt > STALE_MS || built.annoyances !== prefs.annoyances

// The engine from the last fetch, at once; the lists fetched again when they are
// old, missing or built for the other setting — now, and every few hours while
// the app runs. Nothing is fetched while blocking is off.
async function load() {
  if (!prefs.on) return
  if (checkTimer === undefined) {
    checkTimer = setInterval(() => { if (prefs.on && stale()) void update() }, CHECK_MS)
  }
  if (!engine) {
    try {
      const meta = JSON.parse(await fs.promises.readFile(metaFile(), 'utf8'))
      use(ElectronBlocker.deserialize(new Uint8Array(await fs.promises.readFile(engineFile()))))
      built = {
        fetchedAt: Number(meta.fetchedAt) || 0, listsAt: Number(meta.listsAt) || 0, annoyances: meta.annoyances === true,
      }
    } catch { /* none yet, or from another version of the engine: fetched below */ }
    tellApp('lb-adblock-changed', state())
  }
  if (stale()) await update()
}

// ---- the session's request listeners call these (main.js owns the listeners) ----
function onBeforeRequest(details, cb) {
  const wc = details.webContents
  if (details.resourceType === 'mainFrame' && wc && blocked.has(wc.id)) {
    blocked.set(wc.id, 0) // a new page counts from nothing
    tellSoon(wc.id)
  }
  if (!activeFor(wc)) return cb({})
  engine.onBeforeRequest(details, cb)
}
function onHeadersReceived(details, cb) {
  if (!activeFor(details.webContents)) return cb({})
  engine.onHeadersReceived(details, cb)
}

function wire(sess, appContents, dir) {
  home = dir
  fs.mkdirSync(home, { recursive: true })
  readPrefs()
  sess.webRequest.onBeforeRequest(onBeforeRequest)
  // The preload runs in every page of the session (only the browser's pages use
  // it: the app's window has an in-memory session of its own); the
  // handlers answer only a guest page that is being blocked for.
  sess.setPreloads([...sess.getPreloads(), PRELOAD])
  // The engine puts its styles and scriptlets in through `event.sender`. Handed the
  // real one, every scriptlet run while the page loads waits for it to stop
  // (webContents.executeJavaScript does — a listener each, and the scriptlet comes
  // too late), and one that throws on this page is a rejection nobody catches. So
  // it gets the frame that asked, which runs them now, and their failures are the
  // page's business.
  ipcMain.handle('@ghostery/adblocker/inject-cosmetic-filters', (e, url, msg) => {
    if (!activeFor(e.sender) || !e.senderFrame) return undefined
    const frame = e.senderFrame
    const sender = {
      insertCSS: (css, opts) => e.sender.insertCSS(css, opts).catch(() => {}),
      executeJavaScript: (code, gesture) => frame.executeJavaScript(code, gesture).catch(() => {}),
    }
    return engine.onInjectCosmeticFilters({ frameId: e.frameId, processId: e.processId, sender }, url, msg)
  })
  ipcMain.handle('@ghostery/adblocker/is-mutation-observer-enabled', (e) =>
    activeFor(e.sender) && engine.config.enableMutationObserver)

  ipcMain.handle('lb-adblock-state', (e) => (e.sender === appContents ? state() : null))
  // { on } switches blocking everywhere, { annoyances } the cookie banners and
  // pop-ups (the engine is built again for it), { site, paused } one site
  ipcMain.handle('lb-adblock-set', async (e, change) => {
    if (e.sender !== appContents || !change || typeof change !== 'object') return null
    if (typeof change.on === 'boolean') prefs.on = change.on
    if (typeof change.annoyances === 'boolean') prefs.annoyances = change.annoyances
    const site = typeof change.site === 'string' ? change.site.toLowerCase().replace(/^www\./, '') : ''
    if (site && typeof change.paused === 'boolean') {
      prefs.paused = prefs.paused.filter((d) => d !== site)
      if (change.paused) prefs.paused.push(site)
    }
    await writeAtomic(prefsFile(), JSON.stringify(prefs, null, 2))
    tellApp('lb-adblock-changed', state())
    // switched on, or for the other lists: answered once the engine is ready for it
    if (typeof change.on === 'boolean' || typeof change.annoyances === 'boolean') await load()
    return state()
  })
  ipcMain.handle('lb-adblock-update', async (e) => {
    if (e.sender !== appContents) return null
    await update()
    return state()
  })
}

module.exports = { wire, load, onHeadersReceived }
