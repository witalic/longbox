<script setup lang="ts">
// The capture panel IS the draft (design/state-model.md §4): a new record or an
// edit target chosen on purpose — never "whatever page I'm on". Navigation can't
// change it; auto-fill is explicit and writes only auto/empty fields.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  commitDraft, confirmDiscard, discardDraft, draftFromTitle, draftState, isDirty, newDraft,
} from '../draft'
import {
  addChapterRow, askConfirm, cache, downloads, groupRings, langRings, openTitle, pollDownloads,
  refreshTitle as refreshTitleOf, store, titleById, watchDownloads,
} from '../store'
import { browser, newTab } from '../browser'
import { api, type DownloadItem } from '../api'
import {
  chaptersInScope, compareChapterNums, coverAt, faviconFor, groupByNum, hostOf, labelChoices,
  matchTitles, nextLabel, nextLabels, sameChapter,
  type Chapter,
} from '../data'
import {
  PAGE_FILTER_DEFAULTS, pageCapture, pageFilter, resetPageFilter, savePageFilter,
  startPageCapture, stopPageCapture, type PickField,
} from '../pagecapture'
import Combo from './Combo.vue'
import FieldVisibility from './FieldVisibility.vue'
import Icon from './Icon.vue'
import MenuButton from './MenuButton.vue'
import MetadataEditor from './MetadataEditor.vue'

const props = defineProps<{
  hasPage: boolean       // a page is loaded in the active tab → capture actions work
  busy: boolean
  pageUrl: string        // the live page — pre-fills entry source links, ranks targets
  pageTitle: string
  domain: string         // whose recipe decides which fields this panel offers
  hiddenFields: string[] // ids this source does not offer (from that recipe)
  // Library records this page's title may already belong to. Set only for a
  // BRAND-NEW draft: filling one that already targets a title is an edit, and
  // an edit needs no warning.
  fillMatches: { id: string; title: string; score: number }[]
}>()
const emit = defineEmits<{
  (e: 'autofill'): void
  (e: 'capture', field: PickField): void
  // gather THIS page's values into a list field that already has some
  (e: 'merge', field: string): void
  // the decision on that warning: fill anyway, or leave the page alone
  (e: 'fillAnyway'): void
  (e: 'fillCancel'): void
  (e: 'hideField', id: string, hidden: boolean): void
}>()

// The rows this SOURCE offers, and the ones you told it to stop offering. The
// registry is shared; which of it a site is worth showing is the site's business.
const editableFields = computed(() => store.fields.filter((f) => f.editable))
const offered = computed(() =>
  editableFields.value.filter((f) => f.required || !props.hiddenFields.includes(f.id)))
const notOffered = computed(() =>
  editableFields.value.filter((f) => !f.required && props.hiddenFields.includes(f.id)))

const d = computed(() => draftState.cur)
const pickTarget = ref(false)
const flash = ref('')
let flashTimer: ReturnType<typeof setTimeout> | undefined
function showFlash(msg: string) {
  flash.value = msg
  if (flashTimer) clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { flash.value = '' }, 2600)
}
defineExpose({ showFlash })

// draft preview may be a data: URL (captured bytes) — only the stored cover
// endpoint understands the ?w= downscale
const targetCover = computed(() => coverAt((d.value?.targetId ? titleById(d.value.targetId)?.cover : '') || '', 96))
const srcIconFail = ref(false)
watch(() => d.value?.meta.source.domain, () => { srcIconFail.value = false })

// ---- the Contents tab: entries + armed downloads ----
const tab = ref<'meta' | 'downloads'>('meta')
const dLabel = ref('') // the entry's single free-text label
// The next number IS the answer nearly every time, so it is already in the box
// and the dropdown is for the exception — a bonus, a chapter out of order. A
// label the human typed is never overwritten; creating an entry ends that claim
// and the count carries on from what was just filed.
const labelTouched = ref(false)
const dLang = ref('')
const dGroup = ref('')
// SOURCE LINK pre-fills from the active page but never clobbers a hand edit
const dUrl = ref('')
const urlTouched = ref(false)
watch(() => props.pageUrl, (u) => { if (!urlTouched.value) dUrl.value = u || '' }, { immediate: true })
function onUrlInput() { urlTouched.value = dUrl.value.trim() !== '' }
// Downloads are APP state (store.ts polls them once for everyone) — the dock
// reads them and says what finished, which is the one part that belongs here.
const armed = computed(() => downloads.armed)
const told = new Set<string>()
watch(() => downloads.items.map((i) => `${i.id}:${i.state}`).join(), () => {
  for (const it of downloads.items) {
    if (it.state === 'done' && !told.has(it.id)) {
      told.add(it.id)
      showFlash(`✓ ch. ${it.num} saved`)
    } else if (it.state !== 'downloading') told.add(it.id)
  }
})
// the Contents tab is a live view of them: keep the poll warm while it is open
watch(tab, (v) => watchDownloads(v === 'downloads'), { immediate: true })
onBeforeUnmount(() => { if (tab.value === 'downloads') watchDownloads(false) })

// Stop a download, or clear one that failed. The transfer belongs to the SHELL
// (it owns the Electron item), the record belongs to the sidecar — so both are
// told, in that order, and the row is gone by the next poll.
async function stopDownload(id: string, running: boolean) {
  if (running) await window.longbox?.cancelDownload(id)
  try { await api.forgetDownload(id) } catch { /* already gone */ }
  told.add(id) // its 'failed' report is expected, and is not news
  await pollDownloads(true)
}

// An entry the human no longer wants: the row AND whatever was downloaded into
// it. The title page owns the fuller editing; the dock owns what it captured.
async function removeEntry(r: PanelRow) {
  const tid = d.value?.targetId
  if (!tid || !r.chapter) return
  const ok = await askConfirm({
    title: 'Remove entry', danger: true, okLabel: 'Remove',
    message: `Remove ${r.num}${r.lang ? ' · ' + r.lang : ''}${r.group ? ' · ' + r.group : ''}`
      + ' and its downloaded file?',
  })
  if (!ok) return
  try {
    cache([await api.deleteChapterRow(tid, r.chapter.id)])
  } catch (e) {
    // a 409 says the player still holds the file — that message IS the answer
    showFlash(e instanceof Error ? e.message : String(e))
  }
}

