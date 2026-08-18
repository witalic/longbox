<script setup lang="ts">
// The browser IS a browser (design/browser-refactor.md §A): tabs on top, a real
// toolbar, one persistent session. Browsing never touches the draft; the right
// dock holds the capture panel (the draft) and, during a pick, the inspector.
// All page-facing work goes through the preload: pick chains, live selector
// previews, and one-shot snapshots (rendered DOM + cover bytes via page context).
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api, ApiError } from '../api'
import { activeTab, browser, newTab, newTabBackground, onTabNavigated, setTabLoading, setTabTitle, tabById } from '../browser'
import CapturePanel from '../components/CapturePanel.vue'
import Icon from '../components/Icon.vue'
import PickInspector, { type ChainNode, type PickUse, type ProbeReq, type ProbeResult } from '../components/PickInspector.vue'
import {
  applyCapture, applyCoverCapture, applyCoverUrlAuto, draftState, EDITABLE_FIELDS,
  mergeSnapshot, noteCaptureSource, type EditableField, type Snapshot,
} from '../draft'
import { pageCapture, pageFilter, pageKeyFor, stopCaptureFor, type PickField } from '../pagecapture'
import type { Candidate, FieldRule, Recipe } from '../data'
import { cache, store } from '../store'

const isElectron = typeof navigator !== 'undefined' && navigator.userAgent.includes('Electron')

// ---- one <webview> per tab (keeps each tab's page + history alive) ----
const wvRefs = new Map<string, any>()
const wvSetters = new Map<string, (el: any) => void>()
function wvRef(id: string): (el: any) => void {
  let fn = wvSetters.get(id)
  if (!fn) { fn = (el: any) => { if (el) wvRefs.set(id, el); else wvRefs.delete(id) }; wvSetters.set(id, fn) }
  return fn
}
function wv(): any { return browser.activeId ? wvRefs.get(browser.activeId) : null }

const active = computed(() => activeTab())
function hostOf(url: string): string { try { return new URL(url).hostname.replace(/^www\./, '') } catch { return '' } }
const domain = computed(() => hostOf(active.value?.url || ''))
const isHttps = computed(() => (active.value?.url || '').startsWith('https://'))

// ---- toolbar ----
const addr = ref('')
watch(() => [active.value?.id, active.value?.url], () => { addr.value = active.value?.url || '' }, { immediate: true })

function loadInActive(url: string) {
  const tab = active.value
  if (!tab || !url) return
  tab.url = url
  const el = wv()
  try { el?.loadURL ? el.loadURL(url) : el && (el.src = url) } catch { if (el) el.src = url }
}
function go() {
  const q = addr.value.trim()
  if (!q) return
  const url = /^https?:\/\//.test(q)
    ? q
    : /^[^\s]+\.[a-z]{2,}([/:?].*)?$/i.test(q)
      ? 'https://' + q
      : 'https://www.google.com/search?q=' + encodeURIComponent(q)
  loadInActive(url)
}
function nav(dir: 'back' | 'forward' | 'reload') {
  const el = wv()
  if (!el) return
  if (dir === 'back' && el.canGoBack?.()) el.goBack()
  else if (dir === 'forward' && el.canGoForward?.()) el.goForward()
  else if (dir === 'reload') el.reload?.()
}
function goHomePage() { loadInActive(store.browseHomepage) }

// per-tab zoom
const ZOOMS = [0.5, 0.67, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2]
function zoomStep(d: number) {
  const tab = active.value
  if (!tab) return
  const i = Math.max(0, Math.min(ZOOMS.length - 1, ZOOMS.indexOf(closestZoom(tab.zoom)) + d))
  tab.zoom = ZOOMS[i]
  wv()?.setZoomFactor?.(tab.zoom)
}
function closestZoom(z: number): number {
  return ZOOMS.reduce((a, b) => (Math.abs(b - z) < Math.abs(a - z) ? b : a))
}
function zoomReset() {
  const tab = active.value
  if (!tab) return
  tab.zoom = 1
  wv()?.setZoomFactor?.(1)
}

