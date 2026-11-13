'use strict'
// Where a vault's browser profile lives (design/sign-ins.md): in the vault, or
// on each computer — the vault's own answer, kept in `.longbox/browser.json`
// and read before the browser opens. A profile is moved to where the answer
// says the way the 3.0 move did it: copied beside its place, renamed in when it
// is whole, and only then is the old one removed — all of it, its caches with
// it. Caches are never carried across; they refill.
const fs = require('node:fs')
const path = require('node:path')
const vaults = require('./vaults')

const DOC = 'browser.json'
const SCHEMA = 1
const KEEPS = new Set(['vault', 'computer'])

const vaultDir = (root) => path.join(root, vaults.APP_DIR, 'browser')
// a vault's profile on this computer is named by its id, never its path: a vault
// opened from another drive letter or a share keeps its sign-ins
const localDir = (home, id) => path.join(home, 'browsers', id)
const markerOf = (home, id) => path.join(home, 'browsers', `${id}.json`)
const isCache = (name) => vaults.CACHES.some((c) => vaults.fold(c) === vaults.fold(name))

async function exists(p) {
  try {
    await fs.promises.stat(p)
    return true
  } catch (e) {
    if (e.code === 'ENOENT') return false
    throw e
  }
}

// The vault's id, from its vault.json — what names its profile on a computer.
async function vaultId(root) {
  const doc = JSON.parse(await fs.promises.readFile(path.join(root, 'vault.json'), 'utf8'))
  const id = String(doc.id || '')
  if (!/^[A-Za-z0-9_-]{8,64}$/.test(id)) throw new Error('the vault has no usable id')
  return id
}

// `{ keep, writable }`: keep is 'vault', 'computer', or null when the vault has
// not answered (no file, or one that does not read). A newer build's file is
// read, never written.
async function readChoice(root) {
  let doc
  try {
    doc = JSON.parse(await fs.promises.readFile(path.join(root, vaults.APP_DIR, DOC), 'utf8'))
  } catch {
    return { keep: null, writable: true }
  }
  const schema = Number(doc?.schema) || SCHEMA
  const keep = KEEPS.has(doc?.keep) ? doc.keep : null
  return { keep, writable: schema <= SCHEMA }
}

async function writeChoice(root, keep) {
  if (!KEEPS.has(keep)) throw new Error(`not a place for sign-ins: ${keep}`)
  if (!(await readChoice(root)).writable) throw new Error(`${DOC} was written by a newer longbox`)
  await fs.promises.mkdir(path.join(root, vaults.APP_DIR), { recursive: true })
  await vaults.writeAtomic(path.join(root, vaults.APP_DIR, DOC), JSON.stringify({ schema: SCHEMA, keep }, null, 2))
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

// What has to happen before the browser opens — null when nothing does:
// `move` (from, to, toward) or `drop` (a profile where the answer says none
// belongs: a computer's own copy once the vault keeps its sign-ins, or one in a
// vault that keeps them on each computer — an older longbox, or a move whose
// last step was cut short, leaves that).
async function plan({ root, home, id, keep }) {
  const inVault = vaultDir(root)
  const here = localDir(home, id)
  const [v, h] = await Promise.all([exists(inVault), exists(here)])
  if (keep === 'computer') {
    if (v && !h) return { kind: 'move', from: inVault, to: here, toward: 'computer' }
    if (v && h) return { kind: 'drop', dir: inVault, where: 'vault' }
  } else if (keep === 'vault') {
    if (h && !v) return { kind: 'move', from: here, to: inVault, toward: 'vault' }
    if (h && v) return { kind: 'drop', dir: here, where: 'computer' }
  }
  return null
}

const RM = { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }

// The move: sign-ins and site data copied beside their place and renamed in
// when whole; then the old profile goes, caches and all. `onProgress(step,
// done, total)` — step 'copy' in bytes, then 'remove'. A copy that fails leaves
// the old profile as it was, and the next attempt starts the copy over.
async function move(p, onProgress = () => {}) {
  const staged = `${p.to}.moving`
  await fs.promises.rm(staged, RM)
  const size = (await sizes(p.from)) || { data: 0, cache: 0 }
  let done = 0
  onProgress('copy', 0, size.data)
  await fs.promises.mkdir(staged, { recursive: true })
  for (const n of await fs.promises.readdir(p.from)) {
    if (isCache(n)) continue
    await vaults.copyInto(path.join(p.from, n), path.join(staged, n), (bytes) => {
      done += bytes
      onProgress('copy', done, size.data)
    }, () => false)
  }
  await fs.promises.rename(staged, p.to)
  onProgress('remove', 0, size.data + size.cache)
  await fs.promises.rm(p.from, RM)
  onProgress('remove', size.data + size.cache, size.data + size.cache)
}

async function drop(p) {
  await fs.promises.rm(p.dir, RM)
}

// This computer's profiles remember which vault they belong to, so forgetting a
// vault can remove its sign-ins here without the vault having to answer.
async function remember(home, id, root) {
  await fs.promises.mkdir(path.join(home, 'browsers'), { recursive: true })
  await vaults.writeAtomic(markerOf(home, id), JSON.stringify({ path: path.resolve(root) }, null, 2))
}

// Remove this computer's sign-ins for the vault at `root`; how many profiles went.
async function forget(home, root) {
  const dir = path.join(home, 'browsers')
  let names
  try { names = await fs.promises.readdir(dir) } catch { return 0 }
  let gone = 0
  for (const n of names.filter((x) => x.endsWith('.json'))) {
    let at = ''
    try { at = JSON.parse(await fs.promises.readFile(path.join(dir, n), 'utf8')).path || '' } catch { continue }
    if (!at || !vaults.same(at, root)) continue
    const id = n.slice(0, -'.json'.length)
    await fs.promises.rm(localDir(home, id), RM)
    await fs.promises.rm(markerOf(home, id), { force: true })
    gone++
  }
  return gone
}

module.exports = { vaultDir, localDir, vaultId, readChoice, writeChoice, sizes, plan, move, drop, remember, forget }
