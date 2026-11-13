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
const OFFER_MS = 10 * 60 * 1000  // an offer nobody answered goes, and its password with it
const USED_EVERY_MS = 60 * 1000  // a login signed in with again is stamped used once a minute at most
const MAX_USERNAME = 256
const MAX_PASSWORD = 1024

let logins = null    // the vault's logins, while unlocked
let locking = null   // a lock still writing what it was writing
let exists = false   // whether the vault has a master password, as last seen
let win = null
let rootOf = () => ''
// a page's id → the sign-in it sent: { origin, username, password: Buffer, timer }
const offers = new Map()
const watched = new WeakSet() // pages whose going is listened for

function held() {
  if (!logins?.unlocked) throw new secrets.SecretsError('ELOCKED', 'the logins are locked')
  return logins
}

async function state() {
  exists = await secrets.exists(rootOf())
  return { exists, unlocked: !!logins?.unlocked, restored: !!logins?.restored }
}

function tellState(s) {
  if (win && !win.isDestroyed()) win.webContents.send('lb-pw-state', s)
}

// ---- the clipboard: a copied password for 30 seconds ----

let copied = '' // the digest of the password on the clipboard, while it may be — never the password
function clearClipboard(digest) {
  if (digest && crypto.createHash('sha256').update(clipboard.readText()).digest('hex') === digest) clipboard.clear()
  if (copied === digest) copied = ''
}
function copy(text) {
  clipboard.writeText(text)
  const digest = crypto.createHash('sha256').update(text).digest('hex')
  copied = digest
  setTimeout(() => clearClipboard(digest), CLIPBOARD_MS).unref?.()
}

// Locked: on the owner's word, when the vault drops, and when the app goes.
// The window is told at once, without asking the disk (a vault that dropped
// would not answer); a password still on the clipboard goes; what the store
// is writing is written, then its key is zeroed. Offers keep their pages'
// sign-ins until they are answered or go.
function lock() {
  const was = logins
  logins = null
  clearClipboard(copied)
  tellState({ exists, unlocked: false, restored: false })
  if (was) locking = was.lock().finally(() => { locking = null })
  return locking ?? Promise.resolve()
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

// Into the page a tab shows, if it is still on the login's site: the origin is
// the frame's own, never one the window names.
function fill(guestId, id) {
  const guest = webContents.fromId(Number(guestId))
  if (!guest || guest.isDestroyed() || guest.getType() !== 'webview' || guest.hostWebContents !== win.webContents) {
    return false
  }
  const frame = guest.mainFrame
  const login = held().list(frame.origin).find((x) => x.id === id)
  if (!login) return false
  frame.send('lb-pw-fill', { origin: frame.origin, username: login.username, password: opened(id) })
  void held().used(id).catch(() => {})
  return true
}

// ---- after a sign-in: save, or update ----

// An offer taken out of the map is its taker's: nothing else zeroes it.
function take(guestId) {
  const offer = offers.get(guestId)
  if (!offer) return null
  offers.delete(guestId)
  clearTimeout(offer.timer)
  return offer
}
// gone, its password zeroed — only `offer`, when one is named, never a newer one
function drop(guestId, offer) {
  if (offer && offers.get(guestId) !== offer) return
  take(guestId)?.password.fill(0)
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
function judge(offer) {
  if (!logins?.unlocked) return { kind: 'locked', match: null }
  if (logins.neverAsks(offer.origin)) return { kind: null, match: null }
  const match = matchOf(offer)
  if (match?.same) {
    if (!(Date.parse(match.login.used) > Date.now() - USED_EVERY_MS)) void logins.used(match.login.id).catch(() => {})
    return { kind: null, match }
  }
  return { kind: match ? 'update' : 'save', match }
}

function judged(guestId, offer) {
  if (offers.get(guestId) !== offer) return
  let kind = null
  try { kind = judge(offer).kind } catch { /* told as nothing to offer */ }
  if (!kind) drop(guestId, offer)
  tell(guestId, kind)
}

// A sign-in a page of this window's sent, from its top frame — one the owner
// sent (the preload takes no other): its origin is the frame's own, never one
// the page names.
function submitted(e, msg) {
  const guest = e.sender
  if (!win || guest.getType() !== 'webview' || guest.hostWebContents !== win.webContents
      || e.senderFrame !== guest.mainFrame) return
  const origin = e.senderFrame.origin
  const password = typeof msg?.password === 'string' ? msg.password : ''
  const username = typeof msg?.username === 'string' ? msg.username : ''
  if (!/^https?:\/\//.test(origin) || !password || password.length > MAX_PASSWORD || username.length > MAX_USERNAME) return
  drop(guest.id) // the next sign-in replaces the one before
  const offer = { origin, username, password: Buffer.from(password),
    timer: setTimeout(() => { drop(guest.id, offer); tell(guest.id, null) }, OFFER_MS) }
  offers.set(guest.id, offer)
  if (!watched.has(guest)) {
    watched.add(guest)
    const id = guest.id
    guest.once('destroyed', () => drop(id))
  }
  judged(guest.id, offer)
}

// The owner's answer to the bar: 'save' (a new login, or the saved one's new
// password — whichever the sign-in turns out to be, now that it is unlocked),
// 'never' for this site, or 'dismiss'. Answered, the offer is taken first, so
// nothing zeroes its password while it is being saved; and the window is told
// it is gone, unless the page sent a newer one meanwhile.
async function answer(guestId, what) {
  try {
    return await answered(guestId, what)
  } finally {
    if (!offers.has(guestId)) tell(guestId, null)
  }
}
async function answered(guestId, what) {
  if (!offers.has(guestId)) return 'gone'
  if (what !== 'save' && what !== 'never') {
    drop(guestId)
    return 'dropped'
  }
  held()
  const offer = take(guestId)
  try {
    if (what === 'never') {
      await held().setNever(offer.origin, true)
      return 'never'
    }
    const { kind, match } = judge(offer)
    if (!kind) return 'same'
    if (match) {
      await held().edit(match.login.id, { steps: [{ password: offer.password }], updated: match.login.updated })
    } else {
      await held().add({ origin: offer.origin, username: offer.username, password: offer.password })
    }
    return match ? 'updated' : 'saved'
  } finally {
    offer.password.fill(0)
  }
}

// Every answer is `{ ok, value }`, or `{ ok: false, code, message }` for what
// the owner can be told (a wrong master password, a locked list, a file that
// does not read); anything else is a fault and rejects. Only the app's own
// window, from its own top frame, is answered.
function handle(channel, fn) {
  ipcMain.handle(channel, async (e, ...args) => {
    if (!win || e.sender !== win.webContents || e.senderFrame !== win.webContents.mainFrame) return null
    try {
      return { ok: true, value: await fn(...args) }
    } catch (err) {
      if (err instanceof secrets.SecretsError) return { ok: false, code: err.code, message: err.message }
      throw err
    }
  })
}

async function unlocked(got) {
  logins = got
  if (got.restored) console.error('secrets.json did not read; the generation before it was opened')
  tellState(await state())
  // the bars that could not tell what to offer can now
  for (const [guestId, offer] of offers) judged(guestId, offer)
}

function wire(window, root) {
  win = window
  rootOf = root
  handle('lb-pw-state', state)
  handle('lb-pw-setup', async (master) => {
    await locking
    await unlocked(await secrets.create(rootOf(), String(master ?? '')))
  })
  handle('lb-pw-unlock', async (master) => {
    if (logins?.unlocked) return
    await locking
    await unlocked(await secrets.unlock(rootOf(), String(master ?? '')))
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
    updated: typeof change?.updated === 'string' ? change.updated : undefined,
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