// find in page
const findOpen = ref(false)
const findText = ref('')
const findCount = reactive({ active: 0, total: 0 })
function findNext(forward = true) {
  if (findText.value) wv()?.findInPage?.(findText.value, { forward, findNext: true })
}
watch(findText, (t) => {
  if (t) wv()?.findInPage?.(t)
  else { wv()?.stopFindInPage?.('clearSelection'); findCount.active = 0; findCount.total = 0 }
})
function closeFind() {
  findOpen.value = false
  findText.value = ''
  wv()?.stopFindInPage?.('clearSelection')
}
function onFound(e: any) {
  findCount.active = e.result?.activeMatchOrdinal ?? 0
  findCount.total = e.result?.matches ?? 0
}

function devtools() { wv()?.openDevTools?.() }

// ---- per-domain recipes (candidate-based; the client extracts, the backend stores) ----
const recipes = reactive<Record<string, Recipe | null>>({})
async function getRecipe(host: string): Promise<Recipe | null> {
  if (!(host in recipes)) {
    try { recipes[host] = await api.recipe(host) } catch { recipes[host] = null }
  }
  return recipes[host]
}
watch(domain, async (h) => {
  if (!h) return
  const r = await getRecipe(h)
  // page capture is taught PER DOMAIN — a site that already knows its reader
  // arms with the right selector immediately. An ACTIVE capture keeps its own:
  // browsing elsewhere must never repoint a running session.
  const pages = r?.fields?.pages?.candidates?.[0]?.selector
  if (pages && !pageCapture.active) pageCapture.selector = pages
})

function metaCand(key: string, attr = 'content'): Candidate {
  return { kind: 'meta', selector: key, attr, note: 'fallback' }
}
// Page-metadata fallbacks — appended after picked candidates so a learned
// selector always wins, but a fresh site still yields something.
const OG_FALLBACKS: Record<string, Candidate[]> = {
  title: [metaCand('og:title'), metaCand('ld:name')],
  desc: [metaCand('og:description'), metaCand('description'), metaCand('ld:description')],
  cover: [metaCand('og:image'), metaCand('ld:image')],
}
function snapshotRules(recipe: Recipe | null): Record<string, FieldRule> {
  const out: Record<string, FieldRule> = {}
  for (const [key, rule] of Object.entries(recipe?.fields || {})) {
    if (!EDITABLE_FIELDS.has(key)) continue
    const extra = (OG_FALLBACKS[key] || []).filter(
      (f) => !rule.candidates.some((c) => c.kind === 'meta' && c.selector === f.selector))
    out[key] = { ...rule, candidates: [...rule.candidates, ...extra] }
  }
  // Bootstrap for untaught sites: ONLY the identity fields (title, cover) come
  // from page metadata on their own. Anything else (desc, …) fills only when
  // the user actually taught that field — no silently invented values.
  for (const key of ['title', 'cover']) {
    if (!out[key]) out[key] = { mode: 'single', candidates: OG_FALLBACKS[key] }
  }
  return out
}

async function saveRecipeField(host: string, field: string, rule: FieldRule | null) {
  const cur = recipes[host]
  const next: Recipe = {
    domain: host,
    version: cur?.version ?? 1,
    fields: { ...(cur?.fields || {}) },
    chapters: cur?.chapters ?? null, // a previously learned list rule is kept as-is
  }
  if (field && rule) next.fields[field] = rule
  try {
    recipes[host] = await api.saveRecipe(host, next)
  } catch {
    recipes[host] = next // keep the learned rule locally even if persisting failed
  }
  return recipes[host]
}

// ---- request/response over the preload (single in-flight per kind) ----
interface SnapWire {
  url: string
  fields: Record<string, string | string[]>
  cover: { data: string; contentType: string; sourceUrl: string } | null
}
let snapResolve: ((r: SnapWire | null) => void) | null = null

function requestSnapshot(fields: Record<string, FieldRule>): Promise<SnapWire | null> {
  const el = wv()
  if (!el) return Promise.resolve(null)
  return new Promise((resolve) => {
    snapResolve?.(null)
    snapResolve = resolve
    el.send?.('snapshot', JSON.parse(JSON.stringify({ fields })))
    setTimeout(() => { if (snapResolve === resolve) { snapResolve = null; resolve(null) } }, 20000)
  })
}

// ---- capture flows ----
const busy = ref(false)
const panel = ref<InstanceType<typeof CapturePanel> | null>(null)

