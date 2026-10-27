// Domain types shared across the UI. These mirror the backend's camelCase DTOs
// (backend/app/library/models.py, backend/app/scraper/models.py); the data
// itself is fetched via api.ts.

// type/status are OPEN vocabularies stored LOWERCASE (custom values can't fork
// on case); the UI capitalizes at display time. Capture maps known synonyms
// onto the canonical values first.
export type MediaType = string
export type Status = string
// Only STATUS gets a starting vocabulary. Type does not: a suggestion list of
// five words the library has never seen is how a vault ends up with `comic` and
// `comics` and one title under each — the type picker offers what is in the
// vault and nothing else.
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
  // the part of the work it belongs to (a volume, a season, a year) — the title
  // page groups by it; it is never part of the entry's identity
  section: string
}
// The two stills an episode can carry — one frame for a tile, a grid of them
// for a preview. Both are cut in the same pass and kept in the vault.
export type FrameKind = 'poster' | 'sheet'

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
  poster: boolean   // the one frame a tile wears is cut and stored
  sheet: string     // the geometry of the stored contact grid, '' when none
  stills: string    // the version those two are cached under (NOT the media's)
  // The name the file ARRIVED under. The vault renames every stored file to the
  // chapter's id, so this is the only record of what it was called at its source.
  origName: string
}

// The metadata layer of a title — exactly what a draft edits and a commit writes.
export interface TitleMeta {
  title: string
  alt: string
  authors: string[]
  artists: string[]
  characters: string[]
  studio: string[]
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
  custom: Record<string, string | string[]>
}

// How many covers a title may store. The VAULT enforces it (it runs out of
// slots and says so); this is only so the strip can stop offering "add another"
// at the same number rather than after a refused request.
export const MAX_COVERS = 8

// The flat wire DTO (meta + user layer + derived), as served by the backend.
export interface Title extends TitleMeta {
  id: string
  // What a tile SHOWS, in rotation order: the title's own covers, or — when it
  // has none — stand-ins the backend cut from its own media.
  covers: string[]
  cover: string      // the first of those; '' only when there is nothing at all
  ownCovers: string[] // the covers actually STORED; the editor manages exactly these
  fav: boolean
  rating: number
  // how the reader shows THIS title — empty until it was first adjusted. Read
  // field by field: it comes from a file a person may have edited by hand.
  reader: Partial<ReaderPrefs>
  unread: number
  provenance: Provenance
  chapters: Chapter[]
}

export interface ReaderPrefs {
  mode: 'pages' | 'strip'
  fit: 'height' | 'width' | 'custom'
  width: number   // px, for the custom fit
  margins: number // px around the page
}

// A stored number as one the app can use: anything that is not a number reads
// as the fallback, anything out of range as the nearest bound.
function boundedNumber(v: unknown, lo: number, hi: number, fallback: number): number {
  const n = Number(v)
  return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fallback
}

// Reader settings from wherever they were kept, as the nearest thing the reader
// can draw — the same bounds the vault enforces on a write.
export function readerPrefsOf(p: Record<string, unknown>): ReaderPrefs {
  return {
    mode: p.mode === 'strip' ? 'strip' : 'pages',
    fit: p.fit === 'width' || p.fit === 'custom' ? p.fit : 'height',
    width: Math.round(boundedNumber(p.width ?? 720, 480, 1400, 720)),
    margins: Math.round(boundedNumber(p.margins ?? 0, 0, 200, 0)),
  }
}

// What page capture accepts as a PAGE among the images its selector matches: a
// selector wide enough to catch every page also catches the reader's icons and
// ads, so a match must be past an absolute floor and not much narrower than
// the widest match on the same view. Kept in the vault, beside the recipes.
export interface PageFilter {
  minPx: number      // an image smaller than this in either dimension is furniture
  widthRatio: number // …and it must be at least this share of the widest match
}
export const PAGE_FILTER_DEFAULTS: PageFilter = { minPx: 200, widthRatio: 0.5 }

export function pageFilterOf(p: Record<string, unknown>): PageFilter {
  return {
    minPx: Math.round(boundedNumber(p.minPx, 0, 2000, PAGE_FILTER_DEFAULTS.minPx)),
    widthRatio: boundedNumber(p.widthRatio, 0, 1, PAGE_FILTER_DEFAULTS.widthRatio),
  }
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
  reader?: ReaderPrefs
}

export interface AuthorWork { id: string; title: string; cover: string }
export interface Bookmark { name: string; url: string }

