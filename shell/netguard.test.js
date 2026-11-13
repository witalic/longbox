'use strict'
// node --test "shell/*.test.js" — what a fetch made on a page's behalf may reach.
const test = require('node:test')
const assert = require('node:assert/strict')
const { isPublicAddress, isPublicUrl } = require('./netguard')

test('public addresses are public', () => {
  for (const ip of ['93.184.216.34', '1.1.1.1', '2606:4700:4700::1111', '[2a00:1450:4001::200e]']) {
    assert.equal(isPublicAddress(ip), true, ip)
  }
})

test('this computer, its network and the reserved ranges are not', () => {
  for (const ip of [
    '127.0.0.1', '10.1.2.3', '172.16.0.1', '172.31.255.255', '192.168.1.1', '169.254.169.254',
    '100.64.0.1', '0.0.0.0', '224.0.0.1', '255.255.255.255',
    '::1', '::', 'fe80::1', 'fe80::1%12', 'fc00::1', 'fd12:3456::1', 'fec0::1', 'ff02::1',
  ]) assert.equal(isPublicAddress(ip), false, ip)
})

test('an IPv4 address carried in an IPv6 one is judged as itself', () => {
  for (const ip of ['::ffff:192.168.1.1', '::ffff:c0a8:101', '::7f00:1', '::127.0.0.1',
    '64:ff9b::c0a8:101', '64:ff9b::10.0.0.1', '2002:c0a8:0101::1']) {
    assert.equal(isPublicAddress(ip), false, ip)
  }
  assert.equal(isPublicAddress('::ffff:93.184.216.34'), true)
  assert.equal(isPublicAddress('64:ff9b::5db8:d822'), true)
})

test('what is not an address is not public', () => {
  for (const v of ['', 'localhost', 'example.com', '1.2.3', null, undefined]) {
    assert.equal(isPublicAddress(v), false, String(v))
  }
})

test('a URL is public only when every address of its host is', async () => {
  const resolve = (map) => async (host) => {
    if (!(host in map)) throw new Error('ENOTFOUND')
    return map[host].map((address) => ({ address, family: address.includes(':') ? 6 : 4 }))
  }
  const lookup = resolve({ 'cdn.example': ['93.184.216.34'], 'mixed.example': ['93.184.216.34', '10.0.0.2'],
    'router.example': ['192.168.1.1'] })
  assert.equal(await isPublicUrl('https://cdn.example/a.jpg', lookup), true)
  assert.equal(await isPublicUrl('https://mixed.example/a.jpg', lookup), false)
  assert.equal(await isPublicUrl('http://router.example/', lookup), false)
  assert.equal(await isPublicUrl('http://nowhere.example/', lookup), false)
  assert.equal(await isPublicUrl('http://192.168.1.1:8080/x', lookup), false)
  assert.equal(await isPublicUrl('http://[::1]/x', lookup), false)
  assert.equal(await isPublicUrl('http://93.184.216.34/x', lookup), true)
  assert.equal(await isPublicUrl('file:///C:/x.jpg', lookup), false)
  assert.equal(await isPublicUrl('not a url', lookup), false)
})
