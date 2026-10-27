<script setup lang="ts">
// The metadata an import gives its works (design/folder-import.md §4): the same
// blocks and controls a title's editor draws, from the same registry, over a
// bag of values instead of a draft. A filled field goes to every work, an empty
// one is left alone; any value may hold a token (`{site}`, `{creator}`) that is
// filled in per work.
//
// Scoped to ONE work, the bag is that work's own: every work's values show dim
// beside it, a list gains what is added here and a single field is replaced.
import { computed } from 'vue'
import { DEFAULT_STATUSES, fieldBlocks, type FieldDef, type ImportValue } from '../data'
import { store } from '../store'
import Combo from './Combo.vue'

const props = withDefaults(defineProps<{
  fields: FieldDef[]
  inherited?: Record<string, ImportValue> // every work's values, when scoped to one
  locked?: Record<string, string>         // field id → the token a folder level puts there
}>(), { inherited: () => ({}), locked: () => ({}) })
const values = defineModel<Record<string, ImportValue>>({ required: true })

const blocks = computed(() => fieldBlocks(props.fields))
const FLAGS = [
  { key: 'adult', label: '18+', color: 'var(--adult)' },
  { key: 'ai', label: 'AI', color: 'var(--ai)' },
  { key: 'censored', label: 'Censored', color: 'var(--cens)' },
] as const

const asList = (v: ImportValue | undefined): string[] =>
  Array.isArray(v) ? v : (typeof v === 'string' && v ? [v] : [])
const own = (id: string) => asList(values.value[id])
const theirs = (id: string) => asList(props.inherited[id])
const one = (id: string) => (typeof values.value[id] === 'string' ? values.value[id] as string : '')
const isToken = (v: string) => /\{\w+\}/.test(v)

function setOne(id: string, v: string) {
  if (v.trim()) values.value[id] = v
  else delete values.value[id]
}
function setList(id: string, list: string[]) {
  if (list.length) values.value[id] = list
  else delete values.value[id]
}
function add(id: string, v: string) {
  if (v && !own(id).includes(v) && !theirs(id).includes(v)) setList(id, [...own(id), v])
}
function toggleFlag(key: string) {
  setList('flags', own('flags').includes(key) ? own('flags').filter((f) => f !== key) : [...own('flags'), key])
}

// Open vocabularies offer what the library already holds — and STATUS its small
// closed set, as the title editor does.
function suggestions(f: FieldDef): string[] {
  const all = [...new Set([...(f.id === 'status' ? DEFAULT_STATUSES : []), ...(store.vocab[f.vocab] ?? [])])]
  return f.control === 'chips' ? all.filter((v) => !own(f.id).includes(v) && !theirs(f.id).includes(v)) : all
}
const placeholder = (f: FieldDef) => (theirs(f.id)[0] ? `every work: ${theirs(f.id)[0]}` : 'leave as it is')
</script>

