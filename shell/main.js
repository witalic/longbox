'use strict'
// Electron main process. Decides which vault the app opens (launcher.js — the
// launch window, only when it is needed), then spawns the FastAPI sidecar on a
// free loopback port to serve it, guards it with a per-launch secret (delivered
// as an HttpOnly SameSite=Strict cookie), confirms it reached its own sidecar via
// the /health identity echo, and loads the UI the sidecar serves at /app/. Locks
// down navigation, window-open, and permissions. Everything the app keeps lives
// in the vault (design/vault-home.md): this computer keeps config.json, and the
// browser profiles of the vaults that keep their sign-ins on each computer
// (design/sign-ins.md).

const { app, BrowserWindow, Menu, clipboard, dialog, ipcMain, net: electronNet, session, shell } =
  require('electron')
const { spawn } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const net = require('node:net')
const path = require('node:path')
const adblock = require('./adblock')
const launcher = require('./launcher')
const netguard = require('./netguard')
const passwords = require('./passwords')
const profile = require('./profile')
const updates = require('./updates')
const vaults = require('./vaults')

const ROOT = path.join(__dirname, '..')
const IS_WIN = process.platform === 'win32'
// A packaged build reads everything that is not code from its resources
// directory (the app itself lives inside an asar); a source checkout reads the
// repo it sits in.
const ASSETS = app.isPackaged ? process.resourcesPath : ROOT

// Debugging the app itself: in a development run, or a released build started
// with LONGBOX_DEVTOOLS=1 — never otherwise. A released build that opened
// DevTools on a key, or ran with Chromium's remote debugging (the DevTools
// protocol on a port or a pipe), would hand whoever reached it the app window,
// the vault's API and every session's cookies.
const DEVTOOLS = !app.isPackaged || process.env.LONGBOX_DEVTOOLS === '1'
if (!DEVTOOLS && ['remote-debugging-port', 'remote-debugging-pipe'].some((s) => app.commandLine.hasSwitch(s))) {
  app.exit(1)
}

// ONE source of app identity (window title, taskbar) — see <repo>/app-meta.json
let APP_META = { name: 'longbox', version: '0.0.0' }
try { APP_META = { ...APP_META, ...JSON.parse(fs.readFileSync(path.join(ASSETS, 'app-meta.json'), 'utf-8')) } } catch { /* defaults */ }

// The browser's data lives where the vault the app opens says (its profile is a
// session at a path in it, or in the app's folder); Chromium's own default
// profile, which nothing uses, and its crash dumps go to the system's temporary
// folder — so the app's folder on this computer holds config.json, and those
// profiles only.
app.setPath('sessionData', path.join(app.getPath('temp'), `${app.getName()}-chromium`))
app.setPath('crashDumps', path.join(app.getPath('temp'), `${app.getName()}-crashes`))
// The browser says it is the Chrome it is — its pages and every window they open.
// The tokens that give the app away are what a sign-in page (Google's first)
// refuses as an embedded app, and what a CDN sniffing for "Electron" refuses an
// image to.
app.userAgentFallback = app.userAgentFallback.replace(/\s*(Electron|longbox[\w-]*)\/[\d.]+/gi, '')
// The built UI on disk: the launch window is drawn from it before any sidecar
// exists to serve it.
const UI_DIR = app.isPackaged ? path.join(process.resourcesPath, 'ui') : path.join(ROOT, 'frontend', 'dist')
const ICON = path.join(__dirname, IS_WIN ? 'icon.ico' : 'icon.png')

// Chromium spams stderr with WebRTC STUN resolution failures (socket_manager:
// "Failed to resolve address for stun.l.google.com, errorcode: -105") whenever
// an embedded page probes WebRTC — harmless, but it buries the sidecar log.
// Keep only FATAL Chromium messages; our own [sidecar] piping is unaffected.
app.commandLine.appendSwitch('log-level', '3')
// Dev sidecar: the project venv + uvicorn. Packaged builds ship it frozen, with
// the built UI inside its own bundle, so neither Python nor the repo is needed.
const PYTHON = process.env.LONGBOX_PYTHON ||
  path.join(ROOT, '.venv', IS_WIN ? 'Scripts/python.exe' : 'bin/python')
const SIDECAR_BIN = path.join(ASSETS, 'sidecar', IS_WIN ? 'longbox-sidecar.exe' : 'longbox-sidecar')

let sidecar = null
let vaultRoot = ''        // the vault this run serves
let vaultSession = null   // the embedded browser's profile — in the vault, or on this computer
let profileDir = ''       // where that profile is (design/sign-ins.md)
let profileId = ''        // the vault's id, which names its profile on this computer
let profileShared = ''    // the other folder that profile may be the profile of too (a copy)
let letGoOfVault = () => {} // drops the vault's lock
let closeLibrary = async () => {} // asks the sidecar to close its library, once it serves one
let vaultLost = false     // the vault stopped answering: nothing more is asked of its drive
let windowShown = false   // until then a failure goes back to the launch window, not away
let partialHome = ''      // the vault's folder for transfers being written

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer()
    srv.unref()
    srv.on('error', reject)
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address()
      srv.close(() => resolve(port))
    })
  })
}

function sha256(s) {
  return crypto.createHash('sha256').update(s).digest('hex')
}

