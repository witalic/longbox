'use strict'
// Where a vault's browser profile lives (design/sign-ins.md): in the vault, or
// on each computer — the vault's own answer, kept in `.longbox/browser.json`
// and read before the browser opens.
//
// The vault's answer only ever names a profile that is in place. A change is
// this computer's own until it is done: asked in Settings, it waits in this
// computer's marker (`browsers/<id>.json`, `owe`) for the next time this
// computer opens the vault; asked at the first start, it is held in memory. The
// start then copies the profile beside its new place, renames it in when whole,
// writes the vault's answer, and only then removes the old profile — all of it,
// caches with it (they are never carried; they refill). Cut short anywhere, the
// next start finds the same change owed and carries it on; another computer
// opening the vault meanwhile acts on the answer as it was; and a change the
// vault's answer has moved on from since (`rev`) is never made.
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const vaults = require('./vaults')

const DOC = 'browser.json'
const SCHEMA = 1
const KEEPS = new Set(['vault', 'computer'])
const ID = /^[A-Za-z0-9_-]{8,64}$/

const vaultDir = (root) => path.join(root, vaults.APP_DIR, 'browser')
// a vault's profile on this computer is named by its id, never its path: a vault
// opened from another drive letter or a share keeps its sign-ins
const localDir = (home, id) => path.join(home, 'browsers', id)
const markerOf = (home, id) => path.join(home, 'browsers', `${id}.json`)
const isCache = (name) => vaults.CACHES.some((c) => vaults.fold(c) === vaults.fold(name))
// an id fit to name a folder; one edited by hand is named by its hash
const idName = (raw) => (ID.test(raw) ? raw : crypto.createHash('sha256').update(raw).digest('hex').slice(0, 32))
const RM = { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }

async function exists(p) {
  try {
    await fs.promises.stat(p)
    return true
  } catch (e) {
    if (e.code === 'ENOENT') return false
    throw e
  }
}

// The vault's id — what names its profile on a computer. A vault without one
// (from before vaults had an id) is given one here.
async function vaultId(root) {
  return idName(await vaults.ensureIdentity(root))
}

// `{ keep, rev, writable }`: keep is 'vault', 'computer', or null when the vault
// has not answered — no file, or one that does not parse. `rev`: which answer
// it is, new each time it is written. A read that failed otherwise is no answer
// at all, and raises. A newer build's file is read, never written.
async function readChoice(root) {
  let text
  try {
    text = await vaults.within(8000, fs.promises.readFile(path.join(root, vaults.APP_DIR, DOC), 'utf8'), root)
  } catch (e) {
    if (e.code === 'ENOENT') return { keep: null, rev: '', writable: true }
    throw e
  }
  let doc
  try { doc = JSON.parse(text) } catch { return { keep: null, rev: '', writable: true } }
  const schema = Number(doc?.schema) || SCHEMA
  const keep = KEEPS.has(doc?.keep) ? doc.keep : null
  return { keep, rev: typeof doc?.rev === 'string' ? doc.rev : '', writable: schema <= SCHEMA }
}

// the answer written, and its rev
async function writeChoice(root, keep) {
  if (!KEEPS.has(keep)) throw new Error(`not a place for sign-ins: ${keep}`)
  if (!(await readChoice(root)).writable) throw new Error(`${DOC} was written by a newer longbox`)
  const rev = crypto.randomBytes(8).toString('hex')
  await fs.promises.mkdir(path.join(root, vaults.APP_DIR), { recursive: true })
  await vaults.writeAtomic(path.join(root, vaults.APP_DIR, DOC), JSON.stringify({ schema: SCHEMA, keep, rev }, null, 2))
  return rev
}

// This computer's marker for a vault's profile: `{ path, owe, against }` — the
// vault it was last used for; the change this computer asked for and has not
// made yet, and the answer (its rev) it was asked against. Kept while the vault
// keeps its sign-ins here, or while a change is owed.
async function readMarker(home, id) {
  try {
    const raw = JSON.parse(await fs.promises.readFile(markerOf(home, id), 'utf8'))
    return { path: typeof raw?.path === 'string' ? raw.path : '', owe: KEEPS.has(raw?.owe) ? raw.owe : null,
      against: typeof raw?.against === 'string' ? raw.against : '' }
  } catch {
    return null
  }
}

