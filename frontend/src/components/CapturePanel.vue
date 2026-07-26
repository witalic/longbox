<script setup lang="ts">
// The capture panel IS the draft (design/state-model.md §4): a new record or an
// edit target chosen on purpose — never "whatever page I'm on". Navigation can't
// change it; auto-fill is explicit and writes only auto/empty fields.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  commitDraft, confirmDiscard, discardDraft, draftFromTitle, draftState, isDirty, newDraft,
} from '../draft'
import { addChapterRow, groupSuggestions, langSuggestions, openTitle, refreshTitle, store, titleById } from '../store'
import { newTab } from '../browser'
import { api, type DownloadItem, type DownloadsState } from '../api'
import { compareChapterNums, coverAt, faviconFor, groupByNum, type Chapter } from '../data'
import type { EditableField } from '../draft'
import Combo from './Combo.vue'
import Icon from './Icon.vue'
import MetadataEditor from './MetadataEditor.vue'

const props = defineProps<{
  hasPage: boolean       // a page is loaded in the active tab → capture actions work
  busy: boolean
  pageUrl: string        // the live page — pre-fills entry source links, ranks targets
  pageTitle: string
}>()
const emit = defineEmits<{
  (e: 'autofill'): void
  (e: 'capture', field: EditableField): void
}>()

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
const dLang = ref('')
const dGroup = ref('')
// SOURCE LINK pre-fills from the active page but never clobbers a hand edit
const dUrl = ref('')
const urlTouched = ref(false)
watch(() => props.pageUrl, (u) => { if (!urlTouched.value) dUrl.value = u || '' }, { immediate: true })
function onUrlInput() { urlTouched.value = dUrl.value.trim() !== '' }
const dl = ref<DownloadsState>({ armed: null, items: [] })
const armed = computed(() => dl.value.armed)
const seenDone = new Set<string>() // items already reported as finished
let pollTimer: number | undefined

// Poll ONLY while there is something to watch: the Downloads tab is open, an
// arm is pending, or a download is still streaming. Idle panel = zero requests.
function shouldPoll(): boolean {
  return tab.value === 'downloads'
    || dl.value.armed !== null
    || dl.value.items.some((i) => i.state === 'downloading')
}
async function pollDownloads() {
  if (!shouldPoll()) return
  try {
    const s = await api.downloadsState()
    for (const it of s.items) {
      if (it.state === 'done' && !seenDone.has(it.id)) {
        seenDone.add(it.id)
        showFlash(`✓ ch. ${it.num} saved`)
        void refreshTitle(it.titleId)
      } else if (it.state === 'failed') {
        seenDone.add(it.id)
      }
    }
    dl.value = s
  } catch { /* sidecar unreachable — keep the current state */ }
}
onMounted(() => { pollTimer = window.setInterval(pollDownloads, 1000) })
onBeforeUnmount(() => window.clearInterval(pollTimer))
watch(tab, (v) => { if (v === 'downloads') void pollDownloads() })

