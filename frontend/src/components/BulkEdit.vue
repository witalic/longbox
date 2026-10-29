<script setup lang="ts">
// Editing many titles at once, from the library's select mode
// (design/bulk-edit.md): what the selection holds, field by field — every value
// with how many of the titles hold it — and the changes to make. Each click is
// a PENDING change, drawn on its chip and listed in the footer; one Apply runs
// them all as ONE Metadata operation over exactly these titles, journaled and
// revertible. What it changes becomes the owner's (manual), like a typed edit.
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import {
  FLAG_DEFS, fieldBlocks, type FieldDef, type FlagKey, type MetaAction, type MetaHeld, type MetaPreview,
} from '../data'
import { askConfirm, editInMetadata, refreshLibrary, runVaultPass, store, visibleFields } from '../store'
import Combo from './Combo.vue'
import Icon from './Icon.vue'

const props = defineProps<{ ids: string[] }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'applied', run: { id: number; text: string }): void
}>()

// Out of reach on purpose: what is one per title — its name, the alternates,
// the cover and any long text — is edited on the title's own page.
const ONE_PER_TITLE = new Set(['title', 'alt', 'cover'])
const rows = computed(() => visibleFields('title', store.fields.filter((f) =>
  f.editable && !ONE_PER_TITLE.has(f.id) && f.control !== 'multiline')))
const blocks = computed(() => fieldBlocks(rows.value))
type Kind = 'list' | 'single' | 'flags'
function kindOf(f: FieldDef): Kind {
  return f.control === 'flags' ? 'flags' : f.type === 'list' ? 'list' : 'single'
}
const labelOf = (id: string) => (store.fields.find((f) => f.id === id)?.label ?? id).toLowerCase()

// ---- what the selection holds: follows the selection AND the library ----
const held = ref<MetaHeld | null>(null)
const heldErr = ref('')
const idsKey = computed(() => props.ids.join('\n'))
let heldSeq = 0
let heldTimer: number | undefined
watch([idsKey, () => store.titles, rows], () => {
  const seq = ++heldSeq
  window.clearTimeout(heldTimer)
  if (!props.ids.length) {
    held.value = null
    return
  }
  heldTimer = window.setTimeout(async () => {
    try {
      const got = await api.metaHeld(rows.value.map((f) => f.id), { ids: props.ids })
      if (seq !== heldSeq) return
      held.value = got
      heldErr.value = ''
    } catch (e) {
      if (seq === heldSeq) heldErr.value = e instanceof Error ? e.message : String(e)
    }
  }, 200)
}, { immediate: true })
const total = computed(() => held.value?.inScope ?? props.ids.length)
function heldIn(field: string) { return held.value?.fields[field] ?? { titles: 0, values: [] } }
function holding(field: string, value: string): number {
  return heldIn(field).values.find((v) => v.value === value)?.titles ?? 0
}

// A field's values can run to hundreds across a big selection: the most held
// come first, and the rest open on request.
const SHOWN = 24
const opened = ref<Record<string, boolean>>({})
function shownValues(f: FieldDef) {
  const all = heldIn(f.id).values
  return opened.value[f.id] ? all : all.slice(0, kindOf(f) === 'single' ? 6 : SHOWN)
}
function hiddenCount(f: FieldDef): number {
  return opened.value[f.id] ? 0 : heldIn(f.id).values.length - shownValues(f).length
}

// ---- pending changes, in the order they were made (the run applies them so) ----
interface Pending { field: string; kind: 'add' | 'remove' | 'set' | 'clear'; value: string }
const pending = ref<Pending[]>([])
function at(field: string, kind: Pending['kind'], value = ''): number {
  return pending.value.findIndex((p) => p.field === field && p.kind === kind && p.value === value)
}
function has(field: string, kind: Pending['kind'], value = ''): boolean { return at(field, kind, value) >= 0 }
function onField(field: string): Pending[] { return pending.value.filter((p) => p.field === field) }
function drop(field: string, kind: Pending['kind'], value = '') {
  const i = at(field, kind, value)
  if (i >= 0) pending.value.splice(i, 1)
}
function dropField(field: string) { pending.value = pending.value.filter((p) => p.field !== field) }

