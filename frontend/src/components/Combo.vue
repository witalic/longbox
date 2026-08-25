<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'

const props = withDefaults(defineProps<{
  modelValue?: string
  suggestions: string[]
  placeholder?: string
  addMode?: boolean // true: emit 'add' on pick/Enter then clear; false: two-way bind the value
  // Everything else that may be picked. The near list is what this SOURCE (or
  // this title) already uses; the rest joins in the moment you start typing,
  // because then you are looking for something, not being offered something.
  moreSuggestions?: string[]
  // Commit on Enter / pick / blur instead of on every keystroke — for a value
  // whose write costs something (a request, a new group) rather than a filter.
  lazy?: boolean
  wide?: boolean
}>(), { modelValue: '', placeholder: '', addMode: false, lazy: false, wide: false,
        moreSuggestions: () => [] })
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void; (e: 'add', v: string): void }>()

const root = ref<HTMLElement | null>(null)
const open = ref(false)
const local = ref('')
const draft = ref<string | null>(null) // lazy mode: what is typed but not committed
// A prefilled value is not a query. Until a key is pressed the list shows what
// there IS; filtering by the value already in the box hid every other option.
const typed = ref(false)

const text = computed({
  get: () => (props.addMode ? local.value : (draft.value ?? props.modelValue)),
  set: (v: string) => {
    if (props.addMode) local.value = v
    else if (props.lazy) draft.value = v
    else emit('update:modelValue', v)
    typed.value = true
    open.value = true
  },
})
const matches = computed(() => {
  const q = typed.value ? text.value.trim().toLowerCase() : ''
  const pool = q ? [...props.suggestions, ...props.moreSuggestions] : props.suggestions
  const seen = new Set<string>()
  return pool
    .filter((s) => (!q || s.toLowerCase().includes(q)) && !seen.has(s) && seen.add(s))
    .slice(0, 40)
})
function commit(v: string) {
  const val = v.trim()
  // an empty commit CLEARS a lazy value on purpose ("no group"); the eager
  // callers keep their old guard, where an empty keystroke means nothing yet
  if (!val && !props.lazy) return
  if (props.addMode) { emit('add', val); local.value = '' }
  else { emit('update:modelValue', val); draft.value = null }
  typed.value = false
  open.value = false
}
// Leaving the field closes its list. A PICK fired its mousedown first and has
// already committed, so this only ever ends an abandoned edit — but tabbing away
// used to leave the menu hanging over the next field.
function onBlur() {
  if (props.lazy && draft.value !== null) commit(text.value)
  open.value = false
}
function onDocDown(e: MouseEvent) { if (root.value && !root.value.contains(e.target as Node)) open.value = false }
onMounted(() => document.addEventListener('mousedown', onDocDown))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocDown))
</script>

<template>
  <div ref="root" class="combo" :style="wide ? 'display:block;width:100%' : ''">
    <input :value="text" class="comboin" :class="{ wide }" :placeholder="placeholder"
           @input="text = ($event.target as HTMLInputElement).value"
           @focus="typed = false; open = true" @keydown.enter.prevent="commit(text)"
           @keydown.esc="draft = null; open = false"
           @blur="onBlur" />
    <div v-if="open && matches.length" class="menu combomenu scroll">
      <div v-for="s in matches" :key="s" class="item" @mousedown.prevent="commit(s)">{{ s }}</div>
    </div>
  </div>
</template>

<style scoped>
.combo { position: relative; display: inline-flex; }
.comboin { border: none; background: transparent; outline: none; color: var(--tx); font: 400 12px/1 system-ui; min-width: 100px; padding: 4px 2px; }
.comboin.wide { width: 100%; font: 500 13px/1.3 system-ui; background: var(--panel); border: 1px solid var(--line); border-radius: 7px; padding: 8px 10px; }
.comboin.wide:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accentSoft); }
.combomenu { position: absolute; top: calc(100% + 6px); left: 0; z-index: 30; min-width: 200px; max-height: 220px; overflow: auto; }
</style>
