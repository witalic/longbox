<script setup lang="ts">
// Settings → Passwords (design/password-manager.md): whether the vault's logins
// are open, how many there are, Lock — and a new master password. The logins
// themselves are managed where they are used: the key in the browser, and
// Sources → a site → Logins.
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import StrengthMeter from './StrengthMeter.vue'
import { hostOf } from '../data'
import { IDLE_LOCKS, askMaster, idleLock, lockLogins, loginsState, settled, strength } from '../logins'

const shell = window.longbox?.logins
const count = ref<{ logins: number; sites: number } | null>(null)
watch(() => loginsState.unlocked, async (open) => {
  if (!open || !shell) {
    count.value = null
    return
  }
  const all = await settled(shell.list()).catch(() => [] as LoginSummary[])
  count.value = { logins: all.length, sites: new Set(all.map((l) => hostOf(l.origin))).size }
}, { immediate: true })

const IDLE_LABELS: Record<(typeof IDLE_LOCKS)[number], string> = {
  '5': '5 min', '15': '15 min', '30': '30 min', '60': '1 hour', '0': 'Never',
}

const stateHint = computed(() => {
  if (!shell) return 'Logins need the desktop app.'
  if (!loginsState.exists) return 'None yet. A master password of yours keeps them encrypted in the vault.'
  if (!loginsState.unlocked) return 'Locked — they open with the master password.'
  if (loginsState.restored) {
    return 'The logins file did not read, so its second copy was opened; the next change writes the file again.'
  }
  const c = count.value
  return c ? `${c.logins} login${c.logins === 1 ? '' : 's'} on ${c.sites} site${c.sites === 1 ? '' : 's'}, open until they are locked.`
    : 'Open until they are locked.'
})

// a new master password: the current one, then the new one twice
const changing = ref(false)
const current = ref('')
const next = ref('')
const again = ref('')
const error = ref('')
const done = ref(false)
const busy = ref(false)
const ready = computed(() => current.value && strength(next.value) > 0 && next.value === again.value)
function startChange() {
  changing.value = true
  current.value = next.value = again.value = error.value = ''
  done.value = false
}
async function change() {
  if (!shell || !ready.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await settled(shell.changeMaster(current.value, next.value))
    changing.value = false
    done.value = true
  } catch (e) {
    error.value = e instanceof Error && 'code' in e && e.code === 'EPASSWORD' ? 'That is not the current master password.'
      : e instanceof Error ? e.message : String(e)
  } finally {
    current.value = next.value = again.value = ''
    busy.value = false
  }
}
</script>

<template>
  <section class="card">
    <div class="row">
      <div class="text">
        <div class="label">Logins <span v-if="loginsState.exists" class="where" :class="{ off: !loginsState.unlocked }">
          {{ loginsState.unlocked ? 'Unlocked' : 'Locked' }}</span></div>
        <div class="hint">{{ stateHint }}</div>
      </div>
      <button v-if="shell && loginsState.unlocked" class="btn" @click="lockLogins"><Icon name="lock" :size="13" :sw="2" />Lock</button>
      <button v-else-if="shell" class="btn" @click="askMaster">{{ loginsState.exists ? 'Unlock…' : 'Set a master password…' }}</button>
    </div>
    <div v-if="loginsState.exists" class="row">
      <div class="text">
        <div class="label">Lock when idle</div>
        <div class="hint">After this long with no use of the computer. They lock, too, when it sleeps or its
          screen is locked, and when the app closes.</div>
      </div>
      <div class="seg">
        <button v-for="v in IDLE_LOCKS" :key="v" class="opt" :class="{ on: idleLock === v }"
                @click="idleLock = v">{{ IDLE_LABELS[v] }}</button>
      </div>
    </div>
    <div v-if="loginsState.exists" class="row">
      <div class="text">
        <div class="label">Master password</div>
        <div class="hint">It opens this vault's logins on any computer the vault is opened from, and is never stored.
          Forgotten, it cannot be recovered — and the logins are lost with it.</div>
        <div v-if="done" class="hint good">The master password is changed.</div>
      </div>
      <button v-if="!changing" class="btn" :disabled="!loginsState.unlocked"
              :title="loginsState.unlocked ? '' : 'Unlock the logins first'" @click="startChange">Change…</button>
    </div>
    <form v-if="changing" class="rowcol change" @submit.prevent="change">
      <input v-model="current" class="in" type="password" autocomplete="off" placeholder="The current master password"
             aria-label="The current master password" />
      <input v-model="next" class="in" type="password" autocomplete="off" placeholder="A new one" aria-label="A new master password" />
      <StrengthMeter :value="next" />
      <input v-model="again" class="in" type="password" autocomplete="off" placeholder="The new one again" aria-label="The new master password again" />
      <div v-if="again && again !== next" class="err">The two are not the same.</div>
      <div v-if="error" class="err">{{ error }}</div>
      <div class="acts">
        <button type="button" class="btn ghost" @click="changing = false">Cancel</button>
        <button type="submit" class="btn accent" :disabled="!ready || busy">{{ busy ? 'Changing…'
          : 'Change the master password' }}</button>
      </div>
    </form>
  </section>
</template>

<style scoped>
.label .where { margin-left: 8px; font: 600 11px/1 system-ui; padding: 4px 8px; border-radius: 999px; background: var(--accentSoft); color: var(--accent); vertical-align: 1px; }
.label .where.off { background: var(--panel2); color: var(--tx3); }
.hint.good { color: var(--good); }
.change { display: flex; flex-direction: column; gap: 8px; max-width: 420px; }
.err { font: 400 12px/1.4 system-ui; color: var(--adult); }
.acts { display: flex; justify-content: flex-end; gap: 8px; }
</style>
