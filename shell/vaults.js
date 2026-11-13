'use strict'
// Where the vaults are, and what the shell asks of one before the app opens it
// (design/vault-home.md). This computer keeps config.json — the registered
// vaults and the one opened last — and nothing else; everything the app keeps
// lives in the vault it serves.
const { app } = require('electron')
const { execFile } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const VAULT_FILE = 'vault.json'
const APP_DIR = '.longbox'
// a lock not refreshed for this long was left behind by an app that is gone
const LOCK_FRESH_MS = 2 * 60 * 1000
const HEARTBEAT_MS = 30 * 1000
const WIN = process.platform === 'win32'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const nameOf = (root) => path.basename(path.resolve(root)) || path.resolve(root)
const fold = (s) => (WIN ? s.toLowerCase() : s) // file names, as this system compares them
const same = (a, b) => fold(path.resolve(a)) === fold(path.resolve(b))
// a scratch name of this write's own: two writers never share one
const scratch = (file) => `${file}.${process.pid}-${crypto.randomBytes(4).toString('hex')}.tmp`
// an answer that does not come in time is none — a write to a drive that just
// dropped waits out the network otherwise
const within = (ms, p, what) => Promise.race([p,
  sleep(ms).then(() => { throw new Error(`${what} did not answer in time`) })])

async function writeAtomic(file, data) {
  const tmp = scratch(file)
  try {
    await fs.promises.writeFile(tmp, data)
    await fs.promises.rename(tmp, file)
  } catch (e) {
    fs.promises.unlink(tmp).catch(() => {})
    throw e
  }
}

// ---- config.json ----------------------------------------------------------------

const configFile = () => path.join(app.getPath('userData'), 'config.json')

// A file that is there but does not parse is the only record of where the vaults
// were: it is set aside, never written over, and the app starts as if new.
function readConfig() {
  let text
  try { text = fs.readFileSync(configFile(), 'utf8') } catch { return {} }
  try {
    const raw = JSON.parse(text)
    if (raw && typeof raw === 'object' && !Array.isArray(raw)) return raw
  } catch { /* set aside below */ }
  const aside = `${configFile()}.unreadable-${Date.now()}`
  try { fs.renameSync(configFile(), aside) } catch { /* left where it is */ }
  console.error(`config.json could not be read; kept as ${aside}`)
  return {}
}

function writeConfig(cfg) {
  const file = configFile()
  fs.mkdirSync(path.dirname(file), { recursive: true })
  const tmp = scratch(file)
  fs.writeFileSync(tmp, JSON.stringify(cfg, null, 2))
  fs.renameSync(tmp, file)
}

const listed = (cfg) => (Array.isArray(cfg.libraries) ? cfg.libraries : []).filter((p) => typeof p === 'string' && p.trim())

// The registered vaults, the one opened last first. Whatever else an older
// build left in the file stays until the sidecar has moved it into a vault.
function known() {
  const cfg = readConfig()
  const last = typeof cfg.library_path === 'string' ? cfg.library_path : ''
  const libs = listed(cfg)
  if (last && !libs.some((p) => same(p, last))) libs.unshift(last)
  return { last, libraries: libs }
}

function register(root, { last = false } = {}) {
  const cfg = readConfig()
  const libs = listed(cfg)
  if (!libs.some((p) => same(p, root))) libs.push(root)
  writeConfig({ ...cfg, libraries: libs, ...(last ? { library_path: root } : {}) })
}

// An older build served `<app data>/library` whenever nothing was chosen, and
// never listed it. One that holds titles is the owner's library: it is listed
// (and opened, when nothing else is). One that holds nothing but the files the
// old build made for itself was never asked for, and goes.
async function adoptOldDefault() {
  const old = path.join(app.getPath('userData'), 'library')
  let names
  try { names = await fs.promises.readdir(old) } catch { return }
  const own = new Set(['index.db', 'index.db-wal', 'index.db-shm', 'index.db-journal', 'vault.json', 'health.json'])
  if (names.every((n) => own.has(n.toLowerCase()))) {
    await fs.promises.rm(old, { recursive: true, force: true }).catch(() => {})
    return
  }
  const { last, libraries } = known()
  if (!libraries.some((p) => same(p, old))) register(old, { last: !last })
}

// ---- does it answer, and is it a vault ----------------------------------------------

// One question per path at a time, and only a couple at all: a stat stuck on a
// dead drive holds one of the four threads Node does file work on until the
// network gives up, and piling more on stalls every file the shell touches.
const asking = new Map()
const MAX_ASKING = 2