// Add an entry row; with `arm`, also arm the NEXT browser download into it
// (an already-existing entry just arms — re-downloading a chapter is normal).
async function addEntry(arm: boolean) {
  const cur = draftState.cur
  if (!cur?.targetId || !dLabel.value.trim()) return
  const t = titleById(cur.targetId)
  if (!t) return
  const ch = { num: dLabel.value.trim(), lang: dLang.value.trim(), group: dGroup.value.trim(), url: dUrl.value.trim() }
  const exists = t.chapters.some((c) => sameChapter(c, ch))
  if (!exists) {
    if (!(await addChapterRow(t, ch))) return
  } else if (!arm) {
    showFlash('that entry already exists')
    return
  }
  if (arm) {
    try {
      Object.assign(downloads,
        await api.armDownload({ titleId: cur.targetId, num: ch.num, lang: ch.lang, group: ch.group }))
    } catch (e) {
      store.error = String(e)
      return
    }
    // KEEP the whole form (label included): each download consumes the arm, so
    // feeding one entry file-by-file (image sets) re-arms with a single click
  } else {
    showFlash('✓ entry added')
    // batch-adding rows: line up the one after it, not an empty box
    dLabel.value = nextLabel(ch.num)
    labelTouched.value = false
  }
}
// ---- the download MODE: an archive per chapter, or the reader page itself ----
// Page capture arms ONE entry exactly like an archive download: the pages land
// in the entry named in the form, and finishing it is explicit.
const pageMode = ref(false)
function setPageMode(on: boolean) {
  pageMode.value = on
  if (!on && pageCapture.active) stopPageCapture()
}
async function startCapture() {
  const cur = draftState.cur
  const t = cur?.targetId ? titleById(cur.targetId) : undefined
  if (!t || !pageCapture.selector || !dLabel.value.trim()) return
  const ch = { num: dLabel.value.trim(), lang: dLang.value.trim(), group: dGroup.value.trim(), url: dUrl.value.trim() }
  const same = (c: Chapter) => sameChapter(c, ch)
  // re-arming an existing entry is normal — pages just continue into it
  if (!t.chapters.some(same) && !(await addChapterRow(t, ch))) return
  const row = titleById(t.id)?.chapters.find(same)
  if (!row) return
  startPageCapture({
    titleId: t.id, chapterId: row.id, label: ch.num, selector: pageCapture.selector,
    // remembered so the session keeps reading the tab and site it was armed on
    domain: hostOf(props.pageUrl), tabId: browser.activeId,
  })
}
// what counts as a page — tuned HERE, next to the selector it corrects, with
// the pick preview showing the result live
const filterOpen = ref(false)
const filterDirty = computed(() =>
  pageFilter.minPx !== PAGE_FILTER_DEFAULTS.minPx || pageFilter.widthRatio !== PAGE_FILTER_DEFAULTS.widthRatio)
const ratioPct = computed({
  get: () => Math.round(pageFilter.widthRatio * 100),
  set: (v: number) => savePageFilter({ widthRatio: (Number(v) || 0) / 100 }),
})

// finish this entry and line the next one up — the label steps forward, so a
// run of chapters is: Start → read → Finish → Start → read → …
function finishCapture() {
  const done = pageCapture.label
  const pages = pageCapture.added
  stopPageCapture()
  dLabel.value = nextLabel(done)
  labelTouched.value = false
  showFlash(pages ? `✓ ch. ${done} — ${pages} pages` : `ch. ${done} — nothing captured`)
  void refreshTitleOf(draftState.cur?.targetId || '')
}
// the capture binds to ONE title — switching the draft target must not keep
// filing pages into the previous one
watch(() => draftState.cur?.targetId, (id) => {
  if (pageCapture.active && pageCapture.titleId !== id) stopPageCapture()
})

