// Browser-style session restore: the app continuously snapshots the open tabs
// (every app tab's place + the browser's pages); on the next launch the
// previous run's snapshot becomes a history entry. The all-tabs menu offers the
// last 3 sessions — one click reopens their tabs in the background.
import { reactive, watch } from 'vue'
import { browser, newTabBackground } from './browser'
import { isRecord, onBeforeFlush, readLocal, removeLocal, writeLocal } from './local'
import {
  editInMetadata, editValueInMetadata, HOME, newLibraryTab, openSection, openTitleBackground, openValuePage,
  store, type Place,
} from './store'

export interface SessionSnap {
  at: string // ISO — when the snapshot was last updated
  places: Place[] // the app tabs but the home tab, which is always there
  web: { url: string; title: string }[]
}

const CUR = 'lb.session.current'
const HIST = 'lb.sessions'
const MAX = 3
// A place moves with every page a reader turns and every letter a search
// takes, and the snapshot lives in the vault's settings.json — on what may be a
// network disk. Written at most this often, and with whatever else is written
// (the window going, a switch of vault).
const SNAP_EVERY = 15_000

export const sessionHistory = reactive<SessionSnap[]>([])

// validated, not merely parsed: a stale or hand-edited key must not throw
// inside app startup, which runs this synchronously
const isSnap = (v: unknown): v is SessionSnap => isRecord(v)
const isSnapList = (v: unknown): v is SessionSnap[] => Array.isArray(v) && v.every(isSnap)
const listOf = (v: unknown): unknown[] => (Array.isArray(v) ? v : [])

function readSnap(key: string): SessionSnap | null {
  return readLocal<SessionSnap | null>(key, (v): v is SessionSnap | null => v === null || isSnap(v), null)
}

export function initSessions() {
  // whatever the LAST run left open becomes history — exactly like a browser
  const prev = readSnap(CUR)
  const hist = readLocal<SessionSnap[]>(HIST, isSnapList, [])
  if (prev && (listOf(prev.places).length || listOf(prev.web).length)) hist.unshift(prev)
  sessionHistory.splice(0, sessionHistory.length, ...hist.slice(0, MAX))
  writeLocal(HIST, [...sessionHistory])
  removeLocal(CUR)

  // live snapshot of THIS session
  let due: number | undefined
  const snap = () => {
    window.clearTimeout(due)
    due = undefined
    writeLocal(CUR, {
      at: new Date().toISOString(),
      places: store.tabs.filter((t) => t.id !== HOME).map((t) => t.place),
      web: browser.tabs.filter((t) => t.url).map((t) => ({ url: t.url, title: t.title || t.label })),
    } satisfies SessionSnap)
  }
  watch(
    () => JSON.stringify([store.tabs.map((t) => t.place), browser.tabs.map((t) => t.url)]),
    () => { if (due === undefined) due = window.setTimeout(snap, SNAP_EVERY) },
  )
  onBeforeFlush(() => { if (due !== undefined) snap() })
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '')
const PROGRESS = ['all', 'unread', 'reading', 'completed'] as const
const strings = (v: unknown): string[] | null =>
  (Array.isArray(v) && v.every((x) => typeof x === 'string') ? v : null)
function bagOf(v: unknown): Record<string, string[]> {
  if (!isRecord(v)) return {}
  return Object.fromEntries(Object.entries(v).flatMap(([k, l]) => (strings(l) ? [[k, strings(l)!]] : [])))
}

// One saved place opened again behind the tab in front — or nothing, for a
// record the library no longer has or a shape this build does not know.
function reopen(raw: unknown): boolean {
  if (!isRecord(raw)) return false
  switch (raw.kind) {
    case 'title': case 'reader': {
      const id = str(raw.id)
      if (!store.cards[id]) return false
      openTitleBackground(id, 'outside')
      return true
    }
    case 'page':
      if (!str(raw.field) || !str(raw.value)) return false
      openValuePage(str(raw.field), str(raw.value), true, 'outside')
      return true
    case 'library': {
      const sel = isRecord(raw.sel) ? raw.sel : {}
      const progress = PROGRESS.find((v) => v === sel.progress) ?? 'all'
      const minRating = typeof sel.minRating === 'number' ? Math.min(5, Math.max(0, Math.round(sel.minRating))) : 0
      newLibraryTab({
        shelf: str(sel.shelf), search: str(sel.search), sort: str(sel.sort) || 'updated',
        favOnly: sel.favOnly === true, progress, minRating,
        include: bagOf(sel.include), exclude: bagOf(sel.exclude), axis: str(raw.axis),
      }, true)
      return true
    }
    case 'metadata': {
      const ids = strings(raw.ids)
      const focus = isRecord(raw.focus) ? raw.focus : null
      if (ids) editInMetadata(ids, true, 'outside')
      else if (focus && str(focus.field)) editValueInMetadata(str(focus.field), str(focus.value), true, 'outside')
      else openSection('metadata', true)
      return true
    }
    case 'import': case 'settings':
      openSection(raw.kind, true)
      return true
  }
  return false
}

// Reopen a past session's tabs IN THE BACKGROUND — the current view stays put;
// titles that no longer exist in the library are skipped silently.
export function restoreSession(index: number): number {
  const s = sessionHistory[index]
  if (!s) return 0
  let opened = 0
  for (const p of listOf(s.places)) if (reopen(p)) opened++
  for (const w of listOf(s.web)) {
    const url = isRecord(w) ? str(w.url) : ''
    if (url && !browser.tabs.some((t) => t.url === url)) {
      newTabBackground(url)
      opened++
    }
  }
  return opened
}

export function sessionLabel(s: SessionSnap): string {
  const d = new Date(s.at)
  const pad = (n: number) => String(n).padStart(2, '0')
  const when = Number.isNaN(d.getTime()) ? '' : `${pad(d.getDate())}.${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  const tabs = listOf(s.places).length
  const web = listOf(s.web).length
  const parts = []
  if (tabs) parts.push(`${tabs} tab${tabs === 1 ? '' : 's'}`)
  if (web) parts.push(`${web} web`)
  return `${when} · ${parts.join(' · ') || 'empty'}`
}
