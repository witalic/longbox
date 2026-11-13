'use strict'
// node --test "shell/*.test.js" — the password manager's store
// (design/password-manager.md): the file, the two keys, the previous passwords.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const secrets = require('./secrets')

const MASTER = 'correct horse battery'
const SITE = 'https://kestrel-reader.example'

function vault(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lb-secrets-'))
  t.after(() => fs.rmSync(root, { recursive: true, force: true }))
  return root
}
const file = (root) => path.join(root, '.longbox', 'secrets.json')
const copy = (root) => path.join(root, '.longbox', 'secrets.prev.json')
const text = (buf) => {
  const s = buf.toString('utf8')
  buf.fill(0)
  return s
}

test('a login is kept under the master password, and only it opens the file', async (t) => {
  const root = vault(t)
  assert.equal(await secrets.exists(root), false)
  await assert.rejects(secrets.create(root, 'short'), { code: 'ESHORT' })
  const logins = await secrets.create(root, MASTER)
  const id = await logins.add({ origin: `${SITE}/account/sign-in`, username: 'reader@example.com', password: 'hunter2-lantern' })
  await logins.lock()
  assert.equal(logins.unlocked, false)
  assert.throws(() => logins.reveal(id), { code: 'ELOCKED' })
  await assert.rejects(secrets.unlock(root, 'not the master password'), { code: 'EPASSWORD' })
  const again = await secrets.unlock(root, MASTER)
  assert.deepEqual(again.list().map((e) => [e.origin, e.username]), [[SITE, 'reader@example.com']])
  assert.equal(text(again.reveal(id)), 'hunter2-lantern')
  await assert.rejects(secrets.create(root, MASTER), { code: 'EEXIST' })
})

test('neither the file nor a list holds a password in the clear', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  await logins.add({ origin: SITE, username: 'reader', password: 'plain-to-find-1' })
  assert.doesNotMatch(fs.readFileSync(file(root), 'utf8'), /plain-to-find|reader/)
  assert.doesNotMatch(JSON.stringify(logins.list()), /plain-to-find/)
})

test('a file edited by hand opens nothing, one that does not read is never written over', async (t) => {
  const root = vault(t)
  await (await secrets.create(root, MASTER)).add({ origin: SITE, username: 'u', password: 'p' })
  fs.rmSync(copy(root)) // no second copy to fall back to
  const doc = JSON.parse(fs.readFileSync(file(root), 'utf8'))
  fs.writeFileSync(file(root), JSON.stringify({ ...doc, kdf: { ...doc.kdf, N: doc.kdf.N / 2 } }))
  await assert.rejects(secrets.unlock(root, MASTER), { code: 'EPASSWORD' })        // the header: the key does not unwrap
  fs.writeFileSync(file(root), JSON.stringify({ ...doc, kdf: { ...doc.kdf, p: 100000 } }))
  await assert.rejects(secrets.unlock(root, MASTER), { code: 'EBADFILE' })         // a cost made to hang the app
  const data = Buffer.from(doc.data.data, 'base64')
  data[0] ^= 1
  fs.writeFileSync(file(root), JSON.stringify({ ...doc, data: { ...doc.data, data: data.toString('base64') } }))
  await assert.rejects(secrets.unlock(root, MASTER), { code: 'EBADFILE' })         // the body
  fs.writeFileSync(file(root), JSON.stringify({ ...doc, schema: 2 }))
  await assert.rejects(secrets.unlock(root, MASTER), { code: 'ENEWER' })
  fs.writeFileSync(file(root), '{ torn')
  await assert.rejects(secrets.unlock(root, MASTER), { code: 'EBADFILE' })
  await assert.rejects(secrets.create(root, MASTER), { code: 'EEXIST' })
  assert.equal(fs.readFileSync(file(root), 'utf8'), '{ torn')
})

test('a file that does not read falls back to its second copy, and the next change writes it again', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  await logins.add({ origin: SITE, username: 'first', password: 'p1' })
  await logins.add({ origin: SITE, username: 'second', password: 'p2' })
  await logins.lock()
  fs.writeFileSync(file(root), '{ "schema": 1, "kd')
  const back = await secrets.unlock(root, MASTER)
  assert.equal(back.restored, true)
  assert.deepEqual(back.list().map((e) => e.username), ['first', 'second'])
  assert.equal((await secrets.unlock(root, 'not the master password').catch((e) => e)).code, 'EBADFILE')
  await back.add({ origin: SITE, username: 'third', password: 'p3' })
  assert.equal(fs.readFileSync(file(root), 'utf8'), fs.readFileSync(copy(root), 'utf8'))
  await back.lock()
  const whole = await secrets.unlock(root, MASTER)
  assert.equal(whole.restored, false)
  assert.deepEqual(whole.list().map((e) => e.username), ['first', 'second', 'third'])
})

