<script setup lang="ts">
// The embedded browser's global settings. Everything here needs the Electron
// shell — a plain dev browser has no session to clear — so the section says so
// rather than offering buttons that quietly do nothing.
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { store } from '../store'
import { askChoice, askConfirm } from '../confirm'
import { restart, restartRefused, restartStops } from '../restart'
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

// Where this vault's sign-ins live (design/sign-ins.md). A change is this
// computer's own until it next opens the vault: the profile moves before the
// browser opens it, and only then does the vault's answer change.
type Place = 'vault' | 'computer'
const signins = ref<{ here: Place; owe: Place | null; writable: boolean; shared: string } | null>(null)
const signinsError = ref('')
const signinsBusy = ref(false)
onMounted(async () => {
  try {
    signins.value = (await window.longbox?.signins?.()) ?? null
  } catch (e) {
    signinsError.value = `Where the sign-ins live could not be read: ${e instanceof Error ? e.message : String(e)}`
  }
})
const PLACE = { vault: 'In the vault', computer: 'On this computer' } as const
const NEWER = 'A newer longbox chose this for the vault; this one cannot change it.'
const signinsHint = computed(() => {
  const s = signins.value
  if (!s) return ''
  if (s.owe) {
    return s.owe === 'computer' ? 'They move to this computer the next time longbox opens this vault here.'
      : 'They move into the vault the next time longbox opens this vault here.'
  }
  if (!s.writable) return NEWER
  if (s.shared) {
    return `This computer keeps them for ${s.shared} too — a copy of this vault, or this one by a path that does `
      + 'not answer now — so they are not moved from here.'
  }
  return s.here === 'vault'
    ? 'They travel with the vault to every computer that opens it, and whoever can read its folder can use them.'
    : "Kept in longbox's folder on this computer; the vault holds none of them. Another computer signs in on its own."
})
async function changeSignins() {
  const s = signins.value
  const shell = window.longbox
  if (!s || !shell || signinsBusy.value) return
  signinsError.value = ''
  signinsBusy.value = true
  try {
    // a move still owed is taken back without one
    if (s.owe) {
      if (await shell.setSignins(s.here, false)) signins.value = { ...s, owe: null }
      return
    }
    const to: Place = s.here === 'vault' ? 'computer' : 'vault'
    const size = await shell.signinsSizes()
    const moves = size ? ` (${bytes(size.data)})` : ''
    const cache = size ? ` (${bytes(size.cache)})` : ''
    // a pass running in the vault: the move waits for the next start, which does not cut it off
    const refused = restartRefused()
    const stops = restartStops('after the restart')
    const answer = await askChoice({
      title: to === 'computer' ? "Keep this vault's sign-ins on this computer?" : "Keep this vault's sign-ins in the vault?",
      message: (to === 'computer'
        ? 'The next time longbox opens this vault here, the sign-ins and site data move from the vault into '
          + `longbox's folder on this computer${moves}, and the vault keeps none of them. Other computers that open `
          + 'it sign in on their own. Copies made before — backups, snapshots, a synced folder\'s history — still '
          + 'hold them; signing out of a site ends those sessions.'
        : 'The next time longbox opens this vault here, the sign-ins and site data move from this computer into the '
          + `vault${moves}, and every computer that opens it uses them — whoever can read the vault can use them too. `
          + 'Another computer that kept its own sign-ins for this vault removes them the next time it opens it.')
        + ` The browser's cache${cache} is deleted, not moved — it refills.`
        + (refused ? ` ${refused[0].toUpperCase()}${refused.slice(1)}: the move waits for the next start.`
          : stops.text ? ` Restart now:${stops.text}` : ''),
      ...(refused ? { okLabel: 'On the next start' }
        : { altLabel: 'On the next start', okLabel: 'Restart now', danger: stops.danger }),
    })
    if (!answer) return
    const now = answer === 'ok' && !refused
    if (await (now ? restart(() => shell.setSignins(to, true)) : shell.setSignins(to, false))) {
      signins.value = { ...s, owe: to }
    } else {
      signinsError.value = NEWER
    }
  } catch (e) {
    signinsError.value = `The change was not saved: ${e instanceof Error ? e.message : String(e)}`
  } finally {
    signinsBusy.value = false
  }
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
    <div v-if="signins || signinsError" class="row">
      <div class="text">
        <div class="label">Sign-ins and site data <span v-if="signins" class="where">{{ PLACE[signins.here] }}</span></div>
        <div class="hint">{{ signinsHint }}</div>
        <div v-if="signinsError" class="hint err">{{ signinsError }}</div>
      </div>
      <button v-if="signins" class="btn" :disabled="signinsBusy || (!signins.owe && (!signins.writable || !!signins.shared))"
              @click="changeSignins">{{ signins.owe ? 'Keep them where they are' : 'Change…' }}</button>
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
.hint.err { color: var(--warn); }
.label .where { margin-left: 8px; font: 600 11px/1 system-ui; padding: 4px 8px; border-radius: 999px; background: var(--accentSoft); color: var(--accent); vertical-align: 1px; }
</style>
