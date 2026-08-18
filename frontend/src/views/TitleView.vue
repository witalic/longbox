<script setup lang="ts">
// View-only: ALL editing happens through THE draft in the browser's capture
// panel (one editor, one flow — design/state-model.md §4). Only the user layer
// (favorite, rating, read state) writes through instantly from here.
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'
import EntryFields from '../components/EntryFields.vue'
import Dropdown from '../components/Dropdown.vue'
import { openInBrowser } from '../browser'
import { readLocalOne, writeLocalOne } from '../local'
import { api } from '../api'
import { chapterIdFor, compareChapterNums, coverAt, faviconFor, filterOptions, orderedChaptersOf, sameChapter } from '../data'

// domains whose favicon failed to load → fall back to the initial letters
const noIcon = reactive<Record<string, boolean>>({})
import {
  addChapterRow, askConfirm, cache, clearTitleSource, commitChapterOrder, deleteTitle,
  editChapterRow, filterBy, filterByPerson, groupSuggestions, langSuggestions, openReader,
  setFavorite, setRating, setRead, store, titleById,
} from '../store'
import {
  READ_COLOR as readColor, groupByNum, statusColor, hueFor, initials,
  type Chapter, type ReadState, type Title,
} from '../data'

const t = computed<Title | undefined>(() => titleById(store.activeTitle))
const pagesTotal = computed(() => (t.value?.chapters ?? []).reduce((n, c) => n + (c.pages || 0), 0))

const NEXT_READ: Record<ReadState, ReadState> = { unread: 'reading', reading: 'read', read: 'unread' }

async function removeTitle() {
  if (!t.value) return
  const ok = await askConfirm({
    title: 'Delete title', danger: true, okLabel: 'Delete',
    message: `Delete “${t.value.title}” from the library? This removes its files on disk.`,
  })
  if (ok) await deleteTitle(t.value.id)
}
function cycleRead(c: Chapter) {
  if (t.value) void setRead(t.value, c.id, NEXT_READ[c.read])
}
async function removeSourceLink() {
  if (!t.value) return
  const ok = await askConfirm({
    title: 'Remove source link', danger: true, okLabel: 'Remove',
    message: `Unlink ${t.value.source.domain} from “${t.value.title}”? The title and its data stay; only the link to the source page is removed.`,
  })
  if (ok) await clearTitleSource(t.value)
}
function coverStyle(x: { cover: string; id: string; title: string }) {
  return x.cover
    ? { background: `#181a1f url("${coverAt(x.cover, 480)}") center/cover no-repeat` }
    : { background: hueFor(x.id || x.title) }
}
const flagDefs = [
  { key: 'adult', tag: '18+', color: 'var(--adult)' },
  { key: 'ai', tag: 'AI', color: 'var(--ai)' },
  { key: 'censored', tag: 'CENSORED', color: 'var(--cens)' },
] as const
const activeFlags = computed(() => flagDefs.filter((f) => t.value?.flags[f.key]))

// ---- the chapter TREE: one node per number, leaves per translation ----
// Display order: smart by number ('auto'), or exactly as the user arranged it
// ('manual' — set by dragging rows in edit mode).
interface ChapterNode { num: string; rows: Chapter[] }
const orderedChapters = computed<Chapter[]>(() =>
  orderedChaptersOf(t.value?.chapters, t.value?.chapterOrder))
// language / translator filters over the chapter list
const langFilter = ref('all')
const groupFilter = ref('all')
const langOpts = computed(() => filterOptions(orderedChapters.value, (c) => c.lang))
const groupOpts = computed(() => filterOptions(orderedChapters.value, (c) => c.group))
const filtersActive = computed(() => langFilter.value !== 'all' || groupFilter.value !== 'all')

const tree = computed<ChapterNode[]>(() => groupByNum(orderedChapters.value.filter((c) =>
  (langFilter.value === 'all' || c.lang === langFilter.value)
  && (groupFilter.value === 'all' || c.group === groupFilter.value))))
const closedNums = reactive<Record<string, boolean>>({}) // tree nodes default to open

// ---- edit mode: reorder (drag num-groups), delete rows/files/pages ----
const editMode = ref(false)
const dragNum = ref<string | null>(null)
const dropTarget = ref<string | null>(null) // num we'd land BEFORE; '#end' = the end
const canDrag = computed(() => editMode.value && !filtersActive.value)
function onDragOverNum(num: string) {
  if ((dragNum.value && dragNum.value !== num) || (dragRow.value && dragRow.value.num !== num)) dropTarget.value = num
}
function clearDrag() {
  dragNum.value = null
  dragRow.value = null
  dropTarget.value = null
}

// re-grouping: dragging a TRANSLATION row onto any label makes it adopt that
// label (the group is derived from the label, nothing else is stored)
const dragRow = ref<Chapter | null>(null)
function onRowDrop(targetNum: string) {
  const moved = dragRow.value
  if (moved) {
    clearDrag()
    if (!t.value || moved.num === targetNum) return
    void editChapterRow(t.value, moved.id, { num: targetNum, lang: moved.lang, group: moved.group, url: moved.url })
    return
  }
  dropOnNum(targetNum)
}

// ---- inline entry edit: LABEL · LANGUAGE · GROUP · SOURCE LINK (id stable) ----
const editRow = ref<string | null>(null)
const eLabel = ref('')
const eLang = ref('')
const eGroup = ref('')
const eUrl = ref('')
function openEntryEdit(c: Chapter) {
  editRow.value = editRow.value === c.id ? null : c.id
  eLabel.value = [c.num, c.title].filter(Boolean).join(' ')
  eLang.value = c.lang
  eGroup.value = c.group
  eUrl.value = c.url
}
async function saveEntryEdit() {
  if (!t.value || !editRow.value || !eLabel.value.trim()) return
  const ok = await editChapterRow(t.value, editRow.value, {
    num: eLabel.value.trim(), lang: eLang.value.trim(), group: eGroup.value.trim(), url: eUrl.value.trim(),
  })
  if (ok) editRow.value = null
}
function applyOrder(nums: string[]) {
  if (!t.value) return
  const byNum = new Map(groupByNum(orderedChapters.value).map((n) => [n.num, n.rows]))
  const ids = nums.flatMap((num) => (byNum.get(num) ?? []).map((c) => c.id))
  void commitChapterOrder(t.value, ids, 'manual')
}
function dropOnNum(targetNum: string) {
  const from = dragNum.value
  clearDrag()
  if (!t.value || from === null || from === targetNum) return
  const nums = groupByNum(orderedChapters.value).map((n) => n.num)
  const i = nums.indexOf(from)
  if (i < 0) return
  nums.splice(i, 1)
  const j = nums.indexOf(targetNum)
  nums.splice(j < 0 ? nums.length : j, 0, from)
  applyOrder(nums)
}
function dropAtEnd() {
  const from = dragNum.value
  clearDrag()
  if (!t.value || from === null) return
  const nums = groupByNum(orderedChapters.value).map((n) => n.num).filter((n) => n !== from)
  nums.push(from)
  applyOrder(nums)
}
function sortAuto() {
  if (!t.value) return
  const ids = [...t.value.chapters].sort((a, b) => compareChapterNums(a.num, b.num)).map((c) => c.id)
  void commitChapterOrder(t.value, ids, 'auto')
}

