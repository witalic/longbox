// Value normalization lives CLIENT-SIDE, in the draft (design/state-model.md §4):
// the preview renders from the draft, so what the user sees while capturing is
// byte-for-byte what gets committed (I4). The backend only validates vocabulary.

import type { Status } from './data'

const COUNT_PAREN = /\s*\(\s*[\d.,]+\s*[kKmM]?\s*\)\s*$/
const COUNT_TRAIL = /\s*[·|,]?\s*[\d.,]+\s*[kKmM]?\s*$/

// Drop a trailing match-count a site shows next to a term ("Romance (255)",
// "Kaname 42"), keeping the text's case.
function stripCount(s: string): string {
  return String(s).replace(COUNT_PAREN, '').replace(COUNT_TRAIL, '').replace(/\s+/g, ' ').trim()
}

// Known synonyms (Latin + Cyrillic sites) → the canonical vocabulary. A value
// that maps stays canonical; anything else is a legitimate custom value.
const STATUS_MAP: [string, Status][] = [
  ['complet', 'completed'], ['finish', 'completed'], ['заверш', 'completed'],
  ['законч', 'completed'], ['окончен', 'completed'],
  ['hiatus', 'paused'], ['paus', 'paused'], ['cancel', 'paused'], ['drop', 'paused'],
  ['заморож', 'paused'], ['приостанов', 'paused'], ['призупин', 'paused'],
  ['перерыв', 'paused'], ['брошен', 'paused'], ['покинут', 'paused'], ['отмен', 'paused'],
  ['ongoing', 'ongoing'], ['publish', 'ongoing'], ['releasing', 'ongoing'], ['serializ', 'ongoing'],
  ['онгоинг', 'ongoing'], ['продолжается', 'ongoing'], ['выходит', 'ongoing'],
  ['выпуск', 'ongoing'], ['триває', 'ongoing'],
]

// Picked status text → our fixed vocabulary (first substring hit wins).
function normStatus(s: string): Status | '' {
  const t = String(s).toLowerCase()
  for (const [k, v] of STATUS_MAP) if (t.includes(k)) return v
  return ''
}

const TYPE_MAP: [string, string][] = [
  ['манга', 'manga'], ['manga', 'manga'],
  ['манхва', 'manhwa'], ['manhwa', 'manhwa'], ['вебтун', 'manhwa'], ['webtoon', 'manhwa'],
  ['маньхуа', 'manhua'], ['манхуа', 'manhua'], ['manhua', 'manhua'],
  ['комікс', 'comic'], ['комикс', 'comic'], ['comic', 'comic'],
]

function normType(s: string): string {
  const t = String(s).toLowerCase()
  for (const [k, v] of TYPE_MAP) if (t.includes(k)) return v
  return ''
}

function normYear(s: string): string {
  const m = String(s).match(/(?:19|20)\d{2}/)
  return m ? m[0] : ''
}

export interface CleanFlags { lower: boolean; stripCounts: boolean }

// Default cleanup per field — every pick can override it in the inspector.
// Taxonomy-ish fields (type/status/genres/tags) all lowercase custom values the
// same way; known type/status synonyms map onto the canonical vocabulary first.
export function defaultClean(key: string): CleanFlags {
  if (key === 'genres' || key === 'tags' || key === 'type' || key === 'status') return { lower: true, stripCounts: true }
  if (key === 'authors' || key === 'artists') return { lower: false, stripCounts: true }
  return { lower: false, stripCounts: false }
}

export const LIST_KEYS = ['authors', 'artists', 'characters', 'genres', 'tags']

function normOne(s: string, c: CleanFlags): string {
  let v = c.stripCounts ? stripCount(s) : String(s).replace(/\s+/g, ' ').trim()
  if (c.lower) v = v.toLowerCase()
  return v
}

// Exactly what would be stored for a captured raw value: status → vocabulary,
// year → the year, lists → cleaned + deduped, everything else → cleaned text.
export function captureValue(key: string, raw: string | string[], clean?: CleanFlags): string | string[] {
  const c = clean || defaultClean(key)
  const text = Array.isArray(raw) ? raw.join(' ') : String(raw)
  if (key === 'status') return normStatus(text) || normOne(text, c)
  if (key === 'type') return normType(text) || normOne(text, c)
  if (key === 'year') return normYear(text)
  if (LIST_KEYS.includes(key)) {
    const list = Array.isArray(raw) ? raw : [raw]
    const out: string[] = []
    for (const x of list) {
      let v = normOne(String(x), c)
      // chip helper glyphs sometimes GLUE onto the value ("-+1boy" from vote
      // buttons inside the tag row) — strip the leading junk run. The tail is
      // left alone on purpose: "18+" is a real tag.
      v = v.replace(/^[+\-–—·•|/\s]+/u, '')
      // a value needs at least one letter/digit — chip helper links ("+", "-")
      // and stray punctuation are never tags/authors
      if (v && /[\p{L}\p{N}]/u.test(v) && !out.includes(v)) out.push(v)
    }
    return out
  }
  return normOne(text, c)
}
