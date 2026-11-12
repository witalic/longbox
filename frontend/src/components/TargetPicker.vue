<script setup lang="ts">
// The library title a draft captures into, chosen on purpose
// (design/edit-target-picker-mockup.html) — under "Edit existing" when no draft
// is open, and as the switch-target menu of an open one.
//
// A name is not an identity: the manga and the manhwa of the same name, two
// artists' "Untitled". So every row says WHAT the record is and WHOSE, the list
// narrows by type and by any field the library filters by, and a row opens in
// the library to be checked before anything is captured into it.
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api } from '../api'
import {
  altNames, coverStyle, FLAG_LABEL, matchTitles, recordLine, titleKey, type FacetValue, type Facets,
} from '../data'
import {
  blankLibrary, buildQuery, cache, facetFields, openTitle, openTitleBackground, store, visibleFields,
  type LibraryFilters,
} from '../store'
import Icon from './Icon.vue'
import SearchBox from './SearchBox.vue'

const props = defineProps<{
  pageUrl: string   // the live page — ranks the record captured from it first
  pageTitle: string // …and the records whose names it carries
  menu?: boolean    // drawn as the open draft's switch-target menu
}>()
const emit = defineEmits<{ (e: 'pick', id: string): void }>()

const q = ref('')
const qt = computed(() => q.value.trim())

// ---- the selection: a type, and field values ----
//
// The library's own selection shape, so the sidecar answers it with the
// library's own semantics — values of a single-valued field are either/or,
// values of a list narrow together. The type rides in as the shelf.
const sel = reactive<LibraryFilters>(blankLibrary())
const narrowed = computed(() => !!sel.shelf || Object.values(sel.include).some((v) => v.length > 0))

// The records the selection admits, and what each field still holds among
// them: one question to the sidecar, asked again when the library changes
// (a commit replaces the global facets). Both stay null while nothing is
// selected. The typed text is no part of it — the list ranks by similarity,
// which a LIKE cannot, and counts taken by another matcher than the list's
// would contradict it.
const admitted = ref<Set<string> | null>(null)
const fetched = ref<Facets | null>(null)
const counts = computed(() => fetched.value ?? store.globalFacets)
let seq = 0
watch(() => [narrowed.value ? JSON.stringify(buildQuery(sel).f) : '', store.globalFacets] as const, async ([key]) => {
  const mine = ++seq
  if (!key) {
    admitted.value = null
    fetched.value = null
    return
  }
  try {
    const query = buildQuery(sel)
    const [list, facets] = await Promise.all([api.library(query), api.facets(query)])
    if (mine !== seq) return
    cache(list)
    admitted.value = new Set(list.map((t) => t.id))
    fetched.value = facets
  } catch (e) {
    if (mine !== seq) return
    admitted.value = new Set() // nothing under a lit chip, rather than everything
    store.error = e instanceof Error ? e.message : String(e)
  }
})
onBeforeUnmount(() => { seq++ })

// The type row keeps the library's order and every type in it — a type the
// selection empties dims instead of leaving, so the row never jumps.
const typeRows = computed(() => {
  const cur = new Map((counts.value.type ?? []).map((x) => [x.v, x.n]))
  return (store.globalFacets.type ?? []).map((g) => ({ v: g.v, n: cur.get(g.v) ?? 0 }))
})
function pickType(v: string) {
  sel.shelf = sel.shelf === v ? '' : v
}

