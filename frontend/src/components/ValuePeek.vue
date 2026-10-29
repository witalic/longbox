<script setup lang="ts">
// The card a value's chip shows when hovered for a beat (peek.ts). One instance,
// mounted by App, placed next to whichever chip asked.
import { coverAt } from '../data'
import { peek, peekClose, peekHold, peekOff } from '../peek'
import { filterByField, openValuePage } from '../store'

function open() {
  const d = peek.data
  peekClose()
  if (d) openValuePage(d.field, d.value)
}
function showInLibrary() {
  const d = peek.data
  peekClose()
  if (d) filterByField(d.field === 'artists' ? 'authors' : d.field, d.value)
}
</script>

<template>
  <div v-if="peek.open && peek.data" class="peek" :class="{ above: peek.above }"
       :style="{ left: `${peek.x}px`, top: `${peek.y}px` }" @mouseenter="peekHold" @mouseleave="peekOff">
    <div class="top">
      <span class="kind mono">{{ peek.data.kind }}</span>
      <span class="nm">{{ peek.data.value }}</span>
      <span class="ct mono">{{ peek.data.titles }} title{{ peek.data.titles === 1 ? '' : 's' }}</span>
    </div>
    <div class="tx" :class="{ none: !peek.data.excerpt }">{{ peek.data.excerpt || 'Nothing written yet.' }}</div>
    <div v-if="peek.data.covers.length" class="mini">
      <span v-for="c in peek.data.covers" :key="c" class="cov"
            :style="{ background: `#181a1f url(${coverAt(c, 104)}) center/cover no-repeat` }"></span>
    </div>
    <div class="acts">
      <button class="btn small accent" @click="open">Open page</button>
      <button class="btn small" @click="showInLibrary">Show in library</button>
    </div>
  </div>
</template>

<style scoped>
.peek { position: fixed; z-index: 60; width: 320px; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); box-shadow: 0 16px 40px rgba(0, 0, 0, .45); padding: 14px; }
.peek.above { transform: translateY(-100%); }
.top { display: flex; align-items: center; gap: 8px; min-width: 0; }
.kind { flex: none; height: 20px; display: inline-flex; align-items: center; padding: 0 7px; font-size: 9.5px; font-weight: 600; letter-spacing: .12em; color: var(--accent); border: 1px solid var(--accent); border-radius: 5px; text-transform: uppercase; }
.nm { min-width: 0; font: 600 15px/1.2 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ct { margin-left: auto; flex: none; font-size: 10.5px; color: var(--tx3); }
.tx { margin-top: 8px; font: 400 12px/1.5 system-ui; color: var(--tx2); }
.tx.none { color: var(--tx3); font-style: italic; }
.mini { display: flex; gap: 6px; margin-top: 10px; }
.mini .cov { width: 52px; aspect-ratio: 2 / 3; border-radius: 5px; border: 1px solid var(--line); }
.acts { display: flex; gap: 6px; margin-top: 12px; }
</style>
