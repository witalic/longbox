<script setup lang="ts">
// A release is out: what it is, and the two ways to it — both in the system's
// browser (design/update-check.md). Opened from the sidebar's footer, above it.
import { onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from './Icon.vue'
import { ago } from '../data'
import { store } from '../store'
import { available, dismiss, DOWNLOAD, openRelease } from '../updates'

const emit = defineEmits<{ close: [] }>()
const root = ref<HTMLElement | null>(null)

// a click anywhere else closes it — except on the button that toggles it
function outside(e: PointerEvent) {
  const at = e.target as HTMLElement | null
  if (root.value?.contains(at) || at?.closest('.updbtn')) return
  emit('close')
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => {
  document.addEventListener('pointerdown', outside, true)
  document.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', outside, true)
  document.removeEventListener('keydown', onKey)
})

function later() {
  dismiss()
  emit('close')
}
</script>

<template>
  <section v-if="available" ref="root" class="pop" aria-labelledby="updpop-title">
    <div id="updpop-title" class="v">longbox {{ available.latest }} is out</div>
    <div class="s">released {{ ago(available.published) }} · you have {{ store.appMeta.version }}</div>
    <div v-if="available.security" class="warn">
      <Icon name="shield" :size="14" :sw="2" />
      <span>It fixes security issues — in the built-in browser, among others.</span>
    </div>
    <div class="acts">
      <button class="btn" @click="openRelease(available.notes)">What's new</button>
      <button class="btn" @click="openRelease(DOWNLOAD)">Download</button>
      <button class="btn ghost later" @click="later">Not now</button>
    </div>
    <div class="ext">Both open in your browser. Not now hides this until a newer version.</div>
  </section>
</template>

<style scoped>
.pop {
  position: fixed; left: 10px; bottom: 52px; z-index: 250; width: 330px;
  background: var(--panel2); border: 1px solid var(--line); border-radius: 10px;
  box-shadow: 0 14px 34px rgba(0, 0, 0, .45); padding: 15px 16px 12px;
}
.v { font: 600 15px/1.2 system-ui; color: var(--tx); }
.s { margin-top: 5px; font: 400 11.5px/1.4 system-ui; color: var(--tx3); }
.warn {
  margin-top: 11px; display: flex; gap: 8px; align-items: flex-start; padding: 8px 10px; border-radius: 6px;
  font: 500 12px/1.45 system-ui; color: var(--warn); background: color-mix(in srgb, var(--warn) 10%, transparent);
}
.warn :deep(svg) { margin-top: 1px; }
.acts { display: flex; gap: 8px; margin-top: 13px; align-items: center; }
.later { margin-left: auto; border-color: transparent; }
.ext { margin-top: 9px; font: 400 10.5px/1.4 system-ui; color: var(--tx3); }
</style>
