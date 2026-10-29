// The app store: navigation, library data + linked faceted filters, and the
// user layer. User-layer changes (star, rating, read state) are instant
// write-through — they never go through a draft (design/state-model.md §11).
// The draft itself lives in draft.ts; browser tabs live in browser.ts.
import { reactive, watch, watchEffect } from 'vue'
import {
  api, ApiError, type DownloadItem, type DownloadsState, type LibraryQuery, type PassState,
} from './api'
import {
  chapterRowsOf, emptyFacets, metaOf, PAGE_FILTER_DEFAULTS, pageFilterOf, readerPrefsOf,
  sameChapter, type Chapter, type Facets, type FieldDef, type MetaScope, type PageFilter, type ReaderPrefs,
  type ReadState, type Source, type Title,
} from './data'
import {
  isBoolMap, isRecord, localKeys, readLocal, readLocalOne, removeLocal, writeLocal, writeLocalOne,
} from './local'
import { stopCaptureFor } from './pagecapture'

export type View = 'library' | 'title' | 'reader' | 'authors' | 'sources' | 'metadata' | 'import' | 'settings'
  | 'browser'
export type Density = 'grid' | 'dense' | 'expanded'
export type ThemePref = 'dark' | 'light' | 'system'

// A facet is keyed by FIELD ID (library/fields.py), and the set of them arrives
// from the backend — so a field added there, or defined by the user, filters
// here without a line of code.
export type FacetKey = string
export function facetFields(): FieldDef[] {
  return store.fields.filter((f) => f.facet)
}

// Which registry fields a SURFACE offers. Hiding one is a user setting kept per
// surface — you may not want Studio among your filters yet still edit it on a
// title. What a SOURCE offers the capture picker is a different scope entirely
// and does not live here (design/state-model.md §4).
// 'title' | 'filters' | one per shelf: `axes:<shelf>`. The axes are configured
// PER SHELF — what is worth browsing by in manga is not what is worth browsing
// by in an anime shelf, and the shelf is what you are looking at when you
// decide. One global list made every shelf's setting overwrite the others.
export type FieldSurface = string
export function axisSurface(shelf = store.library.shelf): FieldSurface {
  return `axes:${shelf}`
}
const HIDDEN_KEY = 'lb.hiddenFields'
function isHiddenMap(v: unknown): v is Record<FieldSurface, Record<string, boolean>> {
  return !!v && typeof v === 'object' && !Array.isArray(v)
    && Object.values(v as Record<string, unknown>).every(isBoolMap)
}
const hiddenFields = reactive<Record<FieldSurface, Record<string, boolean>>>(
  readLocal(HIDDEN_KEY, isHiddenMap, { title: {}, filters: {}, axes: {} }))
watch(hiddenFields, () => writeLocal(HIDDEN_KEY, { ...hiddenFields }), { deep: true })

// A required field is never hidden: the record cannot be saved without it.
function hiddenIn(surface: FieldSurface): Record<string, boolean> {
  // a map written before this surface existed has no key for it
  if (!hiddenFields[surface]) hiddenFields[surface] = {}
  return hiddenFields[surface]
}
export function visibleFields(surface: FieldSurface, base: FieldDef[]): FieldDef[] {
  return base.filter((f) => f.required || !hiddenIn(surface)[f.id])
}
export function hiddenOf(surface: FieldSurface, base: FieldDef[]): FieldDef[] {
  return base.filter((f) => !f.required && hiddenIn(surface)[f.id])
}
export function setFieldHidden(surface: FieldSurface, id: string, hidden: boolean) {
  if (hidden) hiddenIn(surface)[id] = true
  else delete hiddenIn(surface)[id]
  // Hiding a FILTER field means "I do not filter by this" — and an active
  // selection left behind on a row nobody can see is exactly the invisible
  // filtering that makes a library look like it lost titles.
  if (surface === 'filters' && hidden) {
    const lib = store.library
    if (selected(lib.include, id as FacetKey).length || selected(lib.exclude, id as FacetKey).length) {
      delete lib.include[id as FacetKey]
      delete lib.exclude[id as FacetKey]
      onSelectionChange(reloadLibrary)
    }
  }
}

interface LibraryFilters {
  density: Density
  search: string
  sort: string // updated | title | rating | unread
  favOnly: boolean
  progress: 'all' | 'unread' | 'reading' | 'completed' // READING progress, not manga status
  minRating: number // 0 = any
  // which TYPE shelf is open ('' = all). Navigation, not a filter: the filter
  // block never shows it and Clear all never touches it.
  shelf: string
  include: Record<FacetKey, string[]>
  exclude: Record<FacetKey, string[]>
}

function blankSelection(): Record<FacetKey, string[]> {
  return {}
}
function selected(bag: Record<FacetKey, string[]>, key: FacetKey): string[] {
  return bag[key] ?? (bag[key] = [])
}