// The explicit snapshot: recipe + page metadata → merge into the draft
// (auto/empty fields only — the merge invariant lives in draft.ts).
async function autofill() {
  const tab = active.value
  if (!tab || !draftState.cur) return
  busy.value = true
  try {
    const recipe = await getRecipe(domain.value)
    const snap = await requestSnapshot(snapshotRules(recipe))
    if (!snap) { panel.value?.showFlash('could not read the page'); return }
    // clean captured values with the rules' SAVED flags, not the defaults
    const flags: Record<string, { lower: boolean; stripCounts: boolean }> = {}
    for (const [key, rule] of Object.entries(recipe?.fields || {})) {
      flags[key] = { lower: !!rule.lower, stripCounts: !!rule.stripCounts }
    }
    const s: Snapshot = {
      url: snap.url, domain: domain.value, recipeVersion: recipe?.version ?? 0,
      fields: snap.fields, flags, cover: snap.cover ?? undefined,
    }
    const written = mergeIntoDraft(s)
    panel.value?.showFlash(written.length ? `✓ filled: ${written.join(', ')}` : 'nothing new — manual fields are kept')
  } finally {
    busy.value = false
  }
}
function mergeIntoDraft(s: Snapshot): string[] {
  // cover arrived as URL-only (byte fetch failed)? hand it to the merge as a field
  const written = new Set<string>()
  const coverUrl = typeof s.fields.cover === 'string' ? s.fields.cover : ''
  const { cover: _drop, ...fields } = s.fields
  for (const f of mergeSnapshot({ ...s, fields })) written.add(f)
  if (!s.cover && coverUrl && draftState.cur) {
    const d = draftState.cur
    if (d.provenance.cover?.origin === 'auto' || d.cover.kind === 'none') {
      applyCoverUrlAuto(coverUrl, s.url, { url: s.url, recipeVersion: s.recipeVersion })
      written.add('cover')
    }
  }
  return [...written]
}

// pick mode
const pickingField = ref<PickField | null>(null)
const inspect = ref<{ field: string; chain: ChainNode[]; target: number; structural: string; anchor: string } | null>(null)
const probe = reactive<ProbeResult>({ count: 0, values: [], preview: '', pickedIndex: -1 })

function startPick(field: PickField) {
  const el = wv()
  if (!el) return
  if (pickingField.value === field) { pickingField.value = null; el.send?.('set-picking', false); return }
  pickingField.value = field
  inspect.value = null
  el.send?.('set-picking', true)
}
function repick() { wv()?.send?.('set-picking', true) }
function closeInspect() {
  wv()?.send?.('stop-inspect')
  inspect.value = null
  pickingField.value = null
}
function onProbe(req: ProbeReq) {
  // page picks preview the FILTERED set — what capture would actually take
  const kind = pickingField.value === 'pages' ? 'pages' : ''
  wv()?.send?.('preview', JSON.parse(JSON.stringify({ ...req, kind, filter: { ...pageFilter } })))
}
// re-teaching starts from the field's stored cleanup flags
const savedRule = computed(() => {
  const field = inspect.value?.field
  const rule = field ? recipes[domain.value]?.fields[field] : undefined
  return rule ? { lower: !!rule.lower, stripCounts: !!rule.stripCounts } : undefined
})

