<script setup lang="ts">
// One login, edited (design/password-manager.md) — in the key's popover in the
// browser, and in Sources → a site → Logins. Its site is fixed: the origin it
// was opened for, never typed. Everything is staged until Save, which is one
// write: the username, the note, and the steps in the order they were made — a
// new password, a previous one used again, one forgotten. A replaced password
// goes to the top of the previous ones; three at most, and the editor shows
// the one Save would drop before it does.
import { computed, reactive, ref } from 'vue'
import Icon from './Icon.vue'
import { askConfirm } from '../confirm'
import { shortDate, settled } from '../logins'

const props = defineProps<{
  login: LoginSummary | null // null: a new one
  origin: string             // the site it belongs to
  here?: string              // the tab's site now, when it is edited from a tab
}>()
const emit = defineEmits<{ (e: 'saved'): void; (e: 'cancel'): void; (e: 'deleted'): void }>()

const KEEP = 3
const shell = window.longbox?.logins
const username = ref(props.login?.username ?? '')
const note = ref(props.login?.note ?? '')
const fresh = ref('') // a new login's password
const steps = ref<LoginStep[]>([])
const shown = reactive<Record<string, string>>({}) // a password shown, by what it is
const error = ref('')
const busy = ref(false)

// What the password and the previous ones are after the steps so far: the
// current one ('cur'), the i-th previous one ('prev'), or the s-th step's new one.
type Tok = { k: 'cur' } | { k: 'prev'; i: number } | { k: 'new'; s: number }
const keyOf = (t: Tok) => (t.k === 'cur' ? 'c' : t.k === 'prev' ? `p${t.i}` : `n${t.s}`)
const now = computed(() => {
  let cur = { k: 'cur' } as Tok
  let prev: Tok[] = (props.login?.previous ?? []).map((_, i) => ({ k: 'prev', i }))
  const dropped: Tok[] = []
  steps.value.forEach((step, s) => {
    if ('password' in step) {
      prev = [cur, ...prev]
      cur = { k: 'new', s }
    } else if ('use' in step) {
      const back = prev[step.use]
      prev = [cur, ...prev.filter((_, j) => j !== step.use)]
      cur = back
    } else {
      prev = prev.filter((_, j) => j !== step.forget)
    }
    dropped.push(...prev.slice(KEEP))
    prev = prev.slice(0, KEEP)
  })
  return { cur, prev, dropped }
})
const changed = computed(() => !props.login || steps.value.length > 0
  || username.value !== props.login.username || note.value !== props.login.note)

async function reveal(t: Tok) {
  const k = keyOf(t)
  if (k in shown) {
    delete shown[k]
    return
  }
  if (t.k === 'new') shown[k] = (steps.value[t.s] as { password: string }).password
  else if (props.login && shell) {
    shown[k] = await settled(shell.reveal(props.login.id, t.k === 'prev' ? t.i : undefined)).catch(() => '')
  }
}
async function copy(t: Tok) {
  if (!shell) return
  if (t.k === 'new') await settled(shell.copyText((steps.value[t.s] as { password: string }).password))
  else if (props.login) await settled(shell.copy(props.login.id, t.k === 'prev' ? t.i : undefined))
}

// The password field: a new one is typed over whatever it held; cleared, the
// new one is taken back.
const cur = computed(() => now.value.cur)
const curValue = computed(() => (cur.value.k === 'new'
  ? (steps.value[cur.value.s] as { password: string }).password : shown[keyOf(cur.value)] ?? ''))
function typed(v: string) {
  const c = cur.value
  if (c.k === 'new') {
    if (v === '' && c.s === steps.value.length - 1) steps.value.pop()
    else steps.value[c.s] = { password: v }
  } else if (v !== '') {
    steps.value.push({ password: v })
    shown[`n${steps.value.length - 1}`] = v
  }
}
async function generate() {
  if (!shell) return
  const v = await settled(shell.generate())
  if (!props.login) {
    fresh.value = v
    shown.fresh = v
    return
  }
  if (cur.value.k !== 'new') steps.value.push({ password: v })
  else steps.value[cur.value.s] = { password: v }
  shown[keyOf(cur.value)] = v
}

function whenOf(t: Tok): string {
  if (t.k === 'prev') return `replaced ${shortDate(props.login?.previous[t.i]?.replaced ?? '')}`
  return t.k === 'cur' ? 'the current one, from now' : 'typed, from now'
}

