<script setup lang="ts">
// The metadata registry — the one description of what a field IS.
//
// The list IS the manager: a row per field, and the one you open unfolds its
// definition underneath. Built-ins are shown but locked — their id, type and
// behaviour carry logic, not just a value (backend library/fields.py).
import { computed, onMounted, ref } from 'vue'
import Icon from './Icon.vue'
import SettingsFieldForm from './SettingsFieldForm.vue'
import { api } from '../api'
import type { FieldDef } from '../data'
import { askConfirm, refreshLibrary, store } from '../store'

const custom = computed(() => store.fields.filter((f) => !f.builtin))
const builtin = computed(() => store.fields.filter((f) => f.builtin))

// How many titles hold a value here — asked of the backend, because the browser
// only ever holds the page the library is currently showing. Counting there made
// every field report whatever the library happened to be filtered to.
const usage = ref<Record<string, number>>({})
async function loadUsage() {
  try { usage.value = await api.fieldUsage() } catch { /* the counts stay blank */ }
}
onMounted(loadUsage)
function usedBy(f: FieldDef): string {
  const n = usage.value[f.id]
  return n === undefined ? '' : `${n.toLocaleString()} titles`
}

const editing = ref<string | null>(null) // field id being edited, '' while creating
const busy = ref(false)
const err = ref('')

function open(f: FieldDef) { err.value = ''; editing.value = f.id }
function startNew() { err.value = ''; editing.value = '' }

type Payload = { id: string; label: string; type: string; facet: boolean; join: string }

async function save(v: Payload) {
  busy.value = true
  err.value = ''
  try {
    store.fields = await api.putField(v.id, {
      label: v.label, type: v.type, facet: v.facet, join: v.join,
    })
    editing.value = null
    // a new facet changes what the library can be asked; a retype changes the counts
    await Promise.all([refreshLibrary(), loadUsage()])
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function remove(f: FieldDef, values: boolean) {
  const ok = await askConfirm(values ? {
    title: `Remove “${f.label}” and its values`, danger: true, okLabel: 'Remove both',
    message: `Every title that holds a value in “${f.label}” loses it, and the field stops `
      + 'being offered. This is the only way to take the data with the definition, and it '
      + 'cannot be undone by defining the field again.',
  } : {
    title: 'Remove field', danger: true, okLabel: 'Remove',
    message: `Stop offering “${f.label}”? Every title that holds a value keeps it — `
      + 'defining the field again brings those values back.',
  })
  if (!ok) return
  busy.value = true
  try {
    store.fields = await api.deleteField(f.id, values)
    editing.value = null
    await Promise.all([refreshLibrary(), loadUsage()])
  } catch (e) {
    err.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section class="card">
    <div class="rowcol">
      <div class="hint" style="margin-top:0">
        One registry draws the metadata editor, the filters and the capture picker. Add a field
        here and every one of them offers it — no screen has a list of its own.
      </div>

      <div class="flbl">YOURS</div>
      <div v-if="!custom.length" class="hint">None yet.</div>
      <template v-for="f in custom" :key="f.id">
        <div class="frow" :class="{ on: editing === f.id }" @click="open(f)">
          <span class="fnm">{{ f.label }}</span>
          <span class="ftype">{{ f.type }}</span>
          <span class="fwhere">{{ f.facet ? 'title page · filters' : 'title page' }}</span>
          <span class="fused mono" title="Titles with a value, among the ones the library currently lists">
            {{ usedBy(f) }}
          </span>
        </div>
        <SettingsFieldForm v-if="editing === f.id" :field="f" :used-count="usage[f.id] ?? 0"
                           :busy="busy" :error="err"
                           @save="save" @cancel="editing = null" @remove="(v: boolean) => remove(f, v)" />
      </template>

      <div v-if="editing !== ''" class="fnew" @click="startNew">
        <Icon name="plus" :size="13" :sw="2.2" />New field
      </div>
      <SettingsFieldForm v-else :field="null" :used-count="0" :busy="busy" :error="err"
                         @save="save" @cancel="editing = null" />

      <div class="flbl" style="margin-top:6px">
        BUILT IN<span class="flblhint">name and type are fixed — hide them where they are in the way</span>
      </div>
      <div v-for="f in builtin" :key="f.id" class="frow locked">
        <span class="fnm">{{ f.label }}</span>
        <span class="ftype">{{ f.type }}</span>
        <span class="fwhere">{{ f.facet ? 'title page · filters' : 'title page' }}</span>
        <span class="fused mono" title="Titles with a value, among the ones the library currently lists">
          {{ usedBy(f) }}
        </span>
        <Icon name="lock" :size="12" :sw="2" style="color:var(--tx3);opacity:.5" />
      </div>
    </div>
  </section>
</template>

<style scoped>
/* the registry list — a row per field, in the settings' own row grammar */
.flbl { display: flex; align-items: center; gap: 10px; font: 700 9.5px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); margin-top: 4px; }
.flblhint { font: 400 10.5px/1 system-ui; letter-spacing: 0; text-transform: none; }
.frow { display: flex; align-items: center; gap: 12px; height: 34px; padding: 0 8px; border-radius: 7px; color: var(--tx2); cursor: pointer; }
.frow:hover { background: var(--hover); }
.frow.on { background: var(--panel2); }
.frow.locked { cursor: default; }
.frow.locked:hover { background: transparent; }
.fnm { flex: none; min-width: 132px; font: 600 12.5px/1 system-ui; color: var(--tx); }
/* wide enough for the longest type name the registry has — a fixed 62px fit
   "text" and "list" and let "description" run over the column beside it */
.ftype { flex: none; width: 92px; font: 700 8.5px/1 ui-monospace, monospace; letter-spacing: .1em; text-transform: uppercase; color: var(--tx3); border: 1px solid var(--line); border-radius: 4px; padding: 4px 5px; text-align: center; white-space: nowrap; overflow: hidden; }
.fwhere { flex: 1; min-width: 0; font: 400 11px/1 system-ui; color: var(--tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fused { flex: none; width: 78px; text-align: right; font-size: 10.5px; color: var(--tx3); }
.fnew { display: flex; align-items: center; gap: 9px; height: 34px; padding: 0 8px; border-radius: 7px; color: var(--tx3); font: 600 12.5px/1 system-ui; cursor: pointer; }
.fnew:hover { background: var(--hover); color: var(--accent); }
</style>