async function writeMarker(home, id, { path: at, owe = null, against = '' }) {
  await fs.promises.mkdir(path.join(home, 'browsers'), { recursive: true })
  await vaults.writeAtomic(markerOf(home, id),
    JSON.stringify({ path: path.resolve(at), ...(owe ? { owe, against } : {}) }, null, 2))
}

// The change a marker owes the vault — none when the vault's answer was written
// again since it was asked (another computer changed it, maybe back and forth):
// the latest answer stands. `stale`: a change dropped that way, which the vault
// does not already have.
function owed(mark, { keep, rev, writable }) {
  if (!mark?.owe || !writable) return { owe: null, stale: false }
  if (mark.against !== rev) return { owe: null, stale: mark.owe !== keep }
  return { owe: mark.owe, stale: false }
}

async function dropMarker(home, id) {
  await fs.promises.rm(markerOf(home, id), { force: true })
}

// Whether this computer's profile for the id is the vault's at `root`: its
// marker names this folder — by this path or another (a drive letter and its
// share: a probe written here shows up there) — or a folder the vault is no
// longer in (it moved: what that folder was on answers, the vault is not
// there). Otherwise it may be another folder's with the same id, a copy of
// this vault that answers there or cannot be asked now, and nothing of it is
// taken, replaced or removed.
async function mine(root, mark, id) {
  const at = mark?.path
  if (!at || vaults.same(at, root)) return true
  let other
  try {
    other = JSON.parse(await vaults.within(4000, fs.promises.readFile(path.join(at, 'vault.json'), 'utf8'), at)).id
  } catch (e) {
    if (e.code !== 'ENOENT') return false
    const top = path.parse(path.resolve(at)).root
    return vaults.within(4000, fs.promises.stat(top), top).then(() => true, () => false)
  }
  if (typeof other !== 'string' || !other || idName(other) !== id) return true
  const probe = path.join(vaults.APP_DIR, `probe-${crypto.randomBytes(6).toString('hex')}`)
  await fs.promises.writeFile(path.join(root, probe), '')
  try {
    await vaults.within(4000, fs.promises.stat(path.join(at, probe)), at)
    return true
  } catch {
    return false
  } finally {
    await fs.promises.rm(path.join(root, probe), { force: true })
  }
}

// the probes of a start cut short
async function sweepProbes(root) {
  const dir = path.join(root, vaults.APP_DIR)
  for (const n of await fs.promises.readdir(dir).catch(() => [])) {
    if (n.startsWith('probe-')) await fs.promises.rm(path.join(dir, n), { force: true })
  }
}

// What a profile holds, in bytes: sign-ins and site data, and the caches. Null
// when there is no profile there.
async function sizes(dir) {
  let names
  try { names = await fs.promises.readdir(dir) } catch (e) {
    if (e.code === 'ENOENT') return null
    throw e
  }
  const out = { data: 0, cache: 0 }
  for (const n of names) {
    const size = await vaults.sizeOf(path.join(dir, n), () => false).catch(() => 0)
    out[isCache(n) ? 'cache' : 'data'] += size
  }
  return out
}

const other = (place) => (place === 'vault' ? 'computer' : 'vault')

