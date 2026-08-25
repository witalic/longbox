<script setup lang="ts">
// Library with linked faceted filters in a RIGHT sidebar. Every facet block has
// a FIXED height with its own scroll and an always-available type-to-filter box,
// so the layout never jumps: rows keep a stable (global) order, values with
// matches come first, zero-count ones sink to the end and dim. The results
// column scrolls independently and is paginated in the footer bar (page memory
// survives view switches). Middle-click opens a title's tab in the background.
import { computed, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'
import SearchBox from '../components/SearchBox.vue'
import FilterBlock from '../components/FilterBlock.vue'
import MenuButton from '../components/MenuButton.vue'
import Stars from '../components/Stars.vue'
import Dropdown from '../components/Dropdown.vue'
import Pager from '../components/Pager.vue'
import {
  store, openTitle, openTitleBackground, openReader, toggleFav, resetFilters,
  activeFilterCount, askConfirm, deleteTitles, goAuthorsFor, setRating,
  opening,
} from '../store'
import { startNewTitle } from '../browser'
import { api } from '../api'
import { ensurePostersFor } from '../frames'
import { readLocalOne, writeLocalOne } from '../local'
import { coverStyle, mediaLabel, orderedChaptersOf, initials, statusColor, type Chapter, type Title } from '../data'

const lib = store.library

// the filter block can hide (a preference shared with the browse view)
const fsideOpen = ref(readLocalOne('lb.fside', ['0', '1'] as const, '1') === '1')
watch(fsideOpen, (v) => writeLocalOne('lb.fside', v ? '1' : '0'))
const activeCount = computed(() => activeFilterCount())
const results = computed(() => store.titles)
const shown = computed(() =>
  results.value.slice((curPage.value - 1) * pageSize.value, curPage.value * pageSize.value))
const total = computed(() => store.total)

interface FOpt { v: string | number; l: string }
const SORT_OPTS: FOpt[] = [{ v: 'updated', l: 'Recently updated' }, { v: 'title', l: 'Title A–Z' }, { v: 'rating', l: 'My rating' }, { v: 'unread', l: 'Unread count' }]
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
  () => JSON.stringify([lib.search, lib.progress, lib.favOnly, lib.minRating, lib.sort,
                        lib.shelf, lib.include, lib.exclude, lib.density]),
  () => goPage(1),
)

function unreadLabel(t: Title): string {
  return t.unread > 0 ? `${t.unread} new` : '—'
}
// The expanded strip previews the FIRST downloaded chapter: its first pages as
// fixed-proportion tiles (2:3), horizontally scrollable when they overflow —
// same cached server-side thumbnails as the title page's pane.
const PREVIEW_PAGES = 10
function orderedChapterRows(t: Title): Chapter[] {
  return orderedChaptersOf(t.chapters, t.chapterOrder)
}
function previewChapter(t: Title): Chapter | undefined {
  // the expanded strip previews PAGES; an episode has no page tiles to show
  return orderedChapterRows(t).find((c) => c.dl && c.kind !== 'video' && c.pages > 0)
}
// A downloaded EPISODE has no page to show, but it is not nothing: the strip
// names it and plays it. (A real poster frame would have to be extracted and
// stored at ingest — the app has no decoder, so it does not pretend to have one.)
// Cutting a frame is the window's job (frames.ts) and it happens once, for the
// episodes actually on screen.
watch(shown, (list) => ensurePostersFor(list), { immediate: true })