// arm straight from an existing entry row
async function armRow(r: PanelRow) {
  const cur = draftState.cur
  if (!cur?.targetId) return
  try {
    Object.assign(downloads,
      await api.armDownload({ titleId: cur.targetId, num: r.num, lang: r.lang, group: r.group }))
  } catch (e) { store.error = String(e) }
}
async function disarm() {
  downloads.armed = null
  try { await api.disarmDownload() } catch { /* already gone */ }
}
function pct(it: { received: number; total: number }): number {
  return it.total > 0 ? Math.min(100, Math.round((it.received / it.total) * 100)) : 0
}
function mb(n: number): string {
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.round(n / 1024)} KB`
}
const targetChapters = computed(() => (d.value?.targetId ? titleById(d.value.targetId)?.chapters ?? [] : []))

// ONE chapter list: saved chapters merged with in-flight downloads (matched by
// num/lang/group get inline progress; brand-new ones appear as pending rows),
// smart-sorted by chapter number.
interface PanelRow {
  key: string
  num: string
  lang: string
  group: string
  chapter?: Chapter
  item?: DownloadItem
}
// A row is in exactly ONE state, and the icon on the left says which; the
// number on the right is the amount, not a second copy of the state.
function stateOf(r: PanelRow): 'ok' | 'run' | 'fail' | '' {
  if (r.item) return r.item.state === 'failed' ? 'fail' : 'run'
  return r.chapter?.dl ? 'ok' : ''
}
function amountOf(r: PanelRow): string {
  if (r.item) {
    if (r.item.state === 'failed') return 'failed'
    return r.item.total ? `${pct(r.item)}%` : mb(r.item.received)
  }
  if (!r.chapter?.dl) return ''
  return r.chapter.pages ? `${r.chapter.pages} pg` : 'file'
}

const panelRows = computed<PanelRow[]>(() => {
  const tid = d.value?.targetId
  const norm = (s: string) => s.trim().toLowerCase()
  const rows: PanelRow[] = targetChapters.value.map((c) => ({
    key: c.id, num: c.num, lang: c.lang, group: c.group, chapter: c,
  }))
  for (const it of downloads.items) {
    if (it.titleId !== tid || it.state === 'done') continue
    const match = rows.find((r) =>
      norm(r.num) === norm(it.num) && norm(r.lang) === norm(it.lang) && norm(r.group) === norm(it.group))
    if (match) match.item = it
    else rows.push({ key: `dl-${it.id}`, num: it.num, lang: it.lang, group: it.group, item: it })
  }
  return rows.sort((a, b) => compareChapterNums(a.num, b.num))
})
// the same label with several translations → a group (v3 row grammar)
const panelTree = computed(() => groupByNum(panelRows.value))
// "new" for an hour after download, then a short date
function freshness(iso: string): string {
  if (!iso) return ''
  const ts = Date.parse(iso)
  if (Number.isNaN(ts)) return ''
  if (Date.now() - ts < 60 * 60 * 1000) return 'new'
  const dt = new Date(ts)
  return `${String(dt.getDate()).padStart(2, '0')}.${String(dt.getMonth() + 1).padStart(2, '0')}`
}
// suggestions for the download form: every language / translator group already
// known to the library (searchable Combo), the target's own values first
// Near = this title's entries and everything captured from THIS source; the
// rest joins the search once the user types (Combo's `moreSuggestions`).
const langSuggest = computed(() => langRings(targetChapters.value, props.domain))
const groupSuggest = computed(() => groupRings(targetChapters.value, props.domain))
// the next few numbers for THIS title; a fresh draft starts at 1
// the rows this entry would join: same language, same group
const labelScope = computed(() =>
  chaptersInScope(targetChapters.value, dLang.value, dGroup.value))
const labelSuggest = computed(() => labelChoices(labelScope.value))
const labelNext = computed(() => nextLabels(labelScope.value))
// a different title — or a fresh draft — counts from its own beginning
watch(() => d.value?.targetId, () => { labelTouched.value = false })
// the NEXT one is what lands in the box; the dropdown's first row is the last
// one used, which is a choice, not a default
watch(labelNext, (next) => {
  if (!labelTouched.value) dLabel.value = next[0] ?? ''
}, { immediate: true })

// Edit-target suggestions, ranked by relevance to the LIVE page: the title
// captured from exactly this page first, then titles whose name/alt/author
// appears in the page title — each explicitly badged — then the rest; a search
// box filters the whole list.
const targetSearch = ref('')
type TargetBadge = '' | 'page' | 'match' | 'near'
const RANK: Record<TargetBadge, number> = { page: 0, match: 1, near: 2, '': 3 }
// Canonical page identity: host (no www) + decoded path — protocol, query,
// hash and trailing slashes never make "the same page" look different.
function canon(u: string): string {
  try {
    const x = new URL(u)
    return x.hostname.replace(/^www\./, '').toLowerCase()
      + decodeURIComponent(x.pathname).replace(/\/$/, '')
  } catch { return u }
}
// What you are looking at, and what you typed, are two different questions.
// With no query the list ranks by how close each record is to THIS PAGE; typing
// re-ranks by how close it is to what you typed. Either way the comparison runs
// over the title AND its alts, and scores partial hits instead of dropping them
// — the same record is filed under a romaji name as often as not.
const targetRows = computed(() => {
  const q = targetSearch.value.trim()
  const page = props.pageUrl ? canon(props.pageUrl) : ''
  const all = Object.values(store.byId)
  const scored = new Map<string, number>()
  for (const { t, score } of matchTitles(q || props.pageTitle, all, 0.34, 999)) {
    scored.set(t.id, score)
  }
  const ql = q.toLowerCase()
  return all
    .map((t) => {
      const score = scored.get(t.id) ?? 0
      let badge: TargetBadge = ''
      if (page && t.source.url && canon(t.source.url) === page) badge = 'page'
      else if (score >= 0.99) badge = 'match'
      else if (score > 0) badge = 'near'
      return { t, badge, score }
    })
    .filter(({ t, score }) => {
      if (!q) return true
      // a typed query also matches plainly: a person's name, part of a word
      return score > 0 || t.title.toLowerCase().includes(ql)
        || t.alt.toLowerCase().includes(ql) || t.authors.some((a) => a.toLowerCase().includes(ql))
    })
    .sort((a, b) => RANK[a.badge] - RANK[b.badge] || b.score - a.score
      || a.t.title.localeCompare(b.t.title))
})
const BADGE_LABEL: Record<string, string> = { page: 'THIS PAGE', match: 'SAME NAME' }
// a near hit says HOW near, because "maybe this one" is only useful with a number
const badgeText = (r: { badge: TargetBadge; score: number }) =>
  r.badge === 'near' ? `${Math.round(r.score * 100)}% MATCH` : BADGE_LABEL[r.badge] ?? ''
// a short list beats a wall: page hits + best matches first, search digs deeper
const TARGET_LIMIT = 12
const targetShown = computed(() => targetRows.value.slice(0, TARGET_LIMIT))
const targetHidden = computed(() => Math.max(0, targetRows.value.length - TARGET_LIMIT))

async function startNew() {
  if (!(await confirmDiscard())) return
  newDraft()
  pickTarget.value = false
  tab.value = 'meta' // a fresh draft has no Contents (needs a saved title) — land on Metadata
}
// What the record IS, in one line: its kind, where it was captured from, and how
// much of it is already here.
function matchLine(id: string): string {
  const t = titleById(id)
  if (!t) return 'no longer in the library'
  const n = t.chapters.length
  return [t.type, t.source.domain || 'no source', n ? `${n} ${n === 1 ? 'entry' : 'entries'}` : 'empty']
    .filter(Boolean).join(' · ')
}

// "that one, not a new one": the draft is re-seeded from the existing title and
// the page is NOT filled over it — the human came here to edit that record.
async function openInstead(id: string) {
  emit('fillCancel')
  await switchTarget(id)
}

async function switchTarget(id: string) {
  const t = titleById(id)
  if (!t) return
  if (!(await confirmDiscard())) return
  draftFromTitle(t)
  pickTarget.value = false
}
async function discard() {
  if (!(await confirmDiscard())) return
  discardDraft()
}
// What happened to the draft belongs ON the button that did it — a line of
// green text beside three buttons is read last, if at all, and in a 344px dock
// it wraps into a column.
const saved = ref<'' | 'created' | 'updated'>('')
let savedTimer: ReturnType<typeof setTimeout> | undefined
async function save(asNew: boolean) {
  const wasNew = asNew || !d.value?.targetId
  if (!(await commitDraft(asNew))) return
  saved.value = wasNew ? 'created' : 'updated'
  if (savedTimer) clearTimeout(savedTimer)
  savedTimer = setTimeout(() => { saved.value = '' }, 2600)
}
</script>

<template>
  <aside class="cp">
    <!-- no draft yet: choose one on purpose -->
    <div v-if="!d" class="nodraft">
      <div class="ndt">No draft open</div>
      <div class="nds">A draft is what you capture into — it is yours, not the page's. Start a new manga or pick a library title to edit.</div>
      <button class="btn accent" @click="startNew"><Icon name="plus" :size="14" :sw="2.2" />New manga</button>
      <div v-if="targetRows.length || targetSearch" class="ndlist scroll">
        <div class="ndlbl">EDIT EXISTING</div>
        <div class="ndsearch"><Icon name="search" :size="12" /><input v-model="targetSearch" class="ndsearchin" placeholder="Search title, author…" /></div>
        <div v-for="r in targetShown" :key="r.t.id" class="ndrow" :class="{ hit: r.badge }" @click="switchTarget(r.t.id)">
          <span class="swatch" :style="r.t.cover ? { background: `#181a1f url('${coverAt(r.t.cover, 64)}') center/cover` } : {}"></span>
          <span class="ndname">{{ r.t.title }}</span>
          <span v-if="r.badge" class="ndbadge" :class="r.badge">{{ badgeText(r) }}</span>
        </div>
        <div v-if="targetHidden" class="ndnone">+ {{ targetHidden }} more — search to narrow down.</div>
        <div v-if="!targetRows.length" class="ndnone">No titles match “{{ targetSearch }}”.</div>
      </div>
    </div>

    <template v-else>
      <!-- draft header: what am I building — clicks through to the library page -->
      <div class="bind" :class="{ newm: !d.targetId, linked: !!d.targetId }"
           :title="d.targetId ? 'Open the title page in the library' : ''"
           @click="d.targetId && openTitle(d.targetId)">
        <span class="cov" :style="(d.cover.preview || targetCover) ? { background: `#181a1f url('${(d.cover.preview || targetCover).replace(/'/g, '%27')}') center/cover no-repeat` } : {}"></span>
        <div class="txt">
          <div class="eyebrow">{{ d.targetId ? 'EDITING LIBRARY TITLE' : 'NEW MANGA' }}</div>
          <div class="mname">{{ d.targetId ? d.targetLabel : (d.meta.title || 'Untitled') }}</div>
          <div class="msub">
            <span v-if="d.meta.source.url" class="srclink" :title="`${d.meta.source.url} — open in a browser tab`"
                  @click.stop="newTab(d.meta.source.url)">
              <span class="srcinit">
                <img v-if="!srcIconFail" class="favimg" :src="faviconFor(d.meta.source.domain)" alt="" @error="srcIconFail = true" />
                <template v-else>{{ (d.meta.source.domain.slice(0, 2) || '?').toUpperCase() }}</template>
              </span>{{ d.meta.source.domain || d.meta.source.url }}
            </span>
            <span v-else>{{ d.targetId ? 'captures and edits update this record' : 'set the Title to save' }}</span>
            <span v-if="isDirty()" class="dirty">· unsaved</span>
          </div>
        </div>
        <button class="hact" title="Switch draft target" @click.stop="pickTarget = !pickTarget"><Icon name="chevron" :size="13" :sw="2" /></button>
      </div>
      <!-- starting fresh is a first-class action, not a menu item -->
      <div class="newrow" @click="startNew">
        <Icon name="plus" :size="13" :sw="2.2" /><span>New manga (fresh draft)</span>
      </div>
      <div v-if="pickTarget" class="targetmenu scroll">
        <div class="ndsearch"><Icon name="search" :size="12" /><input v-model="targetSearch" class="ndsearchin" placeholder="Search title, author…" /></div>
        <div v-for="r in targetShown" :key="r.t.id" class="ndrow" :class="{ hit: r.badge }" @click="switchTarget(r.t.id)">
          <span class="swatch" :style="r.t.cover ? { background: `#181a1f url('${coverAt(r.t.cover, 64)}') center/cover` } : {}"></span>
          <span class="ndname">{{ r.t.title }}</span>
          <span v-if="r.badge" class="ndbadge" :class="r.badge">{{ BADGE_LABEL[r.badge] }}</span>
        </div>
        <div v-if="targetHidden" class="ndnone">+ {{ targetHidden }} more — search to narrow down.</div>
      </div>

      <!-- A new draft over a page the library may already hold. Auto-fill would
           quietly become a SECOND record of the same work, and the duplicate is
           only obvious weeks later — so the choice is put here, before it. -->
      <div v-if="props.fillMatches.length" class="guard">
        <div class="gtitle">Already in your library?</div>
        <div class="ghint">
          This page looks like {{ props.fillMatches.length === 1 ? 'a title' : 'titles' }} you
          already have. Open one to capture INTO it, or fill this new draft anyway.
        </div>
        <button v-for="m in props.fillMatches" :key="m.id" class="grow" @click="openInstead(m.id)">
          <span class="swatch"
                :style="titleById(m.id)?.cover
                  ? { background: `#181a1f url('${coverAt(titleById(m.id)!.cover, 64)}') center/cover` }
                  : {}"></span>
          <span class="gmain">
            <span class="ndname">{{ titleById(m.id)?.title || m.title }}</span>
            <!-- a name alone cannot answer "is this the same work": the manga and
                 its anime share it, and so do two rips from different sites -->
            <span class="gmeta">{{ matchLine(m.id) }}</span>
          </span>
          <span class="ndbadge" :class="m.score >= 0.99 ? 'match' : 'near'">
            {{ m.score >= 0.99 ? 'SAME NAME' : `${Math.round(m.score * 100)}%` }}
          </span>
        </button>
        <div class="gacts">
          <button class="btn ghost chsmall" @click="emit('fillCancel')">Leave it</button>
          <button class="btn accent chsmall" @click="emit('fillAnyway')">Fill anyway</button>
        </div>
      </div>

      <!-- two workstreams: metadata capture and the contents (entries + downloads) -->
      <div class="ptabs">
        <button :class="{ on: tab === 'meta' }" @click="tab = 'meta'">Metadata</button>
        <button :class="{ on: tab === 'downloads' }" @click="tab = 'downloads'">
          Contents<span v-if="armed" class="armdot"></span>
        </button>
      </div>

      <!-- ===== CONTENTS: entries · armed downloads (mockup v3, no list capture) ===== -->
      <div v-if="tab === 'downloads'" class="dlpane scroll">
        <div v-if="!d.targetId" class="afnote">
          Entries and downloads belong to a saved title — press Create below first.
        </div>
        <template v-else>
        <!-- HOW this source gives its content: a file per chapter, or the
             reader page itself (design/state-model.md §9) -->
        <div class="seg modeseg">
          <button class="opt" :class="{ on: !pageMode }" title="The site serves a downloadable archive per chapter" @click="setPageMode(false)">Archive</button>
          <button class="opt" :class="{ on: pageMode }" title="The site only shows pages — capture them while you read" @click="setPageMode(true)">Pages</button>
        </div>

        <div v-if="pageMode && pageCapture.active" class="armedbox">
          <div class="armtitle"><Icon name="download" :size="14" />Capturing into ch. {{ pageCapture.label }}</div>
          <div class="armmeta mono">{{ pageCapture.status || 'open the first page…' }}</div>
          <div v-if="pageCapture.error" class="dlfail" style="font-size:11px">{{ pageCapture.error }}</div>
          <div class="armnote">Read through the chapter — every page you open is added. Pages already
            stored are never fetched again, so going back costs nothing.</div>
          <div class="dlrow2">
            <button class="btn ghost" @click="stopPageCapture">Cancel</button>
            <button class="btn accent" title="Close this chapter and line the next one up" @click="finishCapture">
              Finish chapter
            </button>
          </div>
        </div>

        <div v-else-if="pageMode" class="dlform">
          <div class="dllbl">CAPTURE INTO A NEW ENTRY</div>
          <div class="pcrow">
            <span class="dllbl" style="flex:none;width:78px">PAGES</span>
            <span class="pcsel mono" :class="{ unset: !pageCapture.selector }">{{ pageCapture.selector || 'not taught — pick a page image' }}</span>
            <button class="btn ghost pcpick" :disabled="!props.hasPage" title="Click one page image on the site; every image the selector matches becomes a page" @click="emit('capture', 'pages')">Pick</button>
          </div>
          <!-- the junk filter lives next to the selector it corrects: icons and
               ads share the pages' containers, so size is what tells them apart -->
          <div class="pcfilter">
            <button class="pcftog" @click="filterOpen = !filterOpen">
              <Icon name="chevron" :size="10" :sw="2.4" :style="{ transform: filterOpen ? '' : 'rotate(-90deg)' }" />
              <span>What counts as a page</span>
              <span class="mono pcfsum">≥{{ pageFilter.minPx }}px · ≥{{ ratioPct }}%</span>
            </button>
            <div v-if="filterOpen" class="pcfbody">
              <div class="pcfrow">
                <span class="pcflbl">Minimum size</span>
                <input class="pcfin mono" type="number" min="0" max="2000" step="10" :value="pageFilter.minPx"
                       @change="savePageFilter({ minPx: Number(($event.target as HTMLInputElement).value) })" />
                <span class="pcfunit mono">px</span>
              </div>
              <div class="pcfrow">
                <span class="pcflbl">Min width share</span>
                <input v-model.number="ratioPct" class="pcfin mono" type="number" min="0" max="100" step="5" />
                <span class="pcfunit mono">%</span>
              </div>
              <div class="pcfnote">Smaller than the first rule in either dimension, or narrower than that
                share of the widest match on the same view, and it is furniture. Loosen them if real pages
                are skipped; the pick preview always shows the filtered result.</div>
              <button v-if="filterDirty" class="btn ghost pcpick" style="align-self:flex-start" @click="resetPageFilter()">Reset</button>
            </div>
          </div>
          <div class="dlrow">
            <label class="dllbl">LABEL *</label>
            <Combo :model-value="dLabel" :suggestions="labelSuggest" wide
                   placeholder="5 / 2024 Artworks / Extra"
                   @update:model-value="dLabel = $event; labelTouched = true" />
          </div>
          <div class="dlrow2">
            <div class="dlrow" style="flex:1;min-width:0">
              <label class="dllbl">LANGUAGE</label>
              <Combo :model-value="dLang" :suggestions="langSuggest.near" :more-suggestions="langSuggest.all" wide placeholder="EN"
                     @update:model-value="dLang = $event" />
            </div>
            <div class="dlrow" style="flex:1.4;min-width:0">
              <label class="dllbl">GROUP / SOURCE NAME</label>
              <Combo :model-value="dGroup" :suggestions="groupSuggest.near" :more-suggestions="groupSuggest.all" wide placeholder="translator / site"
                     @update:model-value="dGroup = $event" />
            </div>
          </div>
          <div class="dlrow">
            <label class="dllbl">SOURCE LINK · from this page</label>
            <input v-model="dUrl" class="dlin mono" style="font-size:11px" placeholder="https://…" @input="onUrlInput" />
          </div>
          <div class="dlrow2" style="justify-content:flex-end">
            <button class="btn accent" :disabled="!dLabel.trim() || !pageCapture.selector"
                    :title="pageCapture.selector ? 'Create the entry and capture every page you open into it' : 'Teach the page images first'"
                    @click="startCapture">
              <Icon name="download" :size="13" :sw="2" />Start page capture
            </button>
          </div>
        </div>

        <div v-else-if="armed" class="armedbox">
          <div class="armtitle"><Icon name="download" :size="14" />Next download is armed</div>
          <div class="armmeta mono">{{ armed.num }}<template v-if="armed.lang"> · {{ armed.lang }}</template><template v-if="armed.group"> · {{ armed.group }}</template></div>
          <div class="armnote">Now click the SITE'S own download button in this browser — the file lands in this entry, with its download source recorded. One arm = one download.</div>
          <button class="btn ghost" style="align-self:flex-start" @click="disarm">Cancel</button>
        </div>
        <div v-else-if="!pageMode" class="dlform">
          <div class="dllbl">ADD ENTRY</div>
          <div class="dlrow">
            <label class="dllbl">LABEL *</label>
            <Combo :model-value="dLabel" :suggestions="labelSuggest" wide
                   placeholder="5 / 2024 Artworks / Extra"
                   @update:model-value="dLabel = $event; labelTouched = true" />
          </div>
          <div class="dlrow2">
            <div class="dlrow" style="flex:1;min-width:0">
              <label class="dllbl">LANGUAGE</label>
              <Combo :model-value="dLang" :suggestions="langSuggest.near" :more-suggestions="langSuggest.all" wide placeholder="EN"
                     @update:model-value="dLang = $event" />
            </div>
            <div class="dlrow" style="flex:1.4;min-width:0">
              <label class="dllbl">GROUP / SOURCE NAME</label>
              <Combo :model-value="dGroup" :suggestions="groupSuggest.near" :more-suggestions="groupSuggest.all" wide placeholder="translator / site"
                     @update:model-value="dGroup = $event" />
            </div>
          </div>
          <div class="dlrow">
            <label class="dllbl">SOURCE LINK · from this page</label>
            <input v-model="dUrl" class="dlin mono" style="font-size:11px" placeholder="https://…" @input="onUrlInput" />
          </div>
          <div class="dlrow2" style="justify-content:flex-end">
            <button class="btn ghost" :disabled="!dLabel.trim() || props.busy" title="Create the entry row without arming a download" @click="addEntry(false)">Add row only</button>
            <button class="btn accent" :disabled="!dLabel.trim() || props.busy" title="Create the entry AND arm the next browser download into it" @click="addEntry(true)">
              <Icon name="download" :size="13" :sw="2" />Add + arm download
            </button>
          </div>
        </div>

        <!-- entries + live downloads merged; the SAME v3 grammar as the title
             page, minus reading state (the dock manages files, not reading) -->
        <div class="dllist">
          <div class="dllbl" style="margin-bottom:6px">ENTRIES · {{ targetChapters.length }}</div>
          <div v-if="!panelRows.length" class="afnote">No entries yet.</div>
          <!-- One row per entry: state on the LEFT, one action on the right, and
               the two columns between them never move. A version row fills the
               label column with its language instead of leaving it empty. -->
          <template v-for="g in panelTree" :key="g.num">
            <div v-if="g.rows.length > 1" class="ghead">
              <span class="est"></span>
              <span class="elabel">{{ g.num }}</span>
              <span class="ettl">{{ g.rows.find((r) => r.chapter?.title)?.chapter?.title || '' }}</span>
              <span class="ecount mono">{{ g.rows.length }} versions</span>
            </div>
            <div :class="{ vers: g.rows.length > 1 }">
              <template v-for="r in g.rows" :key="r.key">
                <div class="erow" :class="{ live: r.item?.state === 'downloading' }">
                  <span class="est" :class="stateOf(r)" :title="r.item?.error || ''">
                    <Icon v-if="stateOf(r) === 'ok'" name="check" :size="12" :sw="2.6" />
                    <Icon v-else-if="stateOf(r) === 'run'" name="download" :size="11" :sw="2.4" />
                    <Icon v-else-if="stateOf(r) === 'fail'" name="x" :size="11" :sw="2.6" />
                  </span>
                  <span class="elabel" :class="{ fresh: r.chapter?.dl && freshness(r.chapter.dlAt) === 'new' }"
                        :title="r.chapter?.dl && freshness(r.chapter.dlAt) === 'new' ? 'downloaded just now' : ''">
                    {{ g.rows.length > 1 ? (r.lang || '?') : r.num }}
                  </span>
                  <span class="ettl">
                    {{ g.rows.length > 1 ? (r.group || '') : (r.chapter?.title || '') }}
                  </span>
                  <span v-if="g.rows.length === 1 && r.lang" class="elang mono">{{ r.lang }}</span>
                  <span class="ecnt mono" :class="{ run: r.item?.state === 'downloading' }">{{ amountOf(r) }}</span>
                  <span class="eacts">
                    <button v-if="r.chapter?.dl && r.chapter.dlSource" class="eact"
                            :title="`From ${hostOf(r.chapter.dlSource)} — open it in a tab`"
                            @click="newTab(r.chapter!.dlSource)">
                      <Icon name="forward" :size="11" :sw="2" />
                    </button>
                    <button v-if="!r.item" class="eact"
                            :title="`Arm the next download into ${r.num}${r.lang ? ' · ' + r.lang : ''}`"
                            @click="armRow(r)">
                      <Icon name="download" :size="11" :sw="2" />
                    </button>
                    <!-- one X, two jobs: stop what is running, clear what failed -->
                    <button v-if="r.item" class="eact danger"
                            :title="r.item.state === 'failed' ? 'Clear this failed download'
                              : 'Stop this download'"
                            @click="stopDownload(r.item.id, r.item.state !== 'failed')">
                      <Icon name="x" :size="11" :sw="2.4" />
                    </button>
                    <button v-else-if="r.chapter" class="eact danger"
                            title="Remove this entry and its downloaded file"
                            @click="removeEntry(r)">
                      <Icon name="x" :size="11" :sw="2.4" />
                    </button>
                  </span>
                </div>
              </template>
            </div>
          </template>
        </div>
        </template>
      </div>

      <!-- ===== METADATA ===== -->
      <template v-if="tab === 'meta'">
      <!-- the one-button snapshot: recipe + page metadata, fills auto/empty only -->
      <div class="autofill">
        <div class="afrow">
          <button class="btn accent" style="flex:1" :disabled="!props.hasPage || props.busy" @click="emit('autofill')">
            <Icon name="refresh" :size="13" :sw="2" />{{ props.busy ? 'Reading page…' : 'Auto-fill from this page' }}
          </button>
          <MenuButton icon="settings" :title="`Which fields ${props.domain || 'this source'} offers`" :width="290">
            <FieldVisibility :title="`FIELDS · ${(props.domain || 'this source').toUpperCase()}`"
                             :fields="offered" :hidden="notOffered"
                             note="This source's setting — a site that never shows a studio should not show the row. What YOU hide on title pages is a separate list."
                             @set="(id, h) => emit('hideField', id, h)" />
          </MenuButton>
        </div>
        <div class="afnote">Runs the site's recipe + page metadata. Fills only empty or auto fields — your manual edits are untouchable.</div>
      </div>

      <!-- the shared editor, capture-enabled -->
      <div class="body scroll">
        <MetadataEditor capture :hidden="props.hiddenFields"
                        @capture="emit('capture', $event)" @merge="emit('merge', $event)" />
      </div>
      </template>

      <!-- explicit commit — on BOTH tabs (captured chapter rows commit too) -->
      <div class="foot">
        <div v-if="flash" class="notice" :class="{ plain: !flash.startsWith('✓') }">{{ flash }}</div>
        <div class="footrow">
        <div style="flex:1"></div>
        <button class="btn ghost" @click="discard">Discard</button>
        <button v-if="d.targetId" class="btn ghost" :disabled="d.saving || !d.meta.title.trim()" title="Create a separate new title from this draft" @click="save(true)">Save as new</button>
        <button class="btn accent" :class="{ done: saved }"
                :disabled="d.saving || !d.meta.title.trim()" @click="save(false)">
          <Icon name="check" :size="13" :sw="2.2" />{{ d.saving ? 'Saving…'
            : saved === 'created' ? 'Created' : saved === 'updated' ? 'Saved'
            : (d.targetId ? 'Save' : 'Create') }}
        </button>
        </div>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.cp { width: 344px; flex: none; border-left: 1px solid var(--line); background: var(--bg2); display: flex; flex-direction: column; min-height: 0; }