interface State {
  theme: ThemePref
  view: View
  openTabs: string[]
  pinnedTabs: string[] // pinned title-tab ids — icon-only, stuck to the front
  activeTitle: string | null
  browseHomepage: string
  // data from the backend
  titles: Title[] // current (filtered) library results
  total: number
  sources: Source[]
  sourceGroups: string[] // the user's ordered group names
  sourcesRead: boolean   // both of those came from the ACTIVE library (not a failed read)
  // bumps when the active library changes: what a view caches per library
  // outside this store (recipes, say) watches it and lets go
  libraryGen: number
  // the titles the Metadata view works on when the library's select mode handed
  // them over; null = what the library's filters show
  metaSelection: string[] | null
  browseAxis: string     // which list field the browse view groups by
  // the value page the browse view shows instead of its list, and the pages
  // that led to it — Back walks them before it returns to the list
  valuePage: ValueRef | null
  valueTrail: ValueRef[]
  facets: Facets // linked counts for the CURRENT selection
  globalFacets: Facets // unfiltered counts — the STABLE row order for the sidebar
  // Counts under the SHELF alone: what an axis would actually find if you
  // opened it from here. Deliberately NOT the live selection — an axis list
  // that appears and disappears while you type in the search box is not a list.
  shelfFacets: Facets
  fields: FieldDef[] // the metadata field registry, served by the backend
  // suggestion vocabulary per field id — everything the library already holds
  vocab: Record<string, string[]>
  byId: Record<string, Title> // cache for open tabs / detail view
  loading: boolean
  error: string | null
  library: LibraryFilters
  // pagination memory, preserved across tab/view switches
  ui: { libPage: number; auPage: number }
  // app identity from <repo>/app-meta.json (via /api/settings)
  appMeta: { name?: string; version?: string; updated?: string; description?: string }
  // the reader (view 'reader') — the title is activeTitle, this is the position
  reader: { chapterId: string | null; page: number }
  // tabs currently IN reader mode: switching back to such a tab re-enters the
  // reader at the remembered spot instead of dropping to the title page
  readerTabs: Record<string, { chapterId: string; page: number }>
}

export const store = reactive<State>({
  theme: 'dark',
  view: 'library',
  openTabs: [],
  pinnedTabs: [],
  activeTitle: null,
  browseHomepage: 'https://www.google.com',
  titles: [],
  total: 0,
  sources: [],
  sourceGroups: [],
  sourcesRead: false,
  libraryGen: 0,
  metaSelection: null,
  browseAxis: 'authors',
  valuePage: null,
  valueTrail: [],
  facets: emptyFacets(),
  globalFacets: emptyFacets(),
  shelfFacets: emptyFacets(),
  fields: [],
  vocab: {},
  byId: {},
  loading: false,
  error: null,
  library: {
    density: 'grid', search: '', sort: 'updated', favOnly: false, progress: 'all',
    // the shelf is which TYPE you are looking at — navigation, kept apart from
    // the filter bags so the filter UI never has to pretend it owns it
    minRating: 0, shelf: '', include: blankSelection(), exclude: blankSelection(),
  },
  ui: { libPage: 1, auPage: 1 },
  appMeta: {},
  reader: { chapterId: null, page: 0 },
  readerTabs: {},
})

// ---- theme ----
function resolveTheme(pref: ThemePref): 'dark' | 'light' {
  if (pref === 'system') {
    const light = typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: light)').matches
    return light ? 'light' : 'dark'
  }
  return pref
}
// restore persisted UI prefs before wiring the auto-save
store.theme = readLocalOne('lb.theme', ['dark', 'light', 'system'] as const, store.theme)
store.library.density = readLocalOne('lb.density', ['grid', 'dense', 'expanded'] as const, store.library.density)
// an axis is any list field, custom ones included, so it is not a fixed set
store.browseAxis = readLocal('lb.browseAxis', (v): v is string => typeof v === 'string', 'authors')
// the shell's native window-controls overlay follows the theme
const TITLEBAR = {
  dark: { color: '#0f1115', symbolColor: '#9aa1ad' },
  light: { color: '#e6e3dd', symbolColor: '#575d67' },
} as const
watchEffect(() => {
  const t = resolveTheme(store.theme)
  document.documentElement.setAttribute('data-theme', t)
  window.longbox?.setTitleBar?.(TITLEBAR[t])
})
// frameless chrome (drag regions, overlay clearance) only inside the shell
document.documentElement.classList.toggle('frameless', !!window.longbox)
watchEffect(() => {
  writeLocalOne('lb.theme', store.theme)
  writeLocalOne('lb.density', store.library.density)
  writeLocal('lb.browseAxis', store.browseAxis)
})

// ---- what page capture accepts as a page ----
//
// The ACTIVE library's: it lives in the vault beside the recipes, so it loads
// with the library and switches with it. A change shows at once and is written
// a moment after the last one — a dragged slider is one write, not forty.
//
// Until the library has answered, a change has nothing true to build on and is
// refused: written over a read that failed, it would replace what is stored. A
// switch waits for a write already on its way, and an answer from the library
// it left is dropped (libraryGen).
export const pageFilter = reactive<PageFilter>({ ...PAGE_FILTER_DEFAULTS })
let pageFilterKnown = false
const LEGACY_PAGE_FILTER = 'lb.pageFilter' // where an older build kept it, on this machine

let pageFilterTimer: number | undefined
let pageFilterWrite: Promise<void> = Promise.resolve()
function writePageFilter(): Promise<void> {
  window.clearTimeout(pageFilterTimer)
  pageFilterTimer = undefined
  pageFilterWrite = api.savePageFilter({ ...pageFilter }).then(() => undefined, (e) => {
    store.error = e instanceof Error ? e.message : String(e)
  })
  return pageFilterWrite
}
export function savePageFilter(patch: Partial<PageFilter>) {
  if (!pageFilterKnown) {
    store.error = 'The page filter could not be read from this library, so nothing was changed.'
    return
  }
  Object.assign(pageFilter, pageFilterOf({ ...pageFilter, ...patch }))
  window.clearTimeout(pageFilterTimer)
  pageFilterTimer = window.setTimeout(() => void writePageFilter(), 400)
}
export function resetPageFilter() { savePageFilter(PAGE_FILTER_DEFAULTS) }
// what is waiting, or already on its way, belongs to the library being left
async function settlePageFilter() {
  if (pageFilterTimer !== undefined) await writePageFilter()
  else await pageFilterWrite
}