// a list: give a value to every title, take one from every title — a click on
// a pending chip takes the change back
function addValue(f: FieldDef, raw: string) {
  const v = raw.trim()
  if (!v || has(f.id, 'add', v)) return
  if (has(f.id, 'remove', v)) return drop(f.id, 'remove', v)
  if (holding(f.id, v) === total.value && !has(f.id, 'clear')) return
  pending.value.push({ field: f.id, kind: 'add', value: v })
}
function removeValue(f: FieldDef, v: string) {
  if (has(f.id, 'add', v)) return drop(f.id, 'add', v)
  if (!has(f.id, 'remove', v)) pending.value.push({ field: f.id, kind: 'remove', value: v })
}
function clearField(f: FieldDef) {
  dropField(f.id)
  pending.value.push({ field: f.id, kind: 'clear', value: '' })
}
const cleared = (f: FieldDef) => has(f.id, 'clear')
function suggestions(f: FieldDef): string[] {
  const full = new Set(heldIn(f.id).values.filter((v) => v.titles === total.value).map((v) => v.value))
  return (store.vocab[f.vocab] ?? []).filter((v) => !full.has(v) && !has(f.id, 'add', v))
}
// pending adds of values the selection does not hold yet — a held one shows on
// its own chip, unless the field is being emptied first
const newAdds = (f: FieldDef) =>
  onField(f.id).filter((p) => p.kind === 'add' && (cleared(f) || !holding(f.id, p.value)))

// a single value: set it on all, or clear it on all
const setting = ref('')
function setValue(f: FieldDef, raw: string) {
  setting.value = ''
  // type and status are open vocabularies stored folded
  const v = f.control === 'vocab' ? raw.trim().toLowerCase() : raw.trim()
  if (!v) return
  dropField(f.id)
  if (holding(f.id, v) !== total.value) pending.value.push({ field: f.id, kind: 'set', value: v })
}
const singlePending = (f: FieldDef): Pending | undefined => onField(f.id)[0]
// the box Set… opens takes the keyboard at once
const vFocusIn = { mounted: (el: HTMLElement) => el.querySelector('input')?.focus() }
function emptyCount(f: FieldDef): number { return total.value - heldIn(f.id).titles }

// flags: on for all, then off for all, then as they were
function flagHeld(k: FlagKey): number { return holding('flags', k) }
function cycleFlag(k: FlagKey) {
  if (has('flags', 'add', k)) {
    drop('flags', 'add', k)
    if (flagHeld(k)) pending.value.push({ field: 'flags', kind: 'remove', value: k })
  } else if (has('flags', 'remove', k)) {
    drop('flags', 'remove', k)
  } else if (flagHeld(k) === total.value) {
    pending.value.push({ field: 'flags', kind: 'remove', value: k })
  } else {
    pending.value.push({ field: 'flags', kind: 'add', value: k })
  }
}
function flagState(k: FlagKey): string {
  if (has('flags', 'add', k)) return '→ all'
  if (has('flags', 'remove', k)) return '→ none'
  const n = flagHeld(k)
  return n && n < total.value ? `${n}/${total.value}` : ''
}
const flagLabel = (k: string) => FLAG_DEFS.find((f) => f.key === k)?.label ?? k

// ---- the footer: what each change touches, before anything is written ----
// an add after the field is emptied reaches every title; before, only those without it
const toTheRest = (p: Pending) => p.kind === 'add' && !has(p.field, 'clear') && holding(p.field, p.value) > 0
function touches(p: Pending): number {
  if (p.kind === 'add' && has(p.field, 'clear')) return total.value
  if (p.kind === 'add' || p.kind === 'set') return total.value - holding(p.field, p.value)
  if (p.kind === 'remove') return holding(p.field, p.value)
  return heldIn(p.field).titles
}
function shownValue(p: Pending): string { return p.field === 'flags' ? flagLabel(p.value) : p.value }
const OP_MARK = { add: '+', remove: '−', set: '=', clear: '∅' } as const
function actionOf(p: Pending): MetaAction {
  if (p.kind === 'set') return { kind: 'set', field: p.field, to: p.value }
  if (p.kind === 'clear') return { kind: 'clear', field: p.field }
  return { kind: p.kind, field: p.field, values: [p.value] }
}
const actions = computed(() => pending.value.map(actionOf))

