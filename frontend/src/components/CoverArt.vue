<script setup lang="ts">
// A title's cover art: one image, or — where the title has several — a rotation
// through them WHILE THE POINTER IS ON IT.
//
// Never on its own. A library grid is read by scanning it, and several hundred
// tiles cross-fading independently is not something anyone can scan. Hovering is
// the ask, so hovering is the only thing that starts it.
//
// The extra covers are not even fetched until the first hover: mounting every
// layer up front would turn one image per tile into eight.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { coverAt, hueFor } from '../data'

const props = withDefaults(defineProps<{
  covers: string[]
  id: string          // the fallback colour is keyed by this, so it never moves
  title?: string
  w?: number          // the downscale width to ask the endpoint for
}>(), { title: '', w: 420 })

// Long enough to look at, short enough that a hover shows more than two.
const HOLD_MS = 1600

const at = ref(0)
const armed = ref(false)
let timer: number | undefined

const rotates = computed(() => props.covers.length > 1)

function start() {
  if (!rotates.value || timer !== undefined) return
  armed.value = true
  timer = window.setInterval(() => {
    at.value = (at.value + 1) % props.covers.length
  }, HOLD_MS)
}
function stop() {
  if (timer !== undefined) window.clearInterval(timer)
  timer = undefined
  at.value = 0
}
// A cover removed elsewhere shortens the list under a running rotation: an
// index past its end matches no layer and leaves the tile blank, and an emptied
// list turns the step into a modulo by zero.
watch(() => props.covers, stop)
onBeforeUnmount(stop)
</script>

<template>
  <div class="art" :style="covers.length ? undefined : { background: hueFor(id || title) }"
       @mouseenter="start" @mouseleave="stop">
    <template v-for="(c, i) in covers" :key="c">
      <span v-if="armed || i === 0" class="layer" :class="{ on: i === at }"
            :style="{ backgroundImage: `url(&quot;${coverAt(c, w)}&quot;)` }" />
    </template>
    <slot />
  </div>
</template>

<style scoped>
.art { position: relative; overflow: hidden; }
.layer {
  position: absolute; inset: 0; background: #181a1f center/cover no-repeat;
  opacity: 0; transition: opacity .45s ease;
}
.layer.on { opacity: 1; }
@media (prefers-reduced-motion: reduce) { .layer { transition: none; } }
</style>