async function loadPageFilter() {
  const gen = store.libraryGen
  pageFilterKnown = false
  Object.assign(pageFilter, PAGE_FILTER_DEFAULTS)
  let stored: PageFilter
  try {
    stored = await api.pageFilter()
  } catch {
    return // an older build's value waits for a library that answers
  }
  if (gen !== store.libraryGen) return
  Object.assign(pageFilter, stored)
  pageFilterKnown = true
  const legacy = readLocal(LEGACY_PAGE_FILTER, isRecord, null as Record<string, unknown> | null)
  if (!legacy) return
  // moved ONCE, into the library open now — unless it already says something of its own
  const untouched = stored.minPx === PAGE_FILTER_DEFAULTS.minPx
    && stored.widthRatio === PAGE_FILTER_DEFAULTS.widthRatio
  if (untouched) {
    const write = api.savePageFilter(pageFilterOf(legacy))
    pageFilterWrite = write.then(() => undefined, () => undefined) // a switch waits for it
    try {
      const saved = await write
      if (gen === store.libraryGen) Object.assign(pageFilter, saved)
    } catch {
      return // kept for the next start
    }
  }
  removeLocal(LEGACY_PAGE_FILTER)
}

// ---- data loading ----
export function cache(list: Title[]) {
  for (const t of list) {
    const existing = store.byId[t.id]
    if (existing) Object.assign(existing, t) // keep object identity for open views
    else store.byId[t.id] = t
  }
}

// `{genres: ['action']}` → `['genres:action']` — the one filter shape.
function pairs(bag: Record<FacetKey, string[]>): string[] {
  return Object.entries(bag).flatMap(([id, values]) => values.map((v) => `${id}:${v}`))
}

// Every keystroke in a search box is a selection change, and each one costs a
// round trip (the browse view pays a full re-aggregation). Coalesce the burst:
// a click still feels instant at this delay, typing stops thrashing the sidecar.
function onSelectionChange(fn: () => void, ms = 140) {
  let t: ReturnType<typeof setTimeout> | undefined
  return watch(() => JSON.stringify(buildQuery()), () => {
    if (t) clearTimeout(t)
    t = setTimeout(fn, ms)
  })
}

export function buildQuery(): LibraryQuery {
  const f = store.library
  return {
    search: f.search || undefined,
    progress: f.progress === 'all' ? undefined : f.progress,
    fav: f.favOnly || undefined,
    min_rating: f.minRating || undefined,
    sort: f.sort,
    // the shelf rides in as an ordinary field filter — the backend needs no
    // second concept for it
    f: [...pairs(f.include), ...(f.shelf ? [`type:${f.shelf}`] : [])],
    nf: pairs(f.exclude),
  }
}

// The Metadata view's scope: the selection handed over from the library, or
// exactly what the library shows under its filters right now.
export function metaScope(): MetaScope {
  if (store.metaSelection) return { ids: store.metaSelection }
  const { sort: _sort, ...filters } = buildQuery()
  return filters
}
export function editInMetadata(ids: string[]) {
  store.metaSelection = [...ids]
  goView('metadata')
}

// ---- value pages (design/value-pages.md) ----
// A page lives inside the browse view, on the axis of the field it was opened
// by, so the rail says where you are and its Back returns to that list.
export interface ValueRef { field: string; value: string }
export function openValuePage(field: string, value: string) {
  const cur = store.valuePage
  if (cur && store.view === 'authors') {
    if (cur.field === field && cur.value === value) return
    store.valueTrail.push(cur)
  } else {
    store.valueTrail = []
  }
  store.valuePage = { field, value }
  store.browseAxis = field
  store.view = 'authors'
}
// One-shot handoff to the Metadata view: the field, with the value searched
// and selected — what a page's "In Metadata" means (rename, merge).
let pendingMetaFocus: ValueRef | null = null
export function editValueInMetadata(field: string, value: string) {
  pendingMetaFocus = { field, value }
  store.metaSelection = null
  goView('metadata')
}
export function takeMetaFocus(): ValueRef | null {
  const v = pendingMetaFocus
  pendingMetaFocus = null
  return v
}
export function valuePageBack() {
  const prev = store.valueTrail.pop() ?? null
  store.valuePage = prev
  if (prev) store.browseAxis = prev.field // the rail says where Back went
}
export function closeValuePage() {
  store.valuePage = null
  store.valueTrail = []
}

export async function reloadLibrary() {
  store.loading = true
  store.error = null
  const q = buildQuery()
  try {
    // results + linked facet counts move together
    const [list, facets] = await Promise.all([api.library(q), api.facets(q)])
    store.titles = list.map((t) => store.byId[t.id] ?? t)
    cache(list)
    store.facets = facets
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  } finally {
    store.loading = false
  }
}

// The full (unfiltered) vocabulary: Combo suggestions in the editor, and the
// sidebar's stable row order (rows never appear/disappear/reshuffle mid-filter).
function applyVocab(full: Facets) {
  store.globalFacets = full
  store.vocab = Object.fromEntries(
    Object.entries(full).map(([id, rows]) => [id, rows.map((x) => x.v)]))
}
async function refreshVocab() {
  try { applyVocab(await api.facets()) } catch { /* keep the previous vocab */ }
}