// The count Apply shows is the engine's own answer for exactly these titles
// and changes — an answer for an earlier set is dropped.
const preview = ref<MetaPreview | null>(null)
const previewErr = ref('')
const previewFor = ref('')
const previewKey = computed(() =>
  (pending.value.length && props.ids.length ? JSON.stringify([actions.value, props.ids]) : ''))
let previewSeq = 0
let previewTimer: number | undefined
watch(previewKey, (key) => {
  const seq = ++previewSeq
  window.clearTimeout(previewTimer)
  preview.value = null
  previewErr.value = ''
  if (!key) return
  previewTimer = window.setTimeout(async () => {
    try {
      const got = await api.metaPreview(actions.value, { ids: props.ids })
      if (seq !== previewSeq) return
      preview.value = got
      previewFor.value = key
    } catch (e) {
      if (seq === previewSeq) previewErr.value = e instanceof Error ? e.message : String(e)
    }
  }, 300)
})
const applying = ref(false)
const canApply = computed(() => !applying.value && !!preview.value?.titles && previewFor.value === previewKey.value)
const applyLabel = computed(() => {
  if (applying.value) return 'Applying…'
  if (!pending.value.length || !preview.value) return 'Apply'
  const n = preview.value.titles
  return n ? `Apply to ${n.toLocaleString()} title${n === 1 ? '' : 's'}` : 'Nothing to change'
})

async function apply() {
  if (!canApply.value) return
  const acts = actions.value
  const ids = [...props.ids]
  const count = acts.length
  const got: { out?: Awaited<ReturnType<typeof api.metaRun>> } = {}
  applying.value = true
  await runVaultPass(async () => { got.out = await api.metaRun(acts, { ids }, false, true) })
  applying.value = false
  const out = got.out
  if (!out) return // the pass reported why
  if (out.failed) {
    store.error = `${out.failed} title${out.failed === 1 ? ' was' : 's were'} left as they were `
      + '(unreadable, written by a newer longbox, or in use) — see the vault check.'
  }
  pending.value = []
  if (out.run) {
    emit('applied', { id: out.run, text: `Applied ${count} change${count === 1 ? '' : 's'} to `
      + `${out.changed} title${out.changed === 1 ? '' : 's'}` })
  }
  await refreshLibrary()
}

async function discardOk(): Promise<boolean> {
  const n = pending.value.length
  return !n || askConfirm({
    title: 'Discard changes', okLabel: 'Discard',
    message: `${n} change${n === 1 ? ' is' : 's are'} not applied yet. Leave and drop ${n === 1 ? 'it' : 'them'}?`,
  })
}
async function close() {
  if (await discardOk()) emit('close')
}
async function toMetadata() {
  if (await discardOk()) editInMetadata(props.ids)
}
</script>

