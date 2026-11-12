// ONE way to the window's preferences. They live in the vault (settings.json,
// design/vault-home.md), not in this browser: a vault opened on another
// computer brings its theme, keys and reader settings along, and two vaults
// keep two sets. The window loads them once before it draws anything
// (`loadPrefs`), reads them from memory, and writes a change back in the
// background.
//
// Every read is still validated against what the app expects: a value written
// by an older build (or a corrupt one) must fall back to the default, never
// reach a view and crash it. And a write never throws: a preference is not
// worth an error, and a vault that does not answer gets it on the next write.
import { api } from './api'

const prefs = new Map<string, string>()
const pending = new Map<string, string | null>() // null: drop the key
let flushTimer: number | undefined
let writing: Promise<void> = Promise.resolve() // one write at a time: they land in order

export async function loadPrefs(): Promise<void> {
  try {
    for (const [k, v] of Object.entries((await api.prefs()).prefs)) prefs.set(k, v)
  } catch { /* defaults until the vault answers */ }
}

// What holds a change back to write it less often (the session snapshot) puts
// it in here: whatever flushes — the window going, a switch of vault — takes it
// along in the same write.
const beforeFlush: (() => void)[] = []
export function onBeforeFlush(fn: () => void): void {
  beforeFlush.push(fn)
}

// Write what changed. Called on its own a moment after a change, and by
// whatever is about to restart the app (switching vaults). A write waits for
// the one before it: two in flight could land in either order, and the older
// value would win.
export function flushPrefs(keepalive = false): Promise<void> {
  for (const fn of beforeFlush) fn()
  window.clearTimeout(flushTimer)
  flushTimer = undefined
  writing = writing.then(async () => {
    if (!pending.size) return
    const batch = new Map(pending)
    pending.clear()
    const put: Record<string, string> = {}
    const drop: string[] = []
    for (const [k, v] of batch) {
      if (v === null) drop.push(k)
      else put[k] = v
    }
    try {
      await api.changePrefs({ put, drop }, keepalive)
    } catch {
      // the vault did not take them: kept for the next write, unless a newer value came meanwhile
      for (const [k, v] of batch) if (!pending.has(k)) pending.set(k, v)
    }
  })
  return writing
}

function write(key: string, value: string | null): void {
  if (value === null) prefs.delete(key)
  else prefs.set(key, value)
  pending.set(key, value)
  window.clearTimeout(flushTimer)
  flushTimer = window.setTimeout(() => void flushPrefs(), 300)
}

// the window closing takes the last change with it
window.addEventListener('pagehide', () => void flushPrefs(true))

export function readLocal<T>(key: string, valid: (v: unknown) => v is T, fallback: T): T {
  const raw = prefs.get(key)
  if (raw === undefined) return fallback
  try {
    const parsed: unknown = JSON.parse(raw)
    return valid(parsed) ? parsed : fallback
  } catch {
    return fallback
  }
}

// Plain string preferences (theme, density, a mode flag) are stored AS-IS, not
// as JSON — they read the same in settings.json and never need parsing.
// A remembered set of open/closed toggles. Values that are not booleans come
// from an older build (or a hand-edited store) and are dropped, not trusted.
export function isBoolMap(v: unknown): v is Record<string, boolean> {
  return isRecord(v) && Object.values(v).every((x) => typeof x === 'boolean')
}

// Remembered sizes (panel group heights). A non-number came from an older build
// or a hand-edited store: drop it rather than let a NaN reach a style binding.
export function isNumMap(v: unknown): v is Record<string, number> {
  return isRecord(v) && Object.values(v).every((x) => typeof x === 'number' && Number.isFinite(x))
}

export function readLocalOne<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  const raw = prefs.get(key)
  return raw !== undefined && (allowed as readonly string[]).includes(raw) ? raw as T : fallback
}

export function writeLocalOne(key: string, value: string): void {
  write(key, value)
}

export function writeLocal(key: string, value: unknown): void {
  write(key, JSON.stringify(value))
}

export function removeLocal(key: string): void {
  write(key, null)
}

// Every stored key under a prefix — for moving what an older build kept here
// to where it belongs now.
export function localKeys(prefix: string): string[] {
  return [...prefs.keys()].filter((k) => k.startsWith(prefix))
}

// The shape that covers most preferences.
export const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)
