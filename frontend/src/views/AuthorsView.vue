<script setup lang="ts">
// Authors with the same faceted-sidebar shell as the Library (docked RIGHT):
// fixed-height facet blocks with in-block scroll and an always-available filter
// box; the list itself scrolls independently and is paginated in the footer
// bar. Middle-click on a work opens its tab in the background.
import { computed, reactive, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'
import Pager from '../components/Pager.vue'
import { store, filterBy, openTitle, openTitleBackground, takeAuthorFocus } from '../store'
import { api } from '../api'
import { coverAt, hueFor, initials as initialsOf, type Author } from '../data'

const role = ref<'all' | 'author' | 'artist' | 'both'>('all')
const search = ref('')
// arriving via a person link (library / title page) → land focused on them
const focus = takeAuthorFocus()
if (focus) {
  search.value = focus
  store.ui.auPage = 1
}

// the whole filter sidebar can hide (shared preference with the Library)
const fsideOpen = ref(localStorage.getItem('lb.fside') !== '0')
watch(fsideOpen, (v) => { try { localStorage.setItem('lb.fside', v ? '1' : '0') } catch { /* ignore */ } })

const anyAuthorFilter = computed(() =>
  !!search.value.trim() || role.value !== 'all' || favOnly.value || tagSel.value.length > 0)
function clearAuthorFilters() {
  search.value = ''
  role.value = 'all'
  favOnly.value = false
  tagSel.value = []
}
const favOnly = ref(false)
const tagSel = ref<string[]>([])
const tagQuery = ref('')
const expanded = reactive<Record<string, boolean>>({})
const SEARCH_MIN = 7

const bySearch = computed(() =>
  store.authors.filter((a) => a.name.toLowerCase().includes(search.value.toLowerCase())))
function matchesTags(a: Author): boolean {
  return !tagSel.value.length || tagSel.value.some((t) => a.topTags.includes(t))
}
function matchesFav(a: Author): boolean {
  return !favOnly.value || a.fav
}
const list = computed(() =>
  bySearch.value.filter((a) => (role.value === 'all' || a.role === role.value) && matchesTags(a) && matchesFav(a)))
const AUTHORS_PAGE = 24
const totalPages = computed(() => Math.max(1, Math.ceil(list.value.length / AUTHORS_PAGE)))
const curPage = computed(() => Math.min(Math.max(1, store.ui.auPage), totalPages.value))
function goPage(v: number) {
  store.ui.auPage = Math.min(Math.max(1, Math.floor(v) || 1), totalPages.value)
  amainEl.value?.scrollTo({ top: 0 })
}
const rangeLabel = computed(() => {
  const n = list.value.length
  if (!n) return '0 authors'
  const a = (curPage.value - 1) * AUTHORS_PAGE + 1
  const b = Math.min(curPage.value * AUTHORS_PAGE, n)
  return `${a}–${b} of ${n} authors`
})
const shown = computed(() =>
  list.value.slice((curPage.value - 1) * AUTHORS_PAGE, curPage.value * AUTHORS_PAGE))

// write-through favorite (persisted in the vault's authors.json)
async function toggleAuthorFav(a: Author) {
  a.fav = !a.fav
  try { store.authors = await api.setAuthorFav(a.id, a.fav) } catch (e) { store.error = String(e) }
}

// linked counts: each facet is counted with the OTHER filters applied
const roleRows = computed(() => {
  const pool = bySearch.value.filter((a) => matchesTags(a) && matchesFav(a))
  const n = (r: string) => pool.filter((a) => a.role === r).length
  return [
    { v: 'all', label: 'All', n: pool.length },
    { v: 'author', label: 'Authors', n: n('author') },
    { v: 'artist', label: 'Artists', n: n('artist') },
    { v: 'both', label: 'Both', n: n('both') },
  ] as const
})
const favCount = computed(() =>
  bySearch.value.filter((a) => (role.value === 'all' || a.role === role.value) && matchesTags(a) && a.fav).length)
// STABLE tag rows: the set/order comes from ALL authors (global), counts from
// the current pool; live values first, dead zeros sink to the end and dim.
const tagRows = computed(() => {
  const global = new Map<string, number>()
  for (const a of store.authors) for (const t of a.topTags) global.set(t, (global.get(t) ?? 0) + 1)
  const pool = bySearch.value.filter((a) => (role.value === 'all' || a.role === role.value) && matchesFav(a))
  const cur = new Map<string, number>()
  for (const a of pool) for (const t of a.topTags) cur.set(t, (cur.get(t) ?? 0) + 1)
  const rows = [...global.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([v]) => ({ v, n: cur.get(v) ?? 0 }))
  // selected pinned first, then live, then dead zeros
  const sel = rows.filter((r) => tagSel.value.includes(r.v))
  const live = rows.filter((r) => !tagSel.value.includes(r.v) && r.n > 0)
  const dead = rows.filter((r) => !tagSel.value.includes(r.v) && r.n === 0)
  return [...sel, ...live, ...dead]
})
const visibleTagRows = computed(() => {
  const q = tagQuery.value.trim().toLowerCase()
  return q ? tagRows.value.filter((r) => r.v.toLowerCase().includes(q)) : tagRows.value
})
function toggleTag(v: string) {
  const i = tagSel.value.indexOf(v)
  if (i >= 0) tagSel.value.splice(i, 1)
  else tagSel.value.push(v)
}

// ---- pagination ----
const amainEl = ref<HTMLElement | null>(null)
watch(() => JSON.stringify([search.value, role.value, tagSel.value, favOnly.value]), () => goPage(1))

const roleLabel: Record<string, string> = { author: 'AUTHOR', artist: 'ARTIST', both: 'AUTHOR · ARTIST' }
const initials = initialsOf

// sections COLLAPSED by default, open set shared with the library's sidebar
const openSec = reactive<Record<string, boolean>>((() => {
  try { return JSON.parse(localStorage.getItem('lb.facetOpen') || '{}') } catch { return {} }
})())
watch(openSec, () => {
  try { localStorage.setItem('lb.facetOpen', JSON.stringify(openSec)) } catch { /* ignore */ }
}, { deep: true })
function toggleSec(k: string) { openSec[k] = !openSec[k] }
</script>

<template>
  <div class="au">
    <div class="lmaincol">
    <div class="head">
        <h1 class="h1">Authors</h1>
        <span class="count">{{ list.length }} of {{ store.authors.length }}</span>
    </div>
    <div ref="amainEl" class="amain scroll">
      <div v-if="!list.length" class="emptyv">
        <div class="et">No authors match</div>
        <div class="es">Authors show up here as you add titles.</div>
      </div>
      <div v-else class="list">
        <div v-for="a in shown" :key="a.id" class="acard">
          <div class="identity">
            <div style="display:flex;align-items:center;gap:13px">
              <span class="avatar">{{ initials(a.name) }}</span>
              <div style="min-width:0">
                <div class="aname">{{ a.name }}</div>
                <div class="arole mono">{{ roleLabel[a.role] }}</div>
              </div>
            </div>
            <div v-if="a.topTags.length" class="atags">
              <span class="tlabel">TOP TAGS</span>
              <span v-for="tg in a.topTags" :key="tg" class="atag" :title="`Filter library by ${tg}`" @click="filterBy('tag', tg)">{{ tg }}</span>
            </div>
            <div class="afoot">
              <button class="iconbtn plain" :title="a.fav ? 'Unfavorite' : 'Favorite this author'"
                      :style="{ color: a.fav ? 'var(--fav)' : 'var(--tx3)' }" @click="toggleAuthorFav(a)">
                <Icon name="heart" :size="16" :fill="a.fav ? 'var(--fav)' : 'none'" />
              </button>
              <span class="mono" style="font-size:11px;letter-spacing:.05em;color:var(--tx3)">{{ a.titles }} titles · {{ a.chapters }} ch.</span>
              <div style="flex:1"></div>
              <button class="btn ghost" style="padding:6px 10px" @click="expanded[a.id] = !expanded[a.id]">
                <Icon name="expand" :size="12" :sw="2" />{{ expanded[a.id] ? 'Show less' : `All ${a.works.length}` }}
              </button>
            </div>
          </div>
          <div class="works">
            <div :class="expanded[a.id] ? 'wgrid' : 'wrow'">
              <div v-for="w in a.works" :key="w.id" class="work"
                   :style="w.cover ? { background: `#181a1f url(${coverAt(w.cover, 280)}) center/cover no-repeat` } : { background: hueFor(w.id) }"
                   @click="openTitle(w.id)"
                   @mousedown.middle.prevent="openTitleBackground(w.id)">
                <span v-if="!w.cover" class="wmono">{{ (w.title[0] || '?').toUpperCase() }}</span>
                <div class="wtitle">{{ w.title }}</div>
              </div>
            </div>
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

    <!-- faceted sidebar (right), same shell as the Library, hideable -->
    <aside v-if="fsideOpen" class="fside">
      <div class="ftop">
        <div class="fsearch">
          <Icon name="search" :size="13" />
          <input v-model="search" class="fsearchin" placeholder="Search authors…" />
        </div>
        <button class="iconbtn plain fhide" title="Hide the filter panel" @click="fsideOpen = false">
          <Icon name="panel" :size="14" :sw="1.9" />
        </button>
      </div>
      <div class="fbody scroll">

      <div class="fsec">
        <div class="fhead click" @click="toggleSec('au-role')">
          <Icon name="chevron" :size="10" :sw="2.4" class="fchev" :style="{ transform: openSec['au-role'] ? '' : 'rotate(-90deg)' }" />
          <span class="flbl">ROLE</span>
          <span v-if="!openSec['au-role'] && role !== 'all'" class="fbadge mono">{{ roleRows.find((r) => r.v === role)?.label }}</span>
        </div>
        <template v-if="openSec['au-role']">
          <div v-for="r in roleRows" :key="r.v" class="frow" :class="{ in: role === r.v }" @click="role = r.v">
            <span class="fbox"><Icon v-if="role === r.v" name="check" :size="9" :sw="3.2" /></span>
            <span class="fname">{{ r.label }}</span>
            <span class="fcount mono">{{ r.n }}</span>
          </div>
        </template>
      </div>

      <div class="fsec">
        <div class="fhead click" @click="toggleSec('au-fav')">
          <Icon name="chevron" :size="10" :sw="2.4" class="fchev" :style="{ transform: openSec['au-fav'] ? '' : 'rotate(-90deg)' }" />
          <span class="flbl">FAVORITES</span>
          <Icon v-if="!openSec['au-fav'] && favOnly" name="heart" :size="11" fill="var(--fav)" style="color:var(--fav)" />
        </div>
        <template v-if="openSec['au-fav']">
          <div class="frow" :class="{ in: favOnly }" @click="favOnly = !favOnly">
            <span class="fbox"><Icon v-if="favOnly" name="check" :size="9" :sw="3.2" /></span>
            <span class="fname">Favorites only</span>
            <span class="fcount mono">{{ favCount }}</span>
          </div>
        </template>
      </div>

      <div class="fsec">
        <div class="fhead click" @click="toggleSec('au-tags')">
          <Icon name="chevron" :size="10" :sw="2.4" class="fchev" :style="{ transform: openSec['au-tags'] ? '' : 'rotate(-90deg)' }" />
          <span class="flbl">TOP TAGS</span>
          <span v-if="!openSec['au-tags'] && tagSel.length" class="fbadge mono">{{ tagSel.length }}</span>
          <button v-if="tagSel.length" class="fclear" title="Clear tags" @click.stop="tagSel = []"><Icon name="x" :size="10" :sw="2.4" /></button>
        </div>
        <template v-if="openSec['au-tags']">
          <div v-if="!tagRows.length" class="fempty">nothing yet</div>
          <template v-else>
            <div v-if="tagRows.length > SEARCH_MIN" class="fsub">
              <Icon name="search" :size="11" />
              <input v-model="tagQuery" class="fsubin" placeholder="Filter tags…" />
            </div>
            <div class="flist scroll">
              <div v-for="r in visibleTagRows" :key="r.v" class="frow"
                   :class="{ in: tagSel.includes(r.v), dead: r.n === 0 && !tagSel.includes(r.v) }" @click="toggleTag(r.v)">
                <span class="fbox"><Icon v-if="tagSel.includes(r.v)" name="check" :size="9" :sw="3.2" /></span>
                <span class="fname">{{ r.v }}</span>
                <span class="fcount mono">{{ r.n }}</span>
              </div>
            </div>
          </template>
        </template>
      </div>

      </div>
      <div class="ffoot">
        <button class="btn ghost fclearall" :disabled="!anyAuthorFilter" @click="clearAuthorFilters">Clear all filters</button>
      </div>
    </aside>
    <button v-else class="fopen" title="Show the filter panel" @click="fsideOpen = true">
      <Icon name="panel" :size="15" :sw="1.9" />
      <span v-if="anyAuthorFilter" class="fopendot" title="Filters are active"></span>
    </button>
  </div>
</template>

<style scoped>
.au { display: flex; height: 100%; overflow: hidden; }
.lmaincol { flex: 1; min-width: 0; display: flex; flex-direction: column; }

/* the same sidebar shell as the Library, docked right */
.fside { width: 264px; flex: none; border-left: 1px solid var(--line); display: flex; flex-direction: column; min-height: 0; }
.ftop { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 8px; padding: 0 16px; }
.fbody { flex: 1; min-height: 0; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 14px; }
.ffoot { height: 44px; flex: none; border-top: 1px solid var(--line); display: flex; align-items: center; padding: 0 16px; }
.fhide { width: 30px; height: 30px; }
.fopen { position: relative; flex: none; width: 30px; height: 100%; border: none; border-left: 1px solid var(--line); background: var(--bg2); color: var(--tx3); cursor: pointer; display: flex; flex-direction: column; align-items: center; padding-top: 18px; }
.fopen:hover { color: var(--accent); }
.fopendot { margin-top: 7px; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
.fclearall { justify-content: center; flex: 1; }
.fsearch { flex: 1; display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 10px; background: var(--panel); border: 1px solid color-mix(in srgb, var(--tx3) 38%, var(--line)); border-radius: 8px; color: var(--tx2); }
.fsearch:focus-within { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accentSoft); }
.fsearchin { border: none; background: transparent; outline: none; color: var(--tx); font: 400 12px/1 system-ui; width: 100%; }
.fsec { display: flex; flex-direction: column; gap: 2px; flex: none; }
.fhead { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }
.fhead.click { cursor: pointer; padding: 3px 4px; margin: -3px -4px 1px; border-radius: 6px; }
.fhead.click:hover { background: var(--hover); }
.fchev { color: var(--tx2); transition: transform .12s; flex: none; }
.fbadge { font-size: 9px; color: var(--accent); background: var(--accentSoft); padding: 2px 6px; border-radius: 4px; flex: none; }
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
.frow.dead { opacity: .45; }
.fbox { width: 14px; height: 14px; flex: none; border: 1.5px solid var(--line); border-radius: 4px; display: inline-flex; align-items: center; justify-content: center; }
.frow.in .fbox { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.fname { flex: 1; min-width: 0; font: 500 12px/1.3 system-ui; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fcount { font-size: 10px; color: var(--tx3); flex: none; }

.amain { flex: 1; min-width: 0; min-height: 0; overflow-y: auto; }
.head { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 12px; padding: 0 24px; }
.emptyv { padding: 80px 30px; display: flex; flex-direction: column; align-items: center; gap: 8px; }
.et { font: 600 16px/1 system-ui; color: var(--tx); }
.es { font: 400 13px/1 system-ui; color: var(--tx3); }
.list { padding: 20px 24px 24px; display: flex; flex-direction: column; gap: 12px; }
/* footer + pager styles are GLOBAL (styles.css — the chrome line grid) */
.acard { display: flex; gap: 20px; padding: 16px; border: 1px solid var(--line); border-radius: 11px; background: var(--panel); align-items: stretch; }
.identity { flex: 0 1 300px; min-width: 190px; display: flex; flex-direction: column; }
.avatar { width: 56px; height: 56px; flex: none; border-radius: 50%; background: linear-gradient(135deg, #4a4f5e, #24262e); display: flex; align-items: center; justify-content: center; font: 600 17px/1 system-ui; color: #cfd3dc; }
.aname { font: 600 16px/1.15 system-ui; color: var(--tx); }
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
.wgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(106px, 1fr)); gap: 14px; }
.wrow .work { flex: none; width: 106px; }
.work { position: relative; width: 100%; aspect-ratio: 2/3; border-radius: 6px; overflow: hidden; border: 1px solid var(--line); box-shadow: 0 5px 14px rgba(0,0,0,.4); cursor: pointer; }
.work:hover { outline: 2px solid var(--accent); outline-offset: 1px; }
.wmono { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font: 600 30px/1 Georgia, serif; color: rgba(242,237,230,.15); }
.wtitle { position: absolute; left: 0; right: 0; bottom: 0; padding: 20px 8px 8px; background: linear-gradient(transparent, rgba(9,10,13,.88)); font: 600 11px/1.2 system-ui; color: #f2ede6; }
</style>
