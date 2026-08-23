<script setup lang="ts">
// The ONE metadata editor (design/state-model.md §4): used by the Title view's
// edit mode and the browser's capture panel. Every field is the same widget —
// one label row (name · provenance badge · pick/clear actions) over a full-width
// control, in a single column so it works in the 344px capture dock as-is.
// A human edit marks the field `manual`, making it untouchable for auto capture.
//
// WHICH fields exist is not decided here. The registry is served by the backend
// (design/metadata-model.md), so a field the app gains — or the user defines —
// draws itself without a line of code in this file.
import { computed } from 'vue'
import { DEFAULT_STATUSES, DEFAULT_TYPES, type FieldDef } from '../data'
import { clearField, draftState, setCoverUrl, setManual } from '../draft'
import { store } from '../store'
import Combo from './Combo.vue'
import Icon from './Icon.vue'

const props = withDefaults(defineProps<{
  capture?: boolean // show per-field pick buttons (browser context)
}>(), { capture: false })

const emit = defineEmits<{ (e: 'capture', field: string): void }>()

const d = computed(() => draftState.cur)
const rows = computed(() => store.fields.filter((f) => f.editable))

// The draft's meta as a bag: which keys are in it is the registry's business,
// not this component's.
type Bag = Record<string, unknown>
const bag = () => (d.value!.meta as unknown as Bag)

function text(f: FieldDef): string { return String(bag()[f.id] ?? '') }
function setText(f: FieldDef, value: string) { bag()[f.id] = value; setManual(f.id) }
// A chips value is a list. A draft saved before a field became one — or a
// capture that stored text — must not be spread into one chip per CHARACTER,
// which is what `v-for` over a string does.
function list(f: FieldDef): string[] {
  const v = bag()[f.id]
  if (Array.isArray(v)) return v as string[]
  return typeof v === 'string' && v.trim() ? [v] : []
}

function badge(field: string): 'auto' | 'manual' | '' {
  return d.value?.provenance[field]?.origin ?? ''
}

// Open vocabularies: the common values for the two fields that have any, plus
// everything the library already holds, minus what this title already carries.
const COMMON: Record<string, readonly string[]> = { type: DEFAULT_TYPES, status: DEFAULT_STATUSES }
function suggestions(f: FieldDef): string[] {
  const all = [...new Set([...(COMMON[f.id] ?? []), ...(store.vocab[f.vocab] ?? [])])]
  return f.control === 'chips' ? all.filter((v) => !list(f).includes(v)) : all
}

function addChip(f: FieldDef, value: string) {
  const s = value.trim()
  const b = bag()
  if (!Array.isArray(b[f.id])) b[f.id] = []
  const l = b[f.id] as string[]
  if (s && !l.includes(s)) { l.push(s); setManual(f.id) }
}
function removeChip(f: FieldDef, i: number) {
  list(f).splice(i, 1)
  setManual(f.id)
}

// `flags` is the one field whose values are named switches rather than text.
const flagDefs = [
  { key: 'adult', label: '18+', color: 'var(--adult)' },
  { key: 'ai', label: 'AI', color: 'var(--ai)' },
  { key: 'censored', label: 'Censored', color: 'var(--cens)' },
] as const
type FlagKey = (typeof flagDefs)[number]['key']
function flag(k: FlagKey): boolean { return !!d.value?.meta.flags[k] }
function toggleFlag(k: FlagKey) { if (d.value) d.value.meta.flags[k] = !d.value.meta.flags[k] }

