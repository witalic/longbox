'use strict'
// The password manager's store (design/password-manager.md): the logins of the
// sites the embedded browser signs in to, kept in the vault and encrypted under
// a master password — `<vault>/.longbox/secrets.json`.
//
// Two keys. scrypt over the master password gives the key that unwraps a random
// data key; the data key encrypts the list of logins, and each password in it
// on its own, so opening the list opens no password. Unlocked, only the data
// key is held; a password is decrypted when it is used, into a Buffer the
// caller zeroes. Every sealed part is bound to the file's header (its schema
// and how the key is derived), so a header edited by hand opens nothing.
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const vaults = require('./vaults')

const FILE = 'secrets.json'
const SCHEMA = 1
// ~128 MB and about half a second a guess: what a copied vault's file costs
// whoever tries passwords on it. Kept in the file, so it can be raised later.
const KDF = { alg: 'scrypt', N: 2 ** 17, r: 8, p: 1 }
const MAXMEM = 256 * 1024 * 1024
const MIN_MASTER = 10
const KEEP_PREVIOUS = 3

const fileOf = (root) => path.join(root, vaults.APP_DIR, FILE)
const b64 = (buf) => buf.toString('base64')
const unb64 = (s) => Buffer.from(typeof s === 'string' ? s : '', 'base64')

class SecretsError extends Error {
  constructor(code, message) {
    super(message)
    this.code = code
  }
}
const unreadable = () => new SecretsError('EBADFILE', `${FILE} does not read as longbox wrote it — it is left as it is`)

// the header every sealed part is bound to
const headerOf = (doc) => Buffer.from(JSON.stringify({
  schema: doc.schema, kdf: { alg: doc.kdf.alg, N: doc.kdf.N, r: doc.kdf.r, p: doc.kdf.p, salt: doc.kdf.salt } }))

function seal(key, plain, aad) {
  const nonce = crypto.randomBytes(12)
  const c = crypto.createCipheriv('aes-256-gcm', key, nonce)
  c.setAAD(aad)
  const data = Buffer.concat([c.update(plain), c.final()])
  return { nonce: b64(nonce), data: b64(data), tag: b64(c.getAuthTag()) }
}

// null when it does not open: a wrong key, or a part changed since it was sealed
function open(key, box, aad) {
  const nonce = unb64(box?.nonce)
  const tag = unb64(box?.tag)
  if (nonce.length !== 12 || tag.length !== 16) return null
  try {
    const d = crypto.createDecipheriv('aes-256-gcm', key, nonce)
    d.setAAD(aad)
    d.setAuthTag(tag)
    return Buffer.concat([d.update(unb64(box?.data)), d.final()])
  } catch {
    return null
  }
}

async function derive(master, kdf) {
  const pass = Buffer.from(String(master), 'utf8')
  try {
    return await new Promise((resolve, reject) => crypto.scrypt(pass, unb64(kdf.salt), 32,
      { N: kdf.N, r: kdf.r, p: kdf.p, maxmem: MAXMEM }, (e, k) => (e ? reject(e) : resolve(k))))
  } finally {
    pass.fill(0)
  }
}

// The file as it reads — null when there is none. One that does not parse, or
// that is not this shape, is refused and never written over: it may be all the
// logins there are.
async function readDoc(root) {
  let text
  try {
    text = await vaults.within(8000, fs.promises.readFile(fileOf(root), 'utf8'), root)
  } catch (e) {
    if (e.code === 'ENOENT') return null
    throw e
  }
  let doc
  try { doc = JSON.parse(text) } catch { throw unreadable() }
  if (Number(doc?.schema) > SCHEMA) throw new SecretsError('ENEWER', `${FILE} was written by a newer longbox`)
  const k = doc?.kdf
  if (doc?.schema !== SCHEMA || k?.alg !== 'scrypt' || ![k.N, k.r, k.p].every(Number.isInteger)
      || unb64(k.salt).length < 16 || !doc.key || !doc.data) throw unreadable()
  return doc
}

