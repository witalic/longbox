'use strict'
// The password manager in the main process (design/password-manager.md): the
// one place a vault's logins are unlocked, held and used. The app's window is
// told what it lists — a password only when the owner reveals one — and a page
// is given a login only when the owner fills it, one of its top frame's site,
// into that frame. A password a page sends in a sign-in comes here too,
// never through the window, and is held only until the owner answers the bar
// that offers to save it.
const crypto = require('node:crypto')
const { clipboard, ipcMain, powerMonitor, webContents } = require('electron')
const secrets = require('./secrets')

const CLIPBOARD_MS = 30 * 1000
const OFFER_MS = 10 * 60 * 1000  // an offer nobody answered goes, and its password with it
const USED_EVERY_MS = 60 * 1000  // a login signed in with again is stamped used once a minute at most
const MAX_USERNAME = 256
const MAX_PASSWORD = 1024
const STEP_MS = 60 * 1000 // a Fill waits this long for the page to want its password
const IDLE_CHOICES = [5, 15, 30, 60, 0] // minutes; 0: never for being idle
const IDLE_CHECK_MS = 30 * 1000

let logins = null    // the vault's logins, while unlocked
let locking = null   // a lock still writing what it was writing
let exists = false   // whether the vault has a master password, as last seen
let win = null
let rootOf = () => ''
let idleMinutes = 15 // the window's choice, once it has told it
let idleTimer = null
// a page's id → the sign-in it sent: { origin, username, password: Buffer, timer }
const offers = new Map()
// A Fill, waiting for the page to want its password: at once, when the page has
// a password field, or on the password step of a sign-in in steps — one Fill,
// one sign-in. A page's id → { id, origin, until }.
const steps = new Map()
const watched = new WeakSet() // pages whose going is listened for

// a page of this window's, from its top frame — else null
function topOf(e) {
  const guest = e.sender
  if (!win || guest.getType() !== 'webview' || guest.hostWebContents !== win.webContents
      || e.senderFrame !== guest.mainFrame) return null
  return guest
}
function watchGone(guest) {
  if (watched.has(guest)) return
  watched.add(guest)
  const id = guest.id
  guest.once('destroyed', () => {
    drop(id)
    steps.delete(id)
  })
}

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
// Electron's clipboard answers with promises; a clearing that fails is said,
// never thrown out of a timer, where it would take the app down.

let copied = '' // the digest of the password on the clipboard, while it may be — never the password
async function clearClipboard(digest) {
  if (!digest) return
  if (copied === digest) copied = ''
  try {
    const now = await clipboard.readText() // '' for a clipboard with no text: an image, say — never ours
    if (now && crypto.createHash('sha256').update(now).digest('hex') === digest) clipboard.clear()
  } catch (err) {
    console.error('the clipboard was not cleared:', err)
  }
}
async function copy(text) {
  if (!text) return
  const digest = crypto.createHash('sha256').update(text).digest('hex')
  copied = digest // known before it is written, so a Lock meanwhile clears it too
  await clipboard.writeText(text)
  setTimeout(() => { void clearClipboard(digest) }, CLIPBOARD_MS).unref?.()
}

// Locked: on the owner's word, when the vault drops, and when the app goes.
// The window is told at once, without asking the disk (a vault that dropped
// would not answer); a password still on the clipboard goes; what the store
// is writing is written, then its key is zeroed. Offers keep their pages'
// sign-ins until they are answered or go.
function lock() {
  const was = logins
  logins = null
  watchIdle()
  steps.clear()
  const clearing = clearClipboard(copied)
  tellState({ exists, unlocked: false, restored: false })
  if (was) locking = was.lock().finally(() => { locking = null })
  return Promise.all([locking, clearing]).then(() => {})
}

