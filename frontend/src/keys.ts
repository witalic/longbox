// Central keyboard map — ONE source of truth for every app shortcut. Views ask
// `matches(action, event)` instead of hardcoding keys; Settings → Keyboard
// rebinds actions (persisted) and resets to the defaults below.
import { reactive } from 'vue'
import { isRecord, readLocal } from './local'

export interface KeyAction {
  id: string
  label: string
  def: string[] // default bindings — PHYSICAL keys (KeyboardEvent.code), so
                // shortcuts keep working in any keyboard layout
}

export const KEY_ACTIONS: KeyAction[] = [
  { id: 'reader.next', label: 'Next page / scroll down', def: ['ArrowRight', 'Space'] },
  { id: 'reader.prev', label: 'Previous page / scroll up', def: ['ArrowLeft'] },
  { id: 'reader.chapterNext', label: 'Next chapter', def: ['BracketRight'] },
  { id: 'reader.chapterPrev', label: 'Previous chapter', def: ['BracketLeft'] },
  { id: 'reader.fit', label: 'Cycle fit mode', def: ['KeyF'] },
  { id: 'reader.sidebar', label: 'Toggle the sidebar', def: ['KeyS'] },
  { id: 'reader.marginsDec', label: 'Narrower margins', def: ['Comma'] },
  { id: 'reader.marginsInc', label: 'Wider margins', def: ['Period'] },
  { id: 'reader.back', label: 'Back to the title page', def: ['Escape'] },
  // An episode has no pages to turn, so the same keys scrub it instead — in the
  // reader and on the title page alike, wherever a player is running.
  { id: 'video.back', label: 'Video: back 5 seconds', def: ['ArrowLeft'] },
  { id: 'video.fwd', label: 'Video: forward 5 seconds', def: ['ArrowRight'] },
]

const STORE_KEY = 'lb.keys'

// An earlier build stored e.key CHARACTERS (layout-dependent); lift what we can
// onto physical codes and drop the rest.
function toCode(v: string): string | null {
  if (/^[a-z]$/i.test(v)) return `Key${v.toUpperCase()}`
  if (/^[0-9]$/.test(v)) return `Digit${v}`
  const MAP: Record<string, string> = {
    ' ': 'Space', ',': 'Comma', '.': 'Period', '[': 'BracketLeft', ']': 'BracketRight',
    ';': 'Semicolon', "'": 'Quote', '/': 'Slash', '\\': 'Backslash', '-': 'Minus', '=': 'Equal', '`': 'Backquote',
  }
  if (MAP[v]) return MAP[v]
  // specials (ArrowLeft, Escape, Enter, Tab, F1…) are already valid codes
  return /^[A-Z][A-Za-z0-9]+$/.test(v) ? v : null
}

function loadOverrides(): Record<string, string[]> {
  const raw = readLocal(STORE_KEY, isRecord, {} as Record<string, unknown>)
  {
    const out: Record<string, string[]> = {}
    for (const [k, v] of Object.entries(raw)) {
      if (!Array.isArray(v)) continue
      const codes = v.filter((x): x is string => typeof x === 'string').map(toCode).filter((x): x is string => !!x)
      if (codes.length) out[k] = codes
    }
    return out
  }
}

// reactive so the Settings UI and hotkey handlers update live
export const keyOverrides = reactive<Record<string, string[]>>(loadOverrides())

function persist() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(keyOverrides)) } catch { /* ignore */ }
}

export function bindingsFor(id: string): string[] {
  return keyOverrides[id] ?? KEY_ACTIONS.find((a) => a.id === id)?.def ?? []
}

export function isOverridden(id: string): boolean {
  return id in keyOverrides
}

export function rebind(id: string, keys: string[]) {
  keyOverrides[id] = keys
  persist()
}

export function resetKey(id: string) {
  delete keyOverrides[id]
  persist()
}

export function resetAllKeys() {
  for (const k of Object.keys(keyOverrides)) delete keyOverrides[k]
  persist()
}

// Matching is by PHYSICAL key (e.code) — layout switches never break bindings.
export function matches(id: string, e: KeyboardEvent): boolean {
  if (e.ctrlKey || e.altKey || e.metaKey) return false // plain keys only, for now
  return bindingsFor(id).includes(e.code)
}

const LABELS: Record<string, string> = {
  Space: 'Space', ArrowRight: '→', ArrowLeft: '←', ArrowUp: '↑', ArrowDown: '↓',
  Escape: 'Esc', BracketLeft: '[', BracketRight: ']', Comma: ',', Period: '.',
  Semicolon: ';', Quote: "'", Slash: '/', Backslash: '\\', Minus: '-', Equal: '=', Backquote: '`',
  Enter: 'Enter', Tab: 'Tab', Backspace: 'Bksp', Delete: 'Del',
}
export function keyLabel(code: string): string {
  if (code.startsWith('Key')) return code.slice(3)
  if (code.startsWith('Digit')) return code.slice(5)
  if (code.startsWith('Numpad')) return `Num ${code.slice(6)}`
  return LABELS[code] ?? code
}