export async function refreshShelfFacets() {
  const shelf = store.library.shelf
  try {
    store.shelfFacets = await api.facets(shelf ? { f: [`type:${shelf}`] } : {})
  } catch { /* keep the previous counts */ }
}
watch(() => store.library.shelf, () => void refreshShelfFacets())

// THE axis list, for the rail and for the menu that configures it — one answer,
// so the two can never disagree. An axis with nothing to group is not offered
// whatever the setting says: opening it could only ever show an empty page.
function liveAxes(): FieldDef[] {
  const lists = store.fields.filter((f) => f.type === 'list')
  if (!Object.keys(store.shelfFacets).length) return lists // not counted yet
  return lists.filter((f) => (store.shelfFacets[f.id] ?? []).some((v) => v.n > 0))
}
export function shownAxes(): FieldDef[] { return visibleFields(axisSurface(), liveAxes()) }
export function hiddenAxes(): FieldDef[] { return hiddenOf(axisSurface(), liveAxes()) }

// Sources and the vocab are derived from the titles — refetch after any commit
// or delete, or the source list and the suggestions go stale. Browse groups are
// NOT here: that view loads the axis it is actually showing.
export async function refreshDerived() {
  const gen = store.libraryGen
  try {
    const [sources, groups] = await Promise.all([api.sources(), api.sourceGroups()])
    if (gen === store.libraryGen) {
      store.sources = sources
      store.sourceGroups = groups
      store.sourcesRead = true
    }
  } catch { /* leave the previous values in place */ }
  await Promise.all([refreshVocab(), refreshShelfFacets()])
}

// A write that sends a WHOLE list — a site's bookmarks, the group order —
// replaces what is stored, so it may only be built on what was actually read.
export function sourcesKnown(): boolean {
  if (!store.sourcesRead) {
    store.error = 'The sources could not be read from this library, so nothing was changed.'
  }
  return store.sourcesRead
}

// One refresh for "the library changed": results, counts and derived collections.
export async function refreshLibrary() {
  await Promise.all([reloadLibrary(), refreshDerived(), refreshTotal()])
}

export async function init() {
  // Startup waits for the LIBRARY and nothing else: the grid is what the window
  // opens on, and a big vault makes every extra call visible as an empty screen.
  store.loading = true
  try {
    // the sync status comes WITH the first paint: an index still being filled
    // must read as "reading the library", never as "your library is empty"
    // the field registry rides along: it is a dozen rows, and nothing that
    // shows metadata can draw a thing without it
    const [all, facets, settings, sync, fields] = await Promise.all(
      [api.library(), api.facets(), api.settings(), api.libraryStatus(), api.fields()])
    store.fields = fields
    Object.assign(opening, { active: sync.running, path: sync.path, done: sync.done, total: sync.total })
    cache(all)
    store.total = all.length
    store.titles = all.map((t) => store.byId[t.id])
    // the unfiltered facets ARE the full vocabulary — asking twice is one
    // whole-library scan too many
    store.facets = facets
    applyVocab(facets)
    // no shelf is open at startup, so the unfiltered counts ARE the shelf's
    store.shelfFacets = facets
    if (settings.homepage) store.browseHomepage = settings.homepage
    store.appMeta = settings.app || {}
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  } finally {
    store.loading = false
  }
  // the Authors tab and the source list are not on the way to the grid
  void refreshDerived()
  void loadPageFilter()
  // …and the vault check runs behind the window, refreshing it if disk moved on
  watchLibrarySync()
  // what a previous run left unfinished is already on the sidecar's list
  void pollDownloads(true)
  window.setInterval(() => void pollDownloads(), 1000)
  // a pass survives leaving the page, so following it has to as well
  void pollVaultPass(true)
  window.setInterval(() => void pollVaultPass(), 600)
  // refetch results whenever a server-side filter changes (density is client-only)
  onSelectionChange(reloadLibrary)
}

// Clears the FILTERS. The shelf survives: you asked to see this type, and
// clearing a genre is not a request to leave it.
export function resetFilters() {
  Object.assign(store.library, {
    search: '', favOnly: false, progress: 'all', minRating: 0,
    include: blankSelection(), exclude: blankSelection(),
  })
}

// ---- faceted selection: off → include → exclude → off ----
export function facetState(key: FacetKey, value: string): 'in' | 'out' | '' {
  if (selected(store.library.include, key).includes(value)) return 'in'
  if (selected(store.library.exclude, key).includes(value)) return 'out'
  return ''
}
export function toggleFacet(key: FacetKey, value: string) {
  const inc = selected(store.library.include, key)
  const exc = selected(store.library.exclude, key)
  const i = inc.indexOf(value)
  const e = exc.indexOf(value)
  if (i >= 0) { inc.splice(i, 1); exc.push(value) }
  else if (e >= 0) { exc.splice(e, 1) }
  else inc.push(value)
}
export function anyFilterActive(): boolean {
  const f = store.library
  return !!f.search || f.favOnly || f.progress !== 'all' || f.minRating !== 0
    || [...Object.values(f.include), ...Object.values(f.exclude)].some((v) => v.length)
}

