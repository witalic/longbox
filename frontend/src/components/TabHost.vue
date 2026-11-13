<script setup lang="ts">
// One app tab's screen (design/navigation.md): kept alive while the tab is
// open, so what was done in it — a search, the values picked, an operation half
// set up — is there when the tab comes back. The view inside reads its place
// from here, and every box it scrolled is scrolled back.
import { onActivated, provide } from 'vue'
import { TAB, type AppTab } from '../nav'
import LibraryView from '../views/LibraryView.vue'
import AuthorsView from '../views/AuthorsView.vue'
import MetadataView from '../views/MetadataView.vue'
import ImportView from '../views/ImportView.vue'
import SettingsView from '../views/SettingsView.vue'
import ValuePage from './ValuePage.vue'

const props = defineProps<{ tab: AppTab }>()
provide(TAB, props.tab)

// A box loses its scroll while its tab is put away, so the last one is noted
// as it happens and put back on the way in.
const scrolled = new Map<Element, [number, number]>()
function onScroll(e: Event) {
  const el = e.target
  if (el instanceof Element) scrolled.set(el, [el.scrollTop, el.scrollLeft])
}
onActivated(() => {
  for (const [el, [top, left]] of scrolled) {
    if (!el.isConnected) {
      scrolled.delete(el)
      continue
    }
    el.scrollTop = top
    el.scrollLeft = left
  }
})
</script>

<template>
  <div class="tabhost scroll" @scroll.capture="onScroll">
    <LibraryView v-if="tab.place.kind === 'library' && !tab.place.axis" />
    <AuthorsView v-else-if="tab.place.kind === 'library'" />
    <ValuePage v-else-if="tab.place.kind === 'page'" />
    <MetadataView v-else-if="tab.place.kind === 'metadata'" />
    <ImportView v-else-if="tab.place.kind === 'import'" />
    <SettingsView v-else-if="tab.place.kind === 'settings'" />
  </div>
</template>

<style scoped>
.tabhost { height: 100%; }
</style>
