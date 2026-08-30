<script setup lang="ts">
// Settings — collapsible cards ordered by priority: Storage (libraries),
// Browser (global embedded-browser controls), Keyboard, Appearance, Index.
// Only functionality the app actually supports appears here; knobs that belong
// to a flow live IN that flow (the page-capture filter is in the capture dock).
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import RailSection from '../components/RailSection.vue'
import SettingsAbout from '../components/SettingsAbout.vue'
import SettingsFields from '../components/SettingsFields.vue'
import SettingsHealth from '../components/SettingsHealth.vue'
import SettingsMaintenance from '../components/SettingsMaintenance.vue'
import SettingsStorage from '../components/SettingsStorage.vue'
import SettingsAppearance from '../components/SettingsAppearance.vue'
import SettingsBrowser from '../components/SettingsBrowser.vue'
import SettingsKeyboard from '../components/SettingsKeyboard.vue'
import {
  pollVaultPass, runVaultPass, stopVaultPass, store, vaultPass, watchVaultPass,
} from '../store'
import { api, type CheckReport } from '../api'
import { readLocalOne, writeLocalOne } from '../local'
import { keyOverrides } from '../keys'

// ---- the sections, and the rail that navigates them ----
//
// This page used to be eight collapsible cards in one scroll, which is what you
// build when a page has no navigation: the collapsed summaries existed only so
// you could tell what was inside without opening everything. The rail is the
// app's own — Browse and Sources teleport into the same one — it carries each
// section's state as a tag, and a new section is a row in it rather than more
// scroll for everyone.
//
// The VIEW owns the data and the state (RailSection's own rule); the section
// components own the markup.
type SectionId = 'storage' | 'health' | 'maintenance' | 'fields'
  | 'browser' | 'appearance' | 'keyboard' | 'about'

const SECTIONS: { group: string; items: { id: SectionId; label: string }[] }[] = [
  { group: 'Library', items: [
    { id: 'storage', label: 'Storage' },
    { id: 'health', label: 'Health' },
    { id: 'maintenance', label: 'Maintenance' },
    { id: 'fields', label: 'Fields' },
  ] },
  { group: 'App', items: [
    { id: 'browser', label: 'Browser' },
    { id: 'appearance', label: 'Appearance' },
    { id: 'keyboard', label: 'Keyboard' },
    { id: 'about', label: 'About' },
  ] },
]
const SECTION_IDS: readonly SectionId[] = SECTIONS.flatMap((g) => g.items.map((i) => i.id))

const sec = ref<SectionId>(readLocalOne('lb.settingsSection', SECTION_IDS, 'storage'))
watch(sec, (v) => writeLocalOne('lb.settingsSection', v))
const secLabel = computed(() =>
  SECTIONS.flatMap((g) => g.items).find((i) => i.id === sec.value)?.label ?? 'Settings')

// What each rail row says about itself. This is the whole reason the accordion
// existed — being able to tell what is inside without opening it — and here it
// costs a row, not a click.
function railTag(id: SectionId): string {
  if (id === 'storage') return String(libraries.value.length)
  if (id === 'fields') return String(store.fields.length)
  if (id === 'appearance') return store.theme
  if (id === 'keyboard') return Object.keys(keyOverrides).length ? 'custom' : ''
  if (id === 'about') return appMeta.value.version || ''
  if (id === 'health') {
    if (!health.value) return ''
    const bad = health.value.broken.total
    return bad ? bad.toLocaleString() : '✓'
  }
  return ''
}
function railBad(id: SectionId): boolean {
  return id === 'health' && !!health.value && health.value.broken.total > 0
}

// The head's right-hand number, where the section HAS one worth stating.
const headCount = computed(() => {
  if (sec.value === 'storage') return `${titleCount.value} titles`
  if (sec.value === 'fields') {
    const mine = store.fields.filter((f) => !f.builtin).length
    return mine ? `${store.fields.length} fields · ${mine} yours` : `${store.fields.length} fields`
  }
  return ''
})

const path = ref('…')
const titleCount = ref(0)
const libraries = ref<string[]>([])
const homepage = ref('')
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

// The pass belongs to the sidecar, and `store.ts` follows it for the whole app
// — walking to the library and back must not lose the progress of something
// still running. This view only keeps the poll warm while it is on screen.
onMounted(() => { watchVaultPass(true); void pollVaultPass(true) })
onBeforeUnmount(() => watchVaultPass(false))

// The last check, kept HERE because the rail says whether the library needs you
// — and the rail is the page's, not the section's.
const health = ref<CheckReport | null>(null)

</script>