function originOf(url) {
  let origin = 'null'
  try { origin = new URL(String(url)).origin } catch { /* not a URL */ }
  if (!/^https?:\/\//.test(origin)) throw new SecretsError('EORIGIN', `not a site a login belongs to: ${url}`)
  return origin
}

const asBuffer = (password) => (Buffer.isBuffer(password) ? password : Buffer.from(String(password ?? ''), 'utf8'))
const now = () => new Date().toISOString()

// One vault's logins, unlocked. Every change is written at once, whole and
// atomically, one change after another.
class Logins {
  #root
  #doc
  #key
  #entries
  #writing = Promise.resolve()

  constructor(root, doc, key, entries) {
    this.#root = root
    this.#doc = doc
    this.#key = key
    this.#entries = entries
  }

  get unlocked() { return !!this.#key }

  #held() {
    if (!this.#key) throw new SecretsError('ELOCKED', 'the logins are locked')
    return this.#key
  }

  #entry(id) {
    this.#held()
    const e = this.#entries.find((x) => x.id === id)
    if (!e) throw new SecretsError('ENOENT', 'no such login')
    return e
  }

  #sealPassword(id, password) {
    const plain = asBuffer(password)
    try {
      return seal(this.#held(), plain, Buffer.from(id))
    } finally {
      if (plain !== password) plain.fill(0)
    }
  }

  #change(fn) {
    const run = this.#writing.then(async () => {
      this.#held()
      const out = await fn()
      await this.#save()
      return out
    })
    this.#writing = run.catch(() => {})
    return run
  }

  async #save() {
    const header = headerOf(this.#doc)
    const plain = Buffer.from(JSON.stringify(this.#entries))
    this.#doc = { ...this.#doc, data: seal(this.#key, plain, header) }
    await vaults.writeAtomic(fileOf(this.#root), JSON.stringify(this.#doc, null, 2))
  }

  // What a list shows of each login — never a password.
  list(origin = '') {
    this.#held()
    const only = origin ? originOf(origin) : ''
    return this.#entries.filter((e) => !only || e.origin === only).map((e) => ({
      id: e.id, origin: e.origin, username: e.username, note: e.note, created: e.created, updated: e.updated,
      used: e.used, previous: e.previous.map((p) => ({ replaced: p.replaced })),
    }))
  }

  // The password, or the `previous`-th one it replaced, decrypted now: the
  // caller zeroes the Buffer once it is used.
  reveal(id, previous = -1) {
    const e = this.#entry(id)
    const box = previous < 0 ? e.secret : e.previous[previous]?.secret
    if (!box) throw new SecretsError('ENOENT', 'no such password')
    const plain = open(this.#key, box, Buffer.from(id))
    if (!plain) throw unreadable()
    return plain
  }

  add({ origin, username = '', password, note = '' }) {
    return this.#change(() => {
      const id = crypto.randomBytes(8).toString('hex')
      const at = now()
      this.#entries.push({ id, origin: originOf(origin), username: String(username), note: String(note),
        secret: this.#sealPassword(id, password), previous: [], created: at, updated: at, used: '' })
      return id
    })
  }

  // A new password puts the one it replaces at the top of the previous ones;
  // past three, the oldest goes.
  update(id, { username, password, note }) {
    return this.#change(() => {
      const e = this.#entry(id)
      if (username !== undefined) e.username = String(username)
      if (note !== undefined) e.note = String(note)
      if (password !== undefined) {
        e.previous = [{ secret: e.secret, replaced: now() }, ...e.previous].slice(0, KEEP_PREVIOUS)
        e.secret = this.#sealPassword(id, password)
      }
      e.updated = now()
    })
  }

  // A previous password becomes the password again; the one it replaces takes its place at the top.
  usePrevious(id, index) {
    return this.#change(() => {
      const e = this.#entry(id)
      const back = e.previous[index]
      if (!back) throw new SecretsError('ENOENT', 'no such password')
      e.previous = [{ secret: e.secret, replaced: now() }, ...e.previous.filter((_, i) => i !== index)]
      e.secret = back.secret
      e.updated = now()
    })
  }

  forgetPrevious(id, index) {
    return this.#change(() => {
      const e = this.#entry(id)
      if (!e.previous[index]) throw new SecretsError('ENOENT', 'no such password')
      e.previous = e.previous.filter((_, i) => i !== index)
      e.updated = now()
    })
  }

  // Gone from the file at once, its previous passwords with it: there is no bin.
  remove(id) {
    return this.#change(() => {
      this.#entry(id)
      this.#entries = this.#entries.filter((e) => e.id !== id)
    })
  }

  used(id) {
    return this.#change(() => { this.#entry(id).used = now() })
  }

  // The data key wrapped again under the new master password (and a new salt);
  // the logins and their passwords are as they were.
  changeMaster(current, next) {
    return this.#change(async () => {
      if (String(next).length < MIN_MASTER) throw new SecretsError('ESHORT', `at least ${MIN_MASTER} characters`)
      const was = await derive(current, this.#doc.kdf)
      const ok = open(was, this.#doc.key, headerOf(this.#doc))
      was.fill(0)
      if (!ok) throw new SecretsError('EPASSWORD', 'that is not the master password')
      ok.fill(0)
      const kdf = { ...KDF, salt: b64(crypto.randomBytes(16)) }
      const kek = await derive(next, kdf)
      const doc = { schema: SCHEMA, kdf }
      this.#doc = { ...doc, key: seal(kek, this.#key, headerOf(doc)), data: this.#doc.data }
      kek.fill(0)
    })
  }

  // The data key zeroed and the list dropped: nothing of the logins is left
  // in memory until the master password is given again.
  lock() {
    if (this.#key) this.#key.fill(0)
    this.#key = null
    this.#entries = []
  }
}

