<script setup lang="ts">
// The launch window (design/vault-home.md, design/vault-launcher-mockup.html).
// The shell decides which vault opens and owns every step; this page shows the
// step it is on and hands it the human's choices. It never sees the disk:
// folders are picked through the native dialog, in the shell.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Icon from '../components/Icon.vue'

const s = ref<LauncherState | null>(null)
let off: (() => void) | undefined
onMounted(async () => {
  off = window.launcher?.onState((next) => { s.value = next })
  s.value = (await window.launcher?.state()) ?? null
})
onBeforeUnmount(() => off?.())

const act = (a: LauncherAct, arg = '') => void window.launcher?.act(a, arg)

const v = computed(() => s.value?.vault ?? null)
const name = computed(() => v.value?.name || 'the vault')
const drive = computed(() => (v.value && /^[A-Za-z]:/.test(v.value.path) ? v.value.path.slice(0, 2) : ''))
const others = computed(() => s.value?.others ?? [])

// the name field is the window's while it is typed in; the shell checks it a beat later
const typed = ref('')
watch(() => s.value?.create?.name, (n) => { if (n !== undefined && n !== typed.value) typed.value = n })
let nameTimer: number | undefined
function typeName(value: string) {
  typed.value = value
  window.clearTimeout(nameTimer)
  nameTimer = window.setTimeout(() => act('create-name', value), 250)
}
const check = computed(() => s.value?.create?.check ?? null)
const canCreate = computed(() => !!check.value && (check.value.state === 'new' || check.value.state === 'empty'))

function when(iso?: string): string {
  const d = new Date(iso || '')
  if (Number.isNaN(d.getTime())) return '—'
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const today = new Date()
  if (d.toDateString() === today.toDateString()) return `today, ${time}`
  return `${d.toLocaleDateString([], { weekday: 'long' })}, ${time}`
}
function ago(iso?: string): string {
  const ms = Date.now() - new Date(iso || '').getTime()
  if (!Number.isFinite(ms)) return '—'
  const sec = Math.max(0, Math.round(ms / 1000))
  if (sec < 60) return `${sec} seconds ago`
  const min = Math.round(sec / 60)
  if (min < 60) return `${min} minute${min === 1 ? '' : 's'} ago`
  const h = Math.round(min / 60)
  if (h < 48) return `${h} hour${h === 1 ? '' : 's'} ago`
  return `${Math.round(h / 24)} days ago`
}
const mb = (n: number) => `${Math.max(0, Math.round(n / (1024 * 1024)))} MB`
</script>