<template>
  <div class="viewcol">
    <!-- the app's own rail, the one Browse and Sources use -->
    <Teleport v-if="store.view === 'settings'" to="#siderail">
      <RailSection v-for="g in SECTIONS" :key="g.group" :label="g.group">
        <button v-for="i in g.items" :key="i.id" :class="{ on: sec === i.id }" @click="sec = i.id">
          {{ i.label }}
          <span v-if="railTag(i.id)" class="tag mono" :class="{ bad: railBad(i.id) }">{{ railTag(i.id) }}</span>
        </button>
      </RailSection>
    </Teleport>

    <div class="head">
      <h1 class="h1">{{ secLabel }}</h1>
      <span v-if="headCount" class="count">{{ headCount }}</span>
    </div>
    <div class="viewscroll scroll">
    <div class="wrap">

    <!-- 1 · STORAGE -->
    <SettingsStorage v-if="sec === 'storage'" :path="path" :title-count="titleCount"
                     :libraries="libraries" @changed="refresh" />

    <!-- 2 · FIELDS -->
    <SettingsFields v-if="sec === 'fields'" />

    <!-- 3 · BROWSER -->
    <SettingsBrowser v-if="sec === 'browser'" v-model:homepage="homepage" />

    <!-- 4 · KEYBOARD -->
    <SettingsKeyboard v-if="sec === 'keyboard'" />

    <!-- 5 · APPEARANCE -->
    <SettingsAppearance v-if="sec === 'appearance'" />

    <!-- 6 · MAINTENANCE -->
    <SettingsMaintenance v-if="sec === 'maintenance'" :active-op="vaultPass.key"
                         :done="vaultPass.done" :total="vaultPass.total" :running="vaultPass.op"
                         @run="runVaultPass" @stop="stopVaultPass" @counted="titleCount = $event" />


    <!-- 7 · VAULT HEALTH — the questions a listing never asks -->
    <SettingsHealth v-if="sec === 'health'" :active-op="vaultPass.key"
                    :done="vaultPass.done" :total="vaultPass.total" :running="vaultPass.op"
                    @run="runVaultPass" @stop="stopVaultPass" @report="health = $event" />

    <!-- 8 · ABOUT — the app's metadata, from ONE source (app-meta.json) -->
    <SettingsAbout v-if="sec === 'about'" :meta="appMeta" />
    </div>
    </div>
    <!-- the 44px bottom band is the view's line grid; the app's identity lives
         in the About card alone, never twice on one screen -->
    <div class="lfoot"></div>
  </div>
</template>

<style scoped>
.head { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 12px; padding: 0 24px; }
/* left-aligned like every other view; the cards just cap their width */
.wrap { max-width: 820px; padding: 20px 24px 40px; }
/* the rail row's own state, the thing the collapsed card headers used to say */
.tag { margin-left: auto; font-size: 10px; color: var(--tx3); }
.tag.bad { color: var(--adult); }
button.on .tag { color: var(--accent); }
:deep(.mini) { font-size: 10px; color: var(--tx3); font-weight: 500; }
:deep(.row) { padding: 16px 18px; display: flex; align-items: center; gap: 16px; }
:deep(.row + .row) { border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent); }
:deep(.rowcol) { padding: 16px 18px; display: flex; flex-direction: column; gap: 10px; }
:deep(.rowcol + .row) { border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent); }


:deep(.text) { flex: 1; }
:deep(.label) { font: 500 13px/1 system-ui; color: var(--tx); }
:deep(.hint) { margin-top: 5px; font: 400 12px/1.4 system-ui; color: var(--tx3); }

:deep(.lx) { width: 24px; height: 24px; flex: none; }
:deep(.lx:hover) { color: var(--adult); }
:deep(.hpin) { width: 320px; font: 500 12px/1 ui-monospace, monospace; color: var(--tx); background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; height: 30px; padding: 0 9px; outline: none; }
:deep(.hpin:focus) { border-color: var(--accent); }
:deep(.cnt) { font-size: 11px; color: var(--tx3); flex: none; }
:deep(.note) { font: 400 11.5px/1.5 system-ui; color: var(--tx3); }

:deep(.applogo) { width: 34px; height: 34px; display: inline-flex; flex: none; }

:deep(.keyrow) { display: flex; align-items: center; gap: 10px; }
:deep(.keylabel) { flex: 1; font: 400 12.5px/1.3 system-ui; color: var(--tx2); }
:deep(.keychip) { min-width: 120px; text-align: center; font-size: 11px; color: var(--tx); background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; padding: 6px 10px; cursor: pointer; }
:deep(.keychip:hover) { border-color: var(--accent); }
:deep(.keychip.capturing) { border-color: var(--warn); color: var(--warn); background: rgba(214,164,79,.08); }
:deep(.keychip.over) { border-color: color-mix(in srgb, var(--accent) 50%, var(--line)); color: var(--accent); }
</style>
