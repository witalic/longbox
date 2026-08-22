// The app store: navigation, library data + linked faceted filters, and the
// user layer. User-layer changes (star, rating, read state) are instant
// write-through — they never go through a draft (design/state-model.md §11).
// The draft itself lives in draft.ts; browser tabs live in browser.ts.
import { reactive, watch, watchEffect } from 'vue'
import { api, type LibraryQuery } from './api'
import { chapterRowsOf, emptyFacets, metaOf, sameChapter, type Author, type Chapter, type Facets, type ReadState, type Source, type Title } from './data'
import { readLocalOne, writeLocalOne } from './local'
import { stopCaptureFor } from './pagecapture'

export type View = 'library' | 'title' | 'reader' | 'authors' | 'sources' | 'settings' | 'browser'
export type Density = 'grid' | 'dense' | 'expanded'
export type ThemePref = 'dark' | 'light' | 'system'

// Facet keys with include/exclude selections (linked filtering).
export type FacetKey = 'types' | 'statuses' | 'genres' | 'tags' | 'languages' | 'flags' | 'authors' | 'characters'
export const FACET_KEYS: FacetKey[] = ['types', 'statuses', 'genres', 'tags', 'languages', 'flags', 'authors', 'characters']

interface LibraryFilters {
  density: Density
  search: string
  sort: string // updated | title | rating | unread
  favOnly: boolean
  progress: 'all' | 'unread' | 'reading' | 'completed' // READING progress, not manga status
  minRating: number // 0 = any
  include: Record<FacetKey, string[]>
  exclude: Record<FacetKey, string[]>
}