function look(root) {
  let p = asking.get(root)
  if (!p) {
    if (asking.size >= MAX_ASKING) return Promise.resolve('silent')
    p = (async () => {
      for (const mark of [VAULT_FILE, 'index.db']) {
        try {
          await fs.promises.stat(path.join(root, mark))
          return 'vault'
        } catch (e) {
          if (e.code !== 'ENOENT' && e.code !== 'ENOTDIR') return 'unreachable'
        }
      }
      try {
        return (await fs.promises.stat(root)).isDirectory() ? 'folder' : 'missing'
      } catch (e) {
        return e.code === 'ENOENT' ? 'missing' : 'unreachable'
      }
    })().finally(() => asking.delete(root))
    asking.set(root, p)
  }
  return p
}

// 'vault' | 'folder' (there, not a vault) | 'missing' | 'unreachable' | 'silent' (no answer in time)
function ask(root, ms) {
  return Promise.race([look(root), sleep(ms).then(() => 'silent')])
}

// What a mapped drive letter is a name for: `Y:` is `\\192.168.8.146\anime`.
// Read from Windows' own list, which answers from memory even while the share is down.
function shareOf(root) {
  const abs = path.resolve(root)
  const unc = abs.match(/^(\\\\[^\\]+\\[^\\]+)/)
  if (unc) return Promise.resolve(unc[1])
  if (!WIN || !/^[A-Za-z]:/.test(abs)) return Promise.resolve('')
  const letter = abs.slice(0, 2).toUpperCase()
  return new Promise((resolve) => {
    execFile('net', ['use'], { timeout: 3000, windowsHide: true }, (err, out) => {
      if (err) return resolve('')
      const row = String(out).split(/\r?\n/).find((l) => l.toUpperCase().includes(` ${letter} `))
      resolve((row && (row.match(/(\\\\\S+)/) || [])[1]) || '')
    })
  })
}

// ---- creating one ---------------------------------------------------------------

