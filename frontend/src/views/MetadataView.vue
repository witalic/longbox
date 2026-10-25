<script setup lang="ts">
// METADATA — a library's vocabulary in one place (design/metadata-management.md).
// Every field's values with their counts across a SCOPE (the library's own
// filters, or a selection handed over from its select mode); operations that
// change what titles already hold — previewed from the index before anything is
// written, run as a vault pass, journaled; the rules that change what enters a
// title from then on; and the history of every run, each one revertible.
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'
import SearchBox from '../components/SearchBox.vue'
import Pager from '../components/Pager.vue'
import RailSection from '../components/RailSection.vue'
import FilterBlock from '../components/FilterBlock.vue'
import PassButton from '../components/PassButton.vue'
import { api } from '../api'
import {
  activeFilterCount, askConfirm, metaScope, refreshLibrary, runVaultPass, stopVaultPass, store,
  vaultPass, watchVaultPass,
} from '../store'
import {
  movedBefore,
  type MetaAction, type MetaActionKind, type MetaField, type MetaPreview, type MetaRun,
  type MetaShape, type MetaValue, type Rule, type RuleApply, type RuleStats,
} from '../data'
import { readLocal, readLocalOne, writeLocal, writeLocalOne } from '../local'

const TABS = ['values', 'rules', 'history'] as const
const tab = ref<(typeof TABS)[number]>(readLocalOne('lb.metaTab', TABS, 'values'))
watch(tab, (v) => writeLocalOne('lb.metaTab', v))

const PASS = 'metadata'
watchVaultPass(true)
onBeforeUnmount(() => watchVaultPass(false))
const busy = computed(() => vaultPass.running)

