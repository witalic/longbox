<script setup lang="ts">
// Where the library lives. Several folders can be registered and switched
// between on the fly; a switch only re-indexes what is already there, and
// forgetting one removes the entry, never the files.
//
// Opening a populated vault is the slow half, so the switch reports progress
// from the store rather than pretending it was instant.
import { computed, ref } from 'vue'
import Icon from './Icon.vue'
import { api } from '../api'
import { askConfirm, opening, setLibraryPath, store } from '../store'

const props = defineProps<{ path: string; titleCount: number; libraries: string[] }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const busy = ref(false)
const error = ref('')
const canPick = !!window.longbox?.pickFolder
const manualOpen = ref(false) // dev fallback (no Electron bridge): type the path
const manualPath = ref('')

// Reading titles is the slow half of opening a library; before the count is
// known there is nothing honest to show but "reading".
const openingLabel = computed(() => (opening.total
  ? `reading ${opening.done} of ${opening.total} titles`
  : 'reading the folder…'))

async function switchTo(p: string) {
  if (busy.value || p === props.path) return
  busy.value = true
  error.value = ''
  const applied = await setLibraryPath(p)
  busy.value = false
  if (applied) emit('changed')
  else error.value = store.error || 'could not switch'
}

async function addLibrary() {
  error.value = ''
  if (busy.value) {
    error.value = 'still opening the previous folder — one at a time'
    return
  }
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
  if (!error.value) { manualOpen.value = false; manualPath.value = '' }
}

async function forget(p: string) {
  const ok = await askConfirm({
    title: 'Forget library', okLabel: 'Forget',
    message: `Remove “${p}” from the list? The folder and all its files stay on disk — `
      + 'nothing is deleted.',
  })
  if (!ok) return
  try {
    await api.removeLibrary(p)
    emit('changed')
  } catch (e) { error.value = String(e) }
}
</script>

<template>
  <section class="card">
    <div class="rowcol">
      <div class="liblist">
        <div v-for="p in props.libraries" :key="p" class="librow" :class="{ on: p === props.path }">
          <span class="ldot" :class="{ on: p === props.path }"><span v-if="p === props.path"></span></span>
          <span class="mono lpath" :title="p">{{ p }}</span>
          <span v-if="p === props.path" class="cnt mono">{{ props.titleCount }} titles · active</span>
          <template v-else>
            <button class="btn ghost small" :disabled="busy" @click="switchTo(p)">
              {{ busy ? '…' : 'Switch' }}
            </button>
            <button class="iconbtn lx" title="Forget this library (files stay on disk)" @click="forget(p)">
              <Icon name="x" :size="12" :sw="2.2" />
            </button>
          </template>
        </div>
      </div>
      <div v-if="opening.active" class="librow opening">
        <span class="spin" />
        <span class="mono lpath" :title="opening.path">{{ opening.path }}</span>
        <span class="cnt mono">{{ openingLabel }}</span>
      </div>
      <div class="librow addrow" :class="{ off: busy }" @click="addLibrary">
        <Icon name="plus" :size="13" :sw="2.2" /><span>Add library folder…</span>
        <span v-if="!canPick" class="hint" style="margin-left:auto">
          no system dialog here — enter the path
        </span>
      </div>
      <div v-if="manualOpen" class="librow">
        <input v-model="manualPath" class="pathin mono" placeholder="C:\Users\…\Comics"
               @keydown.enter="addManual" />
        <button class="btn accent small" :disabled="busy" @click="addManual">Use</button>
      </div>
      <div v-if="error" class="note" style="color:var(--warn)">{{ error }}</div>
      <div class="note">Each library is a folder on disk (title files + a rebuildable index).
        Switching re-indexes whatever is there; forgetting only removes the entry — files are
        never touched.</div>
    </div>
  </section>
</template>

<style scoped>
/* one row per registered library; the one being opened has no entry yet
   in the list, so its row IS the progress */
.liblist { display: flex; flex-direction: column; gap: 6px; }
.librow { display: flex; align-items: center; gap: 10px; padding: 9px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel2); min-width: 0; }
.librow.on { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); background: var(--accentSoft); }
.ldot { width: 15px; height: 15px; border-radius: 50%; border: 2px solid var(--tx3); flex: none; display: flex; align-items: center; justify-content: center; }
.ldot.on { border-color: var(--accent); }
.ldot.on span { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }
.lpath { flex: 1; min-width: 0; font-size: 12px; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.addrow { cursor: pointer; border-style: dashed; color: var(--accent); font: 600 12px/1 system-ui; background: transparent; }
.addrow:hover { background: var(--accentSoft); }
.addrow.off { color: var(--tx3); cursor: default; }
.addrow.off:hover { background: transparent; }
/* the folder being opened has no entry in the list yet — this IS its row */
.opening { border-style: dashed; }
.spin {
  width: 13px; height: 13px; flex: none; border-radius: 999px;
  border: 2px solid var(--line); border-top-color: var(--accent);
  animation: libspin .8s linear infinite;
}
@keyframes libspin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spin { animation-duration: 2.4s; } }
.pathin { flex: 1; font: 500 12px/1 ui-monospace, monospace; color: var(--tx); background: var(--panel); border: 1px solid var(--accent); border-radius: 6px; height: 30px; padding: 0 9px; outline: none; }
</style>
