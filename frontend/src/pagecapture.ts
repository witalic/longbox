// PAGE CAPTURE — the download mode for sources that serve pages, not archives
// (design/state-model.md §9, second lane).
//
// It works exactly like an armed archive download, only the file arrives page by
// page: the human names ONE entry, arms it, and reads. Every page view is
// scanned and whatever matches is fetched THROUGH THE PAGE'S OWN CONTEXT
// (cookies + referer) and appended to that entry — never to a chapter guessed
// from the URL. Finishing an entry is explicit, then the next one is armed.
//
// Dedup is by the image's own NAME, not its URL: CDN links carry rotating
// tokens, so the same page comes back as a "new" URL on every visit while its
// file name stays put.
//
// This is an ACTIVITY, not persisted state: it lives here (not in browser.ts,
// which carries no capture state) and ends when the user stops it.
import { reactive } from 'vue'
import type { EditableField } from './draft'

// What pick mode can target: the draft's metadata fields, plus the page-image
// selector that page capture teaches.
export type PickField = EditableField | 'pages'

export interface PageCaptureState {
  titleId: string | null
  chapterId: string | null // the armed entry — pages land HERE, always
  label: string            // its label, for the panel's status line
  selector: string         // CSS for the page images, taught via pick mode
  active: boolean
  busy: boolean            // a scan/fetch round is running
  status: string           // one live line for the panel
  added: number            // pages stored into the current entry this session
  error: string
}

export const pageCapture = reactive<PageCaptureState>({
  titleId: null, chapterId: null, label: '', selector: '',
  active: false, busy: false, status: '', added: 0, error: '',
})

// ---- what counts as a page (Settings → Advanced) ----
// A selector wide enough to catch every page also catches the reader's icons
// and ads, so a match must LOOK like a page: past an absolute floor, and not
// much narrower than the widest match on the same view. The defaults suit
// ordinary readers; odd sources need the knobs.
export interface PageFilter {
  minPx: number      // an image smaller than this in either dimension is furniture
  widthRatio: number // …and it must be at least this share of the widest match
}
export const PAGE_FILTER_DEFAULTS: PageFilter = { minPx: 200, widthRatio: 0.5 }
const FILTER_KEY = 'lb.pageFilter'

function loadFilter(): PageFilter {
  try {
    const raw = JSON.parse(localStorage.getItem(FILTER_KEY) || '{}')
    return {
      minPx: Number.isFinite(raw.minPx) ? Math.min(2000, Math.max(0, raw.minPx)) : PAGE_FILTER_DEFAULTS.minPx,
      widthRatio: Number.isFinite(raw.widthRatio) ? Math.min(1, Math.max(0, raw.widthRatio)) : PAGE_FILTER_DEFAULTS.widthRatio,
    }
  } catch { return { ...PAGE_FILTER_DEFAULTS } }
}

export const pageFilter = reactive<PageFilter>(loadFilter())

export function savePageFilter(patch: Partial<PageFilter>) {
  if (patch.minPx !== undefined) pageFilter.minPx = Math.min(2000, Math.max(0, Math.round(patch.minPx) || 0))
  if (patch.widthRatio !== undefined) pageFilter.widthRatio = Math.min(1, Math.max(0, patch.widthRatio))
  try { localStorage.setItem(FILTER_KEY, JSON.stringify({ ...pageFilter })) } catch { /* storage unavailable */ }
}

export function resetPageFilter() { savePageFilter(PAGE_FILTER_DEFAULTS) }

export function startPageCapture(opts: {
  titleId: string; chapterId: string; label: string; selector: string
}) {
  Object.assign(pageCapture, {
    ...opts, active: true, busy: false,
    status: 'open the first page of this chapter…', added: 0, error: '',
  })
}

export function stopPageCapture() {
  pageCapture.active = false
  pageCapture.busy = false
  pageCapture.chapterId = null
  pageCapture.status = ''
  pageCapture.error = ''
}

// The dedup key for a page image: the file name the site serves it under,
// WITHOUT the query (tokens rotate). Falls back to the whole URL when the path
// carries no usable name (`image.php?id=7`).
export function pageKeyFor(url: string): string {
  try {
    const path = new URL(url).pathname
    const name = path.slice(path.lastIndexOf('/') + 1)
    if (name && /\.[a-z0-9]{2,5}$/i.test(name)) return decodeURIComponent(name).toLowerCase()
  } catch { /* not a parseable URL */ }
  return url
}

// The next label after this one, so finishing a chapter arms the following one
// with a single keystroke: "5" → "6", "5.5" → "6.5", "Extra" → "" (name it).
export function nextLabel(label: string): string {
  const m = /^(\D*?)(\d+(?:[.,]\d+)?)(\D*)$/.exec(label.trim())
  if (!m) return ''
  const [, head, num, tail] = m
  const dec = num.includes(',') ? ',' : '.'
  const parts = num.split(/[.,]/)
  const next = parts.length > 1
    ? `${Number(parts[0]) + 1}${dec}${parts[1]}`
    : String(Number(num) + 1)
  return `${head}${next}${tail}`
}
