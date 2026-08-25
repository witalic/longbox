<script setup lang="ts">
// The search box every view band ends up wanting. It was written out three
// times with three slightly different widths and paddings; one widget keeps
// them the same shape, and `wide` is the only dial anyone needed.
import Icon from './Icon.vue'

withDefaults(defineProps<{ placeholder?: string; width?: number }>(),
  { placeholder: 'Search…', width: 300 })
const text = defineModel<string>({ required: true })
</script>

<template>
  <div class="sbox" :style="{ maxWidth: `${width}px` }">
    <Icon name="search" :size="13" />
    <input v-model="text" class="sin" :placeholder="placeholder" />
  </div>
</template>

<style scoped>
.sbox { flex: 1 1 140px; min-width: 96px; display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 11px; background: var(--panel); border: 1px solid var(--line); border-radius: 6px; color: var(--tx3); }
.sbox:focus-within { border-color: var(--accent); }
.sin { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: var(--tx); font: 400 12px/1 system-ui; }
</style>