function spawnSidecar(port, token, root) {
  const opts = {
    env: {
      ...process.env,
      LONGBOX_AUTH_TOKEN: token,
      LONGBOX_PORT: String(port),
      // the vault the shell opened — the sidecar never picks or makes one itself
      LONGBOX_LIBRARY_PATH: root,
      // where the registered vaults are kept (and what an older build left there)
      LONGBOX_CONFIG_DIR: app.getPath('userData'),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  }
  const child = app.isPackaged
    ? spawn(SIDECAR_BIN, [], opts)
    : spawn(
      PYTHON,
      ['-m', 'uvicorn', 'app.main:app', '--app-dir', path.join(ROOT, 'backend'),
        '--host', '127.0.0.1', '--port', String(port), '--no-access-log'],
      opts,
    )
  child.on('error', (e) => {
    // a missing or unrunnable sidecar is the one failure the user cannot debug
    // from an empty window — say what is wrong before the health wait times out
    dialog.showErrorBox('longbox could not start',
      `The local server did not launch.\n\n${app.isPackaged ? SIDECAR_BIN : PYTHON}\n\n${e.message}`)
  })
  child.stdout.on('data', (d) => process.stdout.write(`[sidecar] ${d}`))
  // the end of what it said, for the launch window if it does not come up
  let said = ''
  child.stderr.on('data', (d) => {
    process.stderr.write(`[sidecar] ${d}`)
    said = (said + d).slice(-4000)
  })
  child.on('exit', (code) => {
    if (app.isQuitting) return
    console.error(`sidecar exited (${code})`)
    // before the window is up it was the vault that could not be opened: the
    // launch window says why, instead of the next launch trying it again blind
    if (!windowShown) {
      const last = said.trim().split(/\r?\n/).filter((l) => /error|refused|denied|not a vault|cannot/i.test(l)).pop()
      void relaunch(['--launcher', `--why=${encodeURIComponent(`The library could not be opened${last ? `: ${last.trim()}` : '.'}`)}`])
      return
    }
    app.quit()
  })
  return child
}

// Generous timeout: a cold Python start behind an antivirus scan plus a large
// vault rescan can legitimately take a while — giving up too early kills a
// sidecar that was almost ready.
async function waitForHealth(port, token, timeoutMs = 60000) {
  const want = sha256(token)
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/health`)
      if (res.ok) {
        const body = await res.json()
        // Identity check: confirm we reached OUR sidecar, not a port squatter.
        if (body.sha256 === want) return true
        throw new Error('health token mismatch — not our sidecar')
      }
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 150))
  }
  throw new Error('sidecar did not become healthy in time')
}

// A guest page's fullscreen is granted only once the app has given its <webview>
// the screen's size, so the window then grows around a view that keeps it. A
// <webview> resized twice in a row (stretched while the window grows) leaves the
// frames inside its page taking clicks only over the size in between — the
// right and bottom edge of a site's embedded player go dead until the page is
// resized again (design/browser-refactor.md, "Fullscreen").
const roomWaits = new Map() // token → resolve
function makeRoomForFullscreen(guest) {
  const host = guest.hostWebContents
  if (!host || host.isDestroyed()) return Promise.resolve()
  const token = crypto.randomUUID()
  return new Promise((resolve) => {
    const done = () => { roomWaits.delete(token); resolve() }
    roomWaits.set(token, done)
    // a host that never answers must not leave the page's request hanging
    setTimeout(done, 1000)
    host.send('lb-page-fullscreen', { guestId: guest.id, token })
  })
}

// The browser's pages and the requests they make tell the same story, as
// Chrome's do: the languages a page reads in navigator.languages are the ones its
// requests ask for, and the Client Hints it reads in navigator.userAgentData ride
// every https request — Electron sends none, and a Chrome that says nothing of
// itself there is what a bot check looks for. The hints are read off the app's
// own window, which has the same ones, before any page opens.
function presentAsChrome(sess, appContents) {
  sess.setUserAgent(app.userAgentFallback, app.getPreferredSystemLanguages().join(','))
  let hints = null
  appContents.once('did-finish-load', () => {
    appContents.executeJavaScript('(({ brands, mobile, platform }) => ({ brands, mobile, platform }))(navigator.userAgentData)')
      .then(({ brands, mobile, platform }) => {
        hints = {
          'sec-ch-ua': brands.map((b) => `"${b.brand}";v="${b.version}"`).join(', '),
          'sec-ch-ua-mobile': mobile ? '?1' : '?0',
          'sec-ch-ua-platform': `"${platform}"`,
        }
      })
      .catch((err) => console.error('reading the Client Hints failed:', err))
  })
  sess.webRequest.onBeforeSendHeaders({ urls: ['https://*/*'] }, (details, cb) => {
    if (!hints) return cb({})
    const sent = new Set(Object.keys(details.requestHeaders).map((k) => k.toLowerCase()))
    const add = Object.fromEntries(Object.entries(hints).filter(([k]) => !sent.has(k)))
    cb({ requestHeaders: { ...details.requestHeaders, ...add } })
  })
}

function hardenSession(sess, origin) {
  // Deny everything — EXCEPT going fullscreen. Both handlers gate it:
  // `fullscreen` is asked for through the request handler and re-checked through
  // the sync one, so denying either leaves a player's fullscreen button dead
  // with nothing in the console to say why. Our own pages may, and so may a page
  // in the capture <webview>: a site's player is expected to fill the screen. A scraped page never KEEPS it — Escape ends a
  // guest's fullscreen from the shell, whatever the page does with the key
  // (EXIT_FULLSCREEN_WORLD). A check with no webContents is a cross-origin frame,
  // which only a guest page holds: ours frame nothing foreign.
  const mayFullscreen = (permission, wc, url) =>
    permission === 'fullscreen' &&
    (!wc || wc.getType() === 'webview' || String(url || '').startsWith(origin))
  sess.setPermissionRequestHandler((wc, permission, cb, details) => {
    const ok = mayFullscreen(permission, wc, (details && details.requestingUrl) || wc.getURL())
    if (ok && wc.getType() === 'webview') void makeRoomForFullscreen(wc).then(() => cb(true))
    else cb(ok)
  })
  sess.setPermissionCheckHandler((wc, permission, requestingOrigin) =>
    mayFullscreen(permission, wc, requestingOrigin || (wc && wc.getURL())))
  const appCsp =
    "default-src 'self'; " +
    "script-src 'self'; " +
    "style-src 'self' 'unsafe-inline'; " +
    "img-src 'self' data: blob: https:; " +  // https: so scraped cover images display
    "font-src 'self' data:; " +
    `connect-src 'self' ${origin.replace('http', 'ws')}`
  sess.webRequest.onHeadersReceived((details, cb) => {
    // Only harden OUR app's pages. External sites in the <webview> keep their own
    // headers, or their API calls (e.g. MangaDex → api.mangadex.org) would be blocked;
    // the ad blocker may add a policy a list asks for to theirs.
    if (!details.url.startsWith(origin)) {
      adblock.onHeadersReceived(details, cb)
      return
    }
    cb({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [appCsp] } })
  })
}

// Downloads from source sites go through the ARMED-DOWNLOAD lifecycle: when a
// webview download STARTS, the sidecar consumes the arm and claims the chapter
// binding immediately (parallel downloads are fine — each carries its claim);
// progress streams to the sidecar while the file downloads; on completion the
// temp file is handed over for ingest. Unarmed downloads are rejected at start
// and cancelled. No file ever lands outside the vault.
// The Electron DownloadItem for each claimed download, so a human can stop one.
// Only while it streams: `done` takes it out however it ended.
const liveDownloads = new Map()

// URLs a human asked to save from a page's context menu. A guest download is
// otherwise claimed by the ARMED-CHAPTER lifecycle and ingested into the vault;
// "Save image as…" is not that — it is a plain Save As, and this is how the two
// are told apart at `will-download`, which sees only a URL.
const manualSaves = new Set()

// The page each link a page opened as a tab was on, by the URL the tab opened
// onto. A tab opened straight onto a file commits no page of its own, so this is
// the only record of where that download came from. Few and short-lived: a
// download takes its entry, and the oldest go first.
const openedFrom = new Map()
function noteOpener(url, page) {
  openedFrom.delete(url)
  openedFrom.set(url, page)
  if (openedFrom.size > 32) openedFrom.delete(openedFrom.keys().next().value)
}

// The app window, for the things a GUEST page's handlers need one: a context
// menu has to be popped up over a window, and a <webview>'s webContents is not
// one — `Menu.popup()` with nothing to anchor to takes the whole app down.
let appWindow = null
// Transfers being stopped ON PURPOSE, to be picked up later: their partial file
// is KEPT and their end is reported as interrupted, not as a failure.
const interrupting = new Set()
let dlApi = null // the sidecar's downloads API, once the origin and token exist
// Transfers the app asked to pick up, by their partial file → the record they
// carry on in. A picked-up transfer belongs to no page, so this is how it is
// told from a new one at `will-download`.
const resuming = new Map()

// Where a transfer writes until it is done: the vault it is for, not the system
// temp folder, which may be cleared while one waits to be picked up — and the
// ingest that ends it is then a rename on the same drive, not a copy.
// Made once, when the vault is opened (main).
function partialDir() {
  return partialHome
}
const inPartialDir = (file) => path.resolve(String(file || '')).startsWith(partialDir() + path.sep)

// A partial file nobody will pick up again — its record was forgotten, or a
// crash left it — goes at the next launch. One that arrived after the launch
// belongs to a transfer running now.
async function sweepPartials(origin, token) {
  const since = Date.now()
  let keep
  try {
    const res = await fetch(`${origin}/api/downloads`, { headers: { Authorization: `Bearer ${token}` } })
    keep = new Set((await res.json()).items.filter((i) => i.resume && i.resume.path)
      .map((i) => path.resolve(i.resume.path)))
  } catch { return } // without the list nothing is provably unused
  const dir = partialDir()
  for (const name of await fs.promises.readdir(dir).catch(() => [])) {
    const file = path.join(dir, name)
    const st = await fs.promises.stat(file).catch(() => null)
    if (st && st.isFile() && st.mtimeMs < since && !keep.has(path.resolve(file))) {
      await fs.promises.unlink(file).catch(() => {})
    }
  }
}

// The app polls the sidecar's download list only while it knows something is
// on its way — a download nobody armed would start and land unseen. The shell
// is the one that sees every transfer begin and end, so it says so.
function downloadsChanged() {
  if (appWindow && !appWindow.isDestroyed()) appWindow.webContents.send('lb-downloads-changed')
}

function wireDownloads(sess, origin, token) {
  const api = (p, body) => fetch(`${origin}/api/downloads${p}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  })
  dlApi = api

  sess.on('will-download', (_event, item, webContents) => {
    // asked for by hand from the page's own menu: no save path is set, so
    // Electron asks where to put it, and no arm is consumed
    if (manualSaves.delete(item.getURL())) return
    // A transfer being PICKED UP: it arrives with its partial file and byte
    // offset already set, and belongs to no webview — the app itself asked the
    // session to re-open it.
    const resumed = item.getState() === 'interrupted' && item.canResume()
    if (!resumed && (!webContents || webContents.getType() !== 'webview')) {
      // The app window saves exactly one thing: a copy of what the vault
      // already holds — the player's own download control, the "Save a copy"
      // link on a container we cannot open. Its URL is ours or it is dropped,
      // and no save path is set here, so Electron asks where to put it.
      if (!webContents || !item.getURL().startsWith(origin)) item.cancel()
      return
    }
    if (!resumed) {
      item.setSavePath(path.join(partialDir(), `${Date.now()}-${item.getFilename() || 'chapter.zip'}`))
    }
    const tempFile = item.getSavePath()
    const carried = resumed ? resuming.get(path.resolve(tempFile)) : undefined
    resuming.delete(path.resolve(tempFile))

    // The page it came from — never the file's own address. A tab opened onto
    // the file has none of its own: then it is the page whose link opened it.
    const chain = item.getURLChain()
    const tabUrl = webContents ? webContents.getURL() : ''
    const opener = openedFrom.get(chain[0] || item.getURL())
    openedFrom.delete(chain[0] || item.getURL())
    const pageUrl = opener ?? (/^https?:/.test(tabUrl) && !chain.includes(tabUrl) ? tabUrl : '')

    // Claim the binding NOW — the arm is consumed at download START. A transfer
    // picked up carries on in the record it began in, which claimed it already.
    const request = resumed
      ? api(`/${carried || 'unknown'}/resumed`, {})
      : api('/start', { filename: item.getFilename() || '', fileUrl: item.getURL(), pageUrl })
    const claim = request.then(async (res) => {
      if (!res.ok) {
        // the sidecar did not take it: nowhere to put it, so drop it
        try { item.cancel() } catch { /* already done */ }
        return null
      }
      return (await res.json()).id
    }).catch((err) => {
      console.error('download claim failed:', err)
      try { item.cancel() } catch { /* already done */ }
      return null
    })

    void claim.then((id) => {
      if (id) liveDownloads.set(id, item)
      downloadsChanged() // a refused one too: the sidecar recorded why
    })

    if (resumed) item.resume()

    let lastTick = 0
    item.on('updated', async () => {
      const id = await claim
      if (!id) return
      const now = Date.now()
      if (now - lastTick < 500) return // throttle progress reports
      lastTick = now
      api(`/${id}/progress`, {
        received: item.getReceivedBytes(),
        total: item.getTotalBytes(),
      }).catch(() => {})
    })

    item.once('done', async (_e, state) => {
      const id = await claim
      if (id) liveDownloads.delete(id)
      // stopped on purpose: the partial file STAYS, and the record was already
      // written by whoever stopped it
      if (id && interrupting.delete(id)) return
      // a partial file on a vault that is gone is not asked about: the next
      // launch's sweep takes what no record names
      const drop = () => { if (!vaultLost) fs.promises.unlink(tempFile).catch(() => {}) }
      if (!id) {
        drop()
        return
      }
      try {
        if (state === 'completed') {
          const res = await api(`/${id}/complete`, { path: tempFile })
          if (!res.ok) {
            console.error(`download ingest failed: ${res.status}`)
            drop()
          }
          // on success the sidecar MOVED the file into the vault
        } else {
          await api(`/${id}/failed`, { reason: state })
          drop()
        }
      } catch (err) {
        console.error('download ingest failed:', err)
        drop()
      }
      downloadsChanged()
    })
  })
}

