<script setup lang="ts">
// Three questions the vault cannot answer on its own, and something to do
// about each: is anything broken, is anything wasting space, is anything
// missing. One pass answers all three — duplicates and gaps read the index and
// cost nothing on top of the walk.
//
// Every answer is a sentence about the files, never the name of a mechanism: a
// person owns chapters, not sidecars. And every list is capped, because a
// library of thousands answers in thousands: the row unit is (title × problem),
// so forty broken chapters in one title read as one problem with that title.
import { computed, onMounted, reactive, ref, watch } from 'vue'
import Icon from './Icon.vue'
import PassButton from './PassButton.vue'
import { api, type CheckReport, type HistoryEntry } from '../api'
import { askConfirm, openTitle, store } from '../store'
import { isBoolMap, readLocal, writeLocal } from '../local'

const props = defineProps<{
  activeOp: string; done: number; total: number
  running: string   // what the RUNNING pass calls itself
}>()
const emit = defineEmits<{
  (e: 'run', fn: () => Promise<unknown>): void
  (e: 'stop'): void
  (e: 'report', r: CheckReport | null): void
}>()

const report = ref<CheckReport | null>(null)
const history = ref<HistoryEntry[]>([])

// The last answer is read off disk, not re-earned: a full check can take an
// hour, and closing the panel must not throw it away.
async function load() {
  try {
    const h = await api.vaultHealth()
    report.value = h.lastCheck
    history.value = h.history
    emit('report', h.lastCheck)
  } catch { /* an unread record is the same as none */ }
}
onMounted(load)

const MODES = [
  { v: 'quick', label: 'Quick' }, { v: 'full', label: 'Full' },
  { v: 'baseline', label: 'Full + record' },
] as const
const mode = ref<'quick' | 'full' | 'baseline'>('quick')
const MODE_HINT: Record<string, string> = {
  quick: 'Compares what is on disk against what was recorded: the file is there, it opens, its '
    + 'size matches. Costs one look per chapter.',
  full: 'Re-reads every byte to compare checksums — the only way to catch content that changed '
    + 'under the app. As slow as reading the whole library.',
  baseline: 'A full check that also records a checksum for content stored before there were any. '
    + 'That establishes a baseline from today; it cannot verify one.',
}

async function run() {
  const backfill = mode.value === 'baseline'
  if (backfill) {
    const ok = await askConfirm({
      title: 'Record checksums for existing content', okLabel: 'Record',
      message: 'Content stored before checksums existed has none to compare against. Recording '
        + 'one now proves the files have been stable from today on — it cannot prove they are '
        + 'what originally arrived. Nothing else about them is touched.',
    })
    if (!ok) return
  }
  emit('run', async () => {
    const r = await api.checkVault(mode.value !== 'quick', backfill)
    report.value = r
    emit('report', r)
    await load()
    return r
  })
}

const showFiles = ref(false)

// Each answer folds. On a library with three thousand findings the one you are
// working through should not have to be scrolled past every time; which ones
// you keep shut is worth remembering.
const open = reactive<Record<string, boolean>>(readLocal('lb.healthOpen', isBoolMap, {}))
watch(open, () => writeLocal('lb.healthOpen', { ...open }), { deep: true })
const isOpen = (k: string) => open[k] !== false
const toggle = (k: string) => { open[k] = !isOpen(k) }

async function sweep() {
  const left = report.value?.leftovers
  if (!left?.files) return
  const ok = await askConfirm({
    title: `Delete ${left.files} leftover files`, danger: true, okLabel: 'Delete',
    message: `These ${left.files} files (${mb(left.bytes)}) belong to no entry — nothing in the `
      + 'app can open or reach them. Deleting them frees the space and changes nothing you can '
      + 'see. Chapters, records and covers are never touched.',
  })
  if (!ok) return
  emit('run', async () => {
    const out = await api.deleteLeftovers()
    if (out.failed) store.error = `${out.failed} leftover file(s) could not be deleted`
    // NOT a fresh check: the sweep recomputed the leftovers of every title it
    // visited and corrected the stored report itself, so re-walking the whole
    // library would cost minutes to learn what is already written down.
    await load()
    return out
  })
}

