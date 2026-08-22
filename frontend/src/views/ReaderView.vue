<script setup lang="ts">
// The reader. Same tab as the title — just a view swap (Esc / back returns).
// A HIDEABLE right sidebar owns everything: reading settings (collapsible, so
// only the chapter list can remain) and the chapter tree with lang/group
// filters. Progress is instant write-through: opening a chapter marks it
// 'reading', finishing it (last page / Continue / strip bottom) marks it
// 'read'. Pages load FULL-SIZE from the archive (never the capped previews),
// with the next two prefetched.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'
import Dropdown from '../components/Dropdown.vue'
import VideoSurface from '../components/VideoSurface.vue'
import { api } from '../api'
import { closeReader, setRead, store, titleById } from '../store'
import {
  READ_COLOR as readColor, coverAt, filterOptions, formatDuration, groupByNum, hueFor,
  isReadable, mediaLabel, orderedChaptersOf, type Chapter, type Title,
} from '../data'
import { bindingsFor, keyLabel, matches } from '../keys'
import { isRecord, readLocal, readLocalOne, writeLocal, writeLocalOne } from '../local'

const t = computed<Title | undefined>(() => titleById(store.activeTitle))
const chapter = computed<Chapter | undefined>(() =>
  t.value?.chapters.find((c) => c.id === store.reader.chapterId))
const count = computed(() => chapter.value?.pages ?? 0)
// A title can hold BOTH kinds — episodes plus a bonus image set — so what the
// reader shows is decided per chapter, never per title.
const isVideo = computed(() => chapter.value?.kind === 'video')


// ---- settings, remembered PER TITLE (new titles inherit the last used) ----
type Mode = 'pages' | 'strip'
type Fit = 'height' | 'width' | 'custom'
const mode = ref<Mode>('pages')
const fit = ref<Fit>('height')
const widthPx = ref(720)
const margins = ref(0)
const sidebar = ref(true)
const settingsOpen = ref(readLocalOne('lb.readerSettings', ['0', '1'] as const, '1') === '1')
watch(settingsOpen, (v) => writeLocalOne('lb.readerSettings', v ? '1' : '0'))
// the LEFT rail: page thumbnails of the open chapter, click to jump. Hideable
// like the right one; the choice sticks across sessions.
const thumbs = ref(readLocalOne('lb.readerThumbs', ['0', '1'] as const, '0') === '1')
watch(thumbs, (v) => writeLocalOne('lb.readerThumbs', v ? '1' : '0'))

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
let prefsReady = false // don't re-save while restoring
let autoDone = true // long-strip auto-suggest fires only for titles with NO saved prefs
function loadPrefs(id: string) {
  prefsReady = false
  const own = readLocal(`lb.reader.${id}`, isRecord, null as Record<string, unknown> | null)
  const p = own ?? readLocal('lb.reader.default', isRecord, {} as Record<string, unknown>)
  mode.value = p.mode === 'strip' ? 'strip' : 'pages'
  fit.value = p.fit === 'width' || p.fit === 'custom' ? p.fit : 'height'
  widthPx.value = clamp(Number(p.width) || 720, 480, 1400)
  margins.value = clamp(Number(p.margins) || 0, 0, 200)
  autoDone = !!own
  void nextTick(() => { prefsReady = true })
}
watch([mode, fit, widthPx, margins], () => {
  if (!prefsReady || !t.value) return
  const p = { mode: mode.value, fit: fit.value, width: widthPx.value, margins: margins.value }
  writeLocal(`lb.reader.${t.value.id}`, p)
  writeLocal('lb.reader.default', p)
})
watch(() => t.value?.id, (id) => { if (id) loadPrefs(id) }, { immediate: true })

// ---- the chapter list (display order, lang/group filters like the title page) ----
const ordered = computed<Chapter[]>(() =>
  orderedChaptersOf(t.value?.chapters, t.value?.chapterOrder))
