<script lang="ts">
// whether a version's first page is wide — learned once, for every card that shows it
const wideOf = new Map<string, boolean>()
</script>

<script setup lang="ts">
// The picture an entry card wears (design/contents-views-mockup.html), always
// 16:9. An episode: its poster. Pages: the first one whole when it is wider than
// tall (a spread, an artbook), else three pages side by side — picked at random,
// but seeded by the entry and its version, so the same three come back every
// time until the pages themselves change.
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { isReadable, type Chapter } from '../data'
import Icon from './Icon.vue'

const props = defineProps<{ titleId: string; chapter: Chapter }>()

const SLICES = 3

const c = computed(() => props.chapter)
const key = computed(() => `${props.titleId}/${c.value.id}/${c.value.v}`)
const wide = ref<boolean | undefined>(wideOf.get(key.value))
watch(key, (k) => { wide.value = wideOf.get(k) })
function measure(e: Event) {
  const img = e.target as HTMLImageElement
  if (wide.value !== undefined || !img.naturalWidth) return
  wide.value = img.naturalWidth > img.naturalHeight
  wideOf.set(key.value, wide.value)
}

function seeded(seed: string): () => number {
  let h = 2166136261
  for (const ch of seed) h = Math.imul(h ^ ch.charCodeAt(0), 16777619)
  return () => {
    h = Math.imul(h ^ (h >>> 15), 2246822507)
    h = Math.imul(h ^ (h >>> 13), 3266489909)
    return ((h ^= h >>> 16) >>> 0) / 4294967296
  }
}
const picks = computed(() => {
  const n = c.value.pages
  const next = seeded(key.value)
  const out = new Set<number>()
  while (out.size < Math.min(SLICES, n)) out.add(Math.floor(next() * n))
  return [...out].sort((a, b) => a - b)
})
// cap 1.5 crops webtoon-length pages to a shape a slice can hold
const pageAt = (i: number, w: number) => api.chapterPageSrc(props.titleId, c.value.id, i, c.value.v, w, 1.5)

const progress = computed(() => {
  const ch = c.value
  if (ch.kind !== 'video' || ch.read === 'read' || !ch.position || !ch.duration) return ''
  return `${Math.min(100, (100 * ch.position) / ch.duration)}%`
})
</script>

<template>
  <div class="pv" :class="{ none: !isReadable(c) }">
    <template v-if="!isReadable(c)"><span class="mono">{{ c.dl ? 'nothing to show' : 'not downloaded' }}</span></template>
    <template v-else-if="c.kind === 'video'">
      <img v-if="c.poster" :src="api.chapterFramesSrc(titleId, c.id, 'poster', c.stills, 960)" loading="lazy" alt="" />
      <Icon v-else name="film" :size="22" :sw="1.6" class="mark" />
    </template>
    <!-- the first page answers which picture this is; until it has, it is not shown -->
    <img v-else-if="wide !== false" :src="pageAt(0, 960)" :class="{ probe: wide === undefined }"
         loading="lazy" alt="" @load="measure" />
    <template v-else><img v-for="i in picks" :key="i" :src="pageAt(i, 400)" loading="lazy" alt="" /></template>
    <span v-if="progress" class="prog" :style="{ width: progress }"></span>
  </div>
</template>

<style scoped>
.pv { position: relative; aspect-ratio: 16 / 9; display: flex; gap: 2px; overflow: hidden; background: var(--panel2); }
.pv img { display: block; flex: 1 1 0; min-width: 0; height: 100%; object-fit: cover; }
.pv img.probe { opacity: 0; }
.pv .mark { margin: auto; color: var(--tx3); }
.pv.none { align-items: center; justify-content: center; background: transparent; border-bottom: 1px dashed var(--line); }
.pv.none span { font-size: 11px; color: var(--tx3); }
.prog { position: absolute; left: 0; bottom: 0; height: 3px; background: var(--warn); }
</style>
