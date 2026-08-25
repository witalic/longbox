// ONE way to touch localStorage. Every read is validated against what the app
// expects: a value written by an older build (or a corrupt one) must fall back
// to the default, never reach a view and crash it. Storage can also be denied
// outright, so writes never throw.

export function readLocal<T>(key: string, valid: (v: unknown) => v is T, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    if (raw === null) return fallback
    const parsed: unknown = JSON.parse(raw)
    return valid(parsed) ? parsed : fallback
  } catch {
    return fallback
  }
}

// Plain string preferences (theme, density, a mode flag) are stored AS-IS, not
// as JSON — they read the same in devtools and never need parsing.
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
  try {
    const raw = localStorage.getItem(key)
    return raw !== null && (allowed as readonly string[]).includes(raw) ? raw as T : fallback
  } catch {
    return fallback
  }
}

export function writeLocalOne(key: string, value: string): void {
  try { localStorage.setItem(key, value) } catch { /* storage unavailable */ }
}

export function writeLocal(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch { /* storage unavailable or full — a preference is not worth throwing */ }
}

export function removeLocal(key: string): void {
  try { localStorage.removeItem(key) } catch { /* ignore */ }
}

// The two shapes that cover most preferences.
export const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v)

export function oneOf<T extends string>(...allowed: T[]) {
  return (v: unknown): v is T => typeof v === 'string' && (allowed as string[]).includes(v)
}
