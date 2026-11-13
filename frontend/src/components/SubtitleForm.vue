<script setup lang="ts">
// An episode's subtitles, edited under its row in the contents' edit mode
// (design/subtitles.md §3): the tracks it has, and one to add — a file dropped
// or picked, or one waiting in the library's downloads. The host draws the form
// grammar (`.entryform`, `.flabel`, `.irow`, `.iin`); this holds what is in it.
import { computed, onMounted, ref } from 'vue'
import Dropdown from './Dropdown.vue'
import Icon from './Icon.vue'
import { api } from '../api'
import { cache, store } from '../store'
import { askConfirm } from '../confirm'
import type { Chapter, LooseFile, Subtitle, Title } from '../data'

const props = defineProps<{ title: Title; chapter: Chapter }>()

const tracks = computed(() => props.chapter.subtitles ?? [])
const busy = ref(false)

async function run(work: () => Promise<Title>) {
  busy.value = true
  try {
    cache([await work()])
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

function makeDefault(s: Subtitle) {
  void run(() => api.updateSubtitle(props.title.id, props.chapter.id, s.id, { default: true }))
}
async function remove(s: Subtitle) {
  const ok = await askConfirm({
    title: 'Remove subtitles', danger: true, okLabel: 'Remove',
    message: `Remove the ${s.label} subtitles of episode ${props.chapter.num}? The file goes with them.`,
  })
  if (ok) void run(() => api.deleteSubtitle(props.title.id, props.chapter.id, s.id))
}

// ---- one to add: a file of the owner's, or one from the library's downloads ----
const downloads = ref<LooseFile[]>([])
// the episode's own number standing alone in a name — "05" in "Show - 05 [RUS]"
function forThisEpisode(f: LooseFile): boolean {
  const num = parseFloat(props.chapter.num)
  return !Number.isNaN(num) && (f.name.match(/\d+(?:\.\d+)?/g) ?? []).some((n) => parseFloat(n) === num)
}
const choices = computed(() => {
  const subs = downloads.value.filter((f) => f.kind === 'subtitle')
  return [...subs.filter(forThisEpisode), ...subs.filter((f) => !forThisEpisode(f))]
})
const picked = ref('')     // a file in the library's downloads, by name
const file = ref<File | null>(null)
const lang = ref('')
const label = ref('')
const asDefault = ref(false)
const fileEl = ref<HTMLInputElement | null>(null)
const dropHot = ref(false)

const options = computed(() => [
  { v: '', l: choices.value.length ? 'From downloads…' : 'Downloads: empty' },
  ...choices.value.map((f) => ({ v: f.name, l: forThisEpisode(f) ? `${f.name} — ep. ${props.chapter.num}` : f.name })),
])
function choose(name: string | number) {
  picked.value = String(name)
  file.value = null
  lang.value = downloads.value.find((f) => f.name === picked.value)?.lang ?? ''
}
function takeFile(f: File | undefined | null) {
  if (!f) return
  file.value = f
  picked.value = ''
  lang.value = '' // read off its name when it is stored
}
function onFile(e: Event) {
  const input = e.target as HTMLInputElement
  takeFile(input.files?.[0])
  input.value = ''
}
function onDrop(e: DragEvent) {
  dropHot.value = false
  takeFile(e.dataTransfer?.files?.[0])
}
const ready = computed(() => !busy.value && (!!file.value || !!picked.value))

async function loadDownloads() {
  try {
    downloads.value = await api.looseFiles()
  } catch { downloads.value = [] }
  store.loose = downloads.value.length
  if (!picked.value && !file.value && choices.value[0] && forThisEpisode(choices.value[0])) choose(choices.value[0].name)
}
onMounted(loadDownloads)

async function add() {
  if (!ready.value) return
  const t = props.title.id
  const c = props.chapter.id
  const body = { lang: lang.value.trim(), label: label.value.trim(), isDefault: asDefault.value }
  const f = file.value
  await run(() => (f
    ? api.addSubtitle(t, c, f, body.lang, body.label, body.isDefault)
    : api.subtitleFromDownloads(t, c, { name: picked.value, lang: body.lang, label: body.label, default: body.isDefault })))
  file.value = null
  picked.value = ''
  lang.value = label.value = ''
  asDefault.value = false
  await loadDownloads()
}
</script>

<template>
  <div :class="{ drophot: dropHot }" @dragover.prevent="dropHot = true" @dragleave="dropHot = false" @drop.prevent="onDrop">
    <div class="irow"><span class="flabel">TRACKS</span>
      <span class="hint">{{ tracks.length ? 'the player starts with the default' : 'none yet — drop a subtitle file here' }}</span></div>
    <div v-for="s in tracks" :key="s.id" class="track">
      <span class="lang mono">{{ (s.lang || '?').toUpperCase() }}</span>
      <span class="lbl">{{ s.label }}</span>
      <span v-if="s.default" class="def mono">DEFAULT</span>
      <button v-else class="mk" :disabled="busy" title="Start with this one" @click="makeDefault(s)">☆</button>
      <span class="from mono" :title="s.origName">{{ s.origName }} · {{ s.format }} → vtt</span>
      <button class="x" :disabled="busy" title="Remove these subtitles" @click="remove(s)"><Icon name="x" :size="11" :sw="2.4" /></button>
    </div>

    <div class="irow add"><span class="flabel">ADD</span>
      <span v-if="file" class="iin chosen mono" :title="file.name">{{ file.name }}</span>
      <Dropdown v-else class="pick" :model-value="picked" :options="options" @update:model-value="choose" />
      <button class="btn ghost small" @click="fileEl?.click()">File…</button>
      <input ref="fileEl" type="file" accept=".srt,.vtt,.ass,.ssa" style="display:none" @change="onFile" />
    </div>
    <div class="irow"><span class="flabel">LANG</span>
      <input v-model="lang" class="iin code mono" maxlength="8" placeholder="auto" title="Read off the file's name when left empty" spellcheck="false" />
      <input v-model="label" class="iin name" placeholder="label" title="The language's own name when left empty" spellcheck="false" />
    </div>
    <div class="irow end">
      <label class="defbox"><input v-model="asDefault" type="checkbox" /> start with this one</label>
      <button class="btn accent small" :disabled="!ready" @click="add">{{ busy ? 'Adding…' : 'Add' }}</button>
    </div>
  </div>
</template>

<style scoped>
.hint { font-size: 10.5px; color: var(--tx3); }
.track { display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 8px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel2); font: 500 12px/1 system-ui; min-width: 0; }
.track .lang { width: 26px; flex: none; font-size: 9.5px; font-weight: 700; color: var(--accent); }
.track .lbl { white-space: nowrap; }
.track .def { font-size: 9px; font-weight: 600; letter-spacing: .08em; color: var(--good); }
.track .mk, .track .x { border: none; background: none; cursor: pointer; color: var(--tx3); padding: 2px; display: inline-flex; }
.track .mk:hover, .track .x:hover { color: var(--accent); }
.track .from { margin-left: auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10.5px; color: var(--tx3); }
.add { margin-top: 4px; }
.pick { flex: 1; min-width: 0; }
.pick :deep(.lbl), .pick :deep(span) { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chosen { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.code { width: 64px; flex: none; }
.end { justify-content: flex-end; }
.name { flex: 1; min-width: 0; }
.defbox { display: inline-flex; align-items: center; gap: 5px; font: 500 11.5px/1 system-ui; color: var(--tx2); white-space: nowrap; }
</style>
