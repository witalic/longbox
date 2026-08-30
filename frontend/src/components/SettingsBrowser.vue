<script setup lang="ts">
// The embedded browser's global settings. Everything here needs the Electron
// shell — a plain dev browser has no session to clear — so the section says so
// rather than offering buttons that quietly do nothing.
import { ref } from 'vue'
import { api } from '../api'
import { askConfirm, store } from '../store'

const homepage = defineModel<string>('homepage', { default: '' })

const canBridge = !!window.longbox
const flash = ref('')

async function saveHomepage() {
  const hp = homepage.value.trim()
  if (!hp) return
  try {
    const s = await api.setHomepage(hp)
    homepage.value = s.homepage
    store.browseHomepage = s.homepage
  } catch { /* the field keeps what was typed */ }
}

async function clearBrowsing(what: 'cookies' | 'cache') {
  if (!window.longbox) return
  if (what === 'cookies') {
    const ok = await askConfirm({
      title: 'Clear site cookies', okLabel: 'Clear', danger: true,
      message: 'Remove cookies for ALL sites in the embedded browser? You will be signed out of '
        + 'the sources. The app itself stays signed in.',
    })
    if (!ok) return
  }
  const done = await window.longbox.clearBrowsing(what)
  flash.value = done
    ? (what === 'cookies' ? '✓ site cookies cleared' : '✓ HTTP cache cleared')
    : 'not available'
  setTimeout(() => { flash.value = '' }, 2600)
}
</script>

<template>
  <section class="card">
    <div class="row">
      <div class="text">
        <div class="label">Homepage</div>
        <div class="hint">The page a fresh Browse tab opens.</div>
      </div>
      <input v-model="homepage" class="hpin mono" placeholder="https://www.google.com"
             @blur="saveHomepage" @keydown.enter="saveHomepage" />
    </div>
    <div class="row">
      <div class="text">
        <div class="label">Site cookies</div>
        <div class="hint">Every site in the embedded browser — signs you out of the sources.
          The app's own session is kept.</div>
      </div>
      <span v-if="flash" class="mini" style="color:var(--good)">{{ flash }}</span>
      <button class="btn" :disabled="!canBridge" @click="clearBrowsing('cookies')">Clear cookies</button>
    </div>
    <div class="row">
      <div class="text">
        <div class="label">HTTP cache</div>
        <div class="hint">Cached pages and images of the embedded browser.</div>
      </div>
      <button class="btn" :disabled="!canBridge" @click="clearBrowsing('cache')">Clear cache</button>
    </div>
    <div v-if="!canBridge" class="rowcol" style="padding-top:0">
      <div class="note">Clearing needs the desktop shell — not available in a plain dev browser.</div>
    </div>
  </section>
</template>