// ---- navigation ----
export function goView(v: Exclude<View, 'title' | 'browser'>) {
  store.view = v
  // opening a tab always refetches what it shows — the vault can change
  // underneath (library-path switch, external edits); stale data stays on
  // screen only until the background GET lands
  if (v === 'authors' || v === 'sources') void refreshDerived()
  else if (v === 'library') void reloadLibrary()
}
export async function openTitle(id: string) {
  if (!store.openTabs.includes(id)) store.openTabs.push(id)
  store.activeTitle = id
  // a tab left in reader mode COMES BACK in reader mode, at the same spot
  const r = store.readerTabs[id]
  if (r) {
    store.reader.chapterId = r.chapterId
    store.reader.page = r.page
    store.view = 'reader'
  } else {
    store.view = 'title'
  }
  if (!store.byId[id]) {
    try { cache([await api.title(id)]) } catch (e) { store.error = String(e) }
  }
}
// Middle-click: open the tab WITHOUT leaving the current view.
export async function openTitleBackground(id: string) {
  if (!store.openTabs.includes(id)) store.openTabs.push(id)
  if (!store.byId[id]) {
    try { cache([await api.title(id)]) } catch (e) { store.error = String(e) }
  }
}
// Drop every per-tab trace of a title (open tab, pin, reader memory).
// Returns the tab's former index so closeTab can pick a successor.
function forgetTab(id: string): number {
  const i = store.openTabs.indexOf(id)
  if (i >= 0) store.openTabs.splice(i, 1)
  const p = store.pinnedTabs.indexOf(id)
  if (p >= 0) store.pinnedTabs.splice(p, 1)
  delete store.readerTabs[id]
  return i
}
// A title that no longer exists cannot be captured into — the session ends with
// it instead of posting pages at a dead id every couple of seconds.
function forgetTitle(id: string) {
  delete store.byId[id]
  stopCaptureFor({ titleId: id })
}
export function closeTab(id: string) {
  const i = forgetTab(id)
  if (store.activeTitle === id) {
    const next = store.openTabs[i] ?? store.openTabs[i - 1] ?? null
    store.activeTitle = next
    if (next) void openTitle(next)
    else store.view = 'library'
  }
}

// ---- the reader: SAME tab as the title, just a view swap (mockup decision) ----
export async function openReader(titleId: string, chapterId: string, page = 0) {
  if (!store.openTabs.includes(titleId)) store.openTabs.push(titleId)
  store.activeTitle = titleId
  store.reader.chapterId = chapterId
  store.reader.page = page
  store.readerTabs[titleId] = { chapterId, page }
  store.view = 'reader'
  if (!store.byId[titleId]) {
    try { cache([await api.title(titleId)]) } catch (e) { store.error = String(e) }
  }
}
export function closeReader() {
  // explicit exit — this tab is a title page again
  if (store.activeTitle) delete store.readerTabs[store.activeTitle]
  store.view = 'title'
}

export function toggleTitlePin(id: string) {
  const i = store.pinnedTabs.indexOf(id)
  if (i >= 0) store.pinnedTabs.splice(i, 1)
  else store.pinnedTabs.push(id)
}

// Reorder (drag & drop in the all-tabs menu): put `dragId` before `targetId`.
export function moveTitleTabBefore(dragId: string, targetId: string) {
  if (dragId === targetId) return
  const arr = store.openTabs
  const from = arr.indexOf(dragId)
  if (from < 0) return
  arr.splice(from, 1)
  const to = arr.indexOf(targetId)
  if (to < 0) arr.push(dragId)
  else arr.splice(to, 0, dragId)
}

// Combo suggestions for entry LANG / GROUP fields — the chapters at hand
// first, then the library-wide vocabulary (shared by the title page's entry
// forms and the browser dock's ADD ENTRY).
// What an entry form offers for LANGUAGE and GROUP, in two rings: what is NEAR
// (this title's own entries, then everything captured from the same source) and
// what exists anywhere. The near ring is the list you see; the far one joins the
// search the moment you type — a group you used once on another site is worth
// finding, but not worth being offered by default.
function ringsOf(chapters: { lang: string; group: string }[], domain: string,
                 pick: (c: { lang: string; group: string }) => string): { near: string[]; all: string[] } {
  const near = new Set(chapters.map(pick).filter(Boolean))
  const all = new Set(near)
  const host = domain.toLowerCase()
  for (const t of Object.values(store.byId)) {
    const sameSource = host && (t.source.domain || '').toLowerCase() === host
    for (const c of t.chapters) {
      const v = pick(c)
      if (!v) continue
      if (sameSource) near.add(v)
      all.add(v)
    }
  }
  return { near: [...near], all: [...all] }
}

export function langRings(chapters: { lang: string; group: string }[] = [], domain = '') {
  const rings = ringsOf(chapters, domain, (c) => c.lang)
  // the vocabulary the library already knows is near enough for a language
  for (const v of store.vocab.language ?? []) {
    if (!rings.near.includes(v)) rings.near.push(v)
    if (!rings.all.includes(v)) rings.all.push(v)
  }
  return rings
}
export function groupRings(chapters: { lang: string; group: string }[] = [], domain = '') {
  return ringsOf(chapters, domain, (c) => c.group)
}

// Jump to the library filtered by ONE value of ONE field. Everything that
// links out of a chip, a card or a browse group lands here.
// How many things the current selection has switched on — what the Filters
// button counts, in the library and in the browse view alike.
export function activeFilterCount(): number {
  const f = store.library
  const n = (bag: Record<string, string[]>) => Object.values(bag).reduce((t, v) => t + v.length, 0)
  return n(f.include) + n(f.exclude)
    + (f.favOnly ? 1 : 0) + (f.progress !== 'all' ? 1 : 0) + (f.minRating ? 1 : 0)
}