// The window's preferences an older build kept in the browser's localStorage,
// keyed by the page's origin — which held the port the sidecar used then. Read
// out of the profile the one-time move put in the vault, by a hidden page at
// that origin that never reaches the network, and handed to the sidecar in the
// vault (`.longbox/legacy-window.json`), which takes them into settings.json.
// Nothing is cleared: until this computer's old folder is dropped, the next
// launch reads them again.
async function readLegacyPrefs(sess, port) {
  const origin = `http://127.0.0.1:${port}`
  sess.protocol.handle('http', () => new Response('<!doctype html><title>longbox</title>',
    { headers: { 'content-type': 'text/html' } }))
  const page = new BrowserWindow({ show: false, webPreferences: { session: sess, sandbox: true, contextIsolation: true } })
  try {
    await page.loadURL(`${origin}/`)
    return await page.webContents.executeJavaScript(`(() => {
      const out = {}
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i)
        if (k && k.startsWith('lb.')) out[k] = localStorage.getItem(k)
      }
      return out
    })()`)
  } catch (err) {
    console.error('reading the old window preferences failed:', err)
    return null
  } finally {
    page.destroy()
    sess.protocol.unhandle('http')
  }
}

// The way out of a vault, in this order: the library closes (it writes until
// then), the vault's lock goes, the sidecar stops. Stopped without being asked,
// the sidecar leaves its session open and the next launch checks the whole
// vault (design/state-model.md). A vault that is gone is not written to — its
// lock goes stale on its own.
async function stopSidecar() {
  const running = sidecar && sidecar.exitCode === null && !sidecar.killed
  if (!vaultLost) {
    if (running) await closeLibrary()
    letGoOfVault()
  }
  if (running) sidecar.kill()
}