async function onUse(result: PickUse) {
  const ins = inspect.value
  const tab = active.value
  if (!ins || !tab) return
  const host = domain.value
  // page capture teaches one selector that is not a draft field: the page
  // images. It persists in the domain's recipe, so the site stays taught.
  if (ins.field === 'pages') {
    await saveRecipeField(host, 'pages', {
      mode: 'list',
      candidates: [{ kind: 'css', selector: result.selector, attr: 'src', note: 'picked' }],
    })
    pageCapture.selector = result.selector
    pageCapture.domain = host
    closeInspect()
    panel.value?.showFlash(`✓ page images taught (${result.values.length} on this page)`)
    if (pageCapture.active) scanNow(pageCapture.tabId)
    return
  }
  const field = ins.field as EditableField
  // The learned rule, most-reliable first: the row-label anchor (when the user
  // kept it on) is position-independent, so reordered info rows on another page
  // cannot shift the match; then the picked selector, then generated fallbacks.
  const attr = field === 'cover' ? 'src' : undefined
  const candidates: Candidate[] = []
  if (result.useAnchor && ins.anchor) {
    candidates.push({ kind: 'anchor', selector: ins.anchor, attr, note: 'label' })
  }
  candidates.push({ kind: 'css', selector: result.selector, attr, note: 'picked' })
  if (ins.structural && ins.structural !== result.selector) {
    candidates.push({ kind: 'css', selector: ins.structural, attr, note: 'structural' })
  }
  candidates.push(...(OG_FALLBACKS[field] || []))
  const rule: FieldRule = {
    mode: result.mode, index: result.index,
    lower: result.lower, stripCounts: result.stripCounts, candidates,
  }
  const saved = await saveRecipeField(host, field, rule)
  const prov = { url: tab.url, recipeVersion: saved?.version ?? 0 }
  if (field === 'cover') {
    // bytes through the page context; URL-only if the fetch is refused
    const snap = await requestSnapshot({ cover: rule })
    if (snap?.cover) applyCoverCapture(snap.cover, prov)
    else if (typeof snap?.fields.cover === 'string' && snap.fields.cover) applyCoverUrlAuto(snap.fields.cover, tab.url, prov)
    else panel.value?.showFlash('could not resolve the cover image')
  } else {
    applyCapture(field, result.mode === 'list' ? result.values : result.values[0] ?? '',
      { lower: result.lower, stripCounts: result.stripCounts }, prov)
  }
  noteCaptureSource(host, tab.url) // a per-field pick binds the source too
  closeInspect()
}

// ---- page capture: scan the reader page, fetch what the vault lacks ----
interface ScanWire { url: string; urls: string[]; relaxed?: string }
interface PageImage { url: string; data: string; contentType: string }

let bytesResolve: ((imgs: PageImage[]) => void) | null = null
let byteBuf: PageImage[] = []
let onByte: ((done: number) => void) | null = null

function scanNow(tabId: string | null) {
  if (!tabId || !pageCapture.selector) return
  wvRefs.get(tabId)?.send?.('scan-pages', JSON.parse(JSON.stringify(
    { selector: pageCapture.selector, filter: { ...pageFilter } })))
}
function requestBytes(tabId: string, urls: string[], progress: (done: number) => void): Promise<PageImage[]> {
  const el = wvRefs.get(tabId)
  if (!el) return Promise.resolve([])
  return new Promise((resolve) => {
    bytesResolve?.([])
    byteBuf = []
    onByte = progress
    let settled = false
    const done = (imgs: PageImage[]) => {
      if (settled) return
      settled = true
      if (bytesResolve === done) bytesResolve = null
      onByte = null
      byteBuf = [] // page bytes are megabytes each — never keep them around
      window.clearTimeout(timer)
      resolve(imgs)
    }
    bytesResolve = done
    // generous: a long chapter of big pages fetches sequentially by design
    const timer = window.setTimeout(() => done(byteBuf.slice()), 180000)
    el.send?.('fetch-pages', JSON.parse(JSON.stringify({ urls })))
  })
}

// SCANNING AND FETCHING ARE SEPARATE. A page is seen in a moment; storing it
// takes seconds, and the reader does not wait. So a scan only ever QUEUES what
// it saw — fetching drains that queue behind the human. Skipping a scan because
// the previous page was still downloading is exactly how pages went missing.
// Every queued page carries the entry it was scanned for, so finishing a chapter
// while its last pages are still downloading lands them in THAT chapter while
// the next one is already being read — the queue is never re-pointed.
interface QueuedPage {
  key: string; url: string; pageUrl: string
  titleId: string; chapterId: string; label: string
}
const queued = new Map<string, QueuedPage>()
const stored = new Set<string>() // keys the vault is known to hold — never asked about twice
let draining = false
const PAGE_BATCH = 6
const slot = (chapterId: string, key: string) => `${chapterId}|${key}`

// The known-keys set only has to outlive the entries still in flight: arming a
// fresh session with an empty queue starts it over.
watch(() => pageCapture.active, (on) => { if (on && !queued.size) stored.clear() })

