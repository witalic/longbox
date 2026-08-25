<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import Icon from './components/Icon.vue'
import ConfirmModal from './components/ConfirmModal.vue'
import RailSection from './components/RailSection.vue'
import {
  store, goView, openTitle, closeTab, titleById, toggleTitlePin, moveTitleTabBefore, init,
  shownAxes, runningDownloads, unfinishedDownloads,
} from './store'
import {
  browser, openBrowse, activateTab, closeTab as closeBrowserTab, newTab, toggleTabPin,
  moveBrowserTabBefore, toggleTabMute,
} from './browser'
import { confirmDiscard, discardDraft, draftState } from './draft'
import { initSessions, restoreSession, sessionHistory, sessionLabel } from './sessions'
import { coverAt, hueFor } from './data'
import LibraryView from './views/LibraryView.vue'
import DownloadsPanel from './components/DownloadsPanel.vue'
import TitleView from './views/TitleView.vue'
import ReaderView from './views/ReaderView.vue'
import AuthorsView from './views/AuthorsView.vue'
import SourcesView from './views/SourcesView.vue'
import SettingsView from './views/SettingsView.vue'
import BrowserView from './views/BrowserView.vue'

// Four destinations. Browsing BY a field is not a fifth: it is the library seen
// through an axis, and you enter it by picking that axis in the rail below —
// which is also where you leave it. A nav item for it duplicated the rail and
// read like a second browser.
const nav = [
  { key: 'library', label: 'Library', icon: 'library' },
  { key: 'browse', label: 'Browse', icon: 'browser' },
  { key: 'sources', label: 'Sources', icon: 'sources' },
  { key: 'settings', label: 'Settings', icon: 'settings' },
] as const

function navActive(key: string): boolean {
  if (key === 'library') return ['library', 'title', 'reader', 'authors'].includes(store.view)
  if (key === 'browse') return store.view === 'browser'
  return store.view === key
}
// Navigation is inert by design: the draft lives outside the views and survives
// moving around; no guard is needed here (discarding is what asks).
function navClick(key: string) {
  if (key === 'browse') openBrowse()
  else goView(key as 'library' | 'sources' | 'settings')
}

// ONE browser-styled strip, CONTEXTUAL: in-app views (Library/Title/Authors/
// Settings) show the title tabs; browser-side views (Browse/Sources) show the
// browser tabs. The all-tabs menu always lists both worlds.
const titleTabs = computed(() => {
  const entry = (id: string) => ({ id, t: titleById(id)!, pinned: store.pinnedTabs.includes(id) })
  const all = store.openTabs.map(entry).filter((x) => x.t)
  return [...all.filter((x) => x.pinned), ...all.filter((x) => !x.pinned)]
})
const browserTabs = computed(() =>
  [...browser.tabs.filter((t) => t.pinned), ...browser.tabs.filter((t) => !t.pinned)])
// Settings is neither world — its strip shows no tabs at all.
const stripMode = computed(() =>
  store.view === 'settings' ? 'none' : ['browser', 'sources'].includes(store.view) ? 'web' : 'app')

// Downloads: the count in the footer, the panel over everything, and the one
// case where the WINDOW asks first — closing with transfers still running.
const dlOpen = ref(false)
const closeWarning = ref('')
const unfinished = computed(() => unfinishedDownloads())
const runningCount = computed(() => runningDownloads().length)
let dropCloseHook: (() => void) | null = null
onMounted(() => {
  dropCloseHook = window.longbox?.onCloseBlocked((n) => {
    closeWarning.value = n === 1
      ? 'One download is still running.'
      : `${n} downloads are still running.`
    dlOpen.value = true
  }) ?? null
})
onBeforeUnmount(() => dropCloseHook?.())
async function quitNow() {
  closeWarning.value = ''
  await window.longbox?.closeNow()
}

const tabScrollEl = ref<HTMLElement | null>(null)
// Where the strip CONTINUES. A fade that is always on is just a soft edge; one
// that appears on the side there is more of dissolves the tabs that are being
// scrolled away and leaves the rest alone — nothing blurs for no reason, and
// the strip says which way to look.
const tabsMore = reactive({ left: false, right: false })
function measureStrip() {
  const el = tabScrollEl.value
  if (!el) return
  const room = el.scrollWidth - el.clientWidth
  tabsMore.left = el.scrollLeft > 2
  tabsMore.right = room > 2 && el.scrollLeft < room - 2
}
let stripWatch: ResizeObserver | null = null
onMounted(() => {
  stripWatch = new ResizeObserver(measureStrip)
  if (tabScrollEl.value) stripWatch.observe(tabScrollEl.value)
  measureStrip()
})
onBeforeUnmount(() => stripWatch?.disconnect())
// a tab opening, closing or being renamed changes the strip without scrolling it
watch(() => [browser.tabs.length, store.openTabs.length, stripMode.value],
      () => void nextTick(measureStrip))
