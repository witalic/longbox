// The app store: navigation, library data + linked faceted filters, and the
// user layer. User-layer changes (star, rating, read state) are instant
// write-through — they never go through a draft (design/state-model.md §11).
// The draft itself lives in draft.ts; browser tabs live in browser.ts.
import {
  inject, onActivated, onDeactivated, reactive, ref, watch, watchEffect, type InjectionKey, type Ref,
} from 'vue'
import {
  api, ApiError, type DownloadItem, type DownloadsState, type LibraryQuery, type PassState,
} from './api'
import {
  chapterRowsOf, emptyFacets, metaOf, PAGE_FILTER_DEFAULTS, pageFilterOf, readerPrefsOf,
  sameChapter, type Chapter, type CopyJob, type Facets, type FieldDef, type MetaScope, type PageFilter,
  type PausedImport,
  type ReaderPrefs, type ReadState, type Source, type Title,
} from './data'
import {
  flushPrefs, isBoolMap, isRecord, localKeys, readLocal, readLocalOne, removeLocal, writeLocal, writeLocalOne,
} from './local'
import { stopCaptureFor } from './pagecapture'
import { adoptChildren, forgetOpeners, inStripOrder, insertionIndex, nextAfterClose, type Opening } from './tabstrip'

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
function blankLibrary(): LibraryFilters {
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
  titles: Title[] // current (filtered) library results
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
  byId: Record<string, Title> // cache for open tabs / detail view
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
  try {
    const [sources, groups] = await Promise.all([api.sources(), api.sourceGroups()])
    store.sources = sources
    store.sourceGroups = groups
    store.sourcesRead = true
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
  watchVault() // a vault that drops covers the window, whatever is on it
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
    store.libraryPath = settings.library_path
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  } finally {
    store.loading = false
  }
  // the Authors tab and the source list are not on the way to the grid
  void refreshDerived()
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

// ---- navigation: places and tabs (design/navigation.md) ----
//
// Everything the app shows is a PLACE. An app tab holds one, the trail that
// led to it (Back / Forward) and — kept alive by TabHost — what was done there,
// until it is closed. The web keeps its own tabs and strip (browser.ts).
// `view`, `activeTitle`, `reader` and `library` are what the tab in front holds.
export interface ValueRef { field: string; value: string }
// a library tab: its selection, and the axis it is browsed by ('' — its grid)
export interface LibraryPlace { kind: 'library'; sel: LibraryFilters; axis: string; page: number }
export interface TitlePlace { kind: 'title'; id: string }
export interface ReaderPlace { kind: 'reader'; id: string; chapterId: string; page: number }
// a value's page: its works are the library scoped to it, under the page's own
// selection, a person's role (the field they hold the value in, '' — any) and page
export interface PagePlace {
  kind: 'page'; field: string; value: string; sel: LibraryFilters; role: string; page: number
}
// the titles it was handed (null — the filters of library tab `lib`, the one it
// was opened from), and a value to open on
export interface MetadataPlace { kind: 'metadata'; ids: string[] | null; focus: ValueRef | null; lib: string }
export interface SectionPlace { kind: 'import' | 'settings' }
export type Place = LibraryPlace | TitlePlace | ReaderPlace | PagePlace | MetadataPlace | SectionPlace
export interface AppTab {
  id: string
  place: Place
  back: Place[]
  forward: Place[]
  pinned: boolean
  hold: string // why the tab may not move or close quietly (a page being edited) — '' when it may
  opener: string | null // the tab it was opened from, while the run lasts (tabstrip.ts)
}
type LibraryTab = AppTab & { place: LibraryPlace }
// Where a request to open a place comes from: the tab in front ('page' — a
// title, a value, a link on it), or outside any page ('outside' — the rail, +,
// a restored session), which opens at the end of the strip.
export type OpenedFrom = 'page' | 'outside'

const MAX_STEPS = 50
let tabSeq = 0
// The strip's own order: the library tab, the pinned ones, the rest.
export function appStripOrder(): AppTab[] {
  return inStripOrder(store.tabs, (t) => t.id === HOME || t.pinned)
}
// A new tab, where Chrome would put it (tabstrip.ts): opened from the app tab in
// front — or from the end of the strip when nothing in front opened it.
function addTab(place: Place, how: Opening = 'end'): AppTab {
  const from = how !== 'end' && store.view !== 'browser' && store.view !== 'sources' ? tabById(store.activeTab) : undefined
  if (how === 'front') forgetOpeners(store.tabs)
  const shown = appStripOrder()
  const before = shown[insertionIndex(shown, from?.id ?? null, from ? how : 'end',
    1 + store.tabs.filter((t) => t.pinned).length)]
  const fresh: AppTab = { id: `t${++tabSeq}`, place, back: [], forward: [], pinned: false, hold: '', opener: from?.id ?? null }
  store.tabs.splice(before ? store.tabs.indexOf(before) : store.tabs.length, 0, fresh)
  const tab = tabById(fresh.id)!
  settle(tab)
  return tab
}
const openedAs = (background: boolean, from: OpenedFrom): Opening =>
  from === 'outside' ? 'end' : background ? 'link' : 'front'
export function tabById(id: string): AppTab | undefined {
  return store.tabs.find((t) => t.id === id)
}
export function activeAppTab(): AppTab | undefined {
  return tabById(store.activeTab)
}
// the focus is always a library tab — the home tab when nothing else
function libraryTab(): LibraryTab {
  return (tabById(store.libFocus) ?? store.tabs[0]) as LibraryTab
}
function titleTab(id: string): AppTab | undefined {
  return store.tabs.find((t) => (t.place.kind === 'title' || t.place.kind === 'reader') && t.place.id === id)
}

function viewOf(p: Place): View {
  return p.kind === 'library' ? (p.axis ? 'authors' : 'library') : p.kind
}
// The library tab whose selection `store.library` is — the filters the library,
// the browse groups and a Metadata scope are all under.
function focusLibrary(tab: LibraryTab) {
  if (store.libFocus === tab.id) return
  flushStep()
  store.libFocus = tab.id
  store.library = tab.place.sel
}
// Put a tab in front: what it holds becomes what the app shows. A library tab
// takes the focus; a Metadata tab on the library's filters gives it back to the
// library tab it was opened from, so its scope cannot change underneath it.
function project(tab: AppTab) {
  const p = tab.place
  if (p.kind === 'library') focusLibrary(tab as LibraryTab)
  if (p.kind === 'metadata' && !p.ids) {
    const lib = tabById(p.lib)
    if (lib?.place.kind === 'library') focusLibrary(lib as LibraryTab)
    else p.lib = store.libFocus // the tab it came from is closed: the one in focus now
  }
  store.activeTab = tab.id
  store.activeTitle = p.kind === 'title' || p.kind === 'reader' ? p.id : null
  if (p.kind === 'reader') Object.assign(store.reader, { chapterId: p.chapterId, page: p.page })
  store.view = viewOf(p)
}
async function ensureTitle(id: string) {
  if (store.byId[id]) return
  try { cache([await api.title(id)]) } catch (e) { store.error = String(e) }
}
export function showTab(id: string) {
  const tab = tabById(id)
  if (!tab) return
  // coming back to a library tab whose selection is already the focus: nothing
  // else will ask again, and the vault can change underneath (a pass, a sync)
  const again = tab.place.kind === 'library' && store.libFocus === tab.id
    && (store.activeTab !== tab.id || store.view === 'browser' || store.view === 'sources')
  project(tab)
  const p = tab.place
  if (p.kind === 'title' || p.kind === 'reader') void ensureTitle(p.id)
  else if (p.kind === 'library' && p.axis) void refreshDerived()
  if (again) void reloadLibrary()
}
// Back from the web to the app tab that was in front.
export function showApp() {
  if (store.activeTab === DRAFT) showDraft()
  else showTab(tabById(store.activeTab) ? store.activeTab : HOME)
}
export function showSources() {
  store.view = 'sources'
  void refreshDerived()
}

// An unsaved NEW title has no id to be a tab of: it is drawn on its own, and
// leaving it returns to the tab it was started from.
let beforeDraft = HOME
export function showDraft() {
  if (store.activeTab !== DRAFT) beforeDraft = store.activeTab
  store.activeTab = DRAFT
  store.activeTitle = null
  store.view = 'title'
}
export function leaveDraft() {
  const back = tabById(beforeDraft) ? beforeDraft : HOME
  if (store.view === 'browser' || store.view === 'sources') store.activeTab = back
  else showTab(back)
}

// ---- Back and Forward: each tab walks its own trail ----
function cloneSel(s: LibraryFilters): LibraryFilters {
  return JSON.parse(JSON.stringify(s))
}
function clonePlace(p: Place): Place {
  if (p.kind === 'library') return { ...p, sel: cloneSel(p.sel) }
  if (p.kind === 'metadata') return { ...p, ids: p.ids && [...p.ids], focus: p.focus && { ...p.focus } }
  return { ...p }
}
// A step inside a tab: the place it leaves is the one Back returns to.
function step(tab: AppTab, to: Place) {
  tab.back.push(clonePlace(tab.place))
  if (tab.back.length > MAX_STEPS) tab.back.shift()
  tab.forward = []
  tab.place = to
  if (tab.id === store.activeTab) project(tab)
}
// A library tab keeps ONE selection object for its life — the filter block and
// the results are bound to it — so a step back is copied into it, not swapped.
function arrive(tab: AppTab, to: Place) {
  const cur = tab.place
  if (cur.kind === 'library' && to.kind === 'library') {
    Object.assign(cur.sel, cloneSel(to.sel), { density: cur.sel.density })
    cur.axis = to.axis
    cur.page = 1
    settle(tab)
  } else {
    tab.place = to
  }
  if (tab.id === store.activeTab) project(tab)
}
export function goBack(steps = 1) {
  const tab = activeAppTab()
  if (!tab || tab.hold) return
  flushStep()
  for (let i = 0; i < steps && tab.back.length; i++) {
    tab.forward.push(clonePlace(tab.place))
    arrive(tab, tab.back.pop()!)
  }
}
export function goForward() {
  const tab = activeAppTab()
  if (!tab || tab.hold || !tab.forward.length) return
  flushStep()
  tab.back.push(clonePlace(tab.place))
  arrive(tab, tab.forward.pop()!)
}

// A library tab's steps are taken as its selection settles, not per keystroke:
// a burst of changes (a search being typed) is one step once it stops.
const settled = new Map<string, LibraryPlace>()
let stepTimer: number | undefined
function libKey(p: LibraryPlace): string {
  const { density: _density, include, exclude, ...rest } = p.sel
  const bag = (b: Record<string, string[]>) =>
    Object.entries(b).filter(([, v]) => v.length).sort(([a], [b2]) => a.localeCompare(b2))
  return JSON.stringify([p.axis, rest, bag(include), bag(exclude)])
}
function settle(tab: AppTab) {
  if (tab.place.kind === 'library') settled.set(tab.id, clonePlace(tab.place) as LibraryPlace)
}
function recordStep() {
  const tab = libraryTab()
  const was = settled.get(tab.id)
  if (was && libKey(was) !== libKey(tab.place)) {
    tab.back.push(was)
    if (tab.back.length > MAX_STEPS) tab.back.shift()
    tab.forward = []
  }
  settle(tab)
}
function flushStep() {
  if (stepTimer === undefined) return
  window.clearTimeout(stepTimer)
  stepTimer = undefined
  recordStep()
}
// ---- titles: one tab per title ----
export function openTitle(id: string) {
  showTab((titleTab(id) ?? addTab({ kind: 'title', id }, 'front')).id)
}
// Middle-click: the tab opens behind the one in front.
export function openTitleBackground(id: string, from: OpenedFrom = 'page') {
  if (!titleTab(id)) addTab({ kind: 'title', id }, openedAs(true, from))
  void ensureTitle(id)
}
// A title that no longer exists cannot be captured into — the session ends with
// it instead of posting pages at a dead id every couple of seconds.
function forgetTitle(id: string) {
  delete store.byId[id]
  stopCaptureFor({ titleId: id })
}
function forgetTitleTabs(id: string) {
  const gone = store.tabs.filter((t) => (t.place.kind === 'title' || t.place.kind === 'reader') && t.place.id === id)
  for (const t of gone) dropTab(t.id)
}
// Closing is the only thing that forgets. The home tab stays; a tab holding
// unsaved work asks first.
export async function closeTab(id: string) {
  const tab = tabById(id)
  if (!tab || id === HOME) return
  if (tab.hold && !(await askConfirm({
    title: 'Close the tab', danger: true, okLabel: 'Close',
    message: `${tab.hold} Close the tab and drop it?`,
  }))) return
  dropTab(id)
}
function dropTab(id: string) {
  const i = store.tabs.findIndex((t) => t.id === id)
  if (i <= 0) return // the home tab is first, and stays
  if (store.libFocus === id) flushStep()
  const shown = appStripOrder()
  const next = nextAfterClose(shown, shown.findIndex((t) => t.id === id))
  const [gone] = store.tabs.splice(i, 1)
  adoptChildren(store.tabs, gone)
  settled.delete(id)
  if (store.libFocus === id) {
    const lib = (store.tabs.find((t) => t.place.kind === 'library') ?? store.tabs[0]) as LibraryTab
    store.libFocus = lib.id
    store.library = lib.place.sel
  }
  if (beforeDraft === id) beforeDraft = HOME
  if (store.activeTab !== id) return
  const after = tabById(next ?? HOME) ?? store.tabs[0]
  if (store.view === 'browser' || store.view === 'sources') store.activeTab = after.id
  else showTab(after.id)
}
export function toggleTabPin(id: string) {
  const tab = tabById(id)
  if (tab && id !== HOME) tab.pinned = !tab.pinned
}
// Reorder (drag & drop in the strip or the all-tabs menu): `dragId` before
// `targetId` ('#end' — last). Nothing goes before the home tab.
export function moveTabBefore(dragId: string, targetId: string) {
  if (dragId === targetId || dragId === HOME) return
  const from = store.tabs.findIndex((t) => t.id === dragId)
  if (from < 0) return
  const [tab] = store.tabs.splice(from, 1)
  const to = store.tabs.findIndex((t) => t.id === targetId)
  store.tabs.splice(to < 0 ? store.tabs.length : Math.max(1, to), 0, tab)
}

// ---- the reader: a step inside the title's own tab ----
export function openReader(titleId: string, chapterId: string, page = 0) {
  const tab = titleTab(titleId) ?? addTab({ kind: 'title', id: titleId }, 'front')
  const spot: ReaderPlace = { kind: 'reader', id: titleId, chapterId, page }
  if (tab.place.kind === 'reader') tab.place = spot // already reading: another spot, not another step
  else step(tab, spot)
  showTab(tab.id)
}
export function closeReader() {
  const tab = activeAppTab()
  if (!tab || tab.place.kind !== 'reader') return
  const top = tab.back[tab.back.length - 1]
  if (top?.kind === 'title' && top.id === tab.place.id) goBack()
  else arrive(tab, { kind: 'title', id: tab.place.id })
}
// the tab remembers the reading spot — coming back to it re-enters there
export function keepReaderSpot(chapterId: string, page: number) {
  const p = activeAppTab()?.place
  if (p?.kind === 'reader') Object.assign(p, { chapterId, page })
}

// ---- value pages (design/value-pages.md): a value's page is a tab of its own ----
// one tab per value — authors and artists share the people's pages
function pageKey(field: string, value: string): string {
  const vocab = store.fields.find((f) => f.id === field)?.vocab || field
  return `${vocab}\n${value.trim().toLowerCase()}`
}
// a page opens on all its works: another page's filters are that page's own
function pagePlace(field: string, value: string): PagePlace {
  return { kind: 'page', field, value, sel: blankLibrary(), role: '', page: 1 }
}
export function openValuePage(field: string, value: string, background = false, from: OpenedFrom = 'page') {
  const k = pageKey(field, value)
  const tab = store.tabs.find((t) => t.place.kind === 'page' && pageKey(t.place.field, t.place.value) === k)
    ?? addTab(pagePlace(field, value), openedAs(background, from))
  if (!background) showTab(tab.id)
}
// A neighbour reached FROM a page continues in that page's tab — Back returns.
export function followValue(field: string, value: string) {
  const tab = activeAppTab()
  if (!tab || tab.place.kind !== 'page') return openValuePage(field, value)
  if (pageKey(tab.place.field, tab.place.value) !== pageKey(field, value)) step(tab, pagePlace(field, value))
}

// ---- the library tabs ----
export function goLibrary() {
  showTab(libraryTab().id)
}
export function newLibraryTab(init: Partial<LibraryFilters> & { axis?: string } = {}, background = false) {
  const { axis = '', ...sel } = init
  // switched to (+), it remembers the tab it was opened over: closing it goes back there
  const from = background ? null : activeAppTab()?.id ?? null
  const tab = addTab({ kind: 'library', sel: { ...blankLibrary(), density: store.library.density, ...sel }, axis, page: 1 })
  tab.opener = from
  if (!background) showTab(tab.id)
}
// the library tab in focus, browsed by this axis ('' — its grid)
export function openAxis(axis: string) {
  const tab = libraryTab()
  if (tab.place.axis !== axis) Object.assign(tab.place, { axis, page: 1 })
  showTab(tab.id)
}
// The rail's Browse by: clicking the axis already open goes back to the grid —
// the shelves' grammar, one way in and the same way out.
export function goBrowse(axis: string) {
  const open = store.view === 'authors' && libraryTab().place.axis === axis
  openAxis(open ? '' : axis)
}
// "Show in library": the library tab in focus, its grid filtered to exactly
// this value — a step its Back undoes. From a value page it carries the page's
// own filters along, so the library shows what the page did.
export function showInLibrary(field: string, value: string, filters: LibraryFilters | null = null) {
  const tab = libraryTab()
  const f: LibraryFilters = JSON.parse(JSON.stringify(filters ?? blankLibrary()))
  Object.assign(tab.place.sel, {
    search: f.search, favOnly: f.favOnly, progress: f.progress, minRating: f.minRating, shelf: '',
    include: { ...f.include, [field]: [...new Set([...(f.include[field] ?? []), value])] },
    exclude: f.exclude,
    ...(filters ? { sort: f.sort } : {}),
  })
  Object.assign(tab.place, { axis: '', page: 1 })
  showTab(tab.id)
}

// ---- sections: one tab each; Metadata also opens on exactly what it is handed ----
export type Section = 'metadata' | 'import' | 'settings'
export function openSection(kind: Section, background = false) {
  const tab = store.tabs.find((t) => t.place.kind === kind
    && (t.place.kind !== 'metadata' || (!t.place.ids && !t.place.focus)))
    ?? addTab(kind === 'metadata' ? { kind, ids: null, focus: null, lib: store.libFocus } : { kind })
  if (!background) showTab(tab.id)
}
export function editInMetadata(ids: string[], background = false, from: OpenedFrom = 'page') {
  const key = JSON.stringify(ids)
  const tab = store.tabs.find((t) => t.place.kind === 'metadata' && !!t.place.ids && JSON.stringify(t.place.ids) === key)
    ?? addTab({ kind: 'metadata', ids: [...ids], focus: null, lib: store.libFocus }, openedAs(background, from))
  if (!background) showTab(tab.id)
}
// a value page's "In Metadata": its field, the value searched and selected
export function editValueInMetadata(field: string, value: string, background = false, from: OpenedFrom = 'page') {
  const tab = store.tabs.find((t) => t.place.kind === 'metadata'
    && t.place.focus?.field === field && t.place.focus.value === value)
    ?? addTab({ kind: 'metadata', ids: null, focus: { field, value }, lib: store.libFocus }, openedAs(background, from))
  if (!background) showTab(tab.id)
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)
// What a tab says it holds — its label in the strip, a step in Back's list.
export function placeLabel(p: Place): string {
  switch (p.kind) {
    case 'library': {
      const axis = p.axis ? store.fields.find((f) => f.id === p.axis)?.label ?? p.axis : ''
      const where = [p.sel.shelf && capital(p.sel.shelf), axis].filter(Boolean).join(' · ') || 'Library'
      const n = activeFilterCount(p.sel)
      // one value picked says itself; more say how many
      const one = n === 1 ? Object.values(p.sel.include).flat()[0] : ''
      return [where, p.sel.search && `“${p.sel.search}”`, one || (n && `${n} filter${n === 1 ? '' : 's'}`)]
        .filter(Boolean).join(' · ')
    }
    case 'title': case 'reader': return titleById(p.id)?.title ?? 'Title'
    case 'page': return p.value
    case 'metadata':
      return p.ids ? `Metadata · ${p.ids.length} titles` : p.focus ? `Metadata · ${p.focus.value}` : 'Metadata'
    case 'import': return 'Import'
    case 'settings': return 'Settings'
  }
}

// A tab's view finds its place here (TabHost provides it): a view kept alive
// behind another tab keeps reading its own place, not the one in front.
export const TAB: InjectionKey<AppTab> = Symbol('tab')
export function useTab(): AppTab {
  const tab = inject(TAB)
  if (!tab) throw new Error('a tab view was drawn outside its tab')
  return tab
}
// A view kept alive behind another tab holds what it shows and stops answering
// to the library: `whenShown` runs a load now, or once as the view comes back
// (each load is owed once, however often it was asked for meanwhile).
export function useShown(): { shown: Ref<boolean>; whenShown: (fn: () => void) => void } {
  const shown = ref(true)
  const owed = new Set<() => void>()
  onActivated(() => {
    shown.value = true
    const due = [...owed]
    owed.clear()
    for (const fn of due) fn()
  })
  onDeactivated(() => { shown.value = false })
  return { shown, whenShown: (fn) => { if (shown.value) fn(); else owed.add(fn) } }
}

// the home tab: the library the window opens on — always there, never closed
function homeTab(sel: LibraryFilters): AppTab {
  return { id: HOME, place: { kind: 'library', sel, axis: '', page: 1 }, back: [], forward: [], pinned: false, hold: '',
           opener: null }
}
store.tabs.push(homeTab(store.library))
settle(store.tabs[0])
watch(() => `${store.libFocus}\n${libKey(libraryTab().place)}`, () => {
  window.clearTimeout(stepTimer)
  stepTimer = window.setTimeout(() => {
    stepTimer = undefined
    recordStep()
  }, 700)
})

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
        // a file nobody armed and the library would not keep: nothing else says so
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
    forgetTitleTabs(id)
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
      forgetTitleTabs(id)
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
  try { store.loose = (await api.looseFiles()).length } catch { /* keep the previous count */ }
}

