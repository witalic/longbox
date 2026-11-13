<script setup lang="ts">
// Sources (design/sources-mockup.html): the page that manages the browser's
// tree — the groups, the sites in them and each site's links, in the order the
// browser's rail draws them — and everything kept about a site: its homepage,
// its recipe and what it left in the browser (cookies, storage, cache).
// Opening a link is the rail's job; this page arranges and cleans.
//
// A site row never unfolds: a site with a hundred links would bury the list.
// Its links live in the site's panel, which is built for a hundred.
import { computed, reactive, ref, watch } from 'vue'
import Combo from '../components/Combo.vue'
import Dropdown from '../components/Dropdown.vue'
import Icon from '../components/Icon.vue'
import RailSection from '../components/RailSection.vue'
import SearchBox from '../components/SearchBox.vue'
import SourcesLogins from '../components/SourcesLogins.vue'
import { sourceGroupNames, sourcesKnown, store } from '../store'
import { askConfirm } from '../confirm'
import { newTab } from '../browser'
import { api } from '../api'
import { faviconFor, movedBefore, type Bookmark, type Source } from '../data'
import { vFocus } from '../focus'
import { readLocalOne, writeLocalOne } from '../local'
import { loginsOf, loginsState, showLoginsIn } from '../logins'

const mode = ref(readLocalOne('lb.sourcesMode', ['tree', 'links'] as const, 'tree'))
watch(mode, (m) => writeLocalOne('lb.sourcesMode', m))
const q = ref('')
const needle = computed(() => q.value.trim().toLowerCase())
const noIcon = reactive<Record<string, boolean>>({})
const busy = ref(false)