const NOISE = new Set(['desktop.ini', 'thumbs.db', '.ds_store'])
const BAD_NAME = /[<>:"/\\|?*\x00-\x1f]|[. ]$/

// A folder that exists, as a place for a vault: 'empty' may become one;
// 'files' (with how many), 'vault' and 'unreachable' may not.
async function inspectFolder(target) {
  const got = await ask(target, 4000)
  if (got === 'vault') return { path: target, state: 'vault', files: 0 }
  if (got === 'missing') return { path: target, state: 'missing', files: 0 }
  if (got !== 'folder') return { path: target, state: 'unreachable', files: 0 }
  try {
    const files = (await within(4000, fs.promises.readdir(target), target)).filter((n) => !NOISE.has(n.toLowerCase())).length
    return { path: target, state: files ? 'files' : 'empty', files }
  } catch {
    return { path: target, state: 'unreachable', files: 0 }
  }
}

// Where a new vault would go, and whether it may: a new folder, or an empty
// one. A folder holding anything else is refused, never adopted.
async function inspectNew(parent, name) {
  const clean = String(name || '').trim()
  if (!parent) return { path: '', state: 'noparent', files: 0 }
  const target = path.join(parent, clean)
  if (!clean || BAD_NAME.test(clean)) return { path: target, state: 'badname', files: 0 }
  const got = await inspectFolder(target)
  if (got.state !== 'missing') return got
  const there = await ask(parent, 4000)
  return { path: target, state: there === 'folder' || there === 'vault' ? 'new' : there === 'missing' ? 'noparent' : 'unreachable', files: 0 }
}

const newId = () => crypto.randomUUID().replace(/-/g, '')

async function createVault(target) {
  try {
    await fs.promises.mkdir(target) // its parent was picked: it exists
  } catch (e) {
    if (e.code !== 'EEXIST') throw e
  }
  await writeAtomic(path.join(target, VAULT_FILE),
    JSON.stringify({ id: newId(), created: new Date().toISOString() }, null, 2))
}

// A vault an older build left with its index and no vault.json (that file came
// with the one-time passes, and a pass cut short never wrote it) is a vault all
// the same: it is given its identity before anything is written beside it.
async function ensureIdentity(root) {
  const file = path.join(root, VAULT_FILE)
  try {
    await within(8000, fs.promises.stat(file), root)
    return
  } catch (e) {
    if (e.code !== 'ENOENT') throw e
  }
  await within(8000, fs.promises.stat(path.join(root, 'index.db')), root) // not a vault: refused
  await within(8000, writeAtomic(file, JSON.stringify({ id: newId() }, null, 2)), root)
}

// ---- the lock: one app per vault ---------------------------------------------------

const lockFile = (root) => path.join(root, APP_DIR, 'lock.json')

async function readLock(root) {
  try {
    const raw = JSON.parse(await within(8000, fs.promises.readFile(lockFile(root), 'utf8'), root))
    return raw && typeof raw === 'object' ? raw : null
  } catch { return null }
}

function running(pid) {
  try {
    process.kill(Number(pid), 0)
    return true
  } catch (e) {
    return e.code === 'EPERM' // there, and someone else's
  }
}

// 'free' | 'mine' | 'left' (its app is gone) | 'held' (in use, seen lately).
// On this computer the process itself is asked: two builds of the app (or two
// users) are two processes with locks of their own.
function lockState(lock) {
  if (!lock) return 'free'
  if (lock.computer === os.hostname()) {
    if (lock.pid === process.pid) return 'mine'
    return running(lock.pid) ? 'held' : 'left'
  }
  const seen = Date.parse(lock.seen || '')
  return Number.isFinite(seen) && Date.now() - seen < LOCK_FRESH_MS ? 'held' : 'left'
}

const lockOf = (since) => ({ computer: os.hostname(), pid: process.pid, app: app.getVersion(), since,
  seen: new Date().toISOString() })

// Takes the vault and keeps saying so; returns what lets it go. A lock taken
// over by another computer ("Open anyway" there) is not fought for.
async function holdLock(root) {
  const since = new Date().toISOString()
  await ensureIdentity(root)
  await within(8000, fs.promises.mkdir(path.join(root, APP_DIR), { recursive: true }), root)
  await within(8000, writeAtomic(lockFile(root), JSON.stringify(lockOf(since), null, 2)), root)
  let writing = null
  const timer = setInterval(() => {
    if (writing) return // one at a time: on a drive that dropped, a write waits out the network
    writing = (async () => {
      const held = await readLock(root)
      if (held && lockState(held) !== 'mine') {
        clearInterval(timer)
        console.error(`the vault's lock was taken over by ${held.computer}`)
        return
      }
      await writeAtomic(lockFile(root), JSON.stringify(lockOf(since), null, 2))
    })().catch(() => {}).finally(() => { writing = null })
  }, HEARTBEAT_MS)
  return () => {
    clearInterval(timer)
    try {
      const lock = JSON.parse(fs.readFileSync(lockFile(root), 'utf8'))
      if (lockState(lock) === 'mine') fs.unlinkSync(lockFile(root))
    } catch { /* gone, or the drive is: the lock goes stale on its own */ }
  }
}

// ---- what an older build kept on this computer ----------------------------------------

// The browser profile's folders — sign-ins and site data, then the caches — as
// Chromium lays them out; the ad blocker's files; transfers being written.
const SITE_DATA = ['Network', 'Local Storage', 'IndexedDB', 'Session Storage', 'Service Worker', 'WebStorage',
  'File System', 'blob_storage', 'SharedStorage', 'shared_proto_db', 'Shared Dictionary', 'databases',
  'Preferences', 'VideoDecodeStats', 'Cookies', 'Cookies-journal', 'Trust Tokens', 'Trust Tokens-journal']
const CACHES = ['Cache', 'Code Cache', 'GPUCache', 'DawnCache', 'DawnGraphiteCache', 'DawnWebGPUCache']
const ADBLOCK = { 'adblock.json': 'prefs.json', 'adblock-engine.bin': 'engine.bin', 'adblock-engine.json': 'engine.json' }
// What an older build left that no vault needs: the shell's own port, Chromium's
// profile-wide files, logs, and the sidecar's previews (`cache/thumbs` — on
// Windows the same folder as Chromium's `Cache`, whose `thumbs` is not copied).
const LEFTOVERS = ['shell.json', 'Local State', 'longbox.log', 'Crashpad', 'logs', 'GrShaderCache', 'ShaderCache',
  'GraphiteDawnCache', 'Dictionaries', 'DIPS', 'DIPS-journal', 'TransportSecurity', 'Network Persistent State',
  'cache']
const LEGACY = new Set([...SITE_DATA, ...CACHES, ...Object.keys(ADBLOCK), 'partial-downloads', ...LEFTOVERS].map(fold))

async function sizeOf(p, skip) {
  const st = await fs.promises.lstat(p)
  if (!st.isDirectory()) return st.size
  let n = 0
  for (const e of await fs.promises.readdir(p)) if (!skip(e)) n += await sizeOf(path.join(p, e), skip)
  return n
}

async function copyInto(src, dst, onBytes, skip) {
  const st = await fs.promises.lstat(src)
  if (st.isDirectory()) {
    await fs.promises.mkdir(dst, { recursive: true })
    for (const e of await fs.promises.readdir(src)) if (!skip(e)) await copyInto(path.join(src, e), path.join(dst, e), onBytes, skip)
  } else if (st.isFile()) {
    await fs.promises.copyFile(src, dst)
    onBytes(st.size)
  }
}

// What moves from this computer into the vault, part by part — or null when
// there is nothing. The browser's data goes into the FIRST vault this build
// opens; one that already has a profile of its own keeps it, and the old one is
// dropped.
async function legacyPlan(root) {
  const home = app.getPath('userData')
  let names
  try { names = await fs.promises.readdir(home) } catch { return null }
  const left = names.filter((n) => LEGACY.has(fold(n)))
  if (!left.length) return null
  const has = (n) => left.find((x) => fold(x) === fold(n))
  const browser = path.join(root, APP_DIR, 'browser')
  const staged = `${browser}.moving`
  const hasOwn = await within(8000, fs.promises.stat(browser).then(() => true, (e) => {
    if (e.code === 'ENOENT') return false
    throw e
  }), root)
  // the previews the sidecar kept under `cache/thumbs` are not the browser's
  const skip = (n) => fold(n) === fold('thumbs')
  const part = async (what, pairs) => {
    const items = []
    for (const [src, dst] of pairs) {
      try { items.push({ src, dst, size: await sizeOf(src, skip) }) } catch { /* gone meanwhile */ }
    }
    return { what, items, total: items.reduce((n, i) => n + i.size, 0), done: 0 }
  }
  const profile = (list) => (hasOwn ? [] : list.map(has).filter(Boolean).map((n) => [path.join(home, n), path.join(staged, n)]))
  const parts = [
    await part('Browser sign-ins and site data', profile(SITE_DATA)),
    await part('Browser cache', profile(CACHES)),
    await part('Ad-block lists and unfinished downloads', [
      ...Object.entries(ADBLOCK).filter(([n]) => has(n)).map(([n, to]) => [path.join(home, has(n)), path.join(root, APP_DIR, 'adblock', to)]),
      ...(has('partial-downloads') ? [[path.join(home, has('partial-downloads')), path.join(root, APP_DIR, 'partial-downloads')]] : []),
    ]),
  ]
  let oldPort = 0
  try { oldPort = Number(JSON.parse(fs.readFileSync(path.join(home, has('shell.json') || 'shell.json'), 'utf8')).port) || 0 } catch { /* none */ }
  return { root, home, parts, staged, browser, skip, drop: left, oldPort }
}

// Copy — nothing is deleted here (dropLegacy does that, once the window's old
// preferences are safe too). The browser's folders are copied beside their
// place and renamed into it as soon as they are all there, so a half-copied
// profile is never taken for the vault's own, and a later part that fails does
// not cost the profile. A copy cut short is started over by the next launch.
async function copyLegacy(plan, onProgress) {
  await fs.promises.rm(plan.staged, { recursive: true, force: true })
  for (const [i, p] of plan.parts.entries()) {
    p.done = 0
    for (const item of p.items) {
      await fs.promises.mkdir(path.dirname(item.dst), { recursive: true })
      await copyInto(item.src, item.dst, (n) => { p.done += n; onProgress() }, plan.skip)
    }
    p.done = p.total
    onProgress()
    if (i === 1 && fs.existsSync(plan.staged)) await fs.promises.rename(plan.staged, plan.browser)
  }
}

// Only what is known to be an older build's — never whatever else is there.
async function dropLegacy(plan) {
  for (const n of plan.drop) await fs.promises.rm(path.join(plan.home, n), { recursive: true, force: true })
}

module.exports = {
  APP_DIR, CACHES, adoptOldDefault, ask, copyInto, copyLegacy, createVault, dropLegacy, fold, holdLock, inspectFolder,
  inspectNew, known, legacyPlan, lockState, nameOf, readLock, register, same, shareOf, sizeOf, sleep, writeAtomic,
}