function onTabWheel(e: WheelEvent) {
  tabScrollEl.value?.scrollBy({ left: e.deltaY + e.deltaX })
}
// keep the active tab visible when activation happens from elsewhere
watch(() => [store.view, store.activeTitle, browser.activeId], async () => {
  await nextTick()
  tabScrollEl.value?.querySelector('.tab.on')?.scrollIntoView({ inline: 'nearest', block: 'nearest' })
})

// The strip's "+" opens a new tab OF THE WORLD it belongs to: a browser tab on
// the homepage in the web strip, the library on the app side.
function newStripTab() {
  if (stripMode.value === 'web') newTab()
  else goView('library')
}

// ---- the shelves: the `type` facet, the one field a title physically lives on
// The shelf narrows the library AND the browse groups (they are the same
// selection), so it has to be visible in both — a filter you cannot see from the
// screen it is filtering is just a broken screen.
const SHELF_VIEWS = ['library', 'title', 'reader', 'authors']
const shelfViews = computed(() => SHELF_VIEWS.includes(store.view))
const narrowsHere = computed(() => store.view === 'library' || store.view === 'authors')
const shelfPicked = computed(() => store.library.shelf)
const shelves = computed(() => (store.globalFacets.type ?? []).map((g) => ({
  v: g.v, n: g.n, on: shelfPicked.value === g.v,
})))
function pickShelf(v: string) {
  // Where the shelf is visibly at work (library, browse) a second click on the
  // open one CLEARS it, and you stay where you are. From a title or the reader
  // the same click is navigation — "take me to this shelf" — so it never clears
  // and it goes to the library.
  store.library.shelf = !v || (narrowsHere.value && shelfPicked.value === v) ? '' : v
  if (!narrowsHere.value) goView('library')
}

const axisFields = computed(() => shownAxes())
function goBrowse(id: string) {
  // clicking the OPEN axis goes back to the plain library — the same grammar the
  // shelves have, so the rail has one way in and the same way out
  if (store.view === 'authors' && store.browseAxis === id) {
    goView('library')
    return
  }
  store.browseAxis = id
  goView('authors')
}

// An unsaved NEW title exists only in the draft — it has no id, so it cannot be
// an open tab. Without one, leaving it meant a discard prompt with nowhere to
// go back to. It gets a tab of its own until it is saved.
const draftTab = computed(() => {
  const d = draftState.cur
  return d && !d.targetId ? (d.meta.title.trim() || 'New title') : null
})
const onDraft = computed(() => store.view === 'title' && !store.activeTitle)
function openDraft() {
  store.activeTitle = null
  store.view = 'title'
}
async function closeDraft() {
  if (await confirmDiscard()) discardDraft()
}

const tabsMenu = ref(false)
const tabsMenuRoot = ref<HTMLElement | null>(null)
function onDocDown(e: MouseEvent) {
  if (tabsMenuRoot.value && !tabsMenuRoot.value.contains(e.target as Node)) tabsMenu.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocDown))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocDown))
function jumpTitle(id: string) { tabsMenu.value = false; void openTitle(id) }
function jumpBrowser(id: string) { tabsMenu.value = false; activateTab(id) }
// drag & drop reordering inside the menu (within a group)
const dragging = ref<{ kind: 't' | 'b'; id: string } | null>(null)
function dragStart(kind: 't' | 'b', id: string) { dragging.value = { kind, id } }
function dropOn(kind: 't' | 'b', targetId: string) {
  const d = dragging.value
  dragging.value = null
  if (!d || d.kind !== kind) return
  if (kind === 't') moveTitleTabBefore(d.id, targetId)
  else moveBrowserTabBefore(d.id, targetId)
}

// Chrome-style reorder IN THE STRIP, pointer-based: the tab itself slides
// HORIZONTALLY inside the strip (no free-floating drag ghost over the app);
// crossing a neighbor's midpoint live-reorders and the rest slide aside via
// the TransitionGroup's FLIP `move` transition. A real drag consumes the
// click that follows, so releasing never re-activates by accident.
const stripDrag = ref<{ kind: 't' | 'b'; id: string } | null>(null)
let dragEl: HTMLElement | null = null
let dragPtr = 0
let dragStartX = 0
let dragStartLeft = 0
let dragStartScroll = 0
let dragLive = false
let swallowClick = false

