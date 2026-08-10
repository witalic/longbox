<script setup lang="ts">
// The pick inspector (design/browser-refactor.md §C): the user composes the
// selector from the clicked element's real DOM chain — identical controls on
// every pick, for every field. The live preview is truthful: it shows exactly
// the cleaned value that would land in the draft.
import { computed, reactive, watch } from 'vue'
import { LIST_KEYS, captureValue, defaultClean, type CleanFlags } from '../normalize'
import Icon from './Icon.vue'

export interface ChainNode {
  tag: string
  id: string
  classes: { n: string; dyn: boolean }[]
  attrs: { n: string; v: string; url: boolean; prefix: string }[]
  nth: number
  label: string
}
export interface ProbeResult {
  count: number
  values: string[]
  preview: string
  pickedIndex: number // index of the clicked element among the matches (-1 = not among them)
}

export interface PickUse {
  selector: string
  mode: 'single' | 'list'
  index: number
  lower: boolean
  stripCounts: boolean
  useAnchor: boolean // prefer matching the value by its row label (position-independent)
  values: string[]
  preview: string
}

const props = defineProps<{
  field: string
  chain: ChainNode[]
  target: number
  probe: ProbeResult // live result for the composed selector (owned by the browser)
  anchor?: string    // the row label naming the picked value, when one was detected
  saved?: { lower?: boolean; stripCounts?: boolean } // the field's stored recipe flags
}>()

export interface ProbeReq { selector: string; anchor: string }

const emit = defineEmits<{
  (e: 'probe', req: ProbeReq): void
  (e: 'use', result: PickUse): void
  (e: 'repick'): void
  (e: 'cancel'): void
}>()

const dc: CleanFlags = defaultClean(props.field)
const state = reactive({
  target: props.target,
  useTag: true,
  useId: false,
  classOn: {} as Record<string, boolean>,
  attrOp: {} as Record<string, 'off' | 'prefix' | 'exact'>,
  useNth: false,
  ctxOn: {} as Record<number, boolean>, // ancestor chain levels included as selector context
  scope: (LIST_KEYS.includes(props.field) ? 'all' : 'one') as 'one' | 'all',
  index: 0,
  // re-teaching a field starts from its SAVED cleanup flags, not the defaults
  lower: props.saved?.lower ?? dc.lower,
  stripCounts: props.saved?.stripCounts ?? dc.stripCounts,
  // a detected row label beats any class/position selector across pages
  useAnchor: !!props.anchor && props.field !== 'cover',
})

const DISTINCTIVE_TAGS = new Set(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'main', 'article', 'header', 'footer', 'aside', 'table', 'figure'])
function isDistinctive(n?: ChainNode): boolean {
  return !!n && (!!n.id || n.classes.some((c) => !c.dyn) || DISTINCTIVE_TAGS.has(n.tag))
}
// An ancestor link's category prefix (`a[href^="/artists/"]`) — the strongest
// row discriminator on chip sites where every row shares the same classes.
function urlPrefixAttr(n?: ChainNode) {
  return n?.attrs.find((a) => a.url && a.prefix)
}
// Ancestors offered as selector context (up to 4 levels above the target).
const ctxLevels = computed(() =>
  props.chain.slice(state.target + 1, state.target + 5).map((n, k) => ({ idx: state.target + 1 + k, node: n })))

function deriveToggles(i: number) {
  const node = props.chain[i]
  state.classOn = {}
  for (const c of node?.classes || []) state.classOn[c.n] = !c.dyn // useful on, dynamic off
  state.attrOp = {}
  for (const a of node?.attrs || []) state.attrOp[a.n] = a.url && a.prefix ? 'prefix' : 'off'
  state.useId = false
  state.useTag = true
  state.useNth = false
  // Default selector context, best discriminator first: an ancestor link with a
  // category prefix (`a[href^="/artists/"]`) wins whenever present — on chip
  // sites all rows share the same classes and ONLY the href tells them apart.
  // Otherwise a bare/generic leaf gets scoped under its nearest distinctive
  // ancestor (e.g. `h1 span` instead of a page-wide `span`).
  state.ctxOn = {}
  const lim = Math.min(i + 5, props.chain.length)
  let pick = -1
  for (let k = i + 1; k < lim; k++) {
    if (urlPrefixAttr(props.chain[k])) { pick = k; break }
  }
  if (pick < 0 && node && !isDistinctive(node)) {
    for (let k = i + 1; k < lim; k++) {
      if (isDistinctive(props.chain[k])) { pick = k; break }
    }
  }
  if (pick >= 0) state.ctxOn[pick] = true
}
deriveToggles(state.target)
function retarget(i: number) { state.target = i; deriveToggles(i) }