function onPagesScan(scan: ScanWire, tabId: string) {
  const st = pageCapture
  // only the tab the capture was ARMED on: another tab's images are not this
  // chapter's pages, whichever tab happens to be in front
  if (!st.active || tabId !== st.tabId || !st.titleId || !st.chapterId) return
  // the taught selector matched nothing but a looser one did (a per-page state
  // class) — adopt the version that works, and remember it against the domain
  // the capture was ARMED on, never whatever site is in front now
  if (scan.relaxed && scan.relaxed !== st.selector) {
    st.selector = scan.relaxed
    if (st.domain) {
      void saveRecipeField(st.domain, 'pages', {
        mode: 'list',
        candidates: [{ kind: 'css', selector: scan.relaxed, attr: 'src', note: 'picked' }],
      })
    }
  }
  // dedup by the image's NAME: CDN tokens rotate, file names don't
  let fresh = 0
  for (const url of scan.urls) {
    const key = pageKeyFor(url)
    const at = slot(st.chapterId, key)
    if (stored.has(at) || queued.has(at)) continue
    queued.set(at, {
      key, url, pageUrl: scan.url,
      titleId: st.titleId, chapterId: st.chapterId, label: st.label,
    })
    fresh++
  }
  if (!fresh) {
    if (!queued.size && !draining) {
      st.status = scan.urls.length
        ? `ch. ${st.label} — this page is already stored (${st.added} captured)`
        : `ch. ${st.label} — no page images here (${st.added} stored)`
    }
    return
  }
  void drainQueue()
}

const backlog = () => (queued.size ? ` · ${queued.size} queued` : '')

async function drainQueue(): Promise<void> {
  const st = pageCapture
  if (draining) return
  // Finishing a chapter STOPS THE SCANNING, not the storing: pages already read
  // are already the chapter's, and each one carries the entry it belongs to. So
  // a drain runs the queue out whether or not the session is still armed — the
  // panel just stops narrating once it is not.
  draining = true
  if (st.active) { st.busy = true; st.error = '' }
  let failed: { chapterId: string; batch: [string, QueuedPage][] } | null = null
  try {
    while (queued.size) {
      // Oldest first, so pages reach the vault in the order they were read, and
      // one batch is one page of one entry: its recorded source URL has to be the
      // page those images actually came from.
      const all = [...queued.entries()]
      const head = all[0][1]
      const from = head.pageUrl
      const run = { titleId: head.titleId, chapterId: head.chapterId, label: head.label, tabId: st.tabId }
      // a still-armed entry reports its running total; a finished one says so —
      // and a stopped session says nothing at all
      const say = (text: string) => {
        if (!st.active) return
        st.status = run.chapterId === st.chapterId
          ? `ch. ${run.label} — ${text}` : `finishing ch. ${run.label} — ${text}`
      }
      const batch = all
        .filter(([, p]) => p.chapterId === run.chapterId && p.pageUrl === from)
        .slice(0, PAGE_BATCH)
      failed = { chapterId: run.chapterId, batch }
      const known = new Set(await api.knownPages(run.titleId, run.chapterId, batch.map(([, p]) => p.key)))
      for (const [at, p] of batch) {
        if (!known.has(p.key)) continue
        stored.add(at)
        queued.delete(at)
      }
      const wanted = batch.filter(([, p]) => !known.has(p.key))
      if (!wanted.length) {
        say(`already stored (${st.added} pages)${backlog()}`)
        continue
      }
      // THE SHELL fetches http(s) images: the main process carries the session's
      // cookies and the reader page as Referer, and is not bound by the site's
      // CORS — an in-page fetch of the site's own CDN usually is. blob:/data:
      // pages exist only inside the page, so those go the preload route.
      const bag = new Map<string, PageImage>()
      let done = 0
      let why = ''
      for (const [, page] of wanted) {
        if (!/^https?:/i.test(page.url)) continue
        const got = await window.longbox?.fetchImage?.(page.url, page.pageUrl)
          .catch((e): { data?: string; contentType?: string; error?: string } => ({ error: String(e) }))
        if (got?.data) bag.set(page.url, { url: page.url, data: got.data, contentType: got.contentType || '' })
        else why = why || got?.error || 'no shell bridge'
        say(`fetching ${++done}/${wanted.length}${backlog()}`)
      }
      const retry = wanted.filter(([, p]) => !bag.has(p.url)).map(([, p]) => p.url)
      if (retry.length && run.tabId) {
        const fromPage = await requestBytes(run.tabId, retry, (n) => {
          say(`fetching ${done + n}/${wanted.length}${backlog()}`)
        })
        for (const img of fromPage) bag.set(img.url, img)
      }
      // Whatever came back is stored; whatever did not leaves the queue WITHOUT
      // being marked stored — re-reading that page finds it again, and the drain
      // cannot spin on an image the site will not serve.
      const fetched = wanted.filter(([, p]) => bag.has(p.url))
      const payload = fetched.map(([, p]) => ({ key: p.key, ...bag.get(p.url) as PageImage }))
      for (const [at] of wanted) queued.delete(at)
      if (!payload.length) {
        st.error = `could not fetch ${wanted.length} image(s)${why ? ` — ${why}` : ''}`
        continue
      }
      const saved = await api.capturePages(run.titleId, run.chapterId, from, payload)
      cache([saved])
      for (const [at] of fetched) stored.add(at)
      const total = saved.chapters.find((c) => c.id === run.chapterId)?.pages
      // the vault is authoritative about what it stored — never a local guess
      if (run.chapterId === st.chapterId) st.added = total ?? st.added + payload.length
      say(`${total ?? payload.length} pages captured${backlog()}`)
      if (payload.length < wanted.length) {
        st.error = `${wanted.length - payload.length} image(s) could not be fetched${why ? ` — ${why}` : ''}`
      }
    }
  } catch (e) {
    const status = e instanceof ApiError ? e.status : 0
    // 404: the row was deleted. 409: its archive cannot take pages. Either way
    // the armed entry is gone, and re-reading the same page would re-download
    // its images from the site every 2.5s — end the session instead.
    if (status === 404 || status === 409) {
      queued.clear()
      stopCaptureFor({ chapterId: failed?.chapterId })
      store.error = status === 404
        ? 'the chapter this capture was armed on no longer exists — capture stopped'
        : 'this chapter cannot take captured pages (its archive is not editable) — capture stopped'
    } else if (failed) {
      // a transient failure costs the batch it happened on, nothing else: a
      // chapter still draining behind this one keeps its pages
      for (const [at] of failed.batch) queued.delete(at)
      if (st.active) st.error = e instanceof Error ? e.message : String(e)
    }
  } finally {
    draining = false
    st.busy = false
    // pages that arrived while this drain was finishing
    if (queued.size) window.setTimeout(() => { void drainQueue() }, 0)
  }
}