function parseTx(t: string): number {
  const m = /translateX\((-?[\d.]+)px\)/.exec(t)
  return m ? parseFloat(m[1]) : 0
}
function tabPointerDown(kind: 't' | 'b', id: string, e: PointerEvent) {
  if (e.button !== 0) return
  if ((e.target as HTMLElement).closest('.tact')) return // ✕ / pin are clicks, never drags
  if (stripDrag.value) return // a second pointer must not orphan the live drag
  swallowClick = false // a cancelled drag never gets its click — start clean
  dragEl = e.currentTarget as HTMLElement
  dragPtr = e.pointerId
  dragStartX = e.clientX
  dragStartLeft = dragEl.getBoundingClientRect().left
  dragStartScroll = tabScrollEl.value?.scrollLeft ?? 0
  dragLive = false
  stripDrag.value = { kind, id }
  dragEl.setPointerCapture(e.pointerId)
}
function tabPointerMove(e: PointerEvent) {
  const d = stripDrag.value
  if (!d || !dragEl || e.pointerId !== dragPtr) return
  const dx = e.clientX - dragStartX
  if (!dragLive && Math.abs(dx) < 5) return // click-sized wobble is not a drag
  if (!dragLive) {
    dragLive = true
    // chrome selects the tab the moment its drag starts
    if (d.kind === 't') void openTitle(d.id)
    else activateTab(d.id)
  }
  const strip = tabScrollEl.value
  // glue the tab to the pointer on the X axis only, clamped to the strip. The
  // strip can auto-scroll under us, so the grab point moves with it.
  const cur = dragEl.getBoundingClientRect()
  const slotLeft = cur.left - parseTx(dragEl.style.transform) // layout position without our offset
  const scrolled = (strip?.scrollLeft ?? 0) - dragStartScroll
  let want = dragStartLeft + dx - scrolled
  if (strip) {
    const s = strip.getBoundingClientRect()
    want = Math.max(s.left, Math.min(want, s.right - cur.width))
    // nudge the strip when dragging against its edges
    if (e.clientX > s.right - 28) strip.scrollBy({ left: 14 })
    else if (e.clientX < s.left + 28) strip.scrollBy({ left: -14 })
  }
  dragEl.style.transform = `translateX(${want - slotLeft}px)`
  // live reorder on midpoint crossing (past the last tab's right half → the end).
  // The insert point is computed in DISPLAY order, then translated back to the
  // raw array the move helpers splice — pinned tabs render first, so the two
  // orders are not the same list.
  if (!strip) return
  const shown = d.kind === 't' ? titleTabs.value.map((x) => x.id) : browserTabs.value.map((x) => x.id)
  const from = shown.indexOf(d.id)
  if (from < 0) return
  for (const el of strip.querySelectorAll<HTMLElement>('.tab')) {
    const tid = el.dataset.tabId
    if (!tid || tid === d.id) continue
    const r = el.getBoundingClientRect()
    if (e.clientX < r.left || e.clientX > r.right) continue
    const ti = shown.indexOf(tid)
    if (ti < 0) break
    const insert = e.clientX < r.left + r.width / 2 ? ti : ti + 1
    if (insert === from || insert === from + 1) break // already there — keeps the live swap stable
    // land before the next tab that still exists AFTER this one moves out
    const rest = shown.filter((x) => x !== d.id)
    const beforeId = rest[insert > from ? insert - 1 : insert]
    if (d.kind === 't') moveTitleTabBefore(d.id, beforeId ?? '#end')
    else moveBrowserTabBefore(d.id, beforeId ?? '#end')
    break
  }
}
function tabPointerUp(e: PointerEvent) {
  if (!stripDrag.value || e.pointerId !== dragPtr) return
  const el = dragEl
  // a real drag consumes the click that follows — but a CANCELLED pointer never
  // produces one, so the flag must not outlive it
  swallowClick = dragLive && e.type === 'pointerup'
  stripDrag.value = null
  dragEl = null
  if (el) {
    if (dragLive) {
      // settle into the slot with a short slide instead of an instant snap
      el.classList.add('tabsettle')
      el.style.transform = ''
      window.setTimeout(() => el.classList.remove('tabsettle'), 200)
    } else {
      el.style.transform = ''
    }
  }
  dragLive = false
}
function tabClick(kind: 't' | 'b', id: string) {
  if (swallowClick) { swallowClick = false; return }
  if (kind === 't') void openTitle(id)
  else activateTab(id)
}