export function filterByField(field: FacetKey, value: string) {
  resetFilters()
  store.library.shelf = '' // a jump from a chip means THIS value, not this value on this shelf
  store.library.include[field] = [value]
  store.view = 'library'
}
// The singular vocabulary the title page and the cards speak, over that one.
export function filterBy(kind: 'genre' | 'tag' | 'type' | 'status' | 'character', value: string) {
  filterByField(kind === 'genre' ? 'genres' : kind === 'tag' ? 'tags'
    : kind === 'character' ? 'characters' : kind, value)
}
// Jump to the library filtered to ONE person via the dedicated AUTHOR facet.
export function filterByPerson(name: string) {
  filterByField('authors', name)
}

// One-shot handoff: jump to the Authors tab focused on one person — the view
// picks the name up on mount and puts it into its own search box.
let pendingAuthorFocus = ''
export function goAuthorsFor(name: string) {
  pendingAuthorFocus = name
  closeValuePage()
  store.browseAxis = 'authors' // the browse view may have been left on another axis
  goView('authors')
}
export function takeAuthorFocus(): string {
  const v = pendingAuthorFocus
  pendingAuthorFocus = ''
  return v
}

// ---- lookups ----
export function titleById(id: string | null): Title | undefined {
  return id ? store.byId[id] : undefined
}

// ---- downloads: ONE poller for the whole app -------------------------------
//
// The capture dock used to own this, which meant the rest of the app could not
// know a transfer was running — and the window could not warn about one on its
// way out. It is app state: the dock, the sidebar count and the panel all read
// the same rows.
export const downloads = reactive<DownloadsState>({ armed: null, items: [] })
const seenDone = new Set<string>()
let watchers = 0

/** Something on screen is showing downloads — keep the poll warm while it is. */
export function watchDownloads(on: boolean) {
  watchers = Math.max(0, watchers + (on ? 1 : -1))
}
export function runningDownloads(): DownloadItem[] {
  return downloads.items.filter((i) => i.state === 'downloading')
}
export function unfinishedDownloads(): DownloadItem[] {
  return downloads.items.filter((i) => i.state === 'downloading' || i.state === 'interrupted')
}

export async function pollDownloads(force = false): Promise<void> {
  if (!force && !watchers && !downloads.armed && !runningDownloads().length) return
  try {
    const s = await api.downloadsState()
    for (const it of s.items) {
      if (it.state === 'done' && !seenDone.has(it.id)) {
        seenDone.add(it.id)
        void refreshTitle(it.titleId) // its chapter now has media
      } else if (it.state === 'failed' || it.state === 'interrupted') {
        seenDone.add(it.id)
      }
    }
    downloads.armed = s.armed
    downloads.items = s.items
  } catch { /* the sidecar is busy or gone — keep what we have */ }
}

// ---- the vault pass: ONE poller, and no client-side copy of the truth -------
//
// A check runs on the SERVER. It used to be tracked inside the settings view,
// which meant walking to the library and back lost the progress of something
// still running — and a page reload lost it even while the pass carried on.
// So nothing here decides what is running: the sidecar says, including WHICH
// row started it, and every screen reads the same answer.
export const vaultPass = reactive<PassState & { key: string }>(
  { running: false, op: '', key: '', done: 0, total: 0, detail: '', doneBytes: 0, totalBytes: 0 })
let passWatchers = 0

/** Something on screen is showing the pass — keep the poll warm while it is. */
export function watchVaultPass(on: boolean) {
  passWatchers = Math.max(0, passWatchers + (on ? 1 : -1))
}

export async function pollVaultPass(force = false): Promise<void> {
  if (!force && !passWatchers && !vaultPass.running) return
  try {
    Object.assign(vaultPass, await api.passStatus())
  } catch { /* the sidecar is busy or gone — keep what we have */ }
}

/** Start a pass and follow it. The caller says what to call; the server says
 *  what is running, so this returns as soon as the request does — and the poll
 *  keeps going for anyone still watching. */
export async function runVaultPass(fn: () => Promise<unknown>): Promise<void> {
  try {
    await pollVaultPass(true)
    await fn()
  } catch (e) {
    store.error = String(e)
  } finally {
    await pollVaultPass(true)
  }
}

export async function stopVaultPass(): Promise<void> {
  try { Object.assign(vaultPass, await api.stopPass()) } catch { /* already done */ }
}

// What a pass is and where it was started — one answer for the corner and the
// activity panel, by the key the row that started it gave the server.
export function passIcon(key: string): string {
  if (key === 'import') return 'import'
  if (['metadata', 'mirror', 'retype', 'clear'].includes(key)) return 'meta'
  return key === 'check' ? 'check' : 'refresh'
}
export function passHome(key: string): 'import' | 'metadata' | 'settings' {
  return key === 'import' || key === 'metadata' ? key : 'settings'
}
/** How far the running pass is, 0–100 — by bytes where it counts them. */
export function passPercent(): number {
  const [done, total] = vaultPass.totalBytes
    ? [vaultPass.doneBytes, vaultPass.totalBytes] : [vaultPass.done, vaultPass.total]
  return total ? Math.min(100, Math.floor((done / total) * 100)) : 0
}

export async function refreshTitle(id: string): Promise<void> {
  try { cache([await api.title(id)]) } catch { /* keep the cached copy */ }
}

// ---- user-layer write-through (optimistic; instant, no draft, no confirm) ----
//
// Optimistic means the value changes HERE first, so a click never waits on a
// disk. It also means the value has to change BACK when the write is refused:
// a star left lit over a patch that never landed is a lie the screen keeps
// telling until something else reloads the title. One helper, so every user
// field rolls back the same way.
async function writeThrough(apply: (v: never) => void, next: unknown, prev: unknown,
                            send: () => Promise<Title>) {
  const set = apply as (v: unknown) => void
  set(next)
  try {
    cache([await send()])
  } catch (e) {
    set(prev)
    store.error = e instanceof Error ? e.message : String(e)
  }
}