function failed(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

// ---- the scope and the fields ----
const scopeOpen = ref(false)
const fields = ref<MetaField[]>([])
const inScope = ref(0)
const fieldId = ref(readLocal('lb.metaField', (v): v is string => typeof v === 'string', 'tags'))
watch(fieldId, (v) => writeLocal('lb.metaField', v))
const field = computed(() => fields.value.find((f) => f.id === fieldId.value)
  ?? fields.value.find((f) => f.id === 'tags') ?? fields.value[0])
const shape = computed<MetaShape | undefined>(() => field.value?.shape)
const titleFields = computed(() => fields.value.filter((f) => f.kind === 'title'))
const chapterFields = computed(() => fields.value.filter((f) => f.kind === 'chapter'))
const labelOf = (id: string) => fields.value.find((f) => f.id === id)?.label ?? id
const shapeOf = (id: string) => fields.value.find((f) => f.id === id)?.shape

function useFilters() { store.metaSelection = null }

// ---- the values ----
const values = ref<MetaValue[]>([])
const loading = ref(false)
const search = ref('')
const sort = ref<'count' | 'az'>('count')
const onlyFree = ref(false) // values no rule handles yet: the drift nothing catches
const selected = reactive(new Set<string>())
let anchor = -1

let valuesSeq = 0
async function loadValues() {
  const f = field.value
  if (!f) return
  const seq = ++valuesSeq
  try {
    const got = await api.metaValues(f.id, metaScope())
    if (seq !== valuesSeq) return // a later request (another field, another scope) owns the list
    values.value = got
    const held = new Set(got.map((v) => v.value))
    for (const v of [...selected]) if (!held.has(v)) selected.delete(v)
  } catch (e) { store.error = failed(e) }
}
let reloadSeq = 0
async function reload() {
  const seq = ++reloadSeq
  loading.value = true
  try {
    const got = await api.metaFields(metaScope())
    if (seq !== reloadSeq) return
    fields.value = got.fields
    inScope.value = got.inScope
    await loadValues()
  } catch (e) { store.error = failed(e) } finally { if (seq === reloadSeq) loading.value = false }
}
// `store.titles` is reassigned by every library reload — a filter change, a
// commit — so riding it keeps the counts true to the scope without a poll
watch([() => store.titles, () => store.metaSelection], reload, { immediate: true })
watch(() => field.value?.id, (now, was) => {
  if (!was || now === was) return
  selected.clear()
  closeAction()
  page.value = 1
  void loadValues()
})

const shown = computed(() => {
  let list = values.value
  const q = search.value.trim().toLowerCase()
  if (q) list = list.filter((v) => v.value.toLowerCase().includes(q))
  if (onlyFree.value) list = list.filter((v) => !v.rule)
  if (sort.value === 'az') list = [...list].sort((a, b) => a.value.localeCompare(b.value))
  return list
})
const PAGE = 200
const page = ref(1)
const pages = computed(() => Math.max(1, Math.ceil(shown.value.length / PAGE)))
const curPage = computed(() => Math.min(page.value, pages.value))
const pageRows = computed(() => shown.value.slice((curPage.value - 1) * PAGE, curPage.value * PAGE))
watch([search, sort, onlyFree], () => { page.value = 1 })
const most = computed(() => values.value.reduce((n, v) => Math.max(n, v.titles), 1))

function clickRow(v: MetaValue, e: MouseEvent) {
  const i = shown.value.indexOf(v)
  if (e.shiftKey && anchor >= 0) {
    const [a, b] = anchor < i ? [anchor, i] : [i, anchor]
    for (const row of shown.value.slice(a, b + 1)) selected.add(row.value)
  } else if (selected.has(v.value)) {
    selected.delete(v.value)
  } else {
    selected.add(v.value)
  }
  anchor = i
}
const selectedList = computed(() => values.value.filter((v) => selected.has(v.value)).map((v) => v.value))

// ---- the action: what will happen, before it happens ----
type Mode = MetaActionKind
const mode = ref<Mode | null>(null)
const to = ref('')
const toField = ref('')
const find = ref('')
const remember = ref(true)
const preview = ref<MetaPreview | null>(null)
const previewErr = ref('')
const previewing = ref(false)

// What can be done with the selected values, and with the field as a whole —
// by the field's shape (a flag cannot be renamed; a chapter value has no field
// to move into).
const valueModes = computed<{ k: Mode; label: string }[]>(() => {
  const sh = shape.value
  if (sh === 'flags') return [{ k: 'remove', label: 'Remove' }]
  const out: { k: Mode; label: string }[] = [
    { k: 'rename', label: selected.size > 1 ? 'Merge' : 'Rename' }]
  if (sh === 'list' || sh === 'single') out.push({ k: 'move', label: 'Move to field' })
  out.push({ k: 'remove', label: 'Remove' })
  return out
})
const fieldModes = computed<{ k: Mode; label: string }[]>(() => {
  const sh = shape.value
  const out: { k: Mode; label: string }[] = []
  if (sh === 'list' || sh === 'flags') out.push({ k: 'add', label: 'Add a value…' })
  if (sh === 'single' || sh === 'chapter' || sh === 'flags') out.push({ k: 'set', label: 'Set to…' })
  if (sh !== 'flags') out.push({ k: 'replace', label: 'Replace text…' })
  if (field.value?.id !== 'title') out.push({ k: 'clear', label: 'Clear' })
  return out
})
const byValue = computed(() => mode.value === 'rename' || mode.value === 'remove' || mode.value === 'move')
const canRemember = computed(() => byValue.value && !(mode.value === 'move' && shape.value === 'chapter'))
const moveTargets = computed(() => titleFields.value.filter((f) => f.id !== field.value?.id && f.id !== 'title'))
const MODE_TITLES: Record<Mode, string> = {
  rename: 'Rename', remove: 'Remove', move: 'Move to field', add: 'Add a value',
  set: 'Set the value', clear: 'Clear the field', replace: 'Replace text',
}
const panelTitle = computed(() => {
  if (!mode.value) return ''
  if (mode.value === 'rename' && selected.size > 1) return `Merge ${selected.size} values`
  if (byValue.value && selected.size > 1) return `${MODE_TITLES[mode.value]} ${selected.size} values`
  return MODE_TITLES[mode.value]
})

function openMode(m: Mode) {
  mode.value = m
  to.value = m === 'rename' ? selectedList.value[0] ?? '' : ''
  toField.value = m === 'move' ? moveTargets.value[0]?.id ?? '' : ''
  find.value = ''
  remember.value = true
}
function closeAction() {
  mode.value = null
  preview.value = null
  previewErr.value = ''
}
watch(() => selected.size, (n) => { if (!n && byValue.value) closeAction() })

const action = computed<MetaAction | null>(() => {
  const m = mode.value
  const f = field.value
  if (!m || !f) return null
  const vals = selectedList.value
  if (m === 'rename') return { kind: m, field: f.id, values: vals, to: to.value.trim() }
  if (m === 'remove') return { kind: m, field: f.id, values: vals }
  if (m === 'move') return { kind: m, field: f.id, values: vals, toField: toField.value, to: to.value.trim() }
  if (m === 'add') return { kind: m, field: f.id, values: [to.value.trim()] }
  if (m === 'set') {
    return f.shape === 'flags' ? { kind: m, field: f.id, values: [to.value] } : { kind: m, field: f.id, to: to.value.trim() }
  }
  if (m === 'replace') return { kind: m, field: f.id, find: find.value, to: to.value }
  return { kind: m, field: f.id }
})
// The preview is tied to the action AND the scope it was worked out for: an
// answer for an earlier edit is dropped, and a run is refused unless what is
// on screen was computed for exactly what would be sent (§5 "preview first").
let previewTimer: number | undefined
let previewSeq = 0
const previewFor = ref('')
const currentKey = computed(() => (action.value ? JSON.stringify([action.value, metaScope()]) : ''))
watch(currentKey, (key) => {
  const seq = ++previewSeq
  window.clearTimeout(previewTimer)
  preview.value = null
  previewErr.value = ''
  previewing.value = false
  const a = action.value
  if (!a) return
  previewTimer = window.setTimeout(async () => {
    previewing.value = true
    try {
      const got = await api.metaPreview([a], metaScope())
      if (seq !== previewSeq) return
      preview.value = got
      previewFor.value = key
    } catch (e) {
      if (seq === previewSeq) previewErr.value = failed(e)
    } finally {
      if (seq === previewSeq) previewing.value = false
    }
  }, 300)
})
const previewIsCurrent = computed(() => !!preview.value && previewFor.value === currentKey.value)

const runLabel = computed(() => {
  const n = preview.value?.titles ?? 0
  const verb = mode.value === 'rename' ? (selected.size > 1 ? 'Merge' : 'Rename')
    : mode.value === 'move' ? 'Move' : mode.value ? MODE_TITLES[mode.value].split(' ')[0] : ''
  return `${verb} in ${n.toLocaleString()} title${n === 1 ? '' : 's'}`
})
async function afterRun() {
  await refreshLibrary() // reload() follows store.titles
  await loadRuns()
}
async function runAction() {
  const a = action.value
  if (!a || !previewIsCurrent.value || !preview.value?.titles) return
  const keep = remember.value && canRemember.value
  let ran = false
  await runVaultPass(async () => {
    const out = await api.metaRun([a], metaScope(), keep)
    ran = true
    if (out.failed) {
      store.error = `${out.failed} title${out.failed === 1 ? ' was' : 's were'} left as they were `
        + '(unreadable, written by a newer longbox, or in use) — see the vault check.'
    }
    if (out.rules.length) await loadRules()
  })
  if (ran) {
    closeAction()
    selected.clear()
  }
  await afterRun()
}

// ---- history ----
const runs = ref<MetaRun[]>([])
async function loadRuns() {
  try { runs.value = await api.metaRuns() } catch (e) { store.error = failed(e) }
}
void loadRuns()
const lastRun = computed<MetaRun | undefined>(() => runs.value[0])
function when(at: string): string {
  const d = new Date(at)
  const today = new Date()
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (d.toDateString() === today.toDateString()) return `today ${time}`
  const y = new Date(today)
  y.setDate(today.getDate() - 1)
  if (d.toDateString() === y.toDateString()) return `yesterday ${time}`
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}
async function revert(r: MetaRun) {
  const ok = await askConfirm({
    title: 'Revert this run', okLabel: 'Revert',
    message: `Put back what “${r.summary}” changed in ${r.titles} title${r.titles === 1 ? '' : 's'}? `
      + 'A value changed since then is kept as it is now.',
  })
  if (!ok) return
  await runVaultPass(async () => {
    const out = await api.metaRevert(r.id)
    const left = [
      out.kept ? `${out.kept} changed since the run, kept as they are now` : '',
      out.failed ? `${out.failed} could not be written (unreadable or in use)` : '',
    ].filter(Boolean).join('; ')
    if (left) store.error = `Reverted ${out.reverted}; ${left}.`
  })
  await afterRun()
}

// ---- rules ----
const rules = ref<Rule[]>([])
const stats = ref<Record<string, RuleStats>>({})
const rulesErr = ref('')
async function loadRules() {
  try {
    const got = await api.rules()
    rules.value = got.rules
    stats.value = got.stats
    rulesErr.value = ''
  } catch (e) { rulesErr.value = failed(e) }
}
void loadRules()
async function saveList(list: Rule[]): Promise<boolean> {
  try {
    const got = await api.saveRules(list)
    rules.value = got.rules
    stats.value = got.stats
    return true
  } catch (e) {
    store.error = failed(e)
    return false
  }
}
function ruleText(r: Rule): string {
  if (r.action === 'rename') return `→ ${r.to}`
  if (r.action === 'remove') return 'remove'
  if (r.action === 'move') return `move to ${labelOf(r.toField).toLowerCase()}${r.to ? `: ${r.to}` : ''}`
  return `keep, and add ${labelOf(r.toField).toLowerCase()}: ${r.to}`
}
function ruleCond(r: Rule): string {
  const parts = []
  if (shapeOf(r.field) === 'chapter') parts.push(`chapter ${labelOf(r.field).toLowerCase()}`)
  if (r.action === 'move' && ['single'].includes(shapeOf(r.toField) ?? '')) parts.push(`only where ${labelOf(r.toField).toLowerCase()} is empty`)
  if (r.domains.length) parts.push(`only from ${r.domains.join(', ')}`)
  if (!r.enabled) parts.push('disabled')
  return parts.join(' · ')
}

const editing = ref<Rule | null>(null)
const editingAt = ref(-1) // -1: a new rule
const matchDraft = ref('')
const domainsText = ref('')
function newRule() {
  editing.value = { id: '', enabled: true, field: field.value?.id ?? 'tags', match: [], action: 'rename', to: '', toField: '', domains: [] }
  editingAt.value = -1
  domainsText.value = ''
}
function editRule(i: number) {
  editing.value = JSON.parse(JSON.stringify(rules.value[i]))
  editingAt.value = i
  domainsText.value = rules.value[i].domains.join(', ')
}
function addMatch() {
  const v = matchDraft.value.trim()
  if (editing.value && v && !editing.value.match.includes(v)) editing.value.match.push(v)
  matchDraft.value = ''
}
watch(domainsText, (t) => {
  if (editing.value) editing.value.domains = t.split(',').map((d) => d.trim()).filter(Boolean)
})
const ruleTargets = computed(() => titleFields.value.filter((f) =>
  f.id !== editing.value?.field && f.id !== 'title' && (editing.value?.action !== 'add' || f.shape !== 'single')))
const ruleActions = computed(() => shapeOf(editing.value?.field ?? '') === 'chapter'
  || shapeOf(editing.value?.field ?? '') === 'flags'
  ? (['rename', 'remove'] as const) : (['rename', 'remove', 'move', 'add'] as const))
async function saveRule() {
  const r = editing.value
  if (!r) return
  const list = [...rules.value]
  if (editingAt.value >= 0) list[editingAt.value] = r
  else list.push(r)
  if (await saveList(list)) {
    const at = editingAt.value >= 0 ? editingAt.value : rules.value.length - 1
    editRule(at)
  }
}
async function toggleRule(i: number) {
  const list = rules.value.map((r, k) => (k === i ? { ...r, enabled: !r.enabled } : r))
  await saveList(list)
}
async function deleteRule() {
  if (editingAt.value < 0) { editing.value = null; return }
  const ok = await askConfirm({ title: 'Delete rule', danger: true, okLabel: 'Delete',
    message: 'Delete this rule? Values it already changed stay as they are.' })
  if (!ok) return
  if (await saveList(rules.value.filter((_, k) => k !== editingAt.value))) editing.value = null
}
// order is the owner's: dragging a rule writes the whole list
const dragRule = ref(-1)
const dropRule = ref(-1)
async function onRuleDrop(target: number) {
  const from = dragRule.value
  dragRule.value = dropRule.value = -1
  if (from < 0 || from === target) return
  await saveList(movedBefore(rules.value, from, target > from ? target + 1 : target))
  if (editing.value && editingAt.value >= 0) {
    const i = rules.value.findIndex((r) => r.id === editing.value?.id)
    editingAt.value = i
  }
}

// "try a value" — the rule being edited, saved or not, run on one value
const tryValue = ref('')
const tryOut = ref<RuleApply | null>(null)
const tryErr = ref('')
let tryTimer: number | undefined
let trySeq = 0
watch([editing, tryValue], () => {
  const seq = ++trySeq
  window.clearTimeout(tryTimer)
  tryOut.value = null
  tryErr.value = ''
  const r = editing.value
  const v = tryValue.value.trim()
  if (!r || !v) return
  const rule: Rule = JSON.parse(JSON.stringify(r))
  tryTimer = window.setTimeout(async () => {
    const asList = ['list', 'flags'].includes(shapeOf(rule.field) ?? '')
    try {
      const got = await api.applyRules({ [rule.field]: asList ? [v] : v }, rule.domains[0] ?? '',
                                       { trial: [rule] })
      if (seq === trySeq) tryOut.value = got
    } catch (e) {
      if (seq === trySeq) tryErr.value = failed(e)
    }
  }, 250)
}, { deep: true })
// "apply to stored titles" applies the SAVED rule — never one with edits on screen
const editingSaved = computed(() => editingAt.value >= 0 && !!editing.value
  && JSON.stringify(editing.value) === JSON.stringify(rules.value[editingAt.value]))
const tryShown = computed(() => Object.entries(tryOut.value?.values ?? {}).map(([k, v]) => ({
  field: labelOf(k).toLowerCase(), value: Array.isArray(v) ? v.join(', ') || '—' : v || '—' })))

async function applyRule() {
  const r = editing.value
  if (!r?.id) return
  let pv: MetaPreview
  try { pv = await api.rulePreview(r.id, metaScope()) } catch (e) { store.error = failed(e); return }
  const typed = pv.manual ? ` ${pv.manual} of these values were typed by you and change too.` : ''
  const ok = await askConfirm({ title: 'Apply rule to stored titles', okLabel: `Apply to ${pv.titles}`,
    message: `${pv.titles} title${pv.titles === 1 ? '' : 's'} in the current scope change.${typed}` })
  if (!ok) return
  await runVaultPass(() => api.ruleRun(r.id, metaScope()))
  await afterRun()
}
</script>

<template>
  <div class="meta">
    <Teleport to="#siderail">
      <RailSection label="TITLE" hint="values">
        <button v-for="f in titleFields" :key="f.id" :class="{ on: tab === 'values' && field?.id === f.id }"
                @click="fieldId = f.id; tab = 'values'">
          <span class="slbl">{{ f.label }}</span>
          <span v-if="!f.builtin" class="kind mono">YOURS</span>
          <span class="n mono">{{ f.distinct.toLocaleString() }}</span>
        </button>
      </RailSection>
      <RailSection label="CHAPTERS" hint="values">
        <button v-for="f in chapterFields" :key="f.id" :class="{ on: tab === 'values' && field?.id === f.id }"
                @click="fieldId = f.id; tab = 'values'">
          <span class="slbl">{{ f.label }}</span>
          <span class="n mono">{{ f.distinct.toLocaleString() }}</span>
        </button>
      </RailSection>
    </Teleport>

    <div class="col">
      <div class="head">
        <h1 class="h1">{{ tab === 'values' ? field?.label ?? 'Metadata' : tab === 'rules' ? 'Rules' : 'History' }}</h1>
        <span v-if="tab === 'values'" class="count">{{ values.length.toLocaleString() }} values</span>
        <span v-else-if="tab === 'rules'" class="count">applied top to bottom, once</span>
        <span v-else class="count">the last 50 runs</span>
        <div style="flex:1"></div>
        <div class="seg">
          <button class="opt" :class="{ on: tab === 'values' }" @click="tab = 'values'">Values</button>
          <button class="opt" :class="{ on: tab === 'rules' }" @click="tab = 'rules'">Rules
            <span class="mono dim">{{ rules.length }}</span></button>
          <button class="opt" :class="{ on: tab === 'history' }" @click="tab = 'history'">History</button>
        </div>
        <button v-if="tab !== 'history'" class="btn" :class="{ acton: scopeOpen }"
                :disabled="!!store.metaSelection" :title="store.metaSelection ? 'Working on the selected titles' : 'Which titles'"
                @click="scopeOpen = !scopeOpen">
          <Icon name="filter" :size="13" :sw="1.9" />Scope
          <span v-if="!store.metaSelection && activeFilterCount()" class="numbadge">{{ activeFilterCount() }}</span>
        </button>
        <button v-if="tab === 'rules'" class="btn" @click="newRule()"><Icon name="plus" :size="13" :sw="2.2" />Rule</button>
      </div>

      <!-- what the scope reaches, always in sight -->
      <div v-if="tab !== 'history'" class="scopeline">
        <template v-if="store.metaSelection">
          <span><b>{{ store.metaSelection.length.toLocaleString() }}</b> selected titles</span>
          <button class="lnk" @click="useFilters()">Use the library's filters instead</button>
        </template>
        <template v-else>
          <span><b>{{ inScope.toLocaleString() }}</b> of {{ store.total.toLocaleString() }} titles in scope</span>
          <span class="dim">— the library's filters; an operation changes only titles in here that hold the value</span>
        </template>
      </div>

      <!-- ================= VALUES ================= -->
      <div v-if="tab === 'values'" class="work">
        <div class="vals">
          <div class="vtool">
            <SearchBox v-model="search" :width="240" :placeholder="`Filter ${(field?.label ?? '').toLowerCase()}…`" />
            <div class="seg text">
              <button class="opt" :class="{ on: sort === 'count' }" @click="sort = 'count'">Count</button>
              <button class="opt" :class="{ on: sort === 'az' }" @click="sort = 'az'">A–Z</button>
            </div>
            <div class="seg text">
              <button class="opt" :class="{ on: !onlyFree }" @click="onlyFree = false">All</button>
              <button class="opt" :class="{ on: onlyFree }" title="Values no rule handles yet" @click="onlyFree = true">No rule</button>
            </div>
            <div style="flex:1"></div>
            <span v-if="selected.size" class="selc mono">{{ selected.size }} selected</span>
            <template v-if="selected.size">
              <button v-for="m in valueModes" :key="m.k" class="btn small" :class="{ acton: mode === m.k }"
                      @click="openMode(m.k)">{{ m.label }}</button>
              <button class="btn small ghost" @click="selected.clear()">Clear</button>
            </template>
            <template v-else>
              <button v-for="m in fieldModes" :key="m.k" class="btn small ghost" :class="{ acton: mode === m.k }"
                      @click="openMode(m.k)">{{ m.label }}</button>
            </template>
          </div>
          <div class="vrow hd"><span></span><span>VALUE</span><span>TITLES</span><span>TYPED</span><span>RULE</span></div>
          <div class="vlist scroll">
            <div v-if="!loading && !shown.length" class="emptyv">
              <div class="et">{{ values.length ? 'Nothing matches' : `No ${(field?.label ?? 'values').toLowerCase()} in scope` }}</div>
            </div>
            <div v-for="v in pageRows" :key="v.value" class="vrow" :class="{ sel: selected.has(v.value) }"
                 @click="clickRow(v, $event)">
              <span class="fbox" :class="{ on: selected.has(v.value) }"><Icon v-if="selected.has(v.value)" name="check" :size="9" :sw="3.2" /></span>
              <span class="v" :title="v.value">{{ v.value }}</span>
              <span class="cnt"><span class="bar"><span :style="{ width: `${Math.max(1, v.titles / most * 100)}%` }"></span></span>{{ v.titles.toLocaleString() }}</span>
              <span class="mono typed" :class="{ w: v.typed }">{{ v.typed || '' }}</span>
              <span class="mono rb" :title="v.rule ? `Rule ${v.rule}` : 'No rule handles this value'">
                {{ v.rule ? `${v.ruleText} · rule ${v.rule}` : '—' }}</span>
            </div>
          </div>
          <div class="lfoot">
            <template v-if="lastRun">
              <span class="lfinfo">Last run: <b>{{ lastRun.summary }}</b> · {{ lastRun.titles.toLocaleString() }} titles · {{ lastRun.seconds }} s</span>
              <span v-if="lastRun.revertedAt" class="lfinfo">· reverted</span>
              <button v-else class="lnk" :disabled="busy" @click="revert(lastRun)">Revert</button>
            </template>
            <span v-else class="lfinfo">⇧-click selects a range</span>
            <div style="flex:1"></div>
            <Pager v-if="pages > 1" :page="curPage" :pages="pages" @go="(p) => (page = p)" />
          </div>
        </div>

        <!-- the action: what will happen, and what it costs, before it happens -->
        <div v-if="mode" class="act">
          <div class="ahead">{{ panelTitle }}<button class="iconbtn plain" title="Close" @click="closeAction()"><Icon name="x" :size="12" :sw="2.2" /></button></div>
          <div class="abody scroll">
            <div v-if="byValue">
              <div class="lbl">FROM · {{ (field?.label ?? '').toUpperCase() }}</div>
              <div class="chips"><span v-for="v in selectedList" :key="v" class="chip">{{ v }}</span></div>
            </div>
            <div v-if="mode === 'rename'">
              <div class="lbl">INTO</div>
              <input v-model="to" class="in" placeholder="the value they become" />
              <div class="hint">Any value works — an existing one merges, a new one renames.</div>
            </div>
            <div v-if="mode === 'move'">
              <div class="lbl">INTO</div>
              <div class="two">
                <select v-model="toField" class="in">
                  <option v-for="f in moveTargets" :key="f.id" :value="f.id">{{ f.label }}</option>
                </select>
                <input v-model="to" class="in" placeholder="as it is" />
              </div>
              <div class="hint">A single-value field is filled only where it is empty.</div>
            </div>
            <div v-if="mode === 'add' || mode === 'set'">
              <div class="lbl">{{ mode === 'add' ? 'ADD' : 'SET TO' }}</div>
              <select v-if="shape === 'flags'" v-model="to" class="in">
                <option value="adult">adult</option><option value="ai">ai</option><option value="censored">censored</option>
              </select>
              <input v-else v-model="to" class="in" placeholder="the value" />
            </div>
            <div v-if="mode === 'replace'">
              <div class="lbl">FIND · REPLACE WITH</div>
              <div class="two"><input v-model="find" class="in" placeholder="text to find" /><input v-model="to" class="in" placeholder="nothing" /></div>
            </div>
            <div v-if="mode === 'clear'" class="hint">Empties {{ (field?.label ?? '').toLowerCase() }} on every title in scope.</div>
            <div v-if="mode === 'remove'" class="hint">Takes these values from every title in scope that holds them.</div>

            <label v-if="canRemember" class="check" @click.prevent="remember = !remember">
              <span class="fbox" :class="{ on: remember }"><Icon v-if="remember" name="check" :size="9" :sw="3.2" /></span>
              <span>Also make it a rule
                <span class="hint">From now on capture and your typing do the same — in every title, not just this scope.</span></span>
            </label>

            <div v-if="previewErr" class="err">{{ previewErr }}</div>
            <div v-else-if="preview" class="preview">
              <div class="pv1"><span class="big">{{ preview.titles.toLocaleString() }}</span><span class="s">titles change</span></div>
              <div v-if="preview.manual" class="pvl"><span class="d w"></span><span><b>{{ preview.manual.toLocaleString() }}</b> of these values were typed by you — they change too.</span></div>
              <div v-if="preview.auto" class="pvl"><span class="d i"></span><span><b>{{ preview.auto.toLocaleString() }}</b> came from capture{{ canRemember && remember ? '; the new rule keeps the next capture from bringing them back' : ' — the next capture may bring them back' }}.</span></div>
              <div v-if="preview.untouched" class="pvl"><span class="d"></span><span><b>{{ preview.untouched.toLocaleString() }}</b> already hold <b>{{ to }}</b> — untouched, nothing to write.</span></div>
              <div v-if="preview.conflicts" class="pvl"><span class="d w"></span><span><b>{{ preview.conflicts.toLocaleString() }}</b> already hold another {{ labelOf(toField).toLowerCase() }}: one slot, two answers. Theirs stays.</span></div>
              <div v-if="preview.shelfRename.length" class="pvl"><span class="d i"></span><span>Every title on <span class="mono">{{ preview.shelfRename[0] }}/</span> goes, and no <span class="mono">{{ preview.shelfRename[1] }}/</span> shelf exists yet — the shelf itself is renamed: <b>one directory rename</b>.</span></div>
              <div v-else-if="preview.shelfMoves" class="pvl"><span class="d i"></span><span><b>{{ preview.shelfMoves.toLocaleString() }}</b> titles move to another shelf (a rename within the vault — nothing is copied).</span></div>
              <div v-if="preview.chapterTwins" class="pvl"><span class="d w"></span><span><b>{{ preview.chapterTwins }}</b> chapter rows will share number, language and group with another — each keeps its own file.</span></div>
              <div v-if="preview.sample.length" class="sample">
                <div v-for="s in preview.sample" :key="s.titleId + s.field" class="srow">
                  <span class="tt">{{ s.title }}</span><span class="ch"><s>{{ s.before }}</s> → <i>{{ s.after }}</i></span>
                </div>
              </div>
            </div>
            <div v-else-if="previewing" class="hint">Working it out from the index…</div>
          </div>
          <div class="afoot">
            <PassButton :op="PASS" :active="vaultPass.key" :done="vaultPass.done" :total="vaultPass.total"
                        :running="vaultPass.op" :label="runLabel" accent
                        :disabled="!previewIsCurrent || !preview?.titles || previewing"
                        @run="runAction()" @stop="stopVaultPass()" />
            <button v-if="!busy" class="btn ghost" @click="closeAction()">Cancel</button>
            <span v-if="preview?.titles && !busy" class="cost mono">{{ preview.titles.toLocaleString() }} writes</span>
          </div>
        </div>
      </div>

      <!-- ================= RULES ================= -->
      <div v-else-if="tab === 'rules'" class="work">
        <div class="rules scroll">
          <div v-if="rulesErr" class="err pad">{{ rulesErr }}</div>
          <div v-else-if="!rules.length" class="emptyv">
            <div class="et">No rules yet</div>
            <div class="es">A rule changes a value on its way into a title — from capture, from your typing, from a chapter list. Make one here, or tick "also make it a rule" on an operation.</div>
          </div>
          <div v-for="(r, i) in rules" :key="r.id" class="rrow"
               :class="{ on: editing?.id === r.id && editingAt === i, off: !r.enabled, drop: dropRule === i }"
               draggable="true" @dragstart="dragRule = i" @dragover.prevent="dropRule = i" @dragleave="dropRule = -1"
               @drop.prevent="onRuleDrop(i)" @dragend="dragRule = dropRule = -1" @click="editRule(i)">
            <span class="grip" title="Drag to reorder">⋮⋮</span>
            <span class="toggle" :class="{ on: r.enabled }" :title="r.enabled ? 'Disable' : 'Enable'" @click.stop="toggleRule(i)"></span>
            <span class="fld mono">{{ labelOf(r.field).toLowerCase() }}</span>
            <span class="rdesc">{{ r.match.join(', ') || 'always' }}<span class="arrow">→</span><b>{{ ruleText(r) }}</b>
              <span v-if="ruleCond(r)" class="cond">{{ ruleCond(r) }}</span></span>
            <span class="rhits mono">{{ stats[r.id] ? `fired ${stats[r.id].fired.toLocaleString()}×` : 'not fired yet' }}</span>
          </div>
        </div>
        <div v-if="editing" class="act">
          <div class="ahead">{{ editingAt >= 0 ? `Rule ${editingAt + 1}` : 'New rule' }}<button class="iconbtn plain" title="Close" @click="editing = null"><Icon name="x" :size="12" :sw="2.2" /></button></div>
          <div class="abody scroll">
            <div class="editor">
              <span class="k">Field</span>
              <select v-model="editing.field" class="in">
                <optgroup label="Title"><option v-for="f in titleFields" :key="f.id" :value="f.id">{{ f.label }}</option></optgroup>
                <optgroup label="Chapters"><option v-for="f in chapterFields" :key="f.id" :value="f.id">{{ f.label }}</option></optgroup>
              </select>
              <span class="k">Is</span>
              <div class="chips">
                <span v-for="(m, k) in editing.match" :key="m" class="chip">{{ m }}<button class="cx" @click="editing.match.splice(k, 1)">×</button></span>
                <input v-model="matchDraft" class="chipin" :placeholder="editing.match.length ? 'add…' : 'a value, then Enter'"
                       @keydown.enter.prevent="addMatch()" @blur="addMatch()" />
              </div>
              <span class="k">Then</span>
              <select v-model="editing.action" class="in">
                <option v-for="a in ruleActions" :key="a" :value="a">{{ { rename: 'Rename to', remove: 'Remove', move: 'Move to field', add: 'Keep, and add to' }[a] }}</option>
              </select>
              <template v-if="editing.action === 'move' || editing.action === 'add'">
                <span class="k"></span>
                <div class="two">
                  <select v-model="editing.toField" class="in">
                    <option v-for="f in ruleTargets" :key="f.id" :value="f.id">{{ f.label }}</option>
                  </select>
                  <input v-model="editing.to" class="in" :placeholder="editing.action === 'move' ? 'as it is' : 'the value'" />
                </div>
              </template>
              <template v-else-if="editing.action === 'rename'">
                <span class="k"></span><input v-model="editing.to" class="in" placeholder="the value it becomes" />
              </template>
              <span class="k">Only from</span>
              <input v-model="domainsText" class="in" placeholder="any site — or domains, comma-separated" />
            </div>
            <div class="try">
              <div class="lbl">TRY A VALUE</div>
              <input v-model="tryValue" class="in" placeholder="as it would arrive" />
              <div v-if="tryErr" class="err">{{ tryErr }}</div>
              <div v-else-if="tryOut" class="tryout">
                <span v-for="o in tryShown" :key="o.field"><span class="dim mono">{{ o.field }}:</span> <span class="chip" :class="{ ruled: tryOut.fired.length }">{{ o.value }}</span></span>
                <span class="dim mono">{{ tryOut.fired.length ? 'changed by this rule' : 'this rule leaves it alone' }}</span>
              </div>
            </div>
            <div class="hint">Applies to capture, to what you type in the editor, and to new chapters' language and group. Saving it changes nothing already stored.</div>
          </div>
          <div class="afoot">
            <button class="btn accent" :disabled="busy" @click="saveRule()">Save rule</button>
            <button v-if="editing.id" class="btn" :disabled="busy || !editingSaved"
                    :title="editingSaved ? 'Change what titles already hold, with a preview' : 'Save the rule first'"
                    @click="applyRule()">Apply to stored titles…</button>
            <button v-if="editingAt >= 0" class="btn ghost danger" :disabled="busy" @click="deleteRule()">Delete</button>
          </div>
        </div>
      </div>

      <!-- ================= HISTORY ================= -->
      <div v-else class="hist scroll">
        <div v-if="!runs.length" class="emptyv"><div class="et">Nothing has been run yet</div></div>
        <template v-else>
          <div class="hrow hd"><span>WHEN</span><span>WHAT</span><span>TITLES</span><span>TIME</span><span></span></div>
          <div v-for="r in runs" :key="r.id" class="hrow" :class="{ rev: r.revertedAt }">
            <span class="when mono">{{ when(r.at) }}</span>
            <span class="what" :title="r.summary">{{ r.summary }}<span v-if="r.stopped" class="dim"> · stopped early</span></span>
            <span class="num mono">{{ r.titles.toLocaleString() }}</span>
            <span class="num mono">{{ r.seconds }} s</span>
            <span v-if="r.revertedAt" class="state mono">reverted {{ when(r.revertedAt) }}</span>
            <button v-else class="btn small" :disabled="busy" @click="revert(r)">Revert…</button>
          </div>
        </template>
      </div>
      <div v-if="tab === 'history'" class="lfoot">
        <span class="lfinfo">Revert restores only values that still hold what the run wrote — a later edit is never rolled back.</span>
      </div>
    </div>

    <FilterBlock v-if="scopeOpen && !store.metaSelection" @close="scopeOpen = false" />
  </div>
</template>

<style scoped>
.meta { display: flex; height: 100%; overflow: hidden; position: relative; }
.col { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.head { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 12px; padding: 0 16px 0 24px; }
.dim { color: var(--tx3); }
.kind { font-size: 9px; color: var(--tx3); letter-spacing: .06em; margin-right: 6px; }
.scopeline { height: 30px; flex: none; display: flex; align-items: center; gap: 10px; padding: 0 24px; border-bottom: 1px solid var(--line); background: var(--bg2); font: 500 11.5px/1 system-ui; color: var(--tx2); white-space: nowrap; overflow: hidden; }
.scopeline b { color: var(--tx); font-weight: 600; }
.lnk { border: none; background: transparent; padding: 0; color: var(--accent); font: 600 11.5px/1 system-ui; cursor: pointer; }
.lnk:disabled { color: var(--tx3); cursor: default; }
.work { flex: 1; min-height: 0; display: flex; }
.emptyv { padding: 70px 30px; display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; }
.emptyv .et { font: 600 14px/1.3 system-ui; color: var(--tx2); }
.emptyv .es { font: 400 12px/1.5 system-ui; color: var(--tx3); max-width: 52ch; }
.err { font: 500 12px/1.45 system-ui; color: var(--adult); }
.err.pad { padding: 16px 24px; }

/* ---- values ---- */
.vals { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.vtool { height: 44px; flex: none; display: flex; align-items: center; gap: 8px; padding: 0 16px 0 24px; border-bottom: 1px solid var(--line); }
.selc { font-size: 11px; color: var(--accent); margin-right: 4px; }
.vlist { flex: 1; min-height: 0; overflow-y: auto; }
.vrow { display: grid; grid-template-columns: 22px minmax(0, 1fr) 150px 56px minmax(0, 190px); align-items: center; gap: 10px; height: 34px; padding: 0 16px 0 24px; border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent); font: 500 12.5px/1 system-ui; color: var(--tx); cursor: pointer; user-select: none; }
.vrow:hover { background: var(--hover); }
.vrow.hd { height: 30px; flex: none; font: 700 9.5px/1 ui-monospace, monospace; letter-spacing: .1em; color: var(--tx3); cursor: default; }
.vrow.hd:hover { background: transparent; }
.vrow.sel { background: var(--accentSoft); }
.vrow .v { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cnt { display: flex; align-items: center; gap: 8px; font: 500 11px/1 ui-monospace, monospace; color: var(--tx2); }
.bar { flex: 1; height: 4px; border-radius: 999px; background: var(--line); overflow: hidden; }
.bar span { display: block; height: 100%; background: var(--tx3); }
.vrow.sel .bar span { background: var(--accent); }
.typed { font-size: 11px; color: var(--tx3); }
.typed.w { color: var(--warn); }
.rb { font-size: 10.5px; color: var(--accent); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ---- the action panel ---- */
.act { width: 330px; flex: none; border-left: 1px solid var(--line); background: var(--bg2); display: flex; flex-direction: column; }
.ahead { height: 44px; flex: none; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 0 8px 0 16px; border-bottom: 1px solid var(--line); font: 600 13px/1 system-ui; }
.abody { flex: 1; min-height: 0; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 12px; }
.lbl { font: 700 9.5px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); margin-bottom: 6px; }
.hint { font: 400 11.5px/1.45 system-ui; color: var(--tx3); margin-top: 6px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.chip { display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 8px; border-radius: 999px; background: var(--panel2); border: 1px solid var(--line); font: 500 11.5px/1 system-ui; color: var(--tx); white-space: nowrap; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
.chip.ruled { border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); }
.cx { border: none; background: transparent; color: var(--tx3); cursor: pointer; padding: 0; font: 600 12px/1 system-ui; }
.cx:hover { color: var(--adult); }
.chipin { flex: 1; min-width: 90px; border: none; border-bottom: 1px solid var(--line); background: transparent; outline: none; color: var(--tx); font: 500 12px/1 system-ui; padding: 4px 1px; }
.chipin:focus { border-bottom-color: var(--accent); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.check { display: flex; gap: 9px; align-items: flex-start; font: 500 12px/1.4 system-ui; color: var(--tx); cursor: pointer; }
.check .fbox { margin-top: 1px; }
.check .hint { display: block; margin-top: 2px; }
.preview { border: 1px solid var(--line); border-radius: 10px; background: var(--panel); overflow: hidden; }
.pv1 { padding: 12px 14px; display: flex; align-items: baseline; gap: 8px; border-bottom: 1px solid var(--line); }
.pv1 .big { font: 600 22px/1 system-ui; letter-spacing: -.3px; }
.pv1 .s { font: 500 12px/1 system-ui; color: var(--tx2); }
.pvl { padding: 8px 14px; display: flex; gap: 8px; align-items: flex-start; font: 400 11.5px/1.45 system-ui; color: var(--tx2); border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent); }
.pvl b { color: var(--tx); font-weight: 600; }
.d { width: 7px; height: 7px; border-radius: 999px; margin-top: 5px; flex: none; background: var(--good); }
.d.w { background: var(--warn); }
.d.i { background: var(--accent); }
.sample { padding: 8px 14px 10px; }
.srow { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 10px; height: 24px; align-items: center; font: 500 11.5px/1 system-ui; }
.srow .tt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--tx2); }
.srow .ch { font: 500 10.5px/1 ui-monospace, monospace; color: var(--tx3); max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.srow .ch s { color: var(--adult); text-decoration: none; }
.srow .ch i { color: var(--good); font-style: normal; }
.afoot { height: 44px; flex: none; border-top: 1px solid var(--line); display: flex; align-items: center; gap: 8px; padding: 0 16px; }
.cost { margin-left: auto; font-size: 10.5px; color: var(--tx3); }

/* ---- rules ---- */
.rules { flex: 1; min-width: 0; overflow-y: auto; }
.rrow { display: grid; grid-template-columns: 14px 26px 96px minmax(0, 1fr) 120px; align-items: center; gap: 12px; min-height: 44px; padding: 7px 16px 7px 24px; border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent); cursor: pointer; }
.rrow:hover { background: var(--hover); }
.rrow.on { background: var(--accentSoft); }
.rrow.off { opacity: .5; }
.rrow.drop { box-shadow: inset 0 2px 0 var(--accent); }
.grip { color: var(--tx3); font: 700 11px/1 system-ui; letter-spacing: -2px; cursor: grab; }
.toggle { width: 26px; height: 15px; border-radius: 999px; background: var(--line); position: relative; flex: none; cursor: pointer; }
.toggle::after { content: ''; position: absolute; top: 2px; left: 2px; width: 11px; height: 11px; border-radius: 999px; background: var(--tx3); transition: left .12s ease; }
.toggle.on { background: var(--accent); }
.toggle.on::after { left: 13px; background: var(--accent-ink); }
.fld { font-size: 11px; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; height: 22px; display: inline-flex; align-items: center; padding: 0 7px; width: fit-content; max-width: 96px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rdesc { font: 500 12.5px/1.45 system-ui; min-width: 0; overflow-wrap: anywhere; }
.rdesc .arrow { color: var(--tx3); margin: 0 5px; }
.rdesc .cond { display: block; font: 400 11px/1.4 system-ui; color: var(--tx3); }
.rhits { font-size: 11px; color: var(--tx3); text-align: right; }
.editor { display: grid; grid-template-columns: 70px minmax(0, 1fr); gap: 10px; align-items: center; }
.editor .k { font: 600 11.5px/1 system-ui; color: var(--tx2); }
.try { border: 1px dashed var(--line); border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.try .lbl { margin: 0; }
.tryout { display: flex; align-items: center; gap: 8px; font: 500 12px/1 system-ui; flex-wrap: wrap; }
select.in { height: 30px; padding: 0 8px; }

/* ---- history ---- */
.hist { flex: 1; min-height: 0; overflow-y: auto; }
.hrow { display: grid; grid-template-columns: 120px minmax(0, 1fr) 90px 70px 120px; align-items: center; gap: 12px; height: 44px; padding: 0 16px 0 24px; border-bottom: 1px solid color-mix(in srgb, var(--line) 55%, transparent); font: 500 12.5px/1 system-ui; }
.hrow.hd { height: 30px; font: 700 9.5px/1 ui-monospace, monospace; letter-spacing: .1em; color: var(--tx3); }
.hrow .when { font-size: 11px; color: var(--tx3); }
.hrow .what { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hrow .num { font-size: 11px; color: var(--tx2); }
.hrow .state { font-size: 11px; color: var(--tx3); }
.hrow.rev .what { color: var(--tx3); text-decoration: line-through; }
</style>