// ---- add entry: a row, optionally with an archive file (edit mode) ----
const importOpen = ref(false)
const iNum = ref('') // the single free-text LABEL
const iLang = ref('')
const iGroup = ref('')
const iUrl = ref('')
const importing = ref(false)
const iLangSuggest = computed(() => langSuggestions(t.value?.chapters ?? []))
const iGroupSuggest = computed(() => groupSuggestions())
const ARCHIVE_RE = /\.(zip|cbz|rar|7z)$/i
async function importEntryArchive(file: File) {
  if (!t.value) return
  importing.value = true
  try {
    cache([await api.importChapterArchive(t.value.id,
      { num: iNum.value.trim(), lang: iLang.value.trim(), group: iGroup.value.trim(), url: iUrl.value.trim() }, file)])
    importOpen.value = false
    iNum.value = ''
    iUrl.value = ''
  } catch (err) {
    store.error = err instanceof Error ? err.message : String(err)
  } finally {
    importing.value = false
  }
}
// the same form, without a file: just create the chapter row
async function addRowOnly() {
  if (!t.value || !iNum.value.trim()) return
  if (await addChapterRow(t.value, {
    num: iNum.value.trim(), lang: iLang.value.trim(), group: iGroup.value.trim(), url: iUrl.value.trim(),
  })) {
    iNum.value = ''
    iUrl.value = ''
  }
}
// the same form from loose images: create the row (or reuse an existing
// identical one), then upload the files as its pages
async function uploadEntryImages(files: File[]) {
  if (!files.length || !t.value || !iNum.value.trim()) return
  importing.value = true
  try {
    const ch = { num: iNum.value.trim(), lang: iLang.value.trim(), group: iGroup.value.trim(), url: iUrl.value.trim() }
    const same = (c: Chapter) => sameChapter(c, ch)
    let row = t.value.chapters.find(same)
    if (!row) {
      if (!(await addChapterRow(t.value, ch))) return
      row = t.value.chapters.find(same)
    }
    if (!row) return
    cache([await api.addChapterPages(t.value.id, row.id, files)])
    importOpen.value = false
    iNum.value = ''
    iUrl.value = ''
  } catch (err) {
    store.error = err instanceof Error ? err.message : String(err)
  } finally {
    importing.value = false
  }
}
// ONE entry point for the entry's media: an archive or a set of images — the
// branch is decided by what actually arrived. The OS can't put files and a
// folder into one dialog, so folders come in by DROPPING them onto the form.
const IMAGE_RE = /\.(jpe?g|png|webp|gif|avif|bmp)$/i
const filesEl = ref<HTMLInputElement | null>(null)
async function handleEntryFiles(files: File[]) {
  if (!files.length) return
  const archives = files.filter((f) => ARCHIVE_RE.test(f.name))
  const images = files.filter((f) => IMAGE_RE.test(f.name))
  if (archives.length === 1 && !images.length) await importEntryArchive(archives[0])
  else if (images.length) await uploadEntryImages(sortFilesNat(images))
  else store.error = 'Pick ONE archive (.zip/.cbz/.rar/.7z), or image files'
}
async function onEntryFiles(e: Event) {
  const input = e.target as HTMLInputElement
  const files = [...(input.files ?? [])]
  input.value = ''
  await handleEntryFiles(files)
}
// drag & drop onto the form: files AND folders (walked recursively)
const entryDropHot = ref(false)
async function filesFromDataTransfer(dt: DataTransfer): Promise<File[]> {
  const entries = [...dt.items].map((i) => i.webkitGetAsEntry?.()).filter((x): x is FileSystemEntry => !!x)
  if (!entries.length) return [...dt.files]
  const out: File[] = []
  async function walk(entry: FileSystemEntry): Promise<void> {
    if (entry.isFile) {
      out.push(await new Promise<File>((res, rej) => (entry as FileSystemFileEntry).file(res, rej)))
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader()
      // readEntries returns batches (≤100) — keep reading until it runs dry
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((res, rej) => reader.readEntries(res, rej))
        if (!batch.length) break
        for (const e of batch) await walk(e)
      }
    }
  }
  for (const e of entries) await walk(e)
  return out
}
async function onEntryDrop(e: DragEvent) {
  entryDropHot.value = false
  if (!e.dataTransfer || !iNum.value.trim() || importing.value) return
  await handleEntryFiles(await filesFromDataTransfer(e.dataTransfer))
}

// attach (or replace) an archive file on an EXISTING row — the per-row ⬇ button
const attachFor = ref<Chapter | null>(null)
const attachEl = ref<HTMLInputElement | null>(null)
function pickAttach(c: Chapter) {
  attachFor.value = c
  attachEl.value?.click()
}
async function onAttachFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  const c = attachFor.value
  attachFor.value = null
  if (!file || !c || !t.value) return
  importing.value = true
  try {
    cache([await api.importChapterArchive(t.value.id,
      { num: c.num, lang: c.lang, group: c.group, chapterId: c.id }, file)])
  } catch (err) {
    store.error = err instanceof Error ? err.message : String(err)
  } finally {
    importing.value = false
  }
}

// ---- manual cover: set/replace from a local image, or remove ----
const coverFileEl = ref<HTMLInputElement | null>(null)
async function onCoverFile(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  input.value = ''
  if (!f || !t.value) return
  const bytes = new Uint8Array(await f.arrayBuffer())
  let bin = ''
  for (let i = 0; i < bytes.length; i += 32768) bin += String.fromCharCode(...bytes.subarray(i, i + 32768))
  try {
    cache([await api.setCover(t.value.id, { data: btoa(bin), contentType: f.type || 'image/jpeg' })])
  } catch (err) {
    store.error = err instanceof Error ? err.message : String(err)
  }
}
async function removeCover() {
  if (!t.value) return
  const ok = await askConfirm({
    title: 'Remove cover', danger: true, okLabel: 'Remove',
    message: 'Remove the stored cover image?',
  })
  if (!ok) return
  try { cache([await api.deleteCover(t.value.id)]) } catch (e) { store.error = String(e) }
}

// ---- the pages pane (right column, open by default) ----
// Server-side thumbnails: the grid loads downscaled cached JPEGs, never the
// full-size pages — a 54-page chapter is a few MB of previews, not ~50 MB.
const openPages = ref<string | null>(null)
const pageCount = ref(0)
const selPages = ref<number[]>([])
const pagesError = ref('')

const THUMB_SIZES = { s: { grid: 104, w: 160 }, m: { grid: 150, w: 280 }, l: { grid: 220, w: 440 } } as const
type ThumbKey = keyof typeof THUMB_SIZES
const thumbKey = ref<ThumbKey>(readLocalOne('lb.pageThumb', ['s', 'm', 'l'] as const, 'm'))
watch(thumbKey, (v) => writeLocalOne('lb.pageThumb', v))
const thumb = computed(() => THUMB_SIZES[thumbKey.value])

const openChapter = computed<Chapter | undefined>(() =>
  t.value?.chapters.find((c) => c.id === openPages.value))

