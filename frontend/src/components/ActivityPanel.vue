<script setup lang="ts">
// Everything the app is doing right now, over everything else: the one job over
// the library that can run at a time — an import, a conversion, a metadata run,
// a check — and the transfers (design/activity-mockup.html).
//
// A download is bound to a CHAPTER of a TITLE, so that is what a row says —
// not a file name off a CDN; one nobody armed goes to the library's downloads
// folder and says its file's name (design/subtitles.md), and that folder is
// listed here too. Rows survive a quit: a transfer stopped by closing the window
// is remembered with its byte offset, and can be picked up from there or started
// over, depending on what the server still honours.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from './Icon.vue'
import ProgressBar from './ProgressBar.vue'
import { api } from '../api'
import {
  askConfirm, downloads, openSection, passHome, passIcon, passPercent, pollDownloads, stopVaultPass, store,
  titleById, vaultPass, watchDownloads, watchVaultPass,
} from '../store'
import { hostOf, type LooseFile } from '../data'

const props = defineProps<{ warning: string }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'quit'): void }>()

// the polls are only worth running while something is looking at them
onMounted(() => { watchDownloads(true); watchVaultPass(true); void pollDownloads(true) })
onBeforeUnmount(() => { watchDownloads(false); watchVaultPass(false) })

const rows = computed(() => downloads.items.filter((i) => i.state !== 'done'))
const running = computed(() => rows.value.filter((i) => i.state === 'downloading').length + (vaultPass.running ? 1 : 0))

function titleOf(id: string): string { return titleById(id)?.title || 'a title that is gone' }
function pct(i: { received: number; total: number }): number {
  return i.total ? Math.min(100, Math.round((i.received / i.total) * 100)) : 0
}
function mb(n: number): string { return `${(n / 1048576).toFixed(1)} MB` }
function amount(i: { received: number; total: number }): string {
  return i.total ? `${mb(i.received)} / ${mb(i.total)}` : mb(i.received)
}

// ---- the library job ----
const HOME = { import: 'Import', metadata: 'Metadata', settings: 'Settings' } as const
const passTitle = computed(() => (vaultPass.op ? vaultPass.op[0].toUpperCase() + vaultPass.op.slice(1) : 'A library job'))
function size(n: number): string {
  return n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(1)} GB` : mb(n)
}
// only an import counts bytes, and its steps are files
const passAmount = computed(() => {
  const p = vaultPass
  if (p.totalBytes) return `${p.done} of ${p.total} files · ${size(p.doneBytes)} of ${size(p.totalBytes)}`
  return p.total ? `${p.done} of ${p.total}` : ''
})
function openHome() {
  openSection(passHome(vaultPass.key))
  emit('close')
}

async function stop(id: string) {
  await window.longbox?.cancelDownload(id)
  try { await api.forgetDownload(id) } catch { /* already gone */ }
  await pollDownloads(true)
}

// ---- the library's downloads folder: what came with nothing armed ----
const loose = ref<LooseFile[]>([])
async function loadLoose() {
  try { loose.value = await api.looseFiles() } catch { loose.value = [] }
  store.loose = loose.value.length
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

onMounted(loadLoose)
// a download landing there shows up while the panel is open
watch(() => downloads.items.filter((i) => i.loose && i.state === 'done').length, loadLoose)
const canOpenFolder = !!window.longbox?.openFolder
async function showFolder() {
  try {
    const { path } = await api.looseFolder()
    if (!(await window.longbox?.openFolder(path))) store.error = `Could not open ${path}`
  } catch (e) { store.error = e instanceof Error ? e.message : String(e) }
}
async function dropLoose(f: LooseFile) {
  const ok = await askConfirm({
    title: 'Delete the file', danger: true, okLabel: 'Delete',
    message: `Delete ${f.name} from the library's downloads?`,
  })
  if (!ok) return
  try { await api.deleteLooseFile(f.name) } catch (e) { store.error = e instanceof Error ? e.message : String(e) }
  await loadLoose()
}
const ext = (name: string) => (name.split('.').pop() ?? '').slice(0, 4).toUpperCase()
function when(at: string): string {
  const d = new Date(at)
  if (Number.isNaN(d.getTime())) return ''
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toDateString() === new Date().toDateString() ? `today ${time}` : d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}
function sizeOf(n: number): string {
  return n < 1048576 ? `${Math.max(1, Math.round(n / 1024))} KB` : size(n)
}

</script>

