<script setup lang="ts">
// The master password of the vault's logins (design/password-manager.md): given
// once per app start, set the first time. The app's dialog, over everything —
// never a page's.
import { computed, nextTick, ref, watch } from 'vue'
import Icon from './Icon.vue'
import StrengthMeter from './StrengthMeter.vue'
import { dropMasterAsk, giveMaster, masterAsk, strength } from '../logins'

const first = ref('')
const again = ref('')
const input = ref<HTMLInputElement | null>(null)
watch(() => masterAsk.mode, async (mode) => {
  first.value = ''
  again.value = ''
  if (mode) {
    await nextTick()
    input.value?.focus()
  }
})

const setup = computed(() => masterAsk.mode === 'setup')
const ready = computed(() => (setup.value ? strength(first.value) > 0 && first.value === again.value
  : first.value.length > 0))

function submit() {
  if (ready.value) void giveMaster(first.value)
}
</script>

<template>
  <Transition name="fade">
    <div v-if="masterAsk.mode" class="overlay" data-modal @click.self="dropMasterAsk" @keydown.esc="dropMasterAsk">
      <form class="card" role="dialog" aria-modal="true" @submit.prevent="submit">
        <div class="title">{{ setup ? 'Set a master password' : 'Unlock passwords' }}</div>
        <div class="msg">{{ setup
          ? 'It opens this vault\'s logins, on any computer the vault is opened from. It is never stored.'
          : 'The master password of this vault\'s logins.' }}</div>
        <div class="fields">
          <input ref="input" v-model="first" class="in" type="password" autocomplete="off" spellcheck="false"
                 :placeholder="setup ? 'Master password' : ''" :disabled="masterAsk.busy" />
          <template v-if="setup">
            <StrengthMeter :value="first" />
            <input v-model="again" class="in" type="password" autocomplete="off" spellcheck="false"
                   placeholder="The same again" :disabled="masterAsk.busy" />
            <div v-if="again && again !== first" class="err">The two are not the same.</div>
          </template>
          <div v-if="masterAsk.error" class="err">{{ masterAsk.error }}</div>
        </div>
        <div v-if="setup" class="warn"><Icon name="warn" :size="14" :sw="2" />
          <span>Forgotten, it cannot be recovered — and the logins are lost with it.</span></div>
        <div class="actions">
          <button type="button" class="btn ghost" @click="dropMasterAsk">Cancel</button>
          <button type="submit" class="btn accent" :disabled="!ready || masterAsk.busy">
            {{ masterAsk.busy ? (setup ? 'Setting…' : 'Unlocking…') : setup ? 'Set' : 'Unlock' }}</button>
        </div>
      </form>
    </div>
  </Transition>
</template>

<style scoped>
.overlay { position: fixed; inset: 0; z-index: 1000; display: flex; align-items: center; justify-content: center; background: rgba(6, 7, 9, .58); backdrop-filter: blur(2px); }
.card { width: min(400px, calc(100vw - 40px)); background: var(--panel); border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 24px 60px rgba(0, 0, 0, .55); padding: 20px 22px 18px; }
.title { font: 700 15px/1.2 system-ui; color: var(--tx); }
.msg { margin-top: 10px; font: 400 13px/1.55 system-ui; color: var(--tx2); }
.fields { margin-top: 14px; display: flex; flex-direction: column; gap: 8px; }
.err { font: 400 12px/1.4 system-ui; color: var(--adult); }
.warn { margin-top: 12px; display: flex; gap: 8px; align-items: flex-start; padding: 8px 10px; border-radius: 6px; background: color-mix(in srgb, var(--warn) 12%, transparent); font: 400 12px/1.45 system-ui; color: var(--tx2); }
.warn :deep(svg) { color: var(--warn); margin-top: 1px; }
.actions { margin-top: 18px; display: flex; justify-content: flex-end; gap: 9px; }
.fade-enter-active, .fade-leave-active { transition: opacity .14s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