export async function setFavorite(t: Title, value: boolean) {
  await writeThrough((v) => { t.fav = v }, value, t.fav,
                     () => api.patchUser(t.id, { fav: value }))
}
export async function toggleFav(t: Title) { await setFavorite(t, !t.fav) }
export async function setRating(t: Title, value: number) {
  // clicking the current rating again clears it — every star widget toggles
  const v = value === t.rating ? 0 : value
  await writeThrough((n) => { t.rating = n }, v, t.rating,
                     () => api.patchUser(t.id, { rating: v }))
}
// Where a page chapter remembers WHICH page, an episode remembers WHERE.
//
// Deliberately NOT a full write-through round trip: a position write rewrites
// the title document and refreshes its index row, which on a vault that lives
// on a network drive competes with the stream the player is pulling from that
// same drive. The player is the authority while it runs, so the value is
// applied locally and sent without merging the response back — merging one
// would also replace the chapter list mid-playback and re-render the reader.
export async function setPlaybackPosition(t: Title, chapterId: string, seconds: number) {
  const chapter = t.chapters.find((c) => c.id === chapterId)
  if (!chapter) return
  const before = chapter.position
  chapter.position = seconds
  try {
    await api.patchUser(t.id, { position: { [chapterId]: seconds } })
  } catch (e) {
    chapter.position = before // the player keeps playing; the vault did not move
    store.error = e instanceof Error ? e.message : String(e)
  }
}

// How the reader shows a title. Like a playback position it is applied here and
// sent without merging the response back: a merge would replace the chapter
// list under the open reader.
export async function setReaderPrefs(t: Title, prefs: ReaderPrefs) {
  const before = t.reader
  t.reader = prefs
  try {
    await api.patchUser(t.id, { reader: prefs })
  } catch (e) {
    t.reader = before
    store.error = e instanceof Error ? e.message : String(e)
  }
}

// Before reader settings lived in the title, this machine kept them under the
// title's id. They move ONCE, into the library open at startup, and only after
// that library has verified itself against disk — then a title the library
// does not know (404) is not in it, and its key is dropped: an id from another
// library may name a different title here. A key whose title could not be
// asked about, or whose write failed, stays for the next start. A library
// switch stops the move before its next request and waits for the one in flight.
const LEGACY_READER = 'lb.reader.'
let legacyReader: { stop: boolean; run: Promise<void> } | null = null

function moveLegacyReaderPrefs() {
  if (legacyReader || store.libraryGen !== 0) return
  const state = { stop: false, run: Promise.resolve() }
  state.run = (async () => {
    for (const key of localKeys(LEGACY_READER)) {
      if (state.stop) return
      if (key === 'lb.reader.default') continue // this machine's default, still used
      const raw = readLocal(key, isRecord, null as Record<string, unknown> | null)
      let t: Title
      try {
        t = await api.title(key.slice(LEGACY_READER.length))
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) removeLocal(key)
        continue
      }
      if (state.stop) return
      if (raw && !Object.keys(t.reader ?? {}).length) {
        const prefs = readerPrefsOf(raw)
        try {
          await api.patchUser(t.id, { reader: prefs })
        } catch {
          continue
        }
        const mine = store.byId[t.id]
        if (mine) mine.reader = prefs
      }
      removeLocal(key)
    }
  })()
  legacyReader = state
}

export async function setRead(t: Title, chapterId: string, state: ReadState) {
  const c = t.chapters.find((x) => x.id === chapterId)
  if (!c) return
  await writeThrough((v) => { c.read = v }, state, c.read,
                     () => api.patchUser(t.id, { read: { [chapterId]: state } }))
}

// A commit body built from a title's CURRENT state — for targeted meta-layer
// edits (source unlink, chapter reorder) outside the draft flow.
function commitBodyFrom(t: Title) {
  return {
    meta: metaOf(t, store.fields),
    provenance: JSON.parse(JSON.stringify(t.provenance || {})),
    chapters: chapterRowsOf(t.chapters),
  }
}

