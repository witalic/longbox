'use strict'
// Whether an address is on the public internet — what a fetch the main process
// makes on a page's behalf may reach. A page in the embedded browser cannot
// reach this computer or its network on its own (Chromium asks first, and the
// session refuses), so the app must not do it for the page either: a page names
// an image, and the main process — with the session's cookies — goes and gets
// it. The sidecar's cover fetch draws the same line (backend scraper/covers.py).
const dns = require('node:dns')
const net = require('node:net')

const NOT_PUBLIC = new net.BlockList()
for (const [a, p] of [
  ['0.0.0.0', 8], ['10.0.0.0', 8], ['100.64.0.0', 10], ['127.0.0.0', 8], ['169.254.0.0', 16],
  ['172.16.0.0', 12], ['192.0.0.0', 24], ['192.0.2.0', 24], ['192.168.0.0', 16], ['198.18.0.0', 15],
  ['198.51.100.0', 24], ['203.0.113.0', 24], ['224.0.0.0', 4], ['240.0.0.0', 4],
]) NOT_PUBLIC.addSubnet(a, p, 'ipv4')
for (const [a, p] of [
  ['::', 127], ['fc00::', 7], ['fe80::', 10], ['fec0::', 10], ['ff00::', 8], ['2001:db8::', 32], ['100::', 64],
]) NOT_PUBLIC.addSubnet(a, p, 'ipv6')

// An IPv6 address as its 16 bytes; null for anything else.
function v6bytes(ip) {
  let s = ip
  const quad = /(\d+\.\d+\.\d+\.\d+)$/.exec(s)
  if (quad) {
    const b = quad[1].split('.').map(Number)
    s = s.slice(0, -quad[1].length) + `${((b[0] << 8) | b[1]).toString(16)}:${((b[2] << 8) | b[3]).toString(16)}`
  }
  const [head, tail] = s.includes('::') ? s.split('::') : [s, null]
  const h = head ? head.split(':') : []
  const t = tail ? tail.split(':') : []
  const groups = tail === null ? h : [...h, ...Array(8 - h.length - t.length).fill('0'), ...t]
  if (groups.length !== 8) return null
  const out = []
  for (const g of groups) {
    const n = parseInt(g || '0', 16)
    out.push(n >> 8, n & 255)
  }
  return out
}

// The IPv4 address an IPv6 one carries, where it carries one: mapped
// (::ffff:a.b.c.d), compatible (::a.b.c.d), NAT64 (64:ff9b::/96), 6to4 (2002::/16).
function embeddedV4(ip) {
  const b = v6bytes(ip)
  if (!b) return ''
  const zero = (from, to) => b.slice(from, to).every((x) => x === 0)
  const tail = b.slice(12).join('.')
  if (zero(0, 10) && b[10] === 255 && b[11] === 255) return tail
  if (zero(0, 12) && !zero(12, 16) && !(zero(12, 15) && b[15] === 1)) return tail
  if (b[0] === 0x00 && b[1] === 0x64 && b[2] === 0xff && b[3] === 0x9b && zero(4, 12)) return tail
  if (b[0] === 0x20 && b[1] === 0x02) return b.slice(2, 6).join('.')
  return ''
}

function isPublicAddress(address) {
  const ip = String(address || '').replace(/^\[|\]$/g, '').split('%')[0]
  const family = net.isIP(ip)
  if (family === 4) return !NOT_PUBLIC.check(ip, 'ipv4')
  if (family !== 6) return false
  const v4 = embeddedV4(ip)
  if (v4) return isPublicAddress(v4)
  return !NOT_PUBLIC.check(ip, 'ipv6')
}

// Every address the URL's host resolves to is public. A name that does not
// resolve is no answer at all: refused.
async function isPublicUrl(url, lookup = dns.promises.lookup) {
  let host
  try {
    const u = new URL(url)
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return false
    host = u.hostname.replace(/^\[|\]$/g, '')
  } catch { return false }
  if (net.isIP(host)) return isPublicAddress(host)
  try {
    const found = await lookup(host, { all: true, verbatim: true })
    return found.length > 0 && found.every((r) => isPublicAddress(r.address))
  } catch { return false }
}

module.exports = { isPublicAddress, isPublicUrl }
