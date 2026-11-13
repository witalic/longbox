'use strict'
// The password manager in the main process (design/password-manager.md): the
// one place a vault's logins are unlocked, held and used. The app's window is
// told what it lists — a password only when the owner reveals one — and a page
// is given a login only when the owner fills it, for its own top-level origin,
// into its top frame. A password a page sends in a sign-in comes here too,
// never through the window, and is held only until the owner answers the bar
// that offers to save it.
const crypto = require('node:crypto')
const { clipboard, ipcMain, webContents } = require('electron')
const secrets = require('./secrets')

const CLIPBOARD_MS = 30 * 1000
const OFFER_MS = 10 * 60 * 1000 // an offer nobody answered goes, and its password with it

let logins = null // the vault's logins, while unlocked
let win = null
let rootOf = () => ''
const offers = new Map() // a page's id → the sign-in it sent: { origin, username, password: Buffer, timer }

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
// vault drops, and when the app goes. Offers keep their pages' sign-ins until
// they are answered or go.
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

// ---- after a sign-in: save, or update ----

function drop(guestId) {
  const offer = offers.get(guestId)
  if (!offer) return
  offer.password.fill(0)
  clearTimeout(offer.timer)
  offers.delete(guestId)
}

function tell(guestId, kind) {
  const offer = offers.get(guestId)
  if (!win || win.isDestroyed()) return
  win.webContents.send('lb-pw-offer', kind && offer
    ? { guestId, kind, origin: offer.origin, username: offer.username } : { guestId, kind: null })
}

// The saved login a sign-in is for — the same username, or the site's only
// login when the form asked for none (a change-password page) — and whether it
// sent the password already saved.
function matchOf(offer) {
  const list = held().list(offer.origin)
  const login = list.find((x) => x.username === offer.username) || (!offer.username && list.length === 1 ? list[0] : null)
  if (!login) return null
  const saved = held().reveal(login.id)
  const same = saved.length === offer.password.length && crypto.timingSafeEqual(saved, offer.password)
  saved.fill(0)
  return { login, same }
}

// What to offer: 'save', 'update', 'locked' (not known until the master
// password is given) — or nothing, when the site is never asked or the
// password is the one saved (then it was used).
async function judge(guestId) {
  const offer = offers.get(guestId)
  if (!offer) return null
  if (!logins?.unlocked) return 'locked'
  const match = logins.neverAsks(offer.origin) ? null : matchOf(offer)
  if (logins.neverAsks(offer.origin) || match?.same) {
    if (match) await logins.used(match.login.id)
    drop(guestId)
    return null
  }
  return match ? 'update' : 'save'
}

// A sign-in a page of this window's sent, from its top frame: its origin is the
// frame's own, never one the page names.
function submitted(e, msg) {
  const guest = e.sender
  if (!win || guest.getType() !== 'webview' || guest.hostWebContents !== win.webContents
      || e.senderFrame !== guest.mainFrame) return
  const origin = e.senderFrame.origin
  const password = typeof msg?.password === 'string' ? msg.password : ''
  if (!/^https?:\/\//.test(origin) || !password || password.length > 4096) return
  drop(guest.id) // the next sign-in replaces the one before
  offers.set(guest.id, { origin, username: String(msg?.username ?? '').slice(0, 512), password: Buffer.from(password),
    timer: setTimeout(() => { drop(guest.id); tell(guest.id, null) }, OFFER_MS) })
  guest.once('destroyed', () => drop(guest.id))
  void judge(guest.id).then((kind) => tell(guest.id, kind), () => tell(guest.id, null))
}

// The owner's answer to the bar: 'save' (a new login, or the saved one's new
// password — whichever the sign-in turns out to be, once unlocked), 'never'
// for this site, or 'dismiss'.
async function answer(guestId, what) {
  if (!offers.has(guestId)) return 'gone'
  if (what !== 'save' && what !== 'never') {
    drop(guestId)
    return 'dropped'
  }
  const offer = offers.get(guestId)
  if (what === 'never') {
    await held().setNever(offer.origin, true)
    drop(guestId)
    return 'never'
  }
  held()
  if (!(await judge(guestId))) return 'same'
  const match = matchOf(offer)
  if (match) await held().edit(match.login.id, { steps: [{ password: offer.password }] })
  else await held().add({ origin: offer.origin, username: offer.username, password: offer.password })
  drop(guestId)
  return match ? 'updated' : 'saved'
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
  handle('lb-pw-offer-answer', (guestId, what) => answer(Number(guestId), String(what)))
  handle('lb-pw-change-master', (current, next) => held().changeMaster(String(current ?? ''), String(next ?? '')))
  handle('lb-pw-nevers', () => held().nevers())
  handle('lb-pw-set-never', (origin, never) => held().setNever(String(origin ?? ''), !!never))
  ipcMain.on('lb-pw-submitted', submitted)
}

module.exports = { lock, wire }
