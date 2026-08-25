<script setup lang="ts">
// Everything the app is fetching right now, over everything else.
//
// A download is bound to a CHAPTER of a TITLE, so that is what a row says —
// not a file name off a CDN. Rows survive a quit: a transfer stopped by closing
// the window is remembered with its byte offset, and can be picked up from
// there or started over, depending on what the server still honours.
import { computed, onBeforeUnmount, onMounted } from 'vue'
import Icon from './Icon.vue'
import { api } from '../api'
import { downloads, pollDownloads, store, titleById, watchDownloads } from '../store'

const props = defineProps<{ warning: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'quit'): void }>()

// the poll is only worth running while something is looking at it
onMounted(() => { watchDownloads(true); void pollDownloads(true) })
onBeforeUnmount(() => watchDownloads(false))

const rows = computed(() => downloads.items.filter((i) => i.state !== 'done'))
const running = computed(() => rows.value.filter((i) => i.state === 'downloading'))

function titleOf(id: string): string { return titleById(id)?.title || 'a title that is gone' }
function pct(i: { received: number; total: number }): number {
  return i.total ? Math.min(100, Math.round((i.received / i.total) * 100)) : 0
}
function mb(n: number): string { return `${(n / 1048576).toFixed(1)} MB` }
function amount(i: { received: number; total: number }): string {
  return i.total ? `${mb(i.received)} / ${mb(i.total)}` : mb(i.received)
}

async function stop(id: string) {
  await window.longbox?.cancelDownload(id)
  try { await api.forgetDownload(id) } catch { /* already gone */ }
  await pollDownloads(true)
}

// Picking one up: the chapter is armed again FIRST (so the transfer claims the
// same entry), then the shell re-opens the file at the offset it reached. A
// server that will not honour the range answers from zero — Electron reports
// that as a failure, and the row offers to start over.
async function resume(id: string) {
  const rec = downloads.items.find((i) => i.id === id)
  if (!rec) return
  try { await api.rearmDownload(id) } catch (e) { store.error = String(e); return }
  const ok = await window.longbox?.resumeDownload(rec)
  if (!ok) store.error = 'this transfer cannot be picked up — start it over from the source page'
  await pollDownloads(true)
}
</script>

<template>
  <div class="dlmask" @click.self="emit('close')">
    <div class="dlpanel">
      <div class="dlhead">
        <Icon name="download" :size="15" :sw="2" />
        <span class="dlt">Downloads</span>
        <span v-if="running.length" class="dlnum mono">{{ running.length }} running</span>
        <div style="flex:1"></div>
        <button class="iconbtn" title="Close" @click="emit('close')">
          <Icon name="x" :size="14" :sw="2.2" />
        </button>
      </div>

      <!-- the window is trying to leave while these are still going -->
      <div v-if="props.warning" class="dlwarn">
        <div class="wt">{{ props.warning }}</div>
        <div class="wh">
          Closing now keeps each transfer's place — the part already fetched stays on disk and
          the entry it was claiming is remembered. Whether it can be picked up from there or has
          to start over is the source's call when you come back.
        </div>
        <div class="wacts">
          <button class="btn ghost chsmall" @click="emit('close')">Keep downloading</button>
          <button class="btn accent chsmall" @click="emit('quit')">Close anyway</button>
        </div>
      </div>

      <div v-if="!rows.length" class="dlnone">Nothing is downloading.</div>
      <div v-else class="dlrows scroll">
        <div v-for="i in rows" :key="i.id" class="dlrow" :class="i.state">
          <div class="dlmain">
            <span class="dlname">{{ titleOf(i.titleId) }}</span>
            <span class="dlch mono">
              ch. {{ i.num }}<template v-if="i.lang"> · {{ i.lang }}</template>
            </span>
          </div>
          <div class="dlbar"><span :style="{ width: `${pct(i)}%` }"></span></div>
          <div class="dlfoot">
            <span class="dlamt mono">{{ amount(i) }}</span>
            <span v-if="i.state === 'downloading'" class="dlpct mono">{{ pct(i) }}%</span>
            <span v-else class="dlerr">{{ i.error || i.state }}</span>
            <div style="flex:1"></div>
            <button v-if="i.state === 'interrupted'" class="btn ghost chsmall" @click="resume(i.id)">
              Pick up
            </button>
            <button class="btn ghost chsmall" @click="stop(i.id)">
              {{ i.state === 'downloading' ? 'Stop' : 'Forget' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dlmask { position: fixed; inset: 0; z-index: 300; display: flex; align-items: center; justify-content: center; background: color-mix(in srgb, #000 55%, transparent); }
.dlpanel { width: 520px; max-width: calc(100vw - 40px); max-height: calc(100vh - 80px); display: flex; flex-direction: column; background: var(--panel); border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 30px 70px rgba(0,0,0,.6); }
.dlhead { display: flex; align-items: center; gap: 9px; padding: 12px 14px; border-bottom: 1px solid var(--line); color: var(--tx2); }
.dlt { font: 600 14px/1 system-ui; color: var(--tx); }
.dlnum { font-size: 11px; color: var(--accent); }
.dlnone { padding: 26px; text-align: center; font: 400 12px/1 system-ui; color: var(--tx3); }
.dlrows { padding: 10px 12px; display: flex; flex-direction: column; gap: 9px; overflow-y: auto; min-height: 0; }
.dlrow { padding: 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--panel2); }
.dlrow.interrupted { border-color: color-mix(in srgb, var(--warn) 45%, var(--line)); }
.dlrow.failed { border-color: color-mix(in srgb, var(--adult) 45%, var(--line)); }
.dlmain { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.dlname { font: 600 12.5px/1.3 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dlch { font-size: 11px; color: var(--tx3); flex: none; }
.dlbar { margin: 8px 0 6px; height: 4px; border-radius: 999px; background: var(--line); overflow: hidden; }
.dlbar span { display: block; height: 100%; background: var(--accent); transition: width .3s ease; }
.dlfoot { display: flex; align-items: center; gap: 8px; }
.dlamt, .dlpct { font-size: 10.5px; color: var(--tx3); }
.dlerr { font: 500 11px/1.3 system-ui; color: var(--warn); }
.dlwarn { margin: 12px 12px 0; padding: 11px; border-radius: 9px; background: color-mix(in srgb, var(--warn) 10%, transparent); border: 1px solid color-mix(in srgb, var(--warn) 40%, var(--line)); }
.wt { font: 600 12.5px/1.3 system-ui; color: var(--tx); }
.wh { margin-top: 6px; font: 400 11px/1.5 system-ui; color: var(--tx2); }
.wacts { margin-top: 10px; display: flex; gap: 7px; justify-content: flex-end; }
.chsmall { padding: 6px 10px; font-size: 11.5px; }
</style>
