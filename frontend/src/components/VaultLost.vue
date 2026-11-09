<script setup lang="ts">
// The vault stopped answering (design/vault-home.md, "When the vault drops").
// Until it is back nothing is read from it and nothing is written anywhere: the
// sidecar refuses every request, the browser's pages are unloaded (they write
// into the vault's browser profile), transfers in flight are stopped. The shell
// restarts the app into the vault the moment it answers. This says so, and
// lists only what actually stopped — taken at the moment it did.
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { browser } from '../browser'
import { draftState, isDirty } from '../draft'
import { copies, runningDownloads, store, vault, vaultName, vaultPass } from '../store'

interface Stopped { icon: string; what: string; then: string }
const stopped = ref<Stopped[]>([])
const unsaved = ref('')

watch(() => vault.lost, (lost) => {
  if (!lost) return
  const out: Stopped[] = []
  const n = runningDownloads().length
  if (n) out.push({ icon: 'download', what: `${n} download${n === 1 ? '' : 's'} stopped`, then: 'they start over once it is back.' })
  const copy = copies.jobs.find((j) => j.state === 'running')
  if (copy) out.push({ icon: 'film', what: 'Playable copy', then: `of ${copy.title} · ${copy.label} — it starts over.` })
  if (vaultPass.running) {
    out.push(vaultPass.key === 'import'
      ? { icon: 'import', what: 'Import', then: `${vaultPass.op} — run it again; what is stored is skipped.` }
      : { icon: 'activity', what: vaultPass.op || 'A vault pass', then: '— run it again once it is back.' })
  }
  if (browser.tabs.length) out.push({ icon: 'browser', what: "The browser's tabs", then: 'are closed for now; they reopen with the app.' })
  stopped.value = out
  const held = store.tabs.find((t) => t.hold)?.hold
  const title = draftState.cur?.meta.title
  unsaved.value = isDirty() ? `The changes${title ? ` to ${title}` : ''} you had not saved` : held ? `${held} What was changed` : ''
}, { immediate: true })

const name = computed(() => vault.name || vaultName(vault.path || store.libraryPath) || 'The vault')
const at = computed(() => {
  const d = new Date(vault.since)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
})
const drive = computed(() => (/^[A-Za-z]:/.test(vault.path) ? vault.path.slice(0, 2) : ''))
const chooseAnother = () => void window.longbox?.openLauncher()
const quit = () => void window.longbox?.quit()
</script>

<template>
  <div v-if="vault.lost" class="veil">
    <div class="lost" role="alertdialog" aria-labelledby="vault-lost-h">
      <div class="lbody">
        <div class="lhead">
          <span class="badge"><Icon name="offline" :size="18" /></span>
          <h1 id="vault-lost-h">{{ name }} is unreachable</h1>
        </div>
        <p>
          <template v-if="drive && vault.share"><b>{{ drive }}</b> (<span class="mono">{{ vault.share }}</span>)</template>
          <span v-else-if="vault.path" class="mono">{{ vault.path }}</span>
          <template v-else>The vault</template>
          stopped answering{{ at ? ` at ${at}` : '' }}. Until it is back nothing is read from the vault, and
          nothing is written anywhere.
        </p>
        <div v-if="stopped.length" class="stopped">
          <div v-for="s in stopped" :key="s.what">
            <Icon :name="s.icon" :size="14" /><span><b>{{ s.what }}</b> {{ s.then }}</span>
          </div>
        </div>
        <p v-if="unsaved" class="caution">{{ unsaved }} cannot be saved until the vault is back — and is lost when
          the app restarts.</p>
        <div class="retry"><span class="spin" />Reconnecting — longbox restarts into {{ name }} as soon as it
          answers, and reopens your tabs.</div>
      </div>
      <div class="lfoot">
        <button class="btn" @click="chooseAnother">Choose another vault…</button>
        <span class="sp" />
        <button class="btn ghost" @click="quit">Quit</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* below the chrome (its 40px stay usable: the window still moves and closes) */
.veil { position: fixed; inset: 40px 0 0 0; z-index: 900; display: flex; align-items: center; justify-content: center; background: color-mix(in srgb, var(--bg) 80%, transparent); backdrop-filter: blur(2px); }
.lost { width: 500px; max-width: calc(100vw - 32px); border: 1px solid var(--line); border-radius: 10px; background: var(--bg2); box-shadow: 0 18px 50px rgba(0, 0, 0, .4); overflow: hidden; }
.lbody { padding: 22px 22px 16px; display: flex; flex-direction: column; gap: 12px; }
.lhead { display: flex; align-items: center; gap: 12px; }
.lhead h1 { font: 700 22px/1.15 system-ui; margin: 0; letter-spacing: -.3px; }
.badge { width: 36px; height: 36px; flex: none; border-radius: 999px; display: flex; align-items: center; justify-content: center; color: var(--adult); background: color-mix(in srgb, var(--adult) 16%, transparent); }
p { margin: 0; font: 400 12px/1.5 system-ui; color: var(--tx2); }
p b { color: var(--tx); font-weight: 600; }
.stopped { display: flex; flex-direction: column; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }
.stopped div { display: flex; align-items: center; gap: 10px; padding: 9px 12px; background: var(--panel); font: 400 12px/1.35 system-ui; color: var(--tx2); }
.stopped div + div { border-top: 1px solid var(--line); }
.stopped div > :first-child { color: var(--tx3); flex: none; }
.stopped b { color: var(--tx); font-weight: 600; }
.caution { border-left: 2px solid var(--warn); padding: 2px 0 2px 10px; }
.retry { display: flex; align-items: center; gap: 8px; font: 400 12px/1.4 system-ui; color: var(--tx3); }
.spin { width: 12px; height: 12px; flex: none; border-radius: 50%; border: 2px solid var(--line); border-top-color: var(--accent); animation: lostspin .9s linear infinite; }
@keyframes lostspin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spin { animation-duration: 2.4s; } }
.lfoot { height: 44px; display: flex; align-items: center; gap: 8px; padding: 0 12px; border-top: 1px solid var(--line); }
.lfoot .sp { flex: 1; }
</style>