test('the second copy keeps nothing the file does not: no deleted login, no old master password', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  const gone = await logins.add({ origin: SITE, username: 'deleted', password: 'p1' })
  await logins.add({ origin: SITE, username: 'kept', password: 'p2' })
  await logins.remove(gone)
  await logins.changeMaster(MASTER, 'a brand new master')
  await logins.lock()
  fs.writeFileSync(file(root), '{ torn')                                            // only the copy is left to read
  await assert.rejects(secrets.unlock(root, MASTER), { code: 'EBADFILE' })
  const back = await secrets.unlock(root, 'a brand new master')
  assert.equal(back.restored, true)
  assert.deepEqual(back.list().map((e) => e.username), ['kept'])
})

test('a second copy that is not the file — a crash came between the two writes — is mended at unlock', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  await logins.add({ origin: SITE, username: 'deleted', password: 'p1' })
  const older = fs.readFileSync(file(root), 'utf8')
  await logins.remove(logins.list()[0].id)
  await logins.lock()
  fs.writeFileSync(copy(root), older)
  await secrets.unlock(root, MASTER)
  assert.equal(fs.readFileSync(copy(root), 'utf8'), fs.readFileSync(file(root), 'utf8'))
})

test('a second copy that cannot be written does not undo the change', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  fs.mkdirSync(copy(root))
  t.mock.method(console, 'error', () => {})
  await logins.add({ origin: SITE, username: 'saved', password: 'p1' })
  assert.deepEqual(logins.list().map((e) => e.username), ['saved'])
  await logins.lock()
  assert.deepEqual((await secrets.unlock(root, MASTER)).list().map((e) => e.username), ['saved'])
})

test('a write that fails changes nothing — on disk or in memory — and the next one does not carry it', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  await logins.add({ origin: SITE, username: 'kept', password: 'p1' })
  const aside = `${file(root)}.aside`
  const breakWrites = () => { fs.renameSync(file(root), aside); fs.mkdirSync(file(root)); fs.writeFileSync(path.join(file(root), 'x'), '') }
  const mendWrites = () => { fs.rmSync(file(root), { recursive: true }); fs.renameSync(aside, file(root)) }
  breakWrites()
  await assert.rejects(logins.add({ origin: SITE, username: 'lost', password: 'p2' }))
  await assert.rejects(logins.changeMaster(MASTER, 'a brand new master'))
  assert.deepEqual(logins.list().map((e) => e.username), ['kept'])
  mendWrites()
  await logins.add({ origin: SITE, username: 'later', password: 'p3' })
  await logins.lock()
  const again = await secrets.unlock(root, MASTER)                                   // the old master: the change failed
  assert.deepEqual(again.list().map((e) => e.username), ['kept', 'later'])
  await assert.rejects(secrets.unlock(root, 'a brand new master'), { code: 'EPASSWORD' })
})

test('a change made against a login as it was is refused once it changed; no password is empty', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  await assert.rejects(logins.add({ origin: SITE, username: 'u', password: '' }), { code: 'EEMPTY' })
  const id = await logins.add({ origin: SITE, username: 'u', password: 'one' })
  await logins.edit(id, { steps: [{ password: 'two' }] })
  const seen = logins.list()[0].updated
  await logins.edit(id, { steps: [{ password: 'three' }] })                          // a sign-in's Update, meanwhile
  await assert.rejects(logins.edit(id, { steps: [{ use: 0 }], updated: seen }), { code: 'ESTALE' })
  await assert.rejects(logins.edit(id, { steps: [{ password: '' }] }), { code: 'EEMPTY' })
  assert.equal(text(logins.reveal(id)), 'three')
  await logins.edit(id, { steps: [{ use: 0 }], updated: logins.list()[0].updated })
  assert.equal(text(logins.reveal(id)), 'two')
})

test('a password handed over is the store\'s own once the change is asked for', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  const slow = logins.add({ origin: SITE, username: 'first', password: 'x' })        // a write ahead in the queue
  const mine = Buffer.from('zeroed-by-its-owner')
  const saved = logins.add({ origin: SITE, username: 'second', password: mine })
  mine.fill(0)                                                                       // the offer goes meanwhile
  await slow
  const id = await saved
  assert.equal(text(logins.reveal(id)), 'zeroed-by-its-owner')
})