// The next thing to do before the browser opens — null when nothing is left.
// `keep`: the vault's answer (null: it has not answered, and its profile is the
// one in it); `owe`: the change this computer is making; `mine`: whether this
// computer's profile for the id is this vault's — one that may be another
// folder's is never removed or replaced.
// - `move` (from, to, toward): the profile copied to its new place, renamed in
//   when whole — then the vault's answer is written;
// - `answer` (keep): the answer written, with nothing to carry;
// - `drop` (dir, where): a profile where the answer says none belongs — the
//   old one once a move is done, a leftover (an older build's, or another
//   computer's once the vault keeps its sign-ins), or a copy cut short.
async function next({ root, home, id, keep, owe, mine: ours = true }) {
  const at = { vault: vaultDir(root), computer: localDir(home, id) }
  const [v, h] = await Promise.all([exists(at.vault), exists(at.computer)])
  const has = { vault: v, computer: h }
  const free = (place) => place === 'vault' || ours
  if (owe && owe !== keep) {
    const from = keep === 'computer' ? 'computer' : 'vault'
    // a profile that may be another folder's is never replaced (carried into the
    // vault, it is copied and left where it is)
    if (!free(owe)) throw new Error("this computer's profile for the vault's id may be another folder's")
    if (from !== owe && has[from]) return { kind: 'move', from: at[from], to: at[owe], toward: owe }
    // nothing to carry: what is at the new place belongs to no one
    if (from !== owe && has[owe]) return { kind: 'drop', dir: at[owe], where: owe }
    return { kind: 'answer', keep: owe }
  }
  const settled = owe || keep
  const left = settled && other(settled)
  if (left && has[left] && free(left)) return { kind: 'drop', dir: at[left], where: left }
  for (const place of ['vault', 'computer']) {
    if (free(place) && await exists(`${at[place]}.moving`)) {
      return { kind: 'drop', dir: `${at[place]}.moving`, where: place, staged: true }
    }
  }
  return null
}

// A file just written can be held for a moment on Windows (an antivirus, the
// indexer): the rename is asked again before the copy is given up on.
async function renameIn(from, to) {
  for (let i = 0; ; i++) {
    try {
      await fs.promises.rename(from, to)
      return
    } catch (e) {
      if (i >= 50 || !['EPERM', 'EACCES', 'EBUSY'].includes(e.code)) throw e
      await vaults.sleep(200)
    }
  }
}

// The move's copy: sign-ins and site data copied beside their new place and
// renamed in when whole, over whatever was left there. `onProgress(done,
// total, cache)` in bytes — `cache`: what the old profile's caches hold, which
// go with it. A copy that fails leaves the profile in use as it was, and the
// next attempt starts the copy over.
async function move(p, onProgress = () => {}) {
  const staged = `${p.to}.moving`
  await fs.promises.rm(staged, RM)
  const size = (await sizes(p.from)) || { data: 0, cache: 0 }
  let done = 0
  onProgress(0, size.data, size.cache)
  await fs.promises.mkdir(staged, { recursive: true })
  for (const n of await fs.promises.readdir(p.from)) {
    if (isCache(n)) continue
    await vaults.copyInto(path.join(p.from, n), path.join(staged, n), (bytes) => {
      done += bytes
      onProgress(done, size.data, size.cache)
    }, () => false)
  }
  await fs.promises.rm(p.to, RM)
  await renameIn(staged, p.to)
}

async function drop(p) {
  await fs.promises.rm(p.dir, RM)
}

// Carries out one step of `next`; returns the vault's answer after it. The
// answer is written only once the profile it names is in place; an error in
// writing it says so (`step: 'answer'`).
async function take(step, { root, keep }, onProgress) {
  if (step.kind === 'drop') {
    await drop(step)
    return keep
  }
  if (step.kind === 'move') await move(step, onProgress)
  const to = step.kind === 'move' ? step.toward : step.keep
  try {
    await writeChoice(root, to)
  } catch (e) {
    e.step = 'answer'
    throw e
  }
  return to
}

// Remove this computer's sign-ins for the vault at `root` — and any change it
// owed that vault; how many profiles went. `inUse`: the profile the browser has
// open, never removed (a vault that moved from `root` may be using it).
async function forget(home, root, inUse = '') {
  const dir = path.join(home, 'browsers')
  let names
  try { names = await fs.promises.readdir(dir) } catch { return 0 }
  let gone = 0
  for (const n of names) {
    const id = n.endsWith('.json') ? n.slice(0, -'.json'.length) : ''
    if (!ID.test(id)) continue
    const mark = await readMarker(home, id)
    if (!mark?.path || !vaults.same(mark.path, root) || (inUse && vaults.same(localDir(home, id), inUse))) continue
    if (await exists(localDir(home, id))) gone++
    await fs.promises.rm(localDir(home, id), RM)
    await fs.promises.rm(`${localDir(home, id)}.moving`, RM)
    await dropMarker(home, id)
  }
  return gone
}

module.exports = {
  dropMarker, forget, localDir, mine, move, next, owed, readChoice, readMarker, sizes, sweepProbes, take, vaultDir,
  vaultId, writeChoice, writeMarker,
}
