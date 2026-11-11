// Browser tabs — pure browsing state (design/state-model.md §2). A tab is a
// page, its history and zoom. It carries NO capture state and no draft binding:
// the draft is independent of navigation, and the browser is just "where I can
// capture from right now".
import { reactive } from 'vue'
import { confirmDiscard, draftFromTitle, draftState, newDraft } from './draft'
import { showApp, showDraft, store, titleById } from './store'
import { adoptChildren, forgetOpeners, inStripOrder, insertionIndex, nextAfterClose, type Opening } from './tabstrip'

export interface BrowserTab {
  id: string
  initialUrl: string // src the <webview> mounts with (later navigation is via the element)
  url: string        // current live URL — drives the address bar
  label: string      // hostname — the short fallback name
  title: string      // live page title (drives the tab caption + target matching)
  pinned: boolean    // pinned tabs collapse to icon-only and stick to the front
  zoom: number       // per-tab zoom factor
  loading: boolean   // a page request is in flight — the tab shows a spinner
  audible: boolean   // something is playing in it — the strip says which tab
  muted: boolean     // …and the same mark is the switch that silences it
  opener: string | null // the tab it was opened from, while the run lasts (tabstrip.ts)
}

export const browser = reactive<{
  tabs: BrowserTab[]
  activeId: string | null
  panelOpen: boolean // the capture panel dock
}>({ tabs: [], activeId: null, panelOpen: true })

let tabSeq = 0

function labelFor(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return 'new tab' }
}

function makeTab(url: string): BrowserTab {
  return { id: `bt-${++tabSeq}`, initialUrl: url, url, label: labelFor(url), title: '',
           pinned: false, zoom: 1, loading: false, audible: false, muted: false, opener: null }
}

// A new tab into the strip where Chrome would put it (tabstrip.ts), opened
// from the tab `from`.
function place(t: BrowserTab, how: Opening, from: string | null = null) {
  if (how === 'front') forgetOpeners(browser.tabs)
  t.opener = from
  const shown = inStripOrder(browser.tabs)
  const before = shown[insertionIndex(shown, from, how, browser.tabs.filter((x) => x.pinned).length)]
  browser.tabs.splice(before ? browser.tabs.indexOf(before) : browser.tabs.length, 0, t)
}

// The last few closed tabs, newest first — Ctrl+Shift+T walks back through
// them. Only their address is kept: a restored tab is a fresh page at the URL
// that was open, never a resurrected process.
const closed: string[] = []
const CLOSED_KEPT = 12

export function activeTab(): BrowserTab | undefined {
  return browser.tabs.find((t) => t.id === browser.activeId)
}

export function tabById(id: string | null): BrowserTab | undefined {
  return browser.tabs.find((t) => t.id === id)
}

export function onTabNavigated(id: string, url: string) {
  const t = tabById(id)
  if (t) { t.url = url; t.label = labelFor(url) }
}

export function setTabTitle(id: string, title: string) {
  const t = tabById(id)
  if (t) t.title = title
}

export function setTabLoading(id: string, value: boolean) {
  const t = tabById(id)
  if (t) t.loading = value
}

export function setTabAudible(id: string, value: boolean) {
  const t = tabById(id)
  if (t) t.audible = value
}

export function toggleTabMute(id: string): boolean {
  const t = tabById(id)
  if (!t) return false
  t.muted = !t.muted
  return t.muted
}

export function toggleTabPin(id: string) {
  const t = tabById(id)
  if (t) t.pinned = !t.pinned
}

// Reorder (drag & drop in the all-tabs menu): put `dragId` before `targetId`.
export function moveBrowserTabBefore(dragId: string, targetId: string) {
  if (dragId === targetId) return
  const from = browser.tabs.findIndex((t) => t.id === dragId)
  if (from < 0) return
  const [tab] = browser.tabs.splice(from, 1)
  const to = browser.tabs.findIndex((t) => t.id === targetId)
  if (to < 0) browser.tabs.push(tab)
  else browser.tabs.splice(to, 0, tab)
}

