<script setup lang="ts">
// The things you RUN against the library, as opposed to the questions you ask
// of it. All three walk the whole vault, so all three take the one pass slot,
// report the same way and stop the same way — a row that grew its own progress
// is exactly how this page stopped looking like one page.
//
// Each also says when IT last ran, from the library's own record: "when was
// this last done" is asked right where you would do it again.
import { onMounted, ref } from 'vue'
import PassButton from './PassButton.vue'
import { api, type HistoryEntry } from '../api'
import { askConfirm } from '../store'

const props = defineProps<{
  activeOp: string; done: number; total: number
  running: string   // what the RUNNING pass calls itself
}>()
const emit = defineEmits<{
  (e: 'run', fn: () => Promise<unknown>): void
  (e: 'stop'): void
  (e: 'counted', titles: number): void
}>()

const history = ref<HistoryEntry[]>([])
async function loadHistory() {
  try { history.value = (await api.vaultHealth()).history } catch { /* none yet */ }
}
onMounted(loadHistory)

function lastRun(op: string): string {
  const h = history.value.find((e) => e.op === op)
  if (!h) return 'never run'
  const then = Date.parse(h.at)
  const s = then ? Math.max(0, (Date.now() - then) / 1000) : 0
  const when = !then ? ''
    : s < 90 ? 'just now'
      : s < 5400 ? `${Math.round(s / 60)} min ago`
        : s < 172800 ? `${Math.round(s / 3600)} h ago`
          : `${Math.round(s / 86400)} days ago`
  return `${when} · ${h.outcome}`
}

async function run(fn: () => Promise<unknown>, confirm: Parameters<typeof askConfirm>[0]) {
  if (!(await askConfirm(confirm))) return
  emit('run', async () => {
    const out = await fn()
    await loadHistory()
    return out
  })
}

function rebuild() {
  void run(async () => emit('counted', (await api.rebuildIndex()).title_count), {
    title: 'Rebuild the search index', okLabel: 'Rebuild',
    message: 'Re-read every title on disk and rebuild the search index from scratch? '
      + 'Your files are NEVER modified — this only recreates the index. '
      + 'With a large library it can take a while; the app keeps working meanwhile.',
  })
}

function convert() {
  void run(() => api.normalizeArchives(), {
    title: 'Convert archives to zip', okLabel: 'Convert',
    message: 'Walk every stored chapter and convert anything that is not a plain zip '
      + '(cbz, rar, 7z). This already ran once, when the library was first opened — re-run it '
      + 'after installing an unrar backend, or when archives were added to the folder outside '
      + 'the app. It reads every title either way.',
  })
}

function mirror() {
  void run(() => api.refreshComicInfo(), {
    title: 'Write metadata into the archives', okLabel: 'Update',
    message: 'A ComicInfo.xml lives INSIDE each archive, so giving one to a chapter that has '
      + 'none means rewriting that archive whole. On a library where none of them carry it yet '
      + 'that is every chapter you own, read and written once — hours over a network folder. '
      + 'Nothing is lost: pages and any files the archive already held are carried over, and '
      + 'chapters that already match are skipped. It can be stopped and resumed.',
  })
}
</script>

<template>
  <section class="card">
    <div class="row">
      <div class="text">
        <div class="label">Search index</div>
        <div class="hint">A cache derived from the title files on disk — deleting it never loses
          content. Rebuild re-reads every title and recreates it, which is worth doing when the
          folder was changed outside the app or search results look wrong. Your files are never
          modified.</div>
      </div>
      <span v-if="props.activeOp !== 'rebuild'" class="mini mono">{{ lastRun('rebuild index') }}</span>
      <PassButton op="rebuild" :active="props.activeOp" :done="props.done" :total="props.total"
                  :running="props.running" label="Rebuild" @run="rebuild" @stop="emit('stop')" />
    </div>

    <div class="row">
      <div class="text">
        <div class="label">Convert archives to zip</div>
        <div class="hint">Every chapter is stored as a plain zip, so its pages can always be read
          and edited. Anything arriving through the app is converted on the way in, so this is a
          one-time pass over content that was already in the folder — it ran when this library was
          first opened. Re-run it after installing an unrar backend, or when archives were added
          to the folder by hand. It reads every title either way.</div>
      </div>
      <span v-if="props.activeOp !== 'convert'" class="mini mono">{{ lastRun('convert archives') }}</span>
      <PassButton op="convert" :active="props.activeOp" :done="props.done" :total="props.total"
                  :running="props.running" label="Convert" @run="convert" @stop="emit('stop')" />
    </div>

    <div class="row">
      <div class="text">
        <div class="label">Metadata inside the archives</div>
        <div class="hint">Every chapter carries a ComicInfo.xml describing the work it belongs to,
          so the library reads correctly in Komga, Kavita and anything else that speaks the format
          — and outlives this app. New and edited chapters get it as they are written; this
          rewrites the rest after metadata changes, and only where it differs.</div>
      </div>
      <span v-if="props.activeOp !== 'mirror'" class="mini mono">{{ lastRun('update metadata') }}</span>
      <PassButton op="mirror" :active="props.activeOp" :done="props.done" :total="props.total"
                  :running="props.running" label="Update" @run="mirror" @stop="emit('stop')" />
    </div>
  </section>
</template>
