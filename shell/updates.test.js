'use strict'
// node --test "shell/*.test.js" — the update check's reading of GitHub's answer.
const test = require('node:test')
const assert = require('node:assert/strict')
const { check, describe, newer, releasePage } = require('./updates')

const RELEASES = 'https://github.com/witalic/longbox/releases/'

const RELEASE = {
  tag_name: 'v3.4.1', draft: false, prerelease: false, published_at: '2026-11-20T10:00:00Z',
  html_url: `${RELEASES}tag/v3.4.1`, body: '### Fixed\n- a thing\n',
}

test('a version is newer only by its numbers', () => {
  assert.equal(newer('3.4.1', '3.4.0'), true)
  assert.equal(newer('v3.10.0', '3.9.9'), true)        // numbers, not strings
  assert.equal(newer('3.4.0', '3.4.0'), false)
  assert.equal(newer('3.3.9', '3.4.0'), false)
  assert.equal(newer('3.5.0-beta.1', '3.4.0'), false)  // not a plain version
  assert.equal(newer('', '3.4.0'), false)
})

test('a newer release is described with its page and date', () => {
  assert.deepEqual(describe(RELEASE, '3.4.0'), {
    latest: '3.4.1', newer: true, published: '2026-11-20T10:00:00Z', security: false,
  })
  assert.equal(describe(RELEASE, '3.4.1').newer, false)
})

test('a Security section in the notes is told apart', () => {
  const sec = { ...RELEASE, body: '### Fixed\n- one\n\n### Security\n- the browser\n' }
  assert.equal(describe(sec, '3.4.0').security, true)
  assert.equal(describe({ ...RELEASE, body: 'Security matters' }, '3.4.0').security, false)
})

test('drafts, pre-releases and odd tags are no news', () => {
  for (const r of [{ ...RELEASE, draft: true }, { ...RELEASE, prerelease: true },
    { ...RELEASE, tag_name: 'nightly' }, null]) {
    assert.equal(describe(r, '3.4.0').newer, false)
  }
})

test('the window names a version, and only a release page of longbox is made of it', () => {
  assert.equal(releasePage('3.4.1'), `${RELEASES}tag/v3.4.1`)
  assert.equal(releasePage('v3.4.1'), `${RELEASES}tag/v3.4.1`)
  assert.equal(releasePage('latest'), `${RELEASES}latest`)
  for (const odd of ['', '3.4', '3.4.1/../../../attacker/repo', `${RELEASES}../../../x`,
    'https://example.com/', '3.4.1?x=1', null, undefined, 3]) {
    assert.equal(releasePage(odd), '', String(odd))
  }
})

test('a check reports GitHub refusing, or not answering, as an error', async () => {
  const refused = async () => ({ ok: false, status: 403 })
  assert.deepEqual(await check(refused, '3.4.0'), { ok: false, error: 'GitHub answered 403' })
  const silent = (_url, { signal }) => new Promise((_r, reject) =>
    signal.addEventListener('abort', () => reject(signal.reason)))
  assert.deepEqual(await check(silent, '3.4.0', 20), { ok: false, error: 'the connection timed out' })
})

test('a check asks the one address, and reads the answer', async () => {
  let asked = ''
  const github = async (url) => { asked = url; return { ok: true, json: async () => RELEASE } }
  const got = await check(github, '3.4.0')
  assert.equal(asked, 'https://api.github.com/repos/witalic/longbox/releases/latest')
  assert.equal(got.ok, true)
  assert.equal(got.newer, true)
})
