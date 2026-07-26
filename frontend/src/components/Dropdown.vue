<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import Icon from './Icon.vue'

interface Opt { v: string | number; l: string }
const props = withDefaults(defineProps<{
  label?: string
  modelValue: string | number
  options: Opt[]
  searchable?: boolean // auto-enabled when > 6 options
}>(), { label: '', searchable: undefined })
const emit = defineEmits<{ (e: 'update:modelValue', v: string | number): void }>()

const open = ref(false)
const q = ref('')
const root = ref<HTMLElement | null>(null)

const useSearch = computed(() => props.searchable ?? props.options.length > 6)
const filtered = computed(() => {
  if (!useSearch.value || !q.value.trim()) return props.options
  const s = q.value.toLowerCase()
  return props.options.filter((o) => o.l.toLowerCase().includes(s))
})
const current = computed(() => props.options.find((o) => o.v === props.modelValue) ?? props.options[0])
const active = computed(() => props.modelValue !== 'all' && props.modelValue !== 0)

function toggle() { open.value = !open.value; if (open.value) q.value = '' }
function pick(o: Opt) { emit('update:modelValue', o.v); open.value = false }
function onDocDown(e: MouseEvent) {
  if (root.value && !root.value.contains(e.target as Node)) open.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocDown))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocDown))
</script>

<template>
  <div ref="root" class="pill" :class="{ on: active }" style="position:relative" @click="toggle">
    <span v-if="label" class="k">{{ label }}</span>{{ current?.l }}<Icon name="chevron" :size="11" :sw="2" />
    <div v-if="open" class="menu" style="min-width:180px" @click.stop>
      <div v-if="useSearch" class="ddsearch">
        <Icon name="search" :size="13" />
        <input v-model="q" class="ddsearchin" placeholder="Search…" @click.stop />
      </div>
      <div class="ddlist scroll">
        <div v-for="o in filtered" :key="String(o.v)" class="item" :class="{ on: o.v === modelValue }" @click="pick(o)">
          {{ o.l }}<Icon v-if="o.v === modelValue" name="check" :size="13" :sw="2.4" style="margin-left:auto;color:var(--accent)" />
        </div>
        <div v-if="!filtered.length" class="ddempty">No matches</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ddsearch { display: flex; align-items: center; gap: 7px; padding: 6px 9px; border-bottom: 1px solid var(--line); color: var(--tx3); margin: -2px -2px 4px; }
.ddsearchin { border: none; background: transparent; outline: none; color: var(--tx); font: 400 12px/1 system-ui; width: 100%; }
.ddlist { max-height: 240px; display: flex; flex-direction: column; gap: 2px; }
.ddempty { padding: 8px 10px; font: 400 12px/1 system-ui; color: var(--tx3); }
</style>