test('locked, what is being written is written first', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  const pending = logins.add({ origin: SITE, username: 'in flight', password: 'p' })
  const locking = logins.lock()
  assert.equal(logins.unlocked, false)
  await assert.rejects(logins.add({ origin: SITE, username: 'after', password: 'q' }), { code: 'ELOCKED' })
  await pending
  await locking
  assert.deepEqual((await secrets.unlock(root, MASTER)).list().map((e) => e.username), ['in flight'])
})

test('a new password keeps the ones it replaced — three at most, newest first', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  const id = await logins.add({ origin: SITE, username: 'reader', password: 'one' })
  for (const pw of ['two', 'three', 'four']) await logins.edit(id, { steps: [{ password: pw }] })
  await logins.edit(id, { steps: [{ password: 'five' }] })
  assert.equal(text(logins.reveal(id)), 'five')
  assert.deepEqual([0, 1, 2].map((i) => text(logins.reveal(id, i))), ['four', 'three', 'two'])
  assert.equal(logins.list()[0].previous.length, secrets.KEEP_PREVIOUS)
  assert.throws(() => logins.reveal(id, 3), { code: 'ENOENT' })
  // used again: it is the password, the one it replaced on top of the rest
  await logins.edit(id, { steps: [{ use: 1 }] })
  assert.equal(text(logins.reveal(id)), 'three')
  assert.deepEqual([0, 1, 2].map((i) => text(logins.reveal(id, i))), ['five', 'four', 'two'])
  // one Save, several steps, in the order they were made
  await logins.edit(id, { steps: [{ forget: 2 }, { use: 0 }, { password: 'six' }] })
  assert.equal(text(logins.reveal(id)), 'six')
  assert.deepEqual([0, 1, 2].map((i) => text(logins.reveal(id, i))), ['five', 'three', 'four'])
  // a step that no longer fits changes nothing
  await assert.rejects(logins.edit(id, { username: 'x', steps: [{ use: 0 }, { forget: 5 }] }), { code: 'ESTEP' })
  assert.equal(text(logins.reveal(id)), 'six')
  assert.equal(logins.list()[0].username, 'reader')
  await logins.edit(id, { steps: [{ forget: 2 }] })
  // a name or a note changes no password
  await logins.edit(id, { username: 'reader2', note: 'second email' })
  assert.equal(logins.list()[0].previous.length, 2)
  await logins.lock()
  const again = await secrets.unlock(root, MASTER)
  assert.deepEqual(again.list().map((e) => [e.username, e.note, e.previous.length]), [['reader2', 'second email', 2]])
  assert.equal(text(again.reveal(id, 1)), 'three')
})

test('a deleted login goes with its previous passwords; the others stay', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  const gone = await logins.add({ origin: SITE, username: 'a', password: 'pa' })
  await logins.edit(gone, { steps: [{ password: 'pa2' }] })
  const kept = await logins.add({ origin: 'http://kestrel-reader.example', username: 'b', password: 'pb' })
  await logins.remove(gone)
  await logins.lock()
  const again = await secrets.unlock(root, MASTER)
  assert.deepEqual(again.list().map((e) => e.id), [kept])
  assert.throws(() => again.reveal(gone), { code: 'ENOENT' })
})

test('logins belong to a site: its scheme, its port, and the name one owner holds', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  await logins.add({ origin: `${SITE}/sign-in?next=/`, username: 'https', password: 'x' })
  await logins.add({ origin: 'https://mail.kestrel-reader.example', username: 'mail', password: 'm' })
  await logins.add({ origin: 'http://kestrel-reader.example', username: 'http', password: 'y' })
  await logins.add({ origin: `${SITE}:8443`, username: 'port', password: 'z' })
  const names = (at) => logins.list(at).map((e) => e.username)
  assert.deepEqual(names(`${SITE}/anything`), ['https', 'mail'])                     // the page's own address first
  assert.deepEqual(names('https://mail.kestrel-reader.example/inbox'), ['mail', 'https'])
  assert.deepEqual(names('https://account.kestrel-reader.example/login'), ['https', 'mail'])
  assert.deepEqual(names('http://kestrel-reader.example/'), ['http'])                 // another scheme, another site
  assert.deepEqual(names(`${SITE}:8443/`), ['port'])                                 // another port too
  // a name many own is no one's site: one github.io page never sees another's
  await logins.add({ origin: 'https://reader-one.github.io', username: 'one', password: 'p1' })
  assert.deepEqual(names('https://reader-two.github.io/'), [])
  assert.deepEqual(names('https://reader-one.github.io/app'), ['one'])
  assert.equal(secrets.siteOf('https://a.b.co.uk/x'), secrets.siteOf('https://c.b.co.uk'))
  assert.notEqual(secrets.siteOf('https://b.co.uk'), secrets.siteOf('https://d.co.uk'))
  // an address with no such name is a site of its own
  await logins.add({ origin: 'http://127.0.0.1:8080', username: 'ip', password: 'q' })
  assert.deepEqual(names('http://127.0.0.2:8080/'), [])
  await assert.rejects(logins.add({ origin: 'file:///C:/x', username: 'f', password: 'p' }), { code: 'EORIGIN' })
  assert.throws(() => secrets.originOf('about:blank'), { code: 'EORIGIN' })
})

