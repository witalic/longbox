<script setup lang="ts">
// THE filter block — one implementation for every view that filters the library.
// It binds to the ONE selection (`store.library`), so what you switch on here is
// what the library, the browse groups and their contents are all under; the chips
// at the top say so without opening a single group.
//
// Layout: ONE search over both lists (fields, and the values of the field you
// picked). Three separate search boxes were three ways to ask one question.
import { computed, reactive, ref, watch } from 'vue'
import Icon from './Icon.vue'
import FieldVisibility from './FieldVisibility.vue'
import {
  activeFilterCount, anyFilterActive, facetFields, facetState, hiddenOf, resetFilters,
  setFieldHidden, store, toggleFacet, visibleFields, type FacetKey,
} from '../store'
import { isBoolMap, isNumMap, readLocal, writeLocal } from '../local'
import type { FacetValue } from '../data'

const emit = defineEmits<{ (e: 'close'): void }>()
const lib = store.library

// reading progress — the USER-layer axis, independent of the manga's own status
const progRows = [
  { v: 'all', label: 'All' }, { v: 'unread', label: 'Unread' },
  { v: 'reading', label: 'Reading' }, { v: 'completed', label: 'Completed' },
] as const

const FLAG_LABELS: Record<string, string> = { adult: '18+', ai: 'AI', censored: 'Censored' }
// The sidebar's reading ORDER is a view preference, not a field list: the set of
// sections comes from the served registry, and anything it gains — a custom
// field — simply appends after the ones spelled out here.
const FACET_ORDER = ['authors', 'characters', 'studio', 'type', 'status', 'flags',
  'genres', 'tags', 'language']
const VALUE_LABELS: Record<string, Record<string, string>> = { flags: FLAG_LABELS }
// A selection bag only holds the fields actually filtered on, so a section that
// has never been touched — or a field added since — reads as empty, not as a crash.
function picked(bag: Record<string, string[]>, key: FacetKey): string[] {
  return bag[key] ?? []
}
const SECTIONS = computed(() => [...visibleFields('filters', facetFields())]
  .filter((f) => f.id !== 'type') // the shelf in the rail owns this one
  .sort((a, b) => ((FACET_ORDER.indexOf(a.id) + 1) || 99) - ((FACET_ORDER.indexOf(b.id) + 1) || 99))
  .map((f) => ({
    key: f.id as FacetKey,
    label: f.label.toUpperCase(),
    cap: f.control === 'vocab',   // type/status are stored folded, shown capitalized
    labels: VALUE_LABELS[f.id],
  })))
// ---- the filter block ----
//
// ONE search over both lists (fields and the values of the chosen field): three
// separate search boxes were three ways to ask the same question. The FIELDS
// list doubles as the picker — the values below belong to whatever is selected.
const fq = ref('')
const activeField = ref<FacetKey>('')
const curField = computed<FacetKey>(() => {
  const ids = SECTIONS.value.map((x) => x.key)
  return ids.includes(activeField.value) ? activeField.value : (ids[0] ?? '')
})
const curSection = computed(() => SECTIONS.value.find((x) => x.key === curField.value))
const fieldRows = computed(() => {
  const q = fq.value.trim().toLowerCase()
  const list = visibleFields('filters', facetFields()).filter((f) => f.id !== 'type')
  return q ? list.filter((f) => f.label.toLowerCase().includes(q)) : list
})
const fieldCounts = computed(() => Object.fromEntries(
  SECTIONS.value.map((x) => [x.key, selCount(x.key)]).filter(([, n]) => n)))
const curRows = computed(() => {
  if (!curField.value) return []
  const q = fq.value.trim().toLowerCase()
  const rows = orderedRows(curField.value)
  return q ? rows.filter((r) => r.v.toLowerCase().includes(q)) : rows
})

// What is switched on right now, readable without opening a single group.
const activeChips = computed(() => SECTIONS.value.flatMap((x) => [
  ...picked(lib.include, x.key).map((v) => ({ key: x.key, v, label: x.labels?.[v] ?? v, on: 'inc' })),
  ...picked(lib.exclude, x.key).map((v) => ({ key: x.key, v, label: x.labels?.[v] ?? v, on: 'exc' })),
]))
const activeCount = computed(() => activeFilterCount())
function dropValue(key: FacetKey, v: string) {
  lib.include[key] = picked(lib.include, key).filter((x) => x !== v)
  lib.exclude[key] = picked(lib.exclude, key).filter((x) => x !== v)
}

// Each group keeps its own height; the rule between them is the handle.
const gh = reactive<Record<string, number>>(
  readLocal('lb.filterHeights', isNumMap, { active: 72, fields: 132 }))