function promptCoverUrl() {
  const url = window.prompt('Cover image URL:', d.value?.cover.sourceUrl || '')
  if (url && /^https?:\/\//.test(url.trim())) setCoverUrl(url.trim(), d.value?.meta.source.url || '')
}
function coverBg(preview: string) {
  return preview ? { background: `#181a1f url('${preview.replace(/'/g, '%27')}') center/cover no-repeat` } : {}
}
</script>

<template>
  <div v-if="d" class="me">
    <div v-for="f in rows" :key="f.id" class="fieldrow">
      <div class="lblrow">
        <span class="lbl">{{ f.label.toUpperCase() }}<span v-if="f.required" class="req">*</span></span>
        <span v-if="badge(f.id)" class="pbadge" :class="badge(f.id)">{{ badge(f.id) }}</span>
        <span v-if="f.control !== 'flags'" class="acts">
          <button v-if="props.capture" class="fact" title="Pick on page" @click="emit('capture', f.id)"><Icon name="pick" :size="12" :sw="1.9" /></button>
          <button v-if="f.control === 'cover'" class="fact wide" title="Use an image URL" @click="promptCoverUrl">URL</button>
          <button class="fact" :disabled="f.control === 'cover' && d.cover.kind === 'none'"
                  :title="f.required ? 'Clear' : `Clear — ${f.label.toLowerCase()} is optional`"
                  @click="clearField(f.id)"><Icon name="x" :size="10" :sw="2.4" /></button>
        </span>
      </div>

      <!-- one control per shape; the registry says which -->
      <input v-if="f.control === 'line'" :value="text(f)" class="in"
             :class="{ tin: f.id === 'title', needed: f.required && !text(f).trim(), yearin: f.id === 'year' }"
             :placeholder="f.placeholder" @input="setText(f, ($event.target as HTMLInputElement).value)" />

      <textarea v-else-if="f.control === 'multiline'" :value="text(f)" class="in ta"
                :placeholder="f.placeholder"
                @input="setText(f, ($event.target as HTMLTextAreaElement).value)"></textarea>

      <Combo v-else-if="f.control === 'vocab'" :model-value="text(f)" :suggestions="suggestions(f)"
             wide :placeholder="f.placeholder" @update:model-value="setText(f, $event)" />

      <div v-else-if="f.control === 'chips'" class="chips">
        <span v-for="(v, i) in list(f)" :key="v" class="chip">{{ v }}<span class="rm" @click="removeChip(f, i)">×</span></span>
        <Combo :suggestions="suggestions(f)" add-mode :placeholder="f.placeholder" @add="addChip(f, $event)" />
      </div>

      <div v-else-if="f.control === 'flags'" class="flagrow">
        <label v-for="fl in flagDefs" :key="fl.key" class="flag"
               :style="{ color: flag(fl.key) ? fl.color : 'var(--tx2)', borderColor: flag(fl.key) ? fl.color : 'var(--line)' }"
               @click="toggleFlag(fl.key)">
          <span class="box" :style="{ borderColor: flag(fl.key) ? fl.color : 'var(--line)', background: flag(fl.key) ? fl.color : 'transparent' }">
            <Icon v-if="flag(fl.key)" name="check" :size="9" :sw="3.2" style="color:var(--accent-ink)" />
          </span>{{ fl.label }}
        </label>
      </div>

      <div v-else-if="f.control === 'cover'" class="coverrow">
        <div class="coverthumb" :style="coverBg(d.cover.preview)">
          <Icon v-if="!d.cover.preview" name="image" :size="16" style="color:var(--tx3)" />
        </div>
        <div class="coverhint">
          <template v-if="d.cover.kind === 'captured'">Captured from the page — the stored bytes are exactly this image.</template>
          <template v-else-if="d.cover.kind === 'url'">From URL — fetched when you save.</template>
          <template v-else-if="d.cover.kind === 'keep'">Keeping the stored cover.</template>
          <template v-else>No cover yet{{ props.capture ? ' — pick it on the page or paste a URL' : '' }}.</template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.me { display: flex; flex-direction: column; gap: 15px; min-width: 0; }

.fieldrow { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.lblrow { display: flex; align-items: center; gap: 7px; min-height: 20px; }
.lbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); }
.req { color: var(--warn); margin-left: 2px; }
.acts { margin-left: auto; display: flex; gap: 3px; align-items: center; }
.fact { height: 20px; min-width: 20px; display: inline-flex; align-items: center; justify-content: center; border: none; border-radius: 5px; background: transparent; color: var(--tx3); cursor: pointer; padding: 0 3px; }
.fact:hover { background: var(--hover); color: var(--tx); }
.fact.wide { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .06em; padding: 0 6px; }
.fact:disabled { opacity: .4; cursor: default; }

/* provenance badge — the field widget's origin mark */
.pbadge { font: 700 8px/1 ui-monospace, monospace; letter-spacing: .1em; text-transform: uppercase; padding: 3px 6px; border-radius: 4px; flex: none; }
.pbadge.auto { color: var(--accent); background: var(--accentSoft); }
.pbadge.manual { color: var(--warn); background: rgba(214,164,79,.12); }

.in { font: 500 12.5px/1.3 system-ui; color: var(--tx); background: var(--panel); border: 1px solid var(--line); border-radius: 7px; padding: 8px 10px; outline: none; width: 100%; }
.in:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accentSoft); }
.tin { font-weight: 600; }
.tin.needed { border-color: color-mix(in srgb, var(--warn) 55%, var(--line)); }
.yearin { width: 110px; }
.ta { min-height: 76px; resize: vertical; }

.coverrow { display: flex; gap: 12px; align-items: flex-start; }
.coverthumb { width: 76px; height: 110px; flex: none; border-radius: 6px; border: 1px solid var(--line); background: var(--panel2); display: flex; align-items: center; justify-content: center; overflow: hidden; }
.coverhint { font: 400 11px/1.5 system-ui; color: var(--tx3); padding-top: 4px; min-width: 0; }


.flagrow { display: flex; gap: 7px; flex-wrap: wrap; }
.flag { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font: 500 10.5px/1 system-ui; border: 1px solid; padding: 6px 9px; border-radius: 6px; }
.flag .box { width: 12px; height: 12px; border-radius: 3px; border: 1.5px solid; display: inline-flex; align-items: center; justify-content: center; }

.chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-width: 0; }
.chip { display: inline-flex; align-items: center; gap: 6px; font: 500 11.5px/1 system-ui; color: var(--tx); background: var(--panel2); border: 1px solid var(--line); padding: 5px 9px; border-radius: 6px; max-width: 100%; }
.rm { color: var(--tx3); cursor: pointer; font-size: 13px; }
.rm:hover { color: var(--adult); }
</style>
