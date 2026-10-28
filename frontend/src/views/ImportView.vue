<script lang="ts">
// IMPORT — bring in what is already on disk, sorted the way its owner sorted it
// (design/folder-import.md). A profile says what each level of a folder tree
// means and how names become titles, entries and fields; the preview is the
// server's plan for exactly that, made again on every change and never written;
// Import carries it out as a vault pass.
//
// The session lives here, at module level — the folder read, the profile being
// shaped, this run's choices, the preview: a visit to another view unmounts the
// component, and the work in progress must still be there when the owner is back.
import { reactive, ref } from 'vue'
import {
  blankImportProfile,
  type ImportOverrides, type ImportPlan, type ImportProfile, type ImportReport, type ImportScan,
} from '../data'

const profiles = ref<ImportProfile[]>([])
const profile = reactive<ImportProfile>(blankImportProfile())
const overrides = reactive<ImportOverrides>({ off: [], titles: {}, meta: {} })
const profileErr = ref('')
const scan = ref<ImportScan | null>(null)
const scanning = ref(false)
const scanErr = ref('')
const plan = ref<ImportPlan | null>(null)
const planErr = ref('')
let planSeq = 0
const scope = ref<string | null>(null)
const FILTERS = ['all', 'new', 'skipped', 'problems'] as const
const filter = ref<(typeof FILTERS)[number]>('all')
const folded = ref<Record<string, boolean>>({})
const showIgnored = ref(false)
const runs = ref<ImportReport[]>([])
const report = ref<ImportReport | null>(null)
let opened = false // the first visit picks a profile; a later one finds its own
const sepDraft = ref(' - ') // the title's separator while no two names show it
const advOpen = ref(false)
</script>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import Combo from '../components/Combo.vue'
import FieldVisibility from '../components/FieldVisibility.vue'
import Icon from '../components/Icon.vue'
import ImportMeta from '../components/ImportMeta.vue'
import MenuButton from '../components/MenuButton.vue'
import PassButton from '../components/PassButton.vue'
import RailSection from '../components/RailSection.vue'
import { api } from '../api'
import { goView, refreshLibrary, runVaultPass, stopVaultPass, store, vaultPass, watchVaultPass } from '../store'
import type { ImportEntry, ImportRole, ImportWork } from '../data'
import { readLocal, readLocalOne, writeLocal, writeLocalOne } from '../local'

const PASS = 'import'
watchVaultPass(true)
onBeforeUnmount(() => watchVaultPass(false))

const TABS = ['structure', 'meta', 'files'] as const
const tab = ref<(typeof TABS)[number]>(readLocalOne('lb.importTab', TABS, 'structure'))
watch(tab, (v) => writeLocalOne('lb.importTab', v))

