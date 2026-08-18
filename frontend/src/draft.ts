// THE draft — the working copy of one record (design/state-model.md §4).
//
// A draft is seeded from a blank record, from the vault (editing a saved title)
// or filled from an immutable page snapshot. It lives here, outside the browser,
// and holds no reference to any live page — navigation physically cannot touch
// it (I2). Every field carries provenance; automatic merges write only into
// `auto`/empty fields, so a manual edit is untouchable (I3). Commit is explicit.
import { reactive } from 'vue'
import { api } from './api'
import {
  blankMeta, chapterRowsOf, metaOf,
  type ChapterRow, type FieldProvenance, type Flags, type Provenance, type Title, type TitleMeta,
} from './data'
import { isRecord, readLocal } from './local'
import { LIST_KEYS, captureValue, type CleanFlags } from './normalize'
import { askConfirm, cache, refreshLibrary, store } from './store'

// The draft's view of the cover — decoupled from any page:
//  keep      editing an existing title, its stored cover stays as-is
//  captured  bytes captured from a page's context, held in the draft until commit
//  url       a pasted remote URL; the backend fetches it at commit (fallback path)
//  none      no cover / cleared
export interface DraftCover {
  kind: 'none' | 'keep' | 'captured' | 'url'
  preview: string // what the editor shows: data URL, stored URL, or remote URL
  data?: string          // base64 (captured)
  contentType?: string   // (captured)
  sourceUrl?: string     // where the bytes/URL came from
  referer?: string       // page origin for the fallback fetch
}

export interface Draft {
  targetId: string | null // null = a new record
  targetLabel: string     // display name of the edit target
  meta: TitleMeta
  provenance: Provenance
  chapters: ChapterRow[]
  cover: DraftCover
  saving: boolean
}

// An immutable page snapshot, as returned by the preload in one shot. The draft
// is built FROM this — never from the page (state-model §3).
export interface Snapshot {
  url: string
  domain: string
  recipeVersion: number
  fields: Record<string, string | string[]>
  flags?: Record<string, CleanFlags> // per-field cleanup from the recipe rules
  cover?: { data: string; contentType: string; sourceUrl: string }
}

export const draftState = reactive<{ cur: Draft | null }>({ cur: null })

function baseline(d: Draft): string {
  return JSON.stringify({ meta: d.meta, provenance: d.provenance, chapters: d.chapters, cover: d.cover })
}
let savedBaseline = ''

export function isDirty(): boolean {
  const d = draftState.cur
  return !!d && baseline(d) !== savedBaseline
}

function setDraft(d: Draft) {
  draftState.cur = d
  savedBaseline = baseline(d)
}

// ---- seeding (state-model §4: same editor, different seed) ----

// Sticky defaults: a fresh draft starts from the type/status/flags of the last
// COMMITTED one, so batch-adding similar titles never means re-picking them.
const DEFAULTS_KEY = 'lb.newDraftDefaults'
interface NewDraftDefaults { type?: string; status?: string; flags?: Flags }
function newDraftDefaults(): NewDraftDefaults {
  const raw = readLocal(DEFAULTS_KEY, isRecord, {} as Record<string, unknown>)
  // a value from an older build must not reach `.toLowerCase()` below and take
  // "Add title" down with it
  const str = (v: unknown) => (typeof v === 'string' ? v : undefined)
  return {
    type: str(raw.type), status: str(raw.status),
    flags: isRecord(raw.flags) ? (raw.flags as unknown as Flags) : undefined,
  }
}
function rememberDraftDefaults(meta: TitleMeta) {
  try {
    localStorage.setItem(DEFAULTS_KEY, JSON.stringify(
      { type: meta.type, status: meta.status, flags: meta.flags } satisfies NewDraftDefaults))
  } catch { /* storage unavailable */ }
}

export function newDraft() {
  const meta = blankMeta()
  const last = newDraftDefaults()
  if (last.type) meta.type = last.type.toLowerCase()
  if (last.status) meta.status = last.status.toLowerCase()
  if (last.flags) meta.flags = { ...meta.flags, ...last.flags }
  setDraft({
    targetId: null, targetLabel: '', meta, provenance: {},
    chapters: [], cover: { kind: 'none', preview: '' }, saving: false,
  })
}

