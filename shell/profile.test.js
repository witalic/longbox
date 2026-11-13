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
  return { base, root, home }
}

// a profile as Chromium lays one out: sign-ins and site data, and caches
function profileAt(dir, who = 'sid=1') {
  fs.mkdirSync(path.join(dir, 'Network'), { recursive: true })
  fs.writeFileSync(path.join(dir, 'Network', 'Cookies'), who)
  fs.mkdirSync(path.join(dir, 'Local Storage', 'leveldb'), { recursive: true })
  fs.writeFileSync(path.join(dir, 'Local Storage', 'leveldb', '000001.log'), 'token')
  fs.writeFileSync(path.join(dir, 'Preferences'), '{}')
  fs.mkdirSync(path.join(dir, 'Cache', 'Cache_Data'), { recursive: true })
  fs.writeFileSync(path.join(dir, 'Cache', 'Cache_Data', 'f_000001'), 'x'.repeat(4096))
  fs.mkdirSync(path.join(dir, 'Code Cache', 'js'), { recursive: true })
  fs.writeFileSync(path.join(dir, 'Code Cache', 'js', 'index'), 'y'.repeat(1024))
}
const cookies = (dir) => fs.readFileSync(path.join(dir, 'Network', 'Cookies'), 'utf8')
const keepOf = async (root) => (await profile.readChoice(root)).keep

// What a start does, step by step, as the launch window has it done: the
// steps taken, by name.
async function settle({ root, home, id = ID, owe = null, mine = true }) {
  let keep = await keepOf(root)
  const did = []
  for (let step; (step = await profile.next({ root, home, id, keep, owe, mine }));) {
    did.push(step.kind === 'move' ? `move ${step.toward}` : step.kind === 'answer' ? `answer ${step.keep}`
      : `drop ${step.where}${step.staged ? ' staged' : ''}`)
    keep = await profile.take(step, { root, keep })
    if (did.length > 8) throw new Error(`never settles: ${did}`)
  }
  return did
}

test('a vault that has not answered says so; an answer is kept, each one a revision of its own', async (t) => {
  const { root } = setup(t)
  assert.deepEqual(await profile.readChoice(root), { keep: null, rev: '', writable: true })
  const one = await profile.writeChoice(root, 'computer')
  assert.deepEqual(await profile.readChoice(root), { keep: 'computer', rev: one, writable: true })
  const two = await profile.writeChoice(root, 'computer')
  assert.notEqual(two, one)
  await profile.writeChoice(root, 'vault')
  assert.equal(await keepOf(root), 'vault')
  await assert.rejects(profile.writeChoice(root, 'cloud'))
})

test('a change owed is made only against the answer it was asked of', () => {
  const choice = { keep: 'vault', rev: 'r2', writable: true }
  assert.deepEqual(profile.owed(null, choice), { owe: null, stale: false })
  assert.deepEqual(profile.owed({ path: 'x', owe: null }, choice), { owe: null, stale: false })
  assert.deepEqual(profile.owed({ path: 'x', owe: 'computer', against: 'r2' }, choice), { owe: 'computer', stale: false })
  // asked against r1; another computer has written the answer since: the latest answer stands
  assert.deepEqual(profile.owed({ path: 'x', owe: 'computer', against: 'r1' }, choice), { owe: null, stale: true })
  assert.deepEqual(profile.owed({ path: 'x', owe: 'vault', against: 'r1' }, choice), { owe: null, stale: false })
  // a newer build's answer is never changed
  assert.deepEqual(profile.owed({ path: 'x', owe: 'computer', against: 'r2' }, { ...choice, writable: false }),
    { owe: null, stale: false })
})

test('a file that does not parse is no answer, one that does not read is no answer at all; a newer build\'s is read and never written', async (t) => {
  const { root } = setup(t)
  const file = path.join(root, '.longbox', 'browser.json')
  fs.writeFileSync(file, '{ not json')
  assert.deepEqual(await profile.readChoice(root), { keep: null, rev: '', writable: true })
  fs.writeFileSync(file, JSON.stringify({ schema: 9, keep: 'computer', rev: 'n', more: 1 }))
  assert.deepEqual(await profile.readChoice(root), { keep: 'computer', rev: 'n', writable: false })
  await assert.rejects(profile.writeChoice(root, 'vault'))
  assert.equal(JSON.parse(fs.readFileSync(file, 'utf8')).schema, 9)
  fs.rmSync(file)
  fs.mkdirSync(file) // a read that fails, not a file that is not there
  await assert.rejects(profile.readChoice(root))
})