// Add an entry row; with `arm`, also arm the NEXT browser download into it
// (an already-existing entry just arms — re-downloading a chapter is normal).
async function addEntry(arm: boolean) {
  const cur = draftState.cur
  if (!cur?.targetId || !dLabel.value.trim()) return
  const t = titleById(cur.targetId)
  if (!t) return
  const norm = (s: string) => s.trim().toLowerCase()
  const ch = { num: dLabel.value.trim(), lang: dLang.value.trim(), group: dGroup.value.trim(), url: dUrl.value.trim() }
  const exists = t.chapters.some((c) =>
    norm(c.num) === norm(ch.num) && norm(c.lang) === norm(ch.lang) && norm(c.group) === norm(ch.group))
  if (!exists) {
    if (!(await addChapterRow(t, ch))) return
  } else if (!arm) {
    showFlash('that entry already exists')
    return
  }
  if (arm) {
    try {
      dl.value = await api.armDownload({ titleId: cur.targetId, num: ch.num, lang: ch.lang, group: ch.group })
    } catch (e) {
      store.error = String(e)
      return
    }
  } else {
    showFlash('✓ entry added')
  }
  dLabel.value = ''
}
// arm straight from an existing entry row
async function armRow(r: PanelRow) {
  const cur = draftState.cur
  if (!cur?.targetId) return
  try {
    dl.value = await api.armDownload({ titleId: cur.targetId, num: r.num, lang: r.lang, group: r.group })
  } catch (e) { store.error = String(e) }
}
async function disarm() {
  dl.value = { ...dl.value, armed: null }
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
const panelRows = computed<PanelRow[]>(() => {
  const tid = d.value?.targetId
  const norm = (s: string) => s.trim().toLowerCase()
  const rows: PanelRow[] = targetChapters.value.map((c) => ({
    key: c.id, num: c.num, lang: c.lang, group: c.group, chapter: c,
  }))
  for (const it of dl.value.items) {
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
function hostOf(u: string): string {
  try { return new URL(u).hostname.replace(/^www\./, '') } catch { return u }
}
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
const langSuggest = computed(() => langSuggestions(targetChapters.value))
const groupSuggest = computed(() => groupSuggestions(targetChapters.value))

// Edit-target suggestions, ranked by relevance to the LIVE page: the title
// captured from exactly this page first, then titles whose name/alt/author
// appears in the page title — each explicitly badged — then the rest; a search
// box filters the whole list.
const targetSearch = ref('')
type TargetBadge = '' | 'page' | 'match'
// Canonical page identity: host (no www) + decoded path — protocol, query,
// hash and trailing slashes never make "the same page" look different.
function canon(u: string): string {
  try {
    const x = new URL(u)
    return x.hostname.replace(/^www\./, '').toLowerCase()
      + decodeURIComponent(x.pathname).replace(/\/$/, '')
  } catch { return u }
}
const targetRows = computed(() => {
  const q = targetSearch.value.trim().toLowerCase()
  const page = props.pageUrl ? canon(props.pageUrl) : ''
  const ptitle = props.pageTitle.toLowerCase()
  const rank: Record<TargetBadge, number> = { page: 0, match: 1, '': 2 }
  return Object.values(store.byId)
    .map((t) => {
      let badge: TargetBadge = ''
      if (page && t.source.url && canon(t.source.url) === page) badge = 'page'
      else if (ptitle && (
        (t.title && ptitle.includes(t.title.toLowerCase()))
        || (t.alt && ptitle.includes(t.alt.toLowerCase()))
        || t.authors.some((a) => a && ptitle.includes(a.toLowerCase()))
      )) badge = 'match'
      return { t, badge }
    })
    .filter(({ t }) => !q || t.title.toLowerCase().includes(q)
      || t.alt.toLowerCase().includes(q) || t.authors.some((a) => a.toLowerCase().includes(q)))
    .sort((a, b) => rank[a.badge] - rank[b.badge] || a.t.title.localeCompare(b.t.title))
})
const BADGE_LABEL: Record<string, string> = { page: 'THIS PAGE', match: 'ON PAGE' }
// a short list beats a wall: page hits + best matches first, search digs deeper
const TARGET_LIMIT = 12
const targetShown = computed(() => targetRows.value.slice(0, TARGET_LIMIT))
const targetHidden = computed(() => Math.max(0, targetRows.value.length - TARGET_LIMIT))

async function startNew() {
  if (!(await confirmDiscard())) return
  newDraft()
  pickTarget.value = false
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
async function save(asNew: boolean) {
  const wasNew = asNew || !d.value?.targetId
  const saved = await commitDraft(asNew)
  if (saved) showFlash(wasNew ? '✓ Created — saved to the library' : '✓ Saved')
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
          <span v-if="r.badge" class="ndbadge" :class="r.badge">{{ BADGE_LABEL[r.badge] }}</span>
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
          <div class="msub">{{ d.targetId ? 'captures and edits update this record' : 'set the Title to save' }}<span v-if="isDirty()" class="dirty"> · unsaved</span></div>
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

      <!-- the source this draft is bound to — a proper tile, like the title page -->
      <div v-if="d.meta.source.url" class="srcbar">
        <span class="srclbl">SOURCE</span>
        <span class="srcchip" :title="`${d.meta.source.url} — open in a browser tab`" @click="newTab(d.meta.source.url)">
          <span class="srcinit">
            <img v-if="!srcIconFail" class="favimg" :src="faviconFor(d.meta.source.domain)" alt="" @error="srcIconFail = true" />
            <template v-else>{{ (d.meta.source.domain.slice(0, 2) || '?').toUpperCase() }}</template>
          </span>{{ d.meta.source.domain || d.meta.source.url }}
          <Icon name="forward" :size="10" :sw="2.2" style="color:var(--tx3)" />
        </span>
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
        <div v-if="armed" class="armedbox">
          <div class="armtitle"><Icon name="download" :size="14" />Next download is armed</div>
          <div class="armmeta mono">{{ armed.num }}<template v-if="armed.lang"> · {{ armed.lang }}</template><template v-if="armed.group"> · {{ armed.group }}</template></div>
          <div class="armnote">Now click the SITE'S own download button in this browser — the file lands in this entry, with its download source recorded. One arm = one download.</div>
          <button class="btn ghost" style="align-self:flex-start" @click="disarm">Cancel</button>
        </div>
        <div v-else class="dlform">
          <div class="dllbl">ADD ENTRY</div>
          <div class="dlrow">
            <label class="dllbl">LABEL *</label>
            <input v-model="dLabel" class="dlin" placeholder="5 / 2024 Artworks / Extra" />
          </div>
          <div class="dlrow2">
            <div class="dlrow" style="flex:1;min-width:0">
              <label class="dllbl">LANGUAGE</label>
              <Combo :model-value="dLang" :suggestions="langSuggest" wide placeholder="EN"
                     @update:model-value="dLang = $event" />
            </div>
            <div class="dlrow" style="flex:1.4;min-width:0">
              <label class="dllbl">GROUP / SOURCE NAME</label>
              <Combo :model-value="dGroup" :suggestions="groupSuggest" wide placeholder="translator / site"
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
          <template v-for="g in panelTree" :key="g.num">
            <!-- group header when one label has several translations -->
            <div v-if="g.rows.length > 1" class="dlchrow">
              <span class="dlslot"></span>
              <span class="dlttl"><b class="mono">{{ g.num }}</b><template v-if="g.rows.find((r) => r.chapter?.title)"> {{ g.rows.find((r) => r.chapter?.title)?.chapter?.title }}</template></span>
            </div>
            <div :class="{ dlgrp: g.rows.length > 1 }">
              <template v-for="r in g.rows" :key="r.key">
                <div class="dlchrow">
                  <span v-if="g.rows.length === 1" class="dlttl"><b class="mono">{{ r.num }}</b><template v-if="r.chapter?.title"> {{ r.chapter.title }}</template></span>
                  <span v-if="r.lang || r.group" class="dlbadge"><span class="mono" style="color:var(--accent);font-size:9px">{{ r.lang || '?' }}</span><template v-if="r.group">{{ r.group }}</template></span>
                  <span v-if="g.rows.length > 1" style="flex:1"></span>
                  <template v-if="r.item">
                    <span v-if="r.item.state === 'failed'" class="dlfail mono" :title="r.item.error">✗ failed</span>
                    <span v-else class="dlprog mono">{{ r.item.total ? `${pct(r.item)}%` : mb(r.item.received) }}</span>
                  </template>
                  <template v-else-if="r.chapter">
                    <span v-if="r.chapter.dl && freshness(r.chapter.dlAt) === 'new'" class="dlnew mono">new</span>
                    <span v-if="r.chapter.dl" class="dlok mono">✓ {{ r.chapter.pages ? `${r.chapter.pages} pg` : 'file' }}</span>
                  </template>
                  <button v-if="!r.item" class="dlarm" :title="`Arm the next download into ${r.num}${r.lang ? ' · ' + r.lang : ''}`" @click="armRow(r)">
                    <Icon name="download" :size="11" :sw="2" />
                  </button>
                </div>
                <div v-if="r.item && r.item.state === 'downloading'" class="dlbar">
                  <div class="dlfill" :class="{ indet: !r.item.total }" :style="r.item.total ? { width: pct(r.item) + '%' } : {}"></div>
                </div>
                <!-- the download source, tree-style under the downloaded entry -->
                <div v-if="r.chapter?.dl && r.chapter.dlSource" class="dlsrc" :title="r.chapter.dlSource"
                     @click="newTab(r.chapter!.dlSource)">
                  <span class="dltree mono">└</span>
                  <Icon name="compass" :size="11" :sw="2" />
                  <span class="dlsrcname">{{ hostOf(r.chapter.dlSource) }}</span>
                  <Icon name="forward" :size="10" :sw="2.2" style="margin-left:auto;flex:none" />
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
        <button class="btn accent wide" :disabled="!props.hasPage || props.busy" @click="emit('autofill')">
          <Icon name="refresh" :size="13" :sw="2" />{{ props.busy ? 'Reading page…' : 'Auto-fill from this page' }}
        </button>
        <div class="afnote">Runs the site's recipe + page metadata. Fills only empty or auto fields — your manual edits are untouchable.</div>
      </div>

      <!-- the shared editor, capture-enabled -->
      <div class="body scroll">
        <MetadataEditor capture @capture="emit('capture', $event)" />
      </div>
      </template>

      <!-- explicit commit — on BOTH tabs (captured chapter rows commit too) -->
      <div class="foot">
        <span v-if="flash" class="flash">{{ flash }}</span>
        <div style="flex:1"></div>
        <button class="btn ghost" @click="discard">Discard</button>
        <button v-if="d.targetId" class="btn ghost" :disabled="d.saving || !d.meta.title.trim()" title="Create a separate new title from this draft" @click="save(true)">Save as new</button>
        <button class="btn accent" :disabled="d.saving || !d.meta.title.trim()" @click="save(false)">
          <Icon name="check" :size="13" :sw="2.2" />{{ d.saving ? 'Saving…' : (d.targetId ? 'Save' : 'Create') }}
        </button>
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
.msub { margin-top: 4px; font: 400 10.5px/1.3 system-ui; color: var(--tx3); }
.dirty { color: var(--warn); font-weight: 600; }
.hact { border: none; background: transparent; color: var(--tx3); cursor: pointer; padding: 4px; border-radius: 5px; }
.hact:hover { background: var(--hover); color: var(--tx); }
.targetmenu { max-height: 220px; overflow: auto; border-bottom: 1px solid var(--line); padding: 8px; background: var(--panel); }
.newrow { display: flex; align-items: center; gap: 9px; padding: 9px 14px; border-bottom: 1px solid var(--line); font: 600 12px/1 system-ui; color: var(--accent); cursor: pointer; }
.newrow:hover { background: var(--accentSoft); }
.srcbar { display: flex; align-items: center; gap: 9px; padding: 9px 14px; border-bottom: 1px solid var(--line); min-width: 0; }
.srclbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); flex: none; }
.srcchip { display: inline-flex; align-items: center; gap: 7px; font: 500 11.5px/1 system-ui; color: var(--tx); background: var(--panel); border: 1px solid var(--line); padding: 4px 8px 4px 4px; border-radius: 6px; cursor: pointer; min-width: 0; overflow: hidden; }
.srcchip:hover { border-color: var(--accent); }
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
.dlrow { display: flex; flex-direction: column; gap: 5px; }
.dlrow2 { display: flex; gap: 8px; }
.dllbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); }
.dlin { font: 500 12.5px/1.3 system-ui; color: var(--tx); background: var(--panel); border: 1px solid var(--line); border-radius: 7px; padding: 8px 10px; outline: none; width: 100%; }
.dlin:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accentSoft); }
.dllist { border-top: 1px solid var(--line); padding-top: 12px; }
.dlchrow { display: flex; align-items: center; gap: 8px; padding: 5px 4px; border-radius: 6px; }
.dlchrow:hover { background: var(--hover); }
.dlslot { flex: none; width: 4px; }
.dlttl { flex: 1; min-width: 0; font: 500 12px/1.3 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dlttl b { font: 600 12px/1 ui-monospace, monospace; }
.dlbadge { display: inline-flex; align-items: center; gap: 5px; font: 500 10px/1 system-ui; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); padding: 3px 6px; border-radius: 5px; flex: none; }
/* translations group: the same guide line as the title page */
.dlgrp { margin-left: 8px; padding-left: 9px; border-left: 2px solid var(--line); }
.dlarm { width: 22px; height: 22px; border: 1px solid var(--line); border-radius: 5px; background: transparent; color: var(--tx3); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; flex: none; }
.dlarm:hover { color: var(--accent); border-color: var(--accent); }
.dlok { font-size: 10px; color: var(--good); flex: none; }
.dlfail { font-size: 10px; color: var(--adult); flex: none; }
.dlprog { font-size: 10px; color: var(--accent); flex: none; }
.dlnew { font-size: 9px; color: var(--accent-ink); background: var(--accent); padding: 2px 5px; border-radius: 4px; flex: none; }
.dlsrc { display: flex; align-items: center; gap: 6px; margin: 0 4px 3px 14px; padding: 3px 6px; border-radius: 5px; color: var(--tx3); cursor: pointer; font: 400 10.5px/1 system-ui; }
.dlsrc:hover { background: var(--hover); color: var(--accent); }
.dltree { color: var(--line); font-size: 10px; }
.dlsrcname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.bind.linked { cursor: pointer; }
.bind.linked:hover { background: var(--hover); }
.dlbar { margin: 2px 4px 6px 14px; height: 4px; border-radius: 3px; background: var(--panel2); overflow: hidden; }
.dlfill { height: 100%; border-radius: 3px; background: var(--accent); transition: width .4s; }
.dlfill.indet { width: 35%; animation: dlslide 1.1s linear infinite; }
@keyframes dlslide { from { margin-left: -35%; } to { margin-left: 100%; } }

.autofill { padding: 12px 14px; border-bottom: 1px solid var(--line); }
.wide { width: 100%; justify-content: center; }
.afnote { margin-top: 7px; font: 400 10.5px/1.45 system-ui; color: var(--tx3); }

.body { flex: 1; min-height: 0; overflow: auto; padding: 14px; }

.foot { display: flex; align-items: center; gap: 7px; padding: 12px 14px; border-top: 1px solid var(--line); }
.flash { font: 600 11px/1.3 system-ui; color: var(--good); }
</style>
