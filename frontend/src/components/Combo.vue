<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'

const props = withDefaults(defineProps<{
  modelValue?: string
  suggestions: string[]
  placeholder?: string
  addMode?: boolean // true: emit 'add' on pick/Enter then clear; false: two-way bind the value
  wide?: boolean
}>(), { modelValue: '', placeholder: '', addMode: false, wide: false })
const emit = defineEmits<{ (e: 'update:modelValue', v: string): void; (e: 'add', v: string): void }>()

const root = ref<HTMLElement | null>(null)
const open = ref(false)
const local = ref('')

const text = computed({
  get: () => (props.addMode ? local.value : props.modelValue),
  set: (v: string) => {
    if (props.addMode) local.value = v
    else emit('update:modelValue', v)
    open.value = true
  },
})
const matches = computed(() => {
  const q = text.value.trim().toLowerCase()
  return props.suggestions.filter((s) => !q || s.toLowerCase().includes(q)).slice(0, 30)
})
function commit(v: string) {
  const val = v.trim()
  if (!val) return
  if (props.addMode) { emit('add', val); local.value = '' }
  else emit('update:modelValue', val)
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
           @focus="open = true" @keydown.enter.prevent="commit(text)" />
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