function pageSrc(cid: string, index: number, v: string): string {
  // cap 1.5 crops webtoon-length pages to the tiles' 2:3 shape (previews only)
  return api.chapterPageSrc(t.value!.id, cid, index, v, thumb.value.w, 1.5)
}
const pagesTitle = ref<string | null>(null) // which TITLE the pane belongs to
async function selectPages(c: Chapter) {
  if (!c.dl || !c.pages) {
    // in edit mode a BARE entry opens too — an empty pane whose "Add images"
    // creates the archive; outside edit mode there is nothing to show
    if (!editMode.value || !t.value) return
    pagesTitle.value = t.value.id
    openPages.value = c.id
    selPages.value = []
    selAnchor.value = null
    pageCount.value = 0
    pagesError.value = ''
    return
  }
  const tid = t.value!.id
  pagesTitle.value = tid
  openPages.value = c.id
  selPages.value = []
  pageCount.value = 0
  pagesError.value = ''
  try {
    const n = (await api.chapterPages(tid, c.id)).count
    // a stale response (tab switched mid-flight) must not clobber the pane
    if (t.value?.id === tid && openPages.value === c.id) pageCount.value = n
  } catch (e) {
    if (t.value?.id === tid) pagesError.value = e instanceof Error ? e.message : String(e)
  }
}
// the pane opens BY DEFAULT on the first downloaded chapter. NOTE: chapter ids
// can COLLIDE across titles (same num+lang+group hash) — "the same chapter is
// still open" is only true when the TITLE hasn't changed either.
watch(() => [t.value?.id, t.value?.chapters], () => {
  const chapters = t.value?.chapters ?? []
  const current = chapters.find((c) => c.id === openPages.value && c.dl && c.pages)
  if (current && pagesTitle.value === t.value?.id) return
  const first = chapters.find((c) => c.dl && c.pages)
  openPages.value = null
  pageCount.value = 0
  if (first) void selectPages(first)
}, { immediate: true, deep: false })
function togglePageSel(index: number) {
  if (!editMode.value) return
  const k = selPages.value.indexOf(index)
  if (k >= 0) selPages.value.splice(k, 1)
  else selPages.value.push(index)
}
// range selection: a plain click anchors, Shift+click selects anchor→here
const selAnchor = ref<number | null>(null) // display position of the last plain click
// outside edit mode a page tile opens the READER right at that page
function onTileClick(pos: number, srcIdx: number, e: MouseEvent) {
  if (editMode.value) {
    if (e.shiftKey && selAnchor.value !== null) {
      const [a, b] = selAnchor.value <= pos ? [selAnchor.value, pos] : [pos, selAnchor.value]
      const set = new Set(selPages.value)
      for (const idx of pageMap.value.slice(a, b + 1)) set.add(idx)
      selPages.value = [...set]
    } else {
      togglePageSel(srcIdx)
      selAnchor.value = pos
    }
    return
  }
  if (t.value && openPages.value) void openReader(t.value.id, openPages.value, pos)
}
// ---- pages: add loose images / a folder; move the selection between entries ----
const addImgEl = ref<HTMLInputElement | null>(null)
async function pickAddImagesRow(c: Chapter) {
  await selectPages(c)
  await nextTick() // the pane's hidden input mounts with the selection
  addImgEl.value?.click()
}
const addDirEl = ref<HTMLInputElement | null>(null)
// upload order = page order: natural-sort by file name ('page2' before 'page10'),
// because the OS file picker's order is arbitrary
function sortFilesNat(files: File[]): File[] {
  const key = (s: string) => s.split(/(\d+)/).map((t) => (/^\d+$/.test(t) ? t.padStart(8, '0') : t.toLowerCase())).join('')
  return files.sort((a, b) => key(a.name).localeCompare(key(b.name)))
}
async function onAddImages(e: Event) {
  const input = e.target as HTMLInputElement
  const files = sortFilesNat([...(input.files ?? [])])
  input.value = ''
  const cid = openPages.value
  if (!files.length || !t.value || !cid) return
  importing.value = true
  try {
    cache([await api.addChapterPages(t.value.id, cid, files)])
    pageCount.value = (await api.chapterPages(t.value.id, cid)).count
  } catch (err) {
    store.error = err instanceof Error ? err.message : String(err)
  } finally {
    importing.value = false
  }
}

const moveOpen = ref(false)
const moveRoot = ref<HTMLElement | null>(null)
function onDocDown(e: MouseEvent) {
  if (moveRoot.value && !moveRoot.value.contains(e.target as Node)) moveOpen.value = false
}
onMounted(() => document.addEventListener('mousedown', onDocDown))
onBeforeUnmount(() => document.removeEventListener('mousedown', onDocDown))

// move targets: every OTHER entry, in display order (translations are concrete
// targets of their own — each is its own archive)
const moveTargets = computed<ChapterNode[]>(() => groupByNum(orderedChapters.value))
async function movePagesTo(targetId: string) {
  const cid = openPages.value
  if (!t.value || !cid || !selPages.value.length || targetId === cid) return
  moveOpen.value = false
  try {
    cache([await api.moveChapterPages(t.value.id, cid, targetId, selPages.value.map(serverIdx))])
    selPages.value = []
    const still = t.value.chapters.find((c) => c.id === cid && c.dl && c.pages)
    if (still) pageCount.value = (await api.chapterPages(t.value.id, cid)).count
    else openPages.value = null // the pane's watch re-selects the first entry
  } catch (err) {
    store.error = err instanceof Error ? err.message : String(err)
  }
}
// "New entry from selection": create the row, then move into it
const newFromSel = ref(false)
const nLabel = ref('')
const nLang = ref('')
const nGroup = ref('')
async function createEntryFromSelection() {
  if (!t.value || !nLabel.value.trim()) return
  const ch = { num: nLabel.value.trim(), lang: nLang.value.trim(), group: nGroup.value.trim() }
  if (!(await addChapterRow(t.value, ch))) return
  newFromSel.value = false
  nLabel.value = ''
  await movePagesTo(chapterIdFor(ch))
}

// Manual page order — LOCAL-FIRST with STABLE urls: pageMap maps display
// position → ORIGINAL page index (pane-open time); tiles keep their src for
// the whole edit session, so a move never reloads a single image. lastSync
// mirrors what the SERVER order currently is — each write is expressed
// relative to it; the mapping only resets (with a URL re-version) when the
// pane re-opens the chapter.
const dragPage = ref<number | null>(null)   // display position being dragged
const pageDrop = ref<number | null>(null)   // display position we land BEFORE; -1 = end
const pageMap = ref<number[]>([])
let lastSync: number[] = []
watch(pageCount, (n) => {
  pageMap.value = [...Array(n).keys()]
  lastSync = [...pageMap.value]
}, { immediate: true })
// selection and delete/move talk to the server in CURRENT server indexes
function serverIdx(orig: number): number {
  return lastSync.indexOf(orig)
}
function onPageDrop(target: number) {
  const from = dragPage.value
  dragPage.value = null
  pageDrop.value = null
  if (from === null || target === from || !t.value || !openPages.value) return
  const map = [...pageMap.value]
  const [moved] = map.splice(from, 1)
  const at = target < 0 ? map.length : target > from ? target - 1 : target
  map.splice(at, 0, moved)
  pageMap.value = map // the visual move happens NOW; no URL changes at all
  void applyPageOrder()
}
// Each write is expressed relative to lastSync, which only advances when the
// server answers — so a second drop while the first is in flight would compute
// its permutation against an order the vault has already left. They queue.
let orderChain: Promise<void> = Promise.resolve()
function applyPageOrder(): Promise<void> {
  orderChain = orderChain.then(sendPageOrder, sendPageOrder)
  return orderChain
}

async function sendPageOrder() {
  const want = [...pageMap.value]
  const order = want.map((orig) => lastSync.indexOf(orig))
  if (order.some((i) => i < 0)) return // the pane reset under us; nothing to send
  try {
    cache([await api.reorderChapterPages(t.value!.id, openPages.value!, order)])
    lastSync = want
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
    pageMap.value = [...lastSync] // revert the visual move on failure
  }
}

