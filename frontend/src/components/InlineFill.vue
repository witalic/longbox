<script setup lang="ts">
// The site's logins under the sign-in field the owner clicked
// (design/password-manager.md, "In the form"). The app's, drawn over the page —
// never in it — so the page can neither cover it, hide it, nor take a click on
// it. A click on a login fills the sign-in; one in steps gets its password as
// its step shows. Nothing while the logins are locked, or the site has none.
// A click is taken only half a second after the list last moved — where the
// field stands, how big the list is — so a page cannot bring it under the
// pointer as the owner clicks. It stays inside the page.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from './Icon.vue'
import CrossFillAsk from './CrossFillAsk.vue'
import { addressOf, ago } from '../data'
import { fillInto, loginsState, settled } from '../logins'

export interface FieldAt { n: number; x: number; y: number; w: number; h: number; at: number }

const props = defineProps<{ field: FieldAt; origin: string; guest: () => number | null }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'dismiss'): void; (e: 'manage'): void }>()

const WIDTH = 320
const MARGIN = 8
const SETTLE_MS = 500
const list = ref<LoginSummary[]>([])
const asking = ref<string | null>(null)
const error = ref('')
const root = ref<HTMLElement | null>(null)

// a list asked for before the logins locked does not land after
let asked = 0
async function load() {
  const shell = window.longbox?.logins
  const mine = ++asked
  list.value = []
  if (!shell || !loginsState.unlocked || !/^https?:\/\//.test(props.origin)) return
  const got = await settled(shell.list(props.origin)).catch(() => [] as LoginSummary[])
  if (mine === asked && loginsState.unlocked) list.value = got
}
watch([() => props.origin, () => loginsState.unlocked], load, { immediate: true })
watch(() => props.field.n, () => { asking.value = null; error.value = '' })

// its own size, and the page's, as they change
const height = ref(0)
const room = ref({ w: 0, h: 0 })
function measure() {
  height.value = root.value?.offsetHeight ?? 0
  const area = root.value?.parentElement
  if (area) room.value = { w: area.clientWidth, h: area.clientHeight }
}
const sized = new ResizeObserver(measure)
watch(root, (el, was) => {
  if (was) sized.unobserve(was)
  if (el) {
    measure() // at once: placed right from its first frame
    sized.observe(el)
    if (el.parentElement) sized.observe(el.parentElement)
  }
}, { flush: 'post' })

// under the field, its left edge on the field's — above it when there is no
// room below — and never out of the page
const box = computed(() => {
  const { w: roomW, h: roomH } = room.value
  const width = Math.max(0, Math.min(Math.max(WIDTH, props.field.w), roomW - 2 * MARGIN))
  const left = Math.max(MARGIN, Math.min(props.field.x, roomW - width - MARGIN))
  const below = props.field.y + props.field.h + 4
  const above = props.field.y - height.value - 4
  const want = below + height.value > roomH - MARGIN && above > MARGIN ? above : below
  const top = Math.max(MARGIN, Math.min(want, roomH - height.value - MARGIN))
  return { left: Math.round(left), top: Math.round(top), width: Math.round(width) }
})
let placedAt = Date.now()
watch(() => `${box.value.left} ${box.value.top} ${box.value.width} ${height.value}`, () => { placedAt = Date.now() })

// a press elsewhere in the app, or Esc, and it goes: the page tells only of its own
function outside(e: MouseEvent) {
  if (root.value && !root.value.contains(e.target as Node)) emit('close')
}
function esc(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => {
  document.addEventListener('mousedown', outside, true)
  document.addEventListener('keydown', esc, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', outside, true)
  document.removeEventListener('keydown', esc, true)
  sized.disconnect()
})

async function pick(login: LoginSummary, sure = false) {
  const guest = props.guest()
  if (guest === null || Date.now() - Math.max(props.field.at, placedAt) < SETTLE_MS) return
  error.value = ''
  try {
    const how = await fillInto(guest, login, props.origin, sure)
    asking.value = how === 'ask' ? login.id : null
    if (how === 'filled') emit('close')
    else if (how === 'gone') error.value = 'The page is no longer on this site.'
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
</script>

<template>
  <!-- a press here keeps the page's field focused: the list stays while it is used -->
  <div v-if="list.length" ref="root" class="ifill"
       :style="{ left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px` }" @mousedown.prevent>
    <div class="sh"><Icon name="key" :size="12" :sw="2" />longbox · logins for this site<span class="sp"></span>
      <button class="x" title="Not on this page" @click="emit('dismiss')"><Icon name="x" :size="12" :sw="2.2" /></button></div>
    <div class="rows">
      <template v-for="l in list" :key="l.id">
        <button class="srow" @click="pick(l)">
          <span class="av">{{ (l.username || '?').slice(0, 1).toUpperCase() }}</span>
          <span class="who"><b>{{ l.username || '(no username)' }}</b><span><template v-if="l.origin !== origin"><span
            class="mono">{{ addressOf(l.origin) }}</span> · </template>{{ l.used ? `used ${ago(l.used)}` : 'not used yet' }}</span></span>
          <span class="go">Fill</span>
        </button>
        <CrossFillAsk v-if="asking === l.id" :from="l.origin" :here="origin" @yes="pick(l, true)" @no="asking = null" />
      </template>
    </div>
    <div v-if="error" class="err">{{ error }}</div>
    <button class="sf" @click="emit('manage')"><Icon name="key" :size="12" :sw="2" />Manage logins…</button>
  </div>
</template>

<style scoped>
.ifill { position: absolute; z-index: 6; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 22px 50px rgba(0,0,0,.6); overflow: hidden; }
.sh { display: flex; align-items: center; gap: 7px; height: 30px; padding: 0 6px 0 11px; border-bottom: 1px solid var(--line); font: 500 11px/1 system-ui; color: var(--tx3); }
.sh > :first-child { color: var(--accent); }
.sp { flex: 1; }
.x { width: 20px; height: 20px; border: none; border-radius: 6px; background: transparent; color: var(--tx3); display: inline-flex; align-items: center; justify-content: center; cursor: pointer; }
.x:hover { background: var(--hover); color: var(--tx); }
.rows { padding: 4px; }
.srow { width: 100%; display: flex; align-items: center; gap: 10px; height: 44px; padding: 0 8px; border: none; border-radius: 6px; background: transparent; color: inherit; text-align: left; cursor: pointer; }
.srow:hover { background: var(--hover); }
.av { width: 26px; height: 26px; border-radius: 999px; background: var(--panel2); border: 1px solid var(--line); display: inline-flex; align-items: center; justify-content: center; font: 700 11px/1 system-ui; color: var(--tx2); flex: none; }
.who { flex: 1; min-width: 0; }
.who b { display: block; font: 500 12.5px/1.2 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.who > span { display: block; font: 400 11px/1.3 system-ui; color: var(--tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.go { font: 600 11px/1 system-ui; color: var(--accent); opacity: 0; }
.srow:hover .go { opacity: 1; }
.err { padding: 4px 11px 8px; font: 400 12px/1.4 system-ui; color: var(--adult); }
.sf { width: 100%; display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 11px; border: none; border-top: 1px solid var(--line); background: transparent; color: var(--tx2); font: 500 11.5px/1 system-ui; cursor: pointer; }
.sf:hover { background: var(--hover); color: var(--tx); }
</style>