<template>
  <div class="win">
    <div class="tb"><span class="logo" /><span class="name">longbox</span></div>

    <template v-if="s">
      <!-- the vault does not answer (yet) -->
      <template v-if="s.screen === 'opening' || s.screen === 'starting'">
        <div class="wbody">
          <h1>Opening {{ name }}</h1>
          <div v-if="v" class="path"><Icon name="folder" :size="14" /><span>{{ v.path }}</span></div>
          <div class="status">
            <span class="spin" />
            <template v-if="s.screen === 'starting'">Starting…</template>
            <template v-else-if="v?.share">Waiting for the network drive <span class="mono">{{ v.share }}</span></template>
            <template v-else>Waiting for the folder to answer</template>
            <span v-if="s.screen === 'opening' && s.waited" class="t mono">{{ s.waited }} s</span>
          </div>
          <p v-if="s.screen === 'opening'">A network drive that has just woken up can take a few seconds. The app opens as soon as it answers.</p>
        </div>
        <div class="wfoot">
          <button v-if="s.screen === 'opening'" class="btn" @click="act('choose')">Choose another vault…</button>
          <span class="sp" /><button class="btn ghost" @click="act('quit')">Quit</button>
        </div>
      </template>

      <template v-else-if="s.screen === 'unreachable'">
        <div class="wbody">
          <div class="head"><span class="badge bad"><Icon name="offline" :size="18" /></span><h1>{{ name }} can't be reached</h1></div>
          <div v-if="v" class="path"><Icon name="folder" :size="14" /><span>{{ v.path }}</span></div>
          <p v-if="drive && v?.share"><b>{{ drive }}</b> is the network drive <span class="mono">{{ v.share }}</span>, and it did not answer.</p>
          <p v-else>The folder is not there, or did not answer.</p>
          <p v-if="s.error" class="err">{{ s.error }}</p>
          <div class="retry"><span class="spin" />Still trying — it opens by itself the moment it is back.</div>
          <template v-if="others.length">
            <div class="sub">Other vaults</div>
            <div class="list scroll">
              <div v-for="o in others" :key="o.path" class="vrow">
                <div class="who"><b>{{ o.name }}</b><span>{{ o.path }}</span></div>
                <span class="st"><span class="dot" :class="o.state" />{{ o.state === 'answers' ? 'answers' : o.state === 'checking' ? 'asking…' : o.state === 'notvault' ? 'not a vault' : 'no answer' }}</span>
                <button class="btn small" :disabled="o.state !== 'answers'" @click="act('open', o.path)">Open</button>
              </div>
            </div>
          </template>
        </div>
        <div class="wfoot">
          <button class="btn accent" @click="act('retry')">Try again</button>
          <button class="btn" @click="act('choose')">Choose another…</button>
          <span class="sp" /><button class="btn ghost" @click="act('quit')">Quit</button>
        </div>
      </template>

      <!-- no vault yet, or another one asked for -->
      <template v-else-if="s.screen === 'first'">
        <div class="wbody">
          <h1>Where should longbox keep your library?</h1>
          <p v-if="s.error" class="err">{{ s.error }}</p>
          <p>Everything longbox keeps lives in one folder — the <b>vault</b>: your titles and their files, recipes
            and rules, the browser's sign-ins and cache, and your settings. This computer only remembers where the
            vault is.</p>
          <div v-if="others.length" class="list scroll">
            <div v-for="o in others" :key="o.path" class="vrow">
              <div class="who"><b>{{ o.name }}</b><span>{{ o.path }}</span></div>
              <span class="st"><span class="dot" :class="o.state" />{{ o.state === 'answers' ? 'answers' : o.state === 'checking' ? 'asking…' : o.state === 'notvault' ? 'not a vault' : 'no answer' }}</span>
              <button class="btn small" @click="act('open', o.path)">Open</button>
            </div>
          </div>
          <div class="cards">
            <div class="card">
              <span class="badge acc"><Icon name="plus" :size="18" /></span>
              <h3>Create a new vault</h3>
              <p>In an empty folder, or a new one made where you choose. A network drive works.</p>
              <button class="btn accent" @click="act('create')">Choose a place…</button>
            </div>
            <div class="card">
              <span class="badge acc"><Icon name="folder" :size="18" /></span>
              <h3>Open an existing vault</h3>
              <p>One that another computer, or an earlier install, already made.</p>
              <button class="btn" @click="act('choose')">Choose a vault…</button>
            </div>
          </div>
        </div>
        <div class="wfoot"><span class="sp" /><button class="btn ghost" @click="act('quit')">Quit</button></div>
      </template>

      <template v-else-if="s.screen === 'create' && s.create">
        <div class="wbody">
          <h1>Create a new vault</h1>
          <div class="field">
            <label>In</label>
            <div class="row">
              <div class="path"><Icon name="folder" :size="14" /><span>{{ s.create.parent }}</span></div>
              <button class="btn" @click="act('create-parent')">Change…</button>
            </div>
          </div>
          <div class="field">
            <label for="vault-name">Name</label>
            <input id="vault-name" class="input" :value="typed" spellcheck="false"
                   @input="typeName(($event.target as HTMLInputElement).value)"
                   @keydown.enter="canCreate && act('create-go')" />
          </div>
          <div class="field">
            <label>Will be made</label>
            <div class="path"><Icon name="folder" :size="14" /><span>{{ check?.path || '—' }}</span></div>
          </div>
          <div v-if="check" class="check" :class="canCreate ? 'ok' : 'no'">
            <Icon :name="canCreate ? 'check' : 'x'" :size="14" />
            <span v-if="check.state === 'new'">A new, empty folder — nothing there yet.</span>
            <span v-else-if="check.state === 'empty'">An empty folder — the vault is made in it.</span>
            <span v-else-if="check.state === 'files'"><b>{{ check.path }} already holds {{ check.files }} other item{{ check.files === 1 ? '' : 's' }}.</b>
              A vault needs a folder of its own — pick another name, or an empty folder. To bring those files in,
              create the vault elsewhere and use Import.</span>
            <span v-else-if="check.state === 'vault'"><b>{{ check.path }} is a vault already.</b> Go back and open it instead.</span>
            <span v-else-if="check.state === 'badname'">A folder cannot be called that.</span>
            <span v-else-if="check.state === 'noparent'">That place is not there.</span>
            <span v-else>That place does not answer.</span>
          </div>
          <p v-if="s.error" class="err">{{ s.error }}</p>
        </div>
        <div class="wfoot">
          <button class="btn ghost" @click="act('back')">Back</button><span class="sp" />
          <button class="btn accent" :disabled="!canCreate" @click="act('create-go')">Create vault</button>
        </div>
      </template>

      <template v-else-if="s.screen === 'notvault' && s.notvault">
        <div class="wbody">
          <div class="head"><span class="badge warn"><Icon name="folder" :size="18" /></span><h1>That folder is not a vault</h1></div>
          <div class="path"><Icon name="folder" :size="14" /><span>{{ s.notvault.path }}</span></div>
          <template v-if="s.notvault.empty">
            <p>It is empty. It can become a new vault — or choose the folder of an existing one.</p>
          </template>
          <template v-else>
            <p>A vault has a <span class="mono">vault.json</span> at its top, which this folder does not. Opening it as
              one would start writing an index and shelves among your files, so it is not opened.</p>
            <p>To bring these files into a vault, open the vault and use <b>Import</b>.</p>
          </template>
        </div>
        <div class="wfoot">
          <button class="btn ghost" @click="act('back')">Back</button><span class="sp" />
          <button v-if="s.notvault.empty" class="btn accent" @click="act('create', s.notvault.path)">Make it a vault</button>
          <button class="btn" @click="act('choose')">Choose another…</button>
        </div>
      </template>

      <!-- in use -->
      <template v-else-if="(s.screen === 'busy' || s.screen === 'left') && s.lock">
        <div class="wbody">
          <div class="head">
            <span class="badge warn"><Icon name="lock" :size="18" /></span>
            <h1 v-if="s.screen === 'busy' && s.lock.here">{{ name }} is open in another longbox</h1>
            <h1 v-else-if="s.screen === 'busy'">{{ name }} is open on another computer</h1>
            <h1 v-else>{{ name }} was not closed properly</h1>
          </div>
          <dl class="facts">
            <dt>Computer</dt><dd class="mono">{{ s.lock.computer || '—' }}</dd>
            <dt>Opened</dt><dd>{{ when(s.lock.since) }}</dd>
            <dt>Last seen</dt><dd>{{ ago(s.lock.seen) }}</dd>
          </dl>
          <template v-if="s.screen === 'busy'">
            <p v-if="s.lock.here" class="caution">Another copy of longbox on this computer has it open. Two apps
              writing to one vault can damage its index and the browser data in it — close the other one first.</p>
            <p v-else class="caution">Two apps writing to one vault can damage its index and the browser data in it. Open it
              here only if longbox on that computer is closed — or the computer is off.</p>
            <div class="retry"><span class="spin" />Opens by itself when that computer lets go of it.</div>
          </template>
          <p v-else>That computer has not touched the vault for {{ ago(s.lock.seen).replace(' ago', '') }} — the app
            there most likely crashed or lost the drive. Opening it here is safe.</p>
        </div>
        <div class="wfoot">
          <button v-if="s.screen === 'busy'" class="btn warn" @click="act('anyway')">Open anyway</button>
          <button v-else class="btn accent" @click="act('anyway')">Open {{ name }}</button>
          <button class="btn" @click="act('choose')">Choose another…</button>
          <span class="sp" /><button class="btn ghost" @click="act('quit')">Quit</button>
        </div>
      </template>

      <!-- the one-time move of this computer's data -->
      <template v-else-if="s.screen === 'movefailed'">
        <div class="wbody">
          <div class="head"><span class="badge bad"><Icon name="x" :size="18" /></span><h1>Moving this computer's data did not finish</h1></div>
          <p>Nothing was removed from this computer, and {{ name }} was not opened half-moved into. Trying again starts
            the copy over.</p>
          <p v-if="s.error" class="err mono">{{ s.error }}</p>
        </div>
        <div class="wfoot">
          <button class="btn accent" @click="act('retry')">Try again</button>
          <span class="sp" /><button class="btn ghost" @click="act('quit')">Quit</button>
        </div>
      </template>
      <template v-else-if="s.screen === 'moving' && s.move">
        <div class="wbody">
          <div class="head"><span class="badge acc"><Icon name="download" :size="18" /></span><h1>Moving this computer's data into {{ name }}</h1></div>
          <p>From now on everything lives in the vault. This happens once; this computer keeps only where your vaults are.</p>
          <div class="parts">
            <div v-for="p in s.move.parts" :key="p.what" class="part" :class="{ wait: !p.done && p.total }">
              <Icon v-if="p.done >= p.total" name="check" :size="14" class="ok" />
              <span v-else-if="p.done" class="spin" />
              <span v-else class="ring" />
              <span class="what">{{ p.what }}</span>
              <span class="size mono">{{ p.done && p.done < p.total ? `${mb(p.done)} of ${mb(p.total)}` : mb(p.total) }}</span>
              <span v-if="p.done && p.done < p.total" class="pbar"><span :style="{ width: `${(p.done / p.total) * 100}%` }" /></span>
            </div>
          </div>
        </div>
        <div class="wfoot"><span class="hint">Copied first, then removed here — stopping now loses nothing.</span>
          <span class="sp" /><button class="btn ghost" @click="act('quit')">Quit</button></div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.win { height: 100vh; display: flex; flex-direction: column; background: var(--bg); color: var(--tx); font: 400 13px/1.45 system-ui; overflow: hidden; }
