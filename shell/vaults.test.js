'use strict'
// node --test "shell/*.test.js" — the vault's checks before anything opens it:
// what answers, what is a vault, where a new one may go, and whose lock it is.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const Module = require('node:module')
const os = require('node:os')
const path = require('node:path')
const vaults = require('./vaults')

function base(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lb-vaults-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  return dir
}

test('a vault is a folder with its vault.json — or an older build\'s index', async (t) => {
  const dir = base(t)
  const v = path.join(dir, 'v')
  fs.mkdirSync(v)
  assert.equal(await vaults.ask(v, 4000), 'folder')
  fs.writeFileSync(path.join(v, 'index.db'), '')
  assert.equal(await vaults.ask(v, 4000), 'vault')
  const w = path.join(dir, 'w')
  fs.mkdirSync(w)
  fs.writeFileSync(path.join(w, 'vault.json'), '{}')
  assert.equal(await vaults.ask(w, 4000), 'vault')
  assert.equal(await vaults.ask(path.join(dir, 'nowhere'), 4000), 'missing')
  fs.writeFileSync(path.join(dir, 'a-file'), 'x')
  assert.equal(await vaults.ask(path.join(dir, 'a-file'), 4000), 'missing')
})

test('a folder may become a vault only when it is empty — the system\'s own files aside', async (t) => {
  const dir = base(t)
  const f = path.join(dir, 'f')
  fs.mkdirSync(f)
  fs.writeFileSync(path.join(f, 'desktop.ini'), '')
  fs.writeFileSync(path.join(f, 'Thumbs.db'), '')
  assert.equal((await vaults.inspectFolder(f)).state, 'empty')
  fs.writeFileSync(path.join(f, 'notes.txt'), 'mine')
  assert.deepEqual(await vaults.inspectFolder(f), { path: f, state: 'files', files: 1 })
  fs.writeFileSync(path.join(f, 'vault.json'), '{}')
  assert.equal((await vaults.inspectFolder(f)).state, 'vault')
})

test('where a new vault would go', async (t) => {
  const dir = base(t)
  assert.equal((await vaults.inspectNew(dir, 'Library')).state, 'new')
  for (const bad of ['', '  ', 'a/b', 'a\\b', 'what?', 'ends.']) {
    assert.equal((await vaults.inspectNew(dir, bad)).state, 'badname', JSON.stringify(bad))
  }
  assert.equal((await vaults.inspectNew(dir, '  Library  ')).path, path.join(dir, 'Library')) // trimmed
  assert.equal((await vaults.inspectNew('', 'Library')).state, 'noparent')
  assert.equal((await vaults.inspectNew(path.join(dir, 'gone'), 'Library')).state, 'noparent')
  fs.mkdirSync(path.join(dir, 'Taken'))
  fs.writeFileSync(path.join(dir, 'Taken', 'x.cbz'), '')
  assert.equal((await vaults.inspectNew(dir, 'Taken')).state, 'files')
})

test('a vault is made with an id of its own', async (t) => {
  const dir = base(t)
  const v = path.join(dir, 'New')
  await vaults.createVault(v)
  const doc = JSON.parse(fs.readFileSync(path.join(v, 'vault.json'), 'utf8'))
  assert.match(doc.id, /^[0-9a-f]{32}$/)
  assert.ok(Date.parse(doc.created))
  await vaults.createVault(path.join(dir, 'Other'))
  const other = JSON.parse(fs.readFileSync(path.join(dir, 'Other', 'vault.json'), 'utf8'))
  assert.notEqual(other.id, doc.id)
})

test('a vault keeps its id; one without is given one, and a copy is given a new one — the rest of vault.json kept', async (t) => {
  const v = path.join(base(t), 'v')
  fs.mkdirSync(v)
  const file = path.join(v, 'vault.json')
  fs.writeFileSync(file, JSON.stringify({ id: 'kept-as-it-is', zipNormalized: 2 }))
  assert.equal(await vaults.ensureIdentity(v), 'kept-as-it-is')
  fs.writeFileSync(file, JSON.stringify({ id: '', zipNormalized: 2 }))
  const given = await vaults.ensureIdentity(v)
  assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), { id: given, zipNormalized: 2 })
  const renewed = await vaults.renewIdentity(v)
  assert.notEqual(renewed, given)
  assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), { id: renewed, zipNormalized: 2 })
})