async function save() {
  if (!shell || busy.value) return
  busy.value = true
  error.value = ''
  try {
    if (props.login) {
      // plain copies: what crosses to the shell is cloned, and a reactive list is not
      await settled(shell.edit(props.login.id, { username: username.value, note: note.value,
        steps: steps.value.map((step) => ({ ...step })) }))
    } else {
      await settled(shell.add({ origin: props.origin, username: username.value, password: fresh.value, note: note.value }))
    }
    emit('saved')
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}

async function remove() {
  const login = props.login
  if (!shell || !login) return
  const n = login.previous.length
  const ok = await askConfirm({
    title: 'Delete this login?', okLabel: 'Delete', danger: true,
    message: `${login.username || 'This login'} on ${new URL(login.origin).host} — its password`
      + (n ? ` and its ${n} previous password${n === 1 ? '' : 's'}` : '')
      + ' are deleted from the vault. This cannot be undone. The account on the site stays as it is: only'
      + ' longbox forgets it.',
  })
  if (!ok) return
  try {
    await settled(shell.remove(login.id))
    emit('deleted')
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
</script>

<template>
  <div class="led">
    <div class="grid">
      <span class="k">Site</span>
      <div class="origin mono" :title="origin"><Icon name="lock" :size="11" :sw="2" /><span>{{ origin }}</span>
        <span v-if="here !== undefined" class="why">this tab</span></div>
      <span class="k">Username</span>
      <input v-model="username" class="in" spellcheck="false" autocomplete="off" />
      <span class="k">Password
        <b v-if="cur.k === 'new' || (!login && fresh)" class="mark">NEW</b>
        <b v-else-if="cur.k === 'prev'" class="mark">AGAIN</b></span>
      <div class="pw">
        <input v-if="login" class="in" :class="{ mono: keyOf(cur) in shown || cur.k === 'new' }"
               :type="keyOf(cur) in shown ? 'text' : 'password'" :value="curValue" spellcheck="false" autocomplete="off"
               :placeholder="cur.k === 'new' ? '' : '••••••••••••'"
               @input="typed(($event.target as HTMLInputElement).value)" />
        <input v-else v-model="fresh" class="in" :class="{ mono: 'fresh' in shown }"
               :type="'fresh' in shown ? 'text' : 'password'" spellcheck="false" autocomplete="off" />
        <span class="acts">
          <button v-if="login" class="iconbtn plain" :title="keyOf(cur) in shown ? 'Hide' : 'Show'" @click="reveal(cur)">
            <Icon :name="keyOf(cur) in shown ? 'eyeoff' : 'eye'" :size="14" :sw="2" /></button>
          <button v-else class="iconbtn plain" :title="'fresh' in shown ? 'Hide' : 'Show'"
                  @click="'fresh' in shown ? delete shown.fresh : (shown.fresh = fresh)">
            <Icon :name="'fresh' in shown ? 'eyeoff' : 'eye'" :size="14" :sw="2" /></button>
          <button class="iconbtn plain" title="Copy — it leaves the clipboard after 30 seconds"
                  @click="login ? copy(cur) : shell && settled(shell.copyText(fresh))">
            <Icon name="copy" :size="14" :sw="2" /></button>
          <button class="iconbtn plain" title="Generate a new one" @click="generate"><Icon name="wand" :size="14" :sw="2" /></button>
        </span>
      </div>
      <span class="k top">Note</span>
      <textarea v-model="note" class="in note" rows="2" spellcheck="false"></textarea>
    </div>

    <div v-if="here !== undefined && here !== origin" class="tell warn"><Icon name="warn" :size="13" :sw="2" />
      <span>This tab is now on {{ here || 'another page' }} — this login stays with {{ origin }}.</span></div>
    <div v-if="cur.k === 'new'" class="tell"><Icon name="info" :size="13" :sw="2" />
      <span>Save changes the password here only — change it on the site too, in its own form. The password it replaces
        moves to the previous ones.</span></div>

    <div v-if="login && (now.prev.length || now.dropped.length)" class="prev">
      <div class="plbl"><Icon name="clock" :size="11" :sw="2" />PREVIOUS PASSWORDS
        <span class="n">{{ steps.length ? 'ON SAVE: ' : '' }}{{ now.prev.length }} OF {{ KEEP }}</span></div>
      <div v-for="(t, j) in now.prev" :key="keyOf(t)" class="prow" :class="{ fresh: t.k !== 'prev' }">
        <span class="pwv mono" :class="{ shown: keyOf(t) in shown }">{{ shown[keyOf(t)] ?? '••••••••••••' }}</span>
        <span class="when">{{ whenOf(t) }}</span>
        <span class="acts">
          <button class="iconbtn plain" :title="keyOf(t) in shown ? 'Hide' : 'Show'" @click="reveal(t)">
            <Icon :name="keyOf(t) in shown ? 'eyeoff' : 'eye'" :size="13" :sw="2" /></button>
          <button class="iconbtn plain" title="Copy" @click="copy(t)"><Icon name="copy" :size="13" :sw="2" /></button>
          <button class="iconbtn plain" title="Use again — the password it replaces becomes a previous one"
                  @click="steps.push({ use: j })"><Icon name="undo" :size="13" :sw="2" /></button>
          <button class="iconbtn plain" title="Forget this one" @click="steps.push({ forget: j })">
            <Icon name="x" :size="13" :sw="2.2" /></button>
        </span>
      </div>
      <div v-for="t in now.dropped" :key="`d-${keyOf(t)}`" class="prow going">
        <span class="pwv mono">••••••••••••</span><span class="when">{{ whenOf(t) }} — dropped on Save</span>
      </div>
    </div>

    <div v-if="error" class="err">{{ error }}</div>
    <div class="foot">
      <button v-if="login" class="btn small dghost" @click="remove"><Icon name="trash" :size="13" :sw="2" />Delete</button>
      <span class="sp"></span>
      <button class="btn small ghost" @click="emit('cancel')">Cancel</button>
      <button class="btn small accent" :disabled="busy || !changed || (!login && !fresh)" @click="save">Save</button>
    </div>
  </div>
</template>

<style scoped>
.led { display: flex; flex-direction: column; }
.grid { padding: 12px 14px 4px; display: grid; grid-template-columns: 74px minmax(0, 1fr); gap: 8px 10px; align-items: center; }
.k { font: 600 11px/1.2 system-ui; color: var(--tx2); display: flex; flex-direction: column; gap: 3px; }
.k.top { align-self: start; padding-top: 8px; }
.mark { font: 600 9.5px/1 system-ui; letter-spacing: .04em; color: var(--accent); }
.origin { display: flex; align-items: center; gap: 7px; height: 30px; padding: 0 10px; border-radius: 6px; background: var(--panel2); border: 1px dashed var(--line); font-size: 11.5px; color: var(--tx2); white-space: nowrap; overflow: hidden; min-width: 0; }
.origin :deep(svg) { color: var(--good); }
.origin > span:first-of-type { overflow: hidden; text-overflow: ellipsis; }
.why { margin-left: auto; font: 400 10.5px/1 system-ui; color: var(--tx3); }
.pw { position: relative; display: flex; align-items: center; }
.pw .in { padding-right: 92px; }
.pw .acts { position: absolute; right: 2px; display: flex; }
.pw .iconbtn, .prow .iconbtn { width: 28px; height: 26px; color: var(--tx3); }
.in.mono { font-family: ui-monospace, monospace; }
.note { resize: none; }
.tell { display: flex; gap: 8px; align-items: flex-start; margin: 6px 14px 0; padding: 8px 10px; border-radius: 6px; background: var(--accentSoft); font: 400 11.5px/1.45 system-ui; color: var(--tx2); }
.tell :deep(svg) { color: var(--accent); margin-top: 1px; }
.tell.warn { background: color-mix(in srgb, var(--warn) 12%, transparent); }
.tell.warn :deep(svg) { color: var(--warn); }
.prev { margin: 8px 14px 0; padding: 10px 0 2px; border-top: 1px solid var(--line); }
.plbl { display: flex; align-items: center; gap: 7px; font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); margin-bottom: 4px; }
.plbl .n { color: var(--tx2); }
.prow { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; align-items: center; gap: 8px; min-height: 32px; }
.pwv { font-size: 12px; color: var(--tx2); letter-spacing: 2px; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.pwv.shown { letter-spacing: 0; color: var(--tx); }
.when { font: 400 11px/1 system-ui; color: var(--tx3); white-space: nowrap; }
.prow .acts { display: flex; }
.prow.fresh .when { color: var(--accent); }
.prow.going { opacity: .5; grid-template-columns: minmax(0, 1fr) auto; }
.prow.going .pwv { text-decoration: line-through; }
.prow.going .when { color: var(--warn); }
.err { margin: 8px 14px 0; font: 400 12px/1.4 system-ui; color: var(--adult); }
.foot { display: flex; align-items: center; gap: 8px; padding: 10px 14px; margin-top: 8px; border-top: 1px solid var(--line); }
.sp { flex: 1; }
.btn.dghost { border-color: transparent; background: transparent; color: var(--adult); gap: 6px; }
.btn.dghost:hover:not(:disabled) { border-color: color-mix(in srgb, var(--adult) 40%, var(--line)); }
</style>
