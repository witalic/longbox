'use strict'
// The password manager's store (design/password-manager.md): the logins of the
// sites the embedded browser signs in to, kept in the vault and encrypted under
// a master password — `<vault>/.longbox/secrets.json`.
//
// Two keys. scrypt over the master password gives the key that unwraps a random
// data key; the data key encrypts the list of logins, and each password in it
// on its own, so opening the list opens no password. Unlocked, only the data
// key is held; a password is decrypted when it is used, into a Buffer the
// caller zeroes. The wrapped key and the list are bound to the file's header
// (its schema and how the key is derived), so a header edited by hand opens
// nothing; each password is bound to its login's id.
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const vaults = require('./vaults')

const FILE = 'secrets.json'
const PREVIOUS_FILE = 'secrets.prev.json' // the generation before the last write
const SCHEMA = 1
// ~128 MB and about half a second a guess: what a copied vault's file costs
// whoever tries passwords on it. Kept in the file, so it can be raised later —
// within bounds, or a file made to hang the app would.
const KDF = { alg: 'scrypt', N: 2 ** 17, r: 8, p: 1 }
const MAXMEM = 256 * 1024 * 1024
const kdfFits = (k) => [k.N, k.r, k.p].every(Number.isInteger) && k.N >= 2 ** 14 && k.N <= 2 ** 20
  && (k.N & (k.N - 1)) === 0 && k.r >= 1 && k.r <= 32 && k.p >= 1 && k.p <= 16
const MIN_MASTER = 10
const KEEP_PREVIOUS = 3
const WRITE_MS = 15000

const fileOf = (root) => path.join(root, vaults.APP_DIR, FILE)
const previousOf = (root) => path.join(root, vaults.APP_DIR, PREVIOUS_FILE)
const b64 = (buf) => buf.toString('base64')
const unb64 = (s) => Buffer.from(typeof s === 'string' ? s : '', 'base64')

class SecretsError extends Error {
  constructor(code, message) {
    super(message)
    this.code = code
  }
}
const unreadable = () => new SecretsError('EBADFILE', `${FILE} does not read as longbox wrote it — it is left as it is`)
const locked = () => new SecretsError('ELOCKED', 'the logins are locked')

// the header the wrapped key and the list are bound to
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
    const parts = [d.update(unb64(box?.data)), d.final()]
    const plain = Buffer.concat(parts)
    for (const part of parts) part.fill(0)
    return plain
  } catch {
    return null
  }
}

