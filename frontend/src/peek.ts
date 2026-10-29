// The PEEK (design/value-pages.md §6): hovering a value's chip for a beat shows
// a small card of its page — what is written, how many titles, a few covers —
// and the way in. A click on the chip keeps doing what it always did.
import { reactive, watch } from 'vue'
import { api } from './api'
import type { ValuePeek } from './data'
import { store } from './store'

const WIDTH = 320
const DELAY = 450  // long enough that moving the pointer across chips shows nothing
const LINGER = 180 // time to cross from the chip onto the card

export const peek = reactive({
  open: false,
  x: 0,
  y: 0,
  above: false,
  data: null as ValuePeek | null,
})

let showTimer = 0
let hideTimer = 0
let seq = 0
// a chip hovered again within a moment shows what it showed
const KEEP_MS = 30_000
const seen = new Map<string, { at: number; data: ValuePeek }>()
async function fetchPeek(field: string, value: string): Promise<ValuePeek> {
  const k = `${field}\u0000${value}`
  const hit = seen.get(k)
  if (hit && Date.now() - hit.at < KEEP_MS) return hit.data
  const data = await api.peek(field, value)
  seen.set(k, { at: Date.now(), data })
  return data
}

// a list the owner fills — the fields whose values have pages
export function hasValuePage(field: string): boolean {
  const f = store.fields.find((x) => x.id === field)
  return !!f && f.type === 'list' && f.control === 'chips' && f.editable
}

export function peekOn(e: MouseEvent, field: string, value: string) {
  if (!hasValuePage(field)) return
  window.clearTimeout(hideTimer)
  window.clearTimeout(showTimer)
  const el = e.currentTarget as HTMLElement
  const n = ++seq
  showTimer = window.setTimeout(async () => {
    let data: ValuePeek
    try { data = await fetchPeek(field, value) } catch { return }
    if (n !== seq) return
    const r = el.getBoundingClientRect()
    const above = r.bottom + 280 > window.innerHeight
    Object.assign(peek, {
      open: true, data, above,
      x: Math.max(8, Math.min(r.left, window.innerWidth - WIDTH - 8)),
      y: above ? r.top - 6 : r.bottom + 6,
    })
  }, DELAY)
}

export function peekOff() {
  window.clearTimeout(showTimer)
  seq++
  hideTimer = window.setTimeout(() => { peek.open = false }, LINGER)
}

// the pointer reached the card: it stays
export function peekHold() {
  window.clearTimeout(hideTimer)
}

export function peekClose() {
  window.clearTimeout(showTimer)
  window.clearTimeout(hideTimer)
  seq++
  peek.open = false
}
// a chip's click navigates; the card belongs to the place it was asked from
watch(() => [store.view, store.activeTitle, store.valuePage], peekClose)
// what a page says changes with a save, a run, another library
watch(() => [store.titles, store.libraryGen, store.valuePage], () => seen.clear())