const draftOpen = computed(() => !!draftState.cur)

function toggleTheme() {
  store.theme = store.theme === 'light' ? 'dark' : 'light'
}

function doRestore(i: number) {
  tabsMenu.value = false
  restoreSession(i)
}

init()
initSessions()
</script>

<template>
  <div class="app">
    <!-- chrome 2b (design handoff): ONE full-width titlebar — brand (no box),
         tabs, then the right cluster ⌄ | – ▢ ✕; the sidebar starts BELOW it -->
    <div class="tabbar">
      <div class="brand">
        <svg class="logo" viewBox="0 0 72 72" aria-hidden="true"><defs><linearGradient id="lbgrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8194f4"/><stop offset="1" stop-color="#3e4eb2"/></linearGradient></defs><rect width="72" height="72" rx="17" fill="url(#lbgrad)"/><path d="M27 19h9v25h13v9H27z" fill="#fff"/></svg>
        <span class="name">{{ store.appMeta.name || 'longbox' }}</span>
      </div>
      <div ref="tabScrollEl" class="tabscroll"
           :class="{ moreL: tabsMore.left, moreR: tabsMore.right }"
           @wheel.prevent="onTabWheel" @scroll="measureStrip">
        <div v-if="stripMode === 'app' && draftTab" class="tab draft" :class="{ on: onDraft }"
             :title="`${draftTab} — unsaved`" @click="openDraft">
          <Icon name="edit" :size="11" :sw="2" />
          <span class="lbl">{{ draftTab }}</span>
          <span class="tact" title="Discard this draft" @click.stop="closeDraft">
            <Icon name="x" :size="10" :sw="2.4" />
          </span>
        </div>
        <TransitionGroup v-if="stripMode === 'app'" name="tabslide">
          <div v-for="x in titleTabs" :key="x.id" class="tab" :data-tab-id="x.id"
               :class="{ on: ['title', 'reader'].includes(store.view) && store.activeTitle === x.id, pinned: x.pinned,
                         dragging: stripDrag?.id === x.id }"
               :title="x.t.title"
               @pointerdown="tabPointerDown('t', x.id, $event)" @pointermove="tabPointerMove"
               @pointerup="tabPointerUp" @pointercancel="tabPointerUp"
               @auxclick.prevent="$event.button === 1 && closeTab(x.id)"
               @click="tabClick('t', x.id)">
            <span class="swatch" :style="x.t.cover ? { background: `#181a1f url('${coverAt(x.t.cover, 64)}') center/cover` } : { background: hueFor(x.id) }"></span>
            <template v-if="!x.pinned">
              <span class="lbl">{{ x.t.title }}</span>
              <span class="tact pinbtn" title="Pin tab" @click.stop="toggleTitlePin(x.id)"><Icon name="pin" :size="10" :sw="2" /></span>
              <span class="tact" title="Close" @click.stop="closeTab(x.id)"><Icon name="x" :size="10" :sw="2.4" /></span>
            </template>
            <span v-else class="tact unpin" title="Unpin tab" @click.stop="toggleTitlePin(x.id)"><Icon name="pin" :size="10" :sw="2" /></span>
          </div>
        </TransitionGroup>
        <TransitionGroup v-else-if="stripMode === 'web'" name="tabslide">
          <div v-for="t in browserTabs" :key="t.id" class="tab web" :data-tab-id="t.id"
               :class="{ on: store.view === 'browser' && browser.activeId === t.id, pinned: t.pinned,
                         dragging: stripDrag?.id === t.id }"
               :title="t.title || t.url"
               @pointerdown="tabPointerDown('b', t.id, $event)" @pointermove="tabPointerMove"
               @pointerup="tabPointerUp" @pointercancel="tabPointerUp"
               @auxclick.prevent="$event.button === 1 && closeBrowserTab(t.id)"
               @click="tabClick('b', t.id)">
            <span v-if="t.loading" class="tabspin" title="Loading…"></span>
            <span v-else class="fav">{{ (t.label[0] || '?').toUpperCase() }}</span>
            <!-- what is making the noise, and the switch that stops it -->
            <span v-if="t.audible || t.muted" class="tact snd" :class="{ off: t.muted }"
                  :title="t.muted ? 'Unmute this tab' : 'Playing — click to mute'"
                  @click.stop="toggleTabMute(t.id)">
              <Icon :name="t.muted ? 'muted' : 'sound'" :size="11" :sw="1.9" />
            </span>
            <template v-if="!t.pinned">
              <span class="lbl">{{ t.title || t.label }}</span>
              <span class="tact pinbtn" title="Pin tab" @click.stop="toggleTabPin(t.id)"><Icon name="pin" :size="10" :sw="2" /></span>
              <span class="tact" title="Close" @click.stop="closeBrowserTab(t.id)"><Icon name="x" :size="10" :sw="2.4" /></span>
            </template>
            <span v-else class="tact unpin" title="Unpin tab" @click.stop="toggleTabPin(t.id)"><Icon name="pin" :size="10" :sw="2" /></span>
          </div>
        </TransitionGroup>
      </div>
      <button class="stripbtn" :title="stripMode === 'web' ? 'New browser tab' : 'Library'" @click="newStripTab">
        <Icon name="plus" :size="14" :sw="2.2" />
      </button>
      <div ref="tabsMenuRoot" class="alltabs">
        <button class="stripbtn" :class="{ on: tabsMenu }" title="All tabs" @click="tabsMenu = !tabsMenu"><Icon name="chevron" :size="12" :sw="2.2" /></button>
        <div v-if="tabsMenu" class="tabsmenu scroll">
          <template v-if="titleTabs.length">
            <div class="mlbl">TITLES · {{ titleTabs.length }} <span class="mhint">drag to reorder</span></div>
            <div v-for="x in titleTabs" :key="x.id" class="mitem"
                 :class="{ on: ['title', 'reader'].includes(store.view) && store.activeTitle === x.id, drag: dragging?.id === x.id }"
                 draggable="true" @dragstart="dragStart('t', x.id)" @dragover.prevent @drop.prevent="dropOn('t', x.id)"
                 @click="jumpTitle(x.id)">
              <span class="swatch" :style="x.t.cover ? { background: `#181a1f url('${coverAt(x.t.cover, 64)}') center/cover` } : { background: hueFor(x.id) }"></span>
              <span class="mname">{{ x.t.title }}</span>
              <span class="mx" title="Close" @click.stop="closeTab(x.id)"><Icon name="x" :size="11" :sw="2.4" /></span>
            </div>
          </template>
          <template v-if="browserTabs.length">
            <div class="mlbl">BROWSER · {{ browserTabs.length }} <span class="mhint">drag to reorder</span></div>
            <div v-for="t in browserTabs" :key="t.id" class="mitem"
                 :class="{ on: store.view === 'browser' && browser.activeId === t.id, drag: dragging?.id === t.id }"
                 draggable="true" @dragstart="dragStart('b', t.id)" @dragover.prevent @drop.prevent="dropOn('b', t.id)"
                 @click="jumpBrowser(t.id)">
              <span class="fav">{{ (t.label[0] || '?').toUpperCase() }}</span>
              <span class="mname">{{ t.title || t.label }}</span>
              <span class="mx" title="Close" @click.stop="closeBrowserTab(t.id)"><Icon name="x" :size="11" :sw="2.4" /></span>
            </div>
          </template>
          <div v-if="!titleTabs.length && !browserTabs.length" class="mlbl">NO OPEN TABS</div>
          <div class="mnew" @click="tabsMenu = false; newTab()">
            <Icon name="browser" :size="14" :sw="1.9" /><span>New browser tab</span>
          </div>
          <template v-if="sessionHistory.length">
            <div class="mlbl">RESTORE SESSION</div>
            <div v-for="(sn, i) in sessionHistory" :key="sn.at + i" class="mitem" title="Reopen these tabs in the background" @click="doRestore(i)">
              <Icon name="refresh" :size="13" :sw="2" style="color:var(--tx3);flex:none" />
              <span class="mname mono" style="font-size:11px">{{ sessionLabel(sn) }}</span>
            </div>
          </template>
        </div>
      </div>
      <span class="wdiv"></span>
    </div>

    <div class="approw">
    <aside class="side">
      <nav class="nav">
        <button v-for="n in nav" :key="n.key" class="navitem" :class="{ on: navActive(n.key) }"
                @click="navClick(n.key)">
          <Icon :name="n.icon" :size="18" />{{ n.label }}
          <span v-if="n.key === 'browse' && draftOpen" class="draftdot" title="A draft is open"></span>
        </button>
      </nav>
      <!-- Contextual rail: whichever view is on screen teleports its own
           section here (shelves, sources, groups). Empty is a valid state. -->
      <div id="siderail" class="siderail scroll">
        <!-- Shelves are navigation over GLOBAL state, so they stay put while you
             open a title or read one; the per-view sections teleport in below. -->
        <RailSection v-if="shelfViews" label="SHELVES">
          <button :class="{ on: !shelfPicked }" @click="pickShelf('')">
            <span class="sdot" style="background: transparent"></span>
            <span class="slbl">All types</span>
            <span class="sn mono">{{ store.total }}</span>
          </button>
          <button v-for="sh in shelves" :key="sh.v" :class="{ on: sh.on }" @click="pickShelf(sh.v)">
            <span class="sdot" :style="{ background: hueFor(sh.v) }"></span>
            <span class="slbl">{{ sh.v }}</span>
            <span class="sn mono">{{ sh.n }}</span>
          </button>
        </RailSection>
        <!-- the same axes the browse view groups by, as navigation into it -->
        <RailSection v-if="shelfViews" label="BROWSE BY">
          <button v-for="f in axisFields" :key="f.id"
                  :class="{ on: store.view === 'authors' && store.browseAxis === f.id }"
                  :title="store.view === 'authors' && store.browseAxis === f.id
                    ? 'Back to the library' : `Browse by ${f.label.toLowerCase()}`"
                  @click="goBrowse(f.id)">
            <span class="slbl">{{ f.label }}</span>
          </button>
        </RailSection>
      </div>

      <div class="foot">
        <span class="dot" :style="{ background: 'var(--good)' }"></span>
        <span>Local library</span>
        <div style="flex:1"></div>
        <!-- what is being fetched right now, and the way into the whole list -->
        <button v-if="unfinished.length" class="themebtn dlbtn"
                :class="{ hot: !!runningCount }"
                :title="runningCount ? `${runningCount} downloading` : 'Unfinished downloads'"
                @click="dlOpen = true">
          <Icon name="download" :size="15" :sw="2" />
          <span class="dlcount mono">{{ unfinished.length }}</span>
        </button>
        <button class="themebtn" title="Toggle theme" @click="toggleTheme">
          <Icon name="moon" :size="16" />
        </button>
      </div>
    </aside>

    <!-- over everything, because it is about what the whole app is doing -->
    <DownloadsPanel v-if="dlOpen" :warning="closeWarning"
                    @close="dlOpen = false; closeWarning = ''" @quit="quitNow" />

    <!-- main -->
    <main class="main">
      <div class="viewport scroll">
        <LibraryView v-if="store.view === 'library'" />
        <TitleView v-else-if="store.view === 'title'" />
        <ReaderView v-else-if="store.view === 'reader'" class="reader-host" />
        <AuthorsView v-else-if="store.view === 'authors'" />
        <SourcesView v-else-if="store.view === 'sources'" />
        <SettingsView v-else-if="store.view === 'settings'" />
        <!-- kept mounted so the embedded browser (pages + history) survives leaving Browse -->
        <BrowserView v-show="store.view === 'browser'" class="browser-host" />
      </div>
    </main>
    </div>
    <!-- Failures used to be written to store.error and never shown: a refused
         upload, a failed reorder or delete simply did nothing. One toast, one
         place, dismissable. -->
    <div v-if="store.error" class="errtoast" @click="store.error = null">
      <Icon name="x" :size="13" :sw="2.4" />
      <span class="errmsg">{{ store.error }}</span>
    </div>
    <ConfirmModal />
  </div>