<template>
  <aside class="bulk">
    <div class="bhd">
      <span class="t">Edit {{ total.toLocaleString() }} title{{ total === 1 ? '' : 's' }}</span>
      <button class="meta" :disabled="!ids.length" title="Rename, merge or move values across exactly these titles"
              @click="toMetadata"><Icon name="meta" :size="13" :sw="1.9" />In Metadata</button>
      <button class="iconbtn plain x" title="Close" @click="close"><Icon name="x" :size="13" :sw="2.2" /></button>
    </div>

    <div class="body scroll">
      <div v-if="!ids.length" class="state">Pick titles in the grid — the panel shows what they hold.</div>
      <div v-else-if="heldErr" class="state err">{{ heldErr }}</div>
      <div v-else-if="!held" class="state">Reading what they hold…</div>
      <template v-else>
        <div v-for="b in blocks" :key="b.key" class="grp">
          <div class="gl mono">{{ b.label }}</div>
          <div v-for="f in b.rows" :key="f.id" class="frow">
            <span class="flbl mono">{{ f.label.toUpperCase() }}</span>

            <!-- a list: every value with how many hold it -->
            <div v-if="kindOf(f) === 'list'" class="chips">
              <template v-for="v in shownValues(f)" :key="v.value">
                <button v-if="cleared(f) || has(f.id, 'remove', v.value)" class="chip rm"
                        :disabled="cleared(f)" title="Take the change back"
                        @click="drop(f.id, 'remove', v.value)">{{ v.value }} <span class="n mono">{{ v.titles }}</span></button>
                <button v-else-if="has(f.id, 'add', v.value)" class="chip filled" title="Take the change back"
                        @click="drop(f.id, 'add', v.value)">{{ v.value }} <span class="n mono">→ {{ total }}</span></button>
                <span v-else class="chip" :class="{ part: v.titles < total }">
                  {{ v.value }}
                  <span class="n mono">{{ v.titles < total ? `${v.titles}/${total}` : v.titles }}</span>
                  <button v-if="v.titles < total" class="fill mono" :title="`Give ${v.value} to the other ${total - v.titles}`"
                          @click="addValue(f, v.value)">→ all</button>
                  <button class="rmx" :title="`Take ${v.value} away from the ${v.titles} that hold it`"
                          @click="removeValue(f, v.value)">×</button>
                </span>
              </template>
              <button v-if="hiddenCount(f)" class="more mono" @click="opened[f.id] = true">+{{ hiddenCount(f) }} more</button>
              <button v-for="p in newAdds(f)" :key="'+' + p.value" class="chip add" title="Take the change back"
                      @click="drop(f.id, 'add', p.value)">+ {{ p.value }} <span class="n mono">→ {{ total }}</span></button>
              <span class="addin"><Combo :suggestions="suggestions(f)" add-mode placeholder="+ add…" @add="addValue(f, $event)" /></span>
              <button v-if="cleared(f)" class="lnk" @click="drop(f.id, 'clear')">undo clear</button>
              <button v-else-if="heldIn(f.id).titles" class="lnk" :title="`Empty ${f.label.toLowerCase()} on every selected title`"
                      @click="clearField(f)">Clear</button>
            </div>

            <!-- flags: on for all, off for all, as they were -->
            <div v-else-if="kindOf(f) === 'flags'" class="flags">
              <button v-for="fl in FLAG_DEFS" :key="fl.key" class="flag mono"
                      :class="{ on: has('flags', 'add', fl.key) || (!has('flags', 'remove', fl.key) && flagHeld(fl.key) === total),
                                some: !onField('flags').some((p) => p.value === fl.key) && flagHeld(fl.key) > 0 && flagHeld(fl.key) < total,
                                off: has('flags', 'remove', fl.key) }"
                      :style="{ '--fc': fl.color }" title="On for all, then off for all, then as they were"
                      @click="cycleFlag(fl.key)">{{ fl.label.toUpperCase() }}<span v-if="flagState(fl.key)" class="st">{{ flagState(fl.key) }}</span></button>
            </div>

            <!-- a single value: what the titles hold, and Set… / Clear -->
            <div v-else class="single">
              <template v-for="(v, i) in shownValues(f)" :key="v.value">
                <span v-if="i" class="dot">·</span>
                <span class="v" :class="{ struck: singlePending(f) }">{{ v.value }}</span><span class="n mono">{{ v.titles }}</span>
              </template>
              <button v-if="hiddenCount(f)" class="more mono" @click="opened[f.id] = true">+{{ hiddenCount(f) }} more</button>
              <template v-if="emptyCount(f)">
                <span v-if="heldIn(f.id).values.length" class="dot">·</span>
                <span class="v none">empty</span><span class="n mono">{{ emptyCount(f) }}</span>
              </template>
              <template v-if="singlePending(f)">
                <span class="to">→ {{ singlePending(f)?.kind === 'clear' ? 'empty' : singlePending(f)?.value }}</span>
                <button class="lnk" @click="dropField(f.id)">undo</button>
              </template>
              <template v-else-if="setting !== f.id">
                <button class="set" @click="setting = f.id">Set…</button>
                <button v-if="heldIn(f.id).titles" class="lnk" :title="`Empty ${f.label.toLowerCase()} on every selected title`"
                        @click="clearField(f)">Clear</button>
              </template>
              <div v-if="setting === f.id" v-focus-in class="setin">
                <input v-if="f.control === 'date' || f.control === 'number'" class="in" :type="f.control"
                       @change="setValue(f, ($event.target as HTMLInputElement).value)" @keydown.esc="setting = ''" />
                <Combo v-else wide lazy :suggestions="store.vocab[f.vocab] ?? []" :placeholder="f.placeholder"
                       @update:model-value="setValue(f, $event)" />
                <button class="lnk" @mousedown.prevent @click="setting = ''">cancel</button>
              </div>
            </div>
          </div>
        </div>
        <div class="out">Title, alternative titles, description and cover stay one per title — edit them on the title's page.</div>
      </template>
    </div>

    <div class="bft">
      <div v-if="pending.length" class="pend">
        <div v-for="(p, i) in pending" :key="i" class="prow">
          <span class="op mono" :class="p.kind">{{ OP_MARK[p.kind] }}</span>
          <span class="ptxt">
            {{ labelOf(p.field) }}
            <template v-if="p.kind === 'clear'"> emptied</template>
            <template v-else> <b>{{ shownValue(p) }}</b><template v-if="toTheRest(p)"> to the rest</template></template>
          </span>
          <span class="n mono">{{ touches(p) }} title{{ touches(p) === 1 ? '' : 's' }}</span>
          <button class="iconbtn plain px" title="Take this change back" @click="pending.splice(i, 1)"><Icon name="x" :size="11" :sw="2.4" /></button>
        </div>
      </div>
      <div v-else class="hint">Nothing to apply yet. Add, remove or set a value above — each change waits
        here, and Apply writes them all.</div>
      <div v-if="previewErr" class="warn err">{{ previewErr }}</div>
      <div v-else-if="preview && (preview.conflicts || preview.shelfMoves)" class="warn">
        <template v-if="preview.shelfMoves">{{ preview.shelfMoves }} title{{ preview.shelfMoves === 1 ? '' : 's' }} move to another shelf. </template>
        <template v-if="preview.conflicts">{{ preview.conflicts }} cannot hold the value they would get — left out.</template>
      </div>
      <div class="warn">Fields you change here become <b>yours</b>: capture will not overwrite them.</div>
      <div class="acts">
        <button class="btn ghost" :disabled="!pending.length || applying" title="Drop every change waiting here"
                @click="pending = []">Discard</button>
        <span class="sp"></span>
        <button class="btn accent" :disabled="!canApply" title="Write the changes waiting here to the selected titles"
                @click="apply">{{ applyLabel }}</button>
      </div>
    </div>
  </aside>