async function exists(root) {
  try {
    await vaults.within(8000, fs.promises.stat(fileOf(root)), root)
    return true
  } catch (e) {
    if (e.code === 'ENOENT') return false
    throw e
  }
}

// The vault's first master password: a new file, empty, unlocked.
async function create(root, master) {
  if (String(master).length < MIN_MASTER) throw new SecretsError('ESHORT', `at least ${MIN_MASTER} characters`)
  if (await exists(root)) throw new SecretsError('EEXIST', 'this vault has a master password already')
  const kdf = { ...KDF, salt: b64(crypto.randomBytes(16)) }
  const kek = await derive(master, kdf)
  const key = crypto.randomBytes(32)
  const head = { schema: SCHEMA, kdf }
  const doc = { ...head, key: seal(kek, key, headerOf(head)), data: seal(key, Buffer.from('[]'), headerOf(head)) }
  kek.fill(0)
  await fs.promises.mkdir(path.join(root, vaults.APP_DIR), { recursive: true })
  // never over another file: one made meanwhile holds logins of its own
  await fs.promises.writeFile(fileOf(root), JSON.stringify(doc, null, 2), { flag: 'wx' }).catch((e) => {
    throw e.code === 'EEXIST' ? new SecretsError('EEXIST', 'this vault has a master password already') : e
  })
  return new Logins(root, doc, key, [])
}

async function unlock(root, master) {
  const doc = await readDoc(root)
  if (!doc) throw new SecretsError('ENOENT', 'this vault has no master password yet')
  const header = headerOf(doc)
  const kek = await derive(master, doc.kdf)
  const key = open(kek, doc.key, header)
  kek.fill(0)
  if (!key || key.length !== 32) throw new SecretsError('EPASSWORD', 'that is not the master password')
  const plain = open(key, doc.data, header)
  let entries = null
  try { entries = plain && JSON.parse(plain.toString('utf8')) } catch { /* below */ }
  if (!Array.isArray(entries)) {
    key.fill(0)
    throw unreadable()
  }
  return new Logins(root, doc, key, entries.map((e) => ({ ...e, previous: Array.isArray(e.previous) ? e.previous : [] })))
}

// A new password: `length` characters (8–64) of the kinds asked for, at least
// one of each, with no characters that read alike (l 1 I, O 0 o).
const KINDS = {
  letters: 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ',
  digits: '23456789',
  symbols: '!#$%&*+-=?@^_~',
}
function generate({ length = 20, letters = true, digits = true, symbols = true } = {}) {
  const sets = [letters && KINDS.letters, digits && KINDS.digits, symbols && KINDS.symbols].filter(Boolean)
  if (!sets.length) throw new SecretsError('EKINDS', 'a password needs at least one kind of character')
  const n = Math.min(64, Math.max(8, Math.round(Number(length) || 20)))
  const all = sets.join('')
  for (;;) {
    const out = Array.from({ length: n }, () => all[crypto.randomInt(all.length)])
    if (sets.every((set) => out.some((ch) => set.includes(ch)))) return out.join('')
  }
}

module.exports = { KEEP_PREVIOUS, MIN_MASTER, SecretsError, create, exists, generate, originOf, unlock }
