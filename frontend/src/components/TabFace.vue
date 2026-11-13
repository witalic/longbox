<script setup lang="ts">
// What an app tab wears in the strip and the all-tabs menu: a title its cover,
// a person their initials, any other value its colour, the library and the
// sections their icon.
import { computed } from 'vue'
import Icon from './Icon.vue'
import { coverAt, hueFor, initials } from '../data'
import { cardById } from '../store'
import { type AppTab } from '../nav'

const props = defineProps<{ tab: AppTab }>()
const SECTION_ICON: Record<string, string> = { library: 'library', metadata: 'meta', import: 'import', settings: 'settings' }

const cover = computed(() => {
  const p = props.tab.place
  if (p.kind !== 'title' && p.kind !== 'reader') return null
  const t = cardById(p.id)
  return t?.cover ? { background: `#181a1f url('${coverAt(t.cover, 64)}') center/cover` } : { background: hueFor(p.id) }
})
const person = computed(() => {
  const p = props.tab.place
  return p.kind === 'page' && p.field === 'authors'
})
</script>

<template>
  <span v-if="cover" class="swatch" :style="cover"></span>
  <span v-else-if="tab.place.kind === 'page' && person" class="face"
        :style="{ background: hueFor(tab.place.value) }">{{ initials(tab.place.value) }}</span>
  <span v-else-if="tab.place.kind === 'page'" class="dot" :style="{ background: hueFor(tab.place.value) }"></span>
  <Icon v-else class="kind" :name="SECTION_ICON[tab.place.kind]" :size="13" :sw="1.9" />
</template>

<style scoped>
.swatch { width: 14px; height: 19px; border-radius: 3px; flex: none; border: 1px solid var(--line); }
.face { width: 16px; height: 16px; border-radius: 50%; flex: none; display: inline-flex; align-items: center; justify-content: center; font: 700 6.5px/1 system-ui; color: #fff; }
.dot { width: 9px; height: 9px; border-radius: 50%; flex: none; margin: 0 3px; }
.kind { flex: none; color: var(--tx2); }
</style>