function previewEpisodes(t: Title): Chapter[] {
  return orderedChapterRows(t).filter((c) => c.dl && c.kind === 'video').slice(0, PREVIEW_PAGES)
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
            <SearchBox v-model="lib.search" placeholder="Search title, author…" />
            <Dropdown :model-value="lib.sort" :options="SORT_OPTS" @update:model-value="lib.sort = String($event)" />
            <div class="seg">
              <button class="opt" :class="{ on: lib.density === 'grid' }" title="Covers" @click="lib.density = 'grid'"><Icon name="grid" :size="15" :sw="1.9" /></button>
              <button class="opt" :class="{ on: lib.density === 'dense' }" title="Dense list" @click="lib.density = 'dense'"><Icon name="rows" :size="15" :sw="1.9" /></button>
              <button class="opt" :class="{ on: lib.density === 'expanded' }" title="Detailed rows" @click="lib.density = 'expanded'"><Icon name="detail" :size="15" :sw="1.9" /></button>
            </div>
            <button class="btn" :class="{ acton: fsideOpen }" title="Filters" @click="fsideOpen = !fsideOpen">
              <Icon name="filter" :size="13" :sw="1.9" />Filters
              <span v-if="activeCount" class="numbadge">{{ activeCount }}</span>
            </button>
            <MenuButton v-slot="{ close }">
              <button :disabled="!results.length" @click="close(); toggleSelMode()">
                <Icon name="check" :size="14" :sw="2.2" />Select titles…
              </button>
              <button @click="close(); startNewTitle()">
                <Icon name="plus" :size="14" :sw="2.2" />Add title
              </button>
            </MenuButton>
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
              <Icon name="star" :size="14" :sw="1.8" :fill="t.fav ? 'var(--fav)' : 'none'" />
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
            <Icon name="star" :size="14" :sw="1.8" :fill="t.fav ? 'var(--fav)' : 'none'" />
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
                <Icon name="star" :size="16" :sw="1.8" :fill="t.fav ? 'var(--fav)' : 'none'" />
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
            <template v-else-if="previewEpisodes(t).length">
              <div v-for="c in previewEpisodes(t)" :key="c.id" class="pagetile open ep"
                   :class="{ shot: c.poster }"
                   :title="`Play ep. ${c.num}${c.lang ? ' · ' + c.lang : ''}`"
                   @click.stop="selMode ? toggleSel(t.id) : openReader(t.id, c.id, 0)">
                <img v-if="c.poster" :src="api.chapterFramesSrc(t.id, c.id, 'poster', c.stills, 240)" loading="lazy" alt="" />
                <Icon v-else name="film" :size="20" :sw="1.6" />
                <div class="eplbl mono">{{ c.num || '—' }}</div>
                <div class="eplen mono">{{ mediaLabel(c) }}</div>
                <span v-if="c.read === 'unread'" class="pgnew"></span>
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

    <FilterBlock v-if="fsideOpen" @close="fsideOpen = false" />
  </div>
</template>

<style scoped>
.lib { display: flex; height: 100%; overflow: hidden; position: relative; }

/* the search that belongs to the BAND (the block's own search is for fields) */

.lmaincol { flex: 1; min-width: 0; display: flex; flex-direction: column; }

/* results column */
.lmain { flex: 1; min-width: 0; min-height: 0; overflow-y: auto; }
.head { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; flex-wrap: nowrap; min-width: 0; align-items: center; gap: 12px; padding: 0 24px; }
.controls { margin-left: auto; display: flex; align-items: center; gap: 8px; flex-wrap: nowrap; min-width: 0; }
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
.pagetile.ep { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 5px; color: var(--tx3); background: var(--panel2); position: relative; }
.pagetile.ep img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
/* over a frame the label needs its own ground */
.pagetile.ep.shot .eplbl, .pagetile.ep.shot .eplen { position: relative; background: rgba(10,11,13,.72); border-radius: 4px; padding: 2px 6px; }
.pagetile.ep.shot .eplbl { color: #fff; }
.pagetile.ep:hover { color: var(--accent); border-color: var(--accent); }
.eplbl { font: 600 12px/1 ui-monospace, monospace; color: var(--tx); }
.eplen { font-size: 10px; color: var(--tx3); }
.pgempty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font: 600 15px/1 ui-monospace, monospace; color: var(--tx3); opacity: .6; }
.pgnew { position: absolute; top: 5px; right: 5px; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 2px rgba(10,11,14,.6); }
.pgnum { position: absolute; bottom: 4px; right: 5px; font: 700 9px/1 ui-monospace, monospace; color: #fff; background: rgba(0,0,0,.55); padding: 2px 5px; border-radius: 4px; }
</style>
