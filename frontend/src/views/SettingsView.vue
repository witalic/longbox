<script setup lang="ts">
// Settings — collapsible cards ordered by priority: Storage (libraries),
// Browser (global embedded-browser controls), Keyboard, Appearance, Index.
// Only functionality the app actually supports appears here.
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'
import { store, setLibraryPath, askConfirm, type ThemePref } from '../store'
import { api } from '../api'
import { KEY_ACTIONS, bindingsFor, isOverridden, keyLabel, rebind, resetKey, resetAllKeys, keyOverrides } from '../keys'

// ---- collapsible cards (persisted; default: all open) ----
const openSec = reactive<Record<string, boolean>>((() => {
  try { return JSON.parse(localStorage.getItem('lb.settingsOpen') || '{}') } catch { return {} }
})())
watch(openSec, () => {
  try { localStorage.setItem('lb.settingsOpen', JSON.stringify(openSec)) } catch { /* ignore */ }
}, { deep: true })
function isOpen(k: string): boolean { return openSec[k] !== false }
function toggleSec(k: string) { openSec[k] = !isOpen(k) }

const themeChips: { v: ThemePref; label: string }[] = [
  { v: 'dark', label: 'Dark' }, { v: 'light', label: 'Light' }, { v: 'system', label: 'System' },
]

const path = ref('…')
const titleCount = ref(0)
const libraries = ref<string[]>([])
const busy = ref(false)
const homepage = ref('')
const storageError = ref('')
const appMeta = ref<{ name?: string; version?: string; updated?: string; description?: string }>({})

async function refresh() {
  try {
    const s = await api.settings()
    path.value = s.library_path
    titleCount.value = s.title_count
    homepage.value = s.homepage
    libraries.value = s.libraries
    appMeta.value = s.app || {}
  } catch { /* backend not ready */ }
}
onMounted(refresh)

async function saveHomepage() {
  const hp = homepage.value.trim()
  if (!hp) return
  try {
    const s = await api.setHomepage(hp)
    homepage.value = s.homepage
    store.browseHomepage = s.homepage
  } catch { /* ignore */ }
}

// ---- global browser hygiene (Electron bridge) ----
const canBridge = !!window.longbox
const browserFlash = ref('')
async function clearBrowsing(what: 'cookies' | 'cache') {
  if (!window.longbox) return
  if (what === 'cookies') {
    const ok = await askConfirm({
      title: 'Clear site cookies', okLabel: 'Clear', danger: true,
      message: 'Remove cookies for ALL sites in the embedded browser? You will be signed out of the sources. The app itself stays signed in.',
    })
    if (!ok) return
  }
  const done = await window.longbox.clearBrowsing(what)
  browserFlash.value = done ? (what === 'cookies' ? '✓ site cookies cleared' : '✓ HTTP cache cleared') : 'not available'
  setTimeout(() => { browserFlash.value = '' }, 2600)
}

// ---- libraries: switch on the fly, add via the OS folder picker, forget ----
const canPick = !!window.longbox?.pickFolder
const manualOpen = ref(false) // dev fallback (no Electron bridge): type the path
const manualPath = ref('')

async function switchTo(p: string) {
  if (busy.value || p === path.value) return
  busy.value = true
  storageError.value = ''
  const applied = await setLibraryPath(p)
  busy.value = false
  if (applied) await refresh()
  else storageError.value = store.error || 'could not switch'
}
async function addLibrary() {
  storageError.value = ''
  if (canPick) {
    const picked = await window.longbox!.pickFolder('Choose a library folder')
    if (picked) await switchTo(picked)
  } else {
    manualOpen.value = !manualOpen.value
  }
}
async function addManual() {
  const p = manualPath.value.trim()
  if (!p) return
  await switchTo(p)
  if (!storageError.value) { manualOpen.value = false; manualPath.value = '' }
}
async function forget(p: string) {
  const ok = await askConfirm({
    title: 'Forget library', okLabel: 'Forget',
    message: `Remove “${p}” from the list? The folder and all its files stay on disk — nothing is deleted.`,
  })
  if (!ok) return
  try {
    const s = await api.removeLibrary(p)
    libraries.value = s.libraries
  } catch (e) { storageError.value = String(e) }
}