test('the sites where saving is never offered are kept inside the encryption', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  await logins.setNever(`${SITE}/sign-in`, true)
  assert.equal(logins.neverAsks(SITE), true)
  assert.equal(logins.neverAsks('https://account.kestrel-reader.example/login'), true)  // the site, every address
  assert.deepEqual(logins.nevers(), [SITE])
  assert.equal(logins.neverAsks('http://kestrel-reader.example'), false)
  assert.doesNotMatch(fs.readFileSync(file(root), 'utf8'), /kestrel/)
  await logins.lock()
  const again = await secrets.unlock(root, MASTER)
  assert.equal(again.neverAsks(SITE), true)
  await again.setNever('https://account.kestrel-reader.example/login', true)   // said again, at another address
  await again.setNever('http://kestrel-reader.example', true)                  // another site: http
  assert.deepEqual(again.nevers('https://mail.kestrel-reader.example'), [SITE, 'https://account.kestrel-reader.example'])
  await again.setNever(SITE, false)                                            // asked again: on all of the site
  assert.equal(again.neverAsks('https://account.kestrel-reader.example'), false)
  assert.equal(again.neverAsks(SITE), false)
  assert.deepEqual(again.nevers(), ['http://kestrel-reader.example'])
})

test('a new master password opens what the old one did, and the old one nothing', async (t) => {
  const root = vault(t)
  const logins = await secrets.create(root, MASTER)
  const id = await logins.add({ origin: SITE, username: 'reader', password: 'kept' })
  await logins.edit(id, { steps: [{ password: 'kept-2' }, { use: 0 }] })
  const before = JSON.parse(fs.readFileSync(file(root), 'utf8'))
  await assert.rejects(logins.changeMaster('wrong one entirely', 'a brand new master'), { code: 'EPASSWORD' })
  await assert.rejects(logins.changeMaster(MASTER, 'short'), { code: 'ESHORT' })
  await logins.changeMaster(MASTER, 'a brand new master')
  assert.equal(text(logins.reveal(id)), 'kept')                                     // still unlocked
  assert.equal(text(logins.reveal(id, 0)), 'kept-2')
  await logins.lock()
  await assert.rejects(secrets.unlock(root, MASTER), { code: 'EPASSWORD' })
  const again = await secrets.unlock(root, 'a brand new master')
  assert.equal(text(again.reveal(id)), 'kept')
  assert.equal(text(again.reveal(id, 0)), 'kept-2')
  // a new data key too: a copy made before, opened with the old master, opens nothing written since
  const after = JSON.parse(fs.readFileSync(file(root), 'utf8'))
  fs.writeFileSync(file(root), JSON.stringify({ ...before, data: after.data }))
  fs.rmSync(copy(root))
  await assert.rejects(secrets.unlock(root, MASTER), { code: 'EBADFILE' })
})

test('a generated password: its length, every kind asked for, nothing that reads alike', () => {
  for (let i = 0; i < 200; i++) {
    const pw = secrets.generate()
    assert.equal(pw.length, 20)
    assert.match(pw, /[a-zA-Z]/)
    assert.match(pw, /[0-9]/)
    assert.match(pw, /[!#$%&*+\-=?@^_~]/)
    assert.doesNotMatch(pw, /[lI1O0o|]/)
  }
  assert.equal(secrets.generate({ length: 3 }).length, 8)
  assert.equal(secrets.generate({ length: 500 }).length, 64)
  assert.match(secrets.generate({ symbols: false, digits: false }), /^[a-zA-Z]+$/)
  assert.throws(() => secrets.generate({ letters: false, digits: false, symbols: false }), { code: 'EKINDS' })
})