// A reader page keeps loading images while the human scrolls (long strips,
// lazy loaders), so the page is re-scanned on a slow tick as well as on
// navigation. A scan that finds nothing new costs no requests and no writes.
let captureTimer: number | undefined
watch(() => pageCapture.active, (on) => {
  window.clearInterval(captureTimer)
  if (!on) return
  scanNow(pageCapture.tabId)
  captureTimer = window.setInterval(() => {
    scanNow(pageCapture.tabId)
    void drainQueue()
  }, 2500)
}, { immediate: true })
onBeforeUnmount(() => window.clearInterval(captureTimer))

// ---- webview events ----
function onIpc(e: any, tabId: string) {
  const msg = e.args?.[0]
  switch (e.channel) {
    case 'inspect':
      if (tabId === browser.activeId && pickingField.value) {
        inspect.value = {
          field: pickingField.value, chain: msg.chain, target: msg.target,
          structural: msg.structural || '', anchor: msg.anchor || '',
        }
      }
      break
    case 'preview-result':
      probe.count = msg.count
      probe.values = msg.values || []
      probe.preview = msg.preview || ''
      probe.pickedIndex = msg.pickedIndex ?? -1
      break
    case 'snapshot-result':
      // like every other channel: only the tab we asked may answer
      if (tabId !== browser.activeId) break
      snapResolve?.(msg as SnapWire)
      snapResolve = null
      break
    case 'pages-scan':
      onPagesScan(msg as ScanWire, tabId)
      break
    case 'page-bytes':
      if (msg && !msg.failed) byteBuf.push(msg as PageImage)
      onByte?.(byteBuf.length)
      break
    case 'pages-fetched':
      bytesResolve?.(byteBuf)
      bytesResolve = null
      onByte = null
      break
    case 'cancel':
      if (tabId === browser.activeId) pickingField.value = null
      break
    case 'history': {
      const el = wvRefs.get(tabId)
      if (msg === 'back' && el?.canGoBack?.()) el.goBack()
      else if (msg === 'forward' && el?.canGoForward?.()) el.goForward()
      break
    }
    case 'open-tab': {
      // middle-click / ctrl+click arrives as background — the current page stays fronted
      const req = typeof msg === 'string' ? { url: msg, background: false } : (msg as { url: string; background?: boolean })
      if (req.background) newTabBackground(req.url)
      else newTab(req.url)
      break
    }
  }
}
function onNav(e: any, tabId: string) {
  if (e?.url) onTabNavigated(tabId, e.url)
  // flipping to the next page IS the capture trigger — in the armed tab only
  if (pageCapture.active && tabId === pageCapture.tabId) {
    // an in-page fetch loop dies with the document it runs in: settle it with
    // what already arrived instead of waiting out its timeout
    bytesResolve?.(byteBuf.slice())
    // a full load answers on dom-ready; an in-page one keeps the document, so
    // its scan has to be asked for here
    window.setTimeout(() => scanNow(tabId), 300)
  }
}
function onReady(tabId: string) {
  const tab = tabById(tabId)
  if (tab && tab.zoom !== 1) wvRefs.get(tabId)?.setZoomFactor?.(tab.zoom)
  // the preload of the new document is live only now — an earlier scan request
  // would have been sent into the previous one
  if (pageCapture.active && tabId === pageCapture.tabId) scanNow(tabId)
}
</script>

