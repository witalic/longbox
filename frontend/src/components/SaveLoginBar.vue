<script setup lang="ts">
// After a sign-in on the page in front (design/password-manager.md): a bar of the
// app's, over the top of the page — never in it — offering to save the login,
// or to update the saved one's password (the old one is kept among the
// previous ones). The password itself is the shell's, held until this is
// answered; the window only ever knows the username.
import { computed, ref } from 'vue'
import Icon from './Icon.vue'
import { askMaster, settled } from '../logins'

const props = defineProps<{ offer: LoginOffer }>()
const emit = defineEmits<{ (e: 'answered', guestId: number): void }>()

const host = computed(() => {
  try { return new URL(props.offer.origin ?? '').host } catch { return '' }
})
// the page chose it: shown short, as a name, never as more of the bar
const who = computed(() => {
  const u = props.offer.username ?? ''
  return u.length > 64 ? `${u.slice(0, 63)}…` : u
})
const busy = ref(false)
const error = ref('')

// Saving, or never asking here, needs the logins open: the master password first.
async function say(what: 'save' | 'never' | 'dismiss') {
  const shell = window.longbox?.logins
  if (!shell || busy.value) return
  busy.value = true
  error.value = ''
  try {
    if (what !== 'dismiss' && !(await askMaster())) return
    await settled(shell.answerOffer(props.offer.guestId, what))
    emit('answered', props.offer.guestId)
  } catch (e) {
    error.value = `Not saved: ${e instanceof Error ? e.message : String(e)}`
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="savebar">
    <Icon name="key" :size="15" :sw="2" class="ki" />
    <span v-if="error" class="msg err">{{ error }}</span>
    <span v-else-if="offer.kind === 'update'" class="msg">Update the password saved for <b>{{ who || 'this login' }}</b>
      on {{ host }}? The old one is kept among the previous ones.</span>
    <span v-else class="msg">Save the login for <b>{{ who || 'this site' }}</b> on {{ host }}?<template
      v-if="offer.kind === 'locked'"> longbox asks for the master password first.</template></span>
    <button class="btn small accent" :disabled="busy" @click="say('save')">{{ offer.kind === 'update' ? 'Update'
      : offer.kind === 'locked' ? 'Save…' : 'Save' }}</button>
    <button v-if="offer.kind !== 'update'" class="btn small" :disabled="busy" @click="say('never')">Never for this site</button>
    <button class="btn small ghost" :disabled="busy" @click="say('dismiss')">Not now</button>
  </div>
</template>

<style scoped>
.savebar { position: absolute; top: 0; left: 0; right: 0; z-index: 5; display: flex; align-items: center; gap: 10px; height: 44px; padding: 0 10px 0 14px; background: var(--panel2); border-bottom: 1px solid var(--line); box-shadow: 0 8px 20px rgba(0,0,0,.25); }
.ki { color: var(--accent); }
.msg { flex: 1; min-width: 0; font: 400 12.5px/1.3 system-ui; color: var(--tx2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.msg b { color: var(--tx); font-weight: 600; }
.msg.err { color: var(--adult); }
</style>
