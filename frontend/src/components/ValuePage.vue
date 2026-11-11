<script setup lang="ts">
// A VALUE PAGE (design/value-pages.md): one person, tag, character, studio,
// genre or list value of the owner's. What the library knows about it is
// worked out by the sidecar every time — works, numbers, neighbours, where it
// came from, the rules about it; what the owner writes (a description, links,
// an image, other spellings) is edited here, explicitly, and kept in the vault.
// A page is a tab of its own (design/navigation.md): a neighbour followed from
// here continues in it, and its Back returns.
import { computed, onActivated, onBeforeUnmount, ref, watch } from 'vue'
import Icon from './Icon.vue'
import HistoryNav from './HistoryNav.vue'
import FilterBlock from './FilterBlock.vue'
import SearchBox from './SearchBox.vue'
import Dropdown from './Dropdown.vue'
import Pager from './Pager.vue'
import { api } from '../api'
import { newTab, searchUrl } from '../browser'
import { readLocalOne, writeLocalOne } from '../local'
import {
  activeFilterCount, askConfirm, buildQuery, cache, editValueInMetadata, followValue, openAxis, openTitle,
  openTitleBackground, openValuePage, resetFilters, runVaultPass, showInLibrary, store, useShown, useTab,
} from '../store'
import {
  TITLE_SORTS, coverAt, coverStyle, emptyFacets, hueFor, initials, linkLabel, siteOf,
  type Facets, type PageLink, type PageRuleNote, type Title, type ValuePage,
} from '../data'

const tab = useTab()
const at = computed(() => (tab.place.kind === 'page' ? tab.place : null))
const { shown, whenShown } = useShown()
const p = ref<ValuePage | null>(null)
const loading = ref(false)

