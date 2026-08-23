// Domain types shared across the UI. These mirror the backend's camelCase DTOs
// (backend/app/library/models.py, backend/app/scraper/models.py); the data
// itself is fetched via api.ts.

// type/status are OPEN vocabularies stored LOWERCASE (custom values can't fork
// on case); the UI capitalizes at display time. Capture maps known synonyms
// onto the canonical values first.
export type MediaType = string
export type Status = string
export const DEFAULT_TYPES = ['manga', 'manhwa', 'manhua', 'comic', 'image set']
export const DEFAULT_STATUSES = ['ongoing', 'completed', 'paused']
export type ReadState = 'read' | 'reading' | 'unread'
export type Origin = 'auto' | 'manual'

export interface Flags { adult: boolean; ai: boolean; censored: boolean }
export interface SourceRef { domain: string; url: string }

export interface FieldProvenance {
  origin: Origin
  url?: string
  recipeVersion?: number
}
export type Provenance = Record<string, FieldProvenance>

// One scraped chapter row — one row per translation. `id` is the dedup key for
// accumulative capture and the future on-disk file name; it is not editable.
export interface ChapterRow {
  id: string
  num: string
  title: string
  url: string
  lang: string
  group: string
  date: string
}
export interface Chapter extends ChapterRow {
  read: ReadState
  dl: boolean       // a downloaded archive exists in the vault
  pages: number     // image pages inside it (0 for non-zip archives)
  dlSource: string  // where the DOWNLOAD came from (independent of meta.source)
  dlAt: string      // when it was downloaded (ISO, from the sidecar)
  v: string         // media version — page/video URLs are cached under it
  kind: 'pages' | 'video' // a zip of images, or the episode file itself
  duration: number  // seconds (video only), learned from the player
  playable: boolean // whether this container opens in the app's browser engine
  container: string // mp4 / mkv / avi … — named in the list when it cannot play
  position: number  // seconds into a video chapter — the resume point
  codec: string     // h264 / hevc / av1 … — what the file actually holds
  faststart: boolean // its index sits before the media, so playback starts at once
}

// The metadata layer of a title — exactly what a draft edits and a commit writes.
export interface TitleMeta {
  title: string
  alt: string
  authors: string[]
  artists: string[]
  characters: string[]
  type: MediaType
  status: Status
  year: string
  genres: string[]
  tags: string[]
  flags: Flags
  desc: string
  coverSource: string
  source: SourceRef
  // chapter display order: 'auto' = smart by number; 'manual' = as arranged
  chapterOrder: string
}

// The flat wire DTO (meta + user layer + derived), as served by the backend.
export interface Title extends TitleMeta {
  id: string
  cover: string // local cover endpoint URL when bytes exist, else ''
  fav: boolean
  rating: number
  unread: number
  provenance: Provenance
  chapters: Chapter[]
}

export interface DraftCommit {
  meta: TitleMeta
  provenance: Provenance
  chapters: ChapterRow[]
}

export interface UserPatch {
  fav?: boolean
  rating?: number
  read?: Record<string, ReadState>
  position?: Record<string, number> // chapter id → seconds, for video chapters
}

export interface AuthorWork { id: string; title: string; cover: string }
export interface Author {
  id: string
  name: string
  role: 'author' | 'artist' | 'both'
  works: AuthorWork[]
  fav: boolean // user layer — persisted in the vault's authors.json
  titles: number
  chapters: number
  topTags: string[]
}

export interface Source {
  id: string
  domain: string
  homepage: string
  titles: number
  hasRecipe: boolean
  recipeVer: number
  fields: string[] // recipe field names mapped for this domain
}

export interface FacetValue { v: string; n: number }
export interface Facets {
  types: FacetValue[]
  statuses: FacetValue[]
  genres: FacetValue[]
  tags: FacetValue[]
  languages: FacetValue[]
  flags: FacetValue[]
  authors: FacetValue[]
  characters: FacetValue[]
}
export function emptyFacets(): Facets {
  return { types: [], statuses: [], genres: [], tags: [], languages: [], flags: [], authors: [], characters: [] }
}