<template>
  <div class="bw">
    <!-- tabs live in the app's single top strip (App.vue) — here only the toolbar -->
    <!-- toolbar -->
    <div class="toolbar">
      <span class="nb" title="Back" @click="nav('back')"><Icon name="back" :size="16" :sw="1.9" /></span>
      <span class="nb" title="Forward" @click="nav('forward')"><Icon name="forward" :size="16" :sw="1.9" /></span>
      <span class="nb" title="Reload" @click="nav('reload')"><Icon name="refresh" :size="14" :sw="1.9" /></span>
      <span class="nb" title="Home" @click="goHomePage"><Icon name="home" :size="15" :sw="1.9" /></span>
      <div class="url">
        <Icon name="lock" :size="12" :sw="1.9" :style="{ color: isHttps ? 'var(--good)' : 'var(--tx3)' }" />
        <input v-model="addr" class="urlin mono" placeholder="Search or type a URL" @keydown.enter="go" />
      </div>
      <div class="zoom">
        <span class="nb" title="Zoom out" @click="zoomStep(-1)"><Icon name="minus" :size="13" :sw="2" /></span>
        <span class="zlevel mono" title="Reset zoom" @click="zoomReset">{{ Math.round((active?.zoom ?? 1) * 100) }}%</span>
        <span class="nb" title="Zoom in" @click="zoomStep(1)"><Icon name="plus" :size="13" :sw="2" /></span>
      </div>
      <span class="nb" :class="{ act: findOpen }" title="Find in page (Ctrl+F)" @click="findOpen ? closeFind() : (findOpen = true)"><Icon name="search" :size="14" :sw="2" /></span>
      <span class="nb" title="Open DevTools" @click="devtools"><Icon name="code" :size="14" :sw="2" /></span>
      <span class="nb" :class="{ act: browser.panelOpen }" title="Toggle capture panel" @click="browser.panelOpen = !browser.panelOpen"><Icon name="panel" :size="14" :sw="1.9" /></span>
    </div>

    <!-- find bar -->
    <div v-if="findOpen" class="findbar">
      <Icon name="search" :size="13" :sw="2" />
      <input v-model="findText" class="findin" placeholder="Find in page…" @keydown.enter="findNext(!$event.shiftKey)" @keydown.esc="closeFind" />
      <span class="mono fcount">{{ findCount.total ? `${findCount.active}/${findCount.total}` : '—' }}</span>
      <span class="nb" @click="findNext(false)"><Icon name="back" :size="13" :sw="2" style="transform:rotate(90deg)" /></span>
      <span class="nb" @click="findNext(true)"><Icon name="forward" :size="13" :sw="2" style="transform:rotate(90deg)" /></span>
      <span class="nb" @click="closeFind"><Icon name="x" :size="13" :sw="2" /></span>
    </div>

    <div v-if="!isElectron" class="notice">
      <div class="nt">The browser runs in the desktop app</div>
      <div class="ns">The embedded browser and visual picker need Electron. Launch with <span class="mono">python run.py</span>.</div>
    </div>
    <div v-else-if="!active" class="notice">
      <div class="nt">No open tabs</div>
      <button class="btn accent" @click="newTab()"><Icon name="plus" :size="14" :sw="2.2" />New tab</button>
    </div>

    <div v-else class="split">
      <div class="pagearea">
        <webview
          v-for="t in browser.tabs" :key="t.id" :ref="wvRef(t.id)"
          v-show="t.id === browser.activeId" :src="t.initialUrl" class="wv"
          @ipc-message="onIpc($event, t.id)"
          @did-navigate="onNav($event, t.id)"
          @did-navigate-in-page="onNav($event, t.id)"
          @page-title-updated="setTabTitle(t.id, $event.title || '')"
          @did-start-loading="setTabLoading(t.id, true)"
          @did-stop-loading="setTabLoading(t.id, false)"
          @dom-ready="onReady(t.id)"
          @found-in-page="onFound"
        />
        <div v-if="pickingField && !inspect" class="pickhint">
          Click the <b>{{ pickingField }}</b> on the page · Esc to cancel
        </div>
      </div>

      <!-- right dock: the inspector during a pick, otherwise THE draft -->
      <div v-show="browser.panelOpen" class="dock">
        <PickInspector
          v-if="inspect"
          :field="inspect.field" :chain="inspect.chain" :target="inspect.target" :probe="probe"
          :anchor="inspect.anchor" :saved="savedRule"
          @probe="onProbe" @use="onUse" @repick="repick" @cancel="closeInspect"
        />
        <CapturePanel
          v-show="!inspect" ref="panel"
          :has-page="!!active && !!domain" :busy="busy"
          :page-url="active?.url || ''" :page-title="active?.title || ''"
          @autofill="autofill" @capture="startPick($event)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.bw { display: flex; flex-direction: column; height: 100%; min-height: 0; }