// ---- metadata management (design/metadata-management.md) ----

// Which titles an operation may touch: exactly these ids, or what the library
// shows under its filters (the same query `api.library` takes).
export interface MetaScope {
  ids?: string[] | null
  search?: string
  progress?: string
  fav?: boolean
  min_rating?: number
  f?: string[]
  nf?: string[]
}
export type MetaShape = 'list' | 'single' | 'flags' | 'chapter'
export interface MetaField {
  id: string; label: string; kind: 'title' | 'chapter'; shape: MetaShape
  type: string; builtin: boolean; distinct: number
}
export interface MetaValue {
  value: string
  titles: number
  typed: number    // how many of those titles hold it because the owner typed it
  rule: number     // the position of the rule that already handles it (0 = none)
  ruleText: string // what that rule makes of it
}
export type MetaActionKind = 'rename' | 'remove' | 'move' | 'add' | 'set' | 'clear' | 'replace'
export interface MetaAction {
  kind: MetaActionKind
  field: string
  values?: string[]
  to?: string
  toField?: string
  find?: string
}
export interface MetaSample { titleId: string; title: string; field: string; before: string; after: string }
export interface MetaPreview {
  inScope: number
  titles: number        // titles that change
  manual: number        // changed values the owner typed
  auto: number          // changed values that came from capture
  conflicts: number     // titles where a move found its one slot taken
  shelfMoves: number
  shelfRename: string[] // [old, new] when a whole shelf is renamed at once
  chapterTwins: number
  untouched: number     // titles already holding the rename's target
  sample: MetaSample[]
}
export interface MetaRun {
  id: number; at: string; summary: string; scope: Record<string, unknown>
  titles: number; seconds: number; stopped: boolean; revertedAt: string
}
export type RuleAction = 'rename' | 'remove' | 'move' | 'add'
export interface Rule {
  id: string
  enabled: boolean
  field: string
  match: string[]
  action: RuleAction
  to: string
  toField: string
  domains: string[]
}
export interface RuleStats { fired: number; last: string }
export type FieldValues = Record<string, string | string[]>
export interface RuleApply { values: FieldValues; fired: { rule: string; n: number; field: string }[] }

// Titles grouped by ONE value of ONE list field. People are this plus the two
// things only they have (a role, a favourite mark) — not a separate shape.
export interface BrowseGroup {
  id: string
  field: string
  value: string
  works: AuthorWork[]
  titles: number
  chapters: number
  topTags: string[]
  role: 'author' | 'artist' | 'both' | null
  fav: boolean
}

export interface Source {
  id: string
  domain: string
  homepage: string
  titles: number
  hasRecipe: boolean
  recipeVer: number
  fields: string[] // recipe field names mapped for this domain
  group: string    // the user's grouping; '' = ungrouped
  bookmarks: Bookmark[]
  frozen: boolean  // still listed here, kept out of the browser's rail
}

export interface FacetValue { v: string; n: number }
// What a metadata field is, as the backend describes it (library/fields.py).
// The UI renders from THIS — never from a list of field names of its own.
export interface FieldDef {
  id: string
  label: string
  type: 'text' | 'description' | 'number' | 'list' | 'date'
  // 'cover' and 'flags' are the two bespoke rows: they have a label, a body and
  // a place in the order like any field, but their body is not a value control
  control: 'line' | 'multiline' | 'vocab' | 'chips' | 'number' | 'date' | 'toggle'
    | 'cover' | 'flags'
  builtin: boolean
  required: boolean
  editable: boolean
  facet: boolean
  placeholder: string
  vocab: string // the field id whose suggestions this one offers
  group: string // which block of the editor draws it
}

// Facet counts keyed by field id.
export type Facets = Record<string, FacetValue[]>