// a sealed password, opened under one key and sealed under another
function reseal(from, to, id, box) {
  const plain = open(from, box, Buffer.from(id))
  if (!plain) throw unreadable()
  try {
    return seal(to, plain, Buffer.from(id))
  } finally {
    plain.fill(0)
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

// One file as it reads — null when there is none. One that does not parse, or
// that is not this shape, is refused and never written over: it may be all the
// logins there are.
async function readDoc(root, file) {
  let text
  try {
    text = await vaults.within(8000, fs.promises.readFile(file, 'utf8'), root)
  } catch (e) {
    if (e.code === 'ENOENT') return null
    throw e
  }
  let doc
  try { doc = JSON.parse(text) } catch { throw unreadable() }
  if (Number(doc?.schema) > SCHEMA) throw new SecretsError('ENEWER', `${FILE} was written by a newer longbox`)
  if (doc?.schema !== SCHEMA || doc.kdf?.alg !== 'scrypt' || !kdfFits(doc.kdf)
      || unb64(doc.kdf.salt).length < 16 || !doc.key || !doc.data) throw unreadable()
  return doc
}

// A new file, synced before it is renamed in, so a crash or a power cut leaves
// the old one or the new one, never half of either; the one it replaces is kept
// beside it as the generation before.
const scratchOf = (file) => `${file}.${process.pid}-${crypto.randomBytes(4).toString('hex')}.tmp`
async function writeSynced(file, text) {
  const fh = await fs.promises.open(file, 'w')
  try {
    await fh.writeFile(text)
    await fh.sync()
  } finally {
    await fh.close()
  }
}
async function writeDurably(root, text) {
  const tmp = scratchOf(fileOf(root))
  try {
    await writeSynced(tmp, text)
    await fs.promises.copyFile(fileOf(root), previousOf(root)).catch((e) => { if (e.code !== 'ENOENT') throw e })
    await fs.promises.rename(tmp, fileOf(root))
  } catch (e) {
    fs.promises.unlink(tmp).catch(() => {})
    throw e
  }
}

function originOf(url) {
  let origin = 'null'
  try { origin = new URL(String(url)).origin } catch { /* not a URL */ }
  if (!/^https?:\/\//.test(origin)) throw new SecretsError('EORIGIN', `not a site a login belongs to: ${url}`)
  return origin
}

// a password of the store's own, taken as it is handed over: whoever handed it
// may zero theirs before the change that seals it gets its turn
function ownCopy(password) {
  const copy = Buffer.isBuffer(password) ? Buffer.from(password) : Buffer.from(String(password ?? ''), 'utf8')
  if (!copy.length) throw new SecretsError('EEMPTY', 'a password cannot be empty')
  return copy
}
const now = () => new Date().toISOString()

// One vault's logins, unlocked: the logins, and the sites where saving one is
// never offered — both inside the encryption, so the file says nothing of
// which sites they are. A change is made on a copy and written, one after
// another; only once it is on disk is it what is held, so a write that fails
// leaves the logins as they were, in memory as on disk.
class Logins {
  #root
  #doc
  #key
  #logins
  #never
  #closing = false
  #writing = Promise.resolve()

  constructor(root, doc, key, { logins, never }) {
    this.#root = root
    this.#doc = doc
    this.#key = key
    this.#logins = logins
    this.#never = never
  }

  get unlocked() { return !!this.#key && !this.#closing }

  #held() {
    if (!this.unlocked) throw locked()
  }

  #entry(id) {
    this.#held()
    const e = this.#logins.find((x) => x.id === id)
    if (!e) throw new SecretsError('ENOENT', 'no such login')
    return e
  }

  #change(fn) {
    this.#held()
    const run = this.#writing.then(async () => {
      if (!this.#key) throw locked()
      const next = { logins: structuredClone(this.#logins), never: [...this.#never], doc: this.#doc, key: this.#key }
      try {
        const out = await fn(next)
        const plain = Buffer.from(JSON.stringify({ logins: next.logins, never: next.never }))
        const doc = { ...next.doc, data: seal(next.key, plain, headerOf(next.doc)) }
        plain.fill(0)
        await vaults.within(WRITE_MS, writeDurably(this.#root, JSON.stringify(doc, null, 2)), this.#root)
        if (next.key !== this.#key) this.#key.fill(0)
        this.#doc = doc
        this.#key = next.key
        this.#logins = next.logins
        this.#never = next.never
        return out
      } catch (e) {
        if (next.key !== this.#key) next.key.fill(0)
        throw e
      }
    })
    this.#writing = run.catch(() => {})
    return run
  }

  // What a list shows of each login — never a password.
  list(origin = '') {
    this.#held()
    const only = origin ? originOf(origin) : ''
    return this.#logins.filter((e) => !only || e.origin === only).map((e) => ({
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

  async add({ origin, username = '', password, note = '' }) {
    const site = originOf(origin)
    const plain = ownCopy(password)
    try {
      return await this.#change((next) => {
        const id = crypto.randomBytes(8).toString('hex')
        const at = now()
        next.logins.push({ id, origin: site, username: String(username), note: String(note),
          secret: seal(next.key, plain, Buffer.from(id)), previous: [], created: at, updated: at, used: '' })
        return id
      })
    } finally {
      plain.fill(0)
    }
  }

  // An editor's Save, as one write: the username and the note, and `steps` in
  // the order they were made — `{ password }` a new one, `{ use: i }` the i-th
  // previous one made the password again, `{ forget: i }` the i-th dropped. A
  // password replaced goes to the top of the previous ones; past three, the
  // oldest goes. The steps name the previous passwords by place, so they hold
  // only for the login as it was when they were made (`updated`): changed since
  // — by a sign-in's Update, by another editor — nothing is changed at all, and
  // neither is it when a step no longer fits.
  async edit(id, { username, note, steps = [], updated }) {
    const owned = steps.map((s) => (s && 'password' in s ? { password: ownCopy(s.password) } : s))
    try {
      return await this.#change((next) => {
        const e = next.logins.find((x) => x.id === id)
        if (!e) throw new SecretsError('ENOENT', 'no such login')
        if (updated !== undefined && e.updated !== updated) {
          throw new SecretsError('ESTALE', 'this login was changed meanwhile — open it again')
        }
        let secret = e.secret
        let previous = [...e.previous]
        for (const step of owned) {
          if (step && 'password' in step) {
            previous = [{ secret, replaced: now() }, ...previous]
            secret = seal(next.key, step.password, Buffer.from(id))
          } else if (step && Number.isInteger(step.use) && previous[step.use]) {
            const back = previous[step.use]
            previous = [{ secret, replaced: now() }, ...previous.filter((_, i) => i !== step.use)]
            secret = back.secret
          } else if (step && Number.isInteger(step.forget) && previous[step.forget]) {
            previous = previous.filter((_, i) => i !== step.forget)
          } else {
            throw new SecretsError('ESTEP', 'that change does not fit this login any more')
          }
          previous = previous.slice(0, KEEP_PREVIOUS)
        }
        if (username !== undefined) e.username = String(username)
        if (note !== undefined) e.note = String(note)
        e.secret = secret
        e.previous = previous
        e.updated = now()
      })
    } finally {
      for (const step of owned) if (step && 'password' in step) step.password.fill(0)
    }
  }

  // Gone from the file at once, its previous passwords with it: there is no bin.
  async remove(id) {
    return this.#change((next) => {
      if (!next.logins.some((e) => e.id === id)) throw new SecretsError('ENOENT', 'no such login')
      next.logins = next.logins.filter((e) => e.id !== id)
    })
  }

  async used(id) {
    return this.#change((next) => {
      const e = next.logins.find((x) => x.id === id)
      if (!e) throw new SecretsError('ENOENT', 'no such login')
      e.used = now()
    })
  }

  // Whether saving a login is never offered on this site, the sites where it is
  // not — and the owner's answer to that, either way.
  neverAsks(origin) {
    this.#held()
    return this.#never.includes(originOf(origin))
  }

  nevers() {
    this.#held()
    return [...this.#never]
  }

  async setNever(origin, never) {
    const at = originOf(origin)
    return this.#change((next) => {
      next.never = next.never.filter((o) => o !== at)
      if (never) next.never.push(at)
    })
  }

  // A new master password, and with it a new data key: every password is sealed
  // again under it, so a copy of the vault made before — opened with the old
  // master password, leaked or weak — opens nothing written after.
  async changeMaster(current, nextMaster) {
    if (String(nextMaster).length < MIN_MASTER) throw new SecretsError('ESHORT', `at least ${MIN_MASTER} characters`)
    return this.#change(async (next) => {
      const was = await derive(current, next.doc.kdf)
      const ok = open(was, next.doc.key, headerOf(next.doc))
      was.fill(0)
      if (!ok) throw new SecretsError('EPASSWORD', 'that is not the master password')
      ok.fill(0)
      const key = crypto.randomBytes(32)
      try {
        for (const e of next.logins) {
          e.secret = reseal(next.key, key, e.id, e.secret)
          e.previous = e.previous.map((p) => ({ ...p, secret: reseal(next.key, key, e.id, p.secret) }))
        }
        const kdf = { ...KDF, salt: b64(crypto.randomBytes(16)) }
        const kek = await derive(nextMaster, kdf)
        const head = { schema: SCHEMA, kdf }
        next.doc = { ...head, key: seal(kek, key, headerOf(head)) }
        kek.fill(0)
      } catch (e) {
        key.fill(0)
        throw e
      }
      next.key = key
    })
  }

  // Locked: nothing new is taken from the moment it is asked; what is being
  // written is written; then the data key is zeroed and the list dropped, and
  // nothing of the logins is left in memory until the master password is given
  // again.
  async lock() {
    this.#closing = true
    await vaults.within(WRITE_MS, this.#writing, this.#root).catch(() => {})
    if (this.#key) this.#key.fill(0)
    this.#key = null
    this.#logins = []
    this.#never = []
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

const already = () => new SecretsError('EEXIST', 'this vault has a master password already')

// The vault's first master password: a new file, empty, unlocked. Never over
// another file — one made meanwhile holds logins of its own: the synced new
// file is linked in, which refuses a name that is taken (renamed in, where the
// drive has no links, once it is seen to be free).
async function create(root, master) {
  if (String(master).length < MIN_MASTER) throw new SecretsError('ESHORT', `at least ${MIN_MASTER} characters`)
  if (await exists(root)) throw already()
  const kdf = { ...KDF, salt: b64(crypto.randomBytes(16)) }
  const kek = await derive(master, kdf)
  const key = crypto.randomBytes(32)
  try {
    const head = { schema: SCHEMA, kdf }
    const empty = Buffer.from(JSON.stringify({ logins: [], never: [] }))
    const doc = { ...head, key: seal(kek, key, headerOf(head)), data: seal(key, empty, headerOf(head)) }
    await fs.promises.mkdir(path.join(root, vaults.APP_DIR), { recursive: true })
    const file = fileOf(root)
    const tmp = scratchOf(file)
    try {
      await writeSynced(tmp, JSON.stringify(doc, null, 2))
      try {
        await fs.promises.link(tmp, file)
      } catch (e) {
        if (e.code === 'EEXIST' || await exists(root)) throw already()
        await fs.promises.rename(tmp, file)
      }
    } finally {
      await fs.promises.unlink(tmp).catch(() => {})
    }
    return new Logins(root, doc, key, { logins: [], never: [] })
  } catch (e) {
    key.fill(0)
    throw e
  } finally {
    kek.fill(0)
  }
}

// One generation opened with the master password: the data key and the list.
async function openDoc(doc, master) {
  const header = headerOf(doc)
  const kek = await derive(master, doc.kdf)
  const key = open(kek, doc.key, header)
  kek.fill(0)
  if (!key || key.length !== 32) throw new SecretsError('EPASSWORD', 'that is not the master password')
  const plain = open(key, doc.data, header)
  let data = null
  try { data = plain && JSON.parse(plain.toString('utf8')) } catch { /* below */ }
  plain?.fill(0)
  if (!Array.isArray(data?.logins) || !Array.isArray(data?.never)) {
    key.fill(0)
    throw unreadable()
  }
  return { key, data }
}

// Unlocked with the master password. A file that does not read — torn by a
// crash, damaged since — falls back to the generation before it, losing only
// the last change; `restored` says it did.
async function unlock(root, master) {
  let doc
  try {
    doc = await readDoc(root, fileOf(root))
    if (!doc) throw new SecretsError('ENOENT', 'this vault has no master password yet')
    const { key, data } = await openDoc(doc, master)
    return Object.assign(new Logins(root, doc, key, data), { restored: false })
  } catch (e) {
    if (e.code !== 'EBADFILE') throw e
    const before = await readDoc(root, previousOf(root)).catch(() => null)
    if (!before) throw e
    const { key, data } = await openDoc(before, master).catch(() => { throw e })
    return Object.assign(new Logins(root, before, key, data), { restored: true })
  }
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

module.exports = { KEEP_PREVIOUS, SecretsError, create, exists, generate, originOf, unlock }