async function run(work: () => Promise<Source[] | void>) {
  if (!sourcesKnown()) return
  busy.value = true
  try {
    const got = await work()
    if (got) store.sources = got
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}
const linkCount = computed(() => store.sources.reduce((n, s) => n + s.bookmarks.length, 0))

// ---- groups ----
const groupNames = computed(() => sourceGroupNames())
const picked = ref<string | null>(null) // the rail narrows to one group; '' = no group
const groupCount = (name: string) => store.sources.filter((s) => (s.group || '') === name).length

// The group list is written whole, so only on top of a list that was read.
async function saveGroups(next: string[]): Promise<boolean> {
  if (!sourcesKnown()) return false
  const was = store.sourceGroups
  store.sourceGroups = next // the change shows now; the server is told after
  try {
    store.sourceGroups = await api.putSourceGroups(next)
    return true
  } catch (e) {
    store.error = String(e)
    store.sourceGroups = was
    return false
  }
}
// A name typed that does not exist yet CREATES the group: filing a site only
// into groups made somewhere else is not filing, it is paperwork.
async function ensureGroup(name: string): Promise<boolean> {
  return !name || store.sourceGroups.includes(name) || saveGroups([...store.sourceGroups, name])
}

const addingGroup = ref(false)
const newGroup = ref('')
async function commitNewGroup() {
  const name = newGroup.value.trim()
  addingGroup.value = false
  newGroup.value = ''
  if (name) await ensureGroup(name)
}
function cancelNewGroup() {
  newGroup.value = '' // the blur that follows then has nothing to create
  addingGroup.value = false
}

const renaming = ref<string | null>(null)
const renameTo = ref('')
function startRename(name: string) {
  renaming.value = name
  renameTo.value = name
}
async function commitRename() {
  const from = renaming.value
  const to = renameTo.value.trim()
  renaming.value = null
  if (!from || !to || to === from || !sourcesKnown()) return
  if (groupNames.value.includes(to)) {
    store.error = `A group called "${to}" already exists.`
    return
  }
  // the group and every site in it, in one write
  await run(async () => {
    const got = await api.renameSourceGroup(from, to)
    store.sourceGroups = await api.sourceGroups()
    return got
  })
  if (picked.value === from) picked.value = to
}
async function deleteGroup(name: string) {
  const members = store.sources.filter((s) => s.group === name).map((s) => s.domain)
  const ok = await askConfirm({
    title: 'Delete group', okLabel: 'Delete',
    message: members.length
      ? `Delete "${name}"? Its ${members.length} site${members.length === 1 ? '' : 's'} move to No group.`
      : `Delete "${name}"?`,
  })
  if (!ok) return
  await run(async () => {
    const got = await api.deleteSourceGroup(name)
    store.sourceGroups = await api.sourceGroups()
    return got
  })
  if (picked.value === name) picked.value = null
}

// the group order: dragging a header writes the whole list; "No group" is the
// absence of one and stays last by construction
const dragGroup = ref<string | null>(null)
const groupDrop = ref<string | null>(null)
async function onGroupDrop(target: string) {
  const from = dragGroup.value
  dragGroup.value = groupDrop.value = null
  if (from === null || from === target || !target) return
  const names = [...groupNames.value]
  const at = names.indexOf(from)
  const onto = names.indexOf(target)
  // the header dropped on lights up whole: the moved group takes ITS place
  await saveGroups(movedBefore(names, at, onto > at ? onto + 1 : onto))
}

// ---- the list of sites ----
const FILTERS = [
  { v: 'all', l: 'All' }, { v: 'recipe', l: 'With recipe' }, { v: 'norecipe', l: 'No recipe' },
  { v: 'hidden', l: 'Hidden' },
] as const
const filter = ref<(typeof FILTERS)[number]['v']>('all')
// what the band says the list does, cut short where it has no room (its title says it whole)
const TREE_HINT = "Drag to reorder — the browser's rail follows · Ctrl/Shift-click picks several"
const LINKS_HINT = 'Double-click a name or an address to edit it'
function passes(s: Source): boolean {
  if (filter.value === 'recipe' && !s.hasRecipe) return false
  if (filter.value === 'norecipe' && s.hasRecipe) return false
  if (filter.value === 'hidden' && !s.frozen) return false
  if (picked.value !== null && (s.group || '') !== picked.value) return false
  return !needle.value || s.domain.toLowerCase().includes(needle.value)
    || s.bookmarks.some((b) => `${b.name} ${b.url}`.toLowerCase().includes(needle.value))
}
const filtering = computed(() => !!needle.value || filter.value !== 'all' || picked.value !== null)
const dragSite = ref<string | null>(null)
// Sections in the owner's order. An empty group still shows while nothing
// narrows the list — it is where a site is dragged into — and so does an
// empty "No group" while a site is being dragged.
const sections = computed(() => [...groupNames.value, ''].map((name) => ({
  name,
  rows: store.sources.filter((s) => (s.group || '') === name && passes(s)),
})).filter((sec) => sec.rows.length || (sec.name ? !filtering.value : !!dragSite.value)))
const shownSites = computed(() => sections.value.flatMap((sec) => sec.rows))

// ---- picking several ----
const selected = reactive(new Set<string>())
let anchor: string | null = null
function tick(domain: string) {
  if (selected.has(domain)) selected.delete(domain)
  else selected.add(domain)
  anchor = domain
}
function clickSite(s: Source, e: MouseEvent) {
  if (e.ctrlKey || e.metaKey) {
    tick(s.domain)
    return
  }
  if (e.shiftKey && anchor) {
    const order = shownSites.value.map((x) => x.domain)
    const [a, b] = [order.indexOf(anchor), order.indexOf(s.domain)].sort((x, y) => x - y)
    if (a >= 0) for (const d of order.slice(a, b + 1)) selected.add(d)
    return
  }
  selected.clear()
  openSite(s.domain)
}
const selectedSites = computed(() => store.sources.filter((s) => selected.has(s.domain)))
const allHidden = computed(() => selectedSites.value.length > 0 && selectedSites.value.every((s) => s.frozen))
// the dropdown's resting row: a value no group name can be
const MOVE = '\u0000'
const moveOptions = computed(() => [
  { v: MOVE, l: 'Move to group…' },
  ...groupNames.value.map((g) => ({ v: g, l: g })),
  { v: '', l: 'No group' },
])
async function moveSelected(group: string) {
  if (group === MOVE) return
  await run(() => api.bulkSources({ domains: [...selected], group }))
}
async function hideSelected() {
  await run(() => api.bulkSources({ domains: [...selected], frozen: !allHidden.value }))
}

// ---- dragging sites: inside a group, or into another ----
// where it lands: before this site, or at the end of the group
const siteDrop = ref<{ before: string | null; group: string } | null>(null)
function overSite(s: Source, rows: Source[], e: DragEvent) {
  if (!dragSite.value) return
  const box = (e.currentTarget as HTMLElement).getBoundingClientRect()
  const lower = e.clientY > box.top + box.height / 2
  const next = rows[rows.indexOf(s) + 1]
  siteDrop.value = { before: lower ? (next?.domain ?? null) : s.domain, group: s.group || '' }
}
function overGroupEnd(name: string) {
  if (dragSite.value) siteDrop.value = { before: null, group: name }
}
const dropsBefore = (domain: string) => !!siteDrop.value && siteDrop.value.before === domain
const dropsAtEnd = (name: string) => !!siteDrop.value && siteDrop.value.before === null && siteDrop.value.group === name
async function dropSite() {
  const from = dragSite.value
  const at = siteDrop.value
  dragSite.value = siteDrop.value = null
  if (!from || !at || at.before === from) return
  const moving = store.sources.find((s) => s.domain === from)
  if (!moving) return
  const order = store.sources.map((s) => s.domain).filter((d) => d !== from)
  let i = at.before ? order.indexOf(at.before) : -1
  if (i < 0) {
    const last = [...store.sources].reverse().find((s) => s.domain !== from && (s.group || '') === at.group)
    i = last ? order.indexOf(last.domain) + 1 : order.length
  }
  order.splice(i, 0, from)
  // the order and a change of group are one write
  const regroup = (moving.group || '') !== at.group
  await run(() => api.putSourceOrder(order, regroup ? from : '', regroup ? at.group : undefined))
}

// ---- one site: the panel ----
const open = ref<string | null>(null)
const site = computed(() => store.sources.find((s) => s.domain === open.value) ?? null)
const ptab = ref<'settings' | 'links' | 'logins'>('settings')
function openSite(domain: string) {
  if (open.value !== domain) {
    open.value = domain
    resetLinks()
  }
}
// the site's saved logins, counted for its tab (known once they are unlocked);
// the key's popover in the browser asks for a site's Logins tab
const loginCount = ref(0)
watch([open, () => loginsState.unlocked], async () => {
  loginCount.value = open.value ? (await loginsOf(open.value).catch(() => [])).length : 0
}, { immediate: true })
watch(() => showLoginsIn.domain, (domain) => {
  if (!domain) return
  showLoginsIn.domain = ''
  if (!store.sources.some((x) => x.domain === domain)) return
  openSite(domain)
  ptab.value = 'logins'
}, { immediate: true })

const home = ref('')
watch(() => site.value?.homepage, (h) => { home.value = h ?? '' }, { immediate: true })
async function commitHome() {
  const s = site.value
  const value = home.value.trim()
  if (!s || value === s.homepage) return
  if (value && !/^https?:\/\//i.test(value)) {
    store.error = 'A homepage is an http(s) address.'
    home.value = s.homepage
    return
  }
  await run(() => api.putSourcePrefs(s.domain, { homepage: value }))
}
async function setGroup(s: Source, name: string) {
  const g = name.trim()
  if (await ensureGroup(g)) await run(() => api.putSourcePrefs(s.domain, { group: g }))
}

async function forgetRecipe(s: Source) {
  const ok = await askConfirm({
    title: 'Forget recipe', danger: true, okLabel: 'Forget',
    message: `Forget everything learned about ${s.domain}? Its titles are untouched; you'll teach the fields again on the next capture.`,
  })
  if (!ok) return
  // titles do not change with it: only the sites' list does
  await run(async () => {
    await api.removeRecipe(s.domain)
    return api.sources()
  })
}

// What a site left in the browser. Counted by the shell; a window with no
// shell has no browser to count.
const hasShell = !!window.longbox
const cookies = ref<number | null>(null)
async function countCookies() {
  const d = open.value
  cookies.value = null
  if (!d || !window.longbox) return
  try {
    const got = await window.longbox.siteData(d)
    if (open.value === d) cookies.value = got?.cookies ?? null
  } catch { /* nothing counted: the panel says so */ }
}
watch(open, () => void countCookies())

const SITE_DATA = () => [
  { key: 'cookies', label: 'Cookies', hint: 'Sign-ins and site preferences — you will be signed out', on: true },
  { key: 'storage', label: 'Site storage', hint: 'Local storage, IndexedDB, service workers', on: true },
  { key: 'cache', label: 'Cached pages and images', hint: "Only the site's own — nothing else is touched", on: true },
]
async function clearData(domains: string[]) {
  if (!window.longbox || !domains.length) return
  const choices = SITE_DATA()
  const ok = await askConfirm({
    title: domains.length === 1 ? `Clear browser data for ${domains[0]}` : `Clear browser data for ${domains.length} sites`,
    message: 'Titles, links and recipes stay.', okLabel: 'Clear', danger: true, choices,
  })
  if (!ok || !choices.some((c) => c.on)) return
  const what = Object.fromEntries(choices.map((c) => [c.key, c.on]))
  await clearEach(domains, what)
  void countCookies()
}
// one site refusing does not stop the others; the refusals are said once
async function clearEach(domains: string[], what: { cookies?: boolean; storage?: boolean; cache?: boolean }) {
  if (!window.longbox) return
  const failed: string[] = []
  for (const d of domains) {
    try {
      if (!(await window.longbox.clearSiteData(d, what))) failed.push(d)
    } catch {
      failed.push(d)
    }
  }
  if (failed.length) store.error = `Browser data could not be cleared for ${failed.join(', ')}.`
}
async function clearCookies(s: Source) {
  const ok = await askConfirm({
    title: 'Clear cookies', danger: true, okLabel: 'Clear',
    message: `Clear the cookies of ${s.domain}? You will be signed out of it.`,
  })
  if (!ok) return
  await clearEach([s.domain], { cookies: true })
  void countCookies()
}

// Removing takes a site off this list and forgets its recipe — and, unless the
// owner unticks it, clears what the site left in the browser, which is what
// used to stay behind after a source was gone.
async function removeSites(domains: string[]) {
  const choices = hasShell
    ? [{ label: 'Also clear their browser data', hint: 'Cookies, site storage and cached pages — signs you out', on: true }]
    : []
  const named = domains.length <= 6 ? domains.join(', ')
    : `${domains.slice(0, 5).join(', ')} and ${domains.length - 5} more`
  const ok = await askConfirm({
    title: domains.length === 1 ? 'Remove source' : `Remove ${domains.length} sources`, danger: true, okLabel: 'Remove',
    message: `Remove ${named} from this list and forget what longbox learned? Titles keep their source links — clear those per title, on the title itself.`,
    choices,
  })
  if (!ok || !sourcesKnown()) return
  // gone from the list at once — the answer can take a while on a busy disk,
  // and a row that just sits there reads as a click that did nothing
  const before = store.sources
  store.sources = before.filter((s) => !domains.includes(s.domain))
  for (const d of domains) selected.delete(d)
  if (open.value && domains.includes(open.value)) open.value = null
  try {
    if (choices[0]?.on) await clearEach(domains, { cookies: true, storage: true, cache: true })
    store.sources = await api.bulkSources({ domains, remove: true })
  } catch (e) {
    store.sources = before // back, with the reason
    store.error = e instanceof Error ? e.message : String(e)
  }
}

// ---- links ----
//
// One address saved twice is the same link, whatever the slash or the anchor.
function linkKey(url: string): string {
  try {
    const u = new URL(url)
    return `${u.protocol}//${u.host.toLowerCase()}${u.pathname.replace(/\/+$/, '')}${u.search}`
  } catch { return url.trim() }
}
const linkCounts = computed(() => {
  const n = new Map<string, number>()
  for (const s of store.sources) for (const b of s.bookmarks) n.set(linkKey(b.url), (n.get(linkKey(b.url)) ?? 0) + 1)
  return n
})
const isDup = (b: Bookmark) => (linkCounts.value.get(linkKey(b.url)) ?? 0) > 1
// how long ago a link was saved, in the coarse units a list is scanned in
function ago(iso?: string): string {
  if (!iso) return 'earlier'
  const days = (Date.now() - Date.parse(iso)) / 86_400_000
  if (Number.isNaN(days)) return 'earlier'
  if (days < 1) return 'today'
  if (days < 14) return `${Math.floor(days)} d`
  if (days < 60) return `${Math.floor(days / 7)} w`
  if (days < 365) return `${Math.floor(days / 30)} mo`
  return `${Math.floor(days / 365)} y`
}
// an address on the site itself reads as its path; anywhere else, whole
function shortUrl(s: Source, url: string): string {
  try {
    const u = new URL(url)
    const host = u.hostname.replace(/^www\./, '')
    return host === s.domain.replace(/^www\./, '') ? `${u.pathname}${u.search}` || '/' : url
  } catch { return url }
}
// A link is addressed by its place in the list it was read from — so every
// link action waits for the write before it (`busy`), and a change to a list
// drops the edits and the ticks that pointed into the old one.
async function saveLinks(s: Source, list: Bookmark[]) {
  await run(() => api.putSourcePrefs(s.domain, { bookmarks: list }))
}
watch(() => JSON.stringify(site.value?.bookmarks ?? null), () => {
  editing.value = null
  lsel.clear()
})
watch(() => JSON.stringify(store.sources.map((x) => x.bookmarks)), () => {
  tEditing.value = null
  tsel.clear()
})

// the panel's Links tab — one site's, built for a hundred
const lq = ref('')
const lsort = ref<'order' | 'name' | 'saved'>('order')
const ldups = ref(false)
const lsel = reactive(new Set<number>()) // indexes into the site's list
const editing = ref<number | 'new' | null>(null)
const eName = ref('')
const eUrl = ref('')
function resetLinks() {
  lq.value = ''
  ldups.value = false
  lsel.clear()
  editing.value = null
}
const panelLinks = computed(() => {
  const s = site.value
  if (!s) return []
  const words = lq.value.trim().toLowerCase()
  const rows = s.bookmarks.map((b, i) => ({ b, i }))
    .filter(({ b }) => (!words || `${b.name} ${b.url}`.toLowerCase().includes(words)) && (!ldups.value || isDup(b)))
  if (lsort.value === 'name') rows.sort((a, b) => a.b.name.localeCompare(b.b.name))
  if (lsort.value === 'saved') rows.sort((a, b) => (b.b.added ?? '').localeCompare(a.b.added ?? ''))
  return rows
})
const panelDups = computed(() => site.value?.bookmarks.filter(isDup).length ?? 0)
function startEdit(i: number | 'new') {
  const s = site.value
  if (!s || busy.value) return
  editing.value = i
  const b = i === 'new' ? null : s.bookmarks[i]
  eName.value = b?.name ?? ''
  eUrl.value = b?.url ?? s.homepage
}
async function commitEdit() {
  const s = site.value
  const at = editing.value
  const url = eUrl.value.trim()
  if (!s || at === null || !url) return
  const link = { name: eName.value.trim() || url, url }
  const list = at === 'new' ? [...s.bookmarks, link]
    : s.bookmarks.map((b, i) => (i === at ? { ...b, ...link } : b))
  editing.value = null
  await saveLinks(s, list)
}
async function dropLinks(indexes: number[]) {
  const s = site.value
  if (!s || !indexes.length) return
  const gone = new Set(indexes)
  lsel.clear()
  await saveLinks(s, s.bookmarks.filter((_, i) => !gone.has(i)))
}
const dragLink = ref<number | null>(null)
const linkDrop = ref<number | null>(null)
async function onLinkDrop(to: number) {
  const s = site.value
  const from = dragLink.value
  dragLink.value = linkDrop.value = null
  if (!s || from === null || from === to) return
  await saveLinks(s, movedBefore(s.bookmarks, from, to))
}

// ---- Links: every link of every site, to clean up ----
const tsort = ref<'name' | 'site' | 'saved'>('site')
const tdups = ref(false)
const tsel = reactive(new Set<string>()) // "<domain>#<index>"
// some sites or links picked: the band above the list holds what to do with them
const picking = computed(() => (mode.value === 'tree' ? selected.size : tsel.size) > 0)
const allLinks = computed(() => {
  const rows = store.sources.flatMap((s) => s.bookmarks.map((b, i) => ({ s, b, i, key: `${s.domain}#${i}` })))
    .filter(({ s, b }) => (!needle.value || `${b.name} ${b.url} ${s.domain}`.toLowerCase().includes(needle.value))
      && (!tdups.value || isDup(b)))
  if (tsort.value === 'name') rows.sort((a, b) => a.b.name.localeCompare(b.b.name))
  if (tsort.value === 'saved') rows.sort((a, b) => (b.b.added ?? '').localeCompare(a.b.added ?? ''))
  if (tsort.value === 'site') rows.sort((a, b) => a.s.domain.localeCompare(b.s.domain) || a.i - b.i)
  return rows
})
const allDups = computed(() => store.sources.reduce((n, s) => n + s.bookmarks.filter(isDup).length, 0))
const tEditing = ref<string | null>(null)
const tName = ref('') // the table's own: the panel may have an edit open too
const tUrl = ref('')
function startTableEdit(row: { s: Source; b: Bookmark; key: string }) {
  if (busy.value) return
  tEditing.value = row.key
  tName.value = row.b.name
  tUrl.value = row.b.url
}
async function commitTableEdit(row: { s: Source; i: number }) {
  const url = tUrl.value.trim()
  tEditing.value = null
  if (!url) return
  await saveLinks(row.s, row.s.bookmarks.map((b, i) => (i === row.i ? { ...b, name: tName.value.trim() || url, url } : b)))
}
function openFromTable(domain: string) {
  selected.clear() // the panel shows only with nothing picked
  mode.value = 'tree'
  openSite(domain)
  ptab.value = 'links'
}
async function deleteTicked() {
  const by = new Map<string, Set<number>>()
  for (const key of tsel) {
    const cut = key.lastIndexOf('#')
    const d = key.slice(0, cut)
    by.set(d, (by.get(d) ?? new Set()).add(Number(key.slice(cut + 1))))
  }
  tsel.clear()
  for (const [d, gone] of by) {
    const s = store.sources.find((x) => x.domain === d)
    if (s) await saveLinks(s, s.bookmarks.filter((_, i) => !gone.has(i)))
  }
}
</script>

<template>
  <div class="viewcol">
    <div class="head">
      <h1 class="h1">Sources</h1>
      <span class="count mono">{{ store.sources.length }} site{{ store.sources.length === 1 ? '' : 's' }} · {{ linkCount }} link{{ linkCount === 1 ? '' : 's' }}</span>
    </div>

    <Teleport to="#siderail">
      <RailSection label="GROUPS" hint="click to narrow">
        <button :class="{ on: picked === null }" @click="picked = null">
          <span class="slbl">All sites</span><span class="n mono">{{ store.sources.length }}</span>
        </button>
        <button v-for="g in groupNames" :key="g" :class="{ on: picked === g }" @click="picked = picked === g ? null : g">
          <span class="slbl">{{ g }}</span><span class="n mono">{{ groupCount(g) }}</span>
        </button>
        <button v-if="groupCount('')" :class="{ on: picked === '' }" @click="picked = picked === '' ? null : ''">
          <span class="slbl">No group</span><span class="n mono">{{ groupCount('') }}</span>
        </button>
        <div v-if="addingGroup" class="railin">
          <input v-model="newGroup" v-focus class="in" placeholder="Name the group, then Enter"
                 @keydown.enter="commitNewGroup" @keydown.esc="cancelNewGroup" @blur="commitNewGroup" />
        </div>
        <button v-else class="add" @click="addingGroup = true">
          <Icon name="plus" :size="13" :sw="2.2" /><span class="slbl">New group</span>
        </button>
      </RailSection>
    </Teleport>

    <!-- the band above the list: what narrows it — the filters, the view, the
         search — or, with some picked, what to do with them -->
    <div class="tool" :class="{ bulk: picking }">
      <template v-if="mode === 'tree' && selected.size">
        <span class="sel">{{ selected.size }} site{{ selected.size === 1 ? '' : 's' }} selected</span>
        <Dropdown :model-value="MOVE" :options="moveOptions" @update:model-value="moveSelected(String($event))" />
        <button class="btn small" :disabled="busy" @click="hideSelected">
          <Icon name="eyeoff" :size="12" :sw="1.9" />{{ allHidden ? 'Show in browser' : 'Hide from browser' }}</button>
        <button v-if="hasShell" class="btn small" :disabled="busy" @click="clearData([...selected])">Clear browser data…</button>
        <button class="btn small danger" :disabled="busy" @click="removeSites([...selected])">Remove…</button>
        <div style="flex:1"></div>
        <button class="btn ghost small" @click="selected.clear()"><Icon name="x" :size="12" :sw="2.2" />Clear selection</button>
      </template>
      <template v-else-if="mode === 'links' && tsel.size">
        <span class="sel">{{ tsel.size }} link{{ tsel.size === 1 ? '' : 's' }} selected</span>
        <button class="btn small danger" :disabled="busy" @click="deleteTicked"><Icon name="x" :size="12" :sw="2.2" />Delete</button>
        <div style="flex:1"></div>
        <button class="btn ghost small" @click="tsel.clear()">Clear selection</button>
      </template>
      <template v-else-if="mode === 'tree'">
        <div class="seg">
          <button v-for="f in FILTERS" :key="f.v" class="opt" :class="{ on: filter === f.v }" @click="filter = f.v">{{ f.l }}</button>
        </div>
        <span class="hint" :title="TREE_HINT">{{ TREE_HINT }}</span>
      </template>
      <template v-else>
        <div class="seg">
          <button class="opt" :class="{ on: !tdups }" @click="tdups = false">All</button>
          <button class="opt" :class="{ on: tdups }" @click="tdups = true">Duplicates <span class="mono dim">{{ allDups }}</span></button>
        </div>
        <div class="seg">
          <button class="opt" :class="{ on: tsort === 'name' }" @click="tsort = 'name'">Name</button>
          <button class="opt" :class="{ on: tsort === 'site' }" @click="tsort = 'site'">Site</button>
          <button class="opt" :class="{ on: tsort === 'saved' }" @click="tsort = 'saved'">Saved</button>
        </div>
        <span class="hint" :title="LINKS_HINT">{{ LINKS_HINT }}</span>
      </template>
      <template v-if="!picking">
        <div class="seg">
          <button class="opt" :class="{ on: mode === 'tree' }" @click="mode = 'tree'">Tree</button>
          <button class="opt" :class="{ on: mode === 'links' }" @click="mode = 'links'">Links</button>
        </div>
        <SearchBox v-model="q" :width="240" placeholder="Find a site or a link…" />
        <button class="btn" @click="addingGroup = true"><Icon name="plus" :size="13" :sw="2.2" />Group</button>
      </template>
    </div>

    <div v-if="!store.sources.length" class="emptyv">
      <div class="et">No sources yet</div>
      <div class="es">Sources appear here once your titles reference them, or once you save a link in the browser.</div>
      <button class="btn accent" @click="newTab()"><Icon name="plus" :size="14" :sw="2.2" />Open browser</button>
    </div>

    <!-- ================= TREE ================= -->
    <div v-else-if="mode === 'tree'" class="work">
      <div class="list scroll" :class="{ selecting: selected.size }"
           @dragover.prevent @drop.prevent="dragSite && dropSite()">
        <template v-for="sec in sections" :key="sec.name || '\u0000'">
          <div class="ghead" :class="{ drop: groupDrop === sec.name && !!sec.name, named: !!sec.name }"
               :draggable="!!sec.name && renaming !== sec.name"
               @dragstart="dragGroup = sec.name || null" @dragend="dragGroup = groupDrop = null"
               @dragover.prevent.stop="dragSite ? overGroupEnd(sec.name) : (groupDrop = dragGroup && sec.name ? sec.name : null)"
               @drop.prevent.stop="dragSite ? dropSite() : onGroupDrop(sec.name)">
            <span class="grip">{{ sec.name ? '⋮⋮' : '' }}</span>
            <input v-if="renaming === sec.name" v-model="renameTo" v-focus class="in rename"
                   @keydown.enter="commitRename" @keydown.esc="renaming = null" @blur="commitRename" />
            <span v-else class="glbl">{{ (sec.name || 'No group').toUpperCase() }} · {{ sec.rows.length }}</span>
            <span class="grule"></span>
            <template v-if="sec.name && renaming !== sec.name">
              <button class="gact" @click="startRename(sec.name)">Rename</button>
              <button class="gact" @click="deleteGroup(sec.name)">Delete group</button>
            </template>
          </div>
          <template v-for="s in sec.rows" :key="s.id">
            <div v-if="dropsBefore(s.domain)" class="dropline"></div>
            <div class="srow" :draggable="!busy"
                 :class="{ on: open === s.domain && !selected.size, selected: selected.has(s.domain), cold: s.frozen }"
                 @click="clickSite(s, $event)"
                 @dragstart="dragSite = s.domain" @dragend="dragSite = siteDrop = null"
                 @dragover.prevent.stop="overSite(s, sec.rows, $event)" @drop.prevent.stop="dropSite">
              <span class="fbox tick" :class="{ on: selected.has(s.domain) }" @click.stop="tick(s.domain)">
                <Icon v-if="selected.has(s.domain)" name="check" :size="9" :sw="3.2" />
              </span>
              <span class="grip">⋮⋮</span>
              <span class="init">
                <img v-if="!noIcon[s.domain]" class="favimg" :src="faviconFor(s.domain)" alt="" @error="noIcon[s.domain] = true" />
                <template v-else>{{ (s.domain.slice(0, 2) || '?').toUpperCase() }}</template>
              </span>
              <span class="sname">{{ s.domain }}</span>
              <span class="rmark" :class="{ none: !s.hasRecipe }">{{ s.hasRecipe ? `v${s.recipeVer}` : 'no recipe' }}</span>
              <span v-if="s.frozen" class="hidden mono" title="Kept here, not offered in the browser">
                <Icon name="eyeoff" :size="10" :sw="2" />hidden</span>
              <span style="flex:1"></span>
              <span class="snum mono">{{ s.titles ? `${s.titles} title${s.titles === 1 ? '' : 's'}` : '—' }}</span>
              <span class="slinks mono" :class="{ has: s.bookmarks.length }">
                <Icon name="star" :size="11" :sw="1.8" />{{ s.bookmarks.length }}</span>
            </div>
          </template>
          <div v-if="dropsAtEnd(sec.name)" class="dropline"></div>
        </template>
        <div v-if="!sections.length" class="none">Nothing matches.</div>
      </div>

      <!-- the site's panel: what it IS, and what is saved under it -->
      <aside v-if="site && !selected.size" class="pane">
        <div class="phead">
          <span class="init">
            <img v-if="!noIcon[site.domain]" class="favimg" :src="faviconFor(site.domain)" alt="" @error="noIcon[site.domain] = true" />
            <template v-else>{{ (site.domain.slice(0, 2) || '?').toUpperCase() }}</template>
          </span>
          <span class="pname">{{ site.domain }}</span>
          <button class="btn small" @click="newTab(site.homepage)"><Icon name="browser" :size="12" :sw="1.9" />Open homepage</button>
          <button class="iconbtn plain" title="Close" @click="open = null"><Icon name="x" :size="13" :sw="2.2" /></button>
        </div>
        <div class="ptabs">
          <button :class="{ on: ptab === 'settings' }" @click="ptab = 'settings'">Settings</button>
          <button :class="{ on: ptab === 'links' }" @click="ptab = 'links'">Links <span class="n mono">{{ site.bookmarks.length }}</span></button>
          <button :class="{ on: ptab === 'logins' }" @click="ptab = 'logins'">Logins
            <span v-if="loginCount" class="n mono">{{ loginCount }}</span></button>
        </div>

        <template v-if="ptab === 'settings'">
          <div class="pbody scroll">
            <div class="psec">
              <div class="plbl">SITE</div>
              <div class="form">
                <span class="k">Homepage</span>
                <input v-model="home" class="in mono" :placeholder="`https://${site.domain}/`"
                       @keydown.enter="($event.target as HTMLInputElement).blur()" @blur="commitHome" />
                <span class="k">Group</span>
                <Combo :model-value="site.group" :suggestions="groupNames" wide lazy
                       placeholder="No group — type a name to create one"
                       @update:model-value="setGroup(site, $event)" />
                <span class="k">In browser</span>
                <button class="tg" :disabled="busy" @click="run(() => api.putSourcePrefs(site!.domain, { frozen: !site!.frozen }))">
                  <span class="toggle" :class="{ on: !site.frozen }"></span>
                  <span class="hint">{{ site.frozen ? 'hidden from the rail — kept here' : 'shown in the rail' }}</span>
                </button>
              </div>
            </div>
            <div class="psec">
              <div class="plbl">RECIPE</div>
              <template v-if="site.hasRecipe">
                <div class="fields">
                  <span class="ver mono">v{{ site.recipeVer }}</span>
                  <span v-for="f in site.fields" :key="f" class="field">{{ f }}</span>
                </div>
                <button class="btn small ghost" :disabled="busy" @click="forgetRecipe(site)">Forget recipe</button>
              </template>
              <div v-else class="hint">Not learned yet — capture a title from this site.</div>
            </div>
            <div class="psec">
              <div class="plbl">BROWSER DATA</div>
              <template v-if="hasShell">
                <div class="dataline">
                  <span class="big">{{ cookies ?? '…' }}</span><span class="hint">cookie{{ cookies === 1 ? '' : 's' }} · plus site storage and cached pages, which Chromium does not measure per site</span>
                </div>
                <div class="acts">
                  <button class="btn small" :disabled="!cookies" @click="clearCookies(site)">Clear cookies</button>
                  <button class="btn small" @click="clearData([site.domain])">Clear site data…</button>
                </div>
              </template>
              <div v-else class="hint">The browser runs in the desktop app.</div>
            </div>
          </div>
          <div class="pfoot">
            <button class="btn ghost small danger" :disabled="busy" @click="removeSites([site.domain])">Remove source…</button>
            <div style="flex:1"></div>
            <span class="hint">{{ site.titles }} title{{ site.titles === 1 ? '' : 's' }} keep their links</span>
          </div>
        </template>

        <SourcesLogins v-else-if="ptab === 'logins'" :domain="site.domain" :homepage="site.homepage"
                       @count="loginCount = $event" />
        <template v-else>
          <div v-if="lsel.size" class="pbulk">
            <span class="sel">{{ lsel.size }} selected</span>
            <div style="flex:1"></div>
            <button class="btn small danger" :disabled="busy" @click="dropLinks([...lsel])">Delete</button>
            <button class="iconbtn plain" title="Clear selection" @click="lsel.clear()"><Icon name="x" :size="12" :sw="2.2" /></button>
          </div>
          <div class="ptool">
            <div class="row">
              <SearchBox v-model="lq" :placeholder="`Find among ${site.bookmarks.length} links…`" />
              <button class="btn small accent" :disabled="busy" @click="startEdit('new')"><Icon name="plus" :size="12" :sw="2.2" />Add</button>
            </div>
            <div class="row">
              <div class="seg text">
                <button class="opt" :class="{ on: lsort === 'order' }" title="The rail's order — drag to change it" @click="lsort = 'order'">Order</button>
                <button class="opt" :class="{ on: lsort === 'name' }" @click="lsort = 'name'">Name</button>
                <button class="opt" :class="{ on: lsort === 'saved' }" @click="lsort = 'saved'">Saved</button>
              </div>
              <div class="seg text">
                <button class="opt" :class="{ on: !ldups }" @click="ldups = false">All</button>
                <button class="opt" :class="{ on: ldups }" @click="ldups = true">Duplicates <span class="mono dim">{{ panelDups }}</span></button>
              </div>
            </div>
          </div>
          <div class="plist scroll" :class="{ selecting: lsel.size }">
            <div v-if="editing === 'new'" class="pedit">
              <input v-model="eName" v-focus class="in" placeholder="Name it — Follows, Latest, a saved search…" />
              <input v-model="eUrl" class="in mono" placeholder="https://…" @keydown.enter="commitEdit" />
              <div class="erow"><button class="btn ghost small" @click="editing = null">Cancel</button>
                <button class="btn accent small" :disabled="busy || !eUrl.trim()" @click="commitEdit">Save</button></div>
            </div>
            <template v-for="r in panelLinks" :key="r.b.url + r.i">
              <div v-if="editing === r.i" class="pedit">
                <input v-model="eName" v-focus class="in" />
                <input v-model="eUrl" class="in mono" @keydown.enter="commitEdit" />
                <div class="erow"><button class="btn ghost small" @click="editing = null">Cancel</button>
                  <button class="btn accent small" :disabled="busy || !eUrl.trim()" @click="commitEdit">Save</button></div>
              </div>
              <div v-else class="prow" :class="{ selected: lsel.has(r.i), drop: linkDrop === r.i }"
                   :draggable="lsort === 'order' && !lq && !ldups && !busy"
                   :title="r.b.url"
                   @dragstart="dragLink = r.i" @dragend="dragLink = linkDrop = null"
                   @dragover.prevent="linkDrop = dragLink === null ? null : r.i" @drop.prevent="onLinkDrop(r.i)"
                   @dblclick="startEdit(r.i)">
                <span class="fbox tick" :class="{ on: lsel.has(r.i) }" @click="lsel.has(r.i) ? lsel.delete(r.i) : lsel.add(r.i)">
                  <Icon v-if="lsel.has(r.i)" name="check" :size="9" :sw="3.2" />
                </span>
                <span class="grip">{{ lsort === 'order' && !lq && !ldups ? '⋮⋮' : '' }}</span>
                <span class="lname">{{ r.b.name }}</span>
                <span class="lurl mono">{{ shortUrl(site, r.b.url) }}</span>
                <span v-if="isDup(r.b)" class="ldup mono" title="The same address is saved more than once">DUP</span>
                <button class="lact" title="Edit" :disabled="busy" @click.stop="startEdit(r.i)"><Icon name="edit" :size="12" :sw="1.9" /></button>
                <button class="lact" title="Delete this link" :disabled="busy" @click.stop="dropLinks([r.i])"><Icon name="x" :size="12" :sw="2.2" /></button>
              </div>
            </template>
            <!-- below the last link: where a link dragged to the end lands -->
            <div v-if="lsort === 'order' && !lq && !ldups && site.bookmarks.length" class="pend"
                 :class="{ drop: linkDrop === site.bookmarks.length }"
                 @dragover.prevent="linkDrop = dragLink === null ? null : site.bookmarks.length"
                 @drop.prevent="onLinkDrop(site.bookmarks.length)"></div>
            <div v-if="!site.bookmarks.length && editing !== 'new'" class="none">
              Nothing saved yet. In the browser, the star in the address bar files the open page here.
            </div>
            <div v-else-if="!panelLinks.length && editing !== 'new'" class="none">No link matches.</div>
          </div>
        </template>
      </aside>
    </div>

    <!-- ================= LINKS ================= -->
    <div v-else class="work">
      <div class="list table scroll">
        <div class="thead mono">
          <span></span><span>NAME</span><span>ADDRESS</span><span>SITE</span><span>GROUP</span><span>SAVED</span><span></span>
        </div>
        <div v-for="row in allLinks" :key="row.key" class="trow" :class="{ selected: tsel.has(row.key), dup: isDup(row.b) }">
          <span class="fbox tick" :class="{ on: tsel.has(row.key) }" @click="tsel.has(row.key) ? tsel.delete(row.key) : tsel.add(row.key)">
            <Icon v-if="tsel.has(row.key)" name="check" :size="9" :sw="3.2" />
          </span>
          <template v-if="tEditing === row.key">
            <input v-model="tName" v-focus class="in cell" @keydown.enter="commitTableEdit(row)" @keydown.esc="tEditing = null" />
            <input v-model="tUrl" class="in cell mono" @keydown.enter="commitTableEdit(row)" @keydown.esc="tEditing = null" />
          </template>
          <template v-else>
            <span class="lname" @dblclick="startTableEdit(row)">{{ row.b.name }}
              <span v-if="isDup(row.b)" class="ldup mono">DUPLICATE</span></span>
            <span class="lurl mono" :title="row.b.url" @dblclick="startTableEdit(row)">{{ row.b.url }}</span>
          </template>
          <span class="tsite" @click="openFromTable(row.s.domain)">{{ row.s.domain }}</span>
          <span class="tdim">{{ row.s.group || 'No group' }}</span>
          <span class="tdim mono">{{ ago(row.b.added) }}</span>
          <span class="tacts">
            <template v-if="tEditing === row.key">
              <button class="btn accent small" :disabled="busy" @click="commitTableEdit(row)">Save</button>
            </template>
            <button v-else class="lact" title="Delete this link" :disabled="busy"
                    @click="saveLinks(row.s, row.s.bookmarks.filter((_, i) => i !== row.i))">
              <Icon name="x" :size="12" :sw="2.2" /></button>
          </span>
        </div>
        <div v-if="!allLinks.length" class="none">{{ linkCount ? 'No link matches.' : 'No links saved yet.' }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.head { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 10px; padding: 0 16px 0 24px; min-width: 0; }
.count { font: 500 12px/1 ui-monospace, monospace; color: var(--tx3); white-space: nowrap; flex: none; }
.tool { height: 44px; flex: none; display: flex; align-items: center; gap: 8px; padding: 0 16px 0 24px; border-bottom: 1px solid var(--line); min-width: 0; }
.tool.bulk { background: var(--accentSoft); }
.sel { font: 600 12px/1 system-ui; color: var(--accent); margin-right: 4px; white-space: nowrap; }
.tool .hint { flex: 1; min-width: 0; text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hint { font: 400 11px/1.4 system-ui; color: var(--tx3); }
.dim { color: var(--tx3); }
.emptyv { padding: 66px 30px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.et { font: 600 15px/1 system-ui; color: var(--tx); }
.es { font: 400 13px/1.5 system-ui; color: var(--tx3); max-width: 48ch; text-align: center; margin-bottom: 4px; }
.none { padding: 14px 24px; font: 400 12px/1.5 system-ui; color: var(--tx3); }

.slbl { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.n { font: 500 11px/1 ui-monospace, monospace; color: var(--tx3); flex: none; }
.railin { padding: 2px 8px; }

.work { flex: 1; min-height: 0; display: flex; }
.list { flex: 1; min-width: 0; padding: 6px 0 20px; }
.tick { cursor: pointer; }
.grip { width: 10px; flex: none; font: 700 11px/1 system-ui; letter-spacing: -2px; color: var(--tx3); opacity: 0; cursor: grab; }

.ghead { display: flex; align-items: center; gap: 8px; height: 34px; padding: 0 16px 0 18px; margin-top: 6px; color: var(--tx3); }
.ghead.named { cursor: grab; }
.ghead:hover .grip { opacity: 1; }
.ghead.drop { color: var(--accent); }
.ghead.drop .grule { background: var(--accent); }
.glbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; white-space: nowrap; }
.grule { flex: 1; height: 1px; background: var(--line); }
.gact { opacity: 0; border: none; background: transparent; font: 600 11px/1 system-ui; color: var(--tx3); padding: 5px 7px; border-radius: 6px; cursor: pointer; }
.ghead:hover .gact { opacity: 1; }
.gact:hover { background: var(--hover); color: var(--tx); }
.in.rename { width: 220px; height: 26px; }
.dropline { height: 2px; background: var(--accent); margin: 0 16px 0 18px; border-radius: 2px; }

.srow { display: flex; align-items: center; gap: 10px; height: 40px; padding: 0 16px 0 16px; cursor: pointer; color: var(--tx2); border-left: 2px solid transparent; }
.srow:hover { background: var(--hover); }
.srow:hover .grip { opacity: 1; }
.srow.on { background: var(--panel); border-left-color: var(--accent); }
.srow.selected { background: var(--accentSoft); }
.srow.cold .sname, .srow.cold .init { opacity: .5; }
.srow .tick { opacity: 0; }
.srow:hover .tick, .srow.selected .tick, .list.selecting .srow .tick { opacity: 1; }
.init { width: 22px; height: 22px; flex: none; border-radius: 6px; display: flex; align-items: center; justify-content: center; font: 700 9px/1 system-ui; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); overflow: hidden; }
.favimg { width: 15px; height: 15px; object-fit: contain; }
.sname { flex: none; font: 600 13px/1 system-ui; color: var(--tx); }
.rmark { flex: none; font: 600 10px/1 ui-monospace, monospace; color: var(--good); border: 1px solid color-mix(in srgb, var(--good) 40%, var(--line)); padding: 3px 6px; border-radius: 4px; }
.rmark.none { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 40%, var(--line)); }
.hidden { flex: none; display: inline-flex; align-items: center; gap: 4px; font-size: 10px; color: var(--tx3); border: 1px solid var(--line); padding: 3px 6px; border-radius: 4px; }
.snum { flex: none; width: 76px; text-align: right; font-size: 11px; color: var(--tx3); }
.slinks { flex: none; width: 52px; display: inline-flex; align-items: center; justify-content: flex-end; gap: 5px; font-size: 11px; color: var(--tx3); }
.slinks.has { color: var(--tx2); }

.pane { width: 500px; flex: none; border-left: 1px solid var(--line); background: var(--bg2); display: flex; flex-direction: column; min-height: 0; }
.phead { height: 44px; flex: none; display: flex; align-items: center; gap: 10px; padding: 0 10px 0 16px; border-bottom: 1px solid var(--line); }
.pname { flex: 1; min-width: 0; font: 600 15px/1 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ptabs { flex: none; display: flex; padding: 0 8px; border-bottom: 1px solid var(--line); }
.ptabs button { height: 40px; border: none; background: transparent; color: var(--tx3); font: 600 12px/1 system-ui; padding: 0 12px; border-bottom: 2px solid transparent; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
.ptabs button:hover { color: var(--tx); }
.ptabs button.on { color: var(--accent); border-bottom-color: var(--accent); }
.pbody { flex: 1; min-height: 0; padding: 4px 16px 16px; }
.psec { padding: 14px 0 6px; }
.psec + .psec { border-top: 1px solid var(--line); }
.plbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); margin-bottom: 10px; }
.form { display: grid; grid-template-columns: 84px minmax(0, 1fr); gap: 9px; align-items: center; }
.form .k { font: 600 11px/1 system-ui; color: var(--tx2); }
.in.mono { font-family: ui-monospace, monospace; font-size: 11px; }
.tg { display: inline-flex; align-items: center; gap: 8px; border: none; background: transparent; padding: 0; cursor: pointer; }
.fields { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 10px; }
.ver { font: 600 10px/1 ui-monospace, monospace; color: var(--good); border: 1px solid color-mix(in srgb, var(--good) 40%, var(--line)); padding: 5px 7px; border-radius: 6px; }
.field { font: 500 11px/1 system-ui; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); padding: 5px 8px; border-radius: 6px; }
.dataline { display: flex; align-items: baseline; gap: 8px; margin-bottom: 10px; }
.big { font: 600 15px/1 system-ui; color: var(--tx); }
.acts { display: flex; gap: 6px; flex-wrap: wrap; }
.pfoot { height: 44px; flex: none; display: flex; align-items: center; gap: 8px; padding: 0 16px; border-top: 1px solid var(--line); }

.pbulk { flex: none; display: flex; align-items: center; gap: 8px; height: 40px; padding: 0 12px; background: var(--accentSoft); border-bottom: 1px solid var(--line); }
.ptool { flex: none; display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.ptool .row { display: flex; align-items: center; gap: 8px; }
.plist { flex: 1; min-height: 0; padding: 4px 6px 8px; }
.prow { display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 8px; border-radius: 6px; color: var(--tx2); }
.prow:hover { background: var(--hover); }
.prow:hover .grip { opacity: 1; }
.prow.selected { background: var(--accentSoft); }
.prow.drop { box-shadow: inset 0 2px 0 var(--accent); }
.prow .tick { opacity: 0; }
.prow:hover .tick, .prow.selected .tick, .plist.selecting .prow .tick { opacity: 1; }
.pend { height: 14px; border-radius: 6px; }
.pend.drop { box-shadow: inset 0 2px 0 var(--accent); }
.lname { flex: none; max-width: 190px; font: 500 12px/1.2 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lurl { flex: 1; min-width: 0; font-size: 11px; color: var(--tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ldup { flex: none; font-size: 9px; font-weight: 700; letter-spacing: .08em; color: var(--warn); border: 1px solid color-mix(in srgb, var(--warn) 45%, var(--line)); padding: 2px 5px; border-radius: 4px; }
.lact { width: 24px; height: 24px; flex: none; border: none; background: transparent; border-radius: 6px; color: var(--tx3); display: inline-flex; align-items: center; justify-content: center; cursor: pointer; opacity: 0; }
.prow:hover .lact, .trow:hover .lact { opacity: 1; }
.lact:hover { background: var(--panel2); color: var(--tx); }
.pedit { display: flex; flex-direction: column; gap: 6px; padding: 8px; margin: 4px 2px; border: 1px solid var(--accent); border-radius: 6px; background: var(--panel); }
.erow { display: flex; gap: 6px; justify-content: flex-end; }

.thead, .trow { display: grid; grid-template-columns: 22px minmax(150px, 1.1fr) minmax(200px, 1.6fr) minmax(130px, 1fr) 110px 64px 70px; align-items: center; gap: 10px; padding: 0 16px 0 24px; }
.list.table { padding-top: 0; }
.thead { height: 32px; font-size: 9px; font-weight: 700; letter-spacing: .12em; color: var(--tx3); border-bottom: 1px solid var(--line); position: sticky; top: 0; background: var(--bg); z-index: 1; }
.trow { height: 36px; color: var(--tx2); border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent); }
.trow:hover { background: var(--hover); }
.trow.selected { background: var(--accentSoft); }
.trow.dup { box-shadow: inset 2px 0 0 var(--warn); }
.trow .lname { max-width: none; display: flex; align-items: center; gap: 6px; cursor: text; }
.trow .lurl { cursor: text; }
.tsite { font: 500 12px/1 system-ui; color: var(--tx2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.tsite:hover { color: var(--accent); }
.tdim { font: 500 11px/1 system-ui; color: var(--tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tacts { display: flex; justify-content: flex-end; }
.in.cell { height: 26px; font-size: 12px; padding: 0 8px; border-color: var(--accent); }
</style>