export function draftFromTitle(t: Title) {
  setDraft({
    targetId: t.id,
    targetLabel: t.title,
    meta: metaOf(t),
    provenance: JSON.parse(JSON.stringify(t.provenance || {})),
    chapters: chapterRowsOf(t.chapters),
    cover: { kind: t.cover ? 'keep' : 'none', preview: t.cover },
    saving: false,
  })
}

// Ask before discarding a dirty draft; returns true when it is safe to proceed.
export async function confirmDiscard(): Promise<boolean> {
  if (!draftState.cur || !isDirty()) return true
  return askConfirm({
    title: 'Discard draft?', danger: true, okLabel: 'Discard',
    message: draftState.cur.targetId
      ? `Unsaved changes to “${draftState.cur.targetLabel}” will be lost.`
      : 'This new title hasn’t been saved. Discard it?',
  })
}

export function discardDraft() {
  draftState.cur = null
  savedBaseline = ''
}

// ---- field writes ----

const META_STRINGS = ['title', 'alt', 'year', 'desc', 'type', 'status'] as const
export type EditableField =
  | (typeof META_STRINGS)[number] | 'authors' | 'artists' | 'characters' | 'genres' | 'tags' | 'cover'

// What a DRAFT can hold — the one list. A recipe also stores rules that are not
// draft fields (page capture teaches `pages`), and merging one of those would
// write a joined list of image URLs into the title, with `auto` provenance
// committed to the vault beside it.
export const EDITABLE_FIELDS: ReadonlySet<string> = new Set<EditableField>([
  ...META_STRINGS, 'authors', 'artists', 'characters', 'genres', 'tags', 'cover',
])

function fieldValue(meta: TitleMeta, field: string): string | string[] {
  return (meta as unknown as Record<string, string | string[]>)[field]
}
function setFieldValue(meta: TitleMeta, field: string, value: string | string[]) {
  ;(meta as unknown as Record<string, string | string[]>)[field] = value
}

function isEmpty(v: string | string[] | undefined): boolean {
  return Array.isArray(v) ? v.length === 0 : !String(v ?? '').trim()
}

// A human typed/edited this field → it becomes untouchable for auto capture.
export function setManual(field: string, value?: string | string[]) {
  const d = draftState.cur
  if (!d) return
  if (value !== undefined && field !== 'cover') setFieldValue(d.meta, field, value)
  d.provenance[field] = { origin: 'manual' }
}

export function clearField(field: EditableField) {
  const d = draftState.cur
  if (!d) return
  if (field === 'cover') {
    d.cover = { kind: 'none', preview: '' }
    d.meta.coverSource = ''
  } else if (LIST_KEYS.includes(field)) {
    setFieldValue(d.meta, field, [])
  } else {
    setFieldValue(d.meta, field, '') // type/status clear to empty too — no auto value
  }
  delete d.provenance[field]
}

// ANY capture from a page binds the draft to that source (first page wins) —
// per-field picks must record it exactly like auto-fill does, or the library
// can never recognize "this title came from this page".
export function noteCaptureSource(domain: string, url: string) {
  const d = draftState.cur
  if (d && !d.meta.source.url) d.meta.source = { domain, url }
}

// One captured value (from the pick inspector) → the draft, marked auto.
export function applyCapture(
  field: EditableField, raw: string | string[], clean: CleanFlags, prov: Omit<FieldProvenance, 'origin'>,
) {
  const d = draftState.cur
  if (!d) return
  if (field === 'cover') return // cover bytes go through applyCoverCapture
  setFieldValue(d.meta, field, captureValue(field, raw, clean))
  d.provenance[field] = { origin: 'auto', ...prov }
}

export function applyCoverCapture(cover: { data: string; contentType: string; sourceUrl: string }, prov: Omit<FieldProvenance, 'origin'>) {
  const d = draftState.cur
  if (!d) return
  d.cover = {
    kind: 'captured',
    preview: `data:${cover.contentType};base64,${cover.data}`,
    data: cover.data, contentType: cover.contentType, sourceUrl: cover.sourceUrl,
  }
  d.meta.coverSource = cover.sourceUrl
  d.provenance.cover = { origin: 'auto', ...prov }
}

