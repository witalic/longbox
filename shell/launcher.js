'use strict'
// The launch window (design/vault-home.md). The shell decides which vault the
// app opens BEFORE any sidecar exists, and never waits on one without a window
// up: a vault that answers within a second opens with no launch window at all;
// one that does not, has none chosen yet, or is in use elsewhere, gets this
// window — a page of the frontend build, loaded from disk (there is no sidecar
// to serve it yet), talking to the shell over launcher-preload.js.
const { BrowserWindow, app, dialog, ipcMain, nativeTheme } = require('electron')
const os = require('node:os')
const path = require('node:path')
const vaults = require('./vaults')

const SHOW_AFTER_MS = 1000   // an answer inside this opens the app with no window
const GIVE_UP_MS = 8000      // past this the window says the vault cannot be reached
const ASK_EVERY_MS = 3000

let win = null
let state = { screen: 'first', vault: null, waited: 0, others: [], lock: null, create: null, notvault: null,
  move: null, error: '' }
let token = 0          // the attempt in flight; a newer one makes the older stand down
let anyway = false     // the human said to open a vault that is in use
let settle = null      // resolves `choose` with the vault that opened (and what lets it go)
let retryMove = null   // the failed move's "Try again"

function push() {
  if (win && !win.isDestroyed()) win.webContents.send('lb-launcher-state', state)
}
function set(change) {
  state = { ...state, ...change }
  push()
}

function show(uiDir, icon) {
  if (win && !win.isDestroyed()) return
  const dark = nativeTheme.shouldUseDarkColors
  win = new BrowserWindow({
    width: 560, height: 480, resizable: false, maximizable: false, fullscreenable: false, show: false,
    title: app.getName(), icon, backgroundColor: dark ? '#0a0b0d' : '#f0eeea',
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: dark ? '#0f1115' : '#e6e3dd', symbolColor: dark ? '#9aa1ad' : '#575d67', height: 39 },
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true,
      preload: path.join(__dirname, 'launcher-preload.js') },
  })
  win.setMenu(null)
  win.webContents.on('will-navigate', (e) => e.preventDefault())
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  win.once('ready-to-show', () => win.show())
  // closed before a vault opened: there is nothing to run
  win.on('closed', () => { win = null; if (settle) app.quit() })
  win.loadFile(path.join(uiDir, 'launcher.html'))
}

async function others(except) {
  const list = vaults.known().libraries.filter((p) => !except || !vaults.same(p, except))
  const rows = list.map((p) => ({ path: p, name: vaults.nameOf(p), state: 'checking' }))
  set({ others: rows })
  await Promise.all(rows.map(async (row) => {
    const got = await vaults.ask(row.path, 4000)
    row.state = got === 'vault' ? 'answers' : got === 'folder' ? 'notvault' : 'silent'
    push()
  }))
}

// Open `root` if it can be: answers, is a vault, is not in use. Otherwise say
// why on the window — and, for a vault that does not answer or is in use, keep
// asking, and open it the moment it can be.
async function attempt(root, { quiet = false } = {}) {
  const mine = ++token
  const alive = () => mine === token
  const started = Date.now()
  let shown = !quiet
  state.vault = { path: root, name: vaults.nameOf(root), share: '' }
  set({ error: '', waited: 0, ...(quiet ? {} : { screen: 'opening' }) })
  if (!quiet) { show(attempt.uiDir, attempt.icon); void others(root) }
  void vaults.shareOf(root).then((share) => { if (alive() && state.vault) { state.vault.share = share; push() } })
  const reveal = setTimeout(() => {
    if (!alive() || shown) return
    shown = true
    set({ screen: 'opening' })
    show(attempt.uiDir, attempt.icon)
    void others(root)
  }, SHOW_AFTER_MS)
  try {
    for (;;) {
      const got = await vaults.ask(root, ASK_EVERY_MS)
      if (!alive()) return
      if (got === 'vault') break
      if (got === 'folder') {
        shown = true
        set({ screen: 'notvault', notvault: { path: root, empty: (await vaults.inspectFolder(root)).state === 'empty' } })
        show(attempt.uiDir, attempt.icon)
        return
      }
      const waited = Date.now() - started
      set({ waited: Math.round(waited / 1000), ...(shown && waited > GIVE_UP_MS ? { screen: 'unreachable' } : {}) })
      if (got !== 'silent') await vaults.sleep(ASK_EVERY_MS)
      if (!alive()) return
    }
    let listed = false
    for (;;) {
      const lock = await vaults.readLock(root)
      if (!alive()) return
      const held = vaults.lockState(lock)
      // a lock this computer left is a crash's: its process is gone (lockState asked)
      if (anyway || held === 'free' || held === 'mine' || (held === 'left' && lock.computer === os.hostname())) break
      shown = true
      set({ screen: held === 'held' ? 'busy' : 'left', lock: { ...lock, here: lock.computer === os.hostname() } })
      show(attempt.uiDir, attempt.icon)
      if (!listed) { listed = true; void others(root) }
      if (held === 'left') return // safe, but the human opens it: a plain Open
      await vaults.sleep(5000)    // opens by itself when that computer lets go
      if (!alive()) return
    }
    clearTimeout(reveal)
    // taken now, before anything is written into the vault (the one-time move can take minutes)
    let letGo
    try {
      letGo = await vaults.holdLock(root)
    } catch (err) {
      if (!alive()) return
      shown = true
      set({ screen: 'unreachable', error: `The vault could not be taken: ${err.message}` })
      show(attempt.uiDir, attempt.icon)
      return
    }
    if (!alive() || !settle) { letGo(); return }
    vaults.register(root, { last: true })
    set({ screen: 'starting' })
    const done = settle
    settle = null
    done({ root, letGo })
  } finally {
    clearTimeout(reveal)
  }
}