// ---- index rebuild: explicit confirm, live progress from a parallel poll ----
const rebuilding = ref(false)
const rebuildProgress = ref('')
async function rebuild() {
  const ok = await askConfirm({
    title: 'Rebuild the search index', okLabel: 'Rebuild',
    message: 'Re-read every title on disk and rebuild the search index from scratch? '
      + 'Your files are NEVER modified — this only recreates the index. '
      + 'With a large library it can take a while; the app keeps working meanwhile.',
  })
  if (!ok) return
  rebuilding.value = true
  rebuildProgress.value = '…'
  const run = api.rebuildIndex()
    .then((s) => { titleCount.value = s.title_count; return true })
    .catch((e) => { store.error = String(e); return false })
  const poll = window.setInterval(async () => {
    try {
      const st = await api.rebuildStatus()
      if (st.running && st.total) rebuildProgress.value = `${st.done} / ${st.total}`
    } catch { /* keep the last value */ }
  }, 400)
  const done = await run
  window.clearInterval(poll)
  rebuilding.value = false
  rebuildProgress.value = done ? `✓ rebuilt — ${titleCount.value} titles` : ''
  setTimeout(() => { rebuildProgress.value = '' }, 3000)
}

// ---- keyboard: click a binding → press the new key ----
const capturing = ref<string | null>(null)
function onCaptureKey(e: KeyboardEvent) {
  if (!capturing.value) return
  e.preventDefault()
  e.stopPropagation()
  // modifier keys alone don't finish a capture
  if (['Shift', 'Control', 'Alt', 'Meta'].includes(e.key)) return
  rebind(capturing.value, [e.code]) // PHYSICAL key — layout-independent
  capturing.value = null
}
onMounted(() => window.addEventListener('keydown', onCaptureKey, true))
onBeforeUnmount(() => window.removeEventListener('keydown', onCaptureKey, true))
const anyOverride = computed(() => Object.keys(keyOverrides).length > 0)
</script>