function failed(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T
function size(n: number): string {
  if (n <= 0) return '0 KB'
  if (n >= 2 ** 30) return `${(n / 2 ** 30).toFixed(1)} GB`
  if (n >= 2 ** 20) return `${Math.round(n / 2 ** 20)} MB`
  return `${Math.max(1, Math.round(n / 1024))} KB`
}
const baseName = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() ?? p

// ---- profiles: the shape of a folder tree, kept in the vault ----
function useProfile(p: ImportProfile) {
  Object.assign(profile, clone(p))
  // a profile that leans on patterns or a written template opens with them in sight
  advOpen.value = p.anchor === 'media' || p.levels.some((l) => l.pattern) || p.entryTitle !== '{file}' || !readTitle(p.workTitle)
  resetRun()
}
function newProfile() { useProfile(blankImportProfile()) }

async function saveProfile() {
  profileErr.value = ''
  if (!profile.name.trim()) profile.name = scan.value ? baseName(scan.value.root) : 'Import'
  const list = profiles.value.map((p) => (p.id === profile.id ? clone(profile) : p))
  const at = list.findIndex((p) => p.id === profile.id && p.id)
  try {
    const saved = await api.saveImportProfiles(at >= 0 ? list : [...list, clone(profile)])
    profiles.value = saved
    profile.id = saved[at >= 0 ? at : saved.length - 1].id
  } catch (e) {
    profileErr.value = failed(e)
  }
}
async function deleteProfile() {
  try {
    profiles.value = await api.saveImportProfiles(profiles.value.filter((p) => p.id !== profile.id))
    profile.id = ''
  } catch (e) {
    profileErr.value = failed(e)
  }
}

// ---- the folder: scanned once, planned on every change ----
const canPick = !!window.longbox?.pickFolder
const typedPath = ref('')
// the last folder used with each profile — a fact about THIS machine
const isStrMap = (v: unknown): v is Record<string, string> =>
  typeof v === 'object' && v !== null && !Array.isArray(v) && Object.values(v).every((x) => typeof x === 'string')
const lastRoots = ref(readLocal('lb.importRoots', isStrMap, {}))
const lastRoot = computed(() => lastRoots.value[profile.id || '-'] ?? '')

async function chooseFolder() {
  const picked = canPick ? await window.longbox!.pickFolder('Choose the folder to import') : typedPath.value.trim()
  if (picked) await scanFolder(picked)
}
async function scanFolder(root: string) {
  scanning.value = true
  scanErr.value = ''
  try {
    scan.value = await api.importScan(root)
    resetRun()
    lastRoots.value = { ...lastRoots.value, [profile.id || '-']: root }
    writeLocal('lb.importRoots', lastRoots.value)
    // a profile nobody has shaped yet reads a folder of folders as one work each
    if (!profile.id && profile.levels.length === 1 && scan.value.levels.length > 1) setRole(1, 'work')
  } catch (e) {
    scanErr.value = failed(e)
  } finally {
    scanning.value = false
  }
}
function resetRun() {
  overrides.off = []
  overrides.titles = {}
  overrides.meta = {}
  folded.value = {}
  scope.value = null
  report.value = null
}

let planTimer = 0
watch([() => scan.value?.scanId, () => JSON.stringify(profile), () => JSON.stringify(overrides)], () => {
  window.clearTimeout(planTimer)
  planTimer = window.setTimeout(() => void replan(), 250)
})
async function replan() {
  const s = scan.value
  if (!s) { plan.value = null; return }
  const mine = ++planSeq
  try {
    const p = await api.importPlan(s.scanId, profile, overrides)
    if (mine === planSeq) { plan.value = p; planErr.value = '' }
  } catch (e) {
    if (mine === planSeq) planErr.value = failed(e) // the last good plan stays in sight
  }
}

// ---- structure: what each level means ----
const workAt = computed(() => (profile.anchor === 'level' ? profile.levels.findIndex((l) => l.role === 'work') : -1))
function roleAt(d: number): ImportRole | 'part' {
  if (workAt.value >= 0 && d > workAt.value) return 'part'
  return profile.levels[d]?.role ?? 'collection'
}
function setRole(d: number, role: ImportRole) {
  const lv = profile.levels.map((l) => ({ ...l }))
  while (lv.length <= d) lv.push({ role: 'collection', pattern: '' })
  if (role === 'work') {
    for (const l of lv) if (l.role === 'work') l.role = 'collection'
    lv[d].role = 'work'
    lv.length = d + 1
  } else {
    const wasWork = lv[d].role === 'work'
    lv[d].role = role
    // the work moves one level down rather than vanishing
    if (wasWork && profile.anchor === 'level') lv.push({ role: 'work', pattern: '' })
  }
  profile.levels = lv
}
function setAnchor(a: 'level' | 'media') {
  profile.anchor = a
  if (a === 'media') profile.levels = profile.levels.map((l) => (l.role === 'work' ? { ...l, role: 'collection' } : l))
  else if (!profile.levels.some((l) => l.role === 'work')) setRole(Math.max(0, (scan.value?.levels.length ?? 1) - 1), 'work')
}
function setPattern(d: number, v: string) {
  const lv = profile.levels.map((l) => ({ ...l }))
  while (lv.length <= d) lv.push({ role: 'collection', pattern: '' })
  lv[d].pattern = v
  profile.levels = lv
}
const byLevel = computed(() => profile.anchor === 'level')
const branch = (d: number) => (d ? `${'\u00a0'.repeat((d - 1) * 3)}└ ` : '')
const deeper = computed(() => byLevel.value && (scan.value?.levels.length ?? 0) - 1 > workAt.value)
// moving the work keeps the title's names: the old work's own stays as a level,
// and the new work is always in it
function setWork(d: number) {
  const s = shape.value
  const from = workAt.value
  setRole(d, 'work')
  if (!s) return
  const kept = s.names.map((n) => (n === 'folder' ? from : +n.slice(1))).filter((x) => x >= 0 && x < d)
  writeTitle({ ...s, names: [...new Set(kept)].sort((a, b) => a - b).map((x) => `L${x}`).concat('folder') })
}
const isAuthor = (d: number) => profile.levels[d]?.role === 'creator'
function toggleAuthor(d: number) {
  setRole(d, isAuthor(d) ? 'collection' : 'creator')
  // a tick that fills nothing would look broken
  if (!profile.creatorTo.length && peopleFields.value.length) profile.creatorTo = [peopleFields.value[0].id]
}
const sample = computed<ImportWork | undefined>(() => plan.value?.works[0])
const patternTokens = (d: number) => [...(profile.levels[d]?.pattern ?? '').matchAll(/\{(\w+)\}/g)].map((m) => m[1])
// what a level gives the templates, read off the first work: its whole name,
// the token its role fills, and whatever its pattern cuts out of the name
function gives(d: number): [string, string][] {
  const tok = sample.value?.tokens ?? {}
  const name = tok[`L${d}`]
  if (name === undefined) return []
  const cut = patternTokens(d)
  const out: [string, string][] = [[`L${d}`, name]]
  if (roleAt(d) === 'work') out.push(['folder', name])
  if (roleAt(d) === 'creator' && !cut.includes('creator')) out.push(['creator', name])
  return [...out, ...cut.filter((n) => tok[n] !== undefined).map((n): [string, string] => [n, tok[n]])]
}
const misfit = (d: number) => !!sample.value && patternTokens(d).some((n) => sample.value?.tokens[n] === undefined)
const hasCreator = computed(() => profile.levels.some((l) => l.role === 'creator'))
const peopleFields = computed(() => store.fields.filter((f) => f.group === 'people' && f.type === 'list'))
function toggleCreatorTo(id: string) {
  profile.creatorTo = profile.creatorTo.includes(id) ? profile.creatorTo.filter((f) => f !== id) : [...profile.creatorTo, id]
}
// The work's title as the folder table draws it: some text, then the ticked
// folder names, all joined by one separator. `workTitle` stays the one truth —
// this reads it back, and a template the ticks cannot draw is left to Advanced.
interface TitleShape { before: string; sep: string; names: string[] } // `L1` … `folder`, outermost first
const SEP_AT_END = /\s*[-–—·|/:,]\s*$|\s+$/
function readTitle(tpl: string): TitleShape | null {
  const bits = tpl.split(/\{(\w+)\}/) // literal, name, literal, … literal
  const names = bits.filter((_, i) => i % 2)
  const lits = bits.filter((_, i) => !(i % 2))
  if (!names.length || lits[lits.length - 1]) return null
  if (!names.every((n, i) => (n === 'folder' ? i === names.length - 1 : /^L\d+$/.test(n)))) return null
  const depths = names.filter((n) => n !== 'folder').map((n) => +n.slice(1))
  if (depths.some((x, i) => i && x <= depths[i - 1])) return null
  const lead = lits[0]
  const between = lits.slice(1, -1)
  const sep = between.length ? between[0]
    : lead.length > sepDraft.value.length && lead.endsWith(sepDraft.value) ? sepDraft.value
      : lead.match(SEP_AT_END)?.[0] ?? sepDraft.value
  if (between.some((l) => l !== sep)) return null
  if (lead && !(lead.length > sep.length && lead.endsWith(sep))) return null
  return { before: lead.slice(0, lead.length - (lead ? sep.length : 0)), sep, names }
}
function writeTitle(t: TitleShape) {
  profile.workTitle = [...(t.before ? [t.before] : []), ...t.names.map((n) => `{${n}}`)].join(t.sep)
}
const shape = computed<TitleShape | null>(() => {
  const t = readTitle(profile.workTitle)
  if (!t || !byLevel.value) return t
  // the work's own level, named by its number, is the same tick as {folder}
  const names = t.names.map((n) => (n === `L${workAt.value}` ? 'folder' : n))
  if (new Set(names).size < names.length || names.some((n) => n !== 'folder' && +n.slice(1) > workAt.value)) return null
  return { ...t, names }
})
const tickName = (d: number) => (d === workAt.value ? 'folder' : `L${d}`)
const ticked = (name: string) => !!shape.value?.names.includes(name)
function toggleTitle(name: string) {
  const t = shape.value
  if (!t) return
  const on = t.names.includes(name)
  if (on && t.names.length === 1) return // with no name every work would get the same title
  const at = (n: string) => (n === 'folder' ? Infinity : +n.slice(1))
  writeTitle({ ...t, names: (on ? t.names.filter((n) => n !== name) : [...t.names, name]).sort((a, b) => at(a) - at(b)) })
}
function setBefore(v: string) {
  if (shape.value) writeTitle({ ...shape.value, before: v })
}
function setSep(v: string) {
  const t = shape.value
  sepDraft.value = v
  if (t) writeTitle({ ...t, sep: v })
}
function buildTitle() {
  writeTitle({ before: '', sep: sepDraft.value, names: ['folder'] })
}
const titleParts = computed(() => {
  const t = shape.value
  const tok = sample.value?.tokens
  if (!t || !tok) return []
  return [...(t.before ? [{ text: t.before, fixed: true }] : []),
    ...t.names.map((n) => ({ text: tok[n] ?? `{${n}}`, fixed: false }))]
})
const patternLevels = computed(() => (scan.value?.levels ?? []).filter((l) => !byLevel.value || l.depth <= workAt.value))

const numberHint = computed(() => {
  const missed = plan.value?.works.flatMap((w) => w.warnings).find((w) => w.includes('do not read as'))
  if (missed) return missed
  if (profile.numbering === 'none') {
    return `no numbers — the entry's name is its label, kept ${profile.orderBy === 'date' ? 'oldest file first' : 'in name order'}`
  }
  if (profile.numbering === 'name') return 'every entry reads its number from its name'
  return `1, 2, 3 through each work — ${profile.orderBy === 'date' ? 'oldest file first' : 'in natural name order'}`
})

// ---- metadata: for every work, or for one ----
const scoped = computed(() => plan.value?.works.find((w) => w.key === scope.value))
const metaFields = computed(() => store.fields.filter((f) => f.editable && f.id !== 'title' && f.control !== 'cover'))
const offered = computed(() => metaFields.value.filter((f) => !profile.hidden.includes(f.id)))
const notOffered = computed(() => metaFields.value.filter((f) => profile.hidden.includes(f.id)))
function setHidden(id: string, hide: boolean) {
  profile.hidden = hide ? [...profile.hidden, id] : profile.hidden.filter((f) => f !== id)
}
const locked = computed(() =>
  hasCreator.value ? Object.fromEntries(profile.creatorTo.map((f) => [f, `L${profile.levels.findIndex((l) => l.role === 'creator')}`])) : {})
function scopeTo(key: string) {
  scope.value = scope.value === key ? null : key
  if (scope.value) {
    overrides.meta[scope.value] ??= {}
    tab.value = 'meta'
  }
}
const ownTitle = computed({
  get: () => (scope.value ? overrides.titles[scope.value] ?? '' : ''),
  set: (v: string) => {
    if (!scope.value) return
    if (v.trim()) overrides.titles[scope.value] = v
    else delete overrides.titles[scope.value]
  },
})

// ---- the preview ----
const STATE_LABEL: Record<ImportEntry['state'], string> = {
  new: 'new', exists: 'in library', replace: 'replace', conflict: 'conflict',
  unreadable: 'unreadable', off: 'left out', skip: 'skipped',
}
function shown(e: ImportEntry): boolean {
  if (filter.value === 'new') return e.state === 'new' || e.state === 'replace'
  if (filter.value === 'skipped') return e.state === 'exists' || e.state === 'skip' || e.state === 'off'
  if (filter.value === 'problems') return e.state === 'conflict' || e.state === 'unreadable'
  return true
}
const going = (w: ImportWork) => w.entries.filter((e) => e.state === 'new' || e.state === 'replace')
function badge(w: ImportWork): { text: string; cls: string } {
  if (w.action === 'add') return { text: `ADDS ${going(w).length}`, cls: 'add' }
  if (w.action === 'separate') return { text: 'NEW · SAME NAME AS A TITLE YOU HAVE', cls: 'warn' }
  if (w.action === 'skip') return { text: 'SKIPPED · IN LIBRARY', cls: 'skip' }
  if (w.action === 'conflict') return { text: 'CONFLICT', cls: 'bad' }
  if (w.action === 'off') return { text: 'LEFT OUT', cls: 'skip' }
  return { text: 'NEW', cls: 'new' }
}
function metaLine(w: ImportWork): string {
  return Object.entries(w.meta).map(([k, v]) => `${k} ${Array.isArray(v) ? v.join(', ') : v}`).join(' · ')
}
const opensSection = (w: ImportWork, i: number) =>
  !!w.entries[i].section && (i === 0 || w.entries[i - 1].section !== w.entries[i].section)
function toggleOff(key: string) {
  overrides.off = overrides.off.includes(key) ? overrides.off.filter((k) => k !== key) : [...overrides.off, key]
}
// a work's entries fold away; leaving a work out folds it, bringing it back opens it
function fold(key: string, shut: boolean) {
  if (shut) folded.value[key] = true
  else delete folded.value[key]
}
function toggleWork(key: string) {
  toggleOff(key)
  fold(key, overrides.off.includes(key))
}
const allFolded = computed(() => !!plan.value?.works.length && plan.value.works.every((w) => folded.value[w.key]))
function foldAll() {
  const shut = !allFolded.value
  for (const w of plan.value?.works ?? []) fold(w.key, shut)
}
const canToggle = (e: ImportEntry) => ['new', 'replace', 'off'].includes(e.state)
const skippedCount = computed(() => plan.value?.works.reduce((n, w) => n + w.entries.length - going(w).length, 0) ?? 0)

// ---- the run ----
// a work the library already has takes the profile's metadata even when no
// entry of it is new — running again is how a changed profile reaches them
const extending = computed(() => plan.value?.works.filter((w) => w.action === 'add').length ?? 0)
const runnable = computed(() => !!plan.value && (plan.value.entries > 0 || extending.value > 0))
const runLabel = computed(() => (plan.value?.entries || !extending.value
  ? `Import ${plan.value?.entries ?? 0} entries`
  : `Update ${extending.value} work${extending.value === 1 ? '' : 's'}`))
async function runImport() {
  // the run carries its own target: a folder chosen again mid-run must not
  // change what this one stores, or where the next plan comes from
  const s = scan.value
  if (!s || !runnable.value) return
  const body = { profile: clone(profile), overrides: clone(overrides) }
  await runVaultPass(async () => {
    report.value = await api.importRun(s.scanId, body.profile, body.overrides)
    runs.value = await api.importRuns()
  })
  void refreshLibrary()
  if (body.profile.files === 'move') await scanFolder(s.root)
  else await replan()
}
function openLibrary() { goView('library') }

onMounted(async () => {
  // back from another view: the library may have changed under the preview
  if (scan.value) void replan()
  try {
    profiles.value = await api.importProfiles()
    if (!opened) {
      opened = true
      if (profiles.value.length) useProfile(profiles.value[0])
    }
  } catch (e) {
    profileErr.value = failed(e)
  }
  try { runs.value = await api.importRuns() } catch { /* a list of last runs is not worth a failed view */ }
})
</script>

<template>
  <div class="imp">
    <Teleport to="#siderail">
      <RailSection label="PROFILES">
        <button v-for="p in profiles" :key="p.id" :class="{ on: p.id === profile.id }" @click="useProfile(p)">
          <span class="slbl">{{ p.name || 'Unnamed' }}</span>
        </button>
        <button class="add" @click="newProfile"><Icon name="plus" :size="12" :sw="2.2" />New profile</button>
      </RailSection>
      <RailSection v-if="runs.length" label="LAST RUNS">
        <button v-for="r in runs" :key="r.at + r.root" :class="{ on: report === r }"
                :title="`${r.root} — ${r.at.slice(0, 10)}`" @click="report = r">
          <span class="slbl">{{ baseName(r.root) }}</span>
          <span class="n mono">{{ r.imported + r.replaced }}<template v-if="r.failed.length"> · {{ r.failed.length }}✕</template></span>
        </button>
      </RailSection>
    </Teleport>

    <div class="col">
      <div class="head">
        <h1 class="h1">Import</h1>
        <span v-if="scan" class="path mono" :title="scan.root">{{ scan.root }}
          <span class="dim">· {{ scan.counts.folders ?? 0 }} folders · {{ (scan.counts.video ?? 0) + (scan.counts.archive ?? 0) + (scan.counts.image ?? 0) }} files</span></span>
        <button class="btn small" :disabled="scanning" @click="chooseFolder">{{ scan ? 'Change…' : 'Choose a folder…' }}</button>
        <div style="flex:1"></div>
        <span class="eyebrow">PROFILE</span>
        <input v-model="profile.name" class="in pname" placeholder="name this profile" spellcheck="false" />
        <button class="btn" @click="saveProfile">Save profile</button>
        <button v-if="profile.id" class="btn ghost" title="Delete this profile from the library" @click="deleteProfile">Delete</button>
      </div>
      <div v-if="profileErr || scanErr" class="errline">{{ profileErr || scanErr }}</div>

      <div class="work">
        <!-- the rules, in three tabs -->
        <div class="rcol">
          <div class="rtabs">
            <div class="seg">
              <button class="opt" :class="{ on: tab === 'structure' }" @click="tab = 'structure'">Structure</button>
              <button class="opt" :class="{ on: tab === 'meta' }" @click="tab = 'meta'">Metadata</button>
              <button class="opt" :class="{ on: tab === 'files' }" @click="tab = 'files'">Files</button>
            </div>
          </div>

          <div class="rbody scroll">
            <template v-if="tab === 'structure'">
              <div class="rsec">
                <div class="lbl">FOLDERS<span>each row is one level of the tree</span></div>
                <div v-if="!scan" class="hint">Choose a folder: each level of it gets a row here, with the names found at it.</div>
                <div v-else class="ftab">
                  <div class="fhead"><span></span><span>FOLDER</span><span>TITLE</span><span>AUTHOR</span><span>{{ byLevel ? 'WORK' : '' }}</span></div>
                  <div v-for="lv in scan.levels" :key="lv.depth" class="frow"
                       :class="{ wk: lv.depth === workAt, below: byLevel && lv.depth > workAt }">
                    <span class="d mono">L{{ lv.depth }}</span>
                    <span class="nm" :title="lv.names.join(' · ')"><span class="tw mono">{{ branch(lv.depth) }}</span><b>{{ lv.names[0] }}</b><small v-if="lv.folders > 1">· {{ lv.folders - 1 }} more</small></span>
                    <span class="c"><button v-if="!byLevel || lv.depth <= workAt" class="box" title="Its name goes into the work's title"
                                            @click="toggleTitle(tickName(lv.depth))">
                      <span class="fbox" :class="{ on: ticked(tickName(lv.depth)) }"><Icon v-if="ticked(tickName(lv.depth))" name="check" :size="9" :sw="3.2" /></span></button></span>
                    <span class="c"><button v-if="!byLevel || lv.depth < workAt" class="box" title="Its name is the creator"
                                            @click="toggleAuthor(lv.depth)">
                      <span class="fbox au" :class="{ on: isAuthor(lv.depth) }"><Icon v-if="isAuthor(lv.depth)" name="check" :size="9" :sw="3.2" /></span></button></span>
                    <span class="c"><button v-if="byLevel" class="box" title="Each folder at this level is one work"
                                            @click="setWork(lv.depth)"><span class="rdot" :class="{ on: lv.depth === workAt }"></span></button></span>
                  </div>
                  <div class="frow last">
                    <span></span>
                    <span class="nm" :class="{ span: byLevel }"><span class="tw mono">{{ branch(byLevel ? workAt + 1 : scan.levels.length) }}</span>
                      <template v-if="!byLevel">the folder holding the files → <span class="tag">THE WORK</span></template>
                      <template v-else-if="deeper">folders inside a work → its <span class="tag">SECTIONS</span>; a folder of images is one entry</template>
                      <template v-else>files inside a work → its entries</template></span>
                    <span v-if="!byLevel" class="c"><button class="box" title="Its name goes into the work's title" @click="toggleTitle('folder')">
                      <span class="fbox" :class="{ on: ticked('folder') }"><Icon v-if="ticked('folder')" name="check" :size="9" :sw="3.2" /></span></button></span>
                  </div>
                </div>
                <div v-if="hasCreator" class="goes"><span class="k">Author goes to</span>
                  <button v-for="f in peopleFields" :key="f.id" class="chip tog" :class="{ on: profile.creatorTo.includes(f.id) }"
                          @click="toggleCreatorTo(f.id)">{{ f.label }}</button></div>
                <div v-else-if="scan" class="hint">No author from folders — type one in Metadata, or tick AUTHOR on the level
                  that names the creator.</div>
              </div>

              <div class="rsec">
                <div class="lbl">WORK TITLE<span>{{ shape ? 'the ticked names, in order' : 'a template' }}</span></div>
                <div v-if="shape" class="row2">
                  <span class="k">Text before</span>
                  <input class="in" :value="shape.before" placeholder="nothing" spellcheck="false"
                         @input="setBefore(($event.target as HTMLInputElement).value)" />
                  <span class="k">Separator</span>
                  <input class="in mono sep" :value="shape.sep" spellcheck="false" @input="setSep(($event.target as HTMLInputElement).value)" />
                  <span class="k">Gives</span>
                  <div class="result">
                    <template v-for="(p, i) in titleParts" :key="i"><span v-if="i" class="s">{{ shape.sep }}</span><span :class="p.fixed ? 'x' : 'p'">{{ p.text }}</span></template>
                    <span v-if="!titleParts.length" class="s">choose a folder to see it</span>
                  </div>
                </div>
                <template v-else>
                  <div class="hint">The title is a template — <span class="mono">{{ profile.workTitle }}</span> — written under Advanced.</div>
                  <div v-if="plan?.works.length" class="out">→ <b>{{ plan.works[0].title }}</b></div>
                  <button class="btn small fit" @click="buildTitle">Build it from the folder names</button>
                </template>
              </div>

              <div class="rsec">
                <div class="lbl">ENTRIES</div>
                <div class="row2">
                  <span class="k">Name</span>
                  <div class="line">
                    <span v-if="profile.entryTitle === '{file}'" class="out">the file's name</span>
                    <span v-else class="out mono" title="A template — change it under Advanced">{{ profile.entryTitle }}</span>
                    <button class="check" @click="profile.dropTags = !profile.dropTags">
                      <span class="fbox" :class="{ on: profile.dropTags }"><Icon v-if="profile.dropTags" name="check" :size="9" :sw="3.2" /></span>drop [tags]</button>
                    <button class="check" @click="profile.underscores = !profile.underscores">
                      <span class="fbox" :class="{ on: profile.underscores }"><Icon v-if="profile.underscores" name="check" :size="9" :sw="3.2" /></span>_ → space</button>
                  </div>
                  <span class="k">Number</span>
                  <div class="stack">
                    <div class="line">
                      <div class="seg text">
                        <button class="opt" :class="{ on: profile.numbering === 'order' }" @click="profile.numbering = 'order'">In order</button>
                        <button class="opt" :class="{ on: profile.numbering === 'name' }" @click="profile.numbering = 'name'">From the name</button>
                        <button class="opt" :class="{ on: profile.numbering === 'none' }" @click="profile.numbering = 'none'">None</button>
                      </div>
                      <div class="seg text">
                        <button class="opt" :class="{ on: profile.orderBy === 'name' }" @click="profile.orderBy = 'name'">by name</button>
                        <button class="opt" :class="{ on: profile.orderBy === 'date' }" @click="profile.orderBy = 'date'">by file date</button>
                      </div>
                    </div>
                    <input v-if="profile.numbering === 'name'" v-model="profile.numPattern" class="in mono"
                           placeholder="Ch. {n}" spellcheck="false" />
                    <div class="out">{{ numberHint }}</div>
                  </div>
                </div>
              </div>

              <button class="adv" @click="advOpen = !advOpen">
                <Icon name="chevron" :size="10" :sw="2.4" :style="{ transform: advOpen ? '' : 'rotate(-90deg)' }" />Advanced
                <small>· name patterns, templates, uneven depth</small></button>
              <template v-if="advOpen">
                <div class="rsec">
                  <div class="lbl">THE WORK IS</div>
                  <div class="seg text">
                    <button class="opt" :class="{ on: byLevel }" @click="setAnchor('level')">a level (above)</button>
                    <button class="opt" :class="{ on: !byLevel }" @click="setAnchor('media')">the folder holding the files, at any depth</button>
                  </div>
                </div>
                <div class="rsec">
                  <div class="lbl">NAME PATTERNS<span>cut a folder name into parts</span></div>
                  <template v-for="lv in patternLevels" :key="lv.depth">
                    <div class="prow"><span class="d mono">L{{ lv.depth }}</span><span class="pn" :title="lv.names[0]">{{ lv.names[0] }}</span>
                      <input class="in mono" :value="profile.levels[lv.depth]?.pattern ?? ''" spellcheck="false"
                             placeholder="none — the whole name" @input="setPattern(lv.depth, ($event.target as HTMLInputElement).value)" /></div>
                    <div v-if="patternTokens(lv.depth).length" class="chips pgives">
                      <span v-for="[k, v] in gives(lv.depth)" :key="k" class="chip"><span class="ck">{{ '{' + k + '}' }}</span>{{ v }}</span>
                      <span v-if="misfit(lv.depth)" class="miss">does not fit the pattern — the whole name is used</span></div>
                  </template>
                  <div class="hint"><span class="mono">{site} @{handle} {creator}</span> reads
                    <span class="mono">vidhub @kumo Kumo Works</span> as three parts for templates and metadata values. Text
                    between them must be in the name; the last one takes the rest.</div>
                </div>
                <div class="rsec">
                  <div class="lbl">TEMPLATES</div>
                  <div class="row2">
                    <span class="k">Work title</span><input v-model="profile.workTitle" class="in mono" spellcheck="false" />
                    <span class="k">Entry name</span><input v-model="profile.entryTitle" class="in mono" spellcheck="false" />
                  </div>
                  <div class="hint">Tokens: <span class="mono">{L0} {L1}… {folder} {creator}</span>, a pattern's parts, and for
                    entries <span class="mono">{file} {n} {part} {date}</span>.</div>
                </div>
              </template>
            </template>

            <template v-else-if="tab === 'meta'">
              <div class="rsec">
                <div class="lbl">APPLIES TO</div>
                <div class="scopebar">
                  <button class="scope" :class="{ on: !scoped }" @click="scope = null">Every work
                    <span class="mono dim">{{ plan?.works.length ?? 0 }}</span></button>
                  <span v-if="scoped" class="scope on">{{ scoped.title }}<button class="x" title="Back to every work" @click="scope = null">×</button></span>
                  <div style="flex:1"></div>
                  <MenuButton icon="settings" title="Which fields this profile offers" :width="290">
                    <FieldVisibility :title="`FIELDS · ${(profile.name.trim() || 'this profile').toUpperCase()}`"
                                     :fields="offered" :hidden="notOffered"
                                     note="This profile's setting. The import leaves a hidden field alone — a value it holds waits until the field is shown again."
                                     @set="setHidden" />
                  </MenuButton>
                </div>
                <div class="hint">{{ scoped
                  ? 'Values for every work show dim. What you add here is this work\'s own: a list gains it, a single field is replaced.'
                  : 'A filled field goes to every work; an empty one is left alone. Click a work\'s name in the preview to give it values of its own.' }}</div>
                <div v-if="scoped" class="fieldrow">
                  <span class="flbl">TITLE · THIS WORK</span>
                  <input v-model="ownTitle" class="in" :placeholder="scoped.title" spellcheck="false" />
                </div>
              </div>
              <div class="rsec">
                <ImportMeta v-if="scoped && overrides.meta[scoped.key]" v-model="overrides.meta[scoped.key]"
                            :fields="offered" :inherited="profile.meta" :locked="locked" />
                <ImportMeta v-else v-model="profile.meta" :fields="offered" :locked="locked" />
                <template v-if="!scoped">
                  <div class="blocklbl"><span>EVERY ENTRY</span><i class="rule"></i></div>
                  <div class="fieldrow"><span class="flbl">LANGUAGE</span>
                    <Combo v-model="profile.language" :suggestions="store.vocab.language ?? []" wide placeholder="leave as it is" /></div>
                  <div class="fieldrow"><span class="flbl">GROUP</span>
                    <Combo v-model="profile.group" :suggestions="store.vocab.group ?? []" wide placeholder="leave as it is" /></div>
                  <div class="fieldrow"><span class="flbl">DATE</span>
                    <div class="seg text">
                      <button class="opt" :class="{ on: profile.entryDate }" @click="profile.entryDate = true">the file's date</button>
                      <button class="opt" :class="{ on: !profile.entryDate }" @click="profile.entryDate = false">none</button>
                    </div></div>
                </template>
                <div class="hint" style="margin-top:14px">Any value can hold a token — <span class="mono">{site} {creator}
                  {folder}</span> — filled in per work. Values arrive as <b>automatic</b> and pass through your
                  <b>rules</b>; for a work you already have they fill what is empty or automatic and add to lists —
                  what you typed by hand is never touched.</div>
              </div>
            </template>

            <template v-else>
              <div class="rsec">
                <div class="lbl">FILES &amp; WHAT EXISTS</div>
                <div class="row2">
                  <span class="k">Files</span>
                  <div class="stack">
                    <div class="seg text">
                      <button class="opt" :class="{ on: profile.files === 'copy' }" @click="profile.files = 'copy'">Copy</button>
                      <button class="opt" :class="{ on: profile.files === 'move' }" @click="profile.files = 'move'">Move</button>
                    </div>
                    <div class="out">{{ profile.files === 'copy'
                      ? 'the library gets its own copy; the originals stay where they are'
                      : 'each original goes once its entry is stored; on the library\'s own drive nothing is copied' }}</div>
                  </div>
                  <span class="k">Work exists</span>
                  <div class="seg text">
                    <button class="opt" :class="{ on: profile.workExists === 'add' }" @click="profile.workExists = 'add'">Add what's missing</button>
                    <button class="opt" :class="{ on: profile.workExists === 'separate' }" @click="profile.workExists = 'separate'">Separate work</button>
                    <button class="opt" :class="{ on: profile.workExists === 'skip' }" @click="profile.workExists = 'skip'">Skip</button>
                  </div>
                  <span class="k">Entry exists</span>
                  <div class="seg text">
                    <button class="opt" :class="{ on: profile.entryExists === 'skip' }" @click="profile.entryExists = 'skip'">Skip</button>
                    <button class="opt" :class="{ on: profile.entryExists === 'replace' }" @click="profile.entryExists = 'replace'">Replace media</button>
                  </div>
                </div>
                <button class="check" @click="profile.cover = !profile.cover">
                  <span class="fbox" :class="{ on: profile.cover }"><Icon v-if="profile.cover" name="check" :size="9" :sw="3.2" /></span>an
                  image named cover / folder / poster becomes the cover</button>
              </div>
            </template>
          </div>
        </div>

        <!-- the preview -->
        <div class="prev">
          <div class="ptool">
            <span class="pt">Preview</span>
            <span v-if="plan" class="sum mono">{{ plan.works.length }} work{{ plan.works.length === 1 ? '' : 's' }}
              · {{ plan.entries }} to import · {{ skippedCount }} skipped · {{ size(plan.bytes) }}</span>
            <div style="flex:1"></div>
            <button v-if="plan?.works.length" class="btn ghost small" @click="foldAll">{{ allFolded ? 'Expand all' : 'Collapse all' }}</button>
            <div class="seg text">
              <button v-for="f in FILTERS" :key="f" class="opt" :class="{ on: filter === f }" @click="filter = f">
                {{ f[0].toUpperCase() + f.slice(1) }}</button>
            </div>
          </div>

          <div class="pbody scroll">
            <div v-if="report" class="report">
              <div class="rp1">
                <span class="big">{{ report.imported + report.replaced }}</span><span class="s">stored in {{ report.works.length }} work{{ report.works.length === 1 ? '' : 's' }}</span>
                <span class="big dim">{{ report.skipped }}</span><span class="s">skipped</span>
                <span class="big" :class="{ bad: report.failed.length }">{{ report.failed.length }}</span><span class="s">failed</span>
                <span v-if="report.stopped" class="s warn">stopped before the end</span>
                <button class="x" title="Close the report" @click="report = null">×</button>
              </div>
              <div v-for="w in report.works" :key="w.titleId" class="rpl"><span class="dot"></span>
                <span><b>{{ w.title }}</b> — {{ w.created ? 'new' : 'already there' }}, {{ w.added }} added</span></div>
              <div v-for="f in report.failed" :key="f.path" class="rpl"><span class="dot b"></span>
                <span><b>{{ f.path }}</b> — {{ f.reason }}</span></div>
              <div v-if="report.kept" class="rpl"><span class="dot w"></span>
                <span>{{ report.kept }} original{{ report.kept === 1 ? '' : 's' }} could not be deleted after the move — the copies are stored.</span></div>
              <div class="rpfoot"><button class="btn small" @click="openLibrary">Open the library</button>
                <span class="hint">{{ report.files === 'copy' ? 'Copy — the originals are where they were.' : 'Move — stored entries left the folder.' }}</span></div>
            </div>

            <div v-if="!scan" class="emptyv">
              <Icon name="import" :size="28" style="color:var(--tx3)" />
              <div class="et">Bring in media you already have</div>
              <div class="es">Choose a folder. Each level of it gets a meaning — a collection, a creator, a work —
                and the preview shows exactly what will be created before anything is written.</div>
              <div v-if="!canPick" class="pathin">
                <input v-model="typedPath" class="in mono" placeholder="D:\Media\Collection" spellcheck="false" @keydown.enter="chooseFolder" />
              </div>
              <button class="btn accent" :disabled="scanning" @click="chooseFolder">{{ scanning ? 'Reading…' : 'Choose a folder…' }}</button>
              <button v-if="lastRoot" class="lnk" @click="scanFolder(lastRoot)">or {{ lastRoot }} again</button>
            </div>

            <div v-if="planErr" class="errline">{{ planErr }}</div>
            <div v-for="w in plan?.warnings ?? []" :key="w" class="warnline">{{ w }}</div>

            <template v-for="w in plan?.works ?? []" :key="w.key">
              <div class="wrow" :class="{ sel: scope === w.key }">
                <button class="box fold" :title="folded[w.key] ? 'Show its entries' : 'Hide its entries'"
                        @click="fold(w.key, !folded[w.key])">
                  <Icon name="chevron" :size="10" :sw="2.4" :style="{ transform: folded[w.key] ? 'rotate(-90deg)' : '' }" /></button>
                <button class="box" :title="w.action === 'off' ? 'Bring this work back into the run' : 'Leave this work out of the run'"
                        @click="toggleWork(w.key)"><span class="fbox" :class="{ on: w.action !== 'off' }">
                  <Icon v-if="w.action !== 'off'" name="check" :size="9" :sw="3.2" /></span></button>
                <div class="tt">
                  <button class="name" title="Give this work values of its own" @click="scopeTo(w.key)">{{ w.title }}</button>
                  <span class="badge" :class="badge(w).cls">{{ badge(w).text }}</span>
                  <span v-if="w.own || overrides.titles[w.key]" class="badge own">OWN VALUES</span>
                  <span class="meta">{{ metaLine(w) }}</span>
                </div>
                <span class="cnt mono">{{ going(w).length }} of {{ w.entries.length }} · {{ size(going(w).reduce((n, e) => n + e.size, 0)) }}</span>
              </div>
              <div v-for="m in w.warnings" :key="m" class="warnline sub">{{ m }}</div>
              <div v-if="w.reason" class="warnline sub">{{ w.reason }}</div>
              <template v-for="(e, i) in folded[w.key] ? [] : w.entries" :key="e.path">
                <div v-if="opensSection(w, i) && shown(e)" class="secrow mono">SECTION <b>{{ e.section }}</b></div>
                <div v-if="shown(e)" class="erow" :class="{ out: e.state === 'off', dim: ['exists', 'skip'].includes(e.state) }" :title="e.reason || e.path">
                  <button class="box" :disabled="!canToggle(e)" @click="toggleOff(e.path)">
                    <span class="fbox" :class="{ on: e.state === 'new' || e.state === 'replace' }">
                      <Icon v-if="e.state === 'new' || e.state === 'replace'" name="check" :size="9" :sw="3.2" /></span></button>
                  <span class="num mono">{{ e.title ? e.num : '—' }}</span>
                  <Icon :name="e.kind === 'video' ? 'film' : 'image'" :size="12" class="kind" />
                  <span class="nm">{{ e.title || e.num || e.path }}</span>
                  <span class="dt mono">{{ e.date }}</span>
                  <span class="sz mono">{{ size(e.size) }}</span>
                  <span class="st mono" :class="e.state">{{ STATE_LABEL[e.state] }}</span>
                </div>
              </template>
            </template>

            <div v-if="plan?.ignoredCount" class="ignored">
              <button class="lnk" @click="showIgnored = !showIgnored">{{ plan.ignoredCount }} file{{ plan.ignoredCount === 1 ? '' : 's' }}
                left out — {{ showIgnored ? 'hide' : 'show' }}</button>
              <template v-if="showIgnored">
                <div v-for="g in plan.ignored" :key="g.path" class="igrow mono">{{ g.path }} <span class="dim">— {{ g.reason }}</span></div>
                <div v-if="plan.ignoredCount > plan.ignored.length" class="igrow dim">…and {{ plan.ignoredCount - plan.ignored.length }} more</div>
              </template>
            </div>
          </div>
        </div>
      </div>

      <div class="lfoot">
        <span class="lfinfo">
          <template v-if="vaultPass.key === PASS && vaultPass.detail">{{ vaultPass.detail }}</template>
          <template v-else-if="plan"><b>{{ profile.files === 'copy' ? 'Copy' : 'Move' }}</b> {{ size(plan.bytes) }} into the library
            · {{ profile.files === 'copy' ? 'the originals stay where they are' : 'each original goes once its entry is stored' }}</template>
        </span>
        <div style="flex:1"></div>
        <PassButton :op="PASS" :active="vaultPass.key" :done="vaultPass.done" :total="vaultPass.total"
                    :running="vaultPass.op" :label="runLabel" icon="import" accent
                    :disabled="!runnable || !!planErr" @run="runImport()" @stop="stopVaultPass()" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.imp { display: flex; height: 100%; overflow: hidden; }
.col { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.head { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 12px; padding: 0 16px 0 24px; }
.path { min-width: 0; max-width: 46%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; color: var(--tx2); }
.pname { width: 190px; height: 30px; padding: 0 10px; }
.dim { color: var(--tx3); }
.errline { padding: 9px 24px; font: 500 12px/1.45 system-ui; color: var(--adult); border-bottom: 1px solid var(--line); }
.warnline { padding: 6px 24px; font: 400 11.5px/1.45 system-ui; color: var(--warn); }
.warnline.sub { padding-left: 64px; }
.work { flex: 1; min-height: 0; display: flex; }

/* ---- the rules ---- */
.rcol { width: 392px; flex: none; border-right: 1px solid var(--line); background: var(--bg2); display: flex; flex-direction: column; }
.rtabs { height: 44px; flex: none; display: flex; align-items: center; padding: 0 16px; border-bottom: 1px solid var(--line); }
.rbody { flex: 1; min-height: 0; overflow-y: auto; }
.rsec { padding: 14px 16px 16px; border-bottom: 1px solid var(--line); display: flex; flex-direction: column; gap: 10px; }
.lbl { font: 700 9.5px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); display: flex; align-items: center; gap: 8px; }
.lbl span { margin-left: auto; letter-spacing: 0; font-weight: 500; }
.hint { font: 400 11.5px/1.45 system-ui; color: var(--tx3); }
.hint b { color: var(--tx2); font-weight: 600; }
.ftab { border: 1px solid var(--line); border-radius: 10px; background: var(--panel); overflow: hidden; }
.fhead, .frow { display: grid; grid-template-columns: 22px minmax(0, 1fr) 40px 48px 40px; gap: 6px; align-items: center; padding: 0 10px; }
.fhead { height: 26px; border-bottom: 1px solid var(--line); font: 700 8.5px/1 ui-monospace, monospace; letter-spacing: .1em; color: var(--tx3); }
.fhead span:nth-child(n+3) { text-align: center; }
.frow { height: 36px; border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent); }
.frow:last-child { border-bottom: none; }
.frow .d { font-size: 10px; font-weight: 700; color: var(--tx3); }
.frow .nm { min-width: 0; display: flex; align-items: baseline; gap: 6px; font: 500 12.5px/1.2 system-ui; white-space: nowrap; overflow: hidden; }
.frow .nm b { font-weight: 500; overflow: hidden; text-overflow: ellipsis; }
.frow .nm small { color: var(--tx3); font-size: 11px; flex: none; }
.frow .c { display: flex; justify-content: center; }
.frow.wk { background: var(--accentSoft); }
.frow.wk .nm b { color: var(--accent); font-weight: 600; }
.frow.below .nm { color: var(--tx3); }
.frow.last { height: 32px; }
.frow.last .nm { color: var(--tx3); font-size: 11.5px; }
.frow.last .nm.span { grid-column: 2 / -1; }
.tw { color: var(--tx3); font-size: 11px; flex: none; }
.tag { font: 700 8.5px/1 ui-monospace, monospace; letter-spacing: .1em; color: var(--warn); }
.fbox.au.on { background: var(--fav); border-color: var(--fav); }
.rdot { width: 14px; height: 14px; border-radius: 999px; border: 1.5px solid var(--tx3); display: inline-flex; align-items: center; justify-content: center; }
.rdot.on { border-color: var(--accent); }
.rdot.on::after { content: ''; width: 6px; height: 6px; border-radius: 999px; background: var(--accent); }
.goes { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.goes .k { font: 600 11px/1 system-ui; color: var(--tx3); margin-right: 2px; }
.result { display: flex; align-items: center; flex-wrap: wrap; min-height: 30px; min-width: 0; padding: 4px 10px; border-radius: 6px; background: var(--panel); border: 1px dashed var(--line); font: 600 12.5px/1.4 system-ui; }
.result .p { color: var(--good); }
.result .s { color: var(--tx3); white-space: pre; }
.result .x { color: var(--tx2); }
.in.sep { width: 80px; }
.fit { width: fit-content; }
.adv { display: flex; align-items: center; gap: 8px; width: 100%; height: 44px; padding: 0 16px; border: none; border-bottom: 1px solid var(--line); background: transparent; font: 600 12px/1 system-ui; color: var(--tx2); cursor: pointer; text-align: left; }
.adv small { font: 400 11px/1 system-ui; color: var(--tx3); }
.prow { display: grid; grid-template-columns: 22px 84px minmax(0, 1fr); gap: 8px; align-items: center; }
.prow .d { font-size: 10px; font-weight: 700; color: var(--tx3); }
.prow .pn { font: 500 12px/1 system-ui; color: var(--tx2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pgives { padding-left: 30px; }
.row2 .k { font: 600 11px/1.2 system-ui; color: var(--tx3); }
.row2 { display: grid; grid-template-columns: 86px minmax(0, 1fr); gap: 8px 10px; align-items: center; }
.row2 .k { color: var(--tx2); }
.stack { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.out { font: 500 11.5px/1.35 system-ui; color: var(--tx3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.out b { color: var(--good); font-weight: 600; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
.chip { display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 8px; border-radius: 999px; background: var(--panel2); border: 1px solid var(--line); font: 500 11.5px/1 system-ui; color: var(--tx); white-space: nowrap; }
.chip .ck { font: 500 9.5px/1 ui-monospace, monospace; color: var(--tx3); }
.chip.tog { cursor: pointer; color: var(--tx3); }
.chip.tog.on { background: var(--accentSoft); border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); color: var(--accent); }
.check { display: flex; gap: 8px; align-items: center; border: none; background: transparent; padding: 0; text-align: left; font: 500 12px/1.3 system-ui; color: var(--tx2); cursor: pointer; }
.box { border: none; background: transparent; padding: 0; display: inline-flex; cursor: pointer; }
.box:disabled { cursor: default; opacity: .45; }
.miss { font: 500 11px/1.3 system-ui; color: var(--warn); }
.lnk { border: none; background: transparent; padding: 0; color: var(--accent); font: 600 11.5px/1 system-ui; cursor: pointer; }
.scopebar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.scope { display: inline-flex; align-items: center; gap: 7px; height: 26px; padding: 0 10px; border-radius: 999px; border: 1px solid var(--line); background: var(--panel); font: 500 11.5px/1 system-ui; color: var(--tx2); cursor: pointer; }
.scope.on { background: var(--accentSoft); border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); color: var(--accent); }
.scope .x { border: none; background: transparent; color: var(--tx3); cursor: pointer; padding: 0; font-size: 14px; line-height: 1; }
.fieldrow { display: flex; flex-direction: column; gap: 6px; min-width: 0; margin-top: 12px; }
.flbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); }
.blocklbl { display: flex; align-items: center; gap: 10px; font: 700 10px/1 ui-monospace, monospace; letter-spacing: .16em; color: var(--tx2); padding: 18px 0 0; }
.blocklbl .rule { flex: 1; height: 1px; background: var(--line); }

/* ---- the preview ---- */
.prev { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.ptool { height: 44px; flex: none; display: flex; align-items: center; gap: 10px; padding: 0 16px; border-bottom: 1px solid var(--line); }
.pt { font: 600 13px/1 system-ui; }
.sum { font-size: 11px; color: var(--tx3); }
.pbody { flex: 1; min-height: 0; overflow-y: auto; }
.wrow { display: grid; grid-template-columns: 14px 14px minmax(0, 1fr) auto; gap: 10px; align-items: center; height: 44px; padding: 0 16px; border-bottom: 1px solid var(--line); background: var(--bg2); }
.wrow.sel { background: var(--accentSoft); }
.fold { justify-content: center; color: var(--tx3); }
.fold:hover { color: var(--tx); }
.tt { display: flex; align-items: center; gap: 9px; min-width: 0; }
.name { border: none; background: transparent; padding: 0; font: 600 13px/1 system-ui; color: var(--tx); white-space: nowrap; cursor: pointer; }
.name:hover { color: var(--accent); }
.meta { font: 500 11px/1 system-ui; color: var(--tx3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cnt { font-size: 11px; color: var(--tx2); white-space: nowrap; }
.badge { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .08em; padding: 4px 6px; border-radius: 999px; white-space: nowrap; flex: none; }
.badge.new { color: var(--accent); background: var(--accentSoft); }
.badge.add { color: var(--good); background: color-mix(in srgb, var(--good) 15%, transparent); }
.badge.warn { color: var(--warn); background: color-mix(in srgb, var(--warn) 15%, transparent); }
.badge.bad { color: var(--adult); background: color-mix(in srgb, var(--adult) 15%, transparent); }
.badge.skip { color: var(--tx3); background: var(--panel2); }
.badge.own { color: var(--accent); background: transparent; border: 1px solid color-mix(in srgb, var(--accent) 45%, var(--line)); padding: 3px 6px; }
.secrow { height: 30px; display: flex; align-items: center; gap: 10px; padding: 0 16px 0 64px; font-size: 9.5px; font-weight: 700; letter-spacing: .12em; color: var(--tx3); }
.secrow::after { content: ''; flex: 1; height: 1px; background: var(--line); }
.secrow b { color: var(--tx2); letter-spacing: .02em; font-weight: 600; font-size: 11px; font-family: system-ui; }
.erow { display: grid; grid-template-columns: 14px 34px 14px minmax(0, 1fr) 82px 58px 92px; gap: 10px; align-items: center; height: 30px; padding: 0 16px 0 64px; border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent); font: 500 12px/1 system-ui; }
.erow .num { font-size: 11px; font-weight: 600; color: var(--tx2); text-align: right; }
.erow .kind { color: var(--tx3); }
.erow .nm { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.erow .dt, .erow .sz { font-size: 10.5px; color: var(--tx3); text-align: right; white-space: nowrap; }
.erow.out .nm, .erow.out .num { color: var(--tx3); text-decoration: line-through; }
.erow.dim .nm { color: var(--tx3); }
.st { font-size: 10.5px; display: flex; align-items: center; gap: 6px; justify-content: flex-end; white-space: nowrap; color: var(--tx3); }
.st::before { content: ''; width: 6px; height: 6px; border-radius: 999px; background: currentColor; }
.st.new { color: var(--good); }
.st.replace { color: var(--warn); }
.st.conflict, .st.unreadable { color: var(--adult); }
.ignored { padding: 12px 24px 18px; display: flex; flex-direction: column; gap: 4px; }
.igrow { font-size: 11px; color: var(--tx2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.emptyv { padding: 70px 30px; display: flex; flex-direction: column; align-items: center; gap: 10px; text-align: center; }
.emptyv .et { font: 600 15px/1.3 system-ui; color: var(--tx2); }
.emptyv .es { font: 400 12px/1.5 system-ui; color: var(--tx3); max-width: 56ch; }
.pathin { width: 360px; }
.report { margin: 12px 16px; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); overflow: hidden; }
.rp1 { padding: 12px 16px; display: flex; align-items: baseline; gap: 10px; border-bottom: 1px solid var(--line); }
.rp1 .big { font: 600 22px/1 system-ui; letter-spacing: -.3px; }
.rp1 .big.bad { color: var(--adult); }
.rp1 .s { font: 500 12px/1 system-ui; color: var(--tx2); margin-right: 8px; }
.rp1 .warn { color: var(--warn); }
.rp1 .x { margin-left: auto; border: none; background: transparent; color: var(--tx3); cursor: pointer; font-size: 15px; }
.rpl { padding: 8px 16px; display: flex; gap: 9px; align-items: flex-start; font: 400 12px/1.45 system-ui; color: var(--tx2); border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent); }
.rpl b { color: var(--tx); font-weight: 600; }
.dot { width: 7px; height: 7px; border-radius: 999px; margin-top: 5px; flex: none; background: var(--good); }
.dot.b { background: var(--adult); }
.dot.w { background: var(--warn); }
.rpfoot { height: 44px; display: flex; align-items: center; gap: 10px; padding: 0 16px; }

/* the rail rows this view teleports */
.slbl { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.n { font: 500 10.5px/1 ui-monospace, monospace; color: var(--tx3); flex: none; }
</style>
