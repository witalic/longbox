<script setup lang="ts">
// Sources → a site → Logins (design/password-manager.md): every login saved for
// the site, one card each — username, origin, last used, how many previous
// passwords; copy on the closed card, the pencil opens it into the login
// editor, in place. A new one's site is typed, starting from the site's
// homepage. And the site's origins where saving a login is never offered,
// each to be offered again.
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import LoginEditor from './LoginEditor.vue'
import { ago, hostOf } from '../data'
import { askMaster, loginsOf, loginsState, settled } from '../logins'

const props = defineProps<{ domain: string; homepage: string }>()
const emit = defineEmits<{ (e: 'count', n: number): void }>()

const shell = window.longbox?.logins
const list = ref<LoginSummary[]>([])
const nevers = ref<string[]>([])
const open = ref<string | null>(null) // a login's id, 'new', or none
const error = ref('')
const homeOrigin = computed(() => {
  try { return new URL(props.homepage || `https://${props.domain}/`).origin } catch { return `https://${props.domain}` }
})

async function load() {
  error.value = ''
  try {
    list.value = await loginsOf(props.domain)
    nevers.value = shell && loginsState.unlocked
      ? (await settled(shell.nevers())).filter((o) => hostOf(o) === props.domain) : []
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
  emit('count', list.value.length)
}
watch([() => loginsState.unlocked, () => props.domain], () => {
  open.value = null
  void load()
}, { immediate: true })

async function done() {
  open.value = null
  await load()
}
async function copy(id: string) {
  if (!shell) return
  await settled(shell.copy(id)).catch((e) => { error.value = e instanceof Error ? e.message : String(e) })
}
async function offerAgain(origin: string) {
  if (!shell) return
  await settled(shell.setNever(origin, false)).catch((e) => { error.value = String(e) })
  await load()
}
</script>

<template>
  <div class="slog">
    <div class="body scroll">
      <template v-if="!loginsState.unlocked">
        <div class="say">{{ loginsState.exists ? 'The logins open with the master password.'
          : 'Logins are kept in the vault, encrypted under a master password of yours.' }}</div>
        <button class="btn small" @click="askMaster"><Icon name="lock" :size="12" :sw="2" />
          {{ loginsState.exists ? 'Unlock…' : 'Set a master password…' }}</button>
      </template>
      <template v-else>
        <div v-for="l in list" :key="l.id" class="lcard" :class="{ open: open === l.id }">
          <div class="lrow">
            <span class="av">{{ (l.username || '?').slice(0, 1).toUpperCase() }}</span>
            <span class="who"><b>{{ l.username || '(no username)' }}</b>
              <span class="mono">{{ l.origin }}</span><span> · {{ l.used ? `used ${ago(l.used)}` : 'not used yet' }}</span></span>
            <span v-if="l.previous.length" class="chip"><Icon name="clock" :size="11" :sw="2" />{{ l.previous.length }} previous</span>
            <template v-if="open !== l.id">
              <button class="iconbtn plain" title="Copy the password — it leaves the clipboard after 30 seconds"
                      @click="copy(l.id)"><Icon name="copy" :size="13" :sw="2" /></button>
              <button class="iconbtn plain" title="Edit" @click="open = l.id"><Icon name="edit" :size="13" :sw="2" /></button>
            </template>
          </div>
          <LoginEditor v-if="open === l.id" :login="l" :origin="l.origin" @saved="done" @deleted="done"
                       @cancel="open = null" />
        </div>
        <div v-if="open === 'new'" class="lcard open">
          <div class="lrow"><span class="av"><Icon name="plus" :size="12" :sw="2.2" /></span>
            <span class="who"><b>New login</b></span></div>
          <LoginEditor :login="null" :origin="homeOrigin" pick-origin @saved="done" @cancel="open = null" />
        </div>
        <div v-if="!list.length && open !== 'new'" class="say">Nothing saved for this site yet. A sign-in in the
          browser offers to save it.</div>
        <div v-if="nevers.length" class="never">
          <div class="plbl">SAVING NEVER OFFERED</div>
          <div v-for="o in nevers" :key="o" class="nrow">
            <span class="mono">{{ o }}</span>
            <button class="btn small ghost" @click="offerAgain(o)">Offer again</button>
          </div>
        </div>
      </template>
      <div v-if="error" class="err">{{ error }}</div>
    </div>
    <div class="foot">
      <button class="btn small" :disabled="!loginsState.unlocked || open === 'new'" @click="open = 'new'">
        <Icon name="plus" :size="12" :sw="2.2" />Add login</button>
      <span class="sp"></span>
      <span class="mini">filled on click, in the browser</span>
    </div>
  </div>
</template>

<style scoped>
.slog { flex: 1; min-height: 0; display: flex; flex-direction: column; }
.body { flex: 1; min-height: 0; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.lcard { border: 1px solid var(--line); border-radius: 10px; background: var(--panel); flex: none; }
.lcard.open { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); }
.lrow { display: flex; align-items: center; gap: 10px; min-height: 52px; padding: 0 8px 0 12px; }
.av { width: 26px; height: 26px; border-radius: 999px; background: var(--panel2); border: 1px solid var(--line); display: inline-flex; align-items: center; justify-content: center; font: 700 11px/1 system-ui; color: var(--tx2); flex: none; }
.who { flex: 1; min-width: 0; font: 400 11px/1.4 system-ui; color: var(--tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.who b { display: block; font: 500 12.5px/1.3 system-ui; color: var(--tx); overflow: hidden; text-overflow: ellipsis; }
.who .mono { font-size: 11px; }
.chip { display: inline-flex; align-items: center; gap: 5px; height: 22px; padding: 0 8px; border-radius: 999px; border: 1px solid var(--line); background: var(--panel2); font: 500 11px/1 system-ui; color: var(--tx2); flex: none; }
.lrow .iconbtn { width: 28px; height: 28px; }
.say { font: 400 12px/1.5 system-ui; color: var(--tx3); }
.never { margin-top: 6px; padding-top: 12px; border-top: 1px solid var(--line); }
.plbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); margin-bottom: 6px; }
.nrow { display: flex; align-items: center; gap: 10px; min-height: 30px; font-size: 11.5px; color: var(--tx2); }
.nrow .mono { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.err { font: 400 12px/1.4 system-ui; color: var(--adult); }
.foot { height: 44px; flex: none; display: flex; align-items: center; gap: 8px; padding: 0 16px; border-top: 1px solid var(--line); }
.sp { flex: 1; }
.mini { font: 400 11px/1 system-ui; color: var(--tx3); }
</style>