const node = computed(() => props.chain[state.target])
// breadcrumb, shown root → leaf (the chain is stored leaf → root)
const crumbs = computed(() => props.chain.map((n, idx) => ({ label: n.label, idx })).reverse())

function cssEsc(s: string) { return (window.CSS && CSS.escape) ? CSS.escape(s) : s.replace(/[^a-zA-Z0-9_-]/g, '\\$&') }
function cssAttr(s: string) { return String(s).replace(/"/g, '\\"') }

// selector context: the enabled ancestors, outermost first, as descendant
// scopes. A category href-prefix rides along — it's the attribute that actually
// distinguishes the row (`a[href^="/artists/"]`), where classes don't.
function ctxSeg(n: ChainNode): string {
  if (n.id) return '#' + cssEsc(n.id)
  const pref = urlPrefixAttr(n)
  return pref ? `${n.tag}[${pref.n}^="${cssAttr(pref.prefix)}"]` : n.label
}
const ctxPrefix = computed(() => {
  const parts = ctxLevels.value.filter((l) => state.ctxOn[l.idx]).sort((a, b) => b.idx - a.idx)
  return parts.map((l) => ctxSeg(l.node)).join(' ')
})

const selector = computed(() => {
  const n = node.value
  if (!n) return ''
  if (state.useId && n.id) return '#' + cssEsc(n.id) + (state.useNth ? `:nth-of-type(${n.nth})` : '')
  let s = state.useTag ? n.tag : ''
  for (const c of n.classes) if (state.classOn[c.n]) s += '.' + cssEsc(c.n)
  for (const a of n.attrs) {
    const op = state.attrOp[a.n]
    if (op === 'prefix' && a.prefix) s += `[${a.n}^="${cssAttr(a.prefix)}"]`
    else if (op === 'exact') s += `[${a.n}="${cssAttr(a.v)}"]`
  }
  if (state.useNth) s += `:nth-of-type(${n.nth})`
  const leaf = s || n.tag
  return ctxPrefix.value ? `${ctxPrefix.value} ${leaf}` : leaf
})

// live-preview whenever the composed selector or the anchor toggle changes —
// with the anchor on, the preview tests the LABEL match (what auto-fill runs)
watch([selector, () => state.useAnchor], ([sel]) => {
  state.index = 0
  emit('probe', { selector: sel, anchor: state.useAnchor && props.anchor ? props.anchor : '' })
}, { immediate: true })
// land the position on the element the user actually clicked, never on match #1
watch(() => [props.probe.count, props.probe.pickedIndex], () => {
  if (props.probe.pickedIndex >= 0) {
    state.index = props.probe.pickedIndex
  } else if (state.useAnchor) {
    // the label match can't find (or doesn't contain) the clicked element — a
    // wrong label (page furniture) or one that resolves to nothing; drop back
    // to the selector so a bad anchor can never hijack OR dead-end the pick
    state.useAnchor = false
  }
})

function stepIndex(d: number) {
  state.index = Math.max(0, Math.min(props.probe.count - 1, state.index + d))
}
// exactly what would be stored (year → year, status → vocab, list item → cleaned)
function cleanView(v: string | undefined): string {
  const r = captureValue(props.field, v ?? '', { lower: state.lower, stripCounts: state.stripCounts })
  return Array.isArray(r) ? (r[0] ?? '') : String(r)
}

function use() {
  emit('use', {
    selector: selector.value,
    mode: state.scope === 'all' ? 'list' : 'single',
    index: state.scope === 'one' ? state.index : 0,
    lower: state.lower,
    stripCounts: state.stripCounts,
    useAnchor: state.useAnchor,
    values: state.scope === 'all' ? props.probe.values.slice() : [props.probe.values[state.index] ?? ''],
    preview: props.probe.preview,
  })
}
</script>

<template>
  <div class="pi">
    <div class="ihead">
      <span class="ifield">Refine pick</span><span class="ifor">{{ field }}</span>
      <div style="flex:1"></div>
      <span class="ix" title="Cancel" @click="emit('cancel')"><Icon name="x" :size="14" :sw="2" /></span>
    </div>
    <div class="ibody scroll">
      <!-- breadcrumb -->
      <div class="isec">
        <span class="ilbl">DOM PATH · click a level to target it</span>
        <div class="crumbs">
          <template v-for="(c, i) in crumbs" :key="c.idx">
            <span class="crumb" :class="{ cur: c.idx === state.target }" @click="retarget(c.idx)">{{ c.label }}</span>
            <span v-if="i < crumbs.length - 1" class="sep">›</span>
          </template>
        </div>
      </div>

      <!-- match-on toggles -->
      <div v-if="node" class="isec">
        <span class="ilbl">MATCH ON</span>
        <div class="mrow">
          <span class="mk">tag</span>
          <button class="tok" :class="{ on: state.useTag }" @click="state.useTag = !state.useTag"><span class="box">{{ state.useTag ? '✓' : '' }}</span>{{ node.tag }}</button>
        </div>
        <div v-if="node.classes.length" class="mrow">
          <span class="mk">classes</span>
          <button v-for="c in node.classes" :key="c.n" class="tok" :class="{ on: state.classOn[c.n], off: !state.classOn[c.n], dyn: c.dyn }" @click="state.classOn[c.n] = !state.classOn[c.n]">
            <span class="box">{{ state.classOn[c.n] ? '✓' : '' }}</span>{{ c.n }}<span v-if="c.dyn" class="why">· dynamic</span>
          </button>
        </div>
        <div v-if="node.id" class="mrow">
          <span class="mk">id</span>
          <button class="tok" :class="{ on: state.useId }" @click="state.useId = !state.useId"><span class="box">{{ state.useId ? '✓' : '' }}</span>#{{ node.id }}</button>
        </div>
        <div v-for="a in node.attrs" :key="a.n" class="mrow">
          <span class="mk">{{ a.n }}</span>
          <template v-if="a.url">
            <div class="seg">
              <button :class="{ on: state.attrOp[a.n] === 'off' }" @click="state.attrOp[a.n] = 'off'">off</button>
              <button v-if="a.prefix" :class="{ on: state.attrOp[a.n] === 'prefix' }" @click="state.attrOp[a.n] = 'prefix'">starts with</button>
              <button :class="{ on: state.attrOp[a.n] === 'exact' }" @click="state.attrOp[a.n] = 'exact'">exact</button>
            </div>
            <span class="aval mono">{{ state.attrOp[a.n] === 'prefix' ? a.prefix : a.v }}</span>
          </template>
          <button v-else class="tok" :class="{ on: state.attrOp[a.n] === 'exact' }" @click="state.attrOp[a.n] = state.attrOp[a.n] === 'exact' ? 'off' : 'exact'">
            <span class="box">{{ state.attrOp[a.n] === 'exact' ? '✓' : '' }}</span>{{ a.v.slice(0, 28) }}
          </button>
        </div>
        <div v-if="ctxLevels.length" class="mrow">
          <span class="mk">inside</span>
          <button v-for="l in ctxLevels" :key="l.idx" class="tok" :class="{ on: state.ctxOn[l.idx] }"
                  title="Scope the match under this ancestor"
                  @click="state.ctxOn[l.idx] = !state.ctxOn[l.idx]">
            <span class="box">{{ state.ctxOn[l.idx] ? '✓' : '' }}</span>{{ ctxSeg(l.node) }}
          </button>
        </div>
      </div>

      <!-- row-label anchor: the position-independent way to find this value -->
      <div v-if="props.anchor && field !== 'cover'" class="isec">
        <span class="ilbl">ROW LABEL</span>
        <button class="tok" :class="{ on: state.useAnchor }" @click="state.useAnchor = !state.useAnchor">
          <span class="box">{{ state.useAnchor ? '✓' : '' }}</span>value next to “{{ props.anchor }}”
        </button>
        <span class="anote">Matches by the label, not by class or position — survives reordered info rows on other pages. Tried first; the selector below stays as a fallback.</span>
      </div>

      <!-- scope -->
      <div class="isec">
        <span class="ilbl">SCOPE</span>
        <div class="scope">
          <div class="iradio" :class="{ on: state.scope === 'one' }" @click="state.scope = 'one'"><span class="dot"><span v-if="state.scope === 'one'"></span></span><div><div class="rl">This one</div><div class="rh">single value</div></div></div>
          <div class="iradio" :class="{ on: state.scope === 'all' }" @click="state.scope = 'all'"><span class="dot"><span v-if="state.scope === 'all'"></span></span><div><div class="rl">All matching</div><div class="rh">list</div></div></div>
        </div>
      </div>

      <!-- position — ALWAYS shown when the selector matches more than one.
           It starts on the element the user clicked; stepping is a correction. -->
      <div v-if="probe.count > 1" class="isec">
        <span class="ilbl">POSITION · which of the {{ probe.count }} to take</span>
        <div class="posrow">
          <button class="pbtn" :disabled="state.scope === 'all' || state.index <= 0" @click="state.scope = 'one'; stepIndex(-1)">‹</button>
          <span class="pnum mono">{{ state.scope === 'all' ? 'all' : `${state.index + 1} / ${probe.count}` }}</span>
          <button class="pbtn" :disabled="state.scope === 'all' || state.index >= probe.count - 1" @click="state.scope = 'one'; stepIndex(1)">›</button>
          <span v-if="state.scope === 'one' && state.index === probe.pickedIndex" class="pickedmark">✓ your click</span>
          <span v-if="state.scope === 'one'" class="pval">{{ probe.values[state.index] || '—' }}<span v-if="cleanView(probe.values[state.index]) !== probe.values[state.index]" class="pstore"> → {{ cleanView(probe.values[state.index]) || '∅' }}</span></span>
        </div>
        <span v-if="state.scope === 'one' && probe.count > 12" class="anote">Loose match — enable an “inside” ancestor or a class above instead of relying on position.</span>
      </div>

      <!-- cleanup flags (per field) -->
      <div v-if="field !== 'cover'" class="isec">
        <span class="ilbl">CLEANUP</span>
        <div class="mrow">
          <button class="tok" :class="{ on: state.stripCounts }" @click="state.stripCounts = !state.stripCounts"><span class="box">{{ state.stripCounts ? '✓' : '' }}</span>strip counts</button>
          <button class="tok" :class="{ on: state.lower }" @click="state.lower = !state.lower"><span class="box">{{ state.lower ? '✓' : '' }}</span>lowercase</button>
        </div>
      </div>

      <!-- selector + truthful preview -->
      <div class="isec">
        <span class="ilbl">SELECTOR</span>
        <div class="selbox mono">{{ selector || '—' }}</div>
        <div class="matchline" :class="{ warn: !probe.count }">
          {{ state.useAnchor && props.anchor
            ? (probe.count ? `✓ label “${props.anchor}” → ${probe.count} value${probe.count === 1 ? '' : 's'}` : `label “${props.anchor}” not found — selector fallback applies`)
            : (probe.count ? `✓ matches ${probe.count}` : 'no matches') }}
        </div>
        <div v-if="field === 'cover'" class="covprevi">
          <span class="thumb" :style="{ backgroundImage: `url(${probe.preview || probe.values[0] || ''})` }"></span>
          <span class="curl mono">{{ probe.values[0] || '—' }}</span>
        </div>
        <div v-else-if="state.scope === 'all'" class="pvv">
          <span v-for="(v, i) in probe.values.slice(0, 12)" :key="i" class="pvchip">{{ cleanView(v) }}</span>
          <span v-if="probe.values.length > 12" class="pvchip more">+{{ probe.values.length - 12 }}</span>
        </div>
        <div v-else class="pvv"><span class="pvchip">{{ cleanView(probe.values[state.index]) || '∅' }}</span></div>
      </div>
    </div>
    <div class="ifoot">
      <button class="btn ghost" title="Click a different element on the page" @click="emit('repick')">Pick again</button>
      <button class="btn accent" :disabled="!probe.count" @click="use">Use</button>
    </div>
  </div>
</template>

<style scoped>
.pi { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.ihead { display: flex; align-items: center; gap: 8px; padding: 12px 14px; border-bottom: 1px solid var(--line); }
.ifield { font: 600 13px/1 system-ui; color: var(--tx); }
.ifor { font: 700 10px/1 ui-monospace, monospace; letter-spacing: .08em; color: var(--accent); background: var(--accentSoft); padding: 4px 7px; border-radius: 5px; text-transform: uppercase; }
.ix { cursor: pointer; color: var(--tx3); display: flex; }
.ix:hover { color: var(--tx); }
.ibody { flex: 1; min-height: 0; overflow: auto; padding: 12px 14px; display: flex; flex-direction: column; gap: 14px; }
.isec { display: flex; flex-direction: column; gap: 7px; }
.ilbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .1em; color: var(--tx3); }
.crumbs { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.crumb { font: 500 11px/1 ui-monospace, monospace; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); padding: 4px 7px; border-radius: 5px; cursor: pointer; }
.crumb:hover { color: var(--tx); border-color: var(--tx3); }
.crumb.cur { color: var(--accent); border-color: var(--accent); background: var(--accentSoft); }
.sep { color: var(--tx3); font-size: 10px; }
.mrow { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
.mk { font: 600 9.5px/1 ui-monospace, monospace; color: var(--tx3); width: 52px; flex: none; }
.tok { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--line); background: var(--panel); color: var(--tx2); font: 500 11px/1 ui-monospace, monospace; padding: 4px 7px; border-radius: 5px; cursor: pointer; }
.tok .box { width: 11px; height: 11px; border: 1px solid var(--line); border-radius: 3px; display: inline-flex; align-items: center; justify-content: center; font-size: 8px; }
.tok.on { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 50%, var(--line)); }
.tok.on .box { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.tok.dyn { opacity: .75; }
.tok .why { color: var(--warn); font-size: 9px; }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
.seg button { border: none; background: transparent; color: var(--tx2); font: 500 10.5px/1 system-ui; padding: 5px 8px; cursor: pointer; }
.seg button + button { border-left: 1px solid var(--line); }
.seg button.on { background: var(--accentSoft); color: var(--accent); }
.aval { font-size: 10px; color: var(--tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 160px; }
.scope { display: flex; gap: 8px; }
.iradio { flex: 1; display: flex; gap: 8px; align-items: center; border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; cursor: pointer; }
.iradio.on { border-color: var(--accent); background: var(--accentSoft); }
.iradio .dot { width: 14px; height: 14px; border-radius: 50%; border: 1.5px solid var(--line); display: inline-flex; align-items: center; justify-content: center; }
.iradio.on .dot { border-color: var(--accent); }
.iradio .dot span { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); }
.rl { font: 600 11.5px/1 system-ui; color: var(--tx); }
.rh { margin-top: 3px; font: 400 10px/1 system-ui; color: var(--tx3); }
.posrow { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.pbtn { width: 24px; height: 24px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); color: var(--tx); cursor: pointer; }
.pbtn:disabled { opacity: .4; cursor: default; }
.pnum { font-size: 11px; color: var(--tx2); }
.pval { font: 400 11px/1.4 system-ui; color: var(--tx2); min-width: 0; }
.pstore { color: var(--accent); }
.selbox { font-size: 11px; color: var(--tx); background: var(--panel2); border: 1px solid var(--line); border-radius: 6px; padding: 7px 9px; word-break: break-all; }
.matchline { font: 600 10.5px/1 system-ui; color: var(--good); }
.matchline.warn { color: var(--warn); }
.covprevi { display: flex; gap: 10px; align-items: center; }
.thumb { width: 52px; height: 74px; border-radius: 5px; border: 1px solid var(--line); background: var(--panel2) center/cover no-repeat; flex: none; }
.curl { font-size: 10px; color: var(--tx3); word-break: break-all; }
.pvv { display: flex; flex-wrap: wrap; gap: 5px; }
.pvchip { font: 500 10.5px/1.3 system-ui; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); padding: 4px 7px; border-radius: 5px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pvchip.more { color: var(--tx3); }
.anote { font: 400 10px/1.5 system-ui; color: var(--tx3); }
.pickedmark { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .05em; color: var(--good); border: 1px solid color-mix(in srgb, var(--good) 40%, var(--line)); padding: 3px 6px; border-radius: 4px; flex: none; }
.ifoot { display: flex; gap: 8px; justify-content: flex-end; padding: 12px 14px; border-top: 1px solid var(--line); }
</style>
