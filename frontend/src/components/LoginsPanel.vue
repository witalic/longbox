<script setup lang="ts">
// What the key in the browser's toolbar opens (design/password-manager.md): the
// logins of the page's site — its own address's first, another address of the
// site named on its row — with Fill, copy the username or the password, and a
// pencil that turns the popover into the login editor, in place; a new one for
// this site, and Lock. Locked, it asks for the master password first; with none
// yet, it offers to set one.
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import LoginEditor from './LoginEditor.vue'
import { addressOf, ago, hostOf } from '../data'
import { askMaster, crossFills, lockLogins, loginsState, settled, showLoginsIn } from '../logins'
import { store } from '../store'
import { showSources } from '../nav'

// `guest`: the page a login would be filled into, now
const props = defineProps<{ origin: string; guest: () => number | null }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'changed'): void }>()

const shell = window.longbox?.logins
const site = computed(() => (/^https?:\/\//.test(props.origin) ? props.origin : ''))
const list = ref<LoginSummary[]>([])
const error = ref('')
const note = ref('')
// the editor: the login (null: a new one) and the site it was opened for;
// `bound`: it is this tab's address's, so the editor follows where the tab goes
const editing = ref<{ login: LoginSummary | null; origin: string; bound: boolean } | null>(null)

async function load() {
  error.value = note.value = ''
  if (!shell || !loginsState.unlocked || !site.value) {
    list.value = []
    return
  }
  try {
    list.value = await settled(shell.list(site.value))
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
watch([() => loginsState.unlocked, site], load, { immediate: true })

async function unlock() {
  if (await askMaster()) await load()
}
// a login of another address of the site, asked about before its first fill here
const asking = ref<string | null>(null)
async function fill(login: LoginSummary, sure = false) {
  const guest = props.guest()
  if (!shell || guest === null) return
  const cross = `${login.id} ${site.value}`
  if (login.origin !== site.value && !sure && !crossFills.has(cross)) {
    asking.value = login.id
    return
  }
  asking.value = null
  if (login.origin !== site.value) crossFills.add(cross)
  try {
    if (await settled(shell.fill(guest, login.id))) emit('close')
    else error.value = 'The page is no longer on this site.'
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
async function copy(login: LoginSummary, what: 'username' | 'password') {
  if (!shell) return
  error.value = note.value = ''
  try {
    await settled(what === 'password' ? shell.copy(login.id) : shell.copyName(login.username))
    note.value = what === 'password' ? 'The password is copied — it leaves the clipboard after 30 seconds.'
      : 'The username is copied.'
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
async function done() {
  editing.value = null
  await load()
  emit('changed')
}
async function lock() {
  await lockLogins()
  emit('close')
}
// the site's logins, all of them, where Sources keeps the site — when it does
const sourceSite = computed(() => store.sources.find((s) => s.domain === hostOf(site.value))?.domain ?? '')
function inSources() {
  showLoginsIn.domain = sourceSite.value
  showSources()
  emit('close')
}
</script>

<template>
  <div class="lp" :class="{ wide: editing }">
    <template v-if="editing">
      <div class="head">
        <button class="iconbtn plain" title="Back to the logins" @click="editing = null"><Icon name="back" :size="14" :sw="2" /></button>
        <span class="t">{{ editing.login ? 'Edit login' : 'New login' }}</span>
        <button class="iconbtn plain" title="Close" @click="emit('close')"><Icon name="x" :size="13" :sw="2.2" /></button>
      </div>
      <LoginEditor :login="editing.login" :origin="editing.origin" :here="editing.bound ? site : undefined" @saved="done" @deleted="done"
                   @cancel="editing = null" />
    </template>
    <template v-else>
      <div class="head">
        <Icon name="key" :size="15" :sw="2" class="ki" />
        <span class="t">Logins for this site<small class="mono">{{ site || 'no site' }}</small></span>
        <span v-if="loginsState.unlocked" class="chip"><Icon name="unlock" :size="11" :sw="2" />Unlocked</span>
      </div>
      <div class="rows">
        <div v-if="!site" class="say">Logins belong to a site — this page is not one.</div>
        <template v-else-if="!loginsState.unlocked">
          <button class="mi" @click="unlock"><Icon name="lock" :size="14" :sw="2" />
            {{ loginsState.exists ? 'Unlock…' : 'Set a master password…' }}</button>
          <div class="say">{{ loginsState.exists ? 'The logins open with the master password.'
            : 'Logins are kept in the vault, encrypted under a master password of yours.' }}</div>
        </template>
        <template v-else>
          <div v-if="loginsState.restored" class="say warn">The logins file did not read, so its second copy was
            opened; the next change writes the file again.</div>
          <template v-for="l in list" :key="l.id">
            <div class="acct">
              <span class="av">{{ (l.username || '?').slice(0, 1).toUpperCase() }}</span>
              <span class="who"><b>{{ l.username || '(no username)' }}</b><span><template v-if="l.origin !== site"><span
                class="mono">{{ addressOf(l.origin) }}</span> · </template>{{ l.used ? `used ${ago(l.used)}` : 'not used yet' }}</span></span>
              <button class="btn small accent" @click="fill(l)">Fill</button>
              <button class="iconbtn plain" title="Copy the username" :disabled="!l.username" @click="copy(l, 'username')">
                <Icon name="authors" :size="13" :sw="2" /></button>
              <button class="iconbtn plain" title="Copy the password — it leaves the clipboard after 30 seconds"
                      @click="copy(l, 'password')"><Icon name="key" :size="13" :sw="2" /></button>
              <button class="iconbtn plain" title="Edit" @click="editing = { login: l, origin: l.origin, bound: l.origin === site }">
                <Icon name="edit" :size="13" :sw="2" /></button>
            </div>
            <div v-if="asking === l.id" class="ask">
              <span>Fill the login saved for <b class="mono">{{ addressOf(l.origin) }}</b> into this page, on
                <b class="mono">{{ addressOf(site) }}</b>? Asked once for each address, until the logins lock.</span>
              <button class="btn small accent" @click="fill(l, true)">Fill here</button>
              <button class="btn small ghost" @click="asking = null">Cancel</button>
            </div>
          </template>
          <div v-if="!list.length" class="say">Nothing saved for this site yet.</div>
          <hr />
          <button class="mi" @click="editing = { login: null, origin: site, bound: true }"><Icon name="plus" :size="14" :sw="2" />Add a login for this site</button>
          <button v-if="sourceSite" class="mi" @click="inSources"><Icon name="sources" :size="14" :sw="2" />All of this site's logins — Sources</button>
          <button class="mi" @click="lock"><Icon name="lock" :size="14" :sw="2" />Lock passwords</button>
        </template>
        <div v-if="error" class="err">{{ error }}</div>
        <div v-else-if="note" class="say">{{ note }}</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.lp { width: 340px; }
.lp.wide { width: 430px; }
.head { display: flex; align-items: center; gap: 8px; padding: 10px 10px 10px 12px; border-bottom: 1px solid var(--line); min-height: 44px; }
.head .ki { color: var(--accent); }
.t { flex: 1; min-width: 0; font: 600 13px/1.2 system-ui; color: var(--tx); }
.t small { display: block; margin-top: 3px; font-size: 11px; font-weight: 400; color: var(--tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chip { display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 8px; border-radius: 999px; border: 1px solid var(--line); background: var(--panel2); font: 500 11px/1 system-ui; color: var(--tx2); flex: none; }
.head .iconbtn { width: 28px; height: 28px; }
.rows { padding: 6px; }
.acct { display: flex; align-items: center; gap: 6px; height: 44px; padding: 0 4px 0 9px; border-radius: 6px; }
.acct:hover { background: var(--hover); }
.av { margin-right: 4px; width: 26px; height: 26px; border-radius: 999px; background: var(--panel2); border: 1px solid var(--line); display: inline-flex; align-items: center; justify-content: center; font: 700 11px/1 system-ui; color: var(--tx2); flex: none; }
.who { flex: 1; min-width: 0; }
.who b { display: block; font: 500 12.5px/1.2 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.who > span { display: block; font: 400 11px/1.3 system-ui; color: var(--tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.acct .iconbtn { width: 28px; height: 28px; }
.ask { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; padding: 2px 9px 10px; font: 400 12px/1.45 system-ui; color: var(--tx2); }
.ask span { flex: 1 1 100%; }
.ask b { font-weight: 600; color: var(--tx); }
.mi { width: 100%; display: flex; align-items: center; gap: 10px; height: 32px; padding: 0 9px; border: none; background: transparent; border-radius: 6px; color: var(--tx2); font: 500 12.5px/1 system-ui; cursor: pointer; text-align: left; }
.mi:hover { background: var(--hover); color: var(--tx); }
hr { height: 1px; border: none; background: var(--line); margin: 5px 4px; }
.say { padding: 8px 9px; font: 400 12px/1.45 system-ui; color: var(--tx3); }
.say.warn { color: var(--warn); }
.err { padding: 6px 9px; font: 400 12px/1.4 system-ui; color: var(--adult); }
</style>
