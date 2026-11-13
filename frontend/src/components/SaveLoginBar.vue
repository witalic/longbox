<script setup lang="ts">
// After a sign-in on the page in front (design/password-manager.md): a bar of the
// app's, under the toolbar — never in the page — offering to save the login,
// or to update the saved one's password (the old one is kept among the
// previous ones). The password itself is the shell's, held until this is
// answered; the window only ever knows the username.
import { computed } from 'vue'
import Icon from './Icon.vue'
import { askMaster, settled } from '../logins'

const props = defineProps<{ offer: LoginOffer }>()
const emit = defineEmits<{ (e: 'answered', result: string): void }>()

const host = computed(() => {
  try { return new URL(props.offer.origin ?? '').host } catch { return '' }
})

// Saving, or never asking here, needs the logins open: the master password first.
async function say(what: 'save' | 'never' | 'dismiss') {
  const shell = window.longbox?.logins
  if (!shell) return
  if (what !== 'dismiss' && !(await askMaster())) return
  emit('answered', await settled(shell.answerOffer(props.offer.guestId, what)).catch(() => 'gone'))
}
</script>

<template>
  <div class="savebar">
    <Icon name="key" :size="15" :sw="2" class="ki" />
    <span v-if="offer.kind === 'update'" class="msg">Update the password saved for <b>{{ offer.username || 'this login' }}</b>?
      The old one is kept among the previous ones.</span>
    <span v-else class="msg">Save the login for <b>{{ offer.username || 'this site' }}</b> on {{ host }}?<template
      v-if="offer.kind === 'locked'"> longbox asks for the master password first.</template></span>
    <button class="btn small accent" @click="say('save')">{{ offer.kind === 'update' ? 'Update'
      : offer.kind === 'locked' ? 'Save…' : 'Save' }}</button>
    <button v-if="offer.kind !== 'update'" class="btn small" @click="say('never')">Never for this site</button>
    <button class="btn small ghost" @click="say('dismiss')">Not now</button>
  </div>
</template>

<style scoped>
.savebar { display: flex; align-items: center; gap: 10px; height: 44px; padding: 0 10px 0 14px; background: var(--panel2); border-bottom: 1px solid var(--line); flex: none; }
.ki { color: var(--accent); }
.msg { flex: 1; min-width: 0; font: 400 12.5px/1.3 system-ui; color: var(--tx2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.msg b { color: var(--tx); font-weight: 600; }
</style>
