// The app store: library data + linked faceted filters, and the user layer.
// Navigation — places and app tabs — is nav.ts, which reads and writes it.
// User-layer changes (star, rating, read state) are instant write-through —
// they never go through a draft (design/state-model.md §11). The draft itself
// lives in draft.ts; browser tabs live in browser.ts.
import { reactive, ref, watch, watchEffect } from 'vue'
import {
  api, ApiError, type DownloadItem, type DownloadsState, type LibraryQuery, type PassState,
} from './api'
import {
  chapterRowsOf, emptyFacets, metaOf, PAGE_FILTER_DEFAULTS, pageFilterOf, readerPrefsOf,
  sameChapter, type Chapter, type CopyJob, type Facets, type FieldDef, type MetaScope, type PageFilter,
  type PausedImport,
  type ReaderPrefs, type ReadState, type Source, type Title, type TitleCard,
} from './data'
import {
  isBoolMap, isRecord, localKeys, readLocal, readLocalOne, removeLocal, writeLocal, writeLocalOne,
} from './local'
import { stopCaptureFor } from './pagecapture'
import type { AppTab } from './nav'

export type View = 'library' | 'title' | 'reader' | 'authors' | 'page' | 'sources' | 'metadata' | 'import'
  | 'settings' | 'browser'
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
  // Every library tab, and every step its Back could bring back.
  if (surface === 'filters' && hidden) {
    for (const tab of store.tabs) {
      for (const p of [tab.place, ...tab.back, ...tab.forward]) {
        if (p.kind !== 'library') continue
        delete p.sel.include[id as FacetKey]
        delete p.sel.exclude[id as FacetKey]
      }
    }
  }
}