const langFilter = ref('all')
const groupFilter = ref('all')
const langOpts = computed(() => filterOptions(ordered.value, (c) => c.lang))
const groupOpts = computed(() => filterOptions(ordered.value, (c) => c.group))
const listRows = computed(() => ordered.value.filter((c) =>
  (langFilter.value === 'all' || c.lang === langFilter.value)
  && (groupFilter.value === 'all' || c.group === groupFilter.value)))
// reading flow ([ ] and Continue follow the ACTIVE filters)
const flow = computed(() => listRows.value.filter(isReadable))
const readable = computed(() => ordered.value.filter(isReadable))

// Advance moves BETWEEN labels, never between translations of one label —
// the next chapter keeps the CURRENT translation (lang+group) when the next
// label has it, else same lang, else its first readable row.
function chapterAt(offset: 1 | -1): Chapter | undefined {
  const rows = flow.value.length ? flow.value : readable.value
  const nodes = groupByNum(rows)
  const cur = chapter.value
  const i = nodes.findIndex((n) => n.num === (cur?.num ?? ''))
  if (i < 0) return offset > 0 ? rows[0] : undefined
  const next = nodes[i + offset]
  if (next === undefined) return undefined
  return next.rows.find((c) => c.lang === cur?.lang && c.group === cur?.group)
    ?? next.rows.find((c) => c.lang === cur?.lang)
    ?? next.rows[0]
}

// the sidebar list, grouped by label (same tree as the title page)
const listTree = computed(() => groupByNum(listRows.value))

function openChapter(c: Chapter, page = 0) {
  if (!isReadable(c)) return
  store.reader.chapterId = c.id
  // an episode has no page index — its position lives in the user layer, and
  // the player restores it; clamping against `pages` (which is 0) would refuse
  // to open the row at all
  store.reader.page = c.kind === 'video' ? 0 : clamp(page, 0, c.pages - 1)
  stripTarget = -1
  stripEl.value?.scrollTo({ top: 0 })
  pagesEl.value?.scrollTo({ top: 0 })
  if (store.reader.page > 0) scrollStripToPage(store.reader.page)
}

// "open AT page N" in strip mode: scroll to that image, and keep RE-anchoring
// as the images above it load in (each load pushes the target further down —
// a single scrollIntoView against zero-height lazy imgs lands at the top)
let stripTarget = -1
function scrollStripToPage(index: number) {
  stripTarget = index
  void nextTick(anchorStrip)
}
function anchorStrip() {
  if (stripTarget < 0) return
  const imgs = [...(stripEl.value?.querySelectorAll<HTMLImageElement>('.page') ?? [])]
  imgs[stripTarget]?.scrollIntoView({ block: 'start' })
  if (imgs.length > stripTarget && imgs.slice(0, stripTarget + 1).every((im) => im.complete)) stripTarget = -1
}
function onStripImgLoad(index: number, e: Event) {
  if (index === 0) onPageLoad(e)
  if (stripTarget >= 0 && index <= stripTarget) anchorStrip()
}

// the tab remembers the reading spot — switching tabs and back re-enters here
watch([() => store.reader.chapterId, () => store.reader.page], () => {
  if (t.value && store.view === 'reader' && store.reader.chapterId) {
    store.readerTabs[t.value.id] = { chapterId: store.reader.chapterId, page: store.reader.page }
  }
})

// entering a chapter (from anywhere) marks it 'reading'; a dead/missing id
// falls back to the first readable chapter
watch([() => t.value?.id, () => store.reader.chapterId], () => {
  const c = chapter.value
  if (!c || !c.dl || !c.pages) {
    const first = readable.value[0]
    if (first && first.id !== store.reader.chapterId) {
      store.reader.chapterId = first.id
      store.reader.page = 0
    }
    return
  }
  if (t.value && c.read === 'unread') void setRead(t.value, c.id, 'reading')
}, { immediate: true })

