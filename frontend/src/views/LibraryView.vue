<script setup lang="ts">
// Library with linked faceted filters in a RIGHT sidebar. Every facet block has
// a FIXED height with its own scroll and an always-available type-to-filter box,
// so the layout never jumps: rows keep a stable (global) order, values with
// matches come first, zero-count ones sink to the end and dim. The results
// column scrolls independently and is paginated in the footer bar (page memory
// survives view switches). Middle-click opens a title's tab in the background.
import { computed, reactive, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'
import Stars from '../components/Stars.vue'
import Dropdown from '../components/Dropdown.vue'
import Pager from '../components/Pager.vue'
import {
  store, openTitle, openTitleBackground, openReader, toggleFav, resetFilters,
  toggleFacet, facetState, anyFilterActive, askConfirm, deleteTitles, goAuthorsFor, setRating,
  opening, type FacetKey,
} from '../store'
import { startNewTitle } from '../browser'
import { api } from '../api'
import { isBoolMap, readLocal, readLocalOne, writeLocal, writeLocalOne } from '../local'
import { coverAt, orderedChaptersOf, initials, statusColor, hueFor, type Chapter, type FacetValue, type Title } from '../data'

const lib = store.library
const results = computed(() => store.titles)
const shown = computed(() =>
  results.value.slice((curPage.value - 1) * pageSize.value, curPage.value * pageSize.value))
const total = computed(() => store.total)

interface FOpt { v: string | number; l: string }
const SORT_OPTS: FOpt[] = [{ v: 'updated', l: 'Recently updated' }, { v: 'title', l: 'Title A–Z' }, { v: 'rating', l: 'My rating' }, { v: 'unread', l: 'Unread count' }]
// reading progress — the USER-layer axis, independent of the manga's own status
const progRows = [
  { v: 'all', label: 'All' }, { v: 'unread', label: 'Unread' },
  { v: 'reading', label: 'Reading' }, { v: 'completed', label: 'Completed' },
] as const

const FLAG_LABELS: Record<string, string> = { adult: '18+', ai: 'AI', censored: 'Censored' }
const SECTIONS: { key: FacetKey; label: string; cap: boolean; labels?: Record<string, string> }[] = [
  { key: 'authors', label: 'AUTHOR', cap: false },
  { key: 'characters', label: 'CHARACTERS', cap: false },
  { key: 'types', label: 'TYPE', cap: true },
  { key: 'statuses', label: 'STATUS', cap: true },
  { key: 'flags', label: 'FLAGS', cap: false, labels: FLAG_LABELS },
  { key: 'genres', label: 'GENRES', cap: false },
  { key: 'tags', label: 'TAGS', cap: false },
  { key: 'languages', label: 'LANGUAGE', cap: false },
]
const SEARCH_MIN = 7 // facets longer than this get their type-to-filter box
const facetQuery = reactive<Record<string, string>>({})

// the whole filter sidebar can hide (shared with the Authors view)
const fsideOpen = ref(readLocalOne('lb.fside', ['0', '1'] as const, '1') === '1')
watch(fsideOpen, (v) => writeLocalOne('lb.fside', v ? '1' : '0'))

// sections are COLLAPSED by default; the open set persists across sessions,
// and a collapsed section with an active selection shows a badge
const openSec = reactive<Record<string, boolean>>(
  readLocal('lb.facetOpen', isBoolMap, {}))
watch(openSec, () => writeLocal('lb.facetOpen', { ...openSec }), { deep: true })
function toggleSec(k: string) { openSec[k] = !openSec[k] }
function selCount(key: FacetKey): number {
  return lib.include[key].length + lib.exclude[key].length
}
const PROG_LABEL: Record<string, string> = { unread: 'Unread', reading: 'Reading', completed: 'Completed' }

// The row SET and base order come from the GLOBAL (unfiltered) vocabulary, so
// nothing ever appears/disappears mid-filter; current counts overlay on top.
function rowsFor(key: FacetKey): FacetValue[] {
  const cur = new Map((store.facets[key] ?? []).map((x) => [x.v, x.n]))
  const rows = (store.globalFacets[key] ?? []).map((g) => ({ v: g.v, n: cur.get(g.v) ?? 0 }))
  const seen = new Set(rows.map((r) => r.v))
  for (const v of [...lib.include[key], ...lib.exclude[key]]) {
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
function visibleRows(key: FacetKey): FacetValue[] {
  const rows = orderedRows(key)
  const q = (facetQuery[key] || '').trim().toLowerCase()
  return q ? rows.filter((r) => r.v.toLowerCase().includes(q)) : rows
}
function sectionActive(key: FacetKey): boolean {
  return lib.include[key].length > 0 || lib.exclude[key].length > 0
}
function clearSection(key: FacetKey) {
  lib.include[key] = []
  lib.exclude[key] = []
}

// ---- PAGINATION (hard page limit — the expanded previews are heavy) ----
const lmainEl = ref<HTMLElement | null>(null)
const pageSize = computed(() => (lib.density === 'expanded' ? 12 : lib.density === 'dense' ? 60 : 48))
const totalPages = computed(() => Math.max(1, Math.ceil(results.value.length / pageSize.value)))
const curPage = computed(() => Math.min(Math.max(1, store.ui.libPage), totalPages.value))
function goPage(v: number) {
  store.ui.libPage = Math.min(Math.max(1, Math.floor(v) || 1), totalPages.value)
  lmainEl.value?.scrollTo({ top: 0 })
}
const rangeLabel = computed(() => {
  const n = results.value.length
  if (!n) return '0 titles'
  const a = (curPage.value - 1) * pageSize.value + 1
  const b = Math.min(curPage.value * pageSize.value, n)
  return `${a}–${b} of ${n} titles`
})
// a changed selection (or density) starts from page 1
watch(
  () => JSON.stringify([lib.search, lib.progress, lib.favOnly, lib.minRating, lib.sort, lib.include, lib.exclude, lib.density]),
  () => goPage(1),
)

function unreadLabel(t: Title): string {
  return t.unread > 0 ? `${t.unread} new` : '—'
}
function coverStyle(t: Title, w = 420) {
  return t.cover
    ? { background: `#181a1f url("${coverAt(t.cover, w)}") center/cover no-repeat` }
    : { background: hueFor(t.id) }
}
// The expanded strip previews the FIRST downloaded chapter: its first pages as
// fixed-proportion tiles (2:3), horizontally scrollable when they overflow —
// same cached server-side thumbnails as the title page's pane.
const PREVIEW_PAGES = 10
function orderedChapterRows(t: Title): Chapter[] {
  return orderedChaptersOf(t.chapters, t.chapterOrder)
}
function previewChapter(t: Title): Chapter | undefined {
  return orderedChapterRows(t).find((c) => c.dl && c.pages > 0)
}
function recent(t: Title): Chapter[] {
  return orderedChapterRows(t).slice(0, 8)
}
function pageThumb(t: Title, c: Chapter, index: number): string {
  // cap 1.5 crops webtoon-length pages to the tiles' 2:3 shape (previews only)
  return api.chapterPageSrc(t.id, c.id, index, c.v, 240, 1.5)
}
function pagesOf(t: Title): number {
  return t.chapters.reduce((n, c) => n + (c.pages || 0), 0)
}

// ---- select mode: bulk delete, "all" = everything matching the filters ----
const selMode = ref(false)
const selIds = ref<string[]>([])
function toggleSelMode() {
  selMode.value = !selMode.value
  selIds.value = []
}
function isSel(id: string): boolean {
  return selIds.value.includes(id)
}
function toggleSel(id: string) {
  const i = selIds.value.indexOf(id)
  if (i >= 0) selIds.value.splice(i, 1)
  else selIds.value.push(id)
}
// select-mode-aware card click: toggle instead of navigating
function cardClick(t: Title) {
  if (selMode.value) toggleSel(t.id)
  else void openTitle(t.id)
}
function selectAllFiltered() {
  selIds.value = results.value.map((t) => t.id)
}
async function deleteSelected() {
  const n = selIds.value.length
  if (!n) return
  const ok = await askConfirm({
    title: 'Delete titles', danger: true, okLabel: `Delete ${n}`,
    message: `Delete ${n} title${n === 1 ? '' : 's'} from the library? This removes their files on disk.`,
  })
  if (!ok) return
  await deleteTitles([...selIds.value])
  selIds.value = []
  selMode.value = false
}
</script>

<template>
  <div class="lib">
    <div class="lmaincol">
    <div class="head">
        <h1 class="h1">Library</h1>
        <span class="count">{{ results.length }} of {{ total }} titles</span>
        <div class="controls">
          <template v-if="selMode">
            <span class="selinfo mono">{{ selIds.length }} selected</span>
            <button class="btn ghost" :disabled="selIds.length === results.length" title="Select everything matching the current filters" @click="selectAllFiltered">All {{ results.length }}</button>
            <button class="btn dangerbtn" :disabled="!selIds.length" @click="deleteSelected"><Icon name="x" :size="12" :sw="2.2" />Delete</button>
            <button class="btn ghost" @click="toggleSelMode">Cancel</button>
          </template>
          <template v-else>
            <Dropdown label="SORT" :model-value="lib.sort" :options="SORT_OPTS" @update:model-value="lib.sort = String($event)" />
            <div class="seg">
              <button class="opt" :class="{ on: lib.density === 'grid' }" title="Covers" @click="lib.density = 'grid'"><Icon name="grid" :size="15" :sw="1.9" /></button>
              <button class="opt" :class="{ on: lib.density === 'dense' }" title="Dense list" @click="lib.density = 'dense'"><Icon name="rows" :size="15" :sw="1.9" /></button>
              <button class="opt" :class="{ on: lib.density === 'expanded' }" title="Detailed rows" @click="lib.density = 'expanded'"><Icon name="detail" :size="15" :sw="1.9" /></button>
            </div>
            <button v-if="results.length" class="btn" title="Select titles for bulk actions" @click="toggleSelMode"><Icon name="check" :size="13" :sw="2.2" />Select</button>
            <button class="btn accent" title="Open a fresh draft in the browser's capture panel" @click="startNewTitle()">
              <Icon name="plus" :size="14" :sw="2.2" />Add title
            </button>
          </template>
        </div>
    </div>
    <div ref="lmainEl" class="lmain scroll">
      <!-- LOADING: a big vault takes a moment to compose — show its shape, not a blank page -->
      <div v-if="(store.loading || opening.active) && !results.length" class="skel"
           :class="lib.density === 'grid' ? 'skelgrid' : 'skelrows'">
        <div v-if="opening.active" class="skelnote mono">
          reading the library{{ opening.total ? ` — ${opening.done} of ${opening.total} titles` : '…' }}
        </div>
        <div v-for="i in (lib.density === 'grid' ? 18 : 9)" :key="i" class="skelitem" />
      </div>

      <!-- EMPTY -->
      <div v-else-if="!results.length" class="emptylib">
        <template v-if="store.total === 0">
          <div class="emptyt">Your library is empty</div>
          <div class="emptys">Add your first title to get started.</div>
          <button class="btn accent" @click="startNewTitle()"><Icon name="plus" :size="14" :sw="2.2" />Add title</button>
        </template>
        <template v-else>
          <div class="emptyt">No titles match these filters</div>
          <button class="btn ghost" @click="resetFilters">Clear filters</button>
        </template>
      </div>

      <!-- GRID -->
      <div v-else-if="lib.density === 'grid'" class="grid">
        <div v-for="t in shown" :key="t.id" class="gcard" :class="{ selon: isSel(t.id) }" @click="cardClick(t)"
             @mousedown.middle.prevent="!selMode && openTitleBackground(t.id)">
          <div class="cover" :style="coverStyle(t)">
            <span v-if="!t.cover" class="mono let">{{ t.title[0] }}</span>
            <span v-if="t.unread && !selMode" class="badge">{{ t.unread }} new</span>
            <span v-if="selMode" class="selbox" :class="{ on: isSel(t.id) }"><Icon v-if="isSel(t.id)" name="check" :size="11" :sw="3" /></span>
            <button v-if="!selMode" class="favbtn" :style="{ color: t.fav ? 'var(--fav)' : '#cfd3dc' }" @click.stop="toggleFav(t)">
              <Icon name="heart" :size="14" :fill="t.fav ? 'var(--fav)' : 'none'" />
            </button>
          </div>
          <div>
            <div class="gtitle">{{ t.title }}</div>
            <div class="gmeta">
              <Stars :value="t.rating" :size="12" interactive @set="setRating(t, $event)" />
              <span class="mono type">{{ t.type }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- DENSE -->
      <div v-else-if="lib.density === 'dense'" class="dense">
        <div class="drow head">
          <span v-if="selMode" style="width:22px"></span>
          <span style="width:34px"></span><span style="flex:1.4;min-width:0">TITLE</span>
          <span style="flex:1;min-width:0">AUTHOR</span>
          <span style="width:118px">TYPE · STATUS</span>
          <span style="width:38px;text-align:right">CH</span>
          <span style="width:56px;text-align:right">PAGES</span>
          <span style="width:96px;text-align:right">MY RATING</span>
          <span style="width:64px;text-align:right">UNREAD</span><span style="width:32px"></span>
        </div>
        <div v-for="t in shown" :key="t.id" class="drow" :class="{ selon: isSel(t.id) }" @click="cardClick(t)"
             @mousedown.middle.prevent="!selMode && openTitleBackground(t.id)">
          <span v-if="selMode" class="selbox row" :class="{ on: isSel(t.id) }"><Icon v-if="isSel(t.id)" name="check" :size="10" :sw="3" /></span>
          <span class="thumb" :style="coverStyle(t, 96)"></span>
          <div style="flex:1.4;min-width:0">
            <div class="dt">{{ t.title }}</div>
          </div>
          <span class="dpeople">
            <span v-if="t.authors.length" class="pline">
              <span class="prole mono">STORY</span>
              <a v-for="a in t.authors" :key="a" class="pchip" :title="`Open ${a} in Authors`" @click.stop="goAuthorsFor(a)"><span class="pavatar">{{ initials(a) }}</span>{{ a }}</a>
            </span>
            <span v-if="t.artists.length" class="pline">
              <span class="prole mono">ART</span>
              <a v-for="a in t.artists" :key="a" class="pchip" :title="`Open ${a} in Authors`" @click.stop="goAuthorsFor(a)"><span class="pavatar">{{ initials(a) }}</span>{{ a }}</a>
            </span>
            <span v-if="!t.authors.length && !t.artists.length" style="color:var(--tx3)">—</span>
          </span>
          <span class="dts">
            <span class="mono type">{{ t.type }}</span>
            <span class="sdot" :style="{ background: statusColor(t.status) }"></span><span class="cap">{{ t.status }}</span>
          </span>
          <span class="mono dnum" :style="{ color: t.chapters.length ? 'var(--tx2)' : 'var(--tx3)' }" style="width:38px">{{ t.chapters.length || '—' }}</span>
          <span class="mono dnum" :style="{ color: pagesOf(t) ? 'var(--tx2)' : 'var(--tx3)' }" style="width:56px">{{ pagesOf(t) || '—' }}</span>
          <span style="width:96px;display:flex;justify-content:flex-end"><Stars :value="t.rating" :size="13" interactive @set="setRating(t, $event)" /></span>
          <span class="mono" :style="{ width: '64px', textAlign: 'right', color: t.unread ? 'var(--accent)' : 'var(--tx3)' }">{{ unreadLabel(t) }}</span>
          <button class="iconbtn plain" style="width:32px;height:32px" :style="{ color: t.fav ? 'var(--fav)' : 'var(--tx3)' }" @click.stop="toggleFav(t)">
            <Icon name="heart" :size="14" :fill="t.fav ? 'var(--fav)' : 'none'" />
          </button>
        </div>
      </div>

      <!-- EXPANDED -->
      <div v-else class="exp">
        <div v-for="t in shown" :key="t.id" class="ecard" :class="{ selon: isSel(t.id) }" @click="cardClick(t)"
             @mousedown.middle.prevent="!selMode && openTitleBackground(t.id)">
          <span v-if="selMode" class="selbox row" :class="{ on: isSel(t.id) }"><Icon v-if="isSel(t.id)" name="check" :size="10" :sw="3" /></span>
          <span class="ecover" :style="coverStyle(t, 320)"><span v-if="!t.cover" class="mono">{{ t.title[0] }}</span></span>
          <div class="emeta">
            <div class="etitlerow"><span class="etitle">{{ t.title }}</span><span v-if="t.type" class="etype">{{ t.type }}</span></div>
            <div class="estatus">
              <span class="sdot" :style="{ background: statusColor(t.status) }"></span><span class="cap">{{ t.status }}</span>
              <template v-if="t.authors.length">
                <span class="edot">·</span><span class="prole mono">STORY</span>
                <a v-for="a in t.authors" :key="a" class="pchip" :title="`Open ${a} in Authors`" @click.stop="goAuthorsFor(a)"><span class="pavatar">{{ initials(a) }}</span>{{ a }}</a>
              </template>
              <template v-if="t.artists.length">
                <span class="edot">·</span><span class="prole mono">ART</span>
                <a v-for="a in t.artists" :key="'x' + a" class="pchip" :title="`Open ${a} in Authors`" @click.stop="goAuthorsFor(a)"><span class="pavatar">{{ initials(a) }}</span>{{ a }}</a>
              </template>
              <template v-if="t.year"><span class="edot">·</span><span style="color:var(--tx3)">{{ t.year }}</span></template>
            </div>
            <div class="egenres">
              <span v-for="g in t.genres" :key="g" class="chip">{{ g }}</span>
              <span v-for="tg in t.tags.slice(0, 8)" :key="'t' + tg" class="tagchip">{{ tg }}</span>
              <span v-if="t.tags.length > 8" class="tagchip more">+{{ t.tags.length - 8 }}</span>
            </div>
            <div class="efoot">
              <button class="iconbtn plain" :style="{ color: t.fav ? 'var(--fav)' : 'var(--tx3)' }" @click.stop="toggleFav(t)">
                <Icon name="heart" :size="16" :fill="t.fav ? 'var(--fav)' : 'none'" />
              </button>
              <Stars :value="t.rating" :size="15" interactive @set="setRating(t, $event)" />
              <span class="mono ecounts">{{ t.chapters.length }} ch<template v-if="pagesOf(t)"> · {{ pagesOf(t) }} pg</template></span>
              <span class="mono" :style="{ color: t.unread ? 'var(--accent)' : 'var(--tx3)', fontSize: '11px' }">{{ unreadLabel(t) }}</span>
            </div>
          </div>
          <div class="estrip scroll">
            <template v-if="previewChapter(t)">
              <!-- a page tile opens the READER right at that page (.stop keeps
                   the card's own click from also opening the title view) -->
              <div v-for="i in Math.min(previewChapter(t)!.pages, PREVIEW_PAGES)" :key="i" class="pagetile open"
                   :title="`Read ch. ${previewChapter(t)!.num} from page ${i}`"
                   @click.stop="selMode ? toggleSel(t.id) : openReader(t.id, previewChapter(t)!.id, i - 1)">
                <img :src="pageThumb(t, previewChapter(t)!, i - 1)" loading="lazy" alt="" />
                <div class="pgnum mono">{{ i }}</div>
              </div>
            </template>
            <template v-else>
              <div v-for="c in recent(t)" :key="c.id" class="pagetile"
                   :title="`Chapter ${c.num}${c.lang ? ' · ' + c.lang : ''} · not downloaded`">
                <div class="pgempty mono">{{ c.num || '—' }}</div>
                <span v-if="c.read === 'unread'" class="pgnew"></span>
              </div>
            </template>
          </div>
        </div>
      </div>

    </div>
    <div class="lfoot">
      <span class="mono lfinfo">{{ rangeLabel }}</span>
      <div style="flex:1"></div>
      <Pager :page="curPage" :pages="totalPages" @go="goPage" />
    </div>
    </div>

    <!-- faceted sidebar (right), hideable -->
    <aside v-if="fsideOpen" class="fside">
      <div class="ftop">
        <div class="fsearch">
          <Icon name="search" :size="13" />
          <input v-model="lib.search" class="fsearchin" placeholder="Search title, author…" />
        </div>
        <button class="iconbtn plain fhide" title="Hide the filter panel" @click="fsideOpen = false">
          <Icon name="panel" :size="14" :sw="1.9" />
        </button>
      </div>
      <div class="fbody scroll">

      <div class="fsec">
        <div class="fhead click" @click="toggleSec('reading')">
          <Icon name="chevron" :size="10" :sw="2.4" class="fchev" :style="{ transform: openSec.reading ? '' : 'rotate(-90deg)' }" />
          <span class="flbl">READING</span>
          <span v-if="!openSec.reading && lib.progress !== 'all'" class="fbadge mono">{{ PROG_LABEL[lib.progress] }}</span>
        </div>
        <template v-if="openSec.reading">
          <div v-for="p in progRows" :key="p.v" class="frow" :class="{ in: lib.progress === p.v }" @click="lib.progress = p.v">
            <span class="fbox"><Icon v-if="lib.progress === p.v" name="check" :size="9" :sw="3.2" /></span>
            <span class="fname">{{ p.label }}</span>
          </div>
        </template>
      </div>

      <div class="fsec">
        <div class="fhead click" @click="toggleSec('favorites')">
          <Icon name="chevron" :size="10" :sw="2.4" class="fchev" :style="{ transform: openSec.favorites ? '' : 'rotate(-90deg)' }" />
          <span class="flbl">FAVORITES</span>
          <Icon v-if="!openSec.favorites && lib.favOnly" name="heart" :size="11" fill="var(--fav)" style="color:var(--fav)" />
        </div>
        <template v-if="openSec.favorites">
          <div class="frow" :class="{ in: lib.favOnly }" @click="lib.favOnly = !lib.favOnly">
            <span class="fbox"><Icon v-if="lib.favOnly" name="check" :size="9" :sw="3.2" /></span>
            <span class="fname">Favorites only</span>
            <Icon name="heart" :size="12" :fill="lib.favOnly ? 'var(--fav)' : 'none'" :style="{ color: lib.favOnly ? 'var(--fav)' : 'var(--tx3)' }" />
          </div>
        </template>
      </div>

      <div v-for="s in SECTIONS" :key="s.key" class="fsec">
        <div class="fhead click" @click="toggleSec(s.key)">
          <Icon name="chevron" :size="10" :sw="2.4" class="fchev" :style="{ transform: openSec[s.key] ? '' : 'rotate(-90deg)' }" />
          <span class="flbl">{{ s.label }}</span>
          <span v-if="!openSec[s.key] && selCount(s.key)" class="fbadge mono">{{ selCount(s.key) }}</span>
          <button v-if="sectionActive(s.key)" class="fclear" title="Clear this facet" @click.stop="clearSection(s.key)"><Icon name="x" :size="10" :sw="2.4" /></button>
        </div>
        <template v-if="openSec[s.key]">
          <div v-if="!rowsFor(s.key).length" class="fempty">nothing yet</div>
          <template v-else>
            <div v-if="rowsFor(s.key).length > SEARCH_MIN" class="fsub">
              <Icon name="search" :size="11" />
              <input v-model="facetQuery[s.key]" class="fsubin" :placeholder="`Filter ${s.label.toLowerCase()}…`" />
            </div>
            <!-- FIXED-height block, scroll inside — the sidebar layout never moves -->
            <div class="flist scroll">
              <div v-for="r in visibleRows(s.key)" :key="r.v" class="frow"
                   :class="[facetState(s.key, r.v), { dead: r.n === 0 && facetState(s.key, r.v) === '' }]"
                   :title="facetState(s.key, r.v) === '' ? 'Click: include · again: exclude' : facetState(s.key, r.v) === 'in' ? 'Included — click to exclude' : 'Excluded — click to clear'"
                   @click="toggleFacet(s.key, r.v)">
                <span class="fbox">
                  <Icon v-if="facetState(s.key, r.v) === 'in'" name="check" :size="9" :sw="3.2" />
                  <Icon v-else-if="facetState(s.key, r.v) === 'out'" name="minus" :size="9" :sw="3.2" />
                </span>
                <span class="fname" :class="{ cap: s.cap }">{{ s.labels?.[r.v] ?? r.v }}</span>
                <span class="fcount mono">{{ r.n }}</span>
              </div>
            </div>
          </template>
        </template>
      </div>

      <div class="fsec">
        <div class="fhead click" @click="toggleSec('rating')">
          <Icon name="chevron" :size="10" :sw="2.4" class="fchev" :style="{ transform: openSec.rating ? '' : 'rotate(-90deg)' }" />
          <span class="flbl">MY RATING</span>
          <span v-if="!openSec.rating && lib.minRating" class="fbadge mono">{{ lib.minRating }}+</span>
        </div>
        <template v-if="openSec.rating">
          <div class="fstars">
            <span v-for="i in 5" :key="i" class="fstar" @click="lib.minRating = lib.minRating === i ? 0 : i">
              <Icon name="star" :size="15" :sw="1.5" :fill="i <= lib.minRating ? 'var(--warn)' : 'none'"
                    :style="{ color: i <= lib.minRating ? 'var(--warn)' : 'var(--tx3)' }" />
            </span>
            <span class="frlbl mono">{{ lib.minRating ? `${lib.minRating}+` : 'any' }}</span>
          </div>
        </template>
      </div>

      </div>
      <div class="ffoot">
        <button class="btn ghost fclearall" :disabled="!anyFilterActive()" @click="resetFilters">Clear all filters</button>
      </div>
    </aside>
    <button v-else class="fopen" title="Show the filter panel" @click="fsideOpen = true">
      <Icon name="panel" :size="15" :sw="1.9" />
      <span v-if="anyFilterActive()" class="fopendot" title="Filters are active"></span>
    </button>
  </div>
</template>

<style scoped>
.lib { display: flex; height: 100%; overflow: hidden; }
.lmaincol { flex: 1; min-width: 0; display: flex; flex-direction: column; }

/* faceted sidebar (RIGHT): its own column, never scrolled by the results */
.fside { width: 264px; flex: none; border-left: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; }
.ftop { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; padding: 0 16px; }
.fbody { flex: 1; min-height: 0; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 14px; }
.ffoot { height: 44px; flex: none; border-top: 1px solid var(--line); display: flex; align-items: center; padding: 0 16px; }
.fhide { width: 30px; height: 30px; }
/* collapsed: a slim edge strip that reopens the panel (dot = filters active) */
.fopen { position: relative; flex: none; width: 30px; height: 100%; border: none; border-left: 1px solid var(--line); background: var(--bg2); color: var(--tx3); cursor: pointer; display: flex; flex-direction: column; align-items: center; padding-top: 18px; }
.fopen:hover { color: var(--accent); }
.fopendot { margin-top: 7px; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
/* the search field must READ as the entry point — stronger border, accent focus */
.fsearch { flex: 1; display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 10px; background: var(--panel); border: 1px solid color-mix(in srgb, var(--tx3) 38%, var(--line)); border-radius: 8px; color: var(--tx2); }
.fsearch:focus-within { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accentSoft); }
.fsearchin { border: none; background: transparent; outline: none; color: var(--tx); font: 400 12px/1 system-ui; width: 100%; }
.fsec { display: flex; flex-direction: column; gap: 2px; flex: none; }
.fhead { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.fhead.click { cursor: pointer; padding: 3px 4px; margin: -3px -4px 1px; border-radius: 6px; }
.fhead.click:hover { background: var(--hover); }
.fchev { color: var(--tx2); transition: transform .12s; flex: none; }
.fbadge { font-size: 9px; color: var(--accent); background: var(--accentSoft); padding: 2px 6px; border-radius: 4px; flex: none; }
/* section headers must be visibly headers, not ghost text */
.flbl { font: 700 9.5px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx2); }
.fclear { margin-left: auto; width: 16px; height: 16px; border: none; border-radius: 4px; background: transparent; color: var(--tx3); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
.fclear:hover { background: var(--hover); color: var(--tx); }
.fempty { font: 400 11px/1 system-ui; color: var(--tx3); padding: 2px 0 4px; }
.fsub { display: flex; align-items: center; gap: 6px; padding: 5px 8px; margin: 2px 0 4px; background: var(--panel); border: 1px solid var(--line); border-radius: 6px; color: var(--tx3); flex: none; }
.fsubin { border: none; background: transparent; outline: none; color: var(--tx); font: 400 11px/1 system-ui; width: 100%; }
.flist { max-height: 170px; overflow-y: auto; }
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
.fclearall { justify-content: center; flex: 1; }

/* results column */
.lmain { flex: 1; min-width: 0; min-height: 0; overflow-y: auto; }
.head { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 12px; padding: 0 24px; }
.controls { margin-left: auto; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.sdot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
.type { text-transform: uppercase; letter-spacing: .05em; font-size: 10px; color: var(--tx3); font-weight: 600; }
.cap { text-transform: capitalize; }
/* footer + pager styles are GLOBAL (styles.css — the chrome line grid) */

/* loading state: placeholders in the shape of the density that is coming */
.skel { padding: 20px 24px 24px; }
.skelnote { grid-column: 1 / -1; font-size: 12px; color: var(--tx3); padding-bottom: 4px; }
.skelgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(158px, 1fr)); gap: 20px; }
.skelgrid .skelitem { aspect-ratio: 2 / 3; border-radius: 10px; }
.skelrows { display: flex; flex-direction: column; gap: 8px; }
.skelrows .skelitem { height: 64px; border-radius: 10px; }
.skelitem {
  background: linear-gradient(90deg, var(--panel) 25%, var(--hover) 50%, var(--panel) 75%) 0 0 / 300% 100%;
  animation: skelsweep 1.4s ease-in-out infinite;
}
@keyframes skelsweep { to { background-position: -300% 0; } }
@media (prefers-reduced-motion: reduce) { .skelitem { animation: none; } }

/* empty state */
.emptylib { padding: 80px 30px; display: flex; flex-direction: column; align-items: center; gap: 9px; }
.emptyt { font: 600 16px/1 system-ui; color: var(--tx); }
.emptys { font: 400 13px/1 system-ui; color: var(--tx3); margin-bottom: 6px; }

/* grid */
.grid { padding: 20px 24px 24px; display: grid; grid-template-columns: repeat(auto-fill, minmax(158px, 1fr)); gap: 20px; }
.gcard { cursor: pointer; display: flex; flex-direction: column; gap: 9px; }
.gcard:hover { opacity: .92; }
.cover { position: relative; aspect-ratio: 2/3; border-radius: 8px; overflow: hidden; border: 1px solid var(--line); box-shadow: 0 8px 22px rgba(0,0,0,.4); display: flex; align-items: center; justify-content: center; }
.cover .let { font: 600 40px/1 Georgia, serif; color: rgba(242,237,230,.22); }
.badge { position: absolute; top: 8px; left: 8px; font: 700 10px/1 ui-monospace, monospace; color: var(--accent-ink); background: var(--accent); padding: 4px 7px; border-radius: 6px; }
.favbtn { position: absolute; top: 6px; right: 6px; width: 28px; height: 28px; border: none; border-radius: 7px; background: rgba(10,10,14,.5); backdrop-filter: blur(6px); cursor: pointer; display: flex; align-items: center; justify-content: center; }
.gtitle { font: 600 13px/1.25 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gmeta { margin-top: 4px; display: flex; align-items: center; gap: 7px; }

/* dense */
.dense { padding: 12px 24px 24px; display: flex; flex-direction: column; }
.drow { display: flex; align-items: center; gap: 14px; padding: 8px 12px; border-bottom: 1px solid var(--line); cursor: pointer; }
.drow:hover:not(.head) { background: var(--panel); }
.drow.head { font: 600 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); cursor: default; padding-bottom: 8px; }
.thumb { width: 34px; height: 46px; border-radius: 4px; flex: none; }
.dt { font: 600 13.5px/1.2 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dts { width: 118px; display: flex; align-items: center; gap: 7px; font: 500 11px/1 system-ui; color: var(--tx2); }
.dnum { text-align: right; font-size: 11px; flex: none; }

/* people: Story/Art split, every name a proper CHIP (like the title page) —
   unmistakably clickable, opens the Authors tab focused on that person */
.dpeople { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; font: 400 11.5px/1.3 system-ui; }
.pline { display: flex; align-items: center; gap: 6px; min-width: 0; overflow: hidden; white-space: nowrap; }
.prole { font-size: 8px; letter-spacing: .1em; color: var(--tx3); flex: none; }
.pchip { display: inline-flex; align-items: center; gap: 6px; padding: 2px 8px 2px 2px; border: 1px solid var(--line); border-radius: 999px; background: var(--panel2); color: var(--tx); font: 500 11px/1 system-ui; cursor: pointer; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.pchip:hover { border-color: var(--accent); color: var(--accent); }
.pavatar { width: 16px; height: 16px; border-radius: 50%; background: linear-gradient(135deg, #4a4f5e, #2a2d38); color: #cfd3dc; font: 600 7px/1 system-ui; display: inline-flex; align-items: center; justify-content: center; flex: none; }
.edot { color: var(--tx3); }

/* select mode */
.selinfo { font-size: 11px; color: var(--accent); }
.dangerbtn { color: var(--adult); border-color: color-mix(in srgb, var(--adult) 45%, var(--line)); }
.dangerbtn:hover:not(:disabled) { background: color-mix(in srgb, var(--adult) 12%, transparent); }
.selbox { position: absolute; top: 8px; left: 8px; width: 22px; height: 22px; border-radius: 6px; border: 1.5px solid #fff; background: rgba(10,10,14,.55); backdrop-filter: blur(6px); display: inline-flex; align-items: center; justify-content: center; color: #fff; z-index: 2; }
.selbox.on { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.selbox.row { position: static; width: 18px; height: 18px; flex: none; border: 1.5px solid var(--line); background: var(--panel2); color: var(--accent-ink); align-self: center; }
.selbox.row.on { background: var(--accent); border-color: var(--accent); }
.gcard.selon .cover, .ecard.selon { outline: 2px solid var(--accent); outline-offset: 1px; }
.drow.selon { background: var(--accentSoft); }
.ecounts { font-size: 11px; color: var(--tx3); }
.tagchip { font: 400 10.5px/1 system-ui; color: var(--tx3); border: 1px dashed var(--line); padding: 4px 7px; border-radius: 5px; }
.tagchip.more { border-style: solid; }

/* expanded */
.exp { padding: 16px 24px 24px; display: flex; flex-direction: column; gap: 12px; }
.ecard { display: flex; gap: 20px; padding: 16px; border: 1px solid var(--line); border-radius: 11px; background: var(--panel); cursor: pointer; align-items: stretch; }
.ecard:hover { border-color: #3a4150; }
.ecover { width: 132px; height: 196px; border-radius: 7px; flex: none; display: flex; align-items: center; justify-content: center; font: 600 34px/1 Georgia, serif; color: rgba(242,237,230,.2); }
.emeta { flex: 0 1 300px; min-width: 180px; display: flex; flex-direction: column; }
.etitlerow { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.etitle { font: 600 16px/1.1 system-ui; color: var(--tx); }
.etype { font: 600 9px/1 ui-monospace, monospace; letter-spacing: .06em; color: var(--accent); border: 1px solid var(--accent); padding: 3px 6px; border-radius: 4px; text-transform: uppercase; }
.estatus { margin-top: 6px; display: inline-flex; align-items: center; gap: 6px; font: 500 11px/1 system-ui; color: var(--tx2); }
.egenres { margin-top: 11px; display: flex; flex-wrap: wrap; gap: 5px; }
.chip { font: 500 11px/1 system-ui; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); padding: 4px 8px; border-radius: 5px; }
.efoot { margin-top: auto; display: flex; align-items: center; gap: 12px; padding-top: 8px; }
/* padding+negative margin: the hover ring needs room inside the SCROLL box,
   or the outline gets chopped at the strip's edges (same fix as the authors) */
.estrip { flex: 1; min-width: 0; display: flex; gap: 10px; overflow-x: auto; padding: 4px 4px 8px; margin: -4px; align-items: flex-start; }
/* fixed-proportion page tiles (2:3, never stretched); the strip scrolls
   horizontally when they overflow. Absolute img → layout never depends on load */
.pagetile { flex: none; width: 130px; aspect-ratio: 2/3; position: relative; border-radius: 5px; overflow: hidden; background: var(--panel2); border: 1px solid var(--line); box-shadow: 0 2px 6px rgba(0,0,0,.3); }
.pagetile.open:hover { outline: 2px solid var(--accent); outline-offset: 1px; }
.pagetile img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; object-position: top; display: block; }
.pgempty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font: 600 15px/1 ui-monospace, monospace; color: var(--tx3); opacity: .6; }
.pgnew { position: absolute; top: 5px; right: 5px; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 2px rgba(10,11,14,.6); }
.pgnum { position: absolute; bottom: 4px; right: 5px; font: 700 9px/1 ui-monospace, monospace; color: #fff; background: rgba(0,0,0,.55); padding: 2px 5px; border-radius: 4px; }
</style>