// Start over: the sidecar, the window, the browser profile — into the vault the
// config names, or into the launch window (`--launcher`). Switching vaults and
// a vault coming back after a drop both end here (design/vault-home.md).
async function relaunch(extra = []) {
  app.isQuitting = true
  passwords.lock()
  // what the browser holds in memory reaches its profile before the process goes
  if (vaultSession) {
    await vaultSession.cookies.flushStore().catch(() => {})
    vaultSession.flushStorageData()
  }
  await stopSidecar()
  const own = (a) => a === '--launcher' || a === '--restore' || a.startsWith('--why=')
  app.relaunch({ args: [...process.argv.slice(1).filter((a) => !own(a)), ...extra] })
  app.exit(0)
}

// Whether the vault still answers is the sidecar's to know (app/vault_watch.py);
// the shell asks it, covers the window while the vault is gone, stops the
// transfers that write into it, and restarts into it once it answers again.
function watchVault(origin, win) {
  let known = 'ok'
  const timer = setInterval(async () => {
    let body
    try {
      body = await (await fetch(`${origin}/health`, { signal: AbortSignal.timeout(2500) })).json()
    } catch { return } // the sidecar is busy, or gone (then the app quits on its own)
    const now = body.vault || 'ok'
    if (now === known) return
    known = now
    if (now === 'back') {
      clearInterval(timer)
      void relaunch(['--restore']) // and the window reopens the tabs it had
      return
    }
    if (now === 'lost') {
      vaultLost = true
      passwords.lock()
      for (const item of liveDownloads.values()) { try { item.cancel() } catch { /* done */ } }
    }
    if (!win.isDestroyed()) {
      win.webContents.send('lb-vault', { state: now, since: body.since || '', path: vaultRoot,
        name: vaults.nameOf(vaultRoot), share: await vaults.shareOf(vaultRoot) })
    }
  }, 3000)
}