<template>
  <div class="viewcol">
    <div class="head"><h1 class="h1">Settings</h1></div>
    <div class="viewscroll scroll">
    <div class="wrap">

    <!-- 1 · STORAGE -->
    <section class="card">
      <div class="title click" @click="toggleSec('storage')">
        <Icon name="chevron" :size="11" :sw="2.4" class="chev" :style="{ transform: isOpen('storage') ? '' : 'rotate(-90deg)' }" />
        Storage
        <span v-if="!isOpen('storage')" class="mini mono">{{ libraries.length }} librar{{ libraries.length === 1 ? 'y' : 'ies' }} · {{ titleCount }} titles</span>
      </div>
      <div v-if="isOpen('storage')" class="rowcol">
        <div class="liblist">
          <div v-for="p in libraries" :key="p" class="librow" :class="{ on: p === path }">
            <span class="ldot" :class="{ on: p === path }"><span v-if="p === path"></span></span>
            <span class="mono lpath" :title="p">{{ p }}</span>
            <span v-if="p === path" class="cnt mono">{{ titleCount }} titles · active</span>
            <template v-else>
              <button class="btn ghost lact" :disabled="busy" @click="switchTo(p)">{{ busy ? '…' : 'Switch' }}</button>
              <button class="iconbtn lx" title="Forget this library (files stay on disk)" @click="forget(p)"><Icon name="x" :size="12" :sw="2.2" /></button>
            </template>
          </div>
        </div>
        <div class="librow addrow" @click="addLibrary">
          <Icon name="plus" :size="13" :sw="2.2" /><span>Add library folder…</span>
          <span v-if="!canPick" class="hint" style="margin-left:auto">no system dialog here — enter the path</span>
        </div>
        <div v-if="manualOpen" class="librow">
          <input v-model="manualPath" class="pathin mono" placeholder="C:\Users\…\Comics" @keydown.enter="addManual" />
          <button class="btn accent" style="height:28px" :disabled="busy" @click="addManual">Use</button>
        </div>
        <div v-if="storageError" class="note" style="color:var(--warn)">{{ storageError }}</div>
        <div class="note">Each library is a folder on disk (title files + a rebuildable index). Switching re-indexes
          whatever is there; forgetting only removes the entry — files are never touched.</div>
      </div>
    </section>

    <!-- 2 · BROWSER -->
    <section class="card">
      <div class="title click" @click="toggleSec('browser')">
        <Icon name="chevron" :size="11" :sw="2.4" class="chev" :style="{ transform: isOpen('browser') ? '' : 'rotate(-90deg)' }" />
        Browser
        <span v-if="browserFlash" class="mini" style="color:var(--good)">{{ browserFlash }}</span>
      </div>
      <template v-if="isOpen('browser')">
        <div class="row">
          <div class="text"><div class="label">Homepage</div><div class="hint">The page a fresh Browse tab opens.</div></div>
          <input v-model="homepage" class="hpin mono" placeholder="https://www.google.com" @blur="saveHomepage" @keydown.enter="saveHomepage" />
        </div>
        <div class="row">
          <div class="text"><div class="label">Site cookies</div><div class="hint">Every site in the embedded browser — signs you out of the sources. The app's own session is kept.</div></div>
          <button class="btn" :disabled="!canBridge" @click="clearBrowsing('cookies')">Clear cookies</button>
        </div>
        <div class="row">
          <div class="text"><div class="label">HTTP cache</div><div class="hint">Cached pages and images of the embedded browser.</div></div>
          <button class="btn" :disabled="!canBridge" @click="clearBrowsing('cache')">Clear cache</button>
        </div>
        <div v-if="!canBridge" class="rowcol" style="padding-top:0"><div class="note">Clearing needs the desktop shell — not available in a plain dev browser.</div></div>
      </template>
    </section>

    <!-- 3 · KEYBOARD -->
    <section class="card">
      <div class="title click" @click="toggleSec('keys')">
        <Icon name="chevron" :size="11" :sw="2.4" class="chev" :style="{ transform: isOpen('keys') ? '' : 'rotate(-90deg)' }" />
        Keyboard
        <span v-if="!isOpen('keys') && anyOverride" class="mini mono">customized</span>
        <button v-if="isOpen('keys') && anyOverride" class="btn ghost" style="margin-left:auto;height:26px;font-size:11px" @click.stop="resetAllKeys()">Reset all</button>
      </div>
      <div v-if="isOpen('keys')" class="rowcol">
        <div v-for="a in KEY_ACTIONS" :key="a.id" class="keyrow">
          <span class="keylabel">{{ a.label }}</span>
          <button class="keychip mono" :class="{ capturing: capturing === a.id, over: isOverridden(a.id) }"
                  :title="capturing === a.id ? 'Press the new key (modifiers alone don’t count)' : 'Click, then press a key to rebind'"
                  @click="capturing = capturing === a.id ? null : a.id">
            {{ capturing === a.id ? 'press a key…' : bindingsFor(a.id).map(keyLabel).join(' / ') }}
          </button>
          <button v-if="isOverridden(a.id)" class="iconbtn lx" title="Reset to default" @click="resetKey(a.id)"><Icon name="refresh" :size="12" :sw="2" /></button>
          <span v-else style="width:24px"></span>
        </div>
        <div class="note">Shortcuts apply in the reader. Click a binding, press the new key; rebinding replaces the default.</div>
      </div>
    </section>

    <!-- 4 · APPEARANCE -->
    <section class="card">
      <div class="title click" @click="toggleSec('appearance')">
        <Icon name="chevron" :size="11" :sw="2.4" class="chev" :style="{ transform: isOpen('appearance') ? '' : 'rotate(-90deg)' }" />
        Appearance
        <span v-if="!isOpen('appearance')" class="mini mono">{{ store.theme }}</span>
      </div>
      <div v-if="isOpen('appearance')" class="row">
        <div class="text"><div class="label">Theme</div><div class="hint">Switch the whole app between dark and light. Your choice is remembered.</div></div>
        <div class="seg">
          <button v-for="c in themeChips" :key="c.v" class="opt" :class="{ on: store.theme === c.v }" @click="store.theme = c.v">{{ c.label }}</button>
        </div>
      </div>
    </section>

    <!-- 5 · MAINTENANCE -->
    <section class="card">
      <div class="title click" @click="toggleSec('index')">
        <Icon name="chevron" :size="11" :sw="2.4" class="chev" :style="{ transform: isOpen('index') ? '' : 'rotate(-90deg)' }" />
        Library index
      </div>
      <div v-if="isOpen('index')" class="row">
        <div class="text">
          <div class="label">SQLite search index</div>
          <div class="hint">The search/filter index is a cache derived from the title files on disk.
            Rebuild re-reads every title and recreates it from scratch — useful when the library folder was
            changed outside the app or search results look wrong. Your files are never modified.</div>
        </div>
        <span v-if="rebuildProgress" class="mini mono" :style="{ color: rebuildProgress.startsWith('✓') ? 'var(--good)' : 'var(--accent)' }">{{ rebuildProgress }}</span>
        <button class="btn" :disabled="rebuilding" @click="rebuild"><Icon name="refresh" :size="14" />{{ rebuilding ? 'Rebuilding…' : 'Rebuild' }}</button>
      </div>
    </section>

    <!-- 6 · ABOUT — the app's metadata, from ONE source (app-meta.json) -->
    <section class="card">
      <div class="title click" @click="toggleSec('about')">
        <Icon name="chevron" :size="11" :sw="2.4" class="chev" :style="{ transform: isOpen('about') ? '' : 'rotate(-90deg)' }" />
        About
        <span v-if="!isOpen('about')" class="mini mono">v{{ appMeta.version || '?' }}</span>
      </div>
      <div v-if="isOpen('about')" class="row">
        <span class="applogo"><svg viewBox="0 0 72 72" style="width:34px;height:34px" aria-hidden="true"><defs><linearGradient id="lbgrad2" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8194f4"/><stop offset="1" stop-color="#3e4eb2"/></linearGradient></defs><rect width="72" height="72" rx="17" fill="url(#lbgrad2)"/><path d="M27 19h9v25h13v9H27z" fill="#fff"/></svg></span>
        <div class="text">
          <div class="label">{{ appMeta.name || 'longbox' }} <span class="mini mono" style="margin-left:6px">v{{ appMeta.version || '?' }}</span></div>
          <div class="hint">{{ appMeta.description || '' }}</div>
        </div>
        <span v-if="appMeta.updated" class="mini mono">updated {{ appMeta.updated }}</span>
      </div>
    </section>
    </div>
    </div>
    <div class="lfoot">
      <span class="mono lfinfo">{{ appMeta.name || 'longbox' }} v{{ appMeta.version || '?' }}<template v-if="appMeta.updated"> · updated {{ appMeta.updated }}</template></span>
    </div>
  </div>
