'use strict'
// Electron main process. Spawns the FastAPI sidecar on a free loopback port,
// guards it with a per-launch secret (delivered as an HttpOnly SameSite=Strict
// cookie), confirms it reached its own sidecar via the /health identity echo,
// then loads the UI the sidecar serves at /app/. Locks down navigation,
// window-open, and permissions.

const { app, BrowserWindow, Menu, clipboard, dialog, ipcMain, net: electronNet, session } =
  require('electron')
const { spawn } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const net = require('node:net')
const path = require('node:path')

const ROOT = path.join(__dirname, '..')
const IS_WIN = process.platform === 'win32'
// A packaged build reads everything that is not code from its resources
// directory (the app itself lives inside an asar); a source checkout reads the
// repo it sits in.
const ASSETS = app.isPackaged ? process.resourcesPath : ROOT

// ONE source of app identity (window title, taskbar) — see <repo>/app-meta.json
let APP_META = { name: 'longbox', version: '0.0.0' }
try { APP_META = { ...APP_META, ...JSON.parse(fs.readFileSync(path.join(ASSETS, 'app-meta.json'), 'utf-8')) } } catch { /* defaults */ }

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

function isPortFree(port) {
  return new Promise((resolve) => {
    const srv = net.createServer()
    srv.unref()
    srv.on('error', () => resolve(false))
    srv.listen(port, '127.0.0.1', () => srv.close(() => resolve(true)))
  })
}

// A STABLE port across launches: the renderer's localStorage (theme, key
// bindings, reader prefs, session restore) is keyed by origin — a random port
// each run would wipe it all. The chosen port persists in the shell's own
// config; a squatted port falls back to a fresh one (and re-persists).
function shellCfgPath() {
  return path.join(app.getPath('userData'), 'shell.json')
}
function readShellCfg() {
  try { return JSON.parse(fs.readFileSync(shellCfgPath(), 'utf-8')) } catch { return {} }
}
async function stablePort() {
  const cfg = readShellCfg()
  if (Number.isInteger(cfg.port) && cfg.port > 1024 && (await isPortFree(cfg.port))) return cfg.port
  const port = await freePort()
  try { fs.writeFileSync(shellCfgPath(), JSON.stringify({ ...cfg, port }, null, 2)) } catch { /* non-fatal */ }
  return port
}

function sha256(s) {
  return crypto.createHash('sha256').update(s).digest('hex')
}