// Explicitly remove a title's source link (the meta layer only).
export async function clearTitleSource(t: Title): Promise<void> {
  try {
    const body = commitBodyFrom(t)
    body.meta.source = { domain: '', url: '' }
    cache([await api.commitTitle(t.id, body)])
    await refreshLibrary()
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}

// Persist a chapter order: 'manual' = exactly as arranged by the user,
// 'auto' = back to smart number ordering (new chapters insert by number).
export async function commitChapterOrder(t: Title, orderedIds: string[], mode: 'manual' | 'auto'): Promise<void> {
  try {
    const by = new Map(t.chapters.map((c) => [c.id, c]))
    const rows = orderedIds.map((id) => by.get(id)).filter((c): c is Title['chapters'][number] => !!c)
    for (const c of t.chapters) if (!orderedIds.includes(c.id)) rows.push(c) // never drop a row
    const body = commitBodyFrom(t)
    body.meta.chapterOrder = mode
    body.chapters = chapterRowsOf(rows)
    cache([await api.commitTitle(t.id, body)])
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}

export async function deleteTitle(id: string): Promise<void> {
  try {
    await api.removeTitle(id)
    forgetTitle(id)
    closeTab(id)
    await refreshLibrary()
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}

// Bulk delete (the library's select mode) — one refresh at the end.
export async function deleteTitles(ids: string[]): Promise<number> {
  let done = 0
  for (const id of ids) {
    try {
      await api.removeTitle(id)
      forgetTitle(id)
      forgetTab(id)
      if (store.activeTitle === id) store.activeTitle = null
      done++
    } catch (e) {
      store.error = e instanceof Error ? e.message : String(e)
    }
  }
  await refreshLibrary()
  return done
}

// Edit ONE entry's identity fields in place. The row id NEVER changes here, so
// the archive, read state and download provenance stay bound (no-orphan rule).
export async function editChapterRow(t: Title, id: string,
  fields: { num: string; lang: string; group: string; url: string; section: string }): Promise<boolean> {
  try {
    const body = commitBodyFrom(t)
    const row = body.chapters.find((c) => c.id === id)
    if (!row) return false
    row.num = fields.num
    row.title = '' // single-label model: the label carries the whole name
    row.lang = fields.lang
    row.group = fields.group
    row.url = fields.url
    row.section = fields.section
    cache([await api.commitTitle(t.id, body)])
    return true
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
    return false
  }
}

// Create a bare chapter row (no file) — the manual twin of capturing one.
// Returns the row the VAULT ended up with: the id is assigned server-side (a
// commit can adopt an existing row's id), so anything addressing the new row's
// media has to read it back rather than assume what it will be.
export async function addChapterRow(
  t: Title, ch: { num: string; lang: string; group: string; url?: string; section?: string }): Promise<Chapter | null> {
  if (t.chapters.some((c) => sameChapter(c, ch))) {
    store.error = `Chapter ${ch.num}${ch.lang ? ' · ' + ch.lang : ''} already exists`
    return null
  }
  try {
    const body = commitBodyFrom(t)
    body.chapters.push({ id: '', num: ch.num, title: '', url: ch.url || '', lang: ch.lang, group: ch.group,
                         date: '', section: ch.section || '' })
    const saved = await api.commitTitle(t.id, body)
    cache([saved])
    const added = saved.chapters.find((c) => sameChapter(c, ch))
    // The commit came back without the row it was asked for. That is a bug
    // wherever it happens, and it used to happen in silence — a button that
    // looked broken. Say so rather than return nothing.
    if (!added) store.error = `Chapter ${ch.num} was not added — the library refused the row`
    return added ?? null
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
    return null
  }
}

// The library is served from the index and verified against disk afterwards, so
// this is what "still reading the folder" looks like to the user. A first-ever
// open fills the index here; later opens finish before anyone notices.
export const opening = reactive({ active: false, path: '', done: 0, total: 0 })
let syncPoll: number | undefined

export function watchLibrarySync() {
  window.clearInterval(syncPoll)
  syncPoll = window.setInterval(async () => {
    try {
      const s = await api.libraryStatus()
      Object.assign(opening, { active: s.running, path: s.path, done: s.done, total: s.total })
      if (s.running) return
      window.clearInterval(syncPoll)
      moveLegacyReaderPrefs() // the library has verified itself: "not found" now means it
      // the verification found the vault had moved on — show what it now holds
      if (s.changed) await Promise.all([reloadLibrary(), refreshDerived(), refreshTotal()])
    } catch { /* the sidecar is busy reading — the next tick reports */ }
  }, 700)
}

export async function refreshTotal() {
  try { store.total = (await api.libraryCount()).total } catch { /* keep the previous total */ }
}

export async function setLibraryPath(path: string): Promise<string | null> {
  Object.assign(opening, { active: true, path, done: 0, total: 0 })
  // what is waiting, or already on its way, belongs to the library being left
  if (legacyReader) legacyReader.stop = true
  await Promise.all([settlePageFilter(), legacyReader?.run])
  try {
    const s = await api.setLibraryPath(path)
    // the backend swapped to the new location — from here on, anything asked
    // of the library we left is stale, and everything is reloaded
    store.libraryGen++
    store.sourcesRead = false
    pageFilterKnown = false
    Object.assign(pageFilter, PAGE_FILTER_DEFAULTS)
    stopCaptureFor() // the armed entry belongs to the library we are leaving
    store.byId = {}
    store.metaSelection = null // a selection names the OLD vault's titles
    closeValuePage()
    store.openTabs = []
    store.pinnedTabs = []
    store.activeTitle = null
    store.readerTabs = {} // reader memory is keyed by the OLD vault's title ids
    store.reader = { chapterId: null, page: 0 }
    store.ui.libPage = 1
    store.ui.auPage = 1
    store.view = 'library'
    resetFilters()
    const all = await api.library()
    cache(all)
    store.total = all.length
    store.titles = all.map((t) => store.byId[t.id])
    await refreshDerived() // authors + sources + vocab all belong to the NEW vault
    void loadPageFilter() // …and so does what it knows about pages
    store.facets = await api.facets()
    watchLibrarySync() // the new library verifies itself behind us
    return s.library_path
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
    opening.active = false
    return null
  }
}

// ---- styled confirm dialog (replaces native window.confirm) ----
export interface ConfirmReq { title?: string; message: string; okLabel?: string; danger?: boolean }
export const confirmState = reactive<{ open: boolean; req: ConfirmReq; resolve: ((v: boolean) => void) | null }>({
  open: false, req: { message: '' }, resolve: null,
})
export function askConfirm(req: ConfirmReq): Promise<boolean> {
  return new Promise((resolve) => { confirmState.open = true; confirmState.req = req; confirmState.resolve = resolve })
}
export function resolveConfirm(value: boolean) {
  confirmState.resolve?.(value)
  confirmState.open = false
  confirmState.resolve = null
}