</template>

<style scoped>
.app { height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
.approw { flex: 1; min-height: 0; display: flex; }
.side { width: 220px; height: 100%; flex: none; background: var(--bg2); border-right: 1px solid var(--line); display: flex; flex-direction: column; }
/* the brand lives IN the titlebar — no box, no borders (chrome 2b) */
.brand { display: flex; align-items: center; gap: 9px; padding-right: 18px; flex: none; }
.brand .logo { width: 24px; height: 24px; flex: none; }
.brand .name { font: 700 13.5px/1 system-ui; letter-spacing: -.2px; color: var(--tx); }
.nav { padding: 12px 10px 6px; display: flex; flex-direction: column; gap: 2px; flex: none; }
/* the rail scrolls; the foot below it stays put however long the list gets */
.siderail { flex: 1; min-height: 0; overflow-y: auto; }
.sdot { width: 5px; height: 5px; border-radius: 50%; flex: none; }
.slbl { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-transform: capitalize; }
.sn { font: 500 10.5px/1 ui-monospace, monospace; color: var(--tx3); flex: none; }
.navitem { height: 36px; }
.navitem { display: flex; align-items: center; gap: 11px; padding: 0 12px; border-radius: 8px; border: none; background: transparent; color: var(--tx2); font: 500 13px/1 system-ui; cursor: pointer; text-align: left; }
.navitem:hover { background: var(--hover); color: var(--tx); }
.navitem.on { background: var(--accentSoft); color: var(--accent); }
.draftdot { margin-left: auto; width: 7px; height: 7px; border-radius: 50%; background: var(--warn); }
/* SAME height as the content footers (.lfoot) so the bottom line is one bar */
.foot { margin-top: auto; height: 44px; padding: 0 16px; border-top: 1px solid var(--line); display: flex; align-items: center; gap: 9px; font: 500 11px/1.3 system-ui; color: var(--tx3); flex: none; }
.foot .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
/* the footer decides the spacing now — a spacer div sits between the label and
   these, so neither button pushes the other around */
.themebtn { border: none; background: transparent; color: var(--tx2); cursor: pointer; display: flex; align-items: center; gap: 5px; padding: 2px; }
.dlbtn.hot { color: var(--accent); }
.dlcount { font-size: 10.5px; }
.themebtn:hover { color: var(--tx); }

.main { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--bg); }