async function deleteSelectedPages() {
  const cid = openPages.value
  if (!t.value || !cid || !selPages.value.length) return
  const ok = await askConfirm({
    title: 'Delete pages', danger: true, okLabel: 'Delete',
    message: `Delete ${selPages.value.length} page(s) from this chapter's archive? The file is rewritten without them.`,
  })
  if (!ok) return
  try {
    cache([await api.deleteChapterPages(t.value.id, cid, selPages.value.map(serverIdx))])
    selPages.value = []
    pageCount.value = (await api.chapterPages(t.value.id, cid)).count
  } catch (e) { store.error = String(e) }
}
async function removeDownload(c: Chapter) {
  if (!t.value) return
  const ok = await askConfirm({
    title: 'Remove download', danger: true, okLabel: 'Remove',
    message: `Remove the downloaded file for ch. ${c.num}${c.lang ? ' · ' + c.lang : ''}? The chapter row stays.`,
  })
  if (!ok) return
  try {
    cache([await api.deleteChapterMedia(t.value.id, c.id)])
    if (openPages.value === c.id) openPages.value = null
  } catch (e) { store.error = String(e) }
}
async function removeRow(c: Chapter) {
  if (!t.value) return
  const ok = await askConfirm({
    title: 'Remove chapter', danger: true, okLabel: 'Remove',
    message: `Remove chapter ${c.num}${c.lang ? ' · ' + c.lang : ''}${c.group ? ' · ' + c.group : ''} AND its downloaded file?`,
  })
  if (!ok) return
  try {
    cache([await api.deleteChapterRow(t.value.id, c.id)])
    if (openPages.value === c.id) openPages.value = null
  } catch (e) { store.error = String(e) }
}
</script>