function mb(n: number): string {
  if (n >= 1073741824) return `${(n / 1073741824).toFixed(1)} GB`
  if (n >= 1048576) return `${(n / 1048576).toFixed(1)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}
function n(x: number): string { return x.toLocaleString() }

function entryOf(r: { num: string; lang: string; group: string }): string {
  return [r.num && `ch. ${r.num}`, r.lang, r.group].filter(Boolean).join(' · ')
}
function go(id: string) { if (id) void openTitle(id) }

// The head of each answer: the sentence, and whether it needs anyone.
const brokenLine = computed(() => {
  const b = report.value?.broken
  if (!b?.total) return 'Every chapter checked can be read'
  return `${n(b.total)} ${b.total === 1 ? 'chapter' : 'chapters'} cannot be read`
})
const wasteBytes = computed(() =>
  (report.value?.leftovers.bytes ?? 0) + (report.value?.duplicates.bytes ?? 0))
const wasteLine = computed(() =>
  (wasteBytes.value ? `${mb(wasteBytes.value)} is being wasted` : 'Nothing is being wasted'))
const gapLine = computed(() => {
  const g = report.value?.gaps
  if (!g?.titles) return 'No title has a gap in its numbering'
  return `${n(g.titles)} ${g.titles === 1 ? 'title has' : 'titles have'} gaps in their numbering`
})
const hidden = (shown: number, total: number) => Math.max(0, total - shown)

// "two hours ago" answers the question a timestamp only implies.
function ago(iso: string): string {
  const then = Date.parse(iso)
  if (!then) return ''
  const s = Math.max(0, (Date.now() - then) / 1000)
  if (s < 90) return 'just now'
  if (s < 5400) return `${Math.round(s / 60)} min ago`
  if (s < 172800) return `${Math.round(s / 3600)} h ago`
  return `${Math.round(s / 86400)} days ago`
}
function took(seconds: number): string {
  if (seconds < 1) return ''
  return seconds < 90 ? `${Math.round(seconds)}s` : `${Math.round(seconds / 60)} min`
}
</script>

<template>
  <section class="card">
    <div class="row">
      <div class="text">
        <div class="label">Check the vault</div>
        <div class="seg" style="width: fit-content; margin-top:8px">
          <button v-for="m in MODES" :key="m.v" class="opt" :class="{ on: mode === m.v }"
                  @click="mode = m.v">{{ m.label }}</button>
        </div>
        <div class="hint">{{ MODE_HINT[mode] }}</div>
      </div>
      <span v-if="report && props.activeOp !== 'check'" class="mini mono">
        {{ n(report.withDigest) }} of {{ n(report.expected) }} have a checksum
      </span>
      <PassButton op="check" :active="props.activeOp" :done="props.done"
                  :total="props.total" :running="props.running"
                  label="Check" @run="run" @stop="emit('stop')" />
    </div>

    <!-- A whole library failing at once is a folder that moved, not damage —
         and three thousand identical rows would bury the only useful thing. -->
    <div v-if="report?.systemic" class="alarm">
      <span class="adot bad"></span>
      <div style="flex:1">
        <div class="aline">Almost nothing can be read — {{ n(report.broken.total) }} of
          {{ n(report.expected) }} chapters.</div>
        <div class="awhy">That is not damage to your files. A whole library failing at once almost
          always means the folder is not where the app is looking — a drive that is not mounted,
          a share that went offline, a path that moved.</div>
      </div>
    </div>

    <template v-else-if="report">
      <!-- 1 · is anything broken -->
      <button class="ahead" :class="{ flat: !report.broken.total }" :disabled="!report.broken.total"
              @click="toggle('broken')">
        <Icon v-if="report.broken.total" name="chevron" :size="10" :sw="2.6" class="hchev"
              :style="isOpen('broken') ? '' : 'transform: rotate(-90deg)'" />
        <span v-else class="hchev" style="width:10px"></span>
        <span class="adot" :class="{ bad: report.broken.total }"></span>
        <span class="answer">{{ brokenLine }}</span>
        <span v-if="report.broken.titles > 1" class="amount">in {{ n(report.broken.titles) }} titles</span>
      </button>
      <template v-if="report.broken.total && isOpen('broken')">
        <div class="list">
          <button v-for="(r, i) in report.broken.rows" :key="`${r.titleId}-${i}`" class="arow"
                  @click="go(r.titleId)">
            <span class="aname">{{ r.title || 'a title that is gone' }}</span>
            <span v-if="entryOf(r)" class="aentry">{{ entryOf(r) }}</span>
            <span class="awhat">{{ r.what }}</span>
            <span v-if="r.count > 1" class="acount">{{ n(r.count) }}</span>
            <Icon name="chevron" :size="10" :sw="2.4" class="ago" />
          </button>
        </div>
        <div v-if="hidden(report.broken.rows.length, report.broken.titles)" class="amore">
          {{ n(hidden(report.broken.rows.length, report.broken.titles)) }} more titles not listed
        </div>
        <div class="next">One row per title and problem — forty broken chapters in one title are one
          problem with that title. Open a row to replace a file or remove the entry, with the same
          controls the contents editor already has.</div>
      </template>

      <!-- 2 · is anything wasted -->
      <button class="ahead" :class="{ flat: !wasteBytes }" :disabled="!wasteBytes"
              @click="toggle('waste')">
        <Icon v-if="wasteBytes" name="chevron" :size="10" :sw="2.6" class="hchev"
              :style="isOpen('waste') ? '' : 'transform: rotate(-90deg)'" />
        <span v-else class="hchev" style="width:10px"></span>
        <span class="adot" :class="{ bad: wasteBytes }"></span>
        <span class="answer">{{ wasteLine }}</span>
      </button>
      <template v-if="wasteBytes && isOpen('waste')">
        <div v-if="report.leftovers.files" class="srow">
          <span class="sname">Leftovers</span>
          <span class="ssize">{{ n(report.leftovers.files) }} files · {{ mb(report.leftovers.bytes) }}
            · in {{ n(report.leftovers.titles) }} titles</span>
          <div class="sacts">
            <button class="btn ghost small" @click="showFiles = !showFiles">
              {{ showFiles ? 'Hide' : 'Show' }}
            </button>
            <PassButton op="sweep" :active="props.activeOp" :done="props.done"
                        :total="props.total" :running="props.running" small danger icon="x"
                        :label="`Delete ${n(report.leftovers.files)} files`"
                        @run="sweep" @stop="emit('stop')" />
          </div>
        </div>
        <div v-if="showFiles && report.leftovers.files" class="files">
          <div v-for="(f, i) in report.leftovers.rows" :key="`${f.titleId}-${i}`">
            {{ f.title }} / {{ f.name }}<span>{{ mb(f.bytes) }}</span>
          </div>
          <div v-if="hidden(report.leftovers.rows.length, report.leftovers.files)" class="dim">
            …and {{ n(hidden(report.leftovers.rows.length, report.leftovers.files)) }} more
          </div>
        </div>
        <div v-if="report.duplicates.sets" class="srow">
          <span class="sname">Stored twice</span>
          <span class="ssize">{{ n(report.duplicates.sets) }} sets · {{ mb(report.duplicates.bytes) }}</span>
        </div>
        <div v-if="report.duplicates.sets" class="list">
          <template v-for="g in report.duplicates.groups" :key="g.sha256">
            <button v-for="c in g.copies" :key="`${c.titleId}-${c.num}`" class="arow"
                    @click="go(c.titleId)">
              <span class="aname">{{ c.title }}</span>
              <span class="aentry">{{ entryOf(c) }}</span>
              <span class="awhat">{{ mb(g.size) }}</span>
              <Icon name="chevron" :size="10" :sw="2.4" class="ago" />
            </button>
          </template>
        </div>
        <div class="next">Leftovers belong to no entry at all — nothing else in the app can reach
          them, which is why they can only go from here. Copies are yours to choose between: open
          each and delete the one you do not want.</div>
      </template>

      <!-- 3 · is anything missing -->
      <button class="ahead" :class="{ flat: !report.gaps.titles }" :disabled="!report.gaps.titles"
              @click="toggle('gaps')">
        <Icon v-if="report.gaps.titles" name="chevron" :size="10" :sw="2.6" class="hchev"
              :style="isOpen('gaps') ? '' : 'transform: rotate(-90deg)'" />
        <span v-else class="hchev" style="width:10px"></span>
        <span class="adot" :class="{ idle: report.gaps.titles }"></span>
        <span class="answer">{{ gapLine }}</span>
        <span v-if="report.gaps.titles > 1" class="amount">most incomplete first</span>
      </button>
      <template v-if="report.gaps.titles && isOpen('gaps')">
        <div class="list">
          <button v-for="(g, i) in report.gaps.rows" :key="`${g.titleId}-${i}`" class="arow"
                  @click="go(g.titleId)">
            <span class="aname">{{ g.title }}</span>
            <span v-if="g.lang || g.group" class="aentry">
              {{ [g.lang, g.group].filter(Boolean).join(' · ') }}
            </span>
            <span class="awhat">{{ g.what }}</span>
            <Icon name="chevron" :size="10" :sw="2.4" class="ago" />
          </button>
        </div>
        <div v-if="hidden(report.gaps.rows.length, report.gaps.titles)" class="amore">
          {{ n(hidden(report.gaps.rows.length, report.gaps.titles)) }} more titles not listed
        </div>
        <div class="next">Nothing is wrong with these — they are what is left to find.</div>
      </template>
    </template>

    <!-- What has been done to this library. Kept in the vault, so it travels
         with the library and two of them never share a history. -->
    <template v-if="history.length">
      <button class="ahead" :class="{ flat: !history.length }" :disabled="!history.length"
              @click="toggle('log')">
        <Icon v-if="history.length" name="chevron" :size="10" :sw="2.6" class="hchev"
              :style="isOpen('log') ? '' : 'transform: rotate(-90deg)'" />
        <span v-else class="hchev" style="width:10px"></span>
        <span class="adot"></span>
        <span class="answer">Recently done to this library</span>
        <span class="amount">{{ history.length }}</span>
      </button>
      <div v-if="isOpen('log')" class="list">
        <div v-for="(h, i) in history" :key="`${h.at}-${i}`" class="hrow">
          <span class="hop">{{ h.op }}</span>
          <span class="hout">{{ h.outcome }}<template v-if="h.stopped"> · stopped early</template></span>
          <span v-if="took(h.seconds)" class="htook">{{ took(h.seconds) }}</span>
          <span class="hago">{{ ago(h.at) }}</span>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* One answer per question, in the card's own row rhythm: boxes inside a box is
   what made this read as a jumble. */
.ahead { display: flex; align-items: center; gap: 10px; width: 100%; padding: 11px 18px; background: var(--panel2); border: none; border-top: 1px solid color-mix(in srgb, var(--line) 55%, transparent); cursor: pointer; text-align: left; color: inherit; }
.ahead:hover { background: var(--hover); }
/* nothing under it, nothing to fold — and no hint that there is */
.ahead.flat { cursor: default; }
.ahead.flat:hover { background: var(--panel2); }
.hchev { color: var(--tx3); flex: none; transition: transform .12s ease; }
.adot { width: 7px; height: 7px; border-radius: 999px; flex: none; background: var(--good); }
.adot.bad { background: var(--adult); }
.adot.idle { background: var(--warn); }
.answer { font: 600 12px/1 system-ui; color: var(--tx); }
.amount { margin-left: auto; font: 500 11px/1 ui-monospace, monospace; color: var(--tx3); }

/* thousands of findings scroll inside their answer, never down the page */
.list { max-height: 306px; overflow-y: auto; }
.arow { display: flex; align-items: center; gap: 12px; width: 100%; height: 34px; padding: 0 18px; background: none; border: none; border-top: 1px solid color-mix(in srgb, var(--line) 40%, transparent); cursor: pointer; text-align: left; color: inherit; }
.arow:hover { background: var(--hover); }
.aname { font: 500 12px/1 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 45%; }
.aentry { font: 500 11px/1 ui-monospace, monospace; color: var(--tx3); flex: none; }
.awhat { margin-left: auto; font: 400 11px/1 system-ui; color: var(--tx2); flex: none; }
.acount { font: 600 11px/1 ui-monospace, monospace; color: var(--tx2); flex: none; min-width: 42px; text-align: right; font-variant-numeric: tabular-nums; }
.ago { color: var(--tx3); flex: none; transform: rotate(-90deg); }
.amore { padding: 9px 18px; font: 400 11px/1 system-ui; color: var(--tx3); border-top: 1px solid color-mix(in srgb, var(--line) 40%, transparent); }
.next { padding: 9px 18px 12px; font: 400 11px/1.4 system-ui; color: var(--tx3); border-top: 1px solid color-mix(in srgb, var(--line) 40%, transparent); }

.srow { display: flex; align-items: center; gap: 12px; padding: 10px 18px; border-top: 1px solid color-mix(in srgb, var(--line) 40%, transparent); }
.sname { font: 500 12px/1 system-ui; color: var(--tx); }
.ssize { font: 500 11px/1 ui-monospace, monospace; color: var(--tx3); }
.sacts { margin-left: auto; display: flex; align-items: center; gap: 7px; }

/* exactly what would go, before it goes */
.files { padding: 8px 18px 12px; display: flex; flex-direction: column; gap: 4px; max-height: 180px; overflow-y: auto; border-top: 1px solid color-mix(in srgb, var(--line) 40%, transparent); }
.files > div { font: 400 11px/1.5 ui-monospace, monospace; color: var(--tx3); display: flex; gap: 12px; }
.files > div span { margin-left: auto; }
.files .dim { color: var(--tx2); }

.hrow { display: flex; align-items: center; gap: 12px; padding: 0 18px; height: 30px; border-top: 1px solid color-mix(in srgb, var(--line) 40%, transparent); }
.hop { font: 500 12px/1 system-ui; color: var(--tx); flex: none; min-width: 132px; }
.hout { font: 400 11px/1 system-ui; color: var(--tx2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.htook, .hago { font: 500 11px/1 ui-monospace, monospace; color: var(--tx3); flex: none; }
.htook { margin-left: auto; }
.hago { min-width: 76px; text-align: right; }

.alarm { padding: 14px 18px; display: flex; align-items: flex-start; gap: 12px; background: color-mix(in srgb, var(--adult) 9%, transparent); border-top: 1px solid color-mix(in srgb, var(--adult) 35%, var(--line)); }
.alarm .adot { margin-top: 4px; }
.aline { font: 600 12px/1.3 system-ui; color: var(--tx); }
.awhy { margin-top: 5px; font: 400 11px/1.5 system-ui; color: var(--tx2); }
</style>
