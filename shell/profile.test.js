'use strict'
// node --test "shell/*.test.js" — where a vault's sign-ins live, and the move
// between the vault and this computer (design/sign-ins.md).
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const profile = require('./profile')

const ID = 'a1b2c3d4e5f60718'

function setup(t) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'lb-profile-'))
  t.after(() => fs.rmSync(base, { recursive: true, force: true }))
  const root = path.join(base, 'vault')
  const home = path.join(base, 'home')
  fs.mkdirSync(path.join(root, '.longbox'), { recursive: true })
  fs.mkdirSync(home)
  fs.writeFileSync(path.join(root, 'vault.json'), JSON.stringify({ id: ID }))
  return { root, home }
}

// a profile as Chromium lays one out: sign-ins and site data, and caches
function profileAt(dir) {
  fs.mkdirSync(path.join(dir, 'Network'), { recursive: true })
  fs.writeFileSync(path.join(dir, 'Network', 'Cookies'), 'sid=1')
  fs.mkdirSync(path.join(dir, 'Local Storage', 'leveldb'), { recursive: true })
  fs.writeFileSync(path.join(dir, 'Local Storage', 'leveldb', '000001.log'), 'token')
  fs.writeFileSync(path.join(dir, 'Preferences'), '{}')
  fs.mkdirSync(path.join(dir, 'Cache', 'Cache_Data'), { recursive: true })
  fs.writeFileSync(path.join(dir, 'Cache', 'Cache_Data', 'f_000001'), 'x'.repeat(4096))
  fs.mkdirSync(path.join(dir, 'Code Cache', 'js'), { recursive: true })
  fs.writeFileSync(path.join(dir, 'Code Cache', 'js', 'index'), 'y'.repeat(1024))
}

test('a vault that has not answered says so; an answer is kept', async (t) => {
  const { root } = setup(t)
  assert.deepEqual(await profile.readChoice(root), { keep: null, writable: true })
  await profile.writeChoice(root, 'computer')
  assert.deepEqual(await profile.readChoice(root), { keep: 'computer', writable: true })
  await profile.writeChoice(root, 'vault')
  assert.equal((await profile.readChoice(root)).keep, 'vault')
  await assert.rejects(profile.writeChoice(root, 'cloud'))
})

test('a file that does not read is no answer; a newer build\'s is read and never written', async (t) => {
  const { root } = setup(t)
  const file = path.join(root, '.longbox', 'browser.json')
  fs.writeFileSync(file, '{ not json')
  assert.deepEqual(await profile.readChoice(root), { keep: null, writable: true })
  fs.writeFileSync(file, JSON.stringify({ schema: 9, keep: 'computer', more: 1 }))
  assert.deepEqual(await profile.readChoice(root), { keep: 'computer', writable: false })
  await assert.rejects(profile.writeChoice(root, 'vault'))
  assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).schema, 9)
})

test('the vault id names the profile; a vault with no usable id is refused', async (t) => {
  const { root, home } = setup(t)
  assert.equal(await profile.vaultId(root), ID)
  assert.equal(profile.localDir(home, ID), path.join(home, 'browsers', ID))
  fs.writeFileSync(path.join(root, 'vault.json'), JSON.stringify({ id: '../../x' }))
  await assert.rejects(profile.vaultId(root))
})

test('what has to happen, for every place the profile can be', async (t) => {
  const { root, home } = setup(t)
  const ask = (keep) => profile.plan({ root, home, id: ID, keep })
  const inVault = profile.vaultDir(root)
  const here = profile.localDir(home, ID)
  assert.equal(await ask('vault'), null)                       // nothing anywhere
  assert.equal(await ask('computer'), null)
  profileAt(inVault)
  assert.equal(await ask('vault'), null)                       // where it belongs
  assert.deepEqual(await ask('computer'), { kind: 'move', from: inVault, to: here, toward: 'computer' })
  profileAt(here)
  assert.deepEqual(await ask('vault'), { kind: 'drop', dir: here, where: 'computer' })     // a computer's leftover
  assert.deepEqual(await ask('computer'), { kind: 'drop', dir: inVault, where: 'vault' }) // one left in the vault
  fs.rmSync(inVault, { recursive: true })
  assert.deepEqual(await ask('vault'), { kind: 'move', from: here, to: inVault, toward: 'vault' })
  assert.equal(await ask('computer'), null)
})

test('a move carries sign-ins and site data, and leaves nothing where it was', async (t) => {
  const { root, home } = setup(t)
  const inVault = profile.vaultDir(root)
  profileAt(inVault)
  const before = await profile.sizes(inVault)
  assert.ok(before.data > 0 && before.cache === 4096 + 1024)
  const p = await profile.plan({ root, home, id: ID, keep: 'computer' })
  const steps = []
  await profile.move(p, (step, done, total) => steps.push([step, done, total]))
  const here = profile.localDir(home, ID)
  assert.equal(fs.readFileSync(path.join(here, 'Network', 'Cookies'), 'utf8'), 'sid=1')
  assert.equal(fs.readFileSync(path.join(here, 'Local Storage', 'leveldb', '000001.log'), 'utf8'), 'token')
  assert.equal(fs.existsSync(path.join(here, 'Cache')), false)       // caches are not carried
  assert.equal(fs.existsSync(path.join(here, 'Code Cache')), false)
  assert.equal(fs.existsSync(inVault), false)                        // the old profile is gone, caches and all
  assert.equal(fs.existsSync(`${here}.moving`), false)
  assert.deepEqual(steps.at(-1), ['remove', before.data + before.cache, before.data + before.cache])
  assert.ok(steps.some(([s, d, total]) => s === 'copy' && d === total && total === before.data))
  // and back again
  await profile.move(await profile.plan({ root, home, id: ID, keep: 'vault' }))
  assert.equal(fs.readFileSync(path.join(inVault, 'Network', 'Cookies'), 'utf8'), 'sid=1')
  assert.equal(fs.existsSync(here), false)
})

test('a copy that fails leaves the old profile whole, and the next move starts over', async (t) => {
  const { root, home } = setup(t)
  const inVault = profile.vaultDir(root)
  profileAt(inVault)
  const p = await profile.plan({ root, home, id: ID, keep: 'computer' })
  let calls = 0
  await assert.rejects(profile.move(p, (step) => {
    if (step === 'copy' && ++calls === 2) throw new Error('the disk is full')
  }))
  assert.equal(fs.readFileSync(path.join(inVault, 'Network', 'Cookies'), 'utf8'), 'sid=1')
  assert.equal(fs.existsSync(profile.localDir(home, ID)), false)     // no half profile in its place
  await profile.move(p)                                              // the leftover copy is cleared first
  assert.equal(fs.existsSync(path.join(profile.localDir(home, ID), 'Network', 'Cookies')), true)
  assert.equal(fs.existsSync(inVault), false)
})

test('forgetting a vault removes this computer\'s sign-ins for it, and only those', async (t) => {
  const { root, home } = setup(t)
  const other = 'ffffeeeeddddcccc'
  profileAt(profile.localDir(home, ID))
  profileAt(profile.localDir(home, other))
  await profile.remember(home, ID, root)
  await profile.remember(home, other, path.join(path.dirname(root), 'elsewhere'))
  assert.equal(await profile.forget(home, root), 1)
  assert.equal(fs.existsSync(profile.localDir(home, ID)), false)
  assert.equal(fs.existsSync(profile.localDir(home, other)), true)
  assert.equal(await profile.forget(home, root), 0)
})