async function main(root, legacy, prof) {
  vaultRoot = root
  profileDir = prof.dir
  profileId = prof.id
  profileShared = prof.shared
  partialHome = path.join(root, vaults.APP_DIR, 'partial-downloads')
  await fs.promises.mkdir(partialHome, { recursive: true })
  // the embedded browser's profile is where the vault says (in it, or on this
  // computer); the app window's session is in memory — nothing of the window's
  // own is kept in a browser any more
  vaultSession = session.fromPath(profileDir)
  const appSession = session.fromPartition('lb-app')
  if (legacy) {
    const prefs = legacy.oldPort ? await readLegacyPrefs(vaultSession, legacy.oldPort) : null
    if (prefs && Object.keys(prefs).length) {
      await vaults.writeAtomic(path.join(root, vaults.APP_DIR, 'legacy-window.json'), JSON.stringify(prefs, null, 2))
    }
    // everything this computer kept is in the vault now: its old folder goes
    await vaults.dropLegacy(legacy).catch((err) => console.error('dropping the old app data failed:', err))
  }

  const port = await freePort()
  const token = crypto.randomBytes(32).toString('hex')
  const origin = `http://127.0.0.1:${port}`

  sidecar = spawnSidecar(port, token, root)
  await waitForHealth(port, token)
  closeLibrary = async () => {
    try {
      await fetch(`${origin}/api/close`, {
        method: 'POST', headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(15000),
      })
    } catch { /* stopped either way: the next launch checks the whole vault */ }
  }

  const sess = vaultSession
  hardenSession(appSession, origin)
  hardenSession(sess, origin)
  wireDownloads(appSession, origin, token)
  wireDownloads(sess, origin, token)
  void sweepPartials(origin, token)
  // Deliver the secret as an HttpOnly, SameSite=Strict cookie the backend guard requires.
  await appSession.cookies.set({
    url: origin, name: 'lb_auth', value: token,
    httpOnly: true, sameSite: 'strict',
  })

  const win = new BrowserWindow({
    width: 1280, height: 820, minWidth: 960, minHeight: 620,
    backgroundColor: '#0a0b0d',
    // Shown only once it has something to draw. An empty window that appears
    // first and fills in seconds later reads as a broken app, and the renderer
    // boot is not instant however fast the sidecar answers.
    show: false,
    autoHideMenuBar: true,
    title: APP_META.name,
    icon: ICON,
    // FRAMELESS: the app draws its own chrome; the OS contributes only the
    // native minimize/maximize/close overlay (recolored on theme change) and
    // the tab strip doubles as the drag region (CSS -webkit-app-region)
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#0f1115', symbolColor: '#9aa1ad', height: 39 },
    webPreferences: {
      contextIsolation: true, nodeIntegration: false, sandbox: true, webviewTag: true,
      preload: path.join(__dirname, 'app-preload.js'), session: appSession,
    },
  })

  appWindow = win // guest-page handlers pop their menus over this one
  adblock.wire(sess, win.webContents, path.join(root, vaults.APP_DIR, 'adblock'))
  passwords.wire(win, () => vaultRoot)
  presentAsChrome(sess, win.webContents)

  // DevTools on the app window, when debugging is allowed at all (DEVTOOLS): the
  // one class of problem that cannot be diagnosed from outside — playback
  // stalls, a decode fallback, request timing — is visible nowhere else.
  win.webContents.on('before-input-event', (_e, input) => {
    if (!DEVTOOLS || input.type !== 'keyDown') return
    if (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i')) {
      win.webContents.toggleDevTools()
    }
  })

  // A transfer stopped WITH its place kept: the partial file stays on disk and
  // the sidecar records what it takes to carry on — the URL chain, the byte
  // offset and the validators the server will be asked to match.
  async function interrupt(id, item, reason) {
    liveDownloads.delete(id)
    interrupting.add(id)
    const resume = {
      path: item.getSavePath() || '',
      urlChain: item.getURLChain() || [],
      offset: item.getReceivedBytes() || 0,
      total: item.getTotalBytes() || 0,
      eTag: item.getETag() || '',
      lastModified: item.getLastModifiedTime() || '',
    }
    try { item.cancel() } catch { /* already finished */ }
    try { await dlApi?.(`/${id}/interrupted`, { reason, resume }) } catch { /* the sidecar may be gone */ }
  }
  async function interruptAll(reason) {
    const jobs = [...liveDownloads.entries()]
    for (const [id, item] of jobs) await interrupt(id, item, reason)
    return jobs.length
  }

  // the app has made room for a guest page's fullscreen: grant it now
  ipcMain.on('lb-page-fullscreen-ready', (e, token) => {
    if (e.sender !== win.webContents) return
    roomWaits.get(String(token || ''))?.()
  })

  // Pause: one transfer stopped with its place kept, as quitting stops them all.
  ipcMain.handle('lb-pause-download', async (e, id) => {
    if (e.sender !== win.webContents) return false
    const item = liveDownloads.get(String(id || ''))
    if (!item) return false
    await interrupt(String(id), item, 'paused')
    return true
  })

  // A paused transfer forgotten: its partial file goes with it. Only ever a
  // file in the partial folder — the path comes from the renderer.
  ipcMain.handle('lb-forget-partial', async (e, file) => {
    if (e.sender !== win.webContents || !inPartialDir(file)) return false
    await fs.promises.unlink(path.resolve(file)).catch(() => {})
    return true
  })

  // The window asks before it takes unfinished transfers down with it. The app
  // decides what to show; the shell only refuses to close until it is told to.
  let closeApproved = false
  win.on('close', (e) => {
    if (closeApproved || !liveDownloads.size) return
    e.preventDefault()
    win.webContents.send('lb-close-blocked', liveDownloads.size)
  })
  ipcMain.handle('lb-close-now', async (e) => {
    if (e.sender !== win.webContents) return false
    await interruptAll('the app was closed')
    closeApproved = true
    win.close()
    return true
  })

  // Pick a stopped transfer up where it left off. Electron re-opens the same
  // file at the same offset; the server decides whether it still honours it —
  // an expired cookie or a rotated CDN link answers from scratch, and then the
  // app offers to start over instead.
  ipcMain.handle('lb-resume-download', (e, rec) => {
    if (e.sender !== win.webContents) return false
    const r = (rec && rec.resume) || {}
    if (!rec || !rec.id || !r.path || !Array.isArray(r.urlChain) || !r.urlChain.length) return false
    resuming.set(path.resolve(r.path), String(rec.id))
    try {
      sess.createInterruptedDownload({
        path: r.path,
        urlChain: r.urlChain,
        offset: Math.max(0, Number(r.offset) || 0),
        length: Math.max(0, Number(r.total) || 0),
        eTag: r.eTag || undefined,
        lastModified: r.lastModified || undefined,
      })
      return true
    } catch (err) {
      resuming.delete(path.resolve(r.path))
      console.error('resume failed:', err)
      return false
    }
  })

  // Stop a download in flight. The item's `done` handler then reports it to the
  // sidecar as interrupted and drops the half-written temp file, exactly as it
  // does for a download that dies on its own.
  ipcMain.handle('lb-cancel-download', (e, id) => {
    if (e.sender !== win.webContents) return false
    const item = liveDownloads.get(String(id || ''))
    if (!item) return false
    try { item.cancel() } catch { /* already finished */ }
    return true
  })

  // The vaults (Settings → Storage, the lost-vault cover). Opening another one
  // is a restart into it; every check of the launch applies to it there.
  ipcMain.handle('lb-vault-pick', async (e, mode) => {
    if (e.sender !== win.webContents) return null
    const res = await dialog.showOpenDialog(win, {
      title: mode === 'create' ? 'Choose an empty folder for the new vault' : 'Choose a vault',
      properties: ['openDirectory', 'createDirectory'],
    })
    if (res.canceled || !res.filePaths.length) return { ok: false, reason: '' }
    const picked = res.filePaths[0]
    const got = await vaults.inspectFolder(picked)
    if (mode === 'create') {
      if (got.state !== 'empty') {
        const why = { vault: 'is a vault already — add it instead',
          files: `holds ${got.files} other item(s) — a vault needs a folder of its own`,
          missing: 'is not there', unreachable: 'does not answer' }
        return { ok: false, reason: `${picked} ${why[got.state] || 'cannot hold a vault'}` }
      }
      try { await vaults.createVault(picked) } catch (err) { return { ok: false, reason: err.message } }
    } else if (got.state !== 'vault') {
      return { ok: false, reason: `${picked} ${got.state === 'unreachable' ? 'does not answer' : 'is not a vault'}` }
    }
    vaults.register(picked)
    return { ok: true, path: picked }
  })
  // a restart cuts transfers off: they are put down with their place kept, in
  // the vault being left, to be picked up when it is open again
  ipcMain.handle('lb-vault-switch', async (e, root) => {
    if (e.sender !== win.webContents || !vaults.known().libraries.some((p) => vaults.same(p, String(root || '')))) return false
    await interruptAll('the app switched to another vault')
    vaults.register(String(root), { last: true })
    void relaunch()
    return true
  })
  ipcMain.handle('lb-vault-launcher', async (e) => {
    if (e.sender !== win.webContents) return false
    if (!vaultLost) await interruptAll('the app switched to another vault')
    void relaunch(['--launcher'])
    return true
  })
  ipcMain.handle('lb-quit', (e) => {
    if (e.sender === win.webContents) app.quit()
  })

  // Native folder picker (an import's folders). Only the app window may ask.
  ipcMain.handle('lb-pick-folder', async (e, title) => {
    if (e.sender !== win.webContents) return null
    const res = await dialog.showOpenDialog(win, {
      title: title || 'Choose a library folder',
      properties: ['openDirectory', 'createDirectory'],
    })
    return res.canceled || !res.filePaths.length ? null : res.filePaths[0]
  })

  // Show a title's folder in the system's file manager. A FOLDER only: opening
  // a file this way would run it, and the path comes from the renderer.
  ipcMain.handle('lb-open-folder', async (e, dir) => {
    if (e.sender !== win.webContents) return false
    const target = String(dir || '')
    try {
      if (!path.isAbsolute(target) || !fs.statSync(target).isDirectory()) return false
    } catch { return false }
    return (await shell.openPath(target)) === ''
  })

  // Page capture: fetch ONE image with the browser session's cookies and the
  // reader page as Referer. This runs in the main process on purpose — a fetch
  // from inside the page is bound by the site's CORS, which is exactly what
  // blocks reading a CDN image the page itself displays fine. Hotlink checks
  // pass because the request carries the same cookies and referer as the page.
  const MAX_IMAGE = 24 * 1024 * 1024
  // Content type is not trustworthy (CDNs love application/octet-stream), so
  // the bytes decide: JPEG / PNG / GIF / WEBP / BMP / AVIF magic.
  function sniffImage(buf) {
    if (buf.length < 12) return ''
    if (buf[0] === 0xff && buf[1] === 0xd8) return 'image/jpeg'
    if (buf.slice(0, 8).toString('hex') === '89504e470d0a1a0a') return 'image/png'
    if (buf.slice(0, 3).toString('latin1') === 'GIF') return 'image/gif'
    if (buf.slice(0, 4).toString('latin1') === 'RIFF' && buf.slice(8, 12).toString('latin1') === 'WEBP') return 'image/webp'
    if (buf.slice(0, 2).toString('latin1') === 'BM') return 'image/bmp'
    if (buf.slice(4, 8).toString('latin1') === 'ftyp' && /avif|heic/i.test(buf.slice(8, 12).toString('latin1'))) return 'image/avif'
    return ''
  }
  // The request the page's own <img> makes: from the page's origin (so it is as
  // cross-site as the page's), asking for what Chrome asks for an image, with the
  // Referer Chrome's default policy sends — the whole address to the page's own
  // origin, the origin alone elsewhere, nothing from https down to http. Chromium
  // refuses (ERR_BLOCKED_BY_CLIENT) a Referer the main process sets any other
  // way, as it does pre-set Sec-Fetch-* — those it fills in itself.
  const IMAGE_ACCEPT = 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
  function asThePageAsks(url, page) {
    let from, to
    try { from = new URL(page); to = new URL(url) } catch { return { options: {}, referer: '' } }
    if (!/^https?:$/.test(from.protocol)) return { options: {}, referer: '' }
    const referer = from.protocol === 'https:' && to.protocol === 'http:' ? ''
      : from.origin === to.origin ? from.href.split('#')[0] : `${from.origin}/`
    return { options: { origin: from.origin, referrerPolicy: 'unsafe-url' }, referer }
  }
  // ONE attempt. Every one carries the session's own user agent, languages and
  // Client Hints — the browser's, never a set of its own.
  const IMAGE_TIMEOUT = 30000
  function fetchImageOnce(url, page, withReferer) {
    return new Promise((resolve) => {
      const { options, referer } = asThePageAsks(url, page)
      let request
      try {
        // redirects come back to fetchImage, which checks where they lead first
        request = electronNet.request({ url, session: sess, useSessionCookies: true, redirect: 'manual', ...options })
      } catch (err) {
        resolve({ error: `bad request: ${err.message}` })
        return
      }
      // A hung connection must NOT hang the capture loop: every attempt is
      // bounded, and an oversized body is aborted instead of drained.
      let settled = false
      const finish = (result) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        try { request.abort() } catch { /* already done */ }
        resolve(result)
      }
      const timer = setTimeout(() => finish({ error: 'timed out' }), IMAGE_TIMEOUT)
      request.setHeader('Accept', IMAGE_ACCEPT)
      if (withReferer && referer) request.setHeader('Referer', referer)
      request.on('redirect', (_status, _method, to) => {
        let next = ''
        try { next = new URL(to, url).href } catch { /* no usable address */ }
        finish(next ? { redirect: next } : { error: 'bad redirect' })
      })
      request.on('response', (res) => {
        if (res.statusCode >= 400) {
          res.resume()
          finish({ error: `http ${res.statusCode}` })
          return
        }
        const chunks = []
        let total = 0
        res.on('data', (c) => {
          total += c.length
          if (total > MAX_IMAGE) { finish({ error: 'image too large' }); return }
          chunks.push(c)
        })
        res.on('end', () => {
          if (!chunks.length) { finish({ error: 'empty response' }); return }
          const buf = Buffer.concat(chunks)
          const ct = String(res.headers['content-type'] || '').split(';')[0].trim().toLowerCase()
          const kind = sniffImage(buf) || (ct.startsWith('image/') ? ct : '')
          if (!kind) { finish({ error: `not an image (${ct || 'no content-type'})` }); return }
          finish({ data: buf.toString('base64'), contentType: kind })
        })
        res.on('error', (err) => finish({ error: `stream: ${err.message}` }))
      })
      request.on('error', (err) => finish({ error: err.message }))
      request.end()
    })
  }

  // An image, following its redirects by hand: each address is checked before it
  // is asked (netguard.js) — a page may name an image on this computer or its
  // network, which the page itself could not reach. A page served from such an
  // address (a source on the same network) may show images from it.
  const IMAGE_REDIRECTS = 5
  async function fetchImage(url, page, withReferer, localPage) {
    for (let hop = 0; hop <= IMAGE_REDIRECTS; hop++) {
      if (!localPage && !(await netguard.isPublicUrl(url))) return { error: 'not on the public internet' }
      const got = await fetchImageOnce(url, page, withReferer)
      if (!got.redirect) return got
      url = got.redirect
    }
    return { error: 'too many redirects' }
  }
  ipcMain.handle('lb-fetch-image', async (e, req) => {
    if (e.sender !== win.webContents) return { error: 'denied' }
    const url = String(req?.url || '')
    if (!/^https?:\/\//i.test(url)) return { error: 'not an http url' }
    const page = req?.referer ? String(req.referer) : ''
    const localPage = !!page && !(await netguard.isPublicUrl(page))
    const first = await fetchImage(url, page, true, localPage)
    // A refusal is tried once more as a page whose policy sends no Referer would
    // ask (one site's hotlink check is another's reason to refuse); a definite
    // answer from the server is not worth repeating.
    const refused = !/^http (4|5)\d\d$/.test(first.error || '') || /^http 40[13]$/.test(first.error)
    if (first.data || !refused || !asThePageAsks(url, page).referer) return first
    return fetchImage(url, page, false, localPage)
  })

  // Native window-controls overlay recolor (theme switch)
  ipcMain.on('lb-titlebar', (e, opts) => {
    if (e.sender !== win.webContents) return
    try {
      win.setTitleBarOverlay({ color: opts.color, symbolColor: opts.symbolColor, height: 39 })
    } catch { /* not supported on this platform */ }
  })

  // Embedded-browser hygiene (Settings → Browser), in this vault's browser:
  // 'cookies' signs out of every site; 'cache' drops the HTTP cache. The app's
  // own auth cookie lives in the window's session and is not touched.
  ipcMain.handle('lb-clear-browsing', async (e, what) => {
    if (e.sender !== win.webContents) return false
    if (what === 'cache') {
      await sess.clearCache()
      return true
    }
    if (what === 'cookies') {
      await sess.clearStorageData({ storages: ['cookies'] })
      return true
    }
    return false
  })

  // One site's share of the vault's browser (Sources → a site → Browser data):
  // its cookies counted, and cleared alone or with the site's storage and its
  // cached pages. A site is its domain and every subdomain: the cookies say
  // which hosts it has used, and storage and cache are cleared for each of them.
  const siteHost = (d) => String(d || '').trim().toLowerCase().replace(/^\.+/, '')
  ipcMain.handle('lb-site-data', async (e, domain) => {
    if (e.sender !== win.webContents) return null
    const host = siteHost(domain)
    if (!host) return null
    return { cookies: (await sess.cookies.get({ domain: host })).length }
  })
  ipcMain.handle('lb-site-data-clear', async (e, req) => {
    if (e.sender !== win.webContents) return false
    const host = siteHost(req?.domain)
    if (!host) return false
    const cookies = await sess.cookies.get({ domain: host })
    if (req.cookies) {
      await Promise.all(cookies.map((c) => sess.cookies
        .remove(`${c.secure ? 'https' : 'http'}://${siteHost(c.domain)}${c.path || '/'}`, c.name)
        .catch(() => { /* already gone */ })))
    }
    const dataTypes = [
      ...(req.storage ? ['localStorage', 'indexedDB', 'serviceWorkers', 'fileSystems', 'webSQL'] : []),
      ...(req.cache ? ['cache'] : []),
    ]
    if (dataTypes.length) {
      const hosts = new Set([host, `www.${host}`, ...cookies.map((c) => siteHost(c.domain))])
      await sess.clearData({ dataTypes, origins: [...hosts].flatMap((h) => [`https://${h}`, `http://${h}`]) })
    }
    return true
  })

  // Where this vault's sign-ins live (design/sign-ins.md): Settings → Browser
  // shows it and changes it. A change is this computer's own until the next
  // start makes it — the profile moved, before the browser opens it — so the
  // vault's answer never names a profile that is not in place.
  const here = () => (path.resolve(profileDir) === path.resolve(profile.vaultDir(vaultRoot)) ? 'vault' : 'computer')
  ipcMain.handle('lb-signins', async (e) => {
    if (e.sender !== win.webContents) return null
    const { writable } = await profile.readChoice(vaultRoot)
    const owe = profileShared ? null : (await profile.readMarker(app.getPath('userData'), profileId))?.owe ?? null
    return { here: here(), owe: owe && owe !== here() ? owe : null, writable, shared: profileShared }
  })
  // measured only when asked: the walk reads every file of the profile, the
  // cache's included, and a vault's may be on a network drive
  ipcMain.handle('lb-signins-sizes', async (e) => {
    if (e.sender !== win.webContents) return null
    return vaults.within(15000, profile.sizes(profileDir), profileDir).catch(() => null)
  })
  ipcMain.handle('lb-signins-set', async (e, req) => {
    if (e.sender !== win.webContents || (req?.keep !== 'vault' && req?.keep !== 'computer')) return false
    // a profile another folder may use too is not this vault's alone to move, nor its marker to write
    const choice = await profile.readChoice(vaultRoot)
    if (!choice.writable || profileShared) return false
    const home = app.getPath('userData')
    // taken back, a change owed is no more; the next start clears what is left of it
    if (req.keep === here()) {
      if (here() === 'computer') await profile.writeMarker(home, profileId, { path: vaultRoot })
      else await profile.dropMarker(home, profileId)
      return true
    }
    await profile.writeMarker(home, profileId, { path: vaultRoot, owe: req.keep, against: choice.rev })
    if (req.restart) {
      await interruptAll('the app restarted to move its sign-ins')
      void relaunch()
    }
    return true
  })
  // forgetting a vault (Settings → Storage) takes this computer's sign-ins for
  // it too — never the profile in use, whichever path its marker names
  ipcMain.handle('lb-signins-forget', async (e, root) => {
    if (e.sender !== win.webContents || typeof root !== 'string' || !root || vaults.same(root, vaultRoot)) return 0
    return profile.forget(app.getPath('userData'), root, profileDir)
  })

  // Knowing a release is out (design/update-check.md): one request to GitHub,
  // made only when the window asks — whether and how often is the user's answer.
  // Its pages open in the system's browser, never the embedded one: the window
  // names a version (or 'latest'), and the address is made here.
  ipcMain.handle('lb-updates-check', (e) => {
    if (e.sender !== win.webContents) return { ok: false, error: 'refused' }
    return updates.check((url, opts) => electronNet.fetch(url, opts), APP_META.version)
  })
  ipcMain.handle('lb-updates-open', async (e, which) => {
    const url = updates.releasePage(which)
    if (e.sender !== win.webContents || !url) return false
    await shell.openExternal(url)
    return true
  })

  // Navigation lockdown: keep the window on the app origin; open nothing externally.
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(origin)) e.preventDefault()
  })
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  // the OS window carries the app name ONCE — page titles never override it
  win.webContents.on('page-title-updated', (e) => e.preventDefault())

  win.once('ready-to-show', async () => {
    await launcher.close() // after a notice on it has been read
    win.show()
    windowShown = true
    void adblock.load() // a stale list is fetched and read here, not before the window is up
  })
  watchVault(origin, win)
  // `restore`: the app came back into a vault that had dropped — the window
  // reopens the tabs it had
  win.loadURL(`${origin}/app/${process.argv.includes('--restore') ? '?restore=1' : ''}`)
}