// The fields the library's filter block offers (the type has its own row).
const filterFields = computed(() => visibleFields('filters', facetFields()).filter((f) => f.id !== 'type'))
function valueLabel(field: string, v: string): string {
  return field === 'flags' ? FLAG_LABEL[v] ?? v : v
}
// type and status are stored folded and shown capitalized
function folded(field: string): boolean {
  return store.fields.find((f) => f.id === field)?.control === 'vocab'
}
function picked(field: string, v: string): boolean {
  return (sel.include[field] ?? []).includes(v)
}
function togglePick(field: string, v: string) {
  const list = sel.include[field] ?? []
  sel.include[field] = picked(field, v) ? list.filter((x) => x !== v) : [...list, v]
}
// What a field can still narrow to: values the selection leaves — sorted, the
// picked ones first so a pick never scrolls out of sight.
function liveValues(field: string, sorted = true): FacetValue[] {
  const on = new Set(sel.include[field] ?? [])
  const rows = (counts.value[field] ?? []).filter((x) => x.n > 0 || on.has(x.v))
  for (const v of on) {
    if (!rows.some((x) => x.v === v)) rows.push({ v, n: 0 })
  }
  return sorted
    ? rows.sort((a, b) => Number(on.has(b.v)) - Number(on.has(a.v)) || b.n - a.n || a.v.localeCompare(b.v))
    : rows
}
const chips = computed(() => filterFields.value.flatMap((f) =>
  (sel.include[f.id] ?? []).map((v) => ({ field: f.id, label: f.label.toUpperCase(), v }))))
function clearSelection() {
  sel.shelf = ''
  sel.include = {}
}

// ---- + Filter: the fields, then the values of one ----
const popOpen = ref(false)
const popField = ref('') // '' = the field list
const popQ = ref('')
const pq = computed(() => popQ.value.trim().toLowerCase())
const popEl = ref<HTMLElement | null>(null)
const addEl = ref<HTMLElement | null>(null)
// Over the list while the popover is open: a click that closes it must not
// also pick the row it landed on.
const scrimEl = ref<HTMLElement | null>(null)
const fieldLabel = (id: string) => filterFields.value.find((f) => f.id === id)?.label ?? id

function openPop(field = '') {
  popOpen.value = true
  popField.value = field
  popQ.value = ''
  void nextTick(() => popEl.value?.querySelector('input')?.focus())
}
function closePop() {
  popOpen.value = false
}
function onOutside(e: PointerEvent) {
  const t = e.target as Node
  if (!popEl.value?.contains(t) && !addEl.value?.contains(t) && !scrimEl.value?.contains(t)) closePop()
}
watch(popOpen, (open) => {
  if (open) document.addEventListener('pointerdown', onOutside, true)
  else document.removeEventListener('pointerdown', onOutside, true)
})
onBeforeUnmount(() => document.removeEventListener('pointerdown', onOutside, true))

const fieldRows = computed(() => filterFields.value
  .map((f) => ({ f, n: liveValues(f.id, false).length }))
  .filter(({ f, n }) => n > 0 && (!pq.value || f.label.toLowerCase().includes(pq.value))))
// one search over both lists: typing at the field list finds values too
const valueHits = computed(() => (!pq.value ? [] : filterFields.value.flatMap((f) =>
  liveValues(f.id, false)
    .filter((x) => valueLabel(f.id, x.v).toLowerCase().includes(pq.value))
    .map((x) => ({ field: f.id, label: f.label.toUpperCase(), ...x }))))
  .sort((a, b) => b.n - a.n).slice(0, 40))
const valueRows = computed(() => liveValues(popField.value)
  .filter((x) => !pq.value || valueLabel(popField.value, x.v).toLowerCase().includes(pq.value)))

