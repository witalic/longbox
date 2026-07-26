// Browser-style session restore: the app continuously snapshots the open tabs
// (title tabs + browser tabs); on the next launch the previous run's snapshot
// becomes a history entry. The all-tabs menu offers the last 3 sessions — one
// click reopens their tabs in the background.
import { reactive, watch } from 'vue'
import { browser, newTabBackground } from './browser'
import { openTitleBackground, store } from './store'

export interface SessionSnap {
  at: string // ISO — when the snapshot was last updated
  titles: string[] // open title-tab ids
  web: { url: string; title: string }[]
}

const CUR = 'lb.session.current'
const HIST = 'lb.sessions'
const MAX = 3

export const sessionHistory = reactive<SessionSnap[]>([])

function readJson<T>(key: string): T | null {
  try { return JSON.parse(localStorage.getItem(key) || 'null') } catch { return null }
}

export function initSessions() {
  // whatever the LAST run left open becomes history — exactly like a browser
  const prev = readJson<SessionSnap>(CUR)
  const hist = readJson<SessionSnap[]>(HIST) ?? []
  if (prev && (prev.titles?.length || prev.web?.length)) hist.unshift(prev)
  sessionHistory.splice(0, sessionHistory.length, ...hist.slice(0, MAX))
  try { localStorage.setItem(HIST, JSON.stringify([...sessionHistory])) } catch { /* ignore */ }
  try { localStorage.removeItem(CUR) } catch { /* ignore */ }

  // live snapshot of THIS session
  watch(
    () => JSON.stringify([store.openTabs, browser.tabs.map((t) => t.url)]),
    () => {
      const snap: SessionSnap = {
        at: new Date().toISOString(),
        titles: [...store.openTabs],
        web: browser.tabs.filter((t) => t.url).map((t) => ({ url: t.url, title: t.title || t.label })),
      }
      try { localStorage.setItem(CUR, JSON.stringify(snap)) } catch { /* ignore */ }
    },
  )
}

// Reopen a past session's tabs IN THE BACKGROUND — the current view stays put;
// titles that no longer exist in the library are skipped silently.
export function restoreSession(index: number): number {
  const s = sessionHistory[index]
  if (!s) return 0
  let opened = 0
  for (const id of s.titles) {
    if (store.byId[id] && !store.openTabs.includes(id)) {
      void openTitleBackground(id)
      opened++
    }
  }
  for (const w of s.web) {
    if (w.url && !browser.tabs.some((t) => t.url === w.url)) {
      newTabBackground(w.url)
      opened++
    }
  }
  return opened
}

export function sessionLabel(s: SessionSnap): string {
  const d = new Date(s.at)
  const pad = (n: number) => String(n).padStart(2, '0')
  const when = Number.isNaN(d.getTime()) ? '' : `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  const parts = []
  if (s.titles.length) parts.push(`${s.titles.length} title${s.titles.length === 1 ? '' : 's'}`)
  if (s.web.length) parts.push(`${s.web.length} web`)
  return `${when} · ${parts.join(' · ') || 'empty'}`
}