// ---- page navigation + progress ----
function markCurrentRead() {
  const c = chapter.value
  if (t.value && c && c.read !== 'read') void setRead(t.value, c.id, 'read')
}
function finishAndAdvance() {
  markCurrentRead()
  const n = chapterAt(1)
  if (n) openChapter(n)
}
function nextPage() {
  if (mode.value === 'strip') { scrollStrip(0.9); return }
  if (store.reader.page < count.value - 1) store.reader.page++
  else finishAndAdvance() // explicit press PAST the last page → next chapter
}
function prevPage() {
  if (mode.value === 'strip') { scrollStrip(-0.9); return }
  if (store.reader.page > 0) store.reader.page--
  else {
    const p = chapterAt(-1)
    if (p) openChapter(p, p.pages - 1)
  }
}

function pageSrc(index: number): string {
  const c = chapter.value
  return c && t.value ? api.chapterPageSrc(t.value.id, c.id, index, c.v) : ''
}
// the rail's previews are downscaled thumbnails (never the full-size page);
// cap 1.5 matches the 2:3 tile, so an ordinary page fills it and only a long
// strip is trimmed from the top — the same pairing the gallery uses
function thumbSrc(index: number): string {
  const c = chapter.value
  return c && t.value ? api.chapterPageSrc(t.value.id, c.id, index, c.v, 220, 1.5) : ''
}
// clicking a thumbnail jumps there — a page swap in pages mode, a scroll in strip
function goToPage(index: number) {
  store.reader.page = index
  if (mode.value === 'strip') scrollStripToPage(index)
}
// the rail follows the reading position, whatever moved it
const railEl = ref<HTMLElement | null>(null)
watch([() => store.reader.page, thumbs, chapter], async () => {
  if (!thumbs.value) return
  await nextTick()
  railEl.value?.querySelector('.lthumb.on')?.scrollIntoView({ block: 'nearest' })
}, { immediate: true }) // entering at a remembered page must open ON that thumbnail
// reaching the last page marks the chapter read; the next two pages prefetch
watch([() => store.reader.page, chapter], () => {
  const c = chapter.value
  if (!c) return
  if (store.reader.page >= c.pages - 1) markCurrentRead()
  if (mode.value === 'pages') {
    for (const d of [1, 2]) {
      const i = store.reader.page + d
      if (i < c.pages) new Image().src = pageSrc(i)
    }
  }
  pagesEl.value?.scrollTo({ top: 0 })
}, { immediate: true })

// ---- the stage ----
const pagesEl = ref<HTMLElement | null>(null)
const stripEl = ref<HTMLElement | null>(null)
const stripPct = ref(0)

const pageStyle = computed(() => {
  if (fit.value === 'height') return { maxHeight: '100%', maxWidth: '100%' }
  if (fit.value === 'width') return { width: `calc(100% - ${margins.value * 2}px)` }
  return { width: `${widthPx.value}px`, maxWidth: `calc(100% - ${margins.value * 2}px)` }
})
// in strip mode 'height' makes no sense — read it as full width
const stripStyle = computed(() => (fit.value === 'custom'
  ? { width: `${widthPx.value}px`, maxWidth: `calc(100% - ${margins.value * 2}px)` }
  : { width: `calc(100% - ${margins.value * 2}px)` }))

function onStageClick(e: MouseEvent) {
  const el = e.currentTarget as HTMLElement
  const x = (e.clientX - el.getBoundingClientRect().left) / el.clientWidth
  if (x < 0.33) prevPage()
  else nextPage()
}
function scrollStrip(screens: number) {
  const el = stripEl.value
  if (el) el.scrollBy({ top: el.clientHeight * screens, behavior: 'smooth' })
}
function onStripScroll() {
  const el = stripEl.value
  if (!el) return
  const max = el.scrollHeight - el.clientHeight
  stripPct.value = max > 0 ? Math.round((el.scrollTop / max) * 100) : 100
  if (max > 0 && el.scrollTop >= max - 40) markCurrentRead()
  // the strip has a CURRENT PAGE too: the one filling the viewport. Without
  // this the rail's highlight and the tab's remembered spot stay on page 1.
  const imgs = [...el.querySelectorAll<HTMLImageElement>('img.page')]
  const mid = el.scrollTop + el.clientHeight / 3
  let at = 0
  for (let i = 0; i < imgs.length; i++) {
    if (imgs[i].offsetTop <= mid) at = i
    else break
  }
  if (at !== store.reader.page) store.reader.page = at
}