// ---- the rows ----
//
// What you are looking at, and what you typed, are two different questions.
// With no query the list ranks by how close each record is to THIS PAGE; typing
// re-ranks by how close it is to what you typed. Either way the comparison runs
// over the title AND its alts, and scores partial hits instead of dropping them
// — the same record is filed under a romaji name as often as not.
type Badge = '' | 'page' | 'match' | 'near'
const RANK: Record<Badge, number> = { page: 0, match: 1, near: 2, '': 3 }
// Canonical page identity: host (no www) + decoded path — protocol, query,
// hash and trailing slashes never make "the same page" look different.
function canon(u: string): string {
  try {
    const x = new URL(u)
    return x.hostname.replace(/^www\./, '').toLowerCase()
      + decodeURIComponent(x.pathname).replace(/\/$/, '')
  } catch { return u }
}
const rows = computed(() => {
  const ql = qt.value.toLowerCase()
  const qk = titleKey(qt.value)
  // as the matcher compares names: case and punctuation do not count
  const named = (n: string) => n.toLowerCase().includes(ql) || (!!qk && titleKey(n).includes(qk))
  const page = props.pageUrl ? canon(props.pageUrl) : ''
  const all = Object.values(store.byId)
  const scored = new Map<string, number>()
  for (const { t, score } of matchTitles(qt.value || props.pageTitle, all, 0.34, 999)) {
    scored.set(t.id, score)
  }
  return all
    .filter((t) => !admitted.value || admitted.value.has(t.id))
    .map((t) => {
      const score = scored.get(t.id) ?? 0
      let badge: Badge = ''
      if (page && t.source.url && canon(t.source.url) === page) badge = 'page'
      else if (score >= 0.99) badge = 'match'
      else if (score > 0) badge = 'near'
      // the hit came from another name: that name is what the row should show
      const aka = ql && !named(t.title) ? altNames(t.alt).find(named) ?? '' : ''
      return { t, badge, score, aka }
    })
    .filter(({ t, score, aka }) => {
      if (!ql) return true
      // a typed query also matches plainly: a person's name, part of a word
      return score > 0 || aka !== '' || t.title.toLowerCase().includes(ql)
        || t.authors.some((a) => a.toLowerCase().includes(ql))
    })
    .sort((a, b) => RANK[a.badge] - RANK[b.badge] || b.score - a.score
      || a.t.title.localeCompare(b.t.title))
})
// a short list beats a wall: page hits + best matches first, search digs deeper
const LIMIT = 12
const shown = computed(() => rows.value.slice(0, LIMIT))
const hiddenCount = computed(() => Math.max(0, rows.value.length - LIMIT))
const total = computed(() => Object.keys(store.byId).length)

const BADGE_LABEL: Record<string, string> = { page: 'THIS PAGE', match: 'SAME NAME' }
// a near hit says HOW near, because "maybe this one" is only useful with a number
const badgeText = (r: { badge: Badge; score: number }) =>
  r.badge === 'near' ? `${Math.round(r.score * 100)}% MATCH` : BADGE_LABEL[r.badge] ?? ''

// The typed text, marked wherever it occurs. A string whose lowercase is a
// different length (a dotted İ) cannot be cut by the same offsets — it shows
// unmarked rather than marked in the wrong place.
function parts(text: string, query = qt.value): { t: string; hit: boolean }[] {
  const ql = query.toLowerCase()
  const low = text.toLowerCase()
  if (!ql || low.length !== text.length) return [{ t: text, hit: false }]
  const out: { t: string; hit: boolean }[] = []
  let i = 0
  for (let j = low.indexOf(ql); j >= 0; j = low.indexOf(ql, i)) {
    if (j > i) out.push({ t: text.slice(i, j), hit: false })
    out.push({ t: text.slice(j, j + ql.length), hit: true })
    i = j + ql.length
  }
  if (i < text.length) out.push({ t: text.slice(i), hit: false })
  return out
}
</script>

