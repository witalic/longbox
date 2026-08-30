<script setup lang="ts">
// Reader shortcuts. Bindings are PHYSICAL keys (`e.code`) so they survive a
// keyboard layout change — `keys.ts` owns that rule and the storage; this is
// only the surface that rebinds them.
//
// The capture listener lives here rather than in the page: it is armed only
// while a binding is waiting for a key, and only this section can arm it.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from './Icon.vue'
import {
  KEY_ACTIONS, bindingsFor, isOverridden, keyLabel, keyOverrides, rebind, resetAllKeys, resetKey,
} from '../keys'

const capturing = ref<string | null>(null)
const anyOverride = computed(() => Object.keys(keyOverrides).length > 0)

function onCaptureKey(e: KeyboardEvent) {
  if (!capturing.value) return
  e.preventDefault()
  e.stopPropagation()
  // modifier keys alone don't finish a capture
  if (['Shift', 'Control', 'Alt', 'Meta'].includes(e.key)) return
  rebind(capturing.value, [e.code]) // PHYSICAL key — layout-independent
  capturing.value = null
}
onMounted(() => window.addEventListener('keydown', onCaptureKey, true))
onBeforeUnmount(() => window.removeEventListener('keydown', onCaptureKey, true))
</script>

<template>
  <section class="card">
    <div class="rowcol">
      <div v-if="anyOverride" class="keyrow">
        <span class="keylabel">Some bindings are yours</span>
        <button class="btn ghost small" @click="resetAllKeys()">Reset all</button>
      </div>
      <div v-for="a in KEY_ACTIONS" :key="a.id" class="keyrow">
        <span class="keylabel">{{ a.label }}</span>
        <button class="keychip mono" :class="{ capturing: capturing === a.id, over: isOverridden(a.id) }"
                :title="capturing === a.id ? 'Press the new key (modifiers alone don’t count)' : 'Click, then press a key to rebind'"
                @click="capturing = capturing === a.id ? null : a.id">
          {{ capturing === a.id ? 'press a key…' : bindingsFor(a.id).map(keyLabel).join(' / ') }}
        </button>
        <button v-if="isOverridden(a.id)" class="iconbtn lx" title="Reset to default" @click="resetKey(a.id)">
          <Icon name="refresh" :size="12" :sw="2" />
        </button>
        <span v-else style="width:24px"></span>
      </div>
      <div class="note">Shortcuts apply in the reader. Click a binding, press the new key; rebinding
        replaces the default.</div>
    </div>
  </section>
</template>