// What the address bar does with words that are not an address — and what a
// value page's "Look it up" opens.
export function searchUrl(q: string): string {
  return 'https://www.google.com/search?q=' + encodeURIComponent(q)
}

// A tab opened from outside a page — +, a bookmark, the app's own links: at the
// end. Opened while browsing, it remembers the tab it was opened over, so
// closing it goes back there, as Chrome's new tab does.
export function newTab(url?: string) {
  const t = makeTab(url || store.browseHomepage)
  place(t, 'end', store.view === 'browser' ? browser.activeId : null)
  browser.activeId = t.id
  store.view = 'browser'
}

// A link the page in tab `from` opened as a tab: behind it, after what it
// opened before — or, switched to, right after it.
export function openFromPage(url: string, background: boolean, from: string) {
  const t = makeTab(url)
  place(t, background ? 'link' : 'front', from)
  if (!background) browser.activeId = t.id
}

// An address typed into a tab: the runs end, as Chrome's do.
export function typedAddress() {
  forgetOpeners(browser.tabs)
}

// Add a tab WITHOUT activating it or leaving the current view (session restore).
export function newTabBackground(url: string) {
  const t = makeTab(url)
  place(t, 'end')
  if (!browser.activeId) browser.activeId = t.id
}

export function activateTab(id: string) {
  browser.activeId = id
  store.view = 'browser'
}

export function closeTab(id: string) {
  const i = browser.tabs.findIndex((t) => t.id === id)
  if (i < 0) return
  const shown = inStripOrder(browser.tabs)
  const next = browser.activeId === id ? nextAfterClose(shown, shown.findIndex((t) => t.id === id)) : null
  const [gone] = browser.tabs.splice(i, 1)
  adoptChildren(browser.tabs, gone)
  if (gone.url) {
    closed.unshift(gone.url)
    closed.length = Math.min(closed.length, CLOSED_KEPT)
  }
  if (browser.activeId === id) {
    browser.activeId = next
    if (!next) showApp()
  }
}

/** Reopen the most recently closed tab, and front it. */
export function reopenClosedTab(): boolean {
  const url = closed.shift()
  if (!url) return false
  newTab(url)
  return true
}

/** The tab at a 1-based position; 9 means the LAST one, as in every browser. */
export function activateTabAt(n: number) {
  const list = [...browser.tabs.filter((t) => t.pinned), ...browser.tabs.filter((t) => !t.pinned)]
  const tab = n >= 9 ? list[list.length - 1] : list[n - 1]
  if (tab) activateTab(tab.id)
}

// The rail's Web: show the tabs, or open a fresh one if none.
export function openBrowse() {
  if (browser.tabs.length) store.view = 'browser'
  else newTab()
}

// Capture INTO an existing title: seed the draft from it (unless the draft
// already targets it) and open a tab at its source page. The tab and the draft
// stay independent — closing one never touches the other.
export function openInBrowser(titleId: string, url?: string) {
  const t = titleById(titleId)
  if (!t) return
  if (draftState.cur?.targetId !== titleId) draftFromTitle(t)
  browser.panelOpen = true
  const target = url || t.source.url || store.browseHomepage
  const existing = browser.tabs.find((x) => x.url === target)
  if (existing) activateTab(existing.id)
  else newTab(target)
}

// "Add title": a FRESH draft opened on ITS OWN PAGE. The record is what the
// user asked for, not a web page — the browser opens only when they ask for it
// with "Capture from web". Pass a url to start from a page instead (the capture
// entry point): then the draft opens in the browser with that tab loaded.
export async function startNewTitle(url?: string) {
  if (!(await confirmDiscard())) return
  newDraft()
  if (url) {
    browser.panelOpen = true
    newTab(url)
    return
  }
  showDraft() // no record yet: the page renders the draft alone
}