export interface LibraryFilters {
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
export function blankLibrary(): LibraryFilters {
  return {
    density: 'grid', search: '', sort: 'updated', favOnly: false, progress: 'all',
    // the shelf is which TYPE you are looking at — navigation, kept apart from
    // the filter bags so the filter UI never has to pretend it owns it
    minRating: 0, shelf: '', include: blankSelection(), exclude: blankSelection(),
  }
}

interface State {
  theme: ThemePref
  // what is on screen: the place of the app tab in front, or the web
  view: View
  tabs: AppTab[]     // the app strip, the home library tab first
  activeTab: string  // the app tab in front (DRAFT: an unsaved new title)
  libFocus: string   // the library tab `library` belongs to — the last one in front
  activeTitle: string | null
  browseHomepage: string
  libraryPath: string // the vault this window serves
  // data from the backend
  titles: TitleCard[] // current (filtered) library results
  total: number
  sources: Source[]
  sourceGroups: string[] // the user's ordered group names
  sourcesRead: boolean   // both of those came from the library (not a failed read)
  facets: Facets // linked counts for the CURRENT selection
  globalFacets: Facets // unfiltered counts — the STABLE row order for the sidebar
  // Counts under the SHELF alone: what an axis would actually find if you
  // opened it from here. Deliberately NOT the live selection — an axis list
  // that appears and disappears while you type in the search box is not a list.
  shelfFacets: Facets
  fields: FieldDef[] // the metadata field registry, served by the backend
  // suggestion vocabulary per field id — everything the library already holds
  vocab: Record<string, string[]>
  byId: Record<string, Title> // whole titles: the ones opened, and the detailed rows
  // every title the window has listed, as a list shows it — what a picker or
  // a name match searches; the library sends all of them at launch
  cards: Record<string, TitleCard>
  loading: boolean
  error: string | null
  notice: string | null // something done that is worth a word (a download kept loose)
  loose: number         // files waiting in the library's downloads folder
  library: LibraryFilters // the selection of the library tab in focus — each owns its own
  // app identity from <repo>/app-meta.json (via /api/settings)
  appMeta: { name?: string; version?: string; updated?: string; description?: string }
  // the reader (view 'reader') — the title is activeTitle, this is the position
  reader: { chapterId: string | null; page: number }
}

export const HOME = 'home'
export const DRAFT = 'draft'

export const store = reactive<State>({
  theme: 'dark',
  view: 'library',
  tabs: [],
  activeTab: HOME,
  libFocus: HOME,
  activeTitle: null,
  browseHomepage: 'https://www.google.com',
  libraryPath: '',
  titles: [],
  total: 0,
  sources: [],
  sourceGroups: [],
  sourcesRead: false,
  facets: emptyFacets(),
  globalFacets: emptyFacets(),
  shelfFacets: emptyFacets(),
  fields: [],
  vocab: {},
  byId: {},
  cards: {},
  loading: false,
  error: null,
  notice: null,
  loose: 0,
  library: blankLibrary(),
  appMeta: {},
  reader: { chapterId: null, page: 0 },
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
})

// ---- what page capture accepts as a page ----
//
// The library's: it lives in the vault beside the recipes, so it loads with the
// library. A change shows at once and is written a moment after the last one —
// a dragged slider is one write, not forty.
//
// Until the library has answered, a change has nothing true to build on and is
// refused: written over a read that failed, it would replace what is stored.
export const pageFilter = reactive<PageFilter>({ ...PAGE_FILTER_DEFAULTS })
let pageFilterKnown = false
const LEGACY_PAGE_FILTER = 'lb.pageFilter' // where an older build kept it, on this machine

let pageFilterTimer: number | undefined
function writePageFilter(): Promise<void> {
  window.clearTimeout(pageFilterTimer)
  pageFilterTimer = undefined
  return api.savePageFilter({ ...pageFilter }).then(() => undefined, (e) => {
    store.error = e instanceof Error ? e.message : String(e)
  })
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

async function loadPageFilter() {
  let stored: PageFilter
  try {
    stored = await api.pageFilter()
  } catch {
    return // an older build's value waits for a library that answers
  }
  Object.assign(pageFilter, stored)
  pageFilterKnown = true
  const legacy = readLocal(LEGACY_PAGE_FILTER, isRecord, null as Record<string, unknown> | null)
  if (!legacy) return
  // moved ONCE, into the library open now — unless it already says something of its own
  const untouched = stored.minPx === PAGE_FILTER_DEFAULTS.minPx
    && stored.widthRatio === PAGE_FILTER_DEFAULTS.widthRatio
  if (untouched) {
    try {
      Object.assign(pageFilter, await api.savePageFilter(pageFilterOf(legacy)))
    } catch {
      return // kept for the next start
    }
  }
  removeLocal(LEGACY_PAGE_FILTER)
}

// ---- data loading ----
// Whole titles. Each is a card as well: what a list shows of it moves with it.
// The two are separate objects — a card is refreshed by a listing, a whole
// title only whole, so a newer card never makes a stale title look current.
export function cache(list: Title[]) {
  for (const t of list) {
    const existing = store.byId[t.id]
    if (existing) Object.assign(existing, t) // keep object identity for open views
    else store.byId[t.id] = t
    const card = store.cards[t.id]
    if (card) Object.assign(card, t)
    else store.cards[t.id] = { ...t }
  }
}
// Cards, as a listing sends them. A whole title the window holds whose card
// moved on is fetched again.
export function cacheCards(list: TitleCard[]) {
  const moved: string[] = []
  for (const c of list) {
    const card = store.cards[c.id]
    if (card) Object.assign(card, c)
    else store.cards[c.id] = c
    if (store.byId[c.id] && store.byId[c.id].rev !== c.rev) moved.push(c.id)
  }
  if (moved.length) void fetchWhole(moved)
}
async function fetchWhole(ids: string[]) {
  for (let i = 0; i < ids.length; i += 100) { // what one ask may name
    try { cache(await api.titles(ids.slice(i, i + 100))) } catch (e) { store.error = String(e); return }
  }
}
// The index moved on: the whole titles the window holds are checked in one ask,
// and only the ones that changed fetched again — a title open in a tab, a
// draft's target, the detailed rows. One that is gone is let go of.
let heldAt = -1
async function keepHeldCurrent(at: number) {
  const ids = Object.keys(store.byId)
  if (at < 0 || at === heldAt || !ids.length) return
  heldAt = at
  let revs: Record<string, number>
  try { revs = await api.revs(ids) } catch { heldAt = -1; return }
  for (const id of ids) {
    if (revs[id] === undefined) {
      delete store.byId[id]
      delete store.cards[id]
    }
  }
  await fetchWhole(ids.filter((id) => revs[id] !== undefined && store.byId[id]?.rev !== revs[id]))
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

export function buildQuery(f: LibraryFilters = store.library): LibraryQuery {
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

// The Metadata view's scope: the titles a tab was handed, or exactly what the
// library shows under its filters right now.
export function metaScope(ids: string[] | null): MetaScope {
  if (ids) return { ids }
  const { sort: _sort, ...filters } = buildQuery()
  return filters
}

// What the window last loaded, by the index's change counter (`epoch`): a
// library tab shown again asks one number before reloading the whole library.
// Read BEFORE the load, so a change landing during it reads as a change.
let listedAt = -1
let derivedAt = -1
async function epochNow(): Promise<number> {
  try {
    const c = await api.libraryCount()
    store.total = c.total
    void keepHeldCurrent(c.epoch)
    return c.epoch
  } catch { return -1 }
}

export async function reloadLibrary() {
  store.loading = true
  store.error = null
  const q = buildQuery()
  const at = await epochNow()
  try {
    // results + linked facet counts move together
    const [list, facets] = await Promise.all([api.library(q), api.facets(q)])
    cacheCards(list)
    store.titles = list.map((t) => store.cards[t.id])
    store.facets = facets
    listedAt = at
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

// The sites and their groups — all a view of sites needs; the vocabulary is
// another, heavier question (refreshDerived).
export async function refreshSources() {
  try {
    const [sources, groups] = await Promise.all([api.sources(), api.sourceGroups()])
    store.sources = sources
    store.sourceGroups = groups
    store.sourcesRead = true
  } catch { /* leave the previous values in place */ }
}

// Sources and the vocab are derived from the titles — refetch after any commit
// or delete, or the source list and the suggestions go stale. Browse groups are
// NOT here: that view loads the axis it is actually showing.
export async function refreshDerived() {
  const at = await epochNow()
  await Promise.all([refreshSources(), refreshVocab(), refreshShelfFacets()])
  derivedAt = at
}

// A library or browse tab shown again: reload only what the library changed
// under — one count, where a reload re-reads every title.
export async function reloadIfChanged(withList: boolean) {
  const at = await epochNow()
  if (withList && at !== listedAt) await reloadLibrary()
  if (at !== derivedAt) void refreshDerived()
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
  watchVault() // a vault that drops covers the window, whatever is on it
  // Startup waits for the LIBRARY and nothing else: the grid is what the window
  // opens on, and a big vault makes every extra call visible as an empty screen.
  store.loading = true
  try {
    // the sync status comes WITH the first paint: an index still being filled
    // must read as "reading the library", never as "your library is empty"
    // the field registry rides along: it is a dozen rows, and nothing that
    // shows metadata can draw a thing without it
    const at = await epochNow()
    const [all, facets, settings, sync, fields] = await Promise.all(
      [api.library(), api.facets(), api.settings(), api.libraryStatus(), api.fields()])
    store.fields = fields
    Object.assign(opening, { active: sync.running, path: sync.path, done: sync.done, total: sync.total })
    cacheCards(all)
    store.total = all.length
    store.titles = all.map((t) => store.cards[t.id])
    // the unfiltered facets ARE the full vocabulary — asking twice is one
    // whole-library scan too many
    store.facets = facets
    applyVocab(facets)
    // no shelf is open at startup, so the unfiltered counts ARE the shelf's
    store.shelfFacets = facets
    listedAt = derivedAt = at
    if (settings.homepage) store.browseHomepage = settings.homepage
    store.appMeta = settings.app || {}
    store.libraryPath = settings.library_path
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  } finally {
    store.loading = false
  }
  // the source list is not on the way to the grid (the vocab came with it)
  void refreshSources()
  void loadPageFilter()
  void refreshLoose()
  void refreshPausedImports()
  // …and the vault check runs behind the window, refreshing it if disk moved on
  watchLibrarySync()
  void pollCopies()
  // what a previous run left unfinished is already on the sidecar's list
  void pollDownloads(true)
  window.setInterval(() => void pollDownloads(), 1000)
  // one nobody armed is not polled for: the shell says when a transfer begins or ends
  window.longbox?.onDownloadsChanged?.(() => void pollDownloads(true))
  // a pass survives leaving the page, so following it has to as well
  void pollVaultPass(true)
  window.setInterval(() => void pollVaultPass(), 600)
  // refetch results whenever a server-side filter changes (density is client-only)
  onSelectionChange(reloadLibrary)
}

// Clears the FILTERS. The shelf survives: you asked to see this type, and
// clearing a genre is not a request to leave it.
// (These take the selection they act on: the library tab's, or a value page's.)
export function resetFilters(f: LibraryFilters = store.library) {
  Object.assign(f, {
    search: '', favOnly: false, progress: 'all', minRating: 0,
    include: blankSelection(), exclude: blankSelection(),
  })
}

// ---- faceted selection: off → include → exclude → off ----
export function facetState(key: FacetKey, value: string, f: LibraryFilters = store.library): 'in' | 'out' | '' {
  if (selected(f.include, key).includes(value)) return 'in'
  if (selected(f.exclude, key).includes(value)) return 'out'
  return ''
}
export function toggleFacet(key: FacetKey, value: string, f: LibraryFilters = store.library) {
  const inc = selected(f.include, key)
  const exc = selected(f.exclude, key)
  const i = inc.indexOf(value)
  const e = exc.indexOf(value)
  if (i >= 0) { inc.splice(i, 1); exc.push(value) }
  else if (e >= 0) { exc.splice(e, 1) }
  else inc.push(value)
}
export function anyFilterActive(f: LibraryFilters = store.library): boolean {
  return !!f.search || f.favOnly || f.progress !== 'all' || f.minRating !== 0
    || [...Object.values(f.include), ...Object.values(f.exclude)].some((v) => v.length)
}

// A whole title: the one the window holds, or asked for.
export async function loadTitle(id: string): Promise<Title | undefined> {
  if (!store.byId[id]) {
    try { cache([await api.title(id)]) } catch (e) { store.error = String(e) }
  }
  return store.byId[id]
}
// Whole titles for what a list shows open (the detailed rows) — in one ask.
export async function loadTitles(ids: string[]) {
  await fetchWhole(ids.filter((id) => !store.byId[id]))
}
// A title that no longer exists cannot be captured into — the session ends with
// it instead of posting pages at a dead id every couple of seconds.
export function forgetTitle(id: string) {
  delete store.byId[id]
  delete store.cards[id]
  stopCaptureFor({ titleId: id })
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
                 pick: (c: { lang: string; group: string }) => string,
                 held: (t: TitleCard) => string[]): { near: string[]; all: string[] } {
  const near = new Set(chapters.map(pick).filter(Boolean))
  const all = new Set(near)
  const host = domain.toLowerCase()
  for (const t of Object.values(store.cards)) {
    const sameSource = host && (t.source.domain || '').toLowerCase() === host
    for (const v of held(t)) {
      if (sameSource) near.add(v)
      all.add(v)
    }
  }
  return { near: [...near], all: [...all] }
}

export function langRings(chapters: { lang: string; group: string }[] = [], domain = '') {
  const rings = ringsOf(chapters, domain, (c) => c.lang, (t) => t.chapterLangs)
  // the vocabulary the library already knows is near enough for a language
  for (const v of store.vocab.language ?? []) {
    if (!rings.near.includes(v)) rings.near.push(v)
    if (!rings.all.includes(v)) rings.all.push(v)
  }
  return rings
}
export function groupRings(chapters: { lang: string; group: string }[] = [], domain = '') {
  return ringsOf(chapters, domain, (c) => c.group, (t) => t.chapterGroups)
}

// How many things a selection has switched on — what the Filters button
// counts, in the library and in the browse view alike, and a tab's label.
export function activeFilterCount(f: LibraryFilters = store.library): number {
  const n = (bag: Record<string, string[]>) => Object.values(bag).reduce((t, v) => t + v.length, 0)
  return n(f.include) + n(f.exclude)
    + (f.favOnly ? 1 : 0) + (f.progress !== 'all' ? 1 : 0) + (f.minRating ? 1 : 0)
}

// ---- lookups ----
export function titleById(id: string | null): Title | undefined {
  return id ? store.byId[id] : undefined
}
export function cardById(id: string | null): TitleCard | undefined {
  return id ? store.cards[id] : undefined
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
      if (it.state === 'downloading') seenDone.delete(it.id) // picked up again: its end is news
      else if (it.state === 'done' && !seenDone.has(it.id)) {
        seenDone.add(it.id)
        if (it.loose) {
          store.notice = `Saved to the library's downloads — ${it.filename}`
          void refreshLoose()
        } else {
          void refreshTitle(it.titleId) // its chapter now has media
        }
      } else if ((it.state === 'failed' || it.state === 'interrupted') && !seenDone.has(it.id)) {
        seenDone.add(it.id)
        // a file nobody armed that never arrived: no entry shows it, so this says so
        if (it.loose && it.state === 'failed') store.error = `${it.filename || 'A download'} was ${it.error}`
      }
    }
    downloads.armed = s.armed
    downloads.items = s.items
  } catch { /* the sidecar is busy or gone — keep what we have */ }
}

// One transfer's controls, for the activity panel and the capture dock alike.
// The transfer is the SHELL's (it owns the Electron item), the record the
// sidecar's — both are told, in that order.
export async function stopDownload(id: string): Promise<void> {
  const file = downloads.items.find((i) => i.id === id)?.resume?.path
  await window.longbox?.cancelDownload(id)
  try { await api.forgetDownload(id) } catch { /* already gone */ }
  // a paused one's bytes were kept to be picked up; forgotten, they go
  if (file) await window.longbox?.forgetPartial(file)
  await pollDownloads(true)
}
export async function pauseDownload(id: string): Promise<void> {
  if (!(await window.longbox?.pauseDownload(id))) store.error = 'that download is no longer running'
  await pollDownloads(true)
}
// Picked up where it stopped, in its own row. A server that will not honour the
// range answers from zero — Electron reports that as a failure, and the row is
// cleared to be started over from the source page.
export async function resumeDownload(id: string): Promise<void> {
  const rec = downloads.items.find((i) => i.id === id)
  if (!rec?.resume) return
  // plain data across the bridge: a reactive row is a proxy it cannot copy
  const ok = await window.longbox?.resumeDownload(JSON.parse(JSON.stringify({ id, resume: rec.resume })))
  if (!ok) store.error = 'this transfer cannot be picked up — start it over from the source page'
  await pollDownloads(true)
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
    const was = vaultPass.running
    Object.assign(vaultPass, await api.passStatus())
    // an import put down lands on the paused list as its run ends
    if (was && !vaultPass.running) void refreshPausedImports()
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

// ---- imports put down to be carried on: this library's, kept across restarts ----
export const pausedImports = ref<PausedImport[]>([])
export async function refreshPausedImports(): Promise<void> {
  try { pausedImports.value = await api.pausedImports() } catch { /* keep the previous list */ }
}
/** Put the running import down between two entries; the run that is going
 *  returns once it has, and is then on the paused list. */
export async function pauseImport(): Promise<void> {
  try { Object.assign(vaultPass, await api.pauseImport()) } catch (e) { store.error = String(e) }
}
export async function resumeImport(id: string): Promise<void> {
  await runVaultPass(() => api.resumeImport(id))
  await refreshPausedImports()
  void refreshLibrary()
}
export async function forgetPausedImport(id: string): Promise<void> {
  try { await api.forgetPausedImport(id) } catch (e) { store.error = String(e) }
  await refreshPausedImports()
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

export async function setFavorite(t: TitleCard, value: boolean) {
  await writeThrough((v) => { t.fav = v }, value, t.fav,
                     () => api.patchUser(t.id, { fav: value }))
}
export async function toggleFav(t: TitleCard) { await setFavorite(t, !t.fav) }
export async function setRating(t: TitleCard, value: number) {
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
// asked about, or whose write failed, stays for the next start.
const LEGACY_READER = 'lb.reader.'
let legacyReaderMoved = false

function moveLegacyReaderPrefs() {
  if (legacyReaderMoved) return
  legacyReaderMoved = true
  void (async () => {
    for (const key of localKeys(LEGACY_READER)) {
      if (key === 'lb.reader.default') continue // the vault's default, still used
      const raw = readLocal(key, isRecord, null as Record<string, unknown> | null)
      let t: Title
      try {
        t = await api.title(key.slice(LEGACY_READER.length))
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) removeLocal(key)
        continue
      }
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
export async function addChapterRow(t: Title, ch: MediaEntry): Promise<Chapter | null> {
  if (t.chapters.some((c) => sameChapter(c, ch))) {
    store.error = `Chapter ${ch.num}${ch.lang ? ' · ' + ch.lang : ''} already exists`
    return null
  }
  try {
    return await commitChapterRow(t, ch)
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
    return null
  }
}
async function commitChapterRow(t: Title, ch: MediaEntry): Promise<Chapter> {
  const body = commitBodyFrom(t)
  body.chapters.push({ id: '', num: ch.num, title: '', url: ch.url || '', lang: ch.lang, group: ch.group,
                       date: '', section: ch.section || '' })
  const saved = await api.commitTitle(t.id, body)
  cache([saved])
  const added = saved.chapters.find((c) => sameChapter(c, ch))
  // The commit came back without the row it was asked for. That is a bug
  // wherever it happens, and it used to happen in silence — a button that
  // looked broken. Say so rather than return nothing.
  if (!added) throw new Error(`Chapter ${ch.num} was not added — the library refused the row`)
  return added
}

// ---- media added by hand: queued, so no form ever waits on an upload ----
//
// An archive, an episode or a set of images given to an entry is a JOB. It
// carries its title and its entry with it — by the time it runs, the form that
// queued it may be showing another title — and the jobs run one after another,
// behind whatever is done next. Activity lists them; a failed one stays there
// until it is tried again or dropped.
export interface MediaEntry { num: string; lang: string; group: string; url?: string; section?: string }
export interface MediaJob {
  id: string
  titleId: string
  entry: MediaEntry
  chapterId: string // the entry it goes into; '' — the one with this identity, made if there is none
  kind: 'archive' | 'images'
  files: File[]
  state: 'queued' | 'running' | 'failed'
  error: string
}
export const mediaJobs = reactive<MediaJob[]>([])
let mediaSeq = 0
let mediaRunning = false
export function queueMedia(job: Pick<MediaJob, 'titleId' | 'entry' | 'chapterId' | 'kind' | 'files'>) {
  mediaJobs.push({ ...job, id: `media-${++mediaSeq}`, state: 'queued', error: '' })
  void runMedia()
}
export function retryMedia(id: string) {
  const job = mediaJobs.find((j) => j.id === id && j.state === 'failed')
  if (!job) return
  Object.assign(job, { state: 'queued', error: '' })
  void runMedia()
}
// a queued job given up, or a failed one dismissed — never the one being sent
export function dropMedia(id: string) {
  const i = mediaJobs.findIndex((j) => j.id === id && j.state !== 'running')
  if (i >= 0) mediaJobs.splice(i, 1)
}
async function runMedia() {
  if (mediaRunning) return
  mediaRunning = true
  try {
    for (let job = nextMedia(); job; job = nextMedia()) {
      job.state = 'running'
      try {
        await sendMedia(job)
        mediaJobs.splice(mediaJobs.indexOf(job), 1)
      } catch (e) {
        Object.assign(job, { state: 'failed', error: e instanceof Error ? e.message : String(e) })
      }
    }
  } finally {
    mediaRunning = false
  }
}
const nextMedia = () => mediaJobs.find((j) => j.state === 'queued')
async function sendMedia(job: MediaJob) {
  if (job.kind === 'archive') {
    cache([await api.importChapterArchive(job.titleId,
      { ...job.entry, chapterId: job.chapterId || undefined }, job.files[0])])
    return
  }
  let cid = job.chapterId
  if (!cid) {
    const t = store.byId[job.titleId] ?? await api.title(job.titleId)
    cid = t.chapters.find((c) => sameChapter(c, job.entry))?.id ?? (await commitChapterRow(t, job.entry)).id
  }
  cache([await api.addChapterPages(job.titleId, cid, job.files)])
}

// ---- playable copies (design/playable-copies.md) ----
// Made by the sidecar, which queues them itself — on ingest, on opening a
// library — so the list is asked for on a slow tick even when nothing is known
// to be waiting, and a quick one while something is.
export const copies = reactive<{ available: boolean; jobs: CopyJob[] }>({ available: true, jobs: [] })
let copyPoll: number | undefined
// The copies that landed come numbered, so one made start to finish between two
// slow ticks still reaches the title that shows it. -1: not asked yet — whatever
// landed before then is already in what was loaded.
let landedSeen = -1
export async function pollCopies(): Promise<void> {
  try {
    const now = await api.copies()
    const fresh = landedSeen < 0 ? [] : now.landed.filter((l) => l.seq > landedSeen)
    landedSeen = Math.max(landedSeen, 0, ...now.landed.map((l) => l.seq))
    Object.assign(copies, { available: now.available, jobs: now.jobs })
    await Promise.all([...new Set(fresh.map((l) => l.titleId))].map(refreshTitle))
  } catch { /* the sidecar is busy — the next tick asks again */ }
  window.clearTimeout(copyPoll)
  copyPoll = window.setTimeout(() => void pollCopies(),
    copies.jobs.some((j) => j.state !== 'failed') ? 1000 : 10000)
}
export function copyJobFor(titleId: string, chapterId: string): CopyJob | undefined {
  return copies.jobs.find((j) => j.titleId === titleId && j.chapterId === chapterId)
}
// The window could not show an episode, or the human asked for its copy.
export async function requestCopy(titleId: string, chapterId: string): Promise<void> {
  try {
    const got = await api.requestCopy(titleId, chapterId)
    if (got.state === 'landed') await refreshTitle(titleId) // made already: show it
    else if (!copyJobFor(titleId, chapterId)) copies.jobs.push(got)
  } catch { /* no ffmpeg here — the player says what it can */ }
  void pollCopies()
}
export async function retryCopy(id: string): Promise<void> {
  try { await api.retryCopy(id) } catch { /* it already left the list */ }
  void pollCopies()
}
// a waiting or running copy given up, or a failed one dismissed
export async function dropCopy(id: string): Promise<void> {
  try { await api.dropCopy(id) } catch { /* it already left the list */ }
  void pollCopies()
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
      if (s.running || s.migrating) return
      window.clearInterval(syncPoll)
      moveLegacyReaderPrefs() // the library has verified itself: "not found" now means it
      // the verification (or a one-time pass) found the vault had moved on —
      // show what it now holds, in the grid and in titles a tab already drew
      if (s.changed || s.migrated) await Promise.all([reloadLibrary(), refreshDerived(), refreshTotal()])
      if (s.migrated) {
        await Promise.all([...new Set(store.tabs.flatMap((t) =>
          t.place.kind === 'title' || t.place.kind === 'reader' ? [t.place.id] : []))].map(refreshTitle))
      }
    } catch { /* the sidecar is busy reading — the next tick reports */ }
  }, 700)
}

// How many files wait in the library's downloads (design/subtitles.md) — the
// activity corner shows them, so they are never out of sight.
export async function refreshLoose() {
  try { store.loose = (await api.looseCount()).n } catch { /* keep the previous count */ }
}

// The source groups as they are drawn: the stored list in its order, then any
// name a site still carries that the list lost — or that site would vanish from
// the Sources page and the browser's rail alike.
export function sourceGroupNames(): string[] {
  const listed = [...store.sourceGroups]
  for (const s of store.sources) {
    if (s.group && !listed.includes(s.group)) listed.push(s.group)
  }
  return listed
}

export async function refreshTotal() {
  try { store.total = (await api.libraryCount()).total } catch { /* keep the previous total */ }
}

// ---- the vault (design/vault-home.md) ----------------------------------------
//
// Everything the app keeps lives in the vault it serves — so opening another
// vault is a restart into it, made by the shell (restart.ts).
// While the vault does not answer the window is covered (VaultLost.vue): the
// sidecar refuses every request, and the shell restarts the app into the vault
// the moment it answers again. Lost is the sidecar's watch to say (two answers
// in a row), never one failed request's.
export const vault = reactive({ lost: false, since: '', path: '', name: '', share: '' })

export function watchVault() {
  window.longbox?.onVault((v) => {
    if (v.state === 'lost') Object.assign(vault, { lost: true, since: v.since, path: v.path, name: v.name, share: v.share })
  })
}

export const vaultName = (path: string): string => path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path