<template>
  <div class="tp" :class="{ switcher: menu }">
    <div v-if="!menu" class="tplbl">
      EDIT EXISTING<span class="tpcount">{{ rows.length === total ? total : `${rows.length} OF ${total}` }}</span>
    </div>
    <div class="tpline"><SearchBox v-model="q" placeholder="Search title, alt name, author…" /></div>
    <div v-if="typeRows.length > 1" class="tpchips">
      <button v-for="r in typeRows" :key="r.v" class="tpchip" :class="{ on: sel.shelf === r.v, zero: !r.n }"
              @click="pickType(r.v)">
        <span class="cap">{{ r.v }}</span><span class="n">{{ r.n }}</span>
      </button>
    </div>
    <div class="tpchips">
      <button ref="addEl" class="tpchip add" :class="{ open: popOpen }" @click="popOpen ? closePop() : openPop()">
        <Icon name="filter" :size="11" :sw="2" />Filter
      </button>
      <span v-for="c in chips" :key="`${c.field}:${c.v}`" class="tpchip f">
        <span class="k">{{ c.label }}</span><span :class="{ cap: folded(c.field) }">{{ valueLabel(c.field, c.v) }}</span>
        <button class="x" title="Remove" @click="togglePick(c.field, c.v)"><Icon name="x" :size="9" :sw="2.6" /></button>
      </span>
      <button v-if="narrowed" class="tpclear" @click="clearSelection">Clear</button>
    </div>

    <div class="tpbody">
      <div class="tplist scroll">
        <div v-for="r in shown" :key="r.t.id" class="tprow" :class="{ hit: r.badge }" @click="emit('pick', r.t.id)">
          <span class="tpcov" :style="coverStyle(r.t, 64)"></span>
          <div class="tpmain">
            <div class="tpl1">
              <span class="tpname"><template v-for="(p, i) in parts(r.t.title)" :key="i"><mark v-if="p.hit">{{ p.t }}</mark><template v-else>{{ p.t }}</template></template></span>
              <span v-if="r.badge" class="tpbadge" :class="r.badge">{{ badgeText(r) }}</span>
            </div>
            <div class="tpl2">
              <span v-if="r.t.type" class="tptype">{{ r.t.type }}</span>
              <span v-if="r.t.authors.length" class="tpwho"><template v-for="(p, i) in parts(r.t.authors.join(', '))" :key="i"><mark v-if="p.hit">{{ p.t }}</mark><template v-else>{{ p.t }}</template></template></span>
              <span v-else class="tpwho none">no author</span>
            </div>
            <div v-if="r.aka" class="tpl3">aka <template v-for="(p, i) in parts(r.aka)" :key="i"><mark v-if="p.hit">{{ p.t }}</mark><template v-else>{{ p.t }}</template></template></div>
            <div v-else class="tpl3">{{ recordLine(r.t, r.t.year) }}</div>
          </div>
          <button class="tpopen" title="Open in the library — middle-click: in the background"
                  @click.stop="openTitle(r.t.id)" @mousedown.middle.prevent.stop="openTitleBackground(r.t.id)">
            <Icon name="library" :size="13" :sw="1.9" />
          </button>
        </div>
        <div v-if="hiddenCount" class="tpnone">+ {{ hiddenCount }} more — search or filter to narrow down.</div>
        <div v-if="!rows.length" class="tpnone">No titles match.</div>
      </div>

      <div v-if="popOpen" ref="scrimEl" class="tpscrim" @click="closePop"></div>
      <div v-if="popOpen" ref="popEl" class="fpop" @keydown.esc.stop="closePop">
        <div class="fhead">
          <template v-if="popField">
            <button class="fback" title="All fields" @click="openPop()"><Icon name="back" :size="13" :sw="2.2" /></button>
            <span class="fttl">{{ fieldLabel(popField).toUpperCase() }}</span>
          </template>
          <span v-else class="fttl solo">FILTER BY</span>
        </div>
        <div class="tpline fsearch">
          <SearchBox v-model="popQ" :placeholder="popField ? `Search ${fieldLabel(popField).toLowerCase()}…` : 'Search fields and values…'" />
        </div>
        <div class="flist scroll">
          <template v-if="!popField">
            <div v-for="r in fieldRows" :key="r.f.id" class="frow" @click="openPop(r.f.id)">
              {{ r.f.label }}<span class="n">{{ r.n }}</span><Icon name="forward" :size="12" :sw="2" />
            </div>
            <template v-if="valueHits.length">
              <div class="fsec">VALUES</div>
              <div v-for="h in valueHits" :key="`${h.field}:${h.v}`" class="frow" :class="{ on: picked(h.field, h.v) }"
                   @click="togglePick(h.field, h.v)">
                <span class="k">{{ h.label }}</span>
                <span class="fval" :class="{ cap: folded(h.field) }"><template v-for="(p, i) in parts(valueLabel(h.field, h.v), popQ.trim())" :key="i"><mark v-if="p.hit">{{ p.t }}</mark><template v-else>{{ p.t }}</template></template></span>
                <span class="n">{{ h.n }}</span>
              </div>
            </template>
            <div v-if="!fieldRows.length && !valueHits.length" class="tpnone">Nothing matches.</div>
          </template>
          <template v-else>
            <div v-for="x in valueRows" :key="x.v" class="frow" :class="{ on: picked(popField, x.v) }"
                 @click="togglePick(popField, x.v)">
              <span class="fval" :class="{ cap: folded(popField) }"><template v-for="(p, i) in parts(valueLabel(popField, x.v), popQ.trim())" :key="i"><mark v-if="p.hit">{{ p.t }}</mark><template v-else>{{ p.t }}</template></template></span>
              <span class="n">{{ x.n }}</span>
            </div>
            <div v-if="!valueRows.length" class="tpnone">No values.</div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* the room left under the panel's own lines — from a zero basis, so a long list
   scrolls instead of squeezing the lines above it */
