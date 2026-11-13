// Navigation (design/navigation.md): the places the app shows and the app tabs
// that hold them — opening, closing, pinning, ordering, Back and Forward, the
// tab a page was opened from. It moved here from store.ts, which it reads and
// writes (`store.tabs`, `store.activeTab`, `store.view`); store.ts knows only
// the AppTab type, so this module is the one that depends on the other.
import { inject, onActivated, onDeactivated, ref, watch, type InjectionKey, type Ref } from 'vue'
import { api } from './api'
import { askConfirm } from './confirm'
import {
  DRAFT, HOME, activeFilterCount, blankLibrary, cardById, forgetTitle, loadTitle, refreshLibrary, refreshSources,
  reloadIfChanged, store, type LibraryFilters, type View,
} from './store'
import { adoptChildren, forgetOpeners, inStripOrder, insertionIndex, nextAfterClose, type Opening } from './tabstrip'

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
// selection and page
export interface PagePlace {
  kind: 'page'; field: string; value: string; sel: LibraryFilters; page: number
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
export function showTab(id: string) {
  const tab = tabById(id)
  if (!tab) return
  // coming back to a library tab whose selection is already the focus: nothing
  // else will ask again, and the vault can change underneath (a pass, a sync)
  const again = tab.place.kind === 'library' && store.libFocus === tab.id
    && (store.activeTab !== tab.id || store.view === 'browser' || store.view === 'sources')
  project(tab)
  const p = tab.place
  // a title shown again may have moved on meanwhile (a run, a pass, a sync)
  if (p.kind === 'title' || p.kind === 'reader') void loadTitle(p.id).then(() => reloadIfChanged(false))
  else if (p.kind === 'library' && p.axis) void reloadIfChanged(false)
  if (again) void reloadIfChanged(true)
}
// Back from the web to the app tab that was in front.
export function showApp() {
  if (store.activeTab === DRAFT) showDraft()
  else showTab(tabById(store.activeTab) ? store.activeTab : HOME)
}
export function showSources() {
  store.view = 'sources'
  void refreshSources()
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
  void loadTitle(id)
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
// one tab per value, however it is cased
function pageKey(field: string, value: string): string {
  return `${field}\n${value.trim().toLowerCase()}`
}
// a page opens on all its works: another page's filters are that page's own
function pagePlace(field: string, value: string): PagePlace {
  return { kind: 'page', field, value, sel: blankLibrary(), page: 1 }
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
    case 'title': case 'reader': return cardById(p.id)?.title ?? 'Title'
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