function blankSelection(): Record<FacetKey, string[]> {
  return { types: [], statuses: [], genres: [], tags: [], languages: [], flags: [], authors: [], characters: [] }
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
  authors: Author[]
  sources: Source[]
  facets: Facets // linked counts for the CURRENT selection
  globalFacets: Facets // unfiltered counts — the STABLE row order for the sidebar
  vocab: { types: string[]; statuses: string[]; genres: string[]; tags: string[]; authors: string[]; characters: string[] }
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
  authors: [],
  sources: [],
  facets: emptyFacets(),
  globalFacets: emptyFacets(),
  vocab: { types: [], statuses: [], genres: [], tags: [], authors: [], characters: [] },
  byId: {},
  loading: false,
  error: null,
  library: {
    density: 'grid', search: '', sort: 'updated', favOnly: false, progress: 'all',
    minRating: 0, include: blankSelection(), exclude: blankSelection(),
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

// ---- data loading ----
export function cache(list: Title[]) {
  for (const t of list) {
    const existing = store.byId[t.id]
    if (existing) Object.assign(existing, t) // keep object identity for open views
    else store.byId[t.id] = t
  }
}

function buildQuery(): LibraryQuery {
  const f = store.library
  return {
    search: f.search || undefined,
    progress: f.progress === 'all' ? undefined : f.progress,
    fav: f.favOnly || undefined,
    min_rating: f.minRating || undefined,
    sort: f.sort,
    types: f.include.types, types_not: f.exclude.types,
    statuses: f.include.statuses, statuses_not: f.exclude.statuses,
    genres: f.include.genres, genres_not: f.exclude.genres,
    tags: f.include.tags, tags_not: f.exclude.tags,
    languages: f.include.languages, languages_not: f.exclude.languages,
    flags: f.include.flags, flags_not: f.exclude.flags,
    authors: f.include.authors, authors_not: f.exclude.authors,
    characters: f.include.characters, characters_not: f.exclude.characters,
  }
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
  store.vocab = {
    types: full.types.map((x) => x.v),
    statuses: full.statuses.map((x) => x.v),
    genres: full.genres.map((x) => x.v),
    tags: full.tags.map((x) => x.v),
    authors: full.authors.map((x) => x.v),
    characters: full.characters.map((x) => x.v),
  }
}
async function refreshVocab() {
  try { applyVocab(await api.facets()) } catch { /* keep the previous vocab */ }
}

// Authors, sources and the vocab are derived from the titles — refetch after
// any commit / delete or the Authors tab and suggestions go stale.
export async function refreshDerived() {
  try {
    const [authors, sources] = await Promise.all([api.authors(), api.sources()])
    store.authors = authors
    store.sources = sources
  } catch { /* leave the previous values in place */ }
  await refreshVocab()
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
    const [all, facets, settings, sync] = await Promise.all(
      [api.library(), api.facets(), api.settings(), api.libraryStatus()])
    Object.assign(opening, { active: sync.running, path: sync.path, done: sync.done, total: sync.total })
    cache(all)
    store.total = all.length
    store.titles = all.map((t) => store.byId[t.id])
    // the unfiltered facets ARE the full vocabulary — asking twice is one
    // whole-library scan too many
    store.facets = facets
    applyVocab(facets)
    if (settings.homepage) store.browseHomepage = settings.homepage
    store.appMeta = settings.app || {}
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  } finally {
    store.loading = false
  }
  // the Authors tab and the source list are not on the way to the grid
  void refreshDerived()
  // …and the vault check runs behind the window, refreshing it if disk moved on
  watchLibrarySync()
  // refetch results whenever a server-side filter changes (density is client-only)
  watch(() => JSON.stringify(buildQuery()), reloadLibrary)
}

export function resetFilters() {
  Object.assign(store.library, {
    search: '', favOnly: false, progress: 'all', minRating: 0,
    include: blankSelection(), exclude: blankSelection(),
  })
}

// ---- faceted selection: off → include → exclude → off ----
export function facetState(key: FacetKey, value: string): 'in' | 'out' | '' {
  if (store.library.include[key].includes(value)) return 'in'
  if (store.library.exclude[key].includes(value)) return 'out'
  return ''
}
export function toggleFacet(key: FacetKey, value: string) {
  const inc = store.library.include[key]
  const exc = store.library.exclude[key]
  const i = inc.indexOf(value)
  const e = exc.indexOf(value)
  if (i >= 0) { inc.splice(i, 1); exc.push(value) }
  else if (e >= 0) { exc.splice(e, 1) }
  else inc.push(value)
}
export function anyFilterActive(): boolean {
  const f = store.library
  return !!f.search || f.favOnly || f.progress !== 'all' || f.minRating !== 0
    || FACET_KEYS.some((k) => f.include[k].length || f.exclude[k].length)
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
export function langSuggestions(chapters: { lang: string }[] = []): string[] {
  return [...new Set([
    ...chapters.map((c) => c.lang),
    ...store.globalFacets.languages.map((x) => x.v),
  ])].filter(Boolean)
}
export function groupSuggestions(chapters: { group: string }[] = []): string[] {
  return [...new Set([
    ...chapters.map((c) => c.group),
    ...Object.values(store.byId).flatMap((x) => x.chapters.map((c) => c.group)),
  ])].filter(Boolean)
}

// Clickable metadata chips → jump to the library filtered by that value.
export function filterBy(kind: 'genre' | 'tag' | 'type' | 'status' | 'character', value: string) {
  resetFilters()
  const key: FacetKey = kind === 'genre' ? 'genres' : kind === 'tag' ? 'tags'
    : kind === 'type' ? 'types' : kind === 'character' ? 'characters' : 'statuses'
  store.library.include[key] = [value]
  store.view = 'library'
}
// Jump to the library filtered to ONE person via the dedicated AUTHOR facet.
export function filterByPerson(name: string) {
  resetFilters()
  store.library.include.authors = [name]
  store.view = 'library'
}

// One-shot handoff: jump to the Authors tab focused on one person — the view
// picks the name up on mount and puts it into its own search box.
let pendingAuthorFocus = ''
export function goAuthorsFor(name: string) {
  pendingAuthorFocus = name
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

export async function refreshTitle(id: string): Promise<void> {
  try { cache([await api.title(id)]) } catch { /* keep the cached copy */ }
}

// ---- user-layer write-through (optimistic; instant, no draft, no confirm) ----
export async function setFavorite(t: Title, value: boolean) {
  t.fav = value
  try { cache([await api.patchUser(t.id, { fav: value })]) } catch (e) { store.error = String(e) }
}
export async function toggleFav(t: Title) { await setFavorite(t, !t.fav) }
export async function setRating(t: Title, value: number) {
  // clicking the current rating again clears it — every star widget toggles
  const v = value === t.rating ? 0 : value
  t.rating = v
  try { cache([await api.patchUser(t.id, { rating: v })]) } catch (e) { store.error = String(e) }
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
  if (chapter) chapter.position = seconds
  try {
    await api.patchUser(t.id, { position: { [chapterId]: seconds } })
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}

export async function setRead(t: Title, chapterId: string, state: ReadState) {
  const c = t.chapters.find((x) => x.id === chapterId)
  if (c) c.read = state
  try { cache([await api.patchUser(t.id, { read: { [chapterId]: state } })]) } catch (e) { store.error = String(e) }
}

// A commit body built from a title's CURRENT state — for targeted meta-layer
// edits (source unlink, chapter reorder) outside the draft flow.
function commitBodyFrom(t: Title) {
  return {
    meta: metaOf(t),
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
  fields: { num: string; lang: string; group: string; url: string }): Promise<boolean> {
  try {
    const body = commitBodyFrom(t)
    const row = body.chapters.find((c) => c.id === id)
    if (!row) return false
    row.num = fields.num
    row.title = '' // single-label model: the label carries the whole name
    row.lang = fields.lang
    row.group = fields.group
    row.url = fields.url
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
  t: Title, ch: { num: string; lang: string; group: string; url?: string }): Promise<Chapter | null> {
  if (t.chapters.some((c) => sameChapter(c, ch))) {
    store.error = `Chapter ${ch.num}${ch.lang ? ' · ' + ch.lang : ''} already exists`
    return null
  }
  try {
    const body = commitBodyFrom(t)
    body.chapters.push({ id: '', num: ch.num, title: '', url: ch.url || '', lang: ch.lang, group: ch.group, date: '' })
    const saved = await api.commitTitle(t.id, body)
    cache([saved])
    return saved.chapters.find((c) => sameChapter(c, ch)) ?? null
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
  try {
    const s = await api.setLibraryPath(path)
    // the backend swapped to the new location — reload everything
    stopCaptureFor() // the armed entry belongs to the library we are leaving
    store.byId = {}
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
