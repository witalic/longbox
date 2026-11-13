'use strict'
// The password manager in the main process (design/password-manager.md): the
// one place a vault's logins are unlocked, held and used. The app's window is
// told what it lists — a password only when the owner reveals one — and a page
// is given a login only when the owner fills it, for its own top-level origin,
// into its top frame.
const crypto = require('node:crypto')
const { clipboard, ipcMain, webContents } = require('electron')
const secrets = require('./secrets')

const CLIPBOARD_MS = 30 * 1000

let logins = null // the vault's logins, while unlocked
let win = null
let rootOf = () => ''

function held() {
  if (!logins?.unlocked) throw new secrets.SecretsError('ELOCKED', 'the logins are locked')
  return logins
}

async function state() {
  return { exists: await secrets.exists(rootOf()), unlocked: !!logins?.unlocked }
}

async function push() {
  if (win && !win.isDestroyed()) win.webContents.send('lb-pw-state', await state())
}

// Locked: the data key zeroed, the list dropped — on the owner's word, when the
// vault drops, and when the app goes.
function lock() {
  if (logins) logins.lock()
  logins = null
  void push().catch(() => {})
}

// A password is decrypted for one use and its Buffer zeroed after it.
function opened(id, previous) {
  const buf = held().reveal(String(id), Number.isInteger(previous) ? previous : -1)
  try {
    return buf.toString('utf8')
  } finally {
    buf.fill(0)
  }
}

// On the clipboard for 30 seconds, then cleared — if it still holds it. Only a
// digest is kept to tell, never the password.
function copy(text) {
  clipboard.writeText(text)
  const digest = crypto.createHash('sha256').update(text).digest('hex')
  setTimeout(() => {
    if (crypto.createHash('sha256').update(clipboard.readText()).digest('hex') === digest) clipboard.clear()
  }, CLIPBOARD_MS).unref?.()
}

// Into the page a tab shows, if it is still on the login's site: the origin is
// the frame's own, never one the window names.
async function fill(guestId, id) {
  const guest = webContents.fromId(Number(guestId))
  if (!guest || guest.isDestroyed() || guest.getType() !== 'webview' || guest.hostWebContents !== win.webContents) {
    return false
  }
  const frame = guest.mainFrame
  const login = held().list(frame.origin).find((x) => x.id === id)
  if (!login) return false
  frame.send('lb-pw-fill', { origin: frame.origin, username: login.username, password: opened(id) })
  await held().used(id)
  return true
}

// Every answer is `{ ok, value }`, or `{ ok: false, code, message }` for what
// the owner can be told (a wrong master password, a locked list, a file that
// does not read); anything else is a fault and rejects.
function handle(channel, fn) {
  ipcMain.handle(channel, async (e, ...args) => {
    if (!win || e.sender !== win.webContents) return null
    try {
      return { ok: true, value: await fn(...args) }
    } catch (err) {
      if (err instanceof secrets.SecretsError) return { ok: false, code: err.code, message: err.message }
      throw err
    }
  })
}

function wire(window, root) {
  win = window
  rootOf = root
  handle('lb-pw-state', state)
  handle('lb-pw-setup', async (master) => {
    logins = await secrets.create(rootOf(), String(master ?? ''))
    await push()
  })
  handle('lb-pw-unlock', async (master) => {
    const got = await secrets.unlock(rootOf(), String(master ?? ''))
    if (logins) logins.lock()
    logins = got
    await push()
  })
  handle('lb-pw-lock', () => lock())
  handle('lb-pw-list', (origin) => held().list(typeof origin === 'string' ? origin : ''))
  handle('lb-pw-reveal', (id, previous) => opened(id, previous))
  handle('lb-pw-copy', (id, previous) => { copy(opened(id, previous)) })
  handle('lb-pw-copy-text', (text) => { copy(String(text ?? '')) })
  handle('lb-pw-add', (login) => held().add({ origin: login?.origin, username: login?.username,
    password: String(login?.password ?? ''), note: login?.note }))
  handle('lb-pw-edit', (id, change) => held().edit(String(id), {
    username: change?.username, note: change?.note,
    steps: (Array.isArray(change?.steps) ? change.steps : []).map((s) => (s && 'password' in s
      ? { password: String(s.password) } : { use: s?.use, forget: s?.forget })),
  }))
  handle('lb-pw-remove', (id) => held().remove(String(id)))
  handle('lb-pw-generate', (opts) => secrets.generate(opts && typeof opts === 'object' ? opts : {}))
  handle('lb-pw-fill', (guestId, id) => fill(guestId, String(id)))
}

module.exports = { lock, wire }