async function boot() {
  Menu.setApplicationMenu(null) // no native File/Edit/View menu bar
  const opts = { uiDir: UI_DIR, icon: ICON }
  const why = process.argv.find((a) => a.startsWith('--why='))
  const { root, letGo } = await launcher.choose({ ...opts, forced: process.argv.includes('--launcher'),
    why: why ? decodeURIComponent(why.slice(6)) : '' })
  letGoOfVault = letGo
  try {
    // where the sign-ins live is settled first: what an older build kept here goes there
    const prof = await launcher.signins(root, opts)
    const legacy = await vaults.legacyPlan(root, prof.dir)
    if (legacy) await launcher.moving(legacy, opts)
    await main(root, legacy, prof)
  } catch (err) {
    // the vault could not be opened after all: back to the launch window, which
    // says why — never a quiet exit the next launch would repeat
    console.error(err)
    if (!windowShown) {
      void relaunch(['--launcher', `--why=${encodeURIComponent(`The library could not be opened: ${err.message || err}`)}`])
      return
    }
    throw err
  }
}

// One app per computer: a second launch hands over to the one running — and so
// a vault's lock from this computer can only be one a crash left behind.
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    const w = appWindow && !appWindow.isDestroyed() ? appWindow : launcher.window()
    if (!w) return
    if (w.isMinimized()) w.restore()
    w.focus()
  })
  app.whenReady().then(boot).catch((err) => {
    console.error(err)
    dialog.showErrorBox('longbox could not start', String((err && err.message) || err))
    app.quit()
  })
}