// A cover captured as a URL only (page-context byte fetch failed — e.g. a
// cross-origin CDN rejecting fetch); the backend fetches it at commit.
export function applyCoverUrlAuto(url: string, referer: string, prov: Omit<FieldProvenance, 'origin'>) {
  const d = draftState.cur
  if (!d) return
  d.cover = { kind: 'url', preview: url, sourceUrl: url, referer }
  d.meta.coverSource = url
  d.provenance.cover = { origin: 'auto', ...prov }
}

// A pasted cover URL is a manual choice; the backend fetches it at commit.
export function setCoverUrl(url: string, referer = '') {
  const d = draftState.cur
  if (!d) return
  d.cover = { kind: 'url', preview: url, sourceUrl: url, referer }
  d.meta.coverSource = url
  d.provenance.cover = { origin: 'manual' }
}

// ---- the auto merge (I3 lives here) ----

// Fill the draft from a snapshot: write ONLY fields whose provenance is `auto`
// or which are empty; skip fields the snapshot has no value for. Manual fields
// are untouchable by construction.
export function mergeSnapshot(snap: Snapshot): string[] {
  const d = draftState.cur
  if (!d) return []
  const written: string[] = []
  const prov = { url: snap.url, recipeVersion: snap.recipeVersion }
  for (const [field, raw] of Object.entries(snap.fields)) {
    if (field === 'cover') continue
    // the merge invariant: auto may write only into `auto` or empty fields
    const cur = fieldValue(d.meta, field)
    if (!(d.provenance[field]?.origin === 'auto' || isEmpty(cur))) continue
    const cleaned = captureValue(field, raw, snap.flags?.[field])
    if (isEmpty(cleaned)) continue
    // status/type: known synonyms normalize onto the common vocabulary
    // (captureValue), anything else is stored as the custom value it is
    setFieldValue(d.meta, field, cleaned)
    d.provenance[field] = { origin: 'auto', ...prov }
    written.push(field)
  }
  if (snap.cover) {
    const coverWritable = d.provenance.cover?.origin === 'auto' || d.cover.kind === 'none'
    if (coverWritable) {
      applyCoverCapture(snap.cover, prov)
      written.push('cover')
    }
  }
  // record where this title is captured from (first snapshot wins; a manual
  // source is never overwritten because source is not a captured field)
  if (!d.meta.source.url) d.meta.source = { domain: snap.domain, url: snap.url }
  return written
}

// ---- commit (explicit; atomic on the backend) ----

// Commit the draft to the vault. `asNew` forces a create even when editing.
// Returns the saved title id, or null on failure. No navigation happens here —
// the panel announces the save in place and the draft re-binds to the saved
// record, so the browser flow is never yanked away mid-capture.
export async function commitDraft(asNew = false): Promise<string | null> {
  const d = draftState.cur
  if (!d || !d.meta.title.trim()) return null
  d.saving = true
  try {
    const body = { meta: d.meta, provenance: d.provenance, chapters: d.chapters }
    const target = asNew ? null : d.targetId
    let saved: Title = target ? await api.commitTitle(target, body) : await api.createTitle(body)
    // cover bytes ride along right after the document commit
    if (d.cover.kind === 'captured' && d.cover.data) {
      saved = await api.setCover(saved.id, {
        data: d.cover.data, contentType: d.cover.contentType, sourceUrl: d.cover.sourceUrl,
      })
    } else if (d.cover.kind === 'url' && d.cover.sourceUrl) {
      try {
        saved = await api.setCover(saved.id, { url: d.cover.sourceUrl, referer: d.cover.referer })
      } catch { /* the document is saved; a failed cover fetch is not fatal */ }
    } else if (d.cover.kind === 'none' && target && store.byId[target]?.cover) {
      saved = await api.deleteCover(saved.id)
    }
    cache([saved])
    rememberDraftDefaults(d.meta) // the next fresh draft starts from these choices
    await refreshLibrary()
    draftFromTitle(store.byId[saved.id] ?? saved) // clean re-bind to what was just saved
    return saved.id
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
    return null
  } finally {
    if (draftState.cur === d) d.saving = false
  }
}