export function faviconFor(domain: string): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`
}

// ---- recipe v2 (mirrors backend/app/scraper/models.py) ----
// A field's extractor is an ORDERED list of candidates tried against the
// rendered DOM: the picked CSS selector first, then generated fallbacks, then
// page metadata (og:/JSON-LD).
export type CandidateKind = 'css' | 'anchor' | 'meta'
export interface Candidate {
  kind: CandidateKind
  selector: string      // css → a selector; anchor → a row-label text whose
                        // sibling holds the value; meta → a metadata key ("og:title")
  attr?: string | null  // attribute to read (src/href/content); null → text
  note?: string         // "picked" | "label" | "structural" | "fallback"
}
export interface FieldRule {
  mode: 'single' | 'list'
  index?: number
  lower?: boolean
  stripCounts?: boolean
  candidates: Candidate[]
}
export interface ChapterRule {
  item: string
  id?: FieldRule | null
  num?: FieldRule | null
  title?: FieldRule | null
  url?: FieldRule | null
  lang?: FieldRule | null
  group?: FieldRule | null
  date?: FieldRule | null
}
export interface Recipe {
  domain: string
  version: number
  fields: Record<string, FieldRule>
  chapters?: ChapterRule | null
}

const STATUS_COLOR: Record<string, string> = {
  ongoing: 'var(--good)',
  completed: 'var(--accent)',
  paused: 'var(--warn)',
}
export function statusColor(status: string): string {
  return STATUS_COLOR[status.toLowerCase()] ?? 'var(--tx3)' // custom → neutral dot
}

// type/status start EMPTY on purpose: no built-in value is ever auto-substituted
// — a fresh draft gets only the remembered last choice (draft.ts), or nothing.
export function blankMeta(): TitleMeta {
  return {
    title: '', alt: '', authors: [], artists: [], characters: [], type: '', status: '',
    year: '', genres: [], tags: [], flags: { adult: false, ai: false, censored: false },
    desc: '', coverSource: '', source: { domain: '', url: '' }, chapterOrder: 'auto',
  }
}

// ---- presentation helpers ----

const GRADS = [
  'linear-gradient(150deg,#20303a,#2b4150 55%,#1a2730)',
  'linear-gradient(150deg,#3a2530,#512b3a 55%,#331b24)',
  'linear-gradient(150deg,#233a2f,#2d5142 55%,#1a3327)',
  'linear-gradient(150deg,#2f2a3a,#433a58 55%,#241f31)',
  'linear-gradient(150deg,#3a341f,#524829 55%,#332d17)',
  'linear-gradient(150deg,#2b2140,#3a2b52 55%,#241b33)',
]

// Deterministic placeholder gradient for a title without a cover — presentation
// only, derived from the id (the vault stores no styling).
export function hueFor(key: string): string {
  let h = 5381
  for (let i = 0; i < key.length; i++) h = ((h << 5) + h + key.charCodeAt(i)) | 0
  return GRADS[Math.abs(h) % GRADS.length]
}

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || '?'
}

// Smart chapter ordering: numeric numbers ascending ("2" < "10", "5.5" between
// 5 and 6), non-numeric ones ("Extra") after, alphabetically.
export function compareChapterNums(a: string, b: string): number {
  const ma = /^\s*(\d+(?:[.,]\d+)?)/.exec(a)
  const mb = /^\s*(\d+(?:[.,]\d+)?)/.exec(b)
  if (ma && mb) return parseFloat(ma[1].replace(',', '.')) - parseFloat(mb[1].replace(',', '.'))
  if (ma) return -1
  if (mb) return 1
  return a.trim().toLowerCase().localeCompare(b.trim().toLowerCase())
}

// A sized cover variant — the backend serves a cached LANCZOS downscale, so
// grids never decode (or blurrily GPU-scale) multi-MB originals. Pick w ≈ 2×
// the CSS width so DPR-2 screens stay crisp.
export function coverAt(cover: string, w: number): string {
  return cover ? `${cover}${cover.includes('?') ? '&' : '?'}w=${w}` : ''
}

// Read-state dot colors — one palette for the title page and the reader.
export const READ_COLOR: Record<ReadState, string> = {
  read: 'var(--good)', reading: 'var(--warn)', unread: 'var(--tx3)',
}

// A chapter row's IDENTITY: label + language + group, compared the way the
// backend compares it. Every "does this entry already exist?" check goes
// through here so the answer can't differ between the dock and the title page.
export interface ChapterIdentity { num: string; lang: string; group: string }
export function sameChapter(a: ChapterIdentity, b: ChapterIdentity): boolean {
  const norm = (s: string) => (s || '').trim().toLowerCase()
  return norm(a.num) === norm(b.num) && norm(a.lang) === norm(b.lang) && norm(a.group) === norm(b.group)
}

// Running time, as a human reads it.
export function formatDuration(seconds: number): string {
  if (!seconds || !Number.isFinite(seconds)) return ''
  const s = Math.floor(seconds % 60), m = Math.floor(seconds / 60) % 60, h = Math.floor(seconds / 3600)
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`
}