/* the single, browser-style tab strip */
.tabbar { height: 40px; flex: none; background: var(--bg2); border-bottom: 1px solid var(--line); display: flex; align-items: center; padding-left: 14px; }
.wdiv { width: 1px; height: 22px; margin: 0 4px; background: var(--line); flex: none; }
/* The strip ends against the window controls, so the last tab was cut mid-word
   by a hard edge. It dissolves instead — but only on the side that actually
   continues (.moreL / .moreR), and over a long ramp with a soft knee, so a tab
   being scrolled away fades out instead of meeting a gradient-shaped wall.
   With both flags off the mask is fully opaque and costs nothing. */
.tabscroll { flex: 1; min-width: 0; height: 100%; display: flex; align-items: flex-end; gap: 3px; padding-top: 5px; overflow-x: auto; overflow-y: hidden; scrollbar-width: none;
  --fadeL: 0px; --fadeR: 0px;
  -webkit-mask-image: var(--strip-mask); mask-image: var(--strip-mask);
  --strip-mask: linear-gradient(to right,
    transparent 0, rgba(0,0,0,.55) calc(var(--fadeL) * .45), #000 var(--fadeL),
    #000 calc(100% - var(--fadeR)), rgba(0,0,0,.55) calc(100% - var(--fadeR) * .45), transparent 100%); }
.tabscroll.moreL { --fadeL: 48px; }
.tabscroll.moreR { --fadeR: 64px; }
.tabscroll::-webkit-scrollbar { display: none; }
/* every tab reads as a tab: a visible card even when inactive; the active one
   shares the content background so it visually connects to the page below */
.tab { flex: none; display: inline-flex; align-items: center; gap: 7px; height: 35px; padding: 0 10px; width: 190px; min-width: 0; border: 1px solid color-mix(in srgb, var(--line) 55%, transparent); border-bottom: none; border-radius: 8px 8px 0 0; background: color-mix(in srgb, var(--panel) 55%, transparent); color: var(--tx3); font: 500 12px/1 system-ui; cursor: pointer; user-select: none; }
.tab:hover { background: var(--panel); color: var(--tx); }
/* the ACTIVE tab carries an accent top bar — background alone is too subtle
   in the light theme */
.tab.on { background: var(--bg); color: var(--tx); border-color: var(--line); box-shadow: inset 0 2.5px 0 var(--accent); }
.tab.pinned { width: auto; padding: 0 8px; }
/* the draft is not a saved record yet, and says so */
.tab.draft { border-style: dashed; color: var(--warn); }
.tab.draft.on { color: var(--warn); box-shadow: inset 0 2.5px 0 var(--warn); }
.tab .lbl { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.swatch { width: 14px; height: 19px; border-radius: 3px; flex: none; border: 1px solid var(--line); }
.fav { width: 16px; height: 16px; border-radius: 4px; background: var(--panel2); display: inline-flex; align-items: center; justify-content: center; font: 700 8px/1 system-ui; color: var(--tx3); flex: none; }
.tact { width: 16px; height: 16px; border-radius: 4px; display: inline-flex; align-items: center; justify-content: center; color: var(--tx3); flex: none; }
/* the sound mark is a STATE first and a button second: it is there because the
   tab is making noise, so it holds its place whether or not the row is hovered */
.tact.snd { color: var(--accent); }
.tact.snd.off { color: var(--tx3); }
.tact:hover { background: var(--hover); color: var(--tx); }
.tab .pinbtn { opacity: 0; }
.tab:hover .pinbtn { opacity: 1; }
.tab .unpin { color: var(--accent); }
/* strip reorder (chrome-style, pointer-based): the tab itself slides along the
   strip — raised above its neighbors, which move aside via the FLIP transition;
   its own transform is driven from JS, so any -move transition must not fight it */
.tab.dragging { transition: none !important; position: relative; z-index: 5; box-shadow: 0 4px 14px rgba(0, 0, 0, .35); }
.tab.tabsettle { transition: transform .16s ease; }
.tabslide-move { transition: transform .16s ease; }
/* browser tab loading: the favicon slot becomes a spinner while a request is in flight */
.tabspin { width: 14px; height: 14px; flex: none; border-radius: 50%;
  border: 2px solid color-mix(in srgb, var(--accent) 25%, transparent);
  border-top-color: var(--accent); animation: tabspin .8s linear infinite; }
@keyframes tabspin { to { transform: rotate(360deg); } }
.stripbtn { width: 40px; height: 100%; border: none; border-radius: 0; background: transparent; color: var(--tx2); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; flex: none; }
.stripbtn:hover { background: var(--hover); color: var(--tx); }
.stripbtn.on { background: var(--accentSoft); color: var(--accent); }
.alltabs { position: relative; align-self: stretch; display: flex; flex: none; }
.tabsmenu { position: absolute; top: calc(100% + 6px); right: 0; z-index: 60; width: 320px; max-height: 60vh; overflow: auto; background: var(--panel2); border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 18px 44px rgba(0,0,0,.55); padding: 6px; }
.tabsmenu .mlbl { font: 700 8.5px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); padding: 8px 10px 5px; }
.tabsmenu .mitem { display: flex; align-items: center; gap: 10px; padding: 7px 9px; border-radius: 7px; font: 500 12.5px/1.3 system-ui; color: var(--tx2); cursor: pointer; }
.tabsmenu .mitem:hover { background: var(--hover); color: var(--tx); }
.tabsmenu .mitem.on { color: var(--tx); background: var(--accentSoft); }
.tabsmenu .mitem.drag { opacity: .45; }
.tabsmenu .mname { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.tabsmenu .mx { width: 18px; height: 18px; border-radius: 5px; display: inline-flex; align-items: center; justify-content: center; color: var(--tx3); flex: none; }
.tabsmenu .mx:hover { background: var(--hover); color: var(--tx); }
.tabsmenu .mhint { font: 400 8.5px/1 system-ui; letter-spacing: 0; color: var(--tx3); text-transform: none; margin-left: 6px; opacity: .8; }
.tabsmenu .mnew { display: flex; align-items: center; gap: 9px; margin-top: 6px; padding: 8px 9px; border-top: 1px solid var(--line); font: 600 12px/1 system-ui; color: var(--accent); cursor: pointer; border-radius: 0 0 7px 7px; }
.tabsmenu .mnew:hover { background: var(--accentSoft); }
.viewport { flex: 1; min-height: 0; }
/* the one failure surface: bottom-centred, above everything, click to dismiss */
.errtoast { position: fixed; left: 50%; bottom: 22px; transform: translateX(-50%); z-index: 200;
  display: flex; align-items: center; gap: 9px; max-width: 620px; padding: 10px 14px;
  border-radius: 10px; border: 1px solid color-mix(in srgb, var(--adult) 55%, var(--line));
  background: color-mix(in srgb, var(--adult) 14%, var(--panel2)); color: var(--tx);
  font: 500 12px/1.4 system-ui; box-shadow: 0 14px 34px rgba(0, 0, 0, .45); cursor: pointer; }
.errtoast:hover { border-color: var(--adult); }
.errmsg { overflow: hidden; text-overflow: ellipsis; }
/* frameless drag regions live in styles.css (GLOBAL — a scoped :global()
   variant once compiled into a rule that made the whole window a drag region
   and killed every click) */
.browser-host { height: 100%; }
.reader-host { height: 100%; }
</style>