// And by themselves: when the computer sleeps or its screen is locked, and
// after the owner's choice of minutes with no use of the computer — its
// keyboard and mouse, in any app — looked at while they are unlocked.
function watchIdle() {
  clearInterval(idleTimer)
  idleTimer = null
  if (!logins?.unlocked || !idleMinutes) return
  idleTimer = setInterval(() => {
    if (powerMonitor.getSystemIdleTime() >= idleMinutes * 60) void lock()
  }, IDLE_CHECK_MS)
}
function lockAway() {
  if (logins) void lock()
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
// the frame's own, never one the window names. `here`: the address the window
// believed the page on as the owner chose — another address of the site is
// asked about there first — so a page that is anywhere else now gets nothing.
function fill(guestId, id, here) {
  const guest = webContents.fromId(Number(guestId))
  if (!guest || guest.isDestroyed() || guest.getType() !== 'webview' || guest.hostWebContents !== win.webContents) {
    return false
  }
  const frame = guest.mainFrame
  if (frame.origin !== here) return false
  const login = held().list(frame.origin).find((x) => x.id === id)
  if (!login) return false
  // the username now; the password only once the page has a field for it
  frame.send('lb-pw-fill', { origin: frame.origin, username: login.username })
  steps.set(guest.id, { id, origin: frame.origin, until: Date.now() + STEP_MS })
  watchGone(guest)
  void held().used(id).catch(() => {})
  return true
}

// The page's top frame has a password field — the one it already had as the
// Fill came, or its password step's: the password of the Fill that waits for
// it, if one does — once, on the address it was filled on, within a minute,
// the logins still open. A page on another address, or later, gets nothing.
function stepShown(e) {
  const guest = topOf(e)
  const step = guest && steps.get(guest.id)
  if (!step) return
  steps.delete(guest.id)
  const frame = guest.mainFrame
  if (Date.now() > step.until || frame.origin !== step.origin || !logins?.unlocked) return
  const login = logins.list(frame.origin).find((x) => x.id === step.id)
  if (login) frame.send('lb-pw-fill', { origin: frame.origin, username: login.username, password: opened(step.id) })
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

// `origin`: the saved login's, for an update — it may be another address of the site
function tell(guestId, kind, origin = '') {
  const offer = offers.get(guestId)
  if (!win || win.isDestroyed()) return
  win.webContents.send('lb-pw-offer', kind && offer
    ? { guestId, kind, origin: origin || offer.origin, username: offer.username } : { guestId, kind: null })
}

// The saved login a sign-in is for, and whether it sent the password already
// saved: of the site's logins with its username (the page's own address first;
// the site's only login when the form asked for none, a change-password page),
// the one whose password it sent — else the first.
function matchOf(offer) {
  const list = held().list(offer.origin)
  const named = list.filter((x) => x.username === offer.username)
  const pool = named.length ? named : !offer.username && list.length === 1 ? list : []
  for (const login of pool) {
    const saved = held().reveal(login.id)
    const same = saved.length === offer.password.length && crypto.timingSafeEqual(saved, offer.password)
    saved.fill(0)
    if (same) return { login, same }
  }
  return pool.length ? { login: pool[0], same: false } : null
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
  let origin = ''
  try {
    const { kind: k, match } = judge(offer)
    kind = k
    origin = match?.login.origin ?? ''
  } catch { /* told as nothing to offer */ }
  if (!kind) drop(guestId, offer)
  tell(guestId, kind, origin)
}

// A sign-in a page of this window's sent, from its top frame — one the owner
// sent (the preload takes no other): its origin is the frame's own, never one
// the page names.
function submitted(e, msg) {
  const guest = topOf(e)
  if (!guest) return
  const origin = e.senderFrame.origin
  const password = typeof msg?.password === 'string' ? msg.password : ''
  const username = typeof msg?.username === 'string' ? msg.username : ''
  if (!/^https?:\/\//.test(origin) || !password || password.length > MAX_PASSWORD || username.length > MAX_USERNAME) return
  drop(guest.id) // the next sign-in replaces the one before
  const offer = { origin, username, password: Buffer.from(password),
    timer: setTimeout(() => { drop(guest.id, offer); tell(guest.id, null) }, OFFER_MS) }
  offers.set(guest.id, offer)
  watchGone(guest)
  judged(guest.id, offer)
}

// The owner's answer to the bar: 'save' (a new login, or the saved one's new
// password), 'never' for this site, or 'dismiss'. `shown`: what the bar said
// as it was answered — and only that is done. A bar answered while locked
// learns only now what the sign-in is: when it turns out to be other than it
// said (an update, of a login at another address of the site), it is shown
// anew and nothing is written. Answered, the offer is taken first, so nothing
// zeroes its password while it is being saved; and the window is told it is
// gone, unless the page sent a newer one meanwhile.
async function answer(guestId, what, shown) {
  try {
    return await answered(guestId, what, shown)
  } finally {
    if (!offers.has(guestId)) tell(guestId, null)
  }
}
async function answered(guestId, what, shown) {
  if (!offers.has(guestId)) return 'gone'
  if (what !== 'save' && what !== 'never') {
    drop(guestId)
    return 'dropped'
  }
  held()
  const now = what === 'save' ? judge(offers.get(guestId)) : null
  if (now?.kind) {
    const said = shown?.kind === 'update' ? 'update' : 'save'
    if (now.kind !== said || (said === 'update' && now.match.login.origin !== shown?.origin)) {
      judged(guestId, offers.get(guestId))
      return 'changed'
    }
  }
  const offer = take(guestId)
  try {
    if (what === 'never') {
      await held().setNever(offer.origin, true)
      return 'never'
    }
    const { kind, match } = now
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
  watchIdle()
  if (got.restored) console.error('secrets.json did not read; its second copy was opened')
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
  handle('lb-pw-lock-idle', (minutes) => {
    idleMinutes = IDLE_CHOICES.includes(Number(minutes)) ? Number(minutes) : 15
    watchIdle()
  })
  handle('lb-pw-list', (origin) => held().list(typeof origin === 'string' ? origin : ''))
  handle('lb-pw-reveal', (id, previous) => opened(id, previous))
  handle('lb-pw-copy', (id, previous) => copy(opened(id, previous)))
  handle('lb-pw-copy-text', (text) => copy(String(text ?? '')))
  // a username: no secret, so it stays on the clipboard
  handle('lb-pw-copy-name', (text) => clipboard.writeText(String(text ?? '')))
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
  handle('lb-pw-fill', (guestId, id, here) => fill(guestId, String(id), String(here ?? '')))
  handle('lb-pw-offer-answer', (guestId, what, shown) => answer(Number(guestId), String(what),
    { kind: String(shown?.kind ?? ''), origin: String(shown?.origin ?? '') }))
  handle('lb-pw-change-master', (current, next) => held().changeMaster(String(current ?? ''), String(next ?? '')))
  handle('lb-pw-nevers', (origin) => held().nevers(typeof origin === 'string' ? origin : ''))
  handle('lb-pw-set-never', (origin, never) => held().setNever(String(origin ?? ''), !!never))
  ipcMain.on('lb-pw-submitted', submitted)
  ipcMain.on('lb-pw-step', stepShown)
  powerMonitor.on('suspend', lockAway)
  powerMonitor.on('lock-screen', lockAway)
}

module.exports = { lock, wire }