export async function refreshTotal() {
  try { store.total = (await api.libraryCount()).total } catch { /* keep the previous total */ }
}

// ---- the vault (design/vault-home.md) ----------------------------------------
//
// Everything the app keeps lives in the vault it serves, the browser's profile
// included — so opening another vault is a restart into it, made by the shell.
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

// Opening another vault: asked first, refused while a pass writes into this one
// (the restart would cut it off mid-way), and the window's last preferences
// reach this vault before it goes.
export async function switchVault(path: string): Promise<boolean> {
  if (!window.longbox) return false
  if (vaultPass.running) {
    store.error = `${vaultPass.op || 'a vault pass'} is running — let it finish first`
    return false
  }
  const name = vaultName(path)
  const here = vaultName(vault.path || store.libraryPath)
  const held = store.tabs.find((t) => t.hold)
  const moving = runningDownloads().length
  const ok = await askConfirm({
    title: `Switch to ${name}?`, okLabel: `Restart into ${name}`, danger: !!held,
    message: `longbox restarts into ${path}. Its titles, recipes, settings and the browser's sign-ins `
      + `come with it; ${here}'s stay in ${here}.`
      + (moving ? ` ${moving} download${moving === 1 ? '' : 's'} in progress stop, to be picked up when ${here} is open again.` : '')
      + (held ? ` ${held.hold}` : ''),
  })
  if (!ok) return false
  await flushPrefs()
  return window.longbox.switchVault(path)
}

export const vaultName = (path: string): string => path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() || path

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
