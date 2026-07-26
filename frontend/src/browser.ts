// Browser tabs — pure browsing state (design/state-model.md §2). A tab is a
// page, its history and zoom. It carries NO capture state and no draft binding:
// the draft is independent of navigation, and the browser is just "where I can
// capture from right now".
import { reactive } from 'vue'
import { confirmDiscard, draftFromTitle, draftState, newDraft } from './draft'
import { store, titleById } from './store'

export interface BrowserTab {
  id: string
  initialUrl: string // src the <webview> mounts with (later navigation is via the element)
  url: string        // current live URL — drives the address bar
  label: string      // hostname — the short fallback name
  title: string      // live page title (drives the tab caption + target matching)
  pinned: boolean    // pinned tabs collapse to icon-only and stick to the front
  zoom: number       // per-tab zoom factor
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
  return { id: `bt-${++tabSeq}`, initialUrl: url, url, label: labelFor(url), title: '', pinned: false, zoom: 1 }
}

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

export function newTab(url?: string) {
  const t = makeTab(url || store.browseHomepage)
  browser.tabs.push(t)
  browser.activeId = t.id
  store.view = 'browser'
}

// Add a tab WITHOUT activating it or leaving the current view (session restore).
export function newTabBackground(url: string) {
  const t = makeTab(url)
  browser.tabs.push(t)
  if (!browser.activeId) browser.activeId = t.id
}

export function activateTab(id: string) {
  browser.activeId = id
  store.view = 'browser'
}

export function closeTab(id: string) {
  const i = browser.tabs.findIndex((t) => t.id === id)
  if (i < 0) return
  browser.tabs.splice(i, 1)
  if (browser.activeId === id) {
    const next = browser.tabs[i] ?? browser.tabs[i - 1] ?? null
    browser.activeId = next?.id ?? null
    if (!next) store.view = store.activeTitle ? 'title' : 'library'
  }
}

// Plain "Browse" nav: show the tabs, or open a fresh one if none.
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

// "Add title": a FRESH draft opened straight in the browser's capture panel —
// there is no separate in-app edit screen. Reuses the active tab when one is
// open; otherwise opens one at the homepage.
export async function startNewTitle(url?: string) {
  if (!(await confirmDiscard())) return
  newDraft()
  browser.panelOpen = true
  if (url || !browser.tabs.length) newTab(url)
  else store.view = 'browser'
}
