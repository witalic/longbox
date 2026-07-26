'use strict'
// Injected into the <webview> that hosts a source site (design/state-model.md).
//
// Two jobs, all read-only against the page:
//  1. Pick mode — highlight the element under the cursor; on click hand the host
//     the element's DOM chain so the inspector can compose a precise selector,
//     and live-preview any candidate selector (highlight + count + values).
//  2. Snapshot — given recipe rules (ordered candidate lists), extract field
//     values from the RENDERED DOM in one shot, including cover bytes fetched
//     through the page's own context (cookies + referer) so what is captured is
//     exactly what the user sees. The host builds the draft from this snapshot,
//     never from the page.

const { ipcRenderer, webFrame } = require('electron')

// Kill the File System Access API in the PAGE's world: sites that "save" via
// showSaveFilePicker bypass Electron's will-download entirely, so the armed
// chapter capture never sees the file. Without FSA they fall back to a classic
// blob download, which will-download intercepts.
webFrame.executeJavaScript(
  'try { delete window.showSaveFilePicker; delete window.showDirectoryPicker; delete window.showOpenFilePicker } catch (e) {}',
).catch(() => {})

let picking = false
let overlay = null

function esc(s) {
  return window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&')
}
function usefulClass(c) {
  // drop our own highlight marker and dynamic/stateful classes: anything with a
  // digit (`tag-2937`, `col-6`) or a known state token — they make
  // otherwise-identical items look distinct.
  return c && c !== '__lb_hit' && c.length < 40 && !/\d/.test(c) &&
    !/(^|[-_])(active|hover|selected|open|show|is|js|css|sc|ng|svelte)([-_]|$)/i.test(c)
}
function leafSeg(el) {
  const tag = el.tagName.toLowerCase()
  const classes = [...el.classList].filter(usefulClass).slice(0, 2)
  return classes.length ? `${tag}.${classes.map(esc).join('.')}` : tag
}
function sameKind(a, b) { return a.tagName === b.tagName && leafSeg(a) === leafSeg(b) }
// Inline "chip" containers a click may land inside — climbing to one of these to catch the
// whole repeated item is safe; climbing into a block (div/td/section) is NOT (it would
// swallow a whole column), so those are excluded.
const CHIP_TAGS = new Set(['A', 'SPAN', 'B', 'I', 'EM', 'STRONG', 'SMALL', 'LABEL', 'LI'])
function repeats(el) {
  return !!el.parentElement && [...el.parentElement.children].filter((c) => sameKind(c, el)).length > 1
}
function repeatingUnit(el) {
  if (repeats(el)) return el
  const p = el.parentElement
  if (p && CHIP_TAGS.has(p.tagName) && repeats(p)) return p
  return el
}
// A stable leading part of a link that identifies its CATEGORY, so links that are otherwise
// identical (Author / Magazine / Parody, all `<a class="has-text-primary">`) can be told
// apart by where they point:  /mangaka/azuse → "/mangaka/",  ?q=artist:… → "?q=artist:"
function hrefPrefix(el) {
  const raw = (el.getAttribute && el.getAttribute('href')) || ''
  let m = raw.match(/^(\/[^/?#]+\/)/)
  if (m) return m[1]
  m = raw.match(/^([^?#]*\?[^=&]*=[A-Za-z]+:)/)
  if (m) return m[1]
  m = raw.match(/^([^?#]*\?[^=&]*=)/)
  return m ? m[1] : ''
}

// ---- DOM chain for the inspector (the host builds the selector from this) ----
function nthOfType(el) {
  const p = el.parentElement
  if (!p) return 1
  return [...p.children].filter((c) => c.tagName === el.tagName).indexOf(el) + 1
}
function interestingAttrs(el) {
  const out = []
  for (const a of el.attributes || []) {
    const n = a.name
    if (n === 'class' || n === 'style' || n === 'id') continue
    const keep = n === 'href' || n === 'src' || n === 'rel' || n === 'itemprop' ||
      n === 'alt' || n === 'title' || n === 'type' || n.startsWith('data-')
    if (keep && a.value && a.value.length < 200) {
      const url = n === 'href' || n === 'src'
      out.push({ n, v: a.value, url, prefix: url ? hrefPrefix(el) : '' })
    }
    if (out.length >= 6) break
  }
  return out
}
// Ancestor chain (leaf → up), each node fully described so the host can compose a selector
// from exactly the parts the user toggles. Dynamic/state classes are flagged, not dropped.
function chainOf(el) {
  const chain = []
  let cur = el
  while (cur && cur.nodeType === 1 && cur.tagName.toLowerCase() !== 'html' && chain.length < 8) {
    chain.push({
      tag: cur.tagName.toLowerCase(),
      id: cur.id || '',
      // never leak our own highlight marker into the inspector
      classes: [...cur.classList].filter((c) => c !== '__lb_hit').map((c) => ({ n: c, dyn: !usefulClass(c) })),
      attrs: interestingAttrs(cur),
      nth: nthOfType(cur),
      label: leafSeg(cur),
    })
    cur = cur.parentElement
  }
  return chain
}
function defaultTarget(el) { return repeatingUnit(el) === el ? 0 : 1 }
function textOf(el) { return (el.textContent || '').replace(/\s+/g, ' ').trim() }
// The row's LEADING text node ("Artists: <chips…>") — many sites label rows with
// bare text, not a label element.
function leadingText(el) {
  for (const n of el.childNodes) {
    if (n.nodeType === 3) {
      const t = (n.textContent || '').replace(/\s+/g, ' ').trim()
      if (t) return t
    } else if (n.nodeType === 1 && textOf(n)) {
      break // first content is an element → the row has no leading text label
    }
  }
  return ''
}
// A row label is a SHORT word/phrase ("Автор", "Artists"), never a title: cap
// the length, reject digits, brackets and long phrases — otherwise a page
// heading sneaks in as a "label" and the anchor grabs the whole info block.
function labelish(t) {
  return !!t && t.length <= 24 && !/\d/.test(t) && !/[[\]()（）【】|·—]/.test(t)
    && t.split(' ').length <= 3
}
// The row label that NAMES the picked value: the immediate preceding sibling —
// or the row's leading text node — at this level, the parent, or one above.
// A tight walk on purpose: key-value rows are compact; anything further up is
// page furniture, not a label (state-model §6: the text-anchor candidate).
function anchorLabel(el) {
  let cur = el
  for (let depth = 0; cur && depth < 3; depth++) {
    const sib = cur.previousElementSibling
    if (sib) {
      const t = textOf(sib).replace(/[:：]\s*$/, '')
      if (labelish(t) && t !== textOf(el)) return t
    }
    const p = cur.parentElement
    if (p) {
      const lead = leadingText(p).replace(/[:：]\s*$/, '')
      if (labelish(lead)) return lead
    }
    cur = p
  }
  return ''
}
// A generated structural fallback for the recipe: the leaf scoped under its
// most DISCRIMINATING ancestor — a link with a category href prefix first
// (`a[href^="/artist/"] span.name`; on chip sites classes are identical across
// rows and only the href tells them apart), else the nearest classed ancestor.
function structuralPath(chain, target) {
  const node = chain[target]
  if (!node) return ''
  for (let i = target + 1; i < chain.length; i++) {
    const seg = chain[i]
    const pref = (seg.attrs || []).find((a) => a.url && a.prefix)
    if (pref) return `${seg.tag}[${pref.n}^="${String(pref.prefix).replace(/"/g, '\\"')}"] ${node.label}`
  }
  for (let i = target + 1; i < chain.length; i++) {
    const seg = chain[i]
    if (seg.classes.some((c) => !c.dyn)) return `${seg.label} ${node.label}`
  }
  return node.label
}

// ---- live highlight of what a selector matches ----
let hlStyle = null
function ensureHl() {
  if (hlStyle) return
  hlStyle = document.createElement('style')
  hlStyle.textContent = '.__lb_hit{outline:2px solid #7089e6 !important;outline-offset:2px;background:rgba(112,137,230,.16) !important}'
  document.documentElement.appendChild(hlStyle)
}
function clearHighlight() { for (const e of document.querySelectorAll('.__lb_hit')) e.classList.remove('__lb_hit') }
function applyHighlight(els) { ensureHl(); clearHighlight(); for (const e of els) e.classList.add('__lb_hit') }

function ensureOverlay() {
  if (overlay) return overlay
  overlay = document.createElement('div')
  overlay.style.cssText =
    'position:fixed;z-index:2147483647;pointer-events:none;box-sizing:border-box;' +
    'border:2px solid #7089e6;background:rgba(112,137,230,.14);border-radius:3px;display:none'
  document.documentElement.appendChild(overlay)
  return overlay
}
function moveOverlay(el) {
  const r = el.getBoundingClientRect()
  const ov = ensureOverlay()
  ov.style.display = 'block'
  ov.style.left = `${r.left}px`
  ov.style.top = `${r.top}px`
  ov.style.width = `${r.width}px`
  ov.style.height = `${r.height}px`
}
function hideOverlay() { if (overlay) overlay.style.display = 'none' }

// Sites often float an EMPTY overlay (e.g. <div class="filter_overlay">) above
// the very image it dims — a pick must land on what the user SEES, so a click
// on a contentless element digs through the point stack to the first element
// that actually shows something (an image, a background image, or text).
function hasVisual(el) {
  if (!el || el === overlay || el.nodeType !== 1) return false
  const tag = el.tagName
  if (tag === 'IMG' || tag === 'PICTURE' || tag === 'VIDEO' || tag === 'CANVAS' || /svg/i.test(tag)) return true
  if ((el.textContent || '').trim()) return true
  try {
    const bg = getComputedStyle(el).backgroundImage
    if (bg && bg !== 'none') return true
  } catch { /* detached */ }
  return false
}
function resolveTarget(e) {
  const t = e.target
  if (hasVisual(t)) return t
  const stack = document.elementsFromPoint(e.clientX, e.clientY) || []
  for (const el of stack) {
    if (el === t || el === overlay || el === document.documentElement || el === document.body) continue
    if (hasVisual(el)) return el
  }
  return t
}

function onMove(e) {
  if (!picking || e.target === overlay) return
  moveOverlay(resolveTarget(e))
}

// ---- values ----
function absUrl(u) { try { return new URL(u, location.href).href } catch { return u || '' } }
// Obvious placeholders a lazy-loader shows before the real image arrives.
const _PLACEHOLDER = /^data:|(?:blank|placeholder|loading|spacer|lazy|1x1|transparent|default|no[-_]?(?:cover|image))\b/i
function largestSrcset(val) {
  if (!val) return ''
  let best = '', bestW = -1
  for (const part of val.split(',')) {
    const [url, d] = part.trim().split(/\s+/)
    const w = d && d.endsWith('w') ? parseInt(d, 10) : d && d.endsWith('x') ? parseFloat(d) * 1000 : 0
    if (url && w >= bestW) { bestW = w; best = url }
  }
  return best
}
// The real image behind an <img>: a loaded, non-placeholder source, then srcset (largest),
// then common data-* lazy attrs — so a not-yet-loaded cover isn't grabbed as a black blank.
function bestImageSrc(img) {
  const cands = [
    img.currentSrc, largestSrcset(img.getAttribute('srcset')),
    img.getAttribute('data-src'), img.getAttribute('data-original'), img.getAttribute('data-lazy-src'),
    largestSrcset(img.getAttribute('data-srcset')), img.getAttribute('src'),
  ]
  for (const u of cands) if (u && !_PLACEHOLDER.test(u)) return absUrl(u)
  return absUrl(img.currentSrc || img.src || img.getAttribute('src') || '')
}
function nodeValue(n, attr) {
  if (attr) {
    if ((attr === 'src' || attr === 'currentSrc') && n.tagName && n.tagName.toLowerCase() === 'img') return bestImageSrc(n)
    const v = n.getAttribute && n.getAttribute(attr)
    return v ? ((attr === 'href' || attr === 'src') ? absUrl(v) : v) : ''
  }
  if (n.tagName && n.tagName.toLowerCase() === 'img') return bestImageSrc(n)
  return (n.textContent || '').replace(/\s+/g, ' ').trim()
}
// A small preview snapshot of an already-loaded image, straight from the page bitmap.
// Cross-origin images taint the canvas → return '' and the host falls back to the URL.
function imgPreview(el) {
  try {
    const w = el.naturalWidth || el.width, h = el.naturalHeight || el.height
    if (!w || !h) return ''
    const scale = Math.min(1, 180 / w)
    const c = document.createElement('canvas')
    c.width = Math.round(w * scale)
    c.height = Math.round(h * scale)
    c.getContext('2d').drawImage(el, 0, 0, c.width, c.height)
    return c.toDataURL('image/jpeg', 0.72)
  } catch { return '' }
}

// ---- candidate resolution (recipe v2: ordered fallbacks against the rendered DOM) ----
// Resolve an anchor candidate: find the element whose OWN text is the label,
// then read the value from its following sibling(s) (or the parent's neighbor
// when the label is wrapped). Position-independent by construction — reordered
// or missing info rows on another page cannot shift it onto the wrong value.
// The chip-like value nodes inside a holder: its texty children individually
// (so "Художники: A, B" and tag rows yield each chip), else the holder itself.
function holderValueNodes(holders) {
  const nodes = []
  for (const h of holders) {
    const kids = [...h.children].filter((k) => textOf(k))
    if (kids.length > 1) nodes.push(...kids)
    else if (textOf(h)) nodes.push(h)
  }
  return nodes
}
// The value NODES named by a row label. Two row shapes are recognized:
//  (a) a dedicated label ELEMENT → values live in its following sibling(s);
//  (b) a row with a leading TEXT label ("Artists: <chips…>") → values are the
//      row's element children after that text.
function anchorNodes(label) {
  const want = String(label || '').replace(/[:：]\s*$/, '').trim().toLowerCase()
  if (!want) return []
  for (const el of document.querySelectorAll('body *')) {
    if (el.childElementCount <= 1 && textOf(el).replace(/[:：]\s*$/, '').toLowerCase() === want) {
      const holders = []
      for (let sib = el.nextElementSibling; sib; sib = sib.nextElementSibling) holders.push(sib)
      if (!holders.length && el.parentElement && el.parentElement.childElementCount === 1
          && el.parentElement.nextElementSibling) {
        holders.push(el.parentElement.nextElementSibling)
      }
      const nodes = holderValueNodes(holders)
      if (nodes.length) return nodes
    }
    if (leadingText(el).replace(/[:：]\s*$/, '').toLowerCase() === want) {
      const nodes = holderValueNodes([...el.children].filter((k) => textOf(k)))
      if (nodes.length) return nodes
    }
  }
  return []
}

// A meaningful metadata value contains at least one letter or digit — helper
// links a site renders next to chips ("+", "-", "×") are never values.
function meaningful(v) {
  return typeof v === 'string' && /[\p{L}\p{N}]/u.test(v)
}

function anchorValue(label, attr, mode) {
  const vals = anchorNodes(label).map((n) => nodeValue(n, attr)).filter(meaningful)
  if (!vals.length) return null
  return mode === 'list' ? vals : vals[0]
}

function metaValue(key) {
  if (key.startsWith('ld:')) {
    const want = key.slice(3)
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        let data = JSON.parse(s.textContent)
        if (Array.isArray(data)) data = data[0]
        if (data && typeof data === 'object' && data[want] != null) {
          const v = data[want]
          return typeof v === 'string' ? v : (v && v.url) || ''
        }
      } catch { /* malformed ld+json */ }
    }
    return ''
  }
  const el = document.querySelector(`meta[property="${key}"], meta[name="${key}"]`)
  return (el && el.getAttribute('content')) || ''
}
// Try a field's candidates in order; report which one worked so the host can
// record it. A field with no live match resolves to empty — an expected state.
function resolveField(rule) {
  const mode = rule.mode === 'list' ? 'list' : 'single'
  for (let i = 0; i < (rule.candidates || []).length; i++) {
    const cand = rule.candidates[i]
    try {
      if (cand.kind === 'anchor') {
        const v = anchorValue(cand.selector, cand.attr, mode)
        if (v && (!Array.isArray(v) || v.length)) return { value: v, used: i }
        continue
      }
      if (cand.kind === 'meta') {
        const v = metaValue(cand.selector)
        if (v) return { value: mode === 'list' ? [v] : ((cand.attr === 'src' || cand.selector.endsWith('image')) ? absUrl(v) : v), used: i }
        continue
      }
      const els = [...document.querySelectorAll(cand.selector)]
      if (!els.length) continue
      if (mode === 'list') {
        const vals = els.map((e) => nodeValue(e, cand.attr)).filter(meaningful)
        if (vals.length) return { value: vals, used: i }
      } else {
        const el = els[rule.index || 0] || els[0]
        const v = nodeValue(el, cand.attr)
        // URLs (cover/href) pass as-is; text must be a meaningful value
        if (v && (cand.attr || meaningful(v))) return { value: v, used: i, el }
      }
    } catch { /* bad selector → next candidate */ }
  }
  return null
}

// Cover bytes THROUGH the page context: same cookies, referer = this page. This
// is the mandatory path — hotlink-protected and gated images only work here.
const MAX_COVER = 8 * 1024 * 1024
async function fetchCoverBytes(url) {
  try {
    const res = await fetch(url, { credentials: 'include' })
    if (!res.ok) return null
    const ct = (res.headers.get('content-type') || '').split(';')[0].trim().toLowerCase()
    if (!ct.startsWith('image/')) return null
    const buf = await res.arrayBuffer()
    if (!buf.byteLength || buf.byteLength > MAX_COVER) return null
    let bin = ''
    const bytes = new Uint8Array(buf)
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000))
    }
    return { data: btoa(bin), contentType: ct, sourceUrl: url }
  } catch { return null }
}

// ---- pick mode ----
let lastPicked = null // the exact element the user clicked — anchors the position control
function onClick(e) {
  if (!picking) return
  e.preventDefault()
  e.stopPropagation()
  picking = false
  hideOverlay()
  clearHighlight() // a lingering preview highlight must not leak into the chain
  const picked = resolveTarget(e) // dig through content-less overlays
  lastPicked = picked
  const chain = chainOf(picked)
  const target = defaultTarget(picked)
  ipcRenderer.sendToHost('inspect', {
    chain,
    target,
    structural: structuralPath(chain, target),
    anchor: anchorLabel(picked),
  })
}

ipcRenderer.on('set-picking', (_e, value) => { picking = !!value; if (!picking) hideOverlay() })

// Test a candidate against the LIVE page: highlight the matches and report the
// count + sample values (image → also a bitmap preview) so the inspector shows it
// live. The payload is either a CSS selector or {selector, anchor} — with an
// anchor the LABEL match is tested, exactly what auto-fill will run later (I4).
ipcRenderer.on('preview', (_e, req) => {
  const selector = typeof req === 'string' ? req : (req && req.selector) || ''
  const anchor = typeof req === 'object' && req ? req.anchor || '' : ''
  let matched = []
  if (anchor) matched = anchorNodes(anchor)
  else { try { matched = selector ? [...document.querySelectorAll(selector)] : [] } catch { /* bad selector */ } }
  applyHighlight(matched)
  const values = matched.slice(0, 24).map((n) => nodeValue(n)).filter((v) => v !== '')
  const first = matched[0]
  const preview = first && first.tagName && first.tagName.toLowerCase() === 'img' ? imgPreview(first) : ''
  // where the CLICKED element sits among the matches (or inside one of them), so
  // the position control lands on what the user actually picked, never on match #1
  let pickedIndex = -1
  if (lastPicked) {
    pickedIndex = matched.indexOf(lastPicked)
    if (pickedIndex < 0) pickedIndex = matched.findIndex((m) => m.contains(lastPicked))
  }
  ipcRenderer.sendToHost('preview-result', { count: matched.length, values, preview, pickedIndex })
})
ipcRenderer.on('stop-inspect', () => clearHighlight())

// ---- the snapshot (one immutable read of the rendered page) ----
ipcRenderer.on('snapshot', async (_e, req) => {
  const fields = {}
  const used = {}
  let cover = null
  for (const key of Object.keys(req.fields || {})) {
    const hit = resolveField(req.fields[key])
    if (!hit) continue
    if (key === 'cover') {
      const url = Array.isArray(hit.value) ? hit.value[0] : hit.value
      if (url) {
        cover = await fetchCoverBytes(absUrl(url))
        if (!cover) fields.cover = absUrl(url) // URL-only fallback (server fetch at commit)
      }
      used[key] = hit.used
      continue
    }
    fields[key] = hit.value
    used[key] = hit.used
  }
  ipcRenderer.sendToHost('snapshot-result', {
    url: location.href, pageTitle: document.title, fields, used, cover,
  })
})

// A window.open / middle-click / target=_blank was denied natively (main process) and
// routed here — hand the URL to the app so it opens as an in-app tab, not an OS window.
ipcRenderer.on('open-url-as-tab', (_e, url) => ipcRenderer.sendToHost('open-tab', url))

document.addEventListener('mousemove', onMove, true)
document.addEventListener('click', onClick, true)
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && picking) { picking = false; hideOverlay(); ipcRenderer.sendToHost('cancel') }
}, true)
// Standard-browser mouse buttons: X1 (3) = back, X2 (4) = forward. Suppress the native
// action on press, act on release, so history navigation happens exactly once.
window.addEventListener('mousedown', (e) => {
  if (e.button === 3 || e.button === 4) { e.preventDefault(); e.stopPropagation() }
}, true)
window.addEventListener('mouseup', (e) => {
  if (e.button === 3) { e.preventDefault(); ipcRenderer.sendToHost('history', 'back') }
  else if (e.button === 4) { e.preventDefault(); ipcRenderer.sendToHost('history', 'forward') }
}, true)
