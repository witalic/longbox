// Chrome's tab strip, for both of the app's strips — its own tabs (store.ts) and
// the browser's (browser.ts). design/tab-strip-mockup.html shows it working.
//
// - Where a new tab goes. One opened from a page in the background goes right
//   after that page, after the tabs it opened before (its run), so they stay in
//   order; one opened and switched to goes right after its page and starts a new
//   run; one opened from anywhere else (+, the rail, a bookmark, a restored
//   session) goes at the end — and, switched to, remembers the tab it was
//   opened over. An address typed into a tab ends every run.
// - Which tab comes to the front when the one in front closes: the first tab it
//   opened; else the next tab its own page opened (right, then left); else that
//   page; else its right neighbour (left at the end).
// - How wide the tabs are: they share the strip, from the usual width down to an
//   icon, before the strip scrolls. A tab closed with the pointer leaves the rest
//   their width while the pointer stays on the strip (App.vue).

export interface StripTab {
  id: string
  pinned: boolean
  opener: string | null // the tab this one was opened from, while the run lasts
}

// 'link' — from the tab in front, behind it; 'front' — from it, and switched to;
// 'end' — from outside the page.
export type Opening = 'link' | 'front' | 'end'

// The strip's own order: the tabs that stay at the front (pinned; the app's
// library tab) first, the rest as they are kept.
export function inStripOrder<T extends StripTab>(tabs: readonly T[], keptFirst: (t: T) => boolean = (t) => t.pinned): T[] {
  return [...tabs.filter(keptFirst), ...tabs.filter((t) => !keptFirst(t))]
}

/** Where a new tab goes in `shown` (the strip's order): the index it takes.
 *  `front` is how many tabs stay at the front — a new tab never goes among them. */
export function insertionIndex(shown: readonly StripTab[], fromId: string | null, how: Opening, front: number): number {
  const i = fromId ? shown.findIndex((t) => t.id === fromId) : -1
  if (how === 'end' || i < 0) return shown.length
  let last = i
  if (how === 'link') {
    // the run: what this page opened, and what those opened, right after it
    const run = new Set([shown[i].id])
    for (let k = i + 1; k < shown.length; k++) {
      if (k < front) continue
      if (!run.has(shown[k].opener ?? '')) break
      run.add(shown[k].id)
      last = k
    }
  }
  return Math.max(last + 1, front)
}

// A tab opened and switched to starts a new run; a typed address ends them all.
export function forgetOpeners(tabs: StripTab[]): void {
  for (const t of tabs) t.opener = null
}

/** The tab that comes to the front when `shown[i]`, the one in front, closes. */
export function nextAfterClose(shown: readonly StripTab[], i: number): string | null {
  const gone = shown[i]
  const near = (opener: string): string | null => {
    for (let k = i + 1; k < shown.length; k++) if (shown[k].opener === opener) return shown[k].id
    for (let k = i - 1; k >= 0; k--) if (shown[k].opener === opener) return shown[k].id
    return null
  }
  const opener = gone.opener && shown.some((t) => t.id === gone.opener) ? gone.opener : null
  return near(gone.id) ?? (opener ? near(opener) ?? opener : null) ?? (shown[i + 1] ?? shown[i - 1])?.id ?? null
}

// What a closed tab opened belongs to the page that opened it.
export function adoptChildren(tabs: StripTab[], gone: StripTab): void {
  for (const t of tabs) if (t.opener === gone.id) t.opener = gone.opener
}

// ---- widths ----
export const TAB_MAX = 190      // the usual width
export const TAB_MIN = 36       // an icon
export const TAB_MIN_FRONT = 62 // the tab in front keeps its ×
export const TAB_NARROW = 110   // narrower: only the tab in front keeps its buttons
export const TAB_TINY = 56      // narrower: an icon, no label
export const TAB_GAP = 3

/** How wide a shared tab is (`w`) and the one in front (`front`), for `count`
 *  tabs sharing `room` pixels — or the width a close left them (`held`). */
export function tabWidths(room: number, count: number, held: number | null): { w: number; front: number } {
  if (held !== null) return { w: held, front: Math.max(held, TAB_MIN_FRONT) }
  const w = Math.min(TAB_MAX, room / Math.max(1, count))
  if (w >= TAB_MIN_FRONT || count <= 1) return { w, front: Math.max(w, Math.min(TAB_MIN_FRONT, room)) }
  return { w: Math.max(TAB_MIN, (room - TAB_MIN_FRONT) / (count - 1)), front: TAB_MIN_FRONT }
}
