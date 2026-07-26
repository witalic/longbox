<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from './components/Icon.vue'
import ConfirmModal from './components/ConfirmModal.vue'
import {
  store, goView, openTitle, closeTab, titleById, toggleTitlePin, moveTitleTabBefore, init,
} from './store'
import {
  browser, openBrowse, activateTab, closeTab as closeBrowserTab, newTab, toggleTabPin,
  moveBrowserTabBefore,
} from './browser'
import { draftState } from './draft'
import { initSessions, restoreSession, sessionHistory, sessionLabel } from './sessions'
import { coverAt, hueFor } from './data'
import LibraryView from './views/LibraryView.vue'
import TitleView from './views/TitleView.vue'
import ReaderView from './views/ReaderView.vue'
import AuthorsView from './views/AuthorsView.vue'
import SourcesView from './views/SourcesView.vue'
import SettingsView from './views/SettingsView.vue'
import BrowserView from './views/BrowserView.vue'

const nav = [
  { key: 'library', label: 'Library', icon: 'library' },
  { key: 'authors', label: 'Authors', icon: 'authors' },
  { key: 'browse', label: 'Browse', icon: 'compass' },
  { key: 'sources', label: 'Sources', icon: 'sources' },
  { key: 'settings', label: 'Settings', icon: 'settings' },
] as const