test('the vault id names the profile — given to a vault from before ids, hashed when edited by hand', async (t) => {
  const { root, home } = setup(t)
  const file = path.join(root, 'vault.json')
  assert.equal(await profile.vaultId(root), ID)
  assert.equal(profile.localDir(home, ID), path.join(home, 'browsers', ID))
  fs.writeFileSync(file, JSON.stringify({ id: '../../x' }))
  const hashed = await profile.vaultId(root)
  assert.match(hashed, /^[0-9a-f]{32}$/)
  assert.equal(await profile.vaultId(root), hashed)
  // a vault.json from before 3.0 holds the passes' markers alone: it is given an id, the markers kept
  fs.writeFileSync(file, JSON.stringify({ zipNormalized: 1 }))
  const given = await profile.vaultId(root)
  assert.match(given, /^[0-9a-f]{32}$/)
  assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), { zipNormalized: 1, id: given })
  assert.equal(await profile.vaultId(root), given)
  for (const torn of ['{}', '{ "zipNorm', '[]']) {
    fs.writeFileSync(file, torn)
    assert.match(await profile.vaultId(root), /^[0-9a-f]{32}$/)
  }
  fs.rmSync(file)
  await assert.rejects(profile.vaultId(root)) // neither vault.json nor an index: not a vault
  fs.writeFileSync(path.join(root, 'index.db'), '')
  assert.match(await profile.vaultId(root), /^[0-9a-f]{32}$/)
})

test('what is left to do, for every place the profile can be', async (t) => {
  const { root, home } = setup(t)
  const ask = (keep, owe = null) => profile.next({ root, home, id: ID, keep, owe })
  const inVault = profile.vaultDir(root)
  const here = profile.localDir(home, ID)
  assert.equal(await ask('vault'), null)                        // nothing anywhere
  assert.equal(await ask('computer'), null)
  profileAt(inVault)
  assert.equal(await ask('vault'), null)                        // where it belongs
  // under "each computer" a profile in the vault is never adopted: it is no one's
  assert.deepEqual(await ask('computer'), { kind: 'drop', dir: inVault, where: 'vault' })
  // a change this computer owes: carried from where the answer says it is
  assert.deepEqual(await ask('vault', 'computer'), { kind: 'move', from: inVault, to: here, toward: 'computer' })
  assert.deepEqual(await ask(null, 'computer'), { kind: 'move', from: inVault, to: here, toward: 'computer' })
  assert.deepEqual(await ask(null, 'vault'), { kind: 'answer', keep: 'vault' })
  profileAt(here)
  assert.deepEqual(await ask('vault'), { kind: 'drop', dir: here, where: 'computer' })      // another computer's answer
  assert.deepEqual(await ask('computer', 'vault'), { kind: 'move', from: here, to: inVault, toward: 'vault' })
  fs.rmSync(inVault, { recursive: true })
  assert.equal(await ask('computer'), null)
  // owed, with nothing to carry: what sits at the new place is no one's
  assert.deepEqual(await ask('vault', 'computer'), { kind: 'drop', dir: here, where: 'computer' })
  fs.rmSync(here, { recursive: true })
  assert.deepEqual(await ask('vault', 'computer'), { kind: 'answer', keep: 'computer' })
  // a copy cut short, left beside either place
  fs.mkdirSync(`${inVault}.moving`)
  assert.deepEqual(await ask('vault'), { kind: 'drop', dir: `${inVault}.moving`, where: 'vault', staged: true })
})