async function pickFolder(title) {
  const res = await dialog.showOpenDialog(win, { title, properties: ['openDirectory', 'createDirectory'] })
  return res.canceled || !res.filePaths.length ? '' : res.filePaths[0]
}

// `here`: an empty folder picked as a vault — it is made one where it is
function startCreate(here) {
  token++ // whatever was being waited on is not any more
  const last = vaults.known().last
  const create = here
    ? { parent: path.dirname(here), name: path.basename(here), check: null }
    : { parent: last ? path.dirname(last) : app.getPath('documents'), name: 'LongBox', check: null }
  set({ screen: 'create', create })
  void check()
}

async function check() {
  const c = state.create
  if (!c) return
  const got = await vaults.inspectNew(c.parent, c.name)
  if (state.create === c) set({ create: { ...c, check: got } })
}

function wire() {
  const own = (e) => win && !win.isDestroyed() && e.sender === win.webContents
  ipcMain.handle('lb-launcher-get', (e) => (own(e) ? state : null))
  ipcMain.handle('lb-launcher-act', async (e, act, arg) => {
    if (!own(e)) return null
    if (act === 'quit') {
      app.quit()
      return null
    }
    if (act === 'retry' && retryMove) {
      retryMove()
      return state
    }
    if (!settle) return state // a vault was chosen already: the window only reports from here
    switch (act) {
      case 'retry':
        if (state.vault) void attempt(state.vault.path)
        break
      case 'open': // one of the registered vaults
        if (typeof arg === 'string' && vaults.known().libraries.some((p) => vaults.same(p, arg))) {
          anyway = false
          void attempt(arg)
        }
        break
      case 'choose': {
        const picked = await pickFolder('Open a vault')
        if (picked) { anyway = false; void attempt(picked) }
        break
      }
      case 'anyway':
        anyway = true
        if (state.vault) void attempt(state.vault.path)
        break
      case 'create':
        startCreate(typeof arg === 'string' ? arg : '')
        break
      case 'create-parent': {
        const picked = await pickFolder('Where should the new vault go?')
        if (picked && state.create) { set({ create: { ...state.create, parent: picked, check: null } }); await check() }
        break
      }
      case 'create-name':
        if (state.create && typeof arg === 'string') { set({ create: { ...state.create, name: arg, check: null } }); await check() }
        break
      case 'create-go': {
        const got = state.create && await vaults.inspectNew(state.create.parent, state.create.name)
        if (!got || (got.state !== 'new' && got.state !== 'empty')) { await check(); break }
        try {
          await vaults.createVault(got.path)
        } catch (err) {
          set({ error: `The vault could not be made: ${err.message}` })
          break
        }
        anyway = false
        void attempt(got.path)
        break
      }
      case 'back':
        token++
        set({ screen: 'first', error: '' })
        void others('')
        break
    }
    return state
  })
}

// The vault to open — `{ root, letGo }`, locked: the one opened last, if it can
// be, with no window when it answers at once; otherwise whatever the human
// chooses in the window. `why`: what went wrong opening the last one, shown
// when the app came back here because of it.
async function choose({ forced, why, uiDir, icon }) {
  attempt.uiDir = uiDir
  attempt.icon = icon
  wire()
  await vaults.adoptOldDefault()
  return new Promise((resolve) => {
    settle = resolve
    const last = vaults.known().last
    if (last && !forced) {
      void attempt(last, { quiet: true })
    } else {
      set({ screen: 'first', error: why || '' })
      show(uiDir, icon)
      void others('')
    }
  })
}

// The one-time move of this computer's data into the vault, shown with its
// progress (the browser's cache alone can be hundreds of megabytes on a network
// drive). Only a copy: nothing is deleted until the app has what it needs. A copy
// that fails stops here — the app does not open a vault half-moved into — and
// "Try again" starts it over.
async function moving(plan, { uiDir, icon }) {
  const parts = () => plan.parts.filter((p) => p.items.length).map((p) => ({ what: p.what, done: p.done, total: p.total }))
  show(uiDir, icon)
  for (;;) {
    set({ screen: 'moving', error: '', move: { parts: parts() } })
    let last = 0
    try {
      await vaults.copyLegacy(plan, () => {
        if (Date.now() - last < 150) return
        last = Date.now()
        set({ move: { parts: parts() } })
      })
      set({ screen: 'starting', move: { parts: parts() } })
      return
    } catch (err) {
      set({ screen: 'movefailed', error: err.message, move: { parts: parts() } })
      await new Promise((r) => { retryMove = r })
      retryMove = null
    }
  }
}

function close() {
  settle = null
  if (win && !win.isDestroyed()) win.close()
  win = null
}

module.exports = { choose, moving, close, window: () => win }
