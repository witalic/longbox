<script setup lang="ts">
// ‹ › for the tab in front (design/navigation.md §5) — the start of every head
// band, where the web view keeps its own. A right-click on ‹ lists the steps;
// Alt+← / Alt+→ and the mouse's side buttons do the same (App.vue).
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from './Icon.vue'
import { activeAppTab, goBack, goForward, placeLabel } from '../nav'

const tab = computed(() => activeAppTab())
const canBack = computed(() => !!tab.value?.back.length && !tab.value.hold)
const canForward = computed(() => !!tab.value?.forward.length && !tab.value.hold)
const steps = computed(() => [...(tab.value?.back ?? [])].reverse().map(placeLabel))

const open = ref(false)
const root = ref<HTMLElement | null>(null)
function onDocDown(e: MouseEvent) {
  if (root.value && !root.value.contains(e.target as Node)) open.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocDown))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocDown))
function jump(n: number) {
  open.value = false
  goBack(n)
}
</script>

<template>
  <span ref="root" class="hist">
    <button class="iconbtn plain" :disabled="!canBack" title="Back (Alt+←) — right-click for the steps"
            @click="goBack()" @contextmenu.prevent="open = canBack"><Icon name="back" :size="15" :sw="2" /></button>
    <button class="iconbtn plain" :disabled="!canForward" title="Forward (Alt+→)"
            @click="goForward()"><Icon name="forward" :size="15" :sw="2" /></button>
    <div v-if="open" class="menu steps scroll">
      <div class="mlbl mono">BACK</div>
      <div v-for="(s, i) in steps" :key="i" class="item" @click="jump(i + 1)"><span class="sl">{{ s }}</span></div>
    </div>
  </span>
</template>

<style scoped>
/* the band's own padding is the gutter; the arrows sit just inside it */
.hist { position: relative; display: inline-flex; gap: 2px; flex: none; margin-left: -10px; }
.steps { width: 290px; max-height: 340px; overflow: auto; z-index: 40; }
.mlbl { font-size: 8.5px; font-weight: 700; letter-spacing: .12em; color: var(--tx3); padding: 7px 10px 5px; }
.sl { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