<template>
  <div class="tv viewcol" v-if="t">
    <div class="viewscroll scroll">
    <div class="band">
      <div class="coverwrap">
        <div class="cover" :style="coverStyle(t)">
          <template v-if="!t.cover">
            <div class="ctitle">{{ t.title }}</div>
            <div class="cstory mono">{{ t.authors.join(', ') }}</div>
          </template>
          <button class="favbtn" :style="{ color: t.fav ? 'var(--fav)' : '#cfd3dc' }" @click="setFavorite(t, !t.fav)">
            <Icon name="heart" :size="16" :fill="t.fav ? 'var(--fav)' : 'none'" />
          </button>
          <div class="covacts">
            <button class="covbtn" :title="t.cover ? 'Replace the cover with an image file' : 'Set a cover from an image file'" @click="coverFileEl?.click()">
              <Icon name="image" :size="13" :sw="1.9" />
            </button>
            <button v-if="t.cover" class="covbtn" title="Remove cover" @click="removeCover">
              <Icon name="x" :size="12" :sw="2.2" />
            </button>
          </div>
          <input ref="coverFileEl" type="file" accept="image/*" style="display:none" @change="onCoverFile" />
        </div>
      </div>

      <div class="info">
        <div class="badges">
          <span v-if="t.type" class="typebadge">{{ t.type }}</span>
          <span v-if="t.status" class="statusbadge"><span class="sdot" :style="{ background: statusColor(t.status) }"></span>{{ t.status }}</span>
          <span v-if="t.year" class="yearbadge mono">{{ t.year }}</span>
          <div style="flex:1"></div>
          <button class="iconbtn" title="Delete" @click="removeTitle"><Icon name="x" :size="15" :sw="2" /></button>
          <button class="btn" title="Edit in the browser's capture panel (draft seeded from this title)" @click="openInBrowser(t.id)">
            <Icon name="edit" :size="14" :sw="1.9" />Edit
          </button>
        </div>

        <h1 class="title">{{ t.title }}</h1>
        <div v-if="t.alt" class="alt">{{ t.alt }}</div>

        <!-- empty fields simply don't render — the page shows what IS known -->
        <div v-if="activeFlags.length" class="flags">
          <span class="eyebrow" style="margin-right:2px">FLAGS</span>
          <span v-for="f in activeFlags" :key="f.key" class="flagtag" :style="{ color: f.color, borderColor: f.color }">{{ f.tag }}</span>
        </div>

        <div v-if="t.authors.length || t.artists.length" class="authors">
          <template v-if="t.authors.length">
            <span class="role">Story</span>
            <a v-for="a in t.authors" :key="'au' + a" class="authorlink" :title="`Filter library by ${a}`" @click="filterByPerson(a)">
              <span class="avatar">{{ initials(a) }}</span><span class="pname">{{ a }}</span>
            </a>
          </template>
          <template v-if="t.artists.length">
            <span class="role">Art</span>
            <a v-for="a in t.artists" :key="'ar' + a" class="authorlink" :title="`Filter library by ${a}`" @click="filterByPerson(a)">
              <span class="avatar">{{ initials(a) }}</span><span class="pname">{{ a }}</span>
            </a>
          </template>
        </div>

        <div class="rating">
          <span class="eyebrow">MY RATING</span>
          <div style="display:flex;gap:3px">
            <span v-for="i in 5" :key="i" class="ratestar" :style="{ color: i <= t.rating ? 'var(--warn)' : 'var(--tx3)' }" @click="setRating(t, i)">
              <Icon name="star" :size="20" :sw="1.4" :fill="i <= t.rating ? 'var(--warn)' : 'none'" />
            </span>
          </div>
          <span style="font:500 12px/1 system-ui;color:var(--tx3)">{{ t.rating }}/5</span>
        </div>

        <div v-if="t.characters.length" class="metarow">
          <span class="eyebrow" style="width:56px;flex:none">CAST</span>
          <div class="wrap"><span v-for="ch in t.characters" :key="ch" class="genre link" :title="`Filter by ${ch}`" @click="filterBy('character', ch)">{{ ch }}</span></div>
        </div>
        <div v-if="t.genres.length" class="metarow" :style="t.characters.length ? 'margin-top:9px' : ''">
          <span class="eyebrow" style="width:56px;flex:none">GENRES</span>
          <div class="wrap"><span v-for="g in t.genres" :key="g" class="genre link" :title="`Filter by ${g}`" @click="filterBy('genre', g)">{{ g }}</span></div>
        </div>
        <div v-if="t.tags.length" class="metarow" style="margin-top:9px">
          <span class="eyebrow" style="width:56px;flex:none">TAGS</span>
          <div class="wrap"><span v-for="tg in t.tags" :key="tg" class="tag link" :title="`Filter by ${tg}`" @click="filterBy('tag', tg)">{{ tg }}</span></div>
        </div>

        <p v-if="t.desc" class="desc">{{ t.desc }}</p>

        <div v-if="t.source.url" class="srcrow">
          <span class="eyebrow">SOURCE</span>
          <span class="srcchip" style="cursor:pointer" :title="t.source.url" @click="openInBrowser(t.id, t.source.url)">
            <span class="srcinit">
              <img v-if="!noIcon[t.source.domain]" class="favimg" :src="faviconFor(t.source.domain)" alt=""
                   @error="noIcon[t.source.domain] = true" />
              <template v-else>{{ (t.source.domain.slice(0, 2) || '?').toUpperCase() }}</template>
            </span>{{ t.source.domain }}
            <Icon name="forward" :size="11" :sw="2" style="color:var(--tx3)" />
          </span>
          <button class="srcx" title="Remove the source link (the title stays)" @click="removeSourceLink">
            <Icon name="x" :size="11" :sw="2.4" />
          </button>
        </div>
      </div>
    </div>

    <!-- CONTENTS: entries with ONE free-text label; a label with several
         translations groups into a guide-lined block (contents-editor-mockup v3) -->
    <div class="lower">
      <div class="chapters">
        <div class="chhead">
          <h2>Contents</h2>
          <span class="mono" style="color:var(--tx3);font-size:12px">{{ t.chapters.length }}</span>
          <Dropdown v-if="langOpts.length > 2" label="LANG" :model-value="langFilter" :options="langOpts" @update:model-value="langFilter = String($event)" />
          <Dropdown v-if="groupOpts.length > 2" label="GROUP" :model-value="groupFilter" :options="groupOpts" @update:model-value="groupFilter = String($event)" />
          <div style="flex:1"></div>
          <template v-if="editMode">
            <button class="btn ghost chsmall" @click="importOpen = !importOpen"><Icon name="plus" :size="12" :sw="2.2" />Add entry</button>
            <button v-if="t.chapterOrder === 'manual'" class="btn ghost chsmall" title="Back to smart number order" @click="sortAuto">Sort №</button>
            <button class="btn accent chsmall" @click="editMode = false; selPages = []; importOpen = false; editRow = null">Done</button>
          </template>
          <button v-else class="iconbtn chedit" title="Edit contents: add/edit entries, attach files, drag to reorder" @click="editMode = true">
            <Icon name="edit" :size="13" :sw="1.9" />
          </button>
        </div>
        <div v-if="editMode" class="chhint mono">
          drag to reorder{{ filtersActive ? ' (clear the filters first)' : '' }} · drag a translation onto another label to move it there · click pages to select
        </div>

        <!-- add entry — the SAME form design as the inline edit below -->
        <div v-if="editMode && importOpen" class="entryform addform" :class="{ drophot: entryDropHot }"
             @dragover.prevent="entryDropHot = true" @dragleave="entryDropHot = false" @drop.prevent="onEntryDrop">
          <EntryFields v-model:label="iNum" v-model:lang="iLang" v-model:group="iGroup" v-model:url="iUrl"
                       :lang-suggest="iLangSuggest" :group-suggest="iGroupSuggest"
                       label-placeholder="5 / 2024 Artworks / Extra" />
          <div class="irow">
            <span class="drophint mono">drop an archive · images · a whole folder</span>
            <div style="flex:1"></div>
            <button class="btn ghost chsmall" :disabled="importing" @click="importOpen = false; iNum = ''; iUrl = ''">Cancel</button>
            <button class="btn ghost chsmall" :disabled="!iNum.trim() || importing" title="Create the entry row without a file — add images later from the pages pane" @click="addRowOnly">Add row only</button>
            <button class="btn accent chsmall" :disabled="!iNum.trim() || importing" title="One archive (.zip/.cbz/.rar/.7z) or a set of images (webp converts to a standard format); drop a folder onto the form" @click="filesEl?.click()">
              {{ importing ? 'Importing…' : 'Choose files…' }}
            </button>
          </div>
          <input ref="filesEl" type="file" accept=".zip,.cbz,.rar,.7z,image/*" multiple style="display:none" @change="onEntryFiles" />
        </div>
        <input ref="attachEl" type="file" accept=".zip,.cbz,.rar,.7z" style="display:none" @change="onAttachFile" />

        <div v-if="tree.length" class="chlist">
          <template v-for="node in tree" :key="node.num">
            <!-- GROUP: header row + translations inside a guide-lined block -->
            <template v-if="node.rows.length > 1">
              <div class="chrow node" :class="{ dragging: dragNum === node.num, dropbefore: dropTarget === node.num }"
                   :draggable="canDrag" @dragstart="dragNum = node.num" @dragend="clearDrag"
                   @dragover.prevent="onDragOverNum(node.num)" @drop.prevent="onRowDrop(node.num)"
                   @click="closedNums[node.num] = !closedNums[node.num]">
                <span class="chslot">
                  <span v-if="canDrag" class="grip mono" title="Drag to reorder">&#8942;&#8942;</span>
                  <Icon v-else name="chevron" :size="12" :sw="2.2" :style="{ transform: closedNums[node.num] ? 'rotate(-90deg)' : '', transition: 'transform .12s' }" />
                </span>
                <span class="chtitle"><b class="chnum mono">{{ node.num }}</b><template v-if="node.rows.find((r) => r.title)"> {{ node.rows.find((r) => r.title)?.title }}</template></span>
                <span class="trcount mono">{{ node.rows.length }} translations</span>
              </div>
              <div v-if="!closedNums[node.num]" class="grp">
                <template v-for="c in node.rows" :key="c.id">
                  <div class="chrow tr" :class="{ open: openPages === c.id, openable: c.dl && c.pages, dragging: dragRow?.id === c.id }"
                       :draggable="canDrag" @dragstart.stop="dragRow = c" @dragend="clearDrag"
                       @dragover.prevent="onDragOverNum(node.num)" @drop.prevent="onRowDrop(node.num)"
                       @click="selectPages(c)">
                    <span class="chslot">
                      <span v-if="canDrag" class="grip mono" title="Drag onto another label to move this translation">&#8942;&#8942;</span>
                      <span v-else class="chdot s" :style="{ background: readColor[c.read] }" :title="`${c.read} — click to change`" @click.stop="cycleRead(c)"></span>
                    </span>
                    <span class="trbadge"><span class="mono" style="color:var(--accent);font-size:9px">{{ c.lang || '?' }}</span><template v-if="c.group">{{ c.group }}</template></span>
                    <span class="chtitle"></span>
                    <span class="chright">
                      <span class="pgcol mono" :title="c.dlSource">{{ c.dl ? (c.pages ? `${c.pages} pg` : 'file') : '' }}</span>
                      <span v-if="editMode" class="chacts">
                        <button class="chact" title="Edit entry: label · language · group · source link" @click.stop="openEntryEdit(c)"><Icon name="edit" :size="12" :sw="1.9" /></button>
                        <button class="chact" title="Add images to this entry" @click.stop="pickAddImagesRow(c)"><Icon name="image" :size="12" :sw="1.9" /></button>
                        <button class="chact" :title="c.dl ? 'Replace the archive file' : 'Attach an archive file'" @click.stop="pickAttach(c)"><Icon name="download" :size="12" :sw="2" /></button>
                        <button v-if="c.dl" class="chact" title="Remove the file (keep the entry)" @click.stop="removeDownload(c)"><Icon name="minus" :size="12" :sw="2.4" /></button>
                        <button class="chact danger" title="Remove the entry AND its file" @click.stop="removeRow(c)"><Icon name="x" :size="12" :sw="2.4" /></button>
                      </span>
                    </span>
                  </div>
                  <div v-if="editMode && editRow === c.id" class="entryform">
                    <EntryFields v-model:label="eLabel" v-model:lang="eLang" v-model:group="eGroup" v-model:url="eUrl"
                                 :lang-suggest="iLangSuggest" :group-suggest="iGroupSuggest" />
                    <div class="irow" style="justify-content:flex-end">
                      <button class="btn ghost chsmall" @click="editRow = null">Cancel</button>
                      <button class="btn accent chsmall" :disabled="!eLabel.trim()" @click="saveEntryEdit">Save</button>
                    </div>
                  </div>
                </template>
              </div>
            </template>

            <!-- SINGLE entry -->
            <template v-else>
              <div class="chrow" :class="{ open: openPages === node.rows[0].id, openable: node.rows[0].dl && node.rows[0].pages, dragging: dragNum === node.num, dropbefore: dropTarget === node.num }"
                   :draggable="canDrag"
                   @dragstart="dragNum = node.num" @dragend="clearDrag"
                   @dragover.prevent="onDragOverNum(node.num)" @drop.prevent="onRowDrop(node.num)"
                   @click="selectPages(node.rows[0])">
                <span class="chslot">
                  <span v-if="canDrag" class="grip mono" title="Drag to reorder">&#8942;&#8942;</span>
                  <span v-else class="chdot" :style="{ background: readColor[node.rows[0].read] }" :title="`${node.rows[0].read} — click to change`" @click.stop="cycleRead(node.rows[0])"></span>
                </span>
                <span class="chtitle"><b class="chnum mono">{{ node.rows[0].num }}</b><template v-if="node.rows[0].title"> {{ node.rows[0].title }}</template></span>
                <span class="chright">
                  <span v-if="node.rows[0].lang || node.rows[0].group" class="trbadge"><span class="mono" style="color:var(--accent);font-size:9px">{{ node.rows[0].lang || '?' }}</span><template v-if="node.rows[0].group">{{ node.rows[0].group }}</template></span>
                  <span class="pgcol mono" :title="node.rows[0].dlSource">{{ node.rows[0].dl ? (node.rows[0].pages ? `${node.rows[0].pages} pg` : 'file') : '' }}</span>
                  <span v-if="editMode" class="chacts">
                    <button class="chact" title="Edit entry: label · language · group · source link" @click.stop="openEntryEdit(node.rows[0])"><Icon name="edit" :size="12" :sw="1.9" /></button>
                    <button class="chact" title="Add images to this entry" @click.stop="pickAddImagesRow(node.rows[0])"><Icon name="image" :size="12" :sw="1.9" /></button>
                    <button class="chact" :title="node.rows[0].dl ? 'Replace the archive file' : 'Attach an archive file'" @click.stop="pickAttach(node.rows[0])"><Icon name="download" :size="12" :sw="2" /></button>
                    <button v-if="node.rows[0].dl" class="chact" title="Remove the file (keep the entry)" @click.stop="removeDownload(node.rows[0])"><Icon name="minus" :size="12" :sw="2.4" /></button>
                    <button class="chact danger" title="Remove the entry AND its file" @click.stop="removeRow(node.rows[0])"><Icon name="x" :size="12" :sw="2.4" /></button>
                  </span>
                </span>
              </div>
              <div v-if="editMode && editRow === node.rows[0].id" class="entryform">
                <EntryFields v-model:label="eLabel" v-model:lang="eLang" v-model:group="eGroup" v-model:url="eUrl"
                             :lang-suggest="iLangSuggest" :group-suggest="iGroupSuggest" />
                <div class="irow" style="justify-content:flex-end">
                  <button class="btn ghost chsmall" @click="editRow = null">Cancel</button>
                  <button class="btn accent chsmall" :disabled="!eLabel.trim()" @click="saveEntryEdit">Save</button>
                </div>
              </div>
            </template>
          </template>
          <!-- a visible landing strip for "move to the end" -->
          <div v-if="dragNum" class="dropend" :class="{ hot: dropTarget === '#end' }"
               @dragover.prevent="dropTarget = '#end'" @drop.prevent="dropAtEnd">
            move to the end
          </div>
        </div>
        <div v-else class="emptychapters">
          No entries yet — capture or download them from the source in the browser,
          or <a class="emptyact" @click="editMode = true; importOpen = true">add them by hand</a> (rows, archives, loose images).
        </div>
      </div>

      <!-- the pages pane: fills the right side, open by default -->
      <div v-if="editMode || t.chapters.some((c) => c.dl && c.pages)" class="pagespane">
        <div class="pghead">
          <span v-if="openChapter" class="mono pglabel">{{ openChapter.num }}<template v-if="openChapter.lang"> · {{ openChapter.lang }}</template> — {{ pageCount }} pages<template v-if="selPages.length"> · <span style="color:var(--accent)">{{ selPages.length }} selected</span></template></span>
          <div style="flex:1"></div>
          <template v-if="editMode && openChapter">
            <button class="btn ghost chsmall" :disabled="importing" title="Append image files to this entry (its archive is created on first add)" @click="addImgEl?.click()">
              <Icon name="plus" :size="12" :sw="2.2" />{{ importing ? 'Adding…' : 'Add images' }}
            </button>
            <button class="btn ghost chsmall" :disabled="importing" title="Append a whole folder of images" @click="addDirEl?.click()">Folder</button>
            <span ref="moveRoot" class="movewrap">
              <button v-if="selPages.length" class="btn chsmall" title="Move the selected pages into another entry" @click="moveOpen = !moveOpen">Move to…</button>
              <div v-if="moveOpen" class="movemenu">
                <template v-for="node in moveTargets" :key="node.num">
                  <div v-if="node.rows.length > 1" class="mvhead mono">{{ node.num }}</div>
                  <template v-for="c in node.rows" :key="c.id">
                    <div v-if="c.id !== openPages" class="mvrow" :class="{ sub: node.rows.length > 1 }" @click="movePagesTo(c.id)">
                      <span v-if="node.rows.length === 1" class="mono mvnum">{{ c.num }}</span>
                      <span v-if="node.rows.length > 1 || c.lang || c.group" class="trbadge"><span class="mono" style="color:var(--accent);font-size:9px">{{ c.lang || '?' }}</span><template v-if="c.group">{{ c.group }}</template></span>
                      <span class="mvpg mono">{{ c.dl && c.pages ? `${c.pages} pg` : '+ creates file' }}</span>
                    </div>
                  </template>
                </template>
                <div v-if="!newFromSel" class="mvrow new" @click.stop="newFromSel = true">+ New entry from selection…</div>
                <div v-else class="mvform" @click.stop>
                  <input v-model="nLabel" class="iin" placeholder="Label *" />
                  <div style="display:flex;gap:6px">
                    <input v-model="nLang" class="iin" style="width:64px" placeholder="EN" />
                    <input v-model="nGroup" class="iin" style="flex:1;min-width:0" placeholder="group" />
                  </div>
                  <button class="btn accent chsmall" :disabled="!nLabel.trim()" @click="createEntryFromSelection">Create + move {{ selPages.length }}</button>
                </div>
              </div>
            </span>
            <button v-if="selPages.length" class="btn dangerbtn chsmall" @click="deleteSelectedPages">
              <Icon name="x" :size="12" :sw="2.2" />Delete {{ selPages.length }}
            </button>
            <input ref="addImgEl" type="file" accept="image/*" multiple style="display:none" @change="onAddImages" />
            <input ref="addDirEl" type="file" webkitdirectory style="display:none" @change="onAddImages" />
          </template>
          <div class="seg zseg">
            <button v-for="k in (['s', 'm', 'l'] as const)" :key="k" :class="{ on: thumbKey === k }"
                    :title="`Thumbnail size: ${k.toUpperCase()}`" @click="thumbKey = k">{{ k.toUpperCase() }}</button>
          </div>
        </div>
        <div v-if="pagesError" class="pgerror">Could not read the archive's pages: {{ pagesError }}</div>
        <div v-else-if="openChapter && !pageCount" class="pgerror">
          {{ openChapter.dl ? "No readable pages in this archive." : "No pages yet — use “Add images” to start this entry." }}
        </div>
        <div v-else-if="openChapter" class="pgrid scroll">
          <div v-for="(srcIdx, pos) in pageMap" :key="`${openChapter.id}-${srcIdx}`" class="ptile"
               :class="{ sel: selPages.includes(srcIdx), selectable: editMode, pdragging: dragPage === pos, pdrop: pageDrop === pos && dragPage !== null }"
               :style="{ width: thumb.grid + 'px' }"
               :title="editMode ? 'Click to select · Shift+click selects a range · drag to rearrange' : `Read from page ${pos + 1}`"
               :draggable="editMode"
               @dragstart="dragPage = pos" @dragend="dragPage = null; pageDrop = null"
               @dragover.prevent="pageDrop = pos" @drop.prevent="onPageDrop(pos)"
               @click="onTileClick(pos, srcIdx, $event)">
            <img :src="pageSrc(openChapter.id, srcIdx, openChapter.v)" loading="lazy" alt="" />
            <span class="pnum mono">{{ pos + 1 }}</span>
            <span v-if="editMode" class="pcheck" :class="{ on: selPages.includes(srcIdx) }">
              <Icon v-if="selPages.includes(srcIdx)" name="check" :size="10" :sw="3" />
            </span>
          </div>
          <div v-if="dragPage !== null" class="pgend" :class="{ hot: pageDrop === -1 }"
               @dragover.prevent="pageDrop = -1" @drop.prevent="onPageDrop(-1)">move to the end</div>
        </div>
      </div>
    </div>
    </div>
    <div class="lfoot">
      <span class="mono lfinfo">{{ t.chapters.length }} entr{{ t.chapters.length === 1 ? 'y' : 'ies' }}<template v-if="pagesTotal"> · {{ pagesTotal }} pages</template></span>
    </div>
  </div>