<template>
  <div class="dlmask" @click.self="emit('close')">
    <div class="dlpanel">
      <div class="dlhead">
        <Icon name="activity" :size="15" :sw="2" />
        <span class="dlt">Activity</span>
        <span v-if="running" class="dlnum mono">{{ running }} running</span>
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
          <button class="btn ghost small" @click="emit('close')">Keep downloading</button>
          <button class="btn accent small" @click="emit('quit')">Close anyway</button>
        </div>
      </div>

      <div class="dlrows scroll">
        <div class="sec">LIBRARY</div>
        <div v-if="vaultPass.running" class="dlrow pass">
          <div class="dlmain">
            <Icon :name="passIcon(vaultPass.key)" :size="14" :sw="2" class="pic" />
            <span class="dlname">{{ passTitle }}</span>
          </div>
          <ProgressBar :done="vaultPass.totalBytes ? vaultPass.doneBytes : vaultPass.done"
                       :total="vaultPass.totalBytes || vaultPass.total" wide class="dlbar" />
          <div class="dlfoot">
            <span class="dlamt mono">{{ passAmount }}</span>
            <span class="dlpct mono">{{ passPercent() }}%</span>
            <div style="flex:1"></div>
            <button class="btn ghost small" @click="openHome">Open {{ HOME[passHome(vaultPass.key)] }}</button>
            <button class="btn ghost small" @click="stopVaultPass()">Stop</button>
          </div>
          <div v-if="vaultPass.detail" class="dlcur mono" :title="vaultPass.detail"><span>now</span> {{ vaultPass.detail }}</div>
        </div>
        <div v-else class="dlnone">Nothing is running.</div>

        <div class="sec">DOWNLOADS</div>
        <div v-if="!rows.length" class="dlnone">Nothing is downloading.</div>
        <div v-for="i in rows" :key="i.id" class="dlrow" :class="i.state">
          <div class="dlmain">
            <template v-if="i.loose">
              <span class="dlname mono">{{ i.filename || 'a download' }}</span>
              <span class="dlch">to the library's downloads</span>
            </template>
            <template v-else>
              <span class="dlname">{{ titleOf(i.titleId) }}</span>
              <span class="dlch mono">
                ch. {{ i.num }}<template v-if="i.lang"> · {{ i.lang }}</template>
              </span>
            </template>
          </div>
          <ProgressBar :done="i.received" :total="i.total" wide class="dlbar" />
          <div class="dlfoot">
            <span class="dlamt mono">{{ amount(i) }}</span>
            <span v-if="i.state === 'downloading'" class="dlpct mono">{{ pct(i) }}%</span>
            <span v-else class="dlerr">{{ i.error || i.state }}</span>
            <div style="flex:1"></div>
            <button v-if="i.state === 'interrupted'" class="btn ghost small" @click="resume(i.id)">
              Pick up
            </button>
            <button class="btn ghost small" @click="stop(i.id)">
              {{ i.state === 'downloading' ? 'Stop' : 'Forget' }}
            </button>
          </div>
        </div>

        <div class="sec">IN THE LIBRARY'S DOWNLOADS<template v-if="loose.length"> · {{ loose.length }}</template></div>
        <div v-if="!loose.length" class="dlnone">Nothing waiting — a download nobody armed lands here.</div>
        <div v-for="f in loose" :key="f.name" class="loose">
          <span class="lkind mono" :class="f.kind">{{ ext(f.name) }}</span>
          <div class="lmain">
            <div class="dlname mono" :title="f.name">{{ f.name }}</div>
            <div class="lsub">{{ sizeOf(f.size) }} · {{ when(f.at) }}<template v-if="f.pageUrl || f.fileUrl"> · from {{ hostOf(f.pageUrl || f.fileUrl) }}</template></div>
          </div>
          <button v-if="canOpenFolder" class="btn ghost small" title="Open the library's downloads folder" @click="showFolder">
            <Icon name="folder" :size="12" :sw="1.9" />Show
          </button>
          <button class="btn ghost small" @click="dropLoose(f)">Delete</button>
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
.dlrows { padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 9px; overflow-y: auto; min-height: 0; }
.sec { font: 700 9.5px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); display: flex; align-items: center; gap: 10px; padding: 6px 2px 0; }
.sec::after { content: ''; flex: 1; height: 1px; background: var(--line); }
.dlnone { padding: 2px 2px 4px; font: 400 12px/1.4 system-ui; color: var(--tx3); }
.dlrow { padding: 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--panel2); }
.dlrow.pass { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
.dlrow.interrupted { border-color: color-mix(in srgb, var(--warn) 45%, var(--line)); }
.dlrow.failed { border-color: color-mix(in srgb, var(--adult) 45%, var(--line)); }
.dlmain { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.dlrow.pass .dlmain { align-items: center; }
.pic { color: var(--accent); }
.dlname { font: 600 12.5px/1.3 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dlch { font-size: 11px; color: var(--tx3); flex: none; }
.dlbar { margin: 8px 0 6px; }
.dlfoot { display: flex; align-items: center; gap: 8px; }
.dlamt, .dlpct { font-size: 10.5px; color: var(--tx3); }
.dlerr { font: 500 11px/1.3 system-ui; color: var(--warn); }
.dlcur { margin-top: 6px; font-size: 11px; color: var(--tx2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.dlcur span { color: var(--tx3); }
.dlwarn { margin: 12px 12px 0; padding: 11px; border-radius: 9px; background: color-mix(in srgb, var(--warn) 10%, transparent); border: 1px solid color-mix(in srgb, var(--warn) 40%, var(--line)); }
.wt { font: 600 12.5px/1.3 system-ui; color: var(--tx); }
.wh { margin-top: 6px; font: 400 11px/1.5 system-ui; color: var(--tx2); }
.wacts { margin-top: 10px; display: flex; gap: 7px; justify-content: flex-end; }
.loose { display: flex; align-items: center; gap: 10px; padding: 9px 10px; border: 1px solid var(--line); border-radius: 9px; background: var(--panel2); }
.lkind { width: 32px; height: 30px; flex: none; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 9px; font-weight: 700; color: var(--tx2); background: var(--bg2); border: 1px solid var(--line); }
.lkind.subtitle { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 40%, var(--line)); }
.lmain { flex: 1; min-width: 0; }
.lsub { margin-top: 3px; font: 400 10.5px/1.3 system-ui; color: var(--tx3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