test('whose lock it is', () => {
  const host = os.hostname()
  const now = new Date().toISOString()
  assert.equal(vaults.lockState(null), 'free')
  assert.equal(vaults.lockState({ computer: host, pid: process.pid, seen: now }), 'mine')
  // this computer: the process itself is asked
  assert.equal(vaults.lockState({ computer: host, pid: process.ppid, seen: now }), 'held')
  assert.equal(vaults.lockState({ computer: host, pid: 2 ** 22 + 7, seen: now }), 'left')
  // another computer: how lately it said so
  assert.equal(vaults.lockState({ computer: `${host}-elsewhere`, pid: 1, seen: now }), 'held')
  const stale = new Date(Date.now() - 10 * 60 * 1000).toISOString()
  assert.equal(vaults.lockState({ computer: `${host}-elsewhere`, pid: 1, seen: stale }), 'left')
  assert.equal(vaults.lockState({ computer: `${host}-elsewhere`, pid: 1, seen: 'never' }), 'left')
})

test('a lock that does not read is no lock', async (t) => {
  const dir = base(t)
  fs.mkdirSync(path.join(dir, '.longbox'))
  assert.equal(await vaults.readLock(dir), null)
  fs.writeFileSync(path.join(dir, '.longbox', 'lock.json'), '{ torn')
  assert.equal(await vaults.readLock(dir), null)
  fs.writeFileSync(path.join(dir, '.longbox', 'lock.json'), JSON.stringify({ computer: 'pc', pid: 1 }))
  assert.deepEqual(await vaults.readLock(dir), { computer: 'pc', pid: 1 })
})

// legacyPlan asks Electron where the app's folder is: here, one of the test's.
// Electron itself need not be installed (CI's shell job does not install it).
function appFolder(t, home) {
  const resolve = Module._resolveFilename
  const id = path.join(__dirname, 'electron.stub')
  Module._resolveFilename = function (request, ...rest) {
    return request === 'electron' ? id : resolve.call(this, request, ...rest)
  }
  require.cache[id] = { id, filename: id, loaded: true, exports: { app: { getPath: () => home } } }
  t.after(() => {
    Module._resolveFilename = resolve
    delete require.cache[id]
  })
}

test('the app\'s folder as this build keeps it is no older build\'s; what an older one left is moved or dropped', async (t) => {
  const dir = base(t)
  const home = path.join(dir, 'home'), root = path.join(dir, 'v')
  fs.mkdirSync(path.join(home, 'browsers', 'v-1'), { recursive: true })
  fs.mkdirSync(path.join(root, '.longbox'), { recursive: true })
  appFolder(t, home)
  for (const n of ['config.json', 'lockfile', path.join('browsers', 'v-1.json')]) fs.writeFileSync(path.join(home, n), '{}')
  const profile = path.join(home, 'browsers', 'v-1')
  assert.equal(await vaults.legacyPlan(root, profile), null)
  // an older build's log: dropped, nothing to copy — so nothing to show
  fs.writeFileSync(path.join(home, 'longbox.log'), 'x')
  fs.writeFileSync(path.join(home, 'longbox.log.1'), 'x')
  const logs = await vaults.legacyPlan(root, profile)
  assert.deepEqual(logs.drop.sort(), ['longbox.log', 'longbox.log.1'])
  assert.equal(logs.parts.some((p) => p.items.length), false)
  // its unfinished downloads: copied into the vault
  fs.mkdirSync(path.join(home, 'partial-downloads'))
  fs.writeFileSync(path.join(home, 'partial-downloads', 'a.part'), 'abc')
  const plan = await vaults.legacyPlan(root, profile)
  assert.deepEqual(plan.parts.map((p) => p.total), [0, 0, 3])
})
