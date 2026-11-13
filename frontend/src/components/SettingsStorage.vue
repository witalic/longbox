<script setup lang="ts">
// Where the library lives: its vault (design/vault-home.md). Everything the app
// keeps is in it — the titles, the recipes, the window's settings, the
// browser's sign-ins — so opening another one is a restart into it, made by the
// shell, with every check of a launch. Forgetting one removes the entry, never
// the files.
import { ref } from 'vue'
import Icon from './Icon.vue'
import { api } from '../api'
import { store, switchVault } from '../store'
import { askConfirm } from '../confirm'

const props = defineProps<{ path: string; titleCount: number; libraries: string[] }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const error = ref('')
const canOpen = !!window.longbox

async function open(p: string) {
  error.value = ''
  store.error = ''
  if (!(await switchVault(p)) && store.error) error.value = store.error
}

// a vault made (in an empty folder) or found, registered, then offered
async function add(mode: 'open' | 'create') {
  error.value = ''
  const got = await window.longbox?.pickVault(mode)
  if (!got) return
  if (!got.ok) {
    error.value = got.reason || ''
    return
  }
  emit('changed')
  if (got.path) await open(got.path)
}

async function forget(p: string) {
  const ok = await askConfirm({
    title: 'Forget vault', okLabel: 'Forget',
    message: `Remove “${p}” from the list? The folder and all its files stay on disk — nothing in it is `
      + 'deleted. If it kept its sign-ins on this computer, they are removed from this computer.',
  })
  if (!ok) return
  try {
    await api.removeLibrary(p)
    await window.longbox?.forgetSignins?.(p)
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
          <span v-if="p === props.path" class="cnt mono">{{ props.titleCount }} titles · open</span>
          <template v-else>
            <button v-if="canOpen" class="btn ghost small" @click="open(p)">Open</button>
            <button class="iconbtn lx" title="Forget this vault (files stay on disk)" @click="forget(p)">
              <Icon name="x" :size="12" :sw="2.2" />
            </button>
          </template>
        </div>
      </div>
      <div v-if="canOpen" class="adds">
        <button class="btn small" @click="add('create')"><Icon name="plus" :size="13" :sw="2.2" />Create a vault…</button>
        <button class="btn small" @click="add('open')">Add an existing vault…</button>
      </div>
      <div v-if="error" class="note" style="color:var(--warn)">{{ error }}</div>
      <div class="note">Each vault is a folder of its own: its titles, recipes and settings, and the
        browser's sign-ins and cache. Opening another restarts the app into it; forgetting one only
        removes it from this list — files are never touched.
        <template v-if="!canOpen">Opening another vault needs the desktop app.</template></div>
    </div>
  </section>
</template>

<style scoped>
.liblist { display: flex; flex-direction: column; gap: 6px; }
.librow { display: flex; align-items: center; gap: 10px; padding: 9px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel2); min-width: 0; }
.librow.on { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); background: var(--accentSoft); }
.ldot { width: 15px; height: 15px; border-radius: 50%; border: 2px solid var(--tx3); flex: none; display: flex; align-items: center; justify-content: center; }
.ldot.on { border-color: var(--accent); }
.ldot.on span { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }
.lpath { flex: 1; min-width: 0; font-size: 12px; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.adds { display: flex; gap: 8px; }
</style>