// The Browse view embeds source sites in a <webview>. Force our pick-mode
// preload from the main process (don't trust a renderer-set attribute), and keep
// the guest sandboxed.
// What a guest page would otherwise swallow — the keys the app binds over a
// page. Everything else belongs to the site.
const GUEST_KEYS = new Set([
  'F5', 'Escape', 'KeyF', 'KeyR', 'KeyL', 'KeyT', 'KeyW', 'ArrowLeft', 'ArrowRight',
  'Minus', 'Equal', 'Digit0', 'Digit1', 'Digit2', 'Digit3', 'Digit4',
  'Digit5', 'Digit6', 'Digit7', 'Digit8', 'Digit9',
])
// Where Escape ends a guest's fullscreen: a world of its own, out of reach of
// the page's scripts — a page that replaced exitFullscreen would keep the screen.
const EXIT_FULLSCREEN_WORLD = 1001
// How long a gesture lets a page open a tab or a window: Chromium's
// transient-activation lifespan.
const GESTURE_MS = 5000

app.on('web-contents-created', (_e, contents) => {
  contents.on('will-attach-webview', (_evt, webPreferences) => {
    // the pages run in the vault's browser profile: its sign-ins, its cache
    webPreferences.session = vaultSession
    webPreferences.preload = path.join(__dirname, 'pick-preload.js')
    // in every frame of the page: a gesture inside a frame (a sign-in button is
    // often the provider's own) has to reach the popup blocker below
    webPreferences.nodeIntegrationInSubFrames = true
    webPreferences.nodeIntegration = false
    webPreferences.contextIsolation = true
  })
  if (contents.getType() === 'webview') {
    // Every link this page opens as a tab goes through here, so a download the
    // new tab lands on straight away still knows the page it came from.
    const openTab = (url, background) => {
      noteOpener(url, contents.getURL())
      contents.send('open-url-as-tab', { url, background })
    }
    // Ctrl+click / middle-click, taken by the preload before the page sees it
    contents.ipc.on('link-tab', (_e, url) => {
      if (typeof url === 'string' && /^https?:\/\//.test(url)) openTab(url, true)
    })
    // A key pressed while the PAGE has focus is delivered to the guest and stops
    // there — the app's document never sees it. So the shortcuts a browser is
    // expected to have would work everywhere except over the page itself, which
    // is where they are wanted. Caught here, named, and handed to the window
    // that owns the toolbar. Matched on the PHYSICAL key: on a Ukrainian layout
    // `key` is 'ф' while the key under the finger is still the browser's F.
    let fullscreen = false
    contents.on('enter-html-full-screen', () => { fullscreen = true })
    contents.on('leave-html-full-screen', () => { fullscreen = false })
    contents.on('before-input-event', (evt, input) => {
      // in fullscreen Escape is the browser's alone, as in any browser
      if (fullscreen && input.type === 'keyDown' && input.code === 'Escape') {
        evt.preventDefault()
        contents.executeJavaScriptInIsolatedWorld(EXIT_FULLSCREEN_WORLD,
          [{ code: 'document.exitFullscreen().catch(() => {})' }]).catch(() => {})
        return
      }
      if (input.type !== 'keyDown' || !GUEST_KEYS.has(input.code)) return
      const mod = input.control || input.meta || input.alt
      // F5 and Escape stand alone; everything else is a chord
      if (!mod && input.code !== 'F5' && input.code !== 'Escape') return
      // Escape is SHARED, not taken: the app calls off a pick with it and the
      // page may be closing its own dialog. The rest are the browser's, and are
      // swallowed so a site cannot bind over them.
      if (input.code !== 'Escape') evt.preventDefault()
      // The shell forwards the physical key; what it MEANS is decided in one
      // place in the app, so the two paths into it can never drift apart.
      const chord = { code: input.code, ctrl: !!(input.control || input.meta),
                      shift: !!input.shift, alt: !!input.alt }
      for (const w of BrowserWindow.getAllWindows()) w.webContents.send('lb-page-key', chord)
    })
    // A page with no context menu is a page you cannot copy a link out of. The
    // items are the ones a browser has and this app actually needs: links and
    // images (a cover is one right-click away instead of a taught selector),
    // the selection, and the page itself.
    //
    // Wrapped, and anchored to the app window: this runs for every right-click
    // on a page nobody controls, and `Menu.popup()` with nothing to anchor to —
    // a <webview>'s webContents is not a window — is a FATAL check in Electron,
    // i.e. the whole app goes down.
    contents.on('context-menu', (_evt, params) => {
      try {
        if (!appWindow || appWindow.isDestroyed()) return
        const items = []
        if (params.linkURL) {
          items.push(
            { label: 'Open link in new tab', click: () => openTab(params.linkURL, false) },
            { label: 'Open link in background tab', click: () => openTab(params.linkURL, true) },
            { label: 'Copy link address', click: () => clipboard.writeText(params.linkURL) },
          )
        }
        if (params.mediaType === 'image' && params.srcURL) {
          if (items.length) items.push({ type: 'separator' })
          items.push(
            { label: 'Copy image address', click: () => clipboard.writeText(params.srcURL) },
            {
              label: 'Save image as…',
              click: () => { manualSaves.add(params.srcURL); contents.downloadURL(params.srcURL) },
            },
          )
        }
        if (params.selectionText) {
          if (items.length) items.push({ type: 'separator' })
          items.push({ label: 'Copy', click: () => clipboard.writeText(params.selectionText) })
        }
        if (params.isEditable) {
          if (items.length) items.push({ type: 'separator' })
          items.push({ label: 'Paste', click: () => contents.paste() })
        }
        if (items.length) items.push({ type: 'separator' })
        // `navigationHistory` is the current API; the flat calls are deprecated
        const nav = contents.navigationHistory
        items.push(
          { label: 'Back', enabled: nav.canGoBack(), click: () => nav.goBack() },
          { label: 'Forward', enabled: nav.canGoForward(), click: () => nav.goForward() },
          { label: 'Reload', click: () => contents.reload() },
          { type: 'separator' },
          { label: 'Copy page address', click: () => clipboard.writeText(contents.getURL()) },
        )
        Menu.buildFromTemplate(items).popup({ window: appWindow })
      } catch (err) {
        console.error('context menu failed:', err)
      }
    })

    // Electron has no popup blocker, so this is one, as Chromium's is: a page
    // opens a tab or a window only off a fresh gesture of the user's, and one per
    // gesture — the ad script that opens windows on load, or five per click, gets
    // none / one. The preload reports the trusted gestures of every frame.
    let gestureAt = 0
    contents.ipc.on('lb-gesture', () => { gestureAt = Date.now() })
    // A page's target=_blank link or window.open (the <webview> carries
    // `allowpopups`, or Chromium drops the request before it gets here) opens as
    // an in-app tab; the disposition rides along — a 'background-tab' keeps the
    // current page fronted. A POPUP (window.open with a size: a sign-in through
    // Google, Apple, Facebook) is the one OS window a page gets, over the app: it
    // answers the page through window.opener, which a tab would not have.
    contents.setWindowOpenHandler(({ url, disposition }) => {
      if (Date.now() - gestureAt > GESTURE_MS) return { action: 'deny' }
      gestureAt = 0
      if (disposition === 'new-window' && (/^https?:\/\//.test(url) || url === 'about:blank')) {
        return { action: 'allow', overrideBrowserWindowOptions: { parent: appWindow, autoHideMenuBar: true } }
      }
      if (/^https?:\/\//.test(url)) openTab(url, disposition === 'background-tab')
      return { action: 'deny' }
    })
    // the popup itself opens nothing: a sign-in needs no third window
    contents.on('did-create-window', (popup) => {
      popup.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
    })
  }
})

// A quit waits for the library to close — out of sight: the windows go at once.
let stopping = false
let stopped = false
app.on('before-quit', (e) => {
  app.isQuitting = true
  passwords.lock()
  if (stopped) return
  e.preventDefault()
  if (stopping) return
  stopping = true
  for (const w of BrowserWindow.getAllWindows()) w.hide()
  void stopSidecar().finally(() => {
    stopped = true
    app.quit()
  })
})

app.on('window-all-closed', () => app.quit())