export function emptyFacets(): Facets {
  return {}
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
  join?: string // what separates several matches inside a one-value field
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
  // registry field ids this source does not offer (a fact about the SITE)
  hidden?: string[]
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
// An empty meta layer. The FIELDS come from the served registry, so one added
// there is carried without a change here; the spine is named, because it is not
// a field with a value but structure the app has logic for.
export function blankMeta(fields: FieldDef[]): TitleMeta {
  return { ...valuesOf({}, fields), ...SPINE() } as unknown as TitleMeta
}

function SPINE() {
  return {
    flags: { adult: false, ai: false, censored: false },
    coverSource: '', source: { domain: '', url: '' }, chapterOrder: 'auto', custom: {},
  }
}

// Field values copied off a bag, one entry per registry field, each an empty
// value of the right shape when the bag has none.
function valuesOf(bag: Record<string, unknown>, fields: FieldDef[]): Record<string, unknown> {
  const custom = (bag.custom ?? {}) as Record<string, unknown>
  const out: Record<string, unknown> = {}
  for (const f of fields) {
    if (!f.editable || f.control === 'cover' || f.control === 'flags') continue
    // A user-defined value is stored under `custom` but edited like any other:
    // the draft carries every field flat, and commit puts it back (draft.ts).
    const v = f.builtin ? bag[f.id] : custom[f.id]
    // Numbers and dates are edited and stored as text, exactly like `year`:
    // an unset number must stay unset, and 0 is a value somebody may mean.
    if (f.type === 'list') out[f.id] = Array.isArray(v) ? [...v] : []
    else out[f.id] = typeof v === 'string' ? v : ''
  }
  return out
}

/** What a title holds in one field, built-in or user-defined.
 *
 * A user-defined value lives under `custom` while a built-in is a property of
 * its own — every screen that shows a field needs the same two-line answer, so
 * it lives here rather than in whichever screen asked first. */
export function fieldValue(t: Title, f: FieldDef): string | string[] {
  const v = f.builtin
    ? (t as unknown as Record<string, unknown>)[f.id]
    : (t.custom ?? {})[f.id]
  if (Array.isArray(v)) return v.filter(Boolean) as string[]
  return typeof v === 'string' ? v : ''
}

export function hasValue(t: Title, f: FieldDef): boolean {
  const v = fieldValue(t, f)
  return Array.isArray(v) ? v.length > 0 : !!v.trim()
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
// ---- title matching -------------------------------------------------------
//
// Two records are the same work far more often than they are spelled the same:
// a page says "Onee-chan Gets Carried Away", the library holds it with a ♡ in
// the name and the romaji only in an alt. So a comparison drops what casing and
// punctuation can differ by, and scores the remainder by the words shared —
// with a containment pass, because a page title is usually the library title
// plus a subtitle ("… The Animation").
const NOT_WORD = /[^\p{L}\p{N}]+/gu

export function titleKey(s: string): string {
  return s.toLowerCase().normalize('NFKD').replace(NOT_WORD, ' ').trim()
}

export function altNames(alt: string): string[] {
  // alt titles are ONE field holding several names — the separator is whatever
  // the capture joined them with (see normalize.ts) or whatever a human typed
  return alt.split(/[|·/;]|\s{2,}/).map((s) => s.trim()).filter(Boolean)
}

export function titleSimilarity(a: string, b: string): number {
  const ka = titleKey(a)
  const kb = titleKey(b)
  if (!ka || !kb) return 0
  if (ka === kb) return 1
  const wa = new Set(ka.split(' ').filter((w) => w.length > 1))
  const wb = new Set(kb.split(' ').filter((w) => w.length > 1))
  if (!wa.size || !wb.size) return 0
  let shared = 0
  for (const w of wa) if (wb.has(w)) shared++
  const dice = (2 * shared) / (wa.size + wb.size) // shared words, both ways
  const inside = ka.includes(kb) || kb.includes(ka) ? 0.85 : 0
  return Math.max(dice, inside)
}

export interface TitleMatch { t: Title; score: number }

/** The library records a name may already belong to — alts included. */
export function matchTitles(name: string, titles: Title[], min = 0.5, limit = 6): TitleMatch[] {
  if (!name.trim()) return []
  const out: TitleMatch[] = []
  for (const t of titles) {
    let best = 0
    for (const n of [t.title, ...altNames(t.alt)]) {
      best = Math.max(best, titleSimilarity(name, n))
      if (best === 1) break
    }
    if (best >= min) out.push({ t, score: best })
  }
  return out.sort((a, b) => b.score - a.score || a.t.title.localeCompare(b.t.title)).slice(0, limit)
}

// The cover of a card, a tile or a title page: the stored image if there is
// one, else the colour that title always gets. Written out in two views with
// two different widths and two different fallback keys — one of which fell back
// on the id and the other on the title, so the same title could be two colours.
export function coverStyle(t: { cover: string; id: string; title?: string }, w = 420) {
  return t.cover
    ? { background: `#181a1f url("${coverAt(t.cover, w)}") center/cover no-repeat` }
    : { background: hueFor(t.id || t.title || '') }
}

// The site a URL belongs to, as a human names it. One of the two copies
// returned the whole URL when it could not be parsed, the other an empty
// string; a caller could not know which it had.
export function hostOf(url: string): string {
  try { return new URL(url).hostname.replace(/^www\./, '') } catch { return '' }
}

// A drag-and-drop reorder: the row at `from` lands where `to` currently is, and
// a `to` outside the list means the end. Written once because three views were
// each getting the "dragging downwards shifts the target" adjustment right (or
// not) on their own.
export function movedBefore<T>(rows: readonly T[], from: number, to: number): T[] {
  const out = [...rows]
  if (from < 0 || from >= out.length) return out
  const [moved] = out.splice(from, 1)
  const at = to < 0 || to > out.length ? out.length : to > from ? to - 1 : to
  out.splice(at, 0, moved)
  return out
}

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

// What the media column can say about itself on hover: the name the file arrived
// under (the vault renamed it to the chapter id) and where it came from. Two
// facts, one per line, and nothing at all when it holds neither.
export function mediaTitle(c: Chapter): string {
  return [c.origName, c.dlSource].filter(Boolean).join('\n')
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
// the SAME shape from one place, so a meta field can't be missed in either.
export function metaOf(t: Title, fields: FieldDef[]): TitleMeta {
  return {
    ...valuesOf(t as unknown as Record<string, unknown>, fields),
    ...SPINE(),
    flags: { ...t.flags },
    coverSource: t.coverSource,
    source: { ...t.source },
    chapterOrder: t.chapterOrder || 'auto',
  } as unknown as TitleMeta
}

// The draft edits EVERY field flat — that is what keeps one editor for both
// kinds. The document nests the user-defined ones, so the split happens once,
// here, on the way to the wire.
export function metaForWire(meta: TitleMeta, fields: FieldDef[]): TitleMeta {
  const out = { ...meta } as unknown as Record<string, unknown>
  const custom: Record<string, unknown> = {}
  for (const f of fields) {
    if (f.builtin || !f.editable) continue
    if (f.id in out) {
      custom[f.id] = out[f.id]
      delete out[f.id]
    }
  }
  out.custom = custom
  return out as unknown as TitleMeta
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

// The labels an entry form should OFFER: the next few, continuing from the
// highest numeric one this title already holds. A named label ("Extra") is not
// a position, so it does not set the sequence; an empty title starts at 1. They
// are suggestions — the field stays free text.
// Numbering runs PER TRANSLATION. A title can hold chapter 6 in EN from one
// group and chapter 2 in UA from another, and "the next one" is a different
// number in each — so the count is taken over the rows that share the language
// and group being filled in. An empty field is not a filter: until a language
// is chosen the scope is everything, which is what a single-track title wants.
export function chaptersInScope<T extends { lang: string; group: string }>(
  rows: T[], lang: string, group: string): T[] {
  const l = lang.trim().toLowerCase()
  const g = group.trim().toLowerCase()
  if (!l && !g) return rows
  return rows.filter((c) => (!l || (c.lang || '').trim().toLowerCase() === l)
    && (!g || (c.group || '').trim().toLowerCase() === g))
}

/** The last label used in this scope — the one an added version repeats. */
export function lastLabel(chapters: { num: string }[] = []): string {
  const numeric = chapters.map((c) => String(c.num))
    .filter((n) => /\d/.test(n))
    .sort(compareChapterNums)
  return numeric.length ? numeric[numeric.length - 1] : ''
}

/** What the LABEL dropdown offers: the last one used, then the next few.
 *
 * The previous label is there because the commonest exception is not a new
 * chapter at all — it is the SAME chapter in another language or from another
 * group, which carries the same number. */
export function labelChoices(chapters: { num: string }[] = [], count = 5): string[] {
  const last = lastLabel(chapters)
  const next = nextLabels(chapters, count)
  return last ? [last, ...next] : next
}

export function nextLabels(chapters: { num: string }[] = [], count = 5): string[] {
  let cur = lastLabel(chapters)
  const out: string[] = []
  for (let i = 0; i < count; i++) {
    cur = cur ? nextLabel(cur) : String(i + 1)
    if (!cur) break
    out.push(cur)
  }
  return out
}

// Strip derived fields (read/dl/pages/…) down to the committable rows.
export function chapterRowsOf(chapters: ChapterRow[]): ChapterRow[] {
  return chapters.map(({ id, num, title, url, lang, group, date, section }) =>
    ({ id, num, title, url, lang, group, date, section }))
}

// Stable chapter id when the site provides none: derived from the row's