function failed(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

let seq = 0
async function load() {
  const r = at.value
  if (!r) return
  const n = ++seq
  loading.value = true
  try {
    const got = await api.page(r.field, r.value)
    if (n === seq) p.value = got
  } catch (e) {
    if (n === seq) store.error = failed(e)
  } finally {
    if (n === seq) loading.value = false
  }
}

const labelOf = (id: string) => store.fields.find((f) => f.id === id)?.label ?? id
const axisLabel = computed(() => (at.value ? labelOf(at.value.field) : ''))
const isPerson = computed(() => p.value?.vocab === 'authors')

// ---- the works: the library, scoped to this value ----
// The same query the library runs, with the value as a filter the page always
// carries — so every work is reachable, a page at a time, under the library's
// own search, sort and filter block. The page's selection and the page number
// live in its place: Back and Forward keep them, another page starts clean, and
// the library's own filters are never touched.
const PAGE_SIZE = 48
function worksQuery() {
  const r = at.value!
  const q = buildQuery(r.sel)
  return { ...q, f: [...(q.f ?? []), `${r.field}:${r.value}`] }
}
const results = ref<Title[]>([])
const workFacets = ref<Facets>(emptyFacets())
const worksLoading = ref(false)
let wseq = 0
async function loadWorks() {
  if (!at.value) return
  const n = ++wseq
  worksLoading.value = true
  try {
    const q = worksQuery()
    const [list, counts] = await Promise.all([api.library(q), api.facets(q)])
    if (n !== wseq) return
    cache(list)
    results.value = list.map((t) => store.byId[t.id] ?? t)
    workFacets.value = counts
  } catch (e) {
    if (n === wseq) store.error = failed(e)
  } finally {
    if (n === wseq) worksLoading.value = false
  }
}
// A changed selection starts from page 1 and asks again — a burst of typing
// once. Arriving at another place (Back, Forward, a neighbour) keeps its page.
let worksTimer: ReturnType<typeof setTimeout> | undefined
watch(() => [at.value, at.value ? JSON.stringify(worksQuery()) : ''] as const, ([place, key], prev) => {
  if (!place || !key) return
  const samePlace = !!prev && prev[0] === place
  if (samePlace && prev[1] !== key) place.page = 1
  if (!samePlace) results.value = [] // another value's works are not this one's, even for a moment
  clearTimeout(worksTimer)
  worksTimer = setTimeout(() => whenShown(loadWorks), samePlace ? 140 : 0)
}, { immediate: true })
onBeforeUnmount(() => clearTimeout(worksTimer))

const totalPages = computed(() => Math.max(1, Math.ceil(results.value.length / PAGE_SIZE)))
const curPage = computed(() => Math.min(Math.max(1, at.value?.page ?? 1), totalPages.value))
const pageWorks = computed(() => results.value.slice((curPage.value - 1) * PAGE_SIZE, curPage.value * PAGE_SIZE))
const worksEl = ref<HTMLElement | null>(null)
function goPage(v: number) {
  if (!at.value) return
  at.value.page = Math.min(Math.max(1, Math.floor(v) || 1), totalPages.value)
  worksEl.value?.scrollIntoView({ block: 'start' })
}
// narrowed below what holds the value — not yet known while the first answer is on its way
const filtered = computed(() => !!p.value && !(worksLoading.value && !results.value.length)
  && results.value.length !== p.value.titles)
const rangeLabel = computed(() => {
  const n = results.value.length
  const all = filtered.value ? ` · ${p.value?.titles} in all` : ''
  if (!n) return `0 works${all}`
  const a = (curPage.value - 1) * PAGE_SIZE + 1
  const b = Math.min(curPage.value * PAGE_SIZE, n)
  return `${a}–${b} of ${n} work${n === 1 ? '' : 's'}${all}`
})
const filtersOpen = ref(false)
const filterCount = computed(() => (at.value ? activeFilterCount(at.value.sel) : 0))
// shown first among the filter block's chips: what the page IS, not a filter
const baseChip = computed(() => (p.value ? `${p.value.kind.toLowerCase()}: ${p.value.value}` : ''))
function clearWorkFilters() {
  if (!at.value) return
  resetFilters(at.value.sel)
}

// the side strip can hide — this machine's preference, the same on every page
const sideOpen = ref(readLocalOne('lb.pageSide', ['0', '1'] as const, '1') === '1')
watch(sideOpen, (v) => writeLocalOne('lb.pageSide', v ? '1' : '0'))

// the library filtered to this value — and to what the page has filtered it to
function inLibrary() {
  const v = p.value
  if (v && at.value) showInLibrary(at.value.field, v.value, at.value.sel)
}
// the crumb: the list this value belongs to, in the library tab
function toList() {
  if (at.value && !editing.value) openAxis(at.value.field)
}

// ---- the face: its image, a person's initials, or its works' covers ----
const mosaic = computed(() => (p.value?.works ?? []).filter((w) => w.cover).slice(0, 4))

// ---- the rules about it ----
// other spellings, each once — a site's rule and a general one may share one
const alsoWritten = computed(() => {
  const out = new Map<string, number[]>()
  for (const n of p.value?.rules ?? []) {
    if (n.kind !== 'spelling' || !n.enabled) continue
    for (const v of n.values) out.set(v, [...(out.get(v) ?? []), n.n])
  }
  return [...out].map(([value, rules]) => ({ value, rules }))
})
function heldBy(n: PageRuleNote): number {
  return n.values.reduce((sum, v) => sum + (p.value?.held[v] ?? 0), 0)
}
function ruleText(n: PageRuleNote): string {
  switch (n.kind) {
    case 'renames': return `renamed to ${n.target}`
    case 'removes': return 'removed on the way in'
    case 'moves': return `moved to ${labelOf(n.targetField).toLowerCase()}${n.target ? `: ${n.target}` : ''}`
    case 'implies': return `adds ${labelOf(n.targetField).toLowerCase()} ${n.target}`
    case 'impliedBy': return `${labelOf(n.field).toLowerCase()} ${n.values.join(', ')} adds it`
    default: return ''
  }
}
const RULE_KIND: Record<PageRuleNote['kind'], string> = {
  spelling: 'SPELLING', renames: 'RENAMED', removes: 'REMOVED', moves: 'MOVED',
  implies: 'IMPLIES', impliedBy: 'FROM',
}
// A spelling's rule applied to the titles that still hold it: the rule's own
// operation, previewed by the count and confirmed.
async function applySpelling(n: PageRuleNote) {
  const held = heldBy(n)
  const ok = await askConfirm({
    title: 'Apply the spelling', okLabel: 'Rename',
    message: `Rename ${n.values.join(', ')} to “${p.value?.value}” in ${held} title${held === 1 ? '' : 's'}? `
      + 'The run is kept in Metadata › History and can be reverted.',
  })
  if (!ok) return
  await runVaultPass(() => api.ruleRun(n.id, {}))
  await load()
}

// ---- the favourite mark people already have (authors.json) ----
async function toggleFav() {
  const v = p.value
  if (!v || !v.personId) return
  const next = !v.fav
  v.fav = next
  try {
    const people = await api.setAuthorFav(v.personId, next)
    v.fav = people.find((x) => x.id === v.personId)?.fav ?? next
  } catch (e) {
    v.fav = !next
    store.error = failed(e)
  }
}

// ---- edit: explicit, Save or Cancel ----
type PendingImage =
  | { kind: 'file'; file: Blob; preview: string }
  | { kind: 'title'; titleId: string; preview: string }
  | { kind: 'drop' }
const editing = ref(false)
const saving = ref(false)
const edDesc = ref('')
const edLinks = ref<PageLink[]>([])
const edSpell = ref<string[]>([])
const spellInput = ref('')
const linkInput = ref('')
const edImage = ref<PendingImage | null>(null) // null: the image stays as it is

// Edit what is stored NOW: spellings are rules, and a rule added elsewhere while
// this tab waited must not be dropped by a save that sends the whole list.
async function startEdit() {
  await load()
  const v = p.value
  if (!v) return
  edDesc.value = v.desc
  edLinks.value = v.links.map((l) => ({ ...l }))
  edSpell.value = [...v.spellings]
  spellInput.value = ''
  linkInput.value = ''
  setImage(null)
  editing.value = true
}
function cancelEdit() {
  editing.value = false
  setImage(null)
}
function setImage(next: PendingImage | null) {
  const cur = edImage.value
  if (cur?.kind === 'file') URL.revokeObjectURL(cur.preview)
  edImage.value = next
}
onBeforeUnmount(() => setImage(null))

// Below the edit state on purpose: the first run is immediate, and it leaves
// edit mode — which must exist by then.
watch(at, () => { cancelEdit(); void load() }, { immediate: true })
// a commit or a metadata run changes the works and the counts — a page behind
// another tab reads itself again when it comes back (below)
watch(() => store.titles, () => {
  whenShown(loadWorks)
  if (shown.value) reloadIdle()
})
function reloadIdle() {
  if (!editing.value) void load()
}
// A page being edited holds its tab: Back waits, and closing it asks.
watch(editing, (on) => {
  tab.hold = on ? `The page of “${at.value?.value ?? ''}” is being edited.` : ''
})
onBeforeUnmount(() => { tab.hold = '' })
// back in front: what was written here or about it elsewhere is read again
onActivated(() => { if (p.value) reloadIdle() })

const shownImage = computed(() => {
  const im = edImage.value
  if (im?.kind === 'drop') return ''
  if (im) return im.preview
  return p.value?.image ? coverAt(p.value.image, 264) : ''
})
// a work's own first cover can become the face; a stand-in page cannot
const coverPicks = computed(() => (p.value?.works ?? [])
  .filter((w) => w.cover.startsWith(`/api/titles/${w.id}/cover`)).slice(0, 6))

function takeFile(file: File | undefined | null) {
  if (!file || !file.type.startsWith('image/')) return
  setImage({ kind: 'file', file, preview: URL.createObjectURL(file) })
}
const fileEl = ref<HTMLInputElement | null>(null)
function onFile(e: Event) {
  const input = e.target as HTMLInputElement
  takeFile(input.files?.[0])
  input.value = ''
}
function onDrop(e: DragEvent) {
  takeFile(e.dataTransfer?.files?.[0])
}
function onPaste(e: ClipboardEvent) {
  if (!editing.value || !shown.value) return
  const file = [...(e.clipboardData?.files ?? [])].find((f) => f.type.startsWith('image/'))
  if (file) {
    e.preventDefault()
    takeFile(file)
  }
}
window.addEventListener('paste', onPaste)
onBeforeUnmount(() => window.removeEventListener('paste', onPaste))

function addLink() {
  const url = linkInput.value.trim()
  if (!url) return
  const full = /^https?:\/\//i.test(url) ? url : `https://${url}`
  if (!siteOf(full).host) return
  edLinks.value.push({ url: full, label: linkLabel(full) })
  linkInput.value = ''
}
let dragFrom = -1
function dropLink(to: number) {
  if (dragFrom < 0 || dragFrom === to) return
  const [moved] = edLinks.value.splice(dragFrom, 1)
  edLinks.value.splice(to, 0, moved)
  dragFrom = -1
}
// a pasted address is a link already — the input's value lands a tick later
function onLinkPaste() {
  window.setTimeout(addLink, 0)
}
function addSpelling() {
  const s = spellInput.value.trim()
  spellInput.value = ''
  if (!s || s.toLowerCase() === p.value?.value.toLowerCase()) return
  if (!edSpell.value.some((x) => x.toLowerCase() === s.toLowerCase())) edSpell.value.push(s)
}

async function saveEdit() {
  const r = at.value
  if (!r || saving.value) return
  saving.value = true
  // the rules are touched only when the spellings actually changed
  const was = p.value?.spellings ?? []
  const spellingsChanged = edSpell.value.length !== was.length
    || edSpell.value.some((s, i) => s !== was[i])
  try {
    let out = await api.savePage(r.field, r.value, {
      desc: edDesc.value,
      links: edLinks.value.filter((l) => l.url.trim()).map((l) => ({ url: l.url.trim(), label: l.label.trim() })),
      ...(spellingsChanged ? { spellings: edSpell.value } : {}),
    })
    const im = edImage.value
    if (im?.kind === 'file') out = await api.pageImage(r.field, r.value, im.file)
    else if (im?.kind === 'title') out = await api.pageImageFromTitle(r.field, r.value, im.titleId)
    else if (im?.kind === 'drop') out = await api.dropPageImage(r.field, r.value)
    if (at.value !== r) return // another page took this one's place meanwhile
    p.value = out
    cancelEdit()
  } catch (e) {
    store.error = failed(e)
  } finally {
    saving.value = false
  }
}

// a description's addresses are links; everything else is the text as written
function pieces(text: string): { t: string; url?: string }[] {
  return text.split(/(https?:\/\/[^\s)]+)/g).filter(Boolean)
    .map((t) => (/^https?:\/\//.test(t) ? { t, url: t } : { t }))
}
const paragraphs = computed(() => (p.value?.desc ?? '').split(/\n\s*\n/).map((s) => s.trim()).filter(Boolean))

function lookUp() {
  const v = p.value
  if (v) newTab(searchUrl(`${v.value} ${v.kind.toLowerCase()}`))
}
</script>

<template>
  <div class="vp">
    <div class="vhead">
      <HistoryNav />
      <span class="crumb"><span class="cl" :title="`All ${axisLabel.toLowerCase()} — Browse by`" @click="toList">{{ axisLabel }}</span>
        <span class="csep">/</span><b>{{ p?.value ?? at?.value }}</b></span>
      <span v-if="editing" class="editing mono">EDITING</span>
      <div style="flex:1"></div>
      <template v-if="!editing">
        <button v-if="p && isPerson" class="btn" :class="{ favon: p.fav }"
                :title="p.fav ? 'Unfavourite' : 'Favourite this person'" @click="toggleFav">
          <Icon name="star" :size="14" :sw="1.8" :fill="p.fav ? 'var(--fav)' : 'none'" />
        </button>
        <button class="btn" :disabled="!p?.titles" @click="inLibrary"><Icon name="filter" :size="13" :sw="1.9" />Show in library</button>
        <button v-if="p" class="btn" title="This value in the Metadata view — to rename or merge it"
                @click="editValueInMetadata(p.field, p.value)"><Icon name="meta" :size="13" :sw="1.9" />In Metadata</button>
        <button class="btn accent" :disabled="!p" @click="startEdit"><Icon name="edit" :size="13" :sw="1.9" />Edit</button>
      </template>
      <template v-else>
        <button class="btn ghost" :disabled="saving" @click="cancelEdit">Cancel</button>
        <button class="btn accent" :disabled="saving" @click="saveEdit">
          <Icon name="check" :size="13" :sw="2.2" />{{ saving ? 'Saving…' : 'Save' }}</button>
      </template>
    </div>

    <div v-if="p" class="vbody scroll">
      <!-- the head band, in the title page's grammar -->
      <div class="band">
        <!-- EDIT: the image is a copy — dropped, pasted, or a cover of its works -->
        <div v-if="editing" class="facecol">
          <div class="drop" :class="{ round: isPerson, has: !!shownImage }" @click="fileEl?.click()"
               @dragover.prevent @drop.prevent="onDrop">
            <img v-if="shownImage" :src="shownImage" alt="" />
            <span v-else><Icon name="download" :size="18" :sw="2" /><br />Drop, paste or pick an image</span>
          </div>
          <input ref="fileEl" type="file" accept="image/*" style="display:none" @change="onFile" />
          <template v-if="coverPicks.length">
            <span class="pickl">or a cover of its works:</span>
            <div class="picks">
              <span v-for="w in coverPicks" :key="w.id" class="pick"
                    :class="{ sel: edImage?.kind === 'title' && edImage.titleId === w.id }" :title="w.title"
                    :style="{ background: `#181a1f url(${coverAt(w.cover, 90)}) center/cover no-repeat` }"
                    @click="setImage({ kind: 'title', titleId: w.id, preview: coverAt(w.cover, 264) })"></span>
            </div>
          </template>
          <button v-if="p.image && edImage?.kind !== 'drop'" class="btn small ghost" @click="setImage({ kind: 'drop' })">Remove image</button>
        </div>
        <div v-else class="face" :class="{ round: isPerson && !p.image }">
          <img v-if="p.image" :src="coverAt(p.image, 264)" alt="" />
          <span v-else-if="isPerson" class="ini" :style="{ background: hueFor(p.value) }">{{ initials(p.value) }}</span>
          <template v-else-if="mosaic.length">
            <span v-for="w in mosaic" :key="w.id" class="mt"
                  :style="{ background: `#181a1f url(${coverAt(w.cover, 140)}) center/cover no-repeat` }"></span>
          </template>
          <span v-else class="ini" :style="{ background: hueFor(p.value) }">{{ (p.value[0] || '?').toUpperCase() }}</span>
        </div>

        <div v-if="editing" class="form">
          <div class="frow"><span class="eyebrow">Description</span>
            <div><textarea v-model="edDesc" class="in ta" rows="5" placeholder="What it is, in your words — a pasted address becomes a link"></textarea></div>
          </div>
          <div class="frow"><span class="eyebrow">Links</span>
            <div>
              <div v-for="(l, i) in edLinks" :key="i" class="lrow" draggable="true"
                   @dragstart="dragFrom = i" @dragover.prevent @drop.prevent="dropLink(i)">
                <span class="grip" title="Drag to reorder">⋮⋮</span>
                <span class="g" :style="{ background: siteOf(l.url).color }">{{ siteOf(l.url).mark }}</span>
                <input v-model="l.url" class="in" spellcheck="false" />
                <input v-model="l.label" class="in" :placeholder="siteOf(l.url).name" />
                <button class="iconbtn plain" title="Remove" @click="edLinks.splice(i, 1)"><Icon name="x" :size="12" :sw="2.2" /></button>
              </div>
              <div class="lrow">
                <span class="grip"></span>
                <span class="g add">+</span>
                <input v-model="linkInput" class="in" spellcheck="false" placeholder="Paste a link — the site names itself"
                       @keydown.enter.prevent="addLink" @blur="addLink" @paste="onLinkPaste" />
                <span></span><span></span>
              </div>
            </div>
          </div>
          <div class="frow"><span class="eyebrow">{{ isPerson ? 'Other names' : 'Also written' }}</span>
            <div>
              <div class="chips">
                <span v-for="(s, i) in edSpell" :key="s" class="chip">{{ s }}
                  <button class="x" title="Remove" @click="edSpell.splice(i, 1)">×</button></span>
                <input v-model="spellInput" class="spin" :placeholder="isPerson ? 'add a name…' : 'add a spelling…'"
                       @keydown.enter.prevent="addSpelling" @blur="addSpelling" />
              </div>
              <div class="hint">Each {{ isPerson ? 'name' : 'spelling' }} is a <b>rename rule</b>
                (Metadata › Rules): capture and your typing turn it into “{{ p.value }}” from now on. Titles that already
                hold one can be renamed from the Rules list here.</div>
            </div>
          </div>
        </div>

        <div v-else class="info">
          <div class="badges">
            <span class="typebadge">{{ p.kind }}</span>
          </div>
          <h1 class="pname">{{ p.value }}</h1>
          <div v-if="alsoWritten.length" class="also">{{ isPerson ? 'also known as' : 'also written' }}
            <template v-for="(a, i) in alsoWritten" :key="a.value"><template v-if="i"> · </template>
              <span class="sp">{{ a.value }}</span> <span class="rl mono">rule {{ a.rules.join(', ') }}</span></template>
          </div>
          <div class="stats mono">
            <span><b>{{ p.titles }}</b> title{{ p.titles === 1 ? '' : 's' }}</span>
            <span><b>{{ p.chapters.toLocaleString() }}</b> ch.</span>
            <span><b>{{ p.read }}</b> read</span>
            <span v-if="p.rated">your rating <b>{{ p.rating }}</b></span>
          </div>
          <div v-if="p.links.length" class="links">
            <a v-for="l in p.links" :key="l.url" class="lk" :title="l.url" @click="newTab(l.url)">
              <span class="g" :style="{ background: siteOf(l.url).color }">{{ siteOf(l.url).mark }}</span>
              {{ l.label || siteOf(l.url).name }}<span class="d mono">{{ siteOf(l.url).host }}</span>
            </a>
          </div>
          <div v-if="paragraphs.length" class="desc">
            <p v-for="(para, i) in paragraphs" :key="i">
              <template v-for="(bit, j) in pieces(para)" :key="j"><a v-if="bit.url" @click="newTab(bit.url)">{{ bit.t }}</a><template v-else>{{ bit.t }}</template></template>
            </p>
          </div>
          <!-- the unknown value: an empty description that says so -->
          <div v-else-if="!p.links.length" class="empty">
            <Icon name="search" :size="18" :sw="1.8" />
            <div class="msg">
              <div class="t">Nothing written about “{{ p.value }}” yet</div>
              <div class="s">{{ p.titles ? 'The works below are the library\'s answer.' : 'No title holds it any more.' }}
                Add what it means — or look it up first.</div>
            </div>
            <div class="acts">
              <button class="btn" @click="lookUp">Look it up</button>
              <button class="btn accent" @click="startEdit">Write what it means</button>
            </div>
          </div>
        </div>
      </div>

      <!-- while editing, the page around the form is not a way out of it -->
      <div class="body" :class="{ inert: editing, noside: !sideOpen }">
        <div class="colmain">
          <div v-if="at" ref="worksEl" class="sec">
            <div class="sechead">
              <span class="eyebrow">Works</span>
              <span class="cnt mono">{{ filtered ? `${results.length} of ${p.titles}` : p.titles }}</span>
              <div class="right">
                <SearchBox v-model="at.sel.search" placeholder="Search these works…" :width="220" />
                <Dropdown :model-value="at.sel.sort" :options="TITLE_SORTS" @update:model-value="at.sel.sort = String($event)" />
                <button class="btn" :class="{ acton: filtersOpen }" title="Filters" @click="filtersOpen = !filtersOpen">
                  <Icon name="filter" :size="13" :sw="1.9" />Filters
                  <span v-if="filterCount" class="numbadge">{{ filterCount }}</span>
                </button>
                <button class="btn sidebtn" :class="{ acton: sideOpen }"
                        :title="sideOpen ? 'Hide the side strip — the works take the width' : 'Show the side strip'"
                        @click="sideOpen = !sideOpen"><Icon name="panel" :size="14" :sw="1.9" /></button>
              </div>
            </div>
            <div v-if="!results.length && worksLoading" class="none">Loading…</div>
            <div v-else-if="!results.length && !p.titles" class="none">No title holds it now.</div>
            <div v-else-if="!results.length" class="nomatch">
              <span class="none">No work matches these filters.</span>
              <button class="btn ghost small" @click="clearWorkFilters">Clear filters</button>
            </div>
            <div v-else class="covers">
              <div v-for="t in pageWorks" :key="t.id" class="cov" :style="coverStyle(t, 220)"
                   :title="t.title" @click="openTitle(t.id)" @mousedown.middle.prevent="openTitleBackground(t.id)">
                <span v-if="!t.cover" class="m">{{ (t.title[0] || '?').toUpperCase() }}</span>
                <span class="w">{{ t.title }}</span>
              </div>
            </div>
          </div>
          <div v-for="g in p.groups" :key="g.label" class="sec">
            <div class="sechead"><span class="eyebrow">{{ g.label }}</span><span class="cnt">on the same titles</span></div>
            <div class="chips">
              <button v-for="n in g.items" :key="n.field + n.value" class="chip link" @click="followValue(n.field, n.value)"
                      @mousedown.middle.prevent="openValuePage(n.field, n.value, true)">
                <span v-if="isPerson && n.field === 'authors'" class="av" :style="{ background: hueFor(n.value) }">{{ initials(n.value) }}</span>
                {{ n.value }}<span class="n mono">{{ n.n }}<template v-if="p.vocab !== n.field"> · {{ labelOf(n.field).toLowerCase() }}</template></span>
              </button>
            </div>
          </div>
        </div>

        <div v-if="sideOpen" class="colside">
          <div class="sec">
            <div class="sechead"><span class="eyebrow">In your library</span></div>
            <div class="kv">
              <span>Titles</span><span class="v mono">{{ p.titles }}</span>
              <span>Chapters</span><span class="v mono">{{ p.chapters.toLocaleString() }}</span>
              <span>Read</span><span class="v mono">{{ p.read }} of {{ p.titles }}</span>
              <div class="bar"><span :style="{ width: p.titles ? `${Math.round(100 * p.read / p.titles)}%` : '0' }"></span></div>
              <span>Your rating</span><span class="v mono">{{ p.rated ? `${p.rating} · ${p.rated} rated` : '—' }}</span>
            </div>
          </div>
          <div v-if="p.sources.length" class="sec">
            <div class="sechead"><span class="eyebrow">Came from</span></div>
            <div v-for="s in p.sources" :key="s.source + s.typed" class="src">
              <span class="sname">{{ s.typed ? 'typed by you' : s.source || 'no source' }}</span>
              <span class="n mono">{{ s.n }}</span>
              <span class="b"><span :style="{ width: `${Math.round(100 * s.n / Math.max(1, p.titles))}%` }"></span></span>
            </div>
          </div>
          <div class="sec">
            <div class="sechead"><span class="eyebrow">Rules</span>
              <div class="right"><a v-if="!editing" class="lnk" @click="startEdit">{{ isPerson ? 'Add a name' : 'Add a spelling' }}</a></div></div>
            <div v-if="p.rulesError" class="none">{{ p.rulesError }}</div>
            <div v-else-if="!p.rules.length" class="none">No rule mentions it.</div>
            <div v-for="n in p.rules" :key="n.id" class="rule" :class="{ off: !n.enabled }">
              <span class="k mono">{{ n.kind === 'spelling' && isPerson ? 'NAME' : RULE_KIND[n.kind] }}</span>
              <span class="rt">
                <template v-if="n.kind === 'spelling'"><b>{{ n.values.join(', ') }}</b> → {{ p.value }}</template>
                <template v-else>{{ ruleText(n) }}</template>
                <template v-if="n.domains.length"> · on {{ n.domains.join(', ') }}</template>
                <template v-if="!n.enabled"> · off</template>
                <template v-if="n.kind === 'spelling' && heldBy(n)">
                  <br /><span class="held">{{ heldBy(n) }} title{{ heldBy(n) === 1 ? '' : 's' }} still hold it —
                    <a class="lnk" @click="applySpelling(n)">rename them</a></span></template>
              </span>
              <span class="rn mono">rule {{ n.n }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div v-else-if="loading" class="none pad">Loading…</div>

    <!-- the view's closing band: where the works are, page by page -->
    <div v-if="p" class="lfoot">
      <span class="mono lfinfo">{{ rangeLabel }}</span>
      <div style="flex:1"></div>
      <Pager :page="curPage" :pages="totalPages" @go="goPage" />
    </div>

    <!-- the library's own filter block, on this page's selection and its works' counts -->
    <FilterBlock v-if="filtersOpen && at && !editing" :sel="at.sel" :facets="workFacets" :base="baseChip"
                 @close="filtersOpen = false" />
  </div>
</template>

<style scoped>
/* The whole height of the tab, as every view in a TabHost takes it — a block
   parent, so flex: 1 sized it to its content, and the side strip and the
   closing band ended wherever the works did. Relative: the filter block floats
   over the page, as it does over the library. */
.vp { position: relative; height: 100%; overflow: hidden; display: flex; flex-direction: column; }
.sidebtn { width: 30px; padding: 0; }
.vhead { height: 44px; flex: none; display: flex; align-items: center; gap: 10px; padding: 0 16px 0 10px; border-bottom: 1px solid var(--line); }
.crumb { font: 500 12px/1 system-ui; color: var(--tx3); display: flex; align-items: center; gap: 7px; min-width: 0; }
.crumb .cl { cursor: pointer; }
.crumb .cl:hover { color: var(--accent); }
.crumb b { color: var(--tx); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.editing { font-size: 10px; letter-spacing: .1em; color: var(--warn); font-weight: 600; }
.btn.favon { color: var(--fav); border-color: color-mix(in srgb, var(--fav) 45%, var(--line)); }
/* A column, so the body below the band can GROW into what is left of the view:
   the side strip runs to the closing band however few works there are. */
.vbody { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; }

.band { flex: none; display: flex; gap: 24px; padding: 24px 24px 22px; border-bottom: 1px solid var(--line); }
.face { width: 132px; height: 132px; flex: none; border-radius: 10px; border: 1px solid var(--line); overflow: hidden; display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr; gap: 2px; background: var(--line); }
.face.round { border-radius: 50%; }
.face img { grid-column: 1 / -1; grid-row: 1 / -1; width: 100%; height: 100%; object-fit: cover; }
.face .ini { grid-column: 1 / -1; grid-row: 1 / -1; display: flex; align-items: center; justify-content: center; font: 600 40px/1 system-ui; color: rgba(255, 255, 255, .88); }
.info { flex: 1; min-width: 0; }
.badges { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.typebadge { display: inline-flex; align-items: center; height: 22px; padding: 0 8px; font: 600 10px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--accent); border: 1px solid var(--accent); border-radius: 5px; text-transform: uppercase; }
.pname { margin: 0; font: 600 30px/1.08 system-ui; letter-spacing: -.6px; color: var(--tx); overflow-wrap: anywhere; }
.also { margin-top: 6px; font: 400 13px/1.4 system-ui; color: var(--tx3); }
.also .sp { color: var(--tx2); }
.also .rl { font-size: 10.5px; color: var(--accent); }
.stats { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 14px; font-size: 11.5px; color: var(--tx2); }
.stats b { color: var(--tx); font-weight: 600; }
.links { margin-top: 14px; display: flex; flex-wrap: wrap; gap: 6px; }
.lk { display: inline-flex; align-items: center; gap: 7px; height: 26px; padding: 0 10px 0 4px; border-radius: 6px; border: 1px solid var(--line); background: var(--panel); font: 500 11.5px/1 system-ui; color: var(--tx); cursor: pointer; }
.lk:hover { border-color: var(--accent); }
.g { width: 18px; height: 18px; flex: none; border-radius: 4px; display: inline-flex; align-items: center; justify-content: center; font: 700 9.5px/1 system-ui; color: #fff; }
.g.add { background: var(--line); color: var(--tx3); }
.lk .d { font-size: 10.5px; color: var(--tx3); }
.desc { margin-top: 14px; max-width: 74ch; font: 400 13px/1.6 system-ui; color: var(--tx2); }
.desc p { margin: 0 0 8px; white-space: pre-line; }
.desc a { color: var(--accent); cursor: pointer; }
.empty { margin-top: 14px; max-width: 820px; border: 1px dashed var(--line); border-radius: 10px; padding: 14px 16px; display: flex; align-items: center; gap: 14px; color: var(--tx3); }
.empty .msg { flex: 1; min-width: 0; }
.empty .t { font: 600 13px/1.3 system-ui; color: var(--tx); }
.empty .s { font: 400 12px/1.45 system-ui; color: var(--tx3); margin-top: 3px; }
.empty .acts { display: flex; gap: 8px; flex: none; }

/* edit */
.facecol { display: flex; flex-direction: column; align-items: center; gap: 10px; width: 132px; flex: none; }
.drop { width: 132px; height: 132px; border-radius: 10px; border: 1.5px dashed var(--accent); background: var(--accentSoft); display: flex; align-items: center; justify-content: center; text-align: center; padding: 0 12px; color: var(--accent); font: 600 11px/1.35 system-ui; cursor: pointer; overflow: hidden; }
.drop.round { border-radius: 50%; }
.drop.has { padding: 0; border-style: solid; }
.drop img { width: 100%; height: 100%; object-fit: cover; }
.pickl { font: 500 11px/1 system-ui; color: var(--tx3); }
.picks { display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; }
.pick { width: 38px; aspect-ratio: 2 / 3; border-radius: 4px; border: 1px solid var(--line); cursor: pointer; }
.pick.sel { outline: 2px solid var(--accent); outline-offset: 2px; }
.form { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 14px; max-width: 780px; }
.frow { display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 12px; align-items: start; }
.frow > .eyebrow { line-height: 30px; }
.ta { height: auto; min-height: 104px; padding: 8px 10px; resize: vertical; font: 400 13px/1.55 system-ui; }
.lrow { display: grid; grid-template-columns: 14px 22px minmax(0, 1fr) 150px 26px; gap: 8px; align-items: center; margin-bottom: 6px; }
.lrow .grip { color: var(--tx3); font: 700 11px/1 system-ui; letter-spacing: -1px; cursor: grab; }
.lrow .g { width: 22px; height: 22px; border-radius: 5px; font-size: 10px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.chip { display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 10px; border-radius: 999px; background: var(--panel2); border: 1px solid var(--line); font: 500 12px/1 system-ui; color: var(--tx); white-space: nowrap; }
.chip .x { border: none; background: none; color: var(--tx3); cursor: pointer; font-size: 14px; line-height: 1; padding: 0; }
.chip .n { font-size: 10px; color: var(--tx3); }
.chip.link { cursor: pointer; }
.chip.link:hover { border-color: var(--accent); color: var(--accent); }
.chip .av { width: 18px; height: 18px; margin-left: -6px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font: 600 8px/1 system-ui; color: #fff; }
.spin { height: 26px; min-width: 150px; border: 1px dashed var(--line); border-radius: 999px; background: transparent; color: var(--tx); padding: 0 10px; font: 500 12px/1 system-ui; outline: none; }
.spin:focus { border-color: var(--accent); }
.hint { font: 400 11.5px/1.45 system-ui; color: var(--tx3); margin-top: 6px; }
.hint b { color: var(--tx2); font-weight: 600; }

/* below the band */
.body { flex: 1 0 auto; display: grid; grid-template-columns: minmax(0, 1fr) 286px; }
.body.noside { grid-template-columns: minmax(0, 1fr); }
.body.inert { opacity: .45; pointer-events: none; }
.colmain { padding: 18px 24px 24px; min-width: 0; }
.colside { border-left: 1px solid var(--line); padding: 18px 18px 24px; background: var(--bg2); }
.sec + .sec { margin-top: 22px; }
.sechead { display: flex; align-items: center; gap: 10px; min-height: 26px; margin-bottom: 10px; }
.sechead .cnt { font-size: 10.5px; color: var(--tx3); }
.sechead .right { margin-left: auto; display: flex; gap: 10px; align-items: center; }
.lnk { font: 600 11.5px/1 system-ui; color: var(--accent); cursor: pointer; }
.none { font: 400 12px/1.45 system-ui; color: var(--tx3); }
.none.pad { padding: 24px; }
.covers { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 12px; }
.cov { position: relative; aspect-ratio: 2 / 3; border-radius: 6px; overflow: hidden; border: 1px solid var(--line); box-shadow: 0 5px 14px rgba(0, 0, 0, .35); cursor: pointer; }
.cov:hover { outline: 2px solid var(--accent); outline-offset: 1px; }
.cov .m { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font: 600 30px/1 Georgia, serif; color: rgba(242, 237, 230, .16); }
.cov .w { position: absolute; left: 0; right: 0; bottom: 0; padding: 18px 7px 7px; background: linear-gradient(transparent, rgba(9, 10, 13, .88)); font: 600 10.5px/1.2 system-ui; color: #f2ede6; }
.nomatch { display: flex; align-items: center; gap: 12px; }
.kv { display: grid; grid-template-columns: 1fr auto; row-gap: 9px; column-gap: 10px; font: 500 12px/1.2 system-ui; color: var(--tx2); }
.kv .v { font-size: 11.5px; color: var(--tx); text-align: right; }
.bar { height: 4px; border-radius: 999px; background: var(--line); overflow: hidden; grid-column: 1 / -1; margin-top: -4px; }
.bar span { display: block; height: 100%; background: var(--good); }
.src { display: flex; align-items: center; gap: 8px; height: 24px; font: 500 12px/1 system-ui; color: var(--tx2); }
.src .sname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.src .n { font-size: 10.5px; color: var(--tx3); }
.src .b { width: 60px; flex: none; height: 4px; border-radius: 999px; background: var(--line); overflow: hidden; }
.src .b span { display: block; height: 100%; background: var(--tx3); }
.rule { display: flex; gap: 9px; align-items: flex-start; padding: 8px 0; border-top: 1px solid color-mix(in srgb, var(--line) 60%, transparent); font: 400 12px/1.4 system-ui; color: var(--tx2); }
.rule:first-of-type { border-top: 0; padding-top: 0; }
.rule.off { opacity: .6; }
.rule .k { font-size: 9px; line-height: 1.6; letter-spacing: .1em; color: var(--tx3); min-width: 58px; font-weight: 600; }
.rule .rt { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.rule b { color: var(--tx); font-weight: 600; }
.rule .rn { font-size: 10.5px; line-height: 1.5; color: var(--accent); white-space: nowrap; }
.rule .held { color: var(--warn); font-size: 11.5px; }
</style>