watch(gh, () => writeLocal('lb.filterHeights', { ...gh }), { deep: true })
function startDrag(key: string, e: PointerEvent) {
  const startY = e.clientY
  const startH = gh[key] ?? 72
  const move = (ev: PointerEvent) => { gh[key] = Math.max(40, Math.min(320, startH + ev.clientY - startY)) }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

// the whole filter sidebar can hide (shared with the Authors view)

const openSec = reactive<Record<string, boolean>>({
  // ACTIVE and FIELDS earn their space; YOURS says what it is set to in its own
  // header, so it opens only when you go there. Defaults FIRST, then what you
  // last left — an undefined here would make the first toggle a no-op.
  active: true, yours: false, fields: true,
  ...readLocal('lb.facetOpen', isBoolMap, {}),
})
watch(openSec, () => writeLocal('lb.facetOpen', { ...openSec }), { deep: true })
function toggleSec(k: string) { openSec[k] = !openSec[k] }
function selCount(key: FacetKey): number {
  return picked(lib.include, key).length + picked(lib.exclude, key).length
}
const PROG_LABEL: Record<string, string> = { unread: 'Unread', reading: 'Reading', completed: 'Completed' }

// The row SET and base order come from the GLOBAL (unfiltered) vocabulary, so
// nothing ever appears/disappears mid-filter; current counts overlay on top.
function rowsFor(key: FacetKey): FacetValue[] {
  const cur = new Map((store.facets[key] ?? []).map((x) => [x.v, x.n]))
  const rows = (store.globalFacets[key] ?? []).map((g) => ({ v: g.v, n: cur.get(g.v) ?? 0 }))
  const seen = new Set(rows.map((r) => r.v))
  for (const v of [...picked(lib.include, key), ...picked(lib.exclude, key)]) {
    if (!seen.has(v)) rows.push({ v, n: cur.get(v) ?? 0 })
  }
  return rows
}
// SELECTED values (included AND excluded) pin to the top of the block, then
// live unselected ones, then dead zeros — a toggled value is always in sight.
function orderedRows(key: FacetKey): FacetValue[] {
  const rows = rowsFor(key)
  const sel = rows.filter((r) => facetState(key, r.v) !== '')
  const live = rows.filter((r) => facetState(key, r.v) === '' && r.n > 0)
  const dead = rows.filter((r) => facetState(key, r.v) === '' && r.n === 0)
  return [...sel, ...live, ...dead]
}
function sectionActive(key: FacetKey): boolean {
  return picked(lib.include, key).length > 0 || picked(lib.exclude, key).length > 0
}
// Hiding a facet must not leave it filtering from off-screen: its picks go too.
function hideFacet(id: string, hidden: boolean) {
  setFieldHidden('filters', id, hidden)
  if (hidden) clearSection(id as FacetKey)
}
function clearSection(key: FacetKey) {
  lib.include[key] = []
  lib.exclude[key] = []
}

</script>

<template>
    <!-- FILTERS: one block under its own button, not a docked column -->
    <div class="fpop">
      <div class="fptop">
        <div class="fsearch">
          <Icon name="search" :size="13" />
          <input v-model="fq" class="fsearchin" placeholder="Search fields and values…" />
        </div>
        <button class="iconbtn plain" title="Close" @click="emit('close')">
          <Icon name="x" :size="14" :sw="2.2" />
        </button>
      </div>

      <!-- ACTIVE: what is switched on, without opening anything -->
      <div class="fgh" @click="toggleSec('active')">
        <Icon name="chevron" :size="10" :sw="2.4" :style="{ transform: openSec.active ? '' : 'rotate(-90deg)' }" />
        <span class="fglbl">ACTIVE</span>
        <span v-if="activeCount" class="numbadge">{{ activeCount }}</span>
        <div style="flex:1"></div>
        <button v-if="anyFilterActive()" class="ftxt" @click.stop="resetFilters">Clear all</button>
      </div>
      <template v-if="openSec.active">
        <div class="fchips scroll" :style="{ height: `${gh.active}px` }">
          <span v-if="!activeCount" class="fnone">nothing filtered yet</span>
          <span v-for="c in activeChips" :key="c.key + c.v" class="chip" :class="c.on"
                :title="c.on === 'inc' ? 'Included — click to drop' : 'Excluded — click to drop'"
                @click="dropValue(c.key, c.v)">
            <span class="cv">{{ c.label }}</span>
            <Icon name="x" :size="9" :sw="2.6" />
          </span>
          <span v-if="lib.progress !== 'all'" class="chip inc" @click="lib.progress = 'all'">
            <span class="cv">{{ PROG_LABEL[lib.progress] }}</span><Icon name="x" :size="9" :sw="2.6" />
          </span>
          <span v-if="lib.favOnly" class="chip inc" @click="lib.favOnly = false">
            <span class="cv">Favourites</span><Icon name="x" :size="9" :sw="2.6" />
          </span>
          <span v-if="lib.minRating" class="chip inc" @click="lib.minRating = 0">
            <span class="cv">{{ lib.minRating }}+</span><Icon name="x" :size="9" :sw="2.6" />
          </span>
        </div>
        <div class="grab" title="Drag to resize" @pointerdown.prevent="startDrag('active', $event)"></div>
      </template>

      <!-- YOURS: the user layer. Not registry fields, so not in the list below -->
      <div class="fgh" @click="toggleSec('yours')">
        <Icon name="chevron" :size="10" :sw="2.4" :style="{ transform: openSec.yours ? '' : 'rotate(-90deg)' }" />
        <span class="fglbl">YOURS</span>
      </div>
      <div v-if="openSec.yours" class="fyours">
        <div class="urow" @click="lib.favOnly = !lib.favOnly">
          <span class="fbox" :class="{ on: lib.favOnly }"><Icon v-if="lib.favOnly" name="check" :size="9" :sw="3.2" /></span>
          <span class="uname">Only favourites</span>
          <Icon name="star" :size="13" :sw="1.8" :fill="lib.favOnly ? 'var(--fav)' : 'none'"
                :style="{ color: lib.favOnly ? 'var(--fav)' : 'var(--tx3)' }" />
        </div>
        <div class="urow">
          <span class="uname">Reading</span>
          <div class="seg text">
            <button v-for="pr in progRows" :key="pr.v" class="opt" :class="{ on: lib.progress === pr.v }"
                    @click="lib.progress = pr.v">{{ pr.label }}</button>
          </div>
        </div>
        <div class="urow">
          <span class="uname">Rating</span>
          <span class="fstars">
            <span v-for="i in 5" :key="i" class="fstar" @click="lib.minRating = lib.minRating === i ? 0 : i">
              <Icon name="star" :size="14" :sw="1.5" :fill="i <= lib.minRating ? 'var(--warn)' : 'none'"
                    :style="{ color: i <= lib.minRating ? 'var(--warn)' : 'var(--tx3)' }" />
            </span>
          </span>
          <span class="frlbl mono">{{ lib.minRating ? `${lib.minRating}+` : 'any' }}</span>
        </div>
        <!-- a view may own user-layer rows nobody else has (a person's role,
             a favourite person) — they belong in THIS group, not in a second block -->
        <slot name="yours" />
      </div>

      <!-- FIELDS: the same list that hides them also picks which one you edit -->
      <div class="fgh" @click="toggleSec('fields')">
        <Icon name="chevron" :size="10" :sw="2.4" :style="{ transform: openSec.fields ? '' : 'rotate(-90deg)' }" />
        <span class="fglbl">FIELDS</span>
        <div style="flex:1"></div>
        <span class="fhint">the eye hides one</span>
      </div>
      <template v-if="openSec.fields">
        <div class="ffields" :style="{ height: `${gh.fields}px` }">
          <FieldVisibility title="" :fields="fieldRows" :hidden="hiddenOf('filters', facetFields())"
                           :selected="curField" :counts="fieldCounts"
                           @set="hideFacet" @pick="activeField = $event as FacetKey" />
        </div>
        <div class="grab" title="Drag to resize" @pointerdown.prevent="startDrag('fields', $event)"></div>
      </template>

      <!-- the values of whatever is picked above -->
      <div class="fvalhead">
        <span class="fglbl">{{ curSection?.label ?? '—' }}</span>
        <span class="fhint">{{ curRows.length }} value{{ curRows.length === 1 ? '' : 's' }}</span>
        <div style="flex:1"></div>
        <button v-if="curField && sectionActive(curField)" class="ftxt" @click="clearSection(curField)">Clear</button>
      </div>
      <div class="flist scroll">
        <div v-if="!curRows.length" class="fnone" style="padding: 8px 10px">nothing here</div>
        <div v-for="r in curRows" :key="r.v" class="frow"
             :class="[facetState(curField, r.v), { dead: r.n === 0 && facetState(curField, r.v) === '' }]"
             :title="facetState(curField, r.v) === '' ? 'Click: include · again: exclude' : facetState(curField, r.v) === 'in' ? 'Included — click to exclude' : 'Excluded — click to clear'"
             @click="toggleFacet(curField, r.v)">
          <span class="fbox">
            <Icon v-if="facetState(curField, r.v) === 'in'" name="check" :size="9" :sw="3.2" />
            <Icon v-else-if="facetState(curField, r.v) === 'out'" name="minus" :size="9" :sw="3.2" />
          </span>
          <span class="fname" :class="{ cap: curSection?.cap }">{{ curSection?.labels?.[r.v] ?? r.v }}</span>
          <span class="fcount mono">{{ r.n }}</span>
        </div>
      </div>
    </div>
</template>

<style scoped>
.fpop { position: absolute; right: 16px; top: 50px; bottom: 52px; width: 380px; z-index: 20;
        background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
        box-shadow: 0 22px 50px rgba(0,0,0,.6); display: flex; flex-direction: column; overflow: hidden; }
.fptop { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; padding: 0 10px 0 12px; }
.fsearch { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 10px; background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; color: var(--tx3); }
.fsearch:focus-within { border-color: var(--accent); }
.fsearchin { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: var(--tx); font: 400 12px/1 system-ui; }
.fgh { display: flex; align-items: center; gap: 8px; height: 30px; flex: none; padding: 0 12px; color: var(--tx3); cursor: pointer; }
.fgh:hover { color: var(--tx2); }
.fglbl { font: 700 9.5px/1 ui-monospace, monospace; letter-spacing: .12em; }
.fhint { font: 400 10px/1 system-ui; color: var(--tx3); }
.ftxt { border: none; background: transparent; color: var(--tx3); font: 500 10.5px/1 system-ui; cursor: pointer; padding: 4px 6px; border-radius: 5px; }
.ftxt:hover { background: var(--hover); color: var(--accent); }
.fchips { flex: none; display: flex; flex-wrap: wrap; align-content: flex-start; gap: 5px; padding: 2px 12px 8px; overflow-y: auto; }
.fnone { font: 400 11px/1.4 system-ui; color: var(--tx3); }
.chip { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; cursor: pointer;
        font: 500 11px/1 system-ui; color: var(--tx2); background: var(--panel2);
        border: 1px solid var(--line); padding: 5px 8px; border-radius: 6px; }
.chip:hover { color: var(--tx); }
/* included / excluded — NOT `.in`, which is the app's text-input class */
.chip.inc { border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); color: var(--tx); }
.chip.exc { border-color: color-mix(in srgb, var(--adult) 55%, var(--line)); }
.chip.exc .cv { text-decoration: line-through; }
.cv { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.grab { flex: none; height: 7px; cursor: ns-resize; position: relative; }
.grab::after { content: ''; position: absolute; left: 12px; right: 12px; top: 3px; height: 1px; background: var(--line); }
.grab:hover::after { background: var(--accent); }
.fyours { flex: none; padding: 2px 8px 8px; display: flex; flex-direction: column; gap: 1px; }
.urow { display: flex; align-items: center; gap: 9px; padding: 5px 6px; border-radius: 6px; color: var(--tx2); cursor: pointer; }
.urow:hover { background: var(--hover); }
.uname { flex: 1; min-width: 0; font: 500 12px/1.3 system-ui; }
.ffields { flex: none; overflow: hidden; padding: 0 4px; display: flex; }
.ffields :deep(.fv) { flex: 1; min-width: 0; }
.ffields :deep(.fvlist) { max-height: none; flex: 1; }
.fvalhead { flex: none; height: 32px; display: flex; align-items: center; gap: 8px; padding: 0 12px; border-top: 1px solid var(--line); background: var(--panel2); color: var(--tx2); }
.flist { flex: 1; min-height: 0; overflow-y: auto; padding: 6px 8px 10px; background: var(--panel2); }
.frow { display: flex; align-items: center; gap: 8px; padding: 5px 6px; border-radius: 6px; cursor: pointer; color: var(--tx2); }
.frow:hover { background: var(--hover); color: var(--tx); }
.frow.in { color: var(--tx); }
.frow.out { color: var(--tx3); }
.frow.out .fname { text-decoration: line-through; }
.frow.dead { opacity: .45; }
.fbox { width: 14px; height: 14px; flex: none; border: 1.5px solid var(--line); border-radius: 4px; display: inline-flex; align-items: center; justify-content: center; }
.frow.in .fbox { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.frow.out .fbox { background: var(--adult); border-color: var(--adult); color: #fff; }
.fname { flex: 1; min-width: 0; font: 500 12px/1.3 system-ui; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fname.cap { text-transform: capitalize; }
.fcount { font-size: 10px; color: var(--tx3); flex: none; }
.fstars { display: flex; align-items: center; gap: 2px; padding: 2px 4px; }
.fstar { cursor: pointer; display: flex; padding: 2px; }
.fstar:hover { transform: scale(1.15); }
.frlbl { margin-left: 7px; font-size: 10px; color: var(--tx3); }
</style>