function navActive(key: string): boolean {
  if (key === 'library') return ['library', 'title', 'reader'].includes(store.view)
  if (key === 'browse') return store.view === 'browser'
  return store.view === key
}
// Navigation is inert by design: the draft lives outside the views and survives
// moving around; no guard is needed here (discarding is what asks).
function navClick(key: string) {
  if (key === 'browse') openBrowse()
  else goView(key as 'library' | 'authors' | 'sources' | 'settings')
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

const tabScrollEl = ref<HTMLElement | null>(null)
function onTabWheel(e: WheelEvent) {
  tabScrollEl.value?.scrollBy({ left: e.deltaY + e.deltaX })
}
// keep the active tab visible when activation happens from elsewhere
watch(() => [store.view, store.activeTitle, browser.activeId], async () => {
  await nextTick()
  tabScrollEl.value?.querySelector('.tab.on')?.scrollIntoView({ inline: 'nearest', block: 'nearest' })
})

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
      <div ref="tabScrollEl" class="tabscroll" @wheel.prevent="onTabWheel">
        <template v-if="stripMode === 'app'">
          <div v-for="x in titleTabs" :key="x.id" class="tab" :class="{ on: ['title', 'reader'].includes(store.view) && store.activeTitle === x.id, pinned: x.pinned }"
               :title="x.t.title" @click="openTitle(x.id)">
            <span class="swatch" :style="x.t.cover ? { background: `#181a1f url('${coverAt(x.t.cover, 64)}') center/cover` } : { background: hueFor(x.id) }"></span>
            <template v-if="!x.pinned">
              <span class="lbl">{{ x.t.title }}</span>
              <span class="tact pinbtn" title="Pin tab" @click.stop="toggleTitlePin(x.id)"><Icon name="pin" :size="10" :sw="2" /></span>
              <span class="tact" title="Close" @click.stop="closeTab(x.id)"><Icon name="x" :size="10" :sw="2.4" /></span>
            </template>
            <span v-else class="tact unpin" title="Unpin tab" @click.stop="toggleTitlePin(x.id)"><Icon name="pin" :size="10" :sw="2" /></span>
          </div>
        </template>
        <template v-else-if="stripMode === 'web'">
          <div v-for="t in browserTabs" :key="t.id" class="tab web" :class="{ on: store.view === 'browser' && browser.activeId === t.id, pinned: t.pinned }"
               :title="t.title || t.url" @click="activateTab(t.id)">
            <span class="fav">{{ (t.label[0] || '?').toUpperCase() }}</span>
            <template v-if="!t.pinned">
              <span class="lbl">{{ t.title || t.label }}</span>
              <span class="tact pinbtn" title="Pin tab" @click.stop="toggleTabPin(t.id)"><Icon name="pin" :size="10" :sw="2" /></span>
              <span class="tact" title="Close" @click.stop="closeBrowserTab(t.id)"><Icon name="x" :size="10" :sw="2.4" /></span>
            </template>
            <span v-else class="tact unpin" title="Unpin tab" @click.stop="toggleTabPin(t.id)"><Icon name="pin" :size="10" :sw="2" /></span>
          </div>
        </template>
      </div>
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
            <Icon name="compass" :size="14" :sw="1.9" /><span>New browser tab</span>
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
      <div class="foot">
        <span class="dot" :style="{ background: 'var(--good)' }"></span>
        <span>Local library</span>
        <button class="themebtn" title="Toggle theme" @click="toggleTheme">
          <Icon name="moon" :size="16" />
        </button>
      </div>
    </aside>

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
.nav { padding: 12px 10px 6px; display: flex; flex-direction: column; gap: 2px; }
.navitem { height: 36px; }
.navitem { display: flex; align-items: center; gap: 11px; padding: 0 12px; border-radius: 8px; border: none; background: transparent; color: var(--tx2); font: 500 13px/1 system-ui; cursor: pointer; text-align: left; }
.navitem:hover { background: var(--hover); color: var(--tx); }
.navitem.on { background: var(--accentSoft); color: var(--accent); }
.draftdot { margin-left: auto; width: 7px; height: 7px; border-radius: 50%; background: var(--warn); }
/* SAME height as the content footers (.lfoot) so the bottom line is one bar */
.foot { margin-top: auto; height: 44px; padding: 0 16px; border-top: 1px solid var(--line); display: flex; align-items: center; gap: 9px; font: 500 11px/1.3 system-ui; color: var(--tx3); flex: none; }
.foot .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.themebtn { margin-left: auto; border: none; background: transparent; color: var(--tx2); cursor: pointer; display: flex; padding: 2px; }
.themebtn:hover { color: var(--tx); }

.main { flex: 1; min-width: 0; display: flex; flex-direction: column; background: var(--bg); }

/* the single, browser-style tab strip */
.tabbar { height: 40px; flex: none; background: var(--bg2); border-bottom: 1px solid var(--line); display: flex; align-items: center; padding-left: 14px; }
.wdiv { width: 1px; height: 22px; margin: 0 4px; background: var(--line); flex: none; }
.tabscroll { flex: 1; min-width: 0; height: 100%; display: flex; align-items: flex-end; gap: 3px; padding-top: 5px; overflow-x: auto; overflow-y: hidden; scrollbar-width: none; }
.tabscroll::-webkit-scrollbar { display: none; }
/* every tab reads as a tab: a visible card even when inactive; the active one
   shares the content background so it visually connects to the page below */
.tab { flex: none; display: inline-flex; align-items: center; gap: 7px; height: 35px; padding: 0 10px; max-width: 190px; min-width: 0; border: 1px solid color-mix(in srgb, var(--line) 55%, transparent); border-bottom: none; border-radius: 8px 8px 0 0; background: color-mix(in srgb, var(--panel) 55%, transparent); color: var(--tx3); font: 500 12px/1 system-ui; cursor: pointer; }
.tab:hover { background: var(--panel); color: var(--tx); }
/* the ACTIVE tab carries an accent top bar — background alone is too subtle
   in the light theme */
.tab.on { background: var(--bg); color: var(--tx); border-color: var(--line); box-shadow: inset 0 2.5px 0 var(--accent); }
.tab.pinned { padding: 0 8px; }
.tab .lbl { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.swatch { width: 14px; height: 19px; border-radius: 3px; flex: none; border: 1px solid var(--line); }
.fav { width: 16px; height: 16px; border-radius: 4px; background: var(--panel2); display: inline-flex; align-items: center; justify-content: center; font: 700 8px/1 system-ui; color: var(--tx3); flex: none; }
.tact { width: 16px; height: 16px; border-radius: 4px; display: inline-flex; align-items: center; justify-content: center; color: var(--tx3); flex: none; }
.tact:hover { background: var(--hover); color: var(--tx); }
.tab .pinbtn { opacity: 0; }
.tab:hover .pinbtn { opacity: 1; }
.tab .unpin { color: var(--accent); }
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
/* frameless drag regions live in styles.css (GLOBAL — a scoped :global()
   variant once compiled into a rule that made the whole window a drag region
   and killed every click) */
.browser-host { height: 100%; }
.reader-host { height: 100%; }
</style>