</template>

<style scoped>
/* docked right; the grid stays beside it and the selection can still change */
.bulk { width: 420px; flex: none; border-left: 1px solid var(--line); background: var(--bg2); display: flex; flex-direction: column; min-height: 0; }
.bhd { height: 44px; flex: none; display: flex; align-items: center; gap: 10px; padding: 0 10px 0 16px; border-bottom: 1px solid var(--line); }
.bhd .t { font: 600 15px/1 system-ui; white-space: nowrap; }
.bhd .meta { margin-left: auto; display: inline-flex; align-items: center; gap: 6px; border: none; background: none; cursor: pointer; font: 600 11.5px/1 system-ui; color: var(--accent); padding: 6px; border-radius: 6px; }
.bhd .meta:hover:not(:disabled) { background: var(--accentSoft); }
.bhd .meta:disabled { color: var(--tx3); cursor: default; }
.bhd .x { width: 30px; height: 30px; color: var(--tx3); }
.body { flex: 1; min-height: 0; padding: 4px 0 10px; }
.state { padding: 18px 16px; font: 400 12px/1.5 system-ui; color: var(--tx3); }
.state.err, .warn.err { color: var(--adult); }

.grp { padding: 12px 16px 2px; }
.gl { font-size: 9.5px; font-weight: 700; letter-spacing: .12em; color: var(--tx3); margin-bottom: 10px; }
.frow { display: grid; grid-template-columns: 78px minmax(0, 1fr); gap: 10px; align-items: start; margin-bottom: 12px; }
.flbl { font-size: 9px; font-weight: 600; letter-spacing: .12em; color: var(--tx3); line-height: 26px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.chips { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; min-width: 0; }
.chip { display: inline-flex; align-items: center; gap: 5px; height: 24px; padding: 0 8px; border-radius: 999px; background: var(--panel2); border: 1px solid var(--line); font: 500 11.5px/1 system-ui; color: var(--tx); white-space: nowrap; max-width: 100%; }
button.chip { cursor: pointer; }
.chip .n { font-size: 9.5px; color: var(--tx3); }
.chip.part { border-style: dashed; background: transparent; color: var(--tx2); }
.chip .fill, .chip .rmx { border: none; background: none; padding: 0; cursor: pointer; font: inherit; }
.chip .fill { font-size: 9.5px; font-weight: 600; color: var(--accent); }
.chip .rmx { color: var(--tx3); font-size: 12px; }
.chip .fill:hover, .chip .rmx:hover { color: var(--tx); }
.chip.add { border-color: var(--accent); background: var(--accentSoft); color: var(--accent); }
.chip.add .n, .chip.filled .n { color: var(--accent); }
.chip.filled { border-color: var(--accent); }
.chip.rm { text-decoration: line-through; color: var(--tx3); border-color: color-mix(in srgb, var(--adult) 45%, var(--line)); background: transparent; }
.chip.rm:disabled { cursor: default; }
.more { border: none; background: none; cursor: pointer; font-size: 10.5px; color: var(--tx3); padding: 0 4px; height: 24px; }
.more:hover { color: var(--accent); }
.addin { height: 24px; min-width: 96px; border: 1px dashed var(--line); border-radius: 999px; padding: 0 9px; display: inline-flex; align-items: center; }
.addin:focus-within { border: 1px solid var(--accent); box-shadow: 0 0 0 2px var(--accentSoft); }
.addin :deep(.comboin) { min-width: 80px; width: 100px; font: 500 11.5px/1 system-ui; padding: 0; }
.lnk { border: none; background: none; cursor: pointer; font: 600 11px/1 system-ui; color: var(--tx3); padding: 4px 2px; }
.lnk:hover { color: var(--accent); }

.single { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font: 500 12px/1 system-ui; color: var(--tx2); min-height: 26px; }
.single .v { color: var(--tx); }
.single .v.none { color: var(--tx3); font-style: italic; }
.single .v.struck { text-decoration: line-through; color: var(--tx3); }
.single .n { font-size: 10px; color: var(--tx3); }
.single .dot { color: var(--tx3); }
.single .to { color: var(--accent); font-weight: 600; }
.single .set { border: none; background: none; cursor: pointer; padding: 4px 2px; color: var(--accent); font: 600 11.5px/1 system-ui; }
.setin { flex-basis: 100%; display: flex; align-items: center; gap: 8px; }
.setin .in { flex: 1; height: 30px; background: var(--panel); border: 1px solid var(--line); border-radius: 6px; color: var(--tx); padding: 0 10px; font: 500 12px/1 system-ui; }
.setin .in:focus { outline: none; border-color: var(--accent); }

.flags { display: flex; gap: 6px; flex-wrap: wrap; }
.flag { display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 9px; border-radius: 6px; border: 1px solid var(--line); background: none; cursor: pointer; font-size: 10.5px; font-weight: 600; letter-spacing: .06em; color: var(--tx3); }
.flag .st { font-weight: 500; letter-spacing: 0; }
.flag.some { color: var(--tx2); border-style: dashed; }
.flag.on { color: var(--fc); border-color: var(--fc); }
.flag.off { text-decoration: line-through; border-color: color-mix(in srgb, var(--adult) 45%, var(--line)); }
.out { padding: 4px 16px 12px; font: 400 11.5px/1.5 system-ui; color: var(--tx3); }

/* the footer: what will happen, before it happens */
.bft { flex: none; border-top: 1px solid var(--line); background: var(--bg); padding: 12px 16px 14px; }
.pend { display: flex; flex-direction: column; gap: 5px; margin-bottom: 10px; max-height: 180px; overflow: auto; }
.prow { display: flex; align-items: center; gap: 8px; font: 500 12px/1.3 system-ui; color: var(--tx2); min-width: 0; }
.prow .op { width: 14px; flex: none; font-size: 12px; font-weight: 700; text-align: center; }
.prow .op.add { color: var(--good); }
.prow .op.remove, .prow .op.clear { color: var(--adult); }
.prow .op.set { color: var(--accent); }
.prow .ptxt { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.prow b { color: var(--tx); font-weight: 600; }
.prow .n { margin-left: auto; font-size: 10.5px; color: var(--tx3); white-space: nowrap; }
.prow .px { width: 22px; height: 22px; color: var(--tx3); flex: none; }
.hint { font: 400 11.5px/1.45 system-ui; color: var(--tx3); margin-bottom: 8px; }
.warn { font: 400 11.5px/1.45 system-ui; color: var(--tx3); margin-bottom: 10px; }
.warn b { color: var(--warn); font-weight: 600; }
.acts { display: flex; align-items: center; gap: 8px; }
.acts .sp { flex: 1; }
</style>