/* the window's own title bar: the OS draws only its buttons, on the right */
.tb { height: 40px; flex: none; display: flex; align-items: center; gap: 9px; padding: 0 150px 0 14px; background: var(--bg2); border-bottom: 1px solid var(--line); -webkit-app-region: drag; }
.tb .logo { width: 16px; height: 16px; border-radius: 4px; background: var(--accent); }
.tb .name { font: 600 12px/1 system-ui; color: var(--tx2); }
.wbody { flex: 1; min-height: 0; padding: 24px 28px 16px; display: flex; flex-direction: column; gap: 12px; overflow: auto; }
.wbody h1 { font: 700 22px/1.15 system-ui; margin: 0; letter-spacing: -.3px; }
.wbody p { margin: 0; color: var(--tx2); }
.wbody p b { color: var(--tx); font-weight: 600; }
.wfoot { height: 44px; flex: none; display: flex; align-items: center; gap: 8px; padding: 0 16px; background: var(--bg2); border-top: 1px solid var(--line); }
.wfoot .sp { flex: 1; }
.wfoot .hint { font: 400 11px/1.3 system-ui; color: var(--tx3); }
.head { display: flex; gap: 12px; align-items: center; }
.badge { width: 36px; height: 36px; border-radius: 999px; display: flex; align-items: center; justify-content: center; flex: none; }
.badge.warn { background: color-mix(in srgb, var(--warn) 16%, transparent); color: var(--warn); }
.badge.bad { background: color-mix(in srgb, var(--adult) 16%, transparent); color: var(--adult); }
.badge.acc { background: var(--accentSoft); color: var(--accent); }
.path { display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 10px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); font: 400 12px/1 ui-monospace, monospace; color: var(--tx); min-width: 0; }
.path > :first-child { color: var(--tx3); flex: none; }
.path span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.status { display: flex; align-items: center; gap: 9px; font: 400 12px/1.4 system-ui; color: var(--tx2); }
.status .t { margin-left: auto; font-size: 11px; color: var(--tx3); }
.spin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--line); border-top-color: var(--accent); flex: none; animation: spin .9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spin { animation-duration: 2.4s; } }
.retry { display: flex; align-items: center; gap: 8px; font: 400 12px/1.4 system-ui; color: var(--tx3); }
.retry .spin { width: 12px; height: 12px; }
.sub { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); text-transform: uppercase; margin-top: 4px; }
.list { border: 1px solid var(--line); border-radius: 10px; overflow: auto; max-height: 160px; flex: none; }
.vrow { height: 52px; display: flex; align-items: center; gap: 12px; padding: 0 12px; background: var(--panel); }
.vrow + .vrow { border-top: 1px solid var(--line); }
.who { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 4px; }
.who b { font: 600 13px/1 system-ui; }
.who span { font: 400 11px/1 ui-monospace, monospace; color: var(--tx3); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.st { display: flex; align-items: center; gap: 6px; font: 400 11px/1 system-ui; color: var(--tx3); white-space: nowrap; }
.dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--tx3); }
.dot.answers { background: var(--good); }
.dot.silent, .dot.notvault { background: var(--adult); }
.cards { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 4px; }
.card { border: 1px solid var(--line); border-radius: 10px; background: var(--panel); padding: 16px; display: flex; flex-direction: column; gap: 10px; }
.card h3 { font: 600 15px/1.2 system-ui; margin: 0; }
.card p { font: 400 12px/1.5 system-ui; }
.card .btn { margin-top: auto; align-self: flex-start; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field label { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); text-transform: uppercase; }
.field .row { display: flex; gap: 8px; }
.field .row .path { flex: 1; }
.input { height: 30px; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); color: var(--tx); padding: 0 10px; font: 400 13px/1 system-ui; outline: none; }
.input:focus { border-color: var(--accent); }
.check { display: flex; align-items: flex-start; gap: 8px; font: 400 12px/1.45 system-ui; color: var(--tx2); }
.check > :first-child { margin-top: 2px; flex: none; }
.check.ok > :first-child { color: var(--good); }
.check.no > :first-child { color: var(--adult); }
.check b { color: var(--tx); font-weight: 600; }
.err { color: var(--adult) !important; }
.facts { display: grid; grid-template-columns: 110px 1fr; gap: 8px 12px; font: 400 12px/1.3 system-ui; margin: 0; }
.facts dt { color: var(--tx3); }
.facts dd { margin: 0; color: var(--tx); }
.caution { border-left: 2px solid var(--warn); padding: 2px 0 2px 10px; font-size: 12px; }
.btn.warn { background: transparent; border-color: var(--warn); color: var(--warn); }
.parts { display: flex; flex-direction: column; gap: 12px; margin-top: 4px; }
.part { display: grid; grid-template-columns: 18px 1fr auto; align-items: center; gap: 4px 10px; }
.part .ok { color: var(--good); }
.part .what { font: 500 13px/1.2 system-ui; }
.part .size { font-size: 11px; color: var(--tx3); }
.part .pbar { grid-column: 2 / 4; display: block; height: 4px; border-radius: 999px; background: var(--line); overflow: hidden; }
.part .pbar > span { display: block; height: 100%; background: var(--accent); }
.part.wait .what { color: var(--tx3); }
.part .spin { width: 12px; height: 12px; }
.part .ring { width: 12px; height: 12px; border-radius: 50%; border: 2px solid var(--line); }
</style>