.toolbar { display: flex; align-items: center; gap: 4px; padding: 6px 10px; flex: none; background: var(--bg2); border-bottom: 1px solid var(--line); }
.nb { width: 28px; height: 28px; border-radius: 7px; display: inline-flex; align-items: center; justify-content: center; color: var(--tx2); cursor: pointer; flex: none; }
.nb:hover { background: var(--hover); color: var(--tx); }
.nb.act { background: var(--accentSoft); color: var(--accent); }
.url { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 10px; margin: 0 4px; background: var(--panel); border: 1px solid var(--line); border-radius: 8px; }
.urlin { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: var(--tx); font-size: 11.5px; }
.zoom { display: flex; align-items: center; gap: 1px; }
.zlevel { font-size: 10px; color: var(--tx3); width: 38px; text-align: center; cursor: pointer; }
.zlevel:hover { color: var(--tx); }

.findbar { display: flex; align-items: center; gap: 7px; padding: 6px 12px; flex: none; background: var(--bg2); border-bottom: 1px solid var(--line); color: var(--tx3); }
.findin { width: 240px; border: 1px solid var(--line); border-radius: 7px; background: var(--panel); color: var(--tx); font: 400 12px/1 system-ui; padding: 6px 9px; outline: none; }
.findin:focus { border-color: var(--accent); }
.fcount { font-size: 10.5px; color: var(--tx3); min-width: 44px; text-align: center; }

.notice { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; }
.nt { font: 600 15px/1 system-ui; color: var(--tx); }
.ns { font: 400 12.5px/1.5 system-ui; color: var(--tx3); }

.split { flex: 1; min-height: 0; display: flex; }
.pagearea { flex: 1; min-width: 0; position: relative; display: flex; }
.wv { flex: 1; min-width: 0; border: none; }
.pickhint { position: absolute; top: 12px; left: 50%; transform: translateX(-50%); background: var(--accent); color: var(--accent-ink); font: 600 12px/1 system-ui; padding: 9px 14px; border-radius: 9px; box-shadow: 0 8px 24px rgba(0,0,0,.4); pointer-events: none; }
.dock { width: 344px; flex: none; display: flex; flex-direction: column; min-height: 0; border-left: 1px solid var(--line); background: var(--bg2); }
.dock :deep(.cp) { border-left: none; width: 100%; flex: 1; }
</style>
