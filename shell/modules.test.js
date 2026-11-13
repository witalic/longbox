'use strict'
// node --test "shell/*.test.js" — the shell's modules as written. A function
// declared twice at the top of a module is replaced by the later one for every
// caller, and nothing says so; until eslint (no-redeclare) runs in CI, this
// does (ROADMAP: linters).
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

test('no shell module declares a top-level function twice', () => {
  for (const name of fs.readdirSync(__dirname).filter((n) => n.endsWith('.js'))) {
    const seen = new Set()
    for (const m of fs.readFileSync(path.join(__dirname, name), 'utf8').matchAll(/^(?:async\s+)?function\s*\*?\s*([\w$]+)/gm)) {
      assert.ok(!seen.has(m[1]), `${name}: function ${m[1]} is declared twice`)
      seen.add(m[1])
    }
  }
})