.nodraft { padding: 22px 16px; display: flex; flex-direction: column; gap: 10px; align-items: flex-start; min-height: 0; }
.ndt { font: 600 14px/1 system-ui; color: var(--tx); }
.nds { font: 400 11.5px/1.5 system-ui; color: var(--tx3); }
.ndlist { align-self: stretch; margin-top: 8px; overflow: auto; min-height: 0; border-top: 1px solid var(--line); padding-top: 10px; }
.ndlbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); margin-bottom: 7px; }
.ndrow { display: flex; align-items: center; gap: 9px; padding: 6px 6px; border-radius: 7px; cursor: pointer; }
.ndrow:hover { background: var(--hover); }
.ndrow.hit { background: var(--panel); border: 1px solid var(--line); }
.ndrow.hit:hover { background: var(--hover); }
.ndbadge { margin-left: auto; flex: none; font: 700 8px/1 ui-monospace, monospace; letter-spacing: .08em; padding: 3px 6px; border-radius: 4px; }
.ndbadge.page { color: var(--good); border: 1px solid color-mix(in srgb, var(--good) 45%, var(--line)); }
.ndbadge.match { color: var(--accent); background: var(--accentSoft); }
.ndbadge.near { color: var(--tx2); border: 1px solid var(--line); }
.guard { margin: 10px 12px 0; padding: 10px; border-radius: 9px; background: color-mix(in srgb, var(--warn) 10%, transparent); border: 1px solid color-mix(in srgb, var(--warn) 40%, var(--line)); }
.gtitle { font: 600 12.5px/1 system-ui; color: var(--tx); }
.ghint { margin-top: 5px; font: 400 11px/1.45 system-ui; color: var(--tx2); }
.grow { width: 100%; margin-top: 6px; display: flex; align-items: center; gap: 8px; padding: 7px 8px; border: 1px solid var(--line); border-radius: 7px; background: var(--panel); color: var(--tx); cursor: pointer; text-align: left; }
.gmain { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.gmeta { font: 400 10px/1.2 system-ui; color: var(--tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.grow:hover { border-color: var(--accent); }
.gacts { margin-top: 9px; display: flex; gap: 7px; justify-content: flex-end; }
.ndsearch { display: flex; align-items: center; gap: 7px; padding: 6px 8px; margin: 2px 0 6px; border: 1px solid var(--line); border-radius: 7px; background: var(--panel); color: var(--tx3); }
.ndsearchin { border: none; background: transparent; outline: none; color: var(--tx); font: 400 11.5px/1 system-ui; width: 100%; }
.ndnone { padding: 8px 6px; font: 400 11px/1.4 system-ui; color: var(--tx3); }
.swatch { width: 20px; height: 28px; border-radius: 4px; background: var(--panel2); border: 1px solid var(--line); flex: none; display: inline-flex; align-items: center; justify-content: center; color: var(--tx3); }
.ndname { font: 500 12px/1.3 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.bind { display: flex; gap: 11px; align-items: center; padding: 13px 14px; border-bottom: 1px solid var(--line); }
.bind .cov { width: 40px; height: 56px; border-radius: 5px; background: var(--panel2); border: 1px solid var(--line); flex: none; }
.bind .txt { min-width: 0; flex: 1; }
.eyebrow { font: 700 8.5px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--warn); }
.bind.newm .eyebrow { color: var(--good); }
.mname { margin-top: 4px; font: 600 13.5px/1.2 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.msub { margin-top: 5px; font: 400 10.5px/1.3 system-ui; color: var(--tx3); display: flex; align-items: center; gap: 5px; min-width: 0; }
.srclink { display: inline-flex; align-items: center; gap: 5px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--tx2); cursor: pointer; }
.srclink:hover { color: var(--accent); }
.dirty { color: var(--warn); font-weight: 600; }
.hact { border: none; background: transparent; color: var(--tx3); cursor: pointer; padding: 4px; border-radius: 5px; }
.hact:hover { background: var(--hover); color: var(--tx); }
.targetmenu { max-height: 220px; overflow: auto; border-bottom: 1px solid var(--line); padding: 8px; background: var(--panel); }
.newrow { display: flex; align-items: center; gap: 9px; padding: 9px 14px; border-bottom: 1px solid var(--line); font: 600 12px/1 system-ui; color: var(--accent); cursor: pointer; }
.newrow:hover { background: var(--accentSoft); }
.srcinit { width: 18px; height: 18px; border-radius: 4px; display: inline-flex; align-items: center; justify-content: center; font: 600 8px/1 system-ui; color: #fff; background: var(--panel2); border: 1px solid var(--line); overflow: hidden; flex: none; }
.favimg { width: 12px; height: 12px; object-fit: contain; }

.ptabs { display: flex; border-bottom: 1px solid var(--line); }
.ptabs button { flex: 1; position: relative; border: none; background: transparent; color: var(--tx3); font: 600 11.5px/1 system-ui; padding: 10px 0; cursor: pointer; border-bottom: 2px solid transparent; }
.ptabs button:hover { color: var(--tx); }
.ptabs button.on { color: var(--accent); border-bottom-color: var(--accent); }
.armdot { position: absolute; margin-left: 5px; margin-top: -2px; width: 6px; height: 6px; border-radius: 50%; background: var(--good); display: inline-block; }

.dlpane { flex: 1; min-height: 0; overflow: auto; padding: 14px; display: flex; flex-direction: column; gap: 16px; }
.armedbox { display: flex; flex-direction: column; gap: 9px; padding: 12px; border: 1px solid color-mix(in srgb, var(--good) 45%, var(--line)); background: color-mix(in srgb, var(--good) 8%, transparent); border-radius: 9px; }
.armtitle { display: flex; align-items: center; gap: 8px; font: 600 12.5px/1 system-ui; color: var(--good); }
.armmeta { font-size: 11px; color: var(--tx); }
.armnote { font: 400 11px/1.5 system-ui; color: var(--tx2); }
.dlform { display: flex; flex-direction: column; gap: 10px; }
/* the download-mode switch, and the two taught selectors under it */
.modeseg { width: 100%; }
.modeseg .opt { flex: 1; }
.pcrow { display: flex; align-items: center; gap: 8px; min-width: 0; }
.pcsel { flex: 1; min-width: 0; font-size: 10.5px; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pcsel.unset { color: var(--tx3); font-style: italic; }
.pcpick { height: 26px; padding: 0 10px; font-size: 11px; flex: none; }
.pcfilter { display: flex; flex-direction: column; }
.pcftog { display: flex; align-items: center; gap: 6px; padding: 5px 4px; border: none; background: transparent; color: var(--tx3); font: 500 11px/1 system-ui; cursor: pointer; border-radius: 6px; }
.pcftog:hover { background: var(--hover); color: var(--tx); }
.pcfsum { margin-left: auto; font-size: 10px; color: var(--tx3); }
.pcfbody { display: flex; flex-direction: column; gap: 7px; padding: 8px 4px 2px; }
.pcfrow { display: flex; align-items: center; gap: 8px; }
.pcflbl { flex: 1; font: 500 11.5px/1.3 system-ui; color: var(--tx2); }
.pcfin { width: 66px; text-align: right; font-size: 12px; color: var(--tx); background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; height: 26px; padding: 0 7px; outline: none; flex: none; }
.pcfin:focus { border-color: var(--accent); }
.pcfunit { width: 14px; font-size: 10px; color: var(--tx3); flex: none; }
.pcfnote { font: 400 10.5px/1.45 system-ui; color: var(--tx3); }
.dlrow { display: flex; flex-direction: column; gap: 5px; }
.dlrow2 { display: flex; gap: 8px; }
.ghead { display: flex; align-items: center; gap: 8px; height: 26px; padding: 0 8px; color: var(--tx3); }
.vers { margin-left: 15px; padding-left: 7px; border-left: 1px solid var(--line); display: flex; flex-direction: column; gap: 1px; }
.erow { display: flex; align-items: center; gap: 8px; height: 32px; padding: 0 6px 0 8px; border-radius: 6px; color: var(--tx2); }
.erow:hover { background: var(--hover); }
.erow.live { background: color-mix(in srgb, var(--good) 7%, transparent); box-shadow: inset 2px 0 0 var(--good); }
.est { width: 14px; flex: none; display: inline-flex; align-items: center; justify-content: center; color: var(--tx3); }
.est.ok { color: var(--good); }
.est.run { color: var(--accent); }
.est.fail { color: var(--adult); }
.elabel { flex: none; max-width: 118px; font: 600 11.5px/1 ui-monospace, monospace; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.elabel.fresh { color: var(--accent); }
.ettl { flex: 1; min-width: 0; font: 500 11.5px/1.3 system-ui; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.elang { flex: none; font-size: 10px; color: var(--tx3); }
.ecnt { flex: none; width: 44px; text-align: right; font-size: 10px; color: var(--tx3); }
.ecnt.run { color: var(--accent); }
.eacts { display: flex; gap: 4px; flex: none; justify-content: flex-end; }
.eact { width: 24px; height: 24px; flex: none; border: 1px solid transparent; background: transparent; border-radius: 6px; color: var(--tx3); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
.erow:hover .eact { border-color: var(--line); }
.eact.danger:hover { color: var(--adult); border-color: color-mix(in srgb, var(--adult) 45%, var(--line)); }
.eact:hover { border-color: var(--accent); color: var(--accent); }
.ecount { font-size: 10px; color: var(--tx3); flex: none; }
.dllbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); }
.dlin { font: 500 12.5px/1.3 system-ui; color: var(--tx); background: var(--panel); border: 1px solid var(--line); border-radius: 7px; padding: 8px 10px; outline: none; width: 100%; }
.dlin:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accentSoft); }
.dllist { border-top: 1px solid var(--line); padding-top: 12px; }
.dlfail { font-size: 10px; color: var(--adult); flex: none; }
.bind.linked { cursor: pointer; }
.bind.linked:hover { background: var(--hover); }
@keyframes dlslide { from { margin-left: -35%; } to { margin-left: 100%; } }

.afrow { display: flex; align-items: center; gap: 8px; }
.afrow { display: flex; align-items: center; gap: 8px; }
.autofill { padding: 12px 14px; border-bottom: 1px solid var(--line); }
.wide { width: 100%; justify-content: center; }
.afnote { margin-top: 7px; font: 400 10.5px/1.45 system-ui; color: var(--tx3); }

.body { flex: 1; min-height: 0; overflow: auto; padding: 14px; }

/* A notice never competes with the buttons for the row: it is its own strip
   above them, full width, free to wrap. In a 344px dock the old inline span
   folded into a column and pushed Discard/Save off their line. */
.foot { display: flex; flex-direction: column; gap: 9px; padding: 12px 14px; border-top: 1px solid var(--line); }
.footrow { display: flex; align-items: center; gap: 7px; }
.notice {
  padding: 7px 10px; border-radius: 7px; font: 500 11px/1.45 system-ui;
  color: var(--good); background: color-mix(in srgb, var(--good) 11%, transparent);
  border: 1px solid color-mix(in srgb, var(--good) 34%, var(--line));
}
.notice.plain { color: var(--tx2); background: var(--panel2); border-color: var(--line); }
.btn.done { background: var(--good); border-color: var(--good); color: var(--bg); }
</style>