function spawnSidecar(port, token) {
  const opts = {
    env: {
      ...process.env,
      LONGBOX_AUTH_TOKEN: token,
      LONGBOX_PORT: String(port),
      // Config (incl. the chosen library path) lives in this stable per-app dir, so
      // the path the user picks in Settings survives restarts. The library path
      // itself is NOT forced here — it comes from that config, defaulting to
      // <configDir>/library.
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
  child.stderr.on('data', (d) => process.stderr.write(`[sidecar] ${d}`))
  child.on('exit', (code) => {
    if (!app.isQuitting) {
      console.error(`sidecar exited (${code}); quitting`)
      app.quit()
    }
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

function hardenSession(sess, origin) {
  // Deny everything — EXCEPT our own pages going fullscreen. Both handlers gate
  // it: `fullscreen` is asked for through the request handler and re-checked
  // through the sync one, so denying either leaves the player's fullscreen
  // button dead with nothing in the console to say why. The origin test is what
  // keeps it ours: a site in the capture <webview> shares this session, and a
  // scraped page must never be able to take over the screen.
  const mayFullscreen = (permission, url) =>
    permission === 'fullscreen' && String(url || '').startsWith(origin)
  sess.setPermissionRequestHandler((wc, permission, cb, details) =>
    cb(mayFullscreen(permission, (details && details.requestingUrl) || wc.getURL())))
  sess.setPermissionCheckHandler((wc, permission, requestingOrigin) =>
    mayFullscreen(permission, requestingOrigin || (wc && wc.getURL())))
  const appCsp =
    "default-src 'self'; " +
    "script-src 'self'; " +
    "style-src 'self' 'unsafe-inline'; " +
    "img-src 'self' data: blob: https:; " +  // https: so scraped cover images display
    "font-src 'self' data:; " +
    `connect-src 'self' ${origin.replace('http', 'ws')}`
  sess.webRequest.onHeadersReceived((details, cb) => {
    // Only harden OUR app's pages. External sites in the <webview> keep their own
    // headers, or their API calls (e.g. MangaDex → api.mangadex.org) would be blocked.
    if (!details.url.startsWith(origin)) {
      cb({})
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

// The app window, for the things a GUEST page's handlers need one: a context
// menu has to be popped up over a window, and a <webview>'s webContents is not
// one — `Menu.popup()` with nothing to anchor to takes the whole app down.
let appWindow = null
// Transfers being stopped ON PURPOSE, to be picked up later: their partial file
// is KEPT and their end is reported as interrupted, not as a failure.
const interrupting = new Set()
let dlApi = null // the sidecar's downloads API, once the origin and token exist

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
      item.setSavePath(path.join(
        app.getPath('temp'),
        `longbox-dl-${Date.now()}-${item.getFilename() || 'chapter.zip'}`,
      ))
    }
    const tempFile = item.getSavePath()

    // claim the binding NOW — the arm is consumed at download START
    const claim = api('/start', {
      filename: item.getFilename() || '',
      fileUrl: item.getURL(),
      pageUrl: webContents ? webContents.getURL() : '',
    }).then(async (res) => {
      if (!res.ok) {
        // 409 = nothing armed — this download was not asked for; drop it
        try { item.cancel() } catch { /* already done */ }
        return null
      }
      return (await res.json()).id
    }).catch((err) => {
      console.error('download claim failed:', err)
      try { item.cancel() } catch { /* already done */ }
      return null
    })

    void claim.then((id) => { if (id) liveDownloads.set(id, item) })

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
      if (!id) {
        fs.promises.unlink(tempFile).catch(() => {})
        return
      }
      try {
        if (state === 'completed') {
          const res = await api(`/${id}/complete`, { path: tempFile })
          if (!res.ok) {
            console.error(`download ingest failed: ${res.status}`)
            fs.promises.unlink(tempFile).catch(() => {})
          }
          // on success the sidecar MOVED the file into the vault
        } else {
          await api(`/${id}/failed`, { reason: state })
          fs.promises.unlink(tempFile).catch(() => {})
        }
      } catch (err) {
        console.error('download ingest failed:', err)
        fs.promises.unlink(tempFile).catch(() => {})
      }
    })
  })
}

async function main() {
  const port = await stablePort()
  const token = crypto.randomBytes(32).toString('hex')
  const origin = `http://127.0.0.1:${port}`

  sidecar = spawnSidecar(port, token)
  await waitForHealth(port, token)

  const sess = session.defaultSession
  hardenSession(sess, origin)
  wireDownloads(sess, origin, token)
  // Deliver the secret as an HttpOnly, SameSite=Strict cookie the backend guard requires.
  await sess.cookies.set({
    url: origin, name: 'lb_auth', value: token,
    httpOnly: true, sameSite: 'strict',
  })

  Menu.setApplicationMenu(null) // no native File/Edit/View menu bar

  const win = new BrowserWindow({
    width: 1280, height: 820, minWidth: 960, minHeight: 620,
    backgroundColor: '#0a0b0d',
    // Shown only once it has something to draw. An empty window that appears
    // first and fills in seconds later reads as a broken app, and the renderer
    // boot is not instant however fast the sidecar answers.
    show: false,
    autoHideMenuBar: true,
    title: APP_META.name,
    icon: path.join(__dirname, IS_WIN ? 'icon.ico' : 'icon.png'),
    // FRAMELESS: the app draws its own chrome; the OS contributes only the
    // native minimize/maximize/close overlay (recolored on theme change) and
    // the tab strip doubles as the drag region (CSS -webkit-app-region)
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: '#0f1115', symbolColor: '#9aa1ad', height: 39 },
    webPreferences: {
      contextIsolation: true, nodeIntegration: false, sandbox: true, webviewTag: true,
      preload: path.join(__dirname, 'app-preload.js'),
    },
  })

  appWindow = win // guest-page handlers pop their menus over this one

  // DevTools, on the app window only. The one class of problem that cannot be
  // diagnosed from outside — playback stalls, a decode fallback, request timing
  // — is visible nowhere else, and this build only ever runs on the owner's
  // machine.
  win.webContents.on('before-input-event', (_e, input) => {
    if (input.type !== 'keyDown') return
    if (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i')) {
      win.webContents.toggleDevTools()
    }
  })

  // Everything still streaming, stopped WITH its place kept: the partial file
  // stays on disk and the sidecar records what it takes to carry on — the URL
  // chain, the byte offset and the validators the server will be asked to match.
  async function interruptAll(reason) {
    const jobs = [...liveDownloads.entries()]
    liveDownloads.clear()
    for (const [id, item] of jobs) {
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
    return jobs.length
  }

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
    if (!r.path || !Array.isArray(r.urlChain) || !r.urlChain.length) return false
    try {
      session.defaultSession.createInterruptedDownload({
        path: r.path,
        urlChain: r.urlChain,
        offset: Math.max(0, Number(r.offset) || 0),
        length: Math.max(0, Number(r.total) || 0),
        eTag: r.eTag || undefined,
        lastModified: r.lastModified || undefined,
      })
      return true
    } catch (err) {
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

  // Native folder picker (Settings → Storage). Only the app window may ask.
  ipcMain.handle('lb-pick-folder', async (e, title) => {
    if (e.sender !== win.webContents) return null
    const res = await dialog.showOpenDialog(win, {
      title: title || 'Choose a library folder',
      properties: ['openDirectory', 'createDirectory'],
    })
    return res.canceled || !res.filePaths.length ? null : res.filePaths[0]
  })

  // Page capture: fetch ONE image with the browser session's cookies and the
  // reader page as Referer. This runs in the main process on purpose — a fetch
  // from inside the page is bound by the site's CORS, which is exactly what
  // blocks reading a CDN image the page itself displays fine. Hotlink checks
  // pass because the request carries the same cookies and referer as the page.
  const MAX_IMAGE = 24 * 1024 * 1024
  // The UA the embedded browser itself sends, minus the tokens that give the
  // app away — a CDN that sniffs for "Electron" would refuse the request the
  // page next to it is allowed to make.
  const browserUA = () =>
    win.webContents.getUserAgent().replace(/\s*(Electron|longbox[\w-]*)\/[\d.]+/gi, '').replace(/\s{2,}/g, ' ')
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
  // ONE attempt with a given header set. Sec-Fetch-* are deliberately absent:
  // Chromium owns those, and a request that arrives with them pre-set from the
  // main process is refused outright (ERR_BLOCKED_BY_CLIENT).
  const IMAGE_TIMEOUT = 30000
  function fetchImageOnce(url, headers) {
    return new Promise((resolve) => {
      let request
      try {
        request = electronNet.request({ url, session: sess, useSessionCookies: true })
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
      for (const [k, v] of Object.entries(headers)) {
        if (v) {
          try { request.setHeader(k, v) } catch { /* a header Chromium reserves */ }
        }
      }
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

  ipcMain.handle('lb-fetch-image', async (e, req) => {
    if (e.sender !== win.webContents) return { error: 'denied' }
    const url = String(req?.url || '')
    if (!/^https?:\/\//i.test(url)) return { error: 'not an http url' }
    const referer = req?.referer ? String(req.referer) : ''
    // Escalating attempts: look like the page's own <img> first (referer + the
    // browser's UA is what hotlink checks read), then plainer, then bare — one
    // site's requirement is another's reason to refuse.
    const attempts = [
      { Referer: referer, 'User-Agent': browserUA(), Accept: 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8', 'Accept-Language': 'en-US,en;q=0.9' },
      { Referer: referer },
      {},
    ]
    let last = { error: 'no attempt ran' }
    for (const headers of attempts) {
      last = await fetchImageOnce(url, headers)
      if (last?.data) return last
      // a definite answer from the server is not worth repeating with other
      // headers — only a refusal or a transport failure is
      if (/^http (4|5)\d\d$/.test(last?.error || '') && !/^http 40[13]$/.test(last.error)) break
    }
    // Last resort: the session's own fetch — a different path through the
    // network stack than net.request, which some blocks only apply to.
    try {
      const res = await sess.fetch(url, {
        headers: referer ? { Referer: referer, 'User-Agent': browserUA() } : { 'User-Agent': browserUA() },
      })
      if (!res.ok) return { error: `${last.error} · session fetch http ${res.status}` }
      const declared = Number(res.headers.get('content-length') || 0)
      if (declared > MAX_IMAGE) return { error: `${last.error} · session fetch: image too large` }
      const buf = Buffer.from(await res.arrayBuffer())
      const ct = String(res.headers.get('content-type') || '').split(';')[0].trim().toLowerCase()
      const kind = sniffImage(buf) || (ct.startsWith('image/') ? ct : '')
      if (buf.length && buf.length <= MAX_IMAGE && kind) {
        return { data: buf.toString('base64'), contentType: kind }
      }
      return { error: `${last.error} · session fetch: not an image (${ct || 'no content-type'})` }
    } catch (err) {
      return { error: `${last.error} · session fetch: ${err.message}` }
    }
  })

  // Native window-controls overlay recolor (theme switch)
  ipcMain.on('lb-titlebar', (e, opts) => {
    if (e.sender !== win.webContents) return
    try {
      win.setTitleBarOverlay({ color: opts.color, symbolColor: opts.symbolColor, height: 39 })
    } catch { /* not supported on this platform */ }
  })

  // Embedded-browser hygiene (Settings → Browser). 'cookies' removes every
  // site cookie EXCEPT the app origin's auth cookie; 'cache' drops HTTP cache.
  // localStorage is untouched — the app's own prefs live there too.
  ipcMain.handle('lb-clear-browsing', async (e, what) => {
    if (e.sender !== win.webContents) return false
    if (what === 'cache') {
      await sess.clearCache()
      return true
    }
    if (what === 'cookies') {
      const cookies = await sess.cookies.get({})
      for (const c of cookies) {
        const url = `${c.secure ? 'https' : 'http'}://${(c.domain || '').replace(/^\./, '')}${c.path || '/'}`
        if (url.startsWith(origin)) continue // keep lb_auth — the app stays signed in
        await sess.cookies.remove(url, c.name).catch(() => {})
      }
      return true
    }
    return false
  })

  // Navigation lockdown: keep the window on the app origin; open nothing externally.
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(origin)) e.preventDefault()
  })
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  // the OS window carries the app name ONCE — page titles never override it
  win.webContents.on('page-title-updated', (e) => e.preventDefault())

  win.once('ready-to-show', () => win.show())
  // A launch-unique query so a rebuilt UI always reaches the window: the entry
  // document carries no content hash, and a cached copy of it pins the window to
  // the bundle it referenced (the assets under it ARE hashed, so they cache hard).
  win.loadURL(`${origin}/app/?b=${Date.now()}`)
}

app.whenReady().then(main).catch((err) => {
  console.error(err)
  app.quit()
})

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

app.on('web-contents-created', (_e, contents) => {
  contents.on('will-attach-webview', (_evt, webPreferences) => {
    webPreferences.preload = path.join(__dirname, 'pick-preload.js')
    webPreferences.nodeIntegration = false
    webPreferences.contextIsolation = true
  })
  // Guest pages that try to open a window (target=_blank, window.open, middle-click)
  // must NOT spawn an OS window — deny it and route the URL back so the app opens an
  // in-app browser tab instead. The disposition rides along: middle-click/ctrl+click
  // is a 'background-tab' — the app keeps the current page fronted, like a browser.
  if (contents.getType() === 'webview') {
    // A key pressed while the PAGE has focus is delivered to the guest and stops
    // there — the app's document never sees it. So the shortcuts a browser is
    // expected to have would work everywhere except over the page itself, which
    // is where they are wanted. Caught here, named, and handed to the window
    // that owns the toolbar. Matched on the PHYSICAL key: on a Ukrainian layout
    // `key` is 'ф' while the key under the finger is still the browser's F.
    contents.on('before-input-event', (evt, input) => {
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
        const openTab = (url, background) =>
          contents.send('open-url-as-tab', { url, background })
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

    contents.setWindowOpenHandler(({ url, disposition }) => {
      if (/^https?:\/\//.test(url)) {
        contents.send('open-url-as-tab', { url, background: disposition === 'background-tab' })
      }
      return { action: 'deny' }
    })
  }
})

app.on('before-quit', () => {
  app.isQuitting = true
  if (sidecar && !sidecar.killed) sidecar.kill()
})

app.on('window-all-closed', () => app.quit())