// a VERY tall first page on a title without saved prefs → suggest the strip
function onPageLoad(e: Event) {
  if (autoDone) return
  autoDone = true
  const img = e.target as HTMLImageElement
  if (img.naturalHeight > img.naturalWidth * 3) {
    mode.value = 'strip'
    fit.value = 'width'
  }
}

// ---- keyboard (bindings live in keys.ts — rebindable in Settings) ----
const FIT_CYCLE: Record<Fit, Fit> = { height: 'width', width: 'custom', custom: 'height' }
function onKey(e: KeyboardEvent) {
  if (store.view !== 'reader') return
  const tag = (e.target as HTMLElement | null)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
  const k = e.key
  if (matches('reader.next', e) || k === 'PageDown') { e.preventDefault(); nextPage() }
  else if (matches('reader.prev', e) || k === 'PageUp') { e.preventDefault(); prevPage() }
  else if (k === 'ArrowDown' && mode.value === 'strip') { e.preventDefault(); scrollStrip(0.25) }
  else if (k === 'ArrowUp' && mode.value === 'strip') { e.preventDefault(); scrollStrip(-0.25) }
  else if (k === 'Home' && mode.value === 'pages') { e.preventDefault(); store.reader.page = 0 }
  else if (k === 'End' && mode.value === 'pages') { e.preventDefault(); if (count.value) store.reader.page = count.value - 1 }
  else if (matches('reader.chapterNext', e)) { const n = chapterAt(1); if (n) openChapter(n) }
  else if (matches('reader.chapterPrev', e)) { const p = chapterAt(-1); if (p) openChapter(p) }
  else if (matches('reader.fit', e)) { fit.value = FIT_CYCLE[fit.value] }
  else if (matches('reader.sidebar', e)) { sidebar.value = !sidebar.value }
  else if (matches('reader.marginsDec', e)) { margins.value = clamp(margins.value - 8, 0, 200) }
  else if (matches('reader.marginsInc', e)) { margins.value = clamp(margins.value + 8, 0, 200) }
  else if (matches('reader.back', e)) { closeReader() }
}
// the sidebar's live hotkey legend follows the (re)bound keys
const legendKeys = (action: string) => bindingsFor(action).map(keyLabel).join(' ')
const keysLegend = computed(() => [
  `${legendKeys('reader.prev')} ${legendKeys('reader.next')} pages`,
  `${legendKeys('reader.chapterPrev')} ${legendKeys('reader.chapterNext')} chapters`,
  `${legendKeys('reader.fit')} fit`,
  `${legendKeys('reader.marginsDec')} ${legendKeys('reader.marginsInc')} margins`,
  `${legendKeys('reader.sidebar')} sidebar`,
  `${legendKeys('reader.back')} back`,
].join(' · '))
onMounted(() => {
  window.addEventListener('keydown', onKey)
  // entered the reader at a specific page (a page-tile click) while in strip mode
  if (mode.value === 'strip' && store.reader.page > 0) scrollStripToPage(store.reader.page)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
// pages → strip keeps the current page in view
watch(mode, (m) => {
  if (m === 'strip' && store.reader.page > 0) scrollStripToPage(store.reader.page)
})

const WIDTH_PRESETS = [600, 720, 900]
</script>

<template>
  <div v-if="t" class="reader">
    <!-- minimal top bar: back · position · sidebar toggle -->
    <div class="rtop">
      <button class="rback" title="Back to the title (Esc)" @click="closeReader">
        <Icon name="back" :size="15" :sw="2.2" />
        <span class="rcover" :style="t.cover ? { background: `#181a1f url('${coverAt(t.cover, 64)}') center/cover` } : { background: hueFor(t.id) }"></span>
        <span class="rtitle">{{ t.title }}</span>
      </button>
      <span v-if="chapter" class="rch mono">ch. {{ chapter.num }}<template v-if="chapter.lang"> · {{ chapter.lang }}</template></span>
      <div style="flex:1"></div>
      <span class="rind mono">{{ isVideo
        ? (chapter?.duration ? formatDuration(chapter.duration) : 'video')
        : (mode === 'pages' ? `${store.reader.page + 1} / ${count}` : `${stripPct}%`) }}</span>
      <button v-if="!isVideo" class="iconbtn rleft" :class="{ on: thumbs }" title="Toggle the page thumbnails" @click="thumbs = !thumbs">
        <Icon name="panel" :size="15" :sw="1.9" />
      </button>
      <button class="iconbtn" :class="{ on: sidebar }" title="Toggle the sidebar (S)" @click="sidebar = !sidebar">
        <Icon name="panel" :size="15" :sw="1.9" />
      </button>
    </div>

    <div class="rbody">
      <!-- LEFT RAIL: the open chapter's pages as thumbnails; click to jump.
           The current page keeps itself in view. -->
      <aside v-if="thumbs && !isVideo" ref="railEl" class="lrail scroll">
        <div v-if="!count" class="rempty" style="font-size:11px;grid-column:1/-1">No pages</div>
        <button v-for="i in count" :key="`th-${chapter?.id}-${i}`" class="lthumb"
                :class="{ on: store.reader.page === i - 1 }" :title="`Page ${i}`" @click="goToPage(i - 1)">
          <img :src="thumbSrc(i - 1)" loading="lazy" alt="" />
          <span class="lnum mono">{{ i }}</span>
        </button>
      </aside>

      <!-- VIDEO: the same shell, a different surface -->
      <VideoSurface v-if="isVideo && t && chapter" :title="t" :chapter="chapter" autoplay />

      <!-- PAGES: one page, click left/right thirds to flip -->
      <div v-else-if="mode === 'pages'" ref="pagesEl" class="stage scroll" :class="{ fith: fit === 'height' }" @click="onStageClick">
        <img v-if="chapter && count" :key="`${chapter.id}-${store.reader.page}`" class="page"
             :style="pageStyle" :src="pageSrc(store.reader.page)" alt="" @load="onPageLoad" />
        <div v-else class="rempty">No downloaded pages in this chapter.</div>
      </div>

      <!-- STRIP: every page stacked, an explicit Continue card at the end -->
      <div v-else-if="!isVideo" ref="stripEl" class="stage strip scroll" @scroll.passive="onStripScroll">
        <template v-if="chapter && count">
          <img v-for="i in count" :key="`${chapter.id}-${i}`" class="page" :style="stripStyle"
               :src="pageSrc(i - 1)" :loading="i - 1 <= store.reader.page + 2 ? 'eager' : 'lazy'"
               alt="" @load="onStripImgLoad(i - 1, $event)" />
          <div class="endcard">
            <div class="endlbl mono">END OF CH. {{ chapter.num }}</div>
            <button v-if="chapterAt(1)" class="btn accent" @click="finishAndAdvance">
              Continue — ch. {{ chapterAt(1)!.num }}<template v-if="chapterAt(1)!.lang"> · {{ chapterAt(1)!.lang }}</template>
            </button>
            <button v-else class="btn ghost" @click="markCurrentRead(); closeReader()">Back to the title</button>
          </div>
        </template>
        <div v-else class="rempty">No downloaded pages in this chapter.</div>
      </div>

      <!-- SIDEBAR (S): settings (collapsible — hide them and only the chapter
           list remains) + the chapter list with lang/group filters -->
      <aside v-if="sidebar" class="rside">
        <div class="rsec">
          <div class="rhead click" @click="settingsOpen = !settingsOpen">
            <Icon name="chevron" :size="10" :sw="2.4" class="rchev" :style="{ transform: settingsOpen ? '' : 'rotate(-90deg)' }" />
            <span class="rlbl">SETTINGS</span>
            <span v-if="!settingsOpen" class="rmini mono">{{ mode }} · {{ fit }}</span>
          </div>
          <template v-if="settingsOpen">
            <div class="rrowlbl mono">MODE</div>
            <div class="seg rseg">
              <button :class="{ on: mode === 'pages' }" @click="mode = 'pages'">Pages</button>
              <button :class="{ on: mode === 'strip' }" @click="mode = 'strip'">Strip</button>
            </div>
            <div class="rrowlbl mono">FIT <span class="rkey">{{ bindingsFor('reader.fit').map(keyLabel).join(' ') }}</span></div>
            <div class="seg rseg">
              <button :class="{ on: fit === 'height' }" :disabled="mode === 'strip'" title="Fit the screen height" @click="fit = 'height'">Height</button>
              <button :class="{ on: fit === 'width' }" title="Stretch to the full width (minus margins)" @click="fit = 'width'">Width</button>
              <button :class="{ on: fit === 'custom' }" title="A fixed pixel width" @click="fit = 'custom'">Custom</button>
            </div>
            <template v-if="fit === 'custom'">
              <div class="rrowlbl mono">WIDTH <span class="rval">{{ widthPx }}px</span></div>
              <div class="rpresets">
                <button v-for="w in WIDTH_PRESETS" :key="w" class="rpre mono" :class="{ on: widthPx === w }" @click="widthPx = w">{{ w }}</button>
              </div>
              <input v-model.number="widthPx" class="rrange" type="range" min="480" max="1400" step="20" />
            </template>
            <template v-if="fit !== 'height' || mode === 'strip'">
              <div class="rrowlbl mono">MARGINS <span class="rkey">{{ [...bindingsFor('reader.marginsDec'), ...bindingsFor('reader.marginsInc')].map(keyLabel).join(' ') }}</span><span class="rval">{{ margins }}px</span></div>
              <input v-model.number="margins" class="rrange" type="range" min="0" max="200" step="4" />
            </template>
          </template>
        </div>

        <div class="rsec grow">
          <div class="rhead">
            <span class="rlbl">CHAPTERS</span>
            <span class="rmini mono">{{ flow.length }} readable</span>
          </div>
          <div v-if="langOpts.length > 2 || groupOpts.length > 2" class="rfilters">
            <Dropdown v-if="langOpts.length > 2" label="LANG" :model-value="langFilter" :options="langOpts" @update:model-value="langFilter = String($event)" />
            <Dropdown v-if="groupOpts.length > 2" label="GROUP" :model-value="groupFilter" :options="groupOpts" @update:model-value="groupFilter = String($event)" />
          </div>
          <div class="rlist scroll">
            <template v-for="node in listTree" :key="node.num">
              <!-- one label, several translations → header + guide-lined group -->
              <template v-if="node.rows.length > 1">
                <div class="rrow rnode">
                  <span class="rname"><b class="mono">{{ node.num }}</b><template v-if="node.rows.find((r) => r.title)"> {{ node.rows.find((r) => r.title)?.title }}</template></span>
                </div>
                <div class="rgrp">
                  <div v-for="c in node.rows" :key="c.id" class="rrow"
                       :class="{ on: c.id === chapter?.id, dead: !c.dl || !c.pages }"
                       :title="!c.dl || !c.pages ? 'Not downloaded' : ''"
                       @click="openChapter(c)">
                    <span class="rdot s" :style="{ background: readColor[c.read] }"></span>
                    <span class="rtrb"><span class="mono" style="color:var(--accent);font-size:9px">{{ c.lang || '?' }}</span><template v-if="c.group">{{ c.group }}</template></span>
                    <span class="rpg mono">{{ mediaLabel(c, '→') || '→' }}</span>
                  </div>
                </div>
              </template>
              <div v-else class="rrow"
                   :class="{ on: node.rows[0].id === chapter?.id, dead: !node.rows[0].dl || !node.rows[0].pages }"
                   :title="!node.rows[0].dl || !node.rows[0].pages ? 'Not downloaded' : ''"
                   @click="openChapter(node.rows[0])">
                <span class="rdot" :style="{ background: readColor[node.rows[0].read] }"></span>
                <span class="rname"><b class="mono">{{ node.rows[0].num || '—' }}</b><template v-if="node.rows[0].title"> {{ node.rows[0].title }}</template></span>
                <span v-if="node.rows[0].lang || node.rows[0].group" class="rtrb"><span class="mono" style="color:var(--accent);font-size:9px">{{ node.rows[0].lang || '?' }}</span><template v-if="node.rows[0].group">{{ node.rows[0].group }}</template></span>
                <span class="rpg mono">{{ mediaLabel(node.rows[0], '→') || '→' }}</span>
              </div>
            </template>
          </div>
        </div>

        <div class="rkeys mono">{{ keysLegend }}</div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.reader { height: 100%; display: flex; flex-direction: column; overflow: hidden; }

.rtop { flex: none; height: 44px; display: flex; align-items: center; gap: 12px; padding: 0 12px; border-bottom: 1px solid var(--line); background: var(--bg2); }
.rback { display: inline-flex; align-items: center; gap: 9px; min-width: 0; max-width: 40%; border: none; background: transparent; color: var(--tx2); font: 500 12.5px/1 system-ui; cursor: pointer; padding: 5px 8px; border-radius: 7px; }
.rback:hover { background: var(--hover); color: var(--tx); }
.rcover { width: 16px; height: 22px; border-radius: 3px; flex: none; border: 1px solid var(--line); }
.rtitle { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.rch { font-size: 11px; color: var(--tx3); flex: none; max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rind { font-size: 11px; color: var(--tx2); flex: none; }
.iconbtn.on { background: var(--accentSoft); color: var(--accent); }

.rbody { flex: 1; min-height: 0; display: flex; }

/* the stage — darker than the app so pages read as the content */
.stage { flex: 1; min-width: 0; overflow: auto; background: color-mix(in srgb, var(--bg) 72%, black); display: flex; flex-direction: column; align-items: center; cursor: pointer; }
.stage.fith { overflow: hidden; justify-content: center; }
.stage .page { display: block; flex: none; }
.stage.strip { cursor: default; scroll-behavior: auto; }
.rempty { margin: auto; font: 400 13px/1.5 system-ui; color: var(--tx3); }
.endcard { width: 100%; padding: 46px 20px 70px; display: flex; flex-direction: column; align-items: center; gap: 14px; }
.endlbl { font-size: 10px; letter-spacing: .14em; color: var(--tx3); }

/* the LEFT rail: page thumbnails of the open chapter, two per row — the SAME
   width as the right sidebar so the reader sits symmetrically between them.
   FLEX wrap with fully fixed tiles (width + aspect-ratio, image ABSOLUTE
   inside), exactly like the title page's gallery: auto grid rows under lazy
   images mis-size in this Chromium and the tiles paint over each other. */
.lrail { width: 248px; flex: none; border-right: 1px solid var(--line); background: var(--bg2); overflow-y: auto; padding: 12px; display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-start; align-content: flex-start; scrollbar-gutter: stable; }
.lthumb { position: relative; flex: none; width: calc(50% - 4px); aspect-ratio: 2/3; padding: 0; border: 1px solid var(--line); border-radius: 6px; background: var(--panel2); cursor: pointer; overflow: hidden; }
.lthumb img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; display: block; }
.lthumb:hover { border-color: var(--accent); }
.lthumb.on { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
.lnum { position: absolute; bottom: 3px; right: 4px; font-size: 9px; color: #fff; background: rgba(0, 0, 0, .6); padding: 1px 4px; border-radius: 4px; }
/* the two panel toggles mirror each other — left rail, right sidebar */
.rleft :deep(svg) { transform: scaleX(-1); }

/* the sidebar */
.rside { width: 248px; flex: none; border-left: 1px solid var(--line); background: var(--bg2); display: flex; flex-direction: column; padding: 12px; gap: 12px; min-height: 0; }
.rsec { display: flex; flex-direction: column; min-height: 0; flex: none; }
.rsec.grow { flex: 1; min-height: 0; }
.rhead { display: flex; align-items: center; gap: 6px; padding: 3px 4px; margin-bottom: 4px; border-radius: 6px; }
.rhead.click { cursor: pointer; }
.rhead.click:hover { background: var(--hover); }
.rchev { color: var(--tx3); transition: transform .12s; flex: none; }
.rlbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); }
.rmini { margin-left: auto; font-size: 9.5px; color: var(--tx3); }
.rrowlbl { display: flex; align-items: center; gap: 7px; font-size: 9px; letter-spacing: .1em; color: var(--tx3); margin: 9px 2px 5px; }
.rkey { font-size: 8.5px; color: var(--tx3); border: 1px solid var(--line); border-radius: 4px; padding: 1.5px 4px; }
.rval { margin-left: auto; font-size: 10px; color: var(--accent); }
.rseg { display: flex; border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }
.rseg button { flex: 1; border: none; background: transparent; color: var(--tx2); font: 500 11.5px/1 system-ui; padding: 7px 0; cursor: pointer; }
.rseg button + button { border-left: 1px solid var(--line); }
.rseg button.on { background: var(--accentSoft); color: var(--accent); }
.rseg button:disabled { opacity: .4; cursor: default; }
.rpresets { display: flex; gap: 6px; margin-bottom: 7px; }
.rpre { flex: 1; border: 1px solid var(--line); border-radius: 6px; background: transparent; color: var(--tx2); font-size: 10.5px; padding: 5px 0; cursor: pointer; }
.rpre.on { border-color: var(--accent); color: var(--accent); background: var(--accentSoft); }
.rrange { width: 100%; accent-color: var(--accent); }

.rfilters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.rlist { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 2px; }
.rrow { display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 7px; cursor: pointer; color: var(--tx2); border: 1px solid transparent; }
.rrow:hover { background: var(--hover); color: var(--tx); }
.rrow.on { background: var(--accentSoft); border-color: color-mix(in srgb, var(--accent) 35%, var(--line)); color: var(--tx); }
.rrow.dead { opacity: .45; cursor: default; }
.rdot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.rname { flex: 1; min-width: 0; font: 500 12px/1.3 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rname b { font: 600 12px/1 ui-monospace, monospace; }
.rnode { cursor: default; color: var(--tx2); }
.rnode:hover { background: transparent; }
.rgrp { margin-left: 10px; padding-left: 9px; border-left: 2px solid var(--line); display: flex; flex-direction: column; gap: 2px; }
.rgrp .rrow { background: color-mix(in srgb, var(--panel) 40%, transparent); }
.rgrp .rrow:hover { background: var(--hover); }
.rgrp .rrow.on { background: var(--accentSoft); }
.rdot.s { width: 7px; height: 7px; }
.rtrb { display: inline-flex; align-items: center; gap: 5px; font: 500 9.5px/1 system-ui; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); padding: 2px 6px; border-radius: 4px; flex: none; }
.rpg { margin-left: auto; font-size: 9.5px; color: var(--tx3); flex: none; }
.rrow:not(.dead) .rpg { color: var(--good); }

.rkeys { flex: none; font-size: 8.5px; color: var(--tx3); text-align: center; padding-top: 8px; border-top: 1px solid var(--line); }
</style>