test('a move carries sign-ins and site data, answers only once they are in place, and leaves nothing where it was', async (t) => {
  const { root, home } = setup(t)
  const inVault = profile.vaultDir(root)
  const here = profile.localDir(home, ID)
  profileAt(inVault)
  await profile.writeChoice(root, 'vault')
  const before = await profile.sizes(inVault)
  assert.ok(before.data > 0 && before.cache === 4096 + 1024)
  const seen = []
  const step = await profile.next({ root, home, id: ID, keep: 'vault', owe: 'computer' })
  await profile.move(step, (done, total, cache) => {
    seen.push([done, total, cache])
    assert.equal(fs.existsSync(here), false) // nothing at the new place until it is whole
  })
  assert.equal(await keepOf(root), 'vault')  // the move alone does not answer
  assert.ok(seen.some(([d, total, cache]) => d === total && total === before.data && cache === before.cache))
  assert.equal(cookies(here), 'sid=1')
  assert.equal(fs.readFileSync(path.join(here, 'Local Storage', 'leveldb', '000001.log'), 'utf8'), 'token')
  assert.equal(fs.existsSync(path.join(here, 'Cache')), false)       // caches are not carried
  assert.equal(fs.existsSync(path.join(here, 'Code Cache')), false)
  assert.equal(fs.existsSync(`${here}.moving`), false)
  assert.deepEqual(await settle({ root, home, owe: 'computer' }), ['move computer', 'drop vault'])
  assert.equal(await keepOf(root), 'computer')
  assert.equal(cookies(here), 'sid=1')
  assert.equal(fs.existsSync(inVault), false)                        // the old profile is gone, caches and all
  // and back again
  assert.deepEqual(await settle({ root, home, owe: 'vault' }), ['move vault', 'drop computer'])
  assert.equal(await keepOf(root), 'vault')
  assert.equal(cookies(inVault), 'sid=1')
  assert.equal(fs.existsSync(here), false)
  assert.deepEqual(await settle({ root, home, owe: 'vault' }), [])  // owed and done: nothing more
})

test('a move cut short anywhere is carried on by the next start, with nothing lost', async (t) => {
  const { root, home } = setup(t)
  const inVault = profile.vaultDir(root)
  const here = profile.localDir(home, ID)
  profileAt(inVault)
  await profile.writeChoice(root, 'vault')
  // the copy fails: the profile in use is whole, and nothing half is in place
  let calls = 0
  const step = await profile.next({ root, home, id: ID, keep: 'vault', owe: 'computer' })
  await assert.rejects(profile.move(step, () => { if (++calls === 2) throw new Error('the disk is full') }))
  assert.equal(cookies(inVault), 'sid=1')
  assert.equal(fs.existsSync(here), false)
  assert.equal(await keepOf(root), 'vault')
  // cut off after the rename, before the answer: both profiles there, the vault's still the one
  await profile.move(step)
  fs.writeFileSync(path.join(inVault, 'Network', 'Cookies'), 'sid=2') // used meanwhile by another computer
  assert.deepEqual(await settle({ root, home, owe: 'computer' }), ['move computer', 'drop vault'])
  assert.equal(cookies(here), 'sid=2')
  // cut off after the answer, before the old one went: it goes, with no word needed
  profileAt(inVault)
  assert.deepEqual(await settle({ root, home, owe: 'computer' }), ['drop vault'])
  assert.equal(cookies(here), 'sid=2')
})

test('two computers, one vault: a change one of them owes is that computer\'s, whoever opens the vault first', async (t) => {
  const { base, root } = setup(t)
  const homeA = path.join(base, 'A')
  const homeB = path.join(base, 'B')
  const inVault = profile.vaultDir(root)
  profileAt(inVault, 'vault')
  await profile.writeChoice(root, 'vault')
  // A asks for "on this computer", next start; B opens first and acts on the vault's answer as it is
  assert.deepEqual(await settle({ root, home: homeB }), [])
  assert.equal(cookies(inVault), 'vault')
  assert.deepEqual(await settle({ root, home: homeA, owe: 'computer' }), ['move computer', 'drop vault'])
  assert.equal(cookies(profile.localDir(homeA, ID)), 'vault')
  // B now starts with an empty profile of its own; it signs in
  assert.deepEqual(await settle({ root, home: homeB }), [])
  profileAt(profile.localDir(homeB, ID), 'B')
  // A takes the vault back to "in the vault" and at once to "on this computer" again, next start;
  // B opens in between: B's own copy goes (D1), and A's sign-ins are not lost
  assert.deepEqual(await settle({ root, home: homeA, owe: 'vault' }), ['move vault', 'drop computer'])
  assert.deepEqual(await settle({ root, home: homeB }), ['drop computer'])
  assert.equal(fs.existsSync(profile.localDir(homeB, ID)), false)
  assert.deepEqual(await settle({ root, home: homeA, owe: 'computer' }), ['move computer', 'drop vault'])
  assert.equal(cookies(profile.localDir(homeA, ID)), 'vault')
  // an older build on B leaves a profile in the vault meanwhile: A's move into the vault replaces it
  profileAt(inVault, 'old build')
  assert.deepEqual(await settle({ root, home: homeA, owe: 'vault' }), ['move vault', 'drop computer'])
  assert.equal(cookies(inVault), 'vault')
})

