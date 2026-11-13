<script setup lang="ts">
// The embedded browser's global settings. Everything here needs the Electron
// shell — a plain dev browser has no session to clear — so the section says so
// rather than offering buttons that quietly do nothing.
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { store } from '../store'
import { askChoice, askConfirm } from '../confirm'
import { bytes } from '../data'

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

// Where this vault's sign-ins live (design/sign-ins.md). The answer changes now;
// the profile moves on the next start, before the browser opens it.
type Place = 'vault' | 'computer'
const signins = ref<{ keep: Place | null; here: Place; data: number; cache: number } | null>(null)
onMounted(async () => { signins.value = (await window.longbox?.signins?.()) ?? null })
const PLACE = { vault: 'In the vault', computer: 'On this computer' } as const
const signinsHint = computed(() => {
  const s = signins.value
  if (!s) return ''
  if (s.keep && s.keep !== s.here) {
    return s.keep === 'computer' ? 'They move to this computer when longbox next starts.'
      : 'They move into the vault when longbox next starts.'
  }
  return s.here === 'vault'
    ? 'They travel with the vault to every computer that opens it, and whoever can read its folder can use them.'
    : "Kept in longbox's folder on this computer; the vault holds none of them. Another computer signs in on its own."
})
async function changeSignins() {
  const s = signins.value
  if (!s || !window.longbox) return
  const keep = s.keep ?? s.here
  // a move that waits for the next start is taken back without one
  if (keep !== s.here) {
    if (await window.longbox.setSignins(s.here, false)) signins.value = { ...s, keep: s.here }
    return
  }
  const to: Place = keep === 'vault' ? 'computer' : 'vault'
  const answer = await askChoice({
    title: to === 'computer' ? "Keep this vault's sign-ins on this computer?" : "Keep this vault's sign-ins in the vault?",
    message: (to === 'computer'
      ? "When longbox next starts, the sign-ins and site data move from the vault into longbox's folder on this "
        + `computer (${bytes(s.data)}), and the vault keeps none of them. Other computers that open it sign in `
        + 'on their own.'
      : 'When longbox next starts, the sign-ins and site data move from this computer into the vault '
        + `(${bytes(s.data)}), and every computer that opens it uses them — whoever can read the vault can use `
        + 'them too.')
      + ` The browser's cache (${bytes(s.cache)}) is deleted, not moved — it refills.`,
    altLabel: 'On the next start', okLabel: 'Restart now',
  })
  if (!answer) return
  if (await window.longbox.setSignins(to, answer === 'ok')) signins.value = { ...s, keep: to }
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
    <div v-if="signins" class="row">
      <div class="text">
        <div class="label">Sign-ins and site data <span class="where">{{ PLACE[signins.here] }}</span></div>
        <div class="hint">{{ signinsHint }}</div>
      </div>
      <button class="btn" @click="changeSignins">{{ signins.keep && signins.keep !== signins.here ? 'Keep them where they are' : 'Change…' }}</button>
    </div>
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

<style scoped>
.label .where { margin-left: 8px; font: 600 11px/1 system-ui; padding: 4px 8px; border-radius: 999px; background: var(--accentSoft); color: var(--accent); vertical-align: 1px; }
</style>