<template>
  <div class="im">
    <template v-for="b in blocks" :key="b.key">
      <div class="blocklbl"><span>{{ b.label }}</span><i class="rule"></i></div>
      <div class="blockrows">
        <div v-for="f in b.rows" :key="f.id" class="fieldrow">
          <div class="lblrow">
            <span class="lbl">{{ f.label.toUpperCase() }}</span>
            <span v-if="inherited[f.id] !== undefined && values[f.id] !== undefined" class="pbadge own">this work</span>
            <span v-if="f.id === 'type'" class="pbadge">decides the shelf</span>
          </div>

          <div v-if="f.control === 'flags'" class="flagrow">
            <button v-for="fl in FLAGS" :key="fl.key" class="flag" :disabled="theirs('flags').includes(fl.key)"
                    :style="own('flags').includes(fl.key) || theirs('flags').includes(fl.key)
                      ? { color: fl.color, borderColor: fl.color } : {}"
                    @click="toggleFlag(fl.key)">
              <span class="box" :style="own('flags').includes(fl.key) || theirs('flags').includes(fl.key)
                ? { borderColor: fl.color, background: fl.color } : {}"></span>{{ fl.label }}
            </button>
          </div>

          <div v-else-if="f.control === 'chips'" class="chips">
            <span v-if="locked[f.id]" class="chip tok lock" title="Read from the folder names — change it in Structure">
              {creator}<span class="src">{{ locked[f.id] }}</span></span>
            <span v-for="v in theirs(f.id)" :key="`i-${v}`" class="chip inh" :class="{ tok: isToken(v) }">{{ v }}</span>
            <span v-for="(v, i) in own(f.id)" :key="v" class="chip" :class="{ tok: isToken(v) }">{{ v }}<button class="rm"
                  title="Remove" @click="setList(f.id, own(f.id).filter((_, j) => j !== i))">×</button></span>
            <Combo :suggestions="suggestions(f)" add-mode placeholder="add…" @add="add(f.id, $event)" />
          </div>

          <Combo v-else-if="f.control === 'vocab'" :model-value="one(f.id)" :suggestions="suggestions(f)" wide lazy
                 :placeholder="placeholder(f)" @update:model-value="setOne(f.id, $event)" />
          <textarea v-else-if="f.control === 'multiline'" :value="one(f.id)" class="in ta" :placeholder="placeholder(f)"
                    @input="setOne(f.id, ($event.target as HTMLTextAreaElement).value)"></textarea>
          <input v-else :value="one(f.id)" class="in" :placeholder="placeholder(f)" spellcheck="false"
                 @input="setOne(f.id, ($event.target as HTMLInputElement).value)" />
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.im { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.blocklbl { display: flex; align-items: center; gap: 10px; font: 700 10px/1 ui-monospace, monospace; letter-spacing: .16em; color: var(--tx2); padding: 18px 0 10px; }
.blocklbl:first-child { padding-top: 2px; }
.blocklbl .rule { flex: 1; height: 1px; background: var(--line); }
.blockrows { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.fieldrow { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.lblrow { display: flex; align-items: center; gap: 7px; min-height: 16px; }
.lbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); }
.pbadge { font: 700 8px/1 ui-monospace, monospace; letter-spacing: .1em; text-transform: uppercase; padding: 3px 6px; border-radius: 4px; color: var(--tx3); background: var(--panel2); }
.pbadge.own { color: var(--accent); background: var(--accentSoft); }
.in { font: 500 12.5px/1.3 system-ui; color: var(--tx); background: var(--panel); border: 1px solid var(--line); border-radius: 7px; padding: 8px 10px; outline: none; width: 100%; }
.in:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accentSoft); }
.ta { min-height: 64px; resize: vertical; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-width: 0; }
.chip { display: inline-flex; align-items: center; gap: 6px; font: 500 11.5px/1 system-ui; color: var(--tx); background: var(--panel2); border: 1px solid var(--line); padding: 5px 9px; border-radius: 6px; max-width: 100%; }
.chip.tok { font-family: ui-monospace, 'Cascadia Code', Consolas, monospace; font-size: 11px; border-style: dashed; border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); }
.chip.inh { background: transparent; color: var(--tx3); }
.chip .src { font: 700 8px/1 ui-monospace, monospace; letter-spacing: .06em; color: var(--accent); }
.rm { border: none; background: transparent; padding: 0; color: var(--tx3); cursor: pointer; font-size: 13px; line-height: 1; }
.rm:hover { color: var(--adult); }
.flagrow { display: flex; gap: 7px; flex-wrap: wrap; }
.flag { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font: 500 10.5px/1 system-ui; border: 1px solid var(--line); color: var(--tx2); background: transparent; padding: 6px 9px; border-radius: 6px; }
.flag:disabled { cursor: default; opacity: .6; }
.flag .box { width: 12px; height: 12px; border-radius: 3px; border: 1.5px solid var(--line); }
</style>
