<script setup lang="ts">
// BROWSE BY: the library seen through one LIST field — people, studios,
// characters, or a field the user defined. The registry decides what can be an
// axis (only lists have anything to group by), so this view gains an axis the
// moment a field exists. People are not a special case, only the axis that
// carries a role and a favourite mark.
// Middle-click on a work opens its tab in the background.
import { computed, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'
import SearchBox from '../components/SearchBox.vue'
import Pager from '../components/Pager.vue'
import {
  activeFilterCount, axisSurface, buildQuery, filterBy, filterByField, hiddenAxes, openTitle,
  openTitleBackground, setFieldHidden, shownAxes, store, takeAuthorFocus,
} from '../store'
import FilterBlock from '../components/FilterBlock.vue'
import MenuButton from '../components/MenuButton.vue'
import FieldVisibility from '../components/FieldVisibility.vue'
import { api } from '../api'
import { readLocalOne, writeLocalOne } from '../local'
import { coverAt, hueFor, initials as initialsOf, type BrowseGroup } from '../data'

// ---- the axis ----
const axis = computed(() => store.browseAxis)
const groups = ref<BrowseGroup[]>([])
const loading = ref(false)
// the axes menu edits THIS shelf's list — the rail shows the same one
const axisFields = computed(() => shownAxes())
const axesHidden = computed(() => hiddenAxes())
const curAxis = computed(() => axisFields.value.find((f) => f.id === axis.value) ?? axisFields.value[0])
const isPeople = computed(() => curAxis.value?.id === 'authors')

async function load() {
  const id = curAxis.value?.id
  if (!id) return
  loading.value = true
  try {
    // The same selection the library is under — MINUS its text search. The
    // shelf and the facets are visible from here (the rail, the ACTIVE chips),
    // so inheriting them is honest; the library's search box is not on this
    // screen, and this view's own search box means something else entirely.
    const { search: _libSearch, ...selection } = buildQuery()
    groups.value = await api.browse(id, selection)
  } catch (e) { store.error = String(e) } finally { loading.value = false }
}
// `store.titles` is reassigned by every library reload — a filter change, a
// commit, a delete. Riding that one signal keeps the groups fresh without a
// second debounce of its own, and it always arrives AFTER the reload.
watch([() => curAxis.value?.id, () => store.fields.length, () => store.titles],
      load, { immediate: true })

const MODES = ['cards', 'rows'] as const
const mode = ref<(typeof MODES)[number]>(
  readLocalOne('lb.browseMode', MODES, 'rows'))
watch(mode, (v) => writeLocalOne('lb.browseMode', v))

const role = ref<'all' | 'author' | 'artist' | 'both'>('all')
const search = ref('')
// arriving via a person link (library / title page) → land focused on them
const focus = takeAuthorFocus()
if (focus) {
  search.value = focus
  store.ui.auPage = 1
}

// the whole filter sidebar can hide (shared preference with the Library)
const fsideOpen = ref(readLocalOne('lb.fside', ['0', '1'] as const, '1') === '1')
watch(fsideOpen, (v) => writeLocalOne('lb.fside', v ? '1' : '0'))

const favOnly = ref(false)

// A tiny stable hash: the order looks shuffled but does not jump around between
// reloads, which a real random would (and a reshuffle on every refetch reads as
// a bug, not as variety).
function shuffleKey(a: string, b: string): number {
  let h = 2166136261
  for (const ch of a + b) h = Math.imul(h ^ ch.charCodeAt(0), 16777619)
  return h >>> 0
}
function worksOf(g: BrowseGroup) {
  return [...g.works].sort((x, y) => shuffleKey(g.id, x.id) - shuffleKey(g.id, y.id))
}
// (3) a group with four hundred titles must not print four hundred covers into
//     a row; the name still leads to all of them in the library
const ROW_WORKS = 14
function rowWorks(g: BrowseGroup) { return worksOf(g).slice(0, ROW_WORKS) }
function moreWorks(g: BrowseGroup) { return Math.max(0, g.works.length - ROW_WORKS) }

const bySearch = computed(() =>
  groups.value.filter((a) => a.value.toLowerCase().includes(search.value.toLowerCase())))
function matchesFav(a: BrowseGroup): boolean {
  return !favOnly.value || a.fav
}
const list = computed(() => bySearch.value.filter((a) =>
  (!isPeople.value || role.value === 'all' || a.role === role.value) && matchesFav(a)))
const AUTHORS_PAGE = 24
const totalPages = computed(() => Math.max(1, Math.ceil(list.value.length / AUTHORS_PAGE)))
const curPage = computed(() => Math.min(Math.max(1, store.ui.auPage), totalPages.value))
function goPage(v: number) {
  store.ui.auPage = Math.min(Math.max(1, Math.floor(v) || 1), totalPages.value)
  amainEl.value?.scrollTo({ top: 0 })
}
const rangeLabel = computed(() => {
  const n = list.value.length
  if (!n) return `0 ${(curAxis?.value?.label ?? '').toLowerCase()}`
  const a = (curPage.value - 1) * AUTHORS_PAGE + 1
  const b = Math.min(curPage.value * AUTHORS_PAGE, n)
  return `${a}–${b} of ${n} ${(curAxis.value?.label ?? '').toLowerCase()}`
})
const shown = computed(() =>
  list.value.slice((curPage.value - 1) * AUTHORS_PAGE, curPage.value * AUTHORS_PAGE))

// write-through favorite (persisted in the vault's authors.json)
// Optimistic, then authoritative: the server owns the user layer, and a refused
// write must not leave a star lit.
async function toggleAuthorFav(a: BrowseGroup) {
  const next = !a.fav
  a.fav = next
  try {
    const people = await api.setAuthorFav(a.id, next)
    a.fav = people.find((x) => x.id === a.id)?.fav ?? next
  } catch (e) {
    a.fav = !next
    store.error = String(e)
  }
}

// linked counts: each facet is counted with the OTHER filters applied
const roleRows = computed(() => {
  const pool = bySearch.value.filter((a) => matchesFav(a))
  const n = (r: string) => pool.filter((a) => a.role === r).length
  return [
    { v: 'all', label: 'All', n: pool.length },
    { v: 'author', label: 'Authors', n: n('author') },
    { v: 'artist', label: 'Artists', n: n('artist') },
    { v: 'both', label: 'Both', n: n('both') },
  ] as const
})
const favCount = computed(() =>
  bySearch.value.filter((a) => (role.value === 'all' || a.role === role.value) && a.fav).length)

// ---- pagination ----
const amainEl = ref<HTMLElement | null>(null)
watch(() => JSON.stringify([search.value, role.value, favOnly.value]), () => goPage(1))

const roleLabel: Record<string, string> = { author: 'AUTHOR', artist: 'ARTIST', both: 'AUTHOR · ARTIST' }
const initials = initialsOf

</script>

<template>
  <div class="au">
    <div class="lmaincol">
    <div class="head">
        <h1 class="h1">{{ curAxis?.label ?? 'Browse' }}</h1>
        <span class="count">{{ list.length }} of {{ groups.length }}</span>
        <div style="flex:1"></div>
        <SearchBox v-model="search" :width="280"
                   :placeholder="`Search ${(curAxis?.label ?? '').toLowerCase()}…`" />
        <div class="seg">
          <button class="opt" :class="{ on: mode === 'cards' }" title="Cards" @click="mode = 'cards'"><Icon name="grid" :size="15" :sw="1.9" /></button>
          <button class="opt" :class="{ on: mode === 'rows' }" title="Wide rows" @click="mode = 'rows'"><Icon name="detail" :size="15" :sw="1.9" /></button>
        </div>
        <button class="btn" :class="{ acton: fsideOpen }" title="Filters" @click="fsideOpen = !fsideOpen">
          <Icon name="filter" :size="13" :sw="1.9" />Filters
          <span v-if="activeFilterCount()" class="numbadge">{{ activeFilterCount() }}</span>
        </button>
        <MenuButton icon="settings" title="Which fields can be an axis" :width="290">
          <FieldVisibility :title="`AXES ON ${(store.library.shelf || 'all types').toUpperCase()}`"
                           :fields="axisFields" :hidden="axesHidden"
                           note="Only list fields can be an axis, and each shelf keeps its own list — hiding one here leaves the other shelves alone."
                           @set="(id, h) => setFieldHidden(axisSurface(), id, h)" />
        </MenuButton>
    </div>
    <div ref="amainEl" class="amain scroll">
      <div v-if="!list.length" class="emptyv">
        <!-- the view is generic: it says what it is actually browsing -->
        <div class="et">No {{ (curAxis?.label ?? 'groups').toLowerCase() }} match</div>
        <div class="es">
          {{ curAxis?.label ?? 'Groups' }} show up here as you fill them in on titles.
        </div>
      </div>
      <div v-else class="list" :class="mode">
        <!-- CARDS: the name leads, three works say what it is -->
        <template v-if="mode === 'cards'">
          <div v-for="a in shown" :key="a.id" class="gcard">
            <div class="ghead2">
              <span class="gname" :title="`Show ${a.value} in the library`"
                    @click="filterByField(a.field, a.value)">{{ a.value }}</span>
              <button v-if="isPeople" class="iconbtn plain" :title="a.fav ? 'Unfavorite' : 'Favorite'"
                      :style="{ color: a.fav ? 'var(--fav)' : 'var(--tx3)' }" @click="toggleAuthorFav(a)">
                <Icon name="star" :size="14" :sw="1.8" :fill="a.fav ? 'var(--fav)' : 'none'" />
              </button>
            </div>
            <div class="gsub mono">{{ a.titles }} title{{ a.titles === 1 ? '' : 's' }} · {{ a.chapters }} ch.</div>
            <div class="gworks">
              <div v-for="w in a.works.slice(0, 3)" :key="w.id" class="gwork" @click="openTitle(w.id)"
                   @mousedown.middle.prevent="openTitleBackground(w.id)">
                <span class="gcov" :style="w.cover ? { background: `#181a1f url(${coverAt(w.cover, 190)}) center/cover no-repeat` } : { background: hueFor(w.id) }"></span>
                <span class="gwt">{{ w.title }}</span>
              </div>
            </div>
          </div>
        </template>

        <!-- WIDE ROWS: the shape this view already had -->
        <template v-else>
          <div v-for="a in shown" :key="a.id" class="acard">
            <div class="identity">
              <div style="display:flex;align-items:center;gap:13px">
                <span class="avatar">{{ initials(a.value) }}</span>
                <div style="min-width:0">
                  <div class="aname" :title="`Show ${a.value} in the library`"
                       @click="filterByField(a.field, a.value)">{{ a.value }}</div>
                  <div v-if="a.role" class="arole mono">{{ roleLabel[a.role] }}</div>
                </div>
              </div>
              <div v-if="a.topTags.length" class="atags">
                <span class="tlabel">TOP TAGS</span>
                <span v-for="tg in a.topTags" :key="tg" class="atag" :title="`Filter library by ${tg}`" @click="filterBy('tag', tg)">{{ tg }}</span>
              </div>
              <div class="afoot">
                <button v-if="isPeople" class="iconbtn plain" :title="a.fav ? 'Unfavorite' : 'Favorite this author'"
                        :style="{ color: a.fav ? 'var(--fav)' : 'var(--tx3)' }" @click="toggleAuthorFav(a)">
                  <Icon name="star" :size="16" :sw="1.8" :fill="a.fav ? 'var(--fav)' : 'none'" />
                </button>
                <span class="mono" style="font-size:11px;letter-spacing:.05em;color:var(--tx3)">{{ a.titles }} titles · {{ a.chapters }} ch.</span>
              </div>
            </div>
            <div class="works">
              <div class="wrow">
                <div v-for="w in rowWorks(a)" :key="w.id" class="work"
                     :style="w.cover ? { background: `#181a1f url(${coverAt(w.cover, 280)}) center/cover no-repeat` } : { background: hueFor(w.id) }"
                     @click="openTitle(w.id)"
                     @mousedown.middle.prevent="openTitleBackground(w.id)">
                  <span v-if="!w.cover" class="wmono">{{ (w.title[0] || '?').toUpperCase() }}</span>
                  <div class="wtitle">{{ w.title }}</div>
                </div>
                <div v-if="moreWorks(a)" class="wmore" :title="`Show all ${a.titles} in the library`"
                     @click="filterByField(a.field, a.value)">+{{ moreWorks(a) }}</div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
    <div class="lfoot">
      <span class="mono lfinfo">{{ rangeLabel }}</span>
      <div style="flex:1"></div>
      <Pager :page="curPage" :pages="totalPages" @go="goPage" />
    </div>
    </div>

    <FilterBlock v-if="fsideOpen" @close="fsideOpen = false">
      <template #yours>
        <template v-if="isPeople">
          <div class="urow">
            <span class="uname">Role</span>
            <div class="seg text">
              <button v-for="r in roleRows" :key="r.v" class="opt" :class="{ on: role === r.v }"
                      :title="`${r.n}`" @click="role = r.v">{{ r.label }}</button>
            </div>
          </div>
          <div class="urow" @click="favOnly = !favOnly">
            <span class="fbox" :class="{ on: favOnly }"><Icon v-if="favOnly" name="check" :size="9" :sw="3.2" /></span>
            <span class="uname">Only favourite people</span>
            <span class="fcount mono">{{ favCount }}</span>
          </div>
        </template>
      </template>
    </FilterBlock>
  </div>
</template>

<style scoped>
.wmore { flex: none; align-self: stretch; min-width: 54px; display: flex; align-items: center; justify-content: center; border: 1px dashed var(--line); border-radius: 8px; color: var(--tx3); font: 600 12px/1 ui-monospace, monospace; cursor: pointer; }
.wmore:hover { border-color: var(--accent); color: var(--accent); }

/* The two rows this view lends to FilterBlock's YOURS group. Scoped CSS does not
   cross a slot boundary — the markup is ours, so the styling is ours too. The
   grammar is copied from that block on purpose: it has to look like it belongs. */
.urow { display: flex; align-items: center; gap: 9px; padding: 5px 6px; border-radius: 6px; color: var(--tx2); cursor: pointer; }
.urow:hover { background: var(--hover); }
.uname { flex: 1; min-width: 0; font: 500 12px/1.3 system-ui; }

.alabel { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* CARDS: the name leads, three works say what this group is */
.list.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.gcard { border: 1px solid var(--line); border-radius: 10px; background: var(--panel); padding: 14px; display: flex; flex-direction: column; gap: 4px; }
.ghead2 { display: flex; align-items: center; gap: 8px; }
.gname { flex: 1; min-width: 0; font: 600 14px/1.2 system-ui; color: var(--tx); cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gname:hover { color: var(--accent); text-decoration: underline; }
.gsub { font-size: 10.5px; color: var(--tx3); }
.gworks { display: flex; gap: 8px; margin-top: 8px; }
.gwork { width: 95px; flex: none; cursor: pointer; }
.gcov { display: block; width: 95px; height: 143px; border-radius: 6px; border: 1px solid var(--line); }
.gwork:hover .gcov { border-color: var(--accent); }
.gwt { display: block; margin-top: 6px; font: 500 10.5px/1.3 system-ui; color: var(--tx2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.au { display: flex; height: 100%; overflow: hidden; position: relative; }
.lmaincol { flex: 1; min-width: 0; display: flex; flex-direction: column; }

/* the same sidebar shell as the Library, docked right */
.fbox { width: 14px; height: 14px; flex: none; border: 1.5px solid var(--line); border-radius: 4px; display: inline-flex; align-items: center; justify-content: center; }
.fcount { font-size: 10px; color: var(--tx3); flex: none; }

.amain { flex: 1; min-width: 0; min-height: 0; overflow-y: auto; }
.head { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; flex-wrap: nowrap; min-width: 0; align-items: center; gap: 12px; padding: 0 24px; }
.emptyv { padding: 80px 30px; display: flex; flex-direction: column; align-items: center; gap: 8px; }
.et { font: 600 16px/1 system-ui; color: var(--tx); }
.es { font: 400 13px/1 system-ui; color: var(--tx3); }
.list { padding: 20px 24px 24px; display: flex; flex-direction: column; gap: 12px; }
/* footer + pager styles are GLOBAL (styles.css — the chrome line grid) */
.acard { display: flex; gap: 20px; padding: 16px; border: 1px solid var(--line); border-radius: 11px; background: var(--panel); align-items: stretch; }
.identity { flex: 0 1 300px; min-width: 190px; display: flex; flex-direction: column; }
.avatar { width: 56px; height: 56px; flex: none; border-radius: 50%; background: linear-gradient(135deg, #4a4f5e, #24262e); display: flex; align-items: center; justify-content: center; font: 600 17px/1 system-ui; color: #cfd3dc; }
.aname { cursor: pointer; font: 600 16px/1.15 system-ui; color: var(--tx); }
.aname:hover { color: var(--accent); text-decoration: underline; }
.arole { margin-top: 5px; font-size: 8.5px; letter-spacing: .1em; color: var(--tx3); border: 1px solid var(--line); padding: 3px 6px; border-radius: 4px; display: inline-block; }
.atags { margin-top: 12px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.atags .tlabel { font: 700 8.5px/1 ui-monospace, monospace; letter-spacing: .1em; color: var(--tx3); margin-right: 2px; }
.atag { font: 500 10.5px/1 system-ui; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); padding: 4px 8px; border-radius: 5px; cursor: pointer; }
.atag:hover { border-color: var(--accent); color: var(--accent); }
.afoot { margin-top: auto; display: flex; align-items: center; gap: 12px; padding-top: 12px; }
.works { flex: 1; min-width: 0; }
/* breathing room for the hover ring (outline would clip at the box edge),
   and overflow SCROLLS instead of chopping the last cover */
.wrow { display: flex; gap: 12px; overflow-x: auto; padding: 4px; margin: -4px; }
.wrow .work { flex: none; width: 106px; }
.work { position: relative; width: 100%; aspect-ratio: 2/3; border-radius: 6px; overflow: hidden; border: 1px solid var(--line); box-shadow: 0 5px 14px rgba(0,0,0,.4); cursor: pointer; }
.work:hover { outline: 2px solid var(--accent); outline-offset: 1px; }
.wmono { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font: 600 30px/1 Georgia, serif; color: rgba(242,237,230,.15); }
.wtitle { position: absolute; left: 0; right: 0; bottom: 0; padding: 20px 8px 8px; background: linear-gradient(transparent, rgba(9,10,13,.88)); font: 600 11px/1.2 system-ui; color: #f2ede6; }
</style>