</template>

<style scoped>
.head { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; align-items: center; padding: 0 24px; }
/* left-aligned like every other view; the cards just cap their width */
.wrap { max-width: 820px; padding: 20px 24px 40px; }
.card { margin-bottom: 16px; }
/* the header follows the card's rounding (11px outer − 1px border), and a
   COLLAPSED card (header is the only child) drops the divider entirely —
   otherwise it paints as a stray line under the rounded bottom edge */
.title { padding: 14px 18px; border-bottom: 1px solid var(--line); border-radius: 10px 10px 0 0; font: 600 13px/1 system-ui; color: var(--tx); display: flex; align-items: center; gap: 10px; }
.card > .title:last-child { border-bottom: none; border-radius: 10px; }
.title.click { cursor: pointer; user-select: none; }
.title.click:hover { background: var(--hover); }
.chev { color: var(--tx3); transition: transform .12s; flex: none; }
.mini { font-size: 10px; color: var(--tx3); font-weight: 500; }
.row { padding: 16px 18px; display: flex; align-items: center; gap: 16px; }
.row + .row { border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent); }
.rowcol { padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }
.text { flex: 1; }
.label { font: 500 13px/1 system-ui; color: var(--tx); }
.hint { margin-top: 5px; font: 400 12px/1.4 system-ui; color: var(--tx3); }

.liblist { display: flex; flex-direction: column; gap: 6px; }
.librow { display: flex; align-items: center; gap: 10px; padding: 9px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel2); min-width: 0; }
.librow.on { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); background: var(--accentSoft); }
.ldot { width: 15px; height: 15px; border-radius: 50%; border: 2px solid var(--tx3); flex: none; display: flex; align-items: center; justify-content: center; }
.ldot.on { border-color: var(--accent); }
.ldot.on span { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }
.lpath { flex: 1; min-width: 0; font-size: 12px; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lact { height: 26px; padding: 0 10px; font-size: 11.5px; }
.lx { width: 24px; height: 24px; flex: none; }
.lx:hover { color: var(--adult); }
.addrow { cursor: pointer; border-style: dashed; color: var(--accent); font: 600 12px/1 system-ui; background: transparent; }
.addrow:hover { background: var(--accentSoft); }
.pathin { flex: 1; font: 500 12px/1 ui-monospace, monospace; color: var(--tx); background: var(--panel); border: 1px solid var(--accent); border-radius: 6px; height: 30px; padding: 0 9px; outline: none; }
.hpin { width: 320px; font: 500 12px/1 ui-monospace, monospace; color: var(--tx); background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; height: 30px; padding: 0 9px; outline: none; }
.hpin:focus { border-color: var(--accent); }
.cnt { font-size: 11px; color: var(--tx3); flex: none; }
.note { font: 400 11.5px/1.5 system-ui; color: var(--tx3); }

.applogo { width: 34px; height: 34px; display: inline-flex; flex: none; }

.keyrow { display: flex; align-items: center; gap: 10px; }
.keylabel { flex: 1; font: 400 12.5px/1.3 system-ui; color: var(--tx2); }
.keychip { min-width: 120px; text-align: center; font-size: 11px; color: var(--tx); background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; padding: 6px 10px; cursor: pointer; }
.keychip:hover { border-color: var(--accent); }
.keychip.capturing { border-color: var(--warn); color: var(--warn); background: rgba(214,164,79,.08); }
.keychip.over { border-color: color-mix(in srgb, var(--accent) 50%, var(--line)); color: var(--accent); }
</style>