.tp { flex: 1 1 0; align-self: stretch; min-height: 0; display: flex; flex-direction: column; margin-top: 8px; padding-top: 10px; border-top: 1px solid var(--line); }
/* the switch-target menu: a fixed height, so + Filter always has room to open */
.tp.switcher { flex: none; height: min(400px, 55vh); margin: 0; padding: 10px 12px 0; border-top: none; border-bottom: 1px solid var(--line); background: var(--panel); }
.tp.switcher :deep(.sbox), .tp.switcher .tprow.hit { background: var(--bg2); }
.tp.switcher .tprow.hit:hover { background: var(--hover); }
.tplbl { flex: none; display: flex; align-items: center; font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); margin-bottom: 8px; }
.tpcount { margin-left: auto; font-weight: 500; letter-spacing: .04em; }
.tpline { display: flex; flex: none; }
.cap { text-transform: capitalize; }

.tpchips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 8px; flex: none; }
.tpchip { height: 22px; padding: 0 9px; border-radius: 999px; border: 1px solid var(--line); background: transparent; color: var(--tx2); font: 600 11px/1 system-ui; display: inline-flex; align-items: center; gap: 5px; cursor: pointer; white-space: nowrap; }
.tpchip:hover { color: var(--tx); border-color: var(--tx3); }
.tpchip .n { font: 500 11px/1 ui-monospace, monospace; color: var(--tx3); }
.tpchip.on { background: var(--accentSoft); color: var(--accent); border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
.tpchip.on .n { color: var(--accent); }
.tpchip.zero:not(.on) { opacity: .45; }
.tpchip.add { border-style: dashed; color: var(--tx3); }
.tpchip.add.open { border-style: solid; border-color: var(--accent); color: var(--accent); }
.tpchip.f { cursor: default; background: var(--accentSoft); color: var(--tx); border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); padding-right: 3px; }
.tpchip.f .k { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .08em; color: var(--accent); }
.tpchip.f .x { width: 16px; height: 16px; border: none; border-radius: 999px; background: transparent; color: var(--tx3); display: inline-flex; align-items: center; justify-content: center; cursor: pointer; padding: 0; }
.tpchip.f .x:hover { background: var(--hover); color: var(--tx); }
.tpclear { border: none; background: transparent; font: 600 11px/22px system-ui; color: var(--tx3); cursor: pointer; padding: 0 4px; }
.tpclear:hover { color: var(--accent); }