test('this computer\'s profile is the vault\'s — unless another folder with the same id may own it', async (t) => {
  const { base, root } = setup(t)
  const mark = (at) => ({ path: at, owe: null, against: '' })
  assert.equal(await profile.mine(root, null, ID), true)
  assert.equal(await profile.mine(root, mark(root), ID), true)
  assert.equal(await profile.mine(root, mark(path.join(base, 'gone')), ID), true)           // the vault moved
  const copy = path.join(base, 'copy')
  fs.mkdirSync(path.join(copy, '.longbox'), { recursive: true })
  fs.writeFileSync(path.join(copy, 'vault.json'), JSON.stringify({ id: ID }))
  assert.equal(await profile.mine(root, mark(copy), ID), false)                             // a copy
  fs.writeFileSync(path.join(copy, 'vault.json'), JSON.stringify({ id: 'ffffeeeeddddcccc' }))
  assert.equal(await profile.mine(root, mark(copy), ID), true)                              // another vault there now
  fs.rmSync(path.join(copy, 'vault.json'))
  fs.mkdirSync(path.join(copy, 'vault.json'))
  assert.equal(await profile.mine(root, mark(copy), ID), false)                             // cannot be asked: not taken
  const link = path.join(base, 'link')
  fs.symlinkSync(root, link, 'junction')
  assert.equal(await profile.mine(root, mark(link), ID), true)                              // the same folder
  assert.deepEqual(fs.readdirSync(path.join(root, '.longbox')), [])                         // the probe is gone
  fs.writeFileSync(path.join(root, '.longbox', 'probe-0a1b2c'), '')
  fs.writeFileSync(path.join(root, '.longbox', 'lock.json'), '{}')
  await profile.sweepProbes(root)
  assert.deepEqual(fs.readdirSync(path.join(root, '.longbox')), ['lock.json'])
})

test('a profile that may be another folder\'s is never removed or replaced — at most copied', async (t) => {
  const { root, home } = setup(t)
  const inVault = profile.vaultDir(root)
  const here = profile.localDir(home, ID)
  profileAt(here, 'the copy\'s')
  await profile.writeChoice(root, 'vault')
  assert.deepEqual(await settle({ root, home, mine: false }), [])                          // no D1 for it
  assert.equal(cookies(here), 'the copy\'s')
  await assert.rejects(profile.next({ root, home, id: ID, keep: 'vault', owe: 'computer', mine: false }))
  // a vault sharing it (both keep their sign-ins on each computer) takes a copy into the vault, and leaves it
  await profile.writeChoice(root, 'computer')
  assert.deepEqual(await settle({ root, home, owe: 'vault', mine: false }), ['move vault'])
  assert.equal(cookies(inVault), 'the copy\'s')
  assert.equal(cookies(here), 'the copy\'s')
})

test('forgetting a vault removes this computer\'s sign-ins for it, the change it owed, and only those', async (t) => {
  const { base, root, home } = setup(t)
  const other = 'ffffeeeeddddcccc'
  const owed = '0123456789abcdef'
  profileAt(profile.localDir(home, ID))
  fs.mkdirSync(`${profile.localDir(home, ID)}.moving`)
  profileAt(profile.localDir(home, other))
  await profile.writeMarker(home, ID, { path: root })
  await profile.writeMarker(home, other, { path: path.join(base, 'elsewhere') })
  await profile.writeMarker(home, owed, { path: root, owe: 'computer', against: 'r1' })
  assert.deepEqual(await profile.readMarker(home, owed), { path: path.resolve(root), owe: 'computer', against: 'r1' })
  // stray names never reach outside a profile's own folder
  fs.writeFileSync(path.join(home, 'browsers', '.json'), JSON.stringify({ path: root }))
  fs.writeFileSync(path.join(home, 'browsers', '..json'), JSON.stringify({ path: root }))
  assert.equal(await profile.forget(home, root), 1)
  assert.equal(fs.existsSync(profile.localDir(home, ID)), false)
  assert.equal(fs.existsSync(`${profile.localDir(home, ID)}.moving`), false)
  assert.equal(await profile.readMarker(home, ID), null)
  assert.equal(await profile.readMarker(home, owed), null)
  assert.equal(fs.existsSync(profile.localDir(home, other)), true)
  assert.equal(fs.existsSync(path.join(home, 'browsers')), true)
  assert.equal(await profile.forget(home, root), 0)
  // a vault that moved from `root` may be using the profile its marker names: that one stays
  profileAt(profile.localDir(home, ID))
  await profile.writeMarker(home, ID, { path: root })
  assert.equal(await profile.forget(home, root, profile.localDir(home, ID)), 0)
  assert.equal(cookies(profile.localDir(home, ID)), 'sid=1')
})