</template>

<style scoped>
.band { display: flex; gap: 28px; padding: 26px 30px 24px; align-items: flex-start; }
.coverwrap { flex: none; width: 196px; }
.cover { position: relative; width: 196px; height: 284px; border-radius: 8px; overflow: hidden; box-shadow: 0 10px 28px rgba(0,0,0,.45); border: 1px solid var(--line); display: flex; flex-direction: column; justify-content: flex-end; padding: 16px; }
.ctitle { font: 600 26px/1.02 Georgia, serif; letter-spacing: -.5px; color: #f2ede6; text-shadow: 0 2px 10px rgba(0,0,0,.5); }
.cstory { margin-top: 6px; font-size: 10px; letter-spacing: .14em; color: rgba(242,237,230,.6); }
.favbtn { position: absolute; top: 8px; right: 8px; width: 32px; height: 32px; border: none; border-radius: 8px; background: rgba(10,10,14,.55); backdrop-filter: blur(6px); cursor: pointer; display: flex; align-items: center; justify-content: center; }
.covacts { position: absolute; bottom: 8px; right: 8px; display: flex; gap: 6px; opacity: 0; transition: opacity .12s; }
.cover:hover .covacts { opacity: 1; }
.covbtn { width: 28px; height: 28px; border: none; border-radius: 7px; background: rgba(10,10,14,.55); backdrop-filter: blur(6px); color: #cfd3dc; cursor: pointer; display: flex; align-items: center; justify-content: center; }
.covbtn:hover { color: #fff; background: rgba(10,10,14,.75); }
.emptyact { color: var(--accent); cursor: pointer; text-decoration: underline; }

.info { flex: 1; min-width: 0; }
.badges { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.typebadge { display: inline-flex; align-items: center; height: 22px; padding: 0 8px; font: 600 10px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--accent); border: 1px solid var(--accent); border-radius: 5px; text-transform: uppercase; }
.statusbadge { display: inline-flex; align-items: center; gap: 6px; height: 22px; padding: 0 9px; font: 500 11px/1 system-ui; color: var(--tx2); border: 1px solid var(--line); border-radius: 5px; text-transform: capitalize; }
.yearbadge { display: inline-flex; align-items: center; height: 22px; padding: 0 9px; font: 500 11px/1 ui-monospace, monospace; color: var(--tx3); border: 1px solid var(--line); border-radius: 5px; }
.sdot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
.title { margin: 0; font: 600 30px/1.08 system-ui; letter-spacing: -.6px; color: var(--tx); }
.alt { margin-top: 6px; font: 400 13px/1.4 system-ui; color: var(--tx3); }

.flags { margin-top: 14px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.flagtag { font: 700 10px/1 ui-monospace, monospace; letter-spacing: .05em; border: 1px solid; padding: 4px 8px; border-radius: 5px; }
.authors { margin-top: 15px; display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; font: 400 13px/1 system-ui; color: var(--tx2); }
.authors .role { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; text-transform: uppercase; color: var(--tx3); }
.authorlink { display: inline-flex; align-items: center; gap: 7px; cursor: pointer; border-radius: 6px; padding: 2px 6px 2px 2px; }
.authorlink:hover { background: var(--panel2); }
.authorlink .pname { font-weight: 500; color: var(--tx); }
.authorlink:hover .pname { color: var(--accent); }
.avatar { width: 22px; height: 22px; border-radius: 50%; background: linear-gradient(135deg, #4a4f5e, #2a2d38); display: inline-flex; align-items: center; justify-content: center; font: 600 9px/1 system-ui; color: #cfd3dc; }
.rating { margin-top: 16px; display: flex; align-items: center; gap: 10px; }
.ratestar { cursor: pointer; display: flex; }
.ratestar:hover { transform: scale(1.12); }
.metarow { margin-top: 18px; display: flex; gap: 10px; align-items: flex-start; }
.metarow .wrap { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.genre { display: inline-flex; align-items: center; gap: 6px; font: 500 12px/1 system-ui; color: var(--tx); background: var(--panel2); border: 1px solid var(--line); padding: 5px 10px; border-radius: 6px; }
.tag { display: inline-flex; align-items: center; gap: 5px; font: 400 11px/1 system-ui; color: var(--tx2); padding: 4px 8px; border-radius: 5px; border: 1px dashed var(--line); }
.link { cursor: pointer; }
.genre.link:hover { border-color: var(--accent); color: var(--accent); }
.tag.link:hover { border-color: var(--accent); color: var(--accent); }
.desc { margin: 18px 0 0; max-width: 760px; font: 400 13.5px/1.65 system-ui; color: var(--tx2); }
.srcrow { margin-top: 18px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.srcchip { display: inline-flex; align-items: center; gap: 7px; font: 500 12px/1 system-ui; color: var(--tx); background: var(--panel2); border: 1px solid var(--line); padding: 5px 9px 5px 5px; border-radius: 6px; }
.srcinit { width: 20px; height: 20px; border-radius: 5px; display: inline-flex; align-items: center; justify-content: center; font: 600 9px/1 system-ui; color: #fff; background: var(--panel); border: 1px solid var(--line); overflow: hidden; }
.favimg { width: 14px; height: 14px; object-fit: contain; }
.srcx { width: 22px; height: 22px; border: none; border-radius: 5px; background: transparent; color: var(--tx3); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
.srcx:hover { background: var(--hover); color: var(--adult); }

.lower { border-top: 1px solid var(--line); padding: 22px 26px 30px; display: flex; gap: 20px; align-items: flex-start; }
.chapters { flex: 0 1 470px; min-width: 340px; }
.chhead { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.chhead h2 { margin: 0; font: 600 16px/1 system-ui; color: var(--tx); }
.chlist { margin-top: 14px; display: flex; flex-direction: column; gap: 3px; }
.chrow { display: flex; align-items: center; gap: 10px; padding: 9px 12px; border-radius: 8px; border: 1px solid transparent; }
.chrow:hover { background: var(--panel2); }
.chrow.node { color: var(--tx2); cursor: pointer; }
/* ONE 14px lead slot: read dot / chevron / drag handle — the label text starts
   at the same x in every row (contents-editor-mockup v3) */
.chslot { flex: none; width: 14px; display: inline-flex; align-items: center; justify-content: center; color: var(--tx3); }
.grip { font-size: 14px; letter-spacing: -3px; color: var(--tx3); cursor: grab; user-select: none; line-height: 1; padding: 6px; margin: -6px; }
.grip:hover { color: var(--tx); }
/* the WHOLE row drags in edit mode — the grip is the affordance, not the handle */
.chrow[draggable="true"] { cursor: grab; }
.chnum { font: 600 13px/1 ui-monospace, monospace; color: var(--tx); }
.chright { margin-left: auto; display: inline-flex; align-items: center; gap: 8px; flex: none; }
.pgerror { padding: 12px 14px; font: 400 12px/1.5 system-ui; color: var(--warn); }
.chdot { flex: none; width: 10px; height: 10px; border-radius: 50%; cursor: pointer; }
.chdot.s { width: 8px; height: 8px; }
.chdot:hover { outline: 2px solid var(--accentSoft); outline-offset: 2px; }
.chtitle { flex: 1; min-width: 0; font: 500 13.5px/1.3 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trbadge { display: inline-flex; align-items: center; gap: 5px; font: 500 10.5px/1 system-ui; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); padding: 3px 7px; border-radius: 5px; flex: none; }
/* translations live in a guide-lined, tinted GROUP — nesting you can't miss */
.grp { margin-left: 19px; padding-left: 12px; border-left: 2px solid var(--line); display: flex; flex-direction: column; gap: 2px; }
.grp .chrow { background: color-mix(in srgb, var(--panel) 45%, transparent); }
.grp .chrow:hover { background: var(--panel2); }
.grp .chrow.open { background: var(--accentSoft); }
/* fixed right columns: pages(56px) then actions — buttons on one vertical */
.pgcol { flex: none; width: 56px; text-align: right; font-size: 10.5px; color: var(--good); }
.chacts { display: inline-flex; gap: 5px; flex: none; }
/* the inline entry form (LABEL · LANG · GROUP · LINK) */
.entryform { margin: 2px 0 4px 26px; padding: 11px; border: 1px solid var(--line); border-radius: 9px; background: var(--panel); display: flex; flex-direction: column; gap: 7px; }
/* the field rows render inside EntryFields (a child component) — style them
   from here via :deep so the form grammar keeps ONE CSS source */
.entryform :deep(.flabel) { flex: none; width: 46px; font: 700 8.5px/1 ui-monospace, monospace; letter-spacing: .08em; color: var(--tx3); }
.emptychapters { margin-top: 14px; padding: 24px; border: 1px dashed var(--line); border-radius: 10px; text-align: center; color: var(--tx3); font: 400 12.5px/1 system-ui; }
/* the Move-to menu (pages pane) */
.movewrap { position: relative; display: inline-flex; }
.movemenu { position: absolute; top: calc(100% + 6px); right: 0; z-index: 40; width: 250px; max-height: 300px; overflow: auto; background: var(--panel2); border: 1px solid var(--line); border-radius: 9px; box-shadow: 0 16px 40px rgba(0,0,0,.55); padding: 5px; }
.mvhead { padding: 6px 9px 3px; font: 600 10.5px/1 ui-monospace, monospace; color: var(--tx3); }
.mvrow { display: flex; align-items: center; gap: 8px; padding: 7px 9px; border-radius: 6px; cursor: pointer; font: 500 12px/1.3 system-ui; color: var(--tx2); }
.mvrow:hover { background: var(--hover); color: var(--tx); }
.mvrow.sub { padding-left: 22px; }
.mvrow.new { color: var(--accent); border-top: 1px solid var(--line); margin-top: 4px; border-radius: 0 0 6px 6px; }
.mvnum { font: 600 12px/1 ui-monospace, monospace; color: var(--tx); }
.mvpg { margin-left: auto; font-size: 9.5px; color: var(--tx3); flex: none; }
.mvform { display: flex; flex-direction: column; gap: 6px; padding: 8px; border-top: 1px solid var(--line); margin-top: 4px; }

/* the tree + edit mode + pages */
.chedit { width: 28px; height: 28px; }
.chsmall { padding: 6px 10px; font-size: 11.5px; }
.chhint { margin: 8px 0 2px; font-size: 10px; color: var(--tx3); }
.dragging { opacity: .45; }
/* the landing indicator: an accent line ABOVE the row the drag would land before */
.dropbefore { box-shadow: 0 -2px 0 var(--accent); border-top-left-radius: 0; border-top-right-radius: 0; }
.dropend { margin-top: 6px; padding: 10px; border: 1px dashed var(--line); border-radius: 8px; text-align: center; font: 600 11px/1 system-ui; color: var(--tx3); }
.dropend.hot { border-color: var(--accent); color: var(--accent); background: var(--accentSoft); }
.entryform.addform { margin: 10px 0 4px; }
.entryform.drophot { border-color: var(--accent); background: var(--accentSoft); }
.drophint { font-size: 9.5px; color: var(--tx3); }
.irow, .entryform :deep(.irow) { display: flex; align-items: center; gap: 8px; min-width: 0; }
.iin, .entryform :deep(.iin) { font: 500 12.5px/1.3 system-ui; color: var(--tx); background: var(--panel2); border: 1px solid var(--line); border-radius: 7px; padding: 8px 10px; outline: none; }
.iin:focus, .entryform :deep(.iin:focus) { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accentSoft); }
.trcount { font-size: 10px; color: var(--accent); border: 1px solid color-mix(in srgb, var(--accent) 40%, var(--line)); padding: 3px 7px; border-radius: 5px; flex: none; }
.chrow.openable { cursor: pointer; }
.chrow.open { background: var(--accentSoft); border-color: color-mix(in srgb, var(--accent) 35%, var(--line)); }
.chact { width: 24px; height: 24px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); color: var(--tx2); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; flex: none; }
.chact:hover { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, var(--line)); }
.chact.danger:hover { color: var(--adult); border-color: color-mix(in srgb, var(--adult) 45%, var(--line)); }

/* the pages pane: right column, sticky, its own scroll */
.pagespane { flex: 1; min-width: 0; position: sticky; top: 14px; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); overflow: hidden; display: flex; flex-direction: column; max-height: calc(100vh - 120px); }
.pghead { display: flex; align-items: center; gap: 10px; padding: 9px 12px; border-bottom: 1px solid var(--line); flex: none; }
.pglabel { font-size: 11px; color: var(--tx2); }
.zseg { display: inline-flex; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.zseg button { border: none; background: transparent; color: var(--tx3); font: 700 9.5px/1 ui-monospace, monospace; padding: 5px 8px; cursor: pointer; }
.zseg button + button { border-left: 1px solid var(--line); }
.zseg button.on { background: var(--accentSoft); color: var(--accent); }
.dangerbtn { color: var(--adult); border-color: color-mix(in srgb, var(--adult) 45%, var(--line)); padding: 5px 10px; font-size: 11.5px; }
.dangerbtn:hover { background: color-mix(in srgb, var(--adult) 12%, transparent); }
/* FLEX wrap, not grid: this Chromium mis-sizes auto grid rows under lazy
   images (tracks freeze at line-height and tiles paint over each other on
   resize/зум). Flex lines always grow with their items, and the tile's size is
   fully fixed (width + aspect-ratio) with the image ABSOLUTE inside — layout
   never depends on image loading, so overlap is impossible. */
.pgrid { padding: 12px; display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-start; align-content: flex-start; overflow-y: auto; min-height: 0; user-select: none; }
.ptile { position: relative; flex: none; aspect-ratio: 2/3; border-radius: 6px; overflow: hidden; border: 1px solid var(--line); background: var(--panel2); cursor: pointer; }
.ptile:not(.selectable):hover { outline: 2px solid var(--accent); outline-offset: 1px; }
.ptile img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; display: block; }
.ptile.selectable { cursor: pointer; }
.ptile.selectable:hover { outline: 2px solid var(--warn); outline-offset: 1px; }
.ptile.sel { outline: 2px solid var(--adult); outline-offset: 1px; }
.ptile.sel img { opacity: .45; }
.ptile.pdragging { opacity: .45; }
.ptile.pdrop { box-shadow: -4px 0 0 var(--accent); }
.pgend { flex: 1 1 100%; padding: 10px; border: 1px dashed var(--line); border-radius: 8px; text-align: center; font: 600 11px/1 system-ui; color: var(--tx3); }
.pgend.hot { border-color: var(--accent); color: var(--accent); background: var(--accentSoft); }
.pnum { position: absolute; bottom: 4px; right: 5px; font: 700 9px/1 ui-monospace, monospace; color: #fff; background: rgba(0,0,0,.55); padding: 2px 5px; border-radius: 4px; }
.pcheck { position: absolute; top: 5px; left: 5px; width: 18px; height: 18px; border-radius: 5px; border: 1.5px solid #fff; background: rgba(0,0,0,.45); display: inline-flex; align-items: center; justify-content: center; color: #fff; }
.pcheck.on { background: var(--adult); border-color: var(--adult); }
</style>