/* the list and the popover share one box: the popover never outgrows it */
.tpbody { position: relative; flex: 1 1 0; min-height: 0; display: flex; margin-top: 8px; }
.tplist { flex: 1; min-width: 0; margin: 0 -6px; padding: 0 6px 10px; }
.tprow { position: relative; display: flex; gap: 10px; padding: 7px 8px; border-radius: 6px; border: 1px solid transparent; cursor: pointer; }
.tprow + .tprow { margin-top: 2px; }
.tprow:hover { background: var(--hover); }
.tprow.hit { background: var(--panel); border-color: var(--line); }
.tprow.hit:hover { background: var(--hover); }
.tpcov { width: 28px; height: 40px; border-radius: 4px; border: 1px solid var(--line); flex: none; }
.tpmain { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; padding-top: 1px; }
.tpl1 { display: flex; align-items: center; gap: 6px; min-width: 0; }
.tpname { font: 500 13px/1.2 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.tpl2 { display: flex; align-items: center; gap: 6px; min-width: 0; padding-right: 28px; font: 400 12px/1.2 system-ui; color: var(--tx2); }
.tptype { flex: none; font: 600 9px/1 ui-monospace, monospace; letter-spacing: .06em; text-transform: uppercase; color: var(--accent); border: 1px solid color-mix(in srgb, var(--accent) 55%, var(--line)); padding: 2px 5px; border-radius: 4px; }
.tpwho { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.tpwho.none { color: var(--tx3); font-style: italic; }
.tpl3 { font: 400 11px/1.2 system-ui; color: var(--tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-right: 28px; }
.tpbadge { margin-left: auto; flex: none; font: 700 8px/1 ui-monospace, monospace; letter-spacing: .08em; padding: 3px 6px; border-radius: 4px; }
.tpbadge.page { color: var(--good); border: 1px solid color-mix(in srgb, var(--good) 45%, var(--line)); }
.tpbadge.match { color: var(--accent); background: var(--accentSoft); }
.tpbadge.near { color: var(--tx2); border: 1px solid var(--line); }
.tpopen { position: absolute; right: 6px; bottom: 6px; width: 24px; height: 24px; border-radius: 6px; border: 1px solid var(--line); background: var(--panel2); color: var(--tx2); display: none; align-items: center; justify-content: center; cursor: pointer; padding: 0; }
.tprow:hover .tpopen { display: inline-flex; }
.tpopen:hover { color: var(--accent); border-color: var(--accent); }
mark { background: color-mix(in srgb, var(--accent) 30%, transparent); color: var(--tx); border-radius: 2px; padding: 0 1px; }
.tpnone { padding: 8px 6px; font: 400 11px/1.4 system-ui; color: var(--tx3); }

.tpscrim { position: absolute; inset: 0; z-index: 4; }
.fpop { position: absolute; top: 0; left: 0; right: 0; max-height: calc(100% - 8px); z-index: 5; display: flex; flex-direction: column; padding: 6px; background: var(--panel2); border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 18px 44px rgba(0, 0, 0, .55); }
.fhead { display: flex; align-items: center; gap: 6px; padding: 2px 2px 6px; flex: none; }
.fback { width: 24px; height: 24px; border-radius: 6px; border: none; background: transparent; color: var(--tx2); display: inline-flex; align-items: center; justify-content: center; cursor: pointer; padding: 0; }
.fback:hover { background: var(--hover); color: var(--tx); }
.fttl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); }
.fttl.solo { padding: 7px 6px; }
.fsearch { margin: 0 2px 6px; }
.flist { min-height: 0; }
.frow { display: flex; align-items: center; gap: 8px; min-height: 28px; padding: 0 8px; border-radius: 6px; font: 500 12px/1.2 system-ui; color: var(--tx2); cursor: pointer; }
.frow:hover { background: var(--hover); color: var(--tx); }
.frow.on { color: var(--accent); }
.frow .k { flex: none; font: 700 9px/1 ui-monospace, monospace; letter-spacing: .08em; color: var(--tx3); }
.frow .fval { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.frow .n { margin-left: auto; flex: none; font: 500 11px/1 ui-monospace, monospace; color: var(--tx3); }
.frow > svg { color: var(--tx3); }
.fsec { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); padding: 8px 8px 5px; }
</style>
