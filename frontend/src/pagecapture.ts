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
  domain: string           // the site it was taught on — a learned fix goes back HERE
  tabId: string | null     // the browser tab it was armed on; other tabs are never read
  active: boolean
  busy: boolean            // the fetch queue is draining
  status: string           // one live line for the panel
  added: number            // pages stored into the current entry this session
  error: string
}

export const pageCapture = reactive<PageCaptureState>({
  titleId: null, chapterId: null, label: '', selector: '', domain: '', tabId: null,
  active: false, busy: false, status: '', added: 0, error: '',
})

export function startPageCapture(opts: {
  titleId: string; chapterId: string; label: string; selector: string
  domain: string; tabId: string | null
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
  pageCapture.tabId = null
  pageCapture.status = ''
  pageCapture.error = ''
}

// Any reason the armed target stopped existing (the title or the chapter row
// deleted, the library switched) ends the session — otherwise every tick keeps
// posting pages at a dead id and the panel only shows failures.
export function stopCaptureFor(opts: { titleId?: string; chapterId?: string } = {}) {
  if (!pageCapture.active) return
  if (opts.titleId && pageCapture.titleId !== opts.titleId) return
  if (opts.chapterId && pageCapture.chapterId !== opts.chapterId) return
  stopPageCapture()
}

// The dedup key for a page image: the file name the site serves it under,
// WITHOUT the query (tokens rotate). Falls back to the whole URL when the path
// carries no usable name (`image.php?id=7`).
export function pageKeyFor(url: string): string {
  try {
    const u = new URL(url)
    const name = u.pathname.slice(u.pathname.lastIndexOf('/') + 1)
    if (name && /\.[a-z0-9]{2,5}$/i.test(name)) return decodeURIComponent(name).toLowerCase()
    // no file name in the path (`read.php?id=7`): the path plus the ordering
    // parameters — but NEVER the whole query, whose token rotates per visit
    const stable = [...u.searchParams.entries()]
      .filter(([k]) => /^(id|p|page|n|no|num|i|idx|index|chapter|ch)$/i.test(k))
      .map(([k, v]) => `${k.toLowerCase()}=${v}`)
      .sort()
      .join('&')
    return `${u.origin}${u.pathname}${stable ? `?${stable}` : ''}`.toLowerCase()
  } catch { /* not a parseable URL */ }
  return url
}

