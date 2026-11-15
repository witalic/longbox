'use strict'
// The shell's lines in the app's one log (backend/app/logs.py). The sidecar
// writes the vault's `.longbox/longbox.log`, so the shell hands its lines to
// it: the ones said before it existed go with it as it starts (`handOver`), to
// be written first; the rest as they come, once it answers. With no sidecar to
// take them (it died, or is closed), the shell appends them to that file
// itself. Every line is on the console too, and every line of a launch carries
// its run's id (`run`), whoever writes it.
const crypto = require('node:crypto')
const fs = require('node:fs')

const HELD = 500 // lines kept while nothing takes them
const FIRST = 50 // lines handed to the sidecar as it starts
const run = crypto.randomBytes(2).toString('hex')
const held = []
let handed = [] // went with the sidecar as it started: written by it, once it answers
let send = null // the sidecar's /api/log, while it answers
let file = '' // the log file, once there is no sidecar to write it
let timer = null

const pad = (n, w = 2) => String(n).padStart(w, '0')
// the time as the sidecar's lines carry it (Python's asctime)
function stamp(at) {
  const d = new Date(at)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())},${pad(d.getMilliseconds(), 3)}`
}

function say(level, text) {
  const line = { at: Date.now(), level, text: String(text).slice(0, 2000) }
  ;(level === 'info' ? console.log : console.error)(line.text)
  held.push(line)
  if (held.length > HELD) held.splice(0, held.length - HELD)
  if ((send || file) && !timer) timer = setTimeout(() => { timer = null; void flush() }, 300)
}

// Everything held, to the sidecar or the file. Lines nothing took are held
// again: the next taker gets them.
async function flush() {
  if (!held.length && !(file && handed.length)) return
  const lines = held.splice(0, held.length)
  if (send) {
    try { if (await send(lines)) return } catch { /* the sidecar is gone */ }
  }
  if (!file) {
    held.unshift(...lines.slice(-(HELD - held.length)))
    return
  }
  const all = [...handed, ...lines]
  handed = [] // a sidecar that never answered may not have written them
  try {
    fs.appendFileSync(file, all.map((l) => `${stamp(l.at)} [${run}] ${l.level.toUpperCase()} longbox.shell: ${l.text}\n`).join(''))
  } catch { /* a log that cannot be written never stops the app */ }
}

module.exports = {
  run,
  info: (text) => say('info', text),
  warn: (text) => say('warning', text),
  error: (text, err) => say('error', err ? `${text} ${(err && err.stack) || err}` : text),
  // the lines said so far, for the sidecar to write first as it starts
  handOver() {
    handed = held.splice(0, held.length).slice(-FIRST)
    return handed
  },
  // the sidecar answers: `fn(lines)` sends them, and says whether they were taken
  to(fn) { send = fn; handed = []; void flush() },
  // no sidecar to take them any more: the file itself
  alone(path) { send = null; file = path; void flush() },
  flush,
}