// What a chapter's media can be, as the file pickers and the drop handler see
// it. The vault decides by the same extensions (backend: media.VIDEO_EXTS),
// and refuses anything whose bytes disagree with its name.
export const ARCHIVE_RE = /\.(zip|cbz|rar|7z)$/i
export const IMAGE_RE = /\.(jpe?g|png|webp|gif|avif|bmp)$/i
export const VIDEO_RE = /\.(mp4|m4v|webm|mkv|mov|avi|ts)$/i
export const MEDIA_ACCEPT = '.zip,.cbz,.rar,.7z,.mp4,.m4v,.webm,.mkv,.mov,.avi,.ts,image/*'
export const FILE_ACCEPT = '.zip,.cbz,.rar,.7z,.mp4,.m4v,.webm,.mkv,.mov,.avi,.ts'

// Whether opening this row shows the human something. A page chapter needs
// pages; an episode needs only its file — asking for `pages` here is what made
// video rows unclickable while the vault held the episode all along.
export function isReadable(c: Chapter): boolean {
  return c.dl && (c.kind === 'video' || c.pages > 0)
}

// What a chapter row shows about its media. A title can hold both kinds, so the
// label has to say WHICH — "12 pg" and "23:41" are the same column.
export function mediaLabel(c: Chapter, empty = ''): string {
  if (!c.dl) return empty
  if (c.kind === 'video') {
    // an episode the app cannot open is named by what it IS: a duration it
    // never measured would be a blank where the reason belongs
    if (!c.playable) return (c.container || 'video').toUpperCase()
    return formatDuration(c.duration) || 'video'
  }
  return c.pages ? `${c.pages} pg` : 'file'
}

// Stored, listed, catalogued — and not openable here. Asked in ONE place so the
// list and the pane can never disagree about it.
export function isUnsupported(c: Chapter): boolean {
  return c.dl && c.kind === 'video' && !c.playable
}

// Why a stored episode has no player. Said once here, so the list and the pane
// cannot word it differently — and with no promise of a remux the app cannot do.
export function unsupportedTitle(c: Chapter): string {
  return `${(c.container || '').toUpperCase() || 'This format'} is not supported`
}
export const UNSUPPORTED_HINT =
  'The file is stored in your vault, but the app cannot play it. MP4, M4V and WebM play.'
export function unsupportedNote(c: Chapter): string {
  return `${unsupportedTitle(c)} — ${UNSUPPORTED_HINT}`
}

// The chapter list in READING order: the title's own manual order when it has
// one, natural chapter order otherwise. The title page, the reader and the
// library grid all show the same shelf, so they read it from here.
export function orderedChaptersOf<T extends { num: string }>(
  chapters: T[] | undefined, chapterOrder: string | undefined): T[] {
  const rows = [...(chapters ?? [])]
  if (chapterOrder !== 'manual') rows.sort((a, b) => compareChapterNums(a.num, b.num))
  return rows
}

// The values actually present on a chapter list, as filter options.
export function filterOptions<T>(rows: T[], pick: (row: T) => string): { v: string; l: string }[] {
  return [{ v: 'all', l: 'All' },
    ...[...new Set(rows.map(pick).filter(Boolean))].map((v) => ({ v, l: v }))]
}

// Group chapter-like rows by their label (num), first-seen order kept — the
// shared tree grammar of the title page, the reader sidebar and the dock.
export function groupByNum<T extends { num: string }>(rows: T[]): { num: string; rows: T[] }[] {
  const order: string[] = []
  const by = new Map<string, T[]>()
  for (const r of rows) {
    if (!by.has(r.num)) { by.set(r.num, []); order.push(r.num) }
    by.get(r.num)!.push(r)
  }
  return order.map((num) => ({ num, rows: by.get(num)! }))
}

// The meta layer of a title, copied — draft seeding and targeted commits build
// the SAME shape from one place, so a new meta field can't be missed in either.
export function metaOf(t: Title): TitleMeta {
  return {
    title: t.title, alt: t.alt, authors: [...t.authors], artists: [...t.artists],
    characters: [...(t.characters ?? [])],
    type: t.type, status: t.status, year: t.year, genres: [...t.genres], tags: [...t.tags],
    flags: { ...t.flags }, desc: t.desc, coverSource: t.coverSource,
    source: { ...t.source }, chapterOrder: t.chapterOrder || 'auto',
  }
}

// Strip derived fields (read/dl/pages/…) down to the committable rows.
export function chapterRowsOf(chapters: ChapterRow[]): ChapterRow[] {
  return chapters.map(({ id, num, title, url, lang, group, date }) => ({ id, num, title, url, lang, group, date }))
}

// Stable chapter id when the site provides none: derived from the row's
