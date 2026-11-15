'use strict'
// node --test "shell/*.test.js" — the shell's lines in the app's one log.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const fresh = () => {
  delete require.cache[require.resolve('./log')]
  return require('./log')
}
const quiet = (t) => {
  t.mock.method(console, 'log', () => {})
  t.mock.method(console, 'error', () => {})
}

test('a launch\'s first lines wait for the sidecar, and reach it in order', async (t) => {
  quiet(t)
  const log = fresh()
  log.info('starting')
  log.warn('the vault is slow')
  const got = []
  log.to(async (lines) => { got.push(...lines); return true })
  await log.flush()
  assert.deepEqual(got.map((l) => [l.level, l.text]), [['info', 'starting'], ['warning', 'the vault is slow']])
  assert.ok(got[0].at <= got[1].at && got[0].at > Date.now() - 5000)
})

test('lines the sidecar does not take are written to the file once it is gone', async (t) => {
  quiet(t)
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lb-log-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  const file = path.join(dir, 'longbox.log')
  const log = fresh()
  log.to(async () => false) // closing: it answers, and takes nothing
  log.error('the sidecar exited (1)')
  await log.flush()
  assert.equal(fs.existsSync(file), false) // held: nothing has taken it yet
  log.alone(file)
  await log.flush()
  const written = fs.readFileSync(file, 'utf8')
  assert.match(written, /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \[[0-9a-f]{4}\] ERROR longbox\.shell: the sidecar exited \(1\)\n$/)
})

test('the lines said before the sidecar existed go with it, and are not sent twice', async (t) => {
  quiet(t)
  const log = fresh()
  log.info('starting')
  log.info('the vault: here')
  assert.deepEqual(log.handOver().map((l) => l.text), ['starting', 'the vault: here'])
  log.info('the sidecar answered')
  const got = []
  log.to(async (lines) => { got.push(...lines); return true })
  await log.flush()
  assert.deepEqual(got.map((l) => l.text), ['the sidecar answered'])
})

test('a sidecar that dies before it answers leaves its first lines to the file, run and all', async (t) => {
  quiet(t)
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'lb-log-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  const file = path.join(dir, 'longbox.log')
  const log = fresh()
  log.info('starting')
  log.handOver()
  log.error('the sidecar exited (1)')
  log.alone(file)
  await log.flush()
  const lines = fs.readFileSync(file, 'utf8').trim().split('\n')
  assert.equal(lines.length, 2)
  assert.ok(lines.every((l) => l.includes(` [${log.run}] `)))
  assert.match(log.run, /^[0-9a-f]{4}$/)
})

test('only the latest lines are held while nothing takes them', async (t) => {
  quiet(t)
  const log = fresh()
  for (let i = 0; i < 600; i++) log.info(`line ${i}`)
  const got = []
  log.to(async (lines) => { got.push(...lines); return true })
  await log.flush()
  assert.equal(got.length, 500)
  assert.equal(got[0].text, 'line 100')
})
