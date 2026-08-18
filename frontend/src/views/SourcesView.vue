<script setup lang="ts">
import { reactive, ref } from 'vue'
import Icon from '../components/Icon.vue'
import { askConfirm, refreshLibrary, store } from '../store'
import { newTab } from '../browser'
import { api } from '../api'
import { faviconFor } from '../data'

// domains whose favicon failed to load → fall back to the initial letters
const noIcon = reactive<Record<string, boolean>>({})
const busy = ref<string | null>(null)

async function forgetRecipe(domain: string) {
  const ok = await askConfirm({
    title: 'Forget recipe', danger: true, okLabel: 'Forget',
    message: `Forget everything learned about ${domain}? Its titles are untouched; you'll teach the fields again on the next capture.`,
  })
  if (!ok) return
  busy.value = domain
  try {
    await api.removeRecipe(domain)
    await refreshLibrary()
  } catch (e) { store.error = String(e) } finally { busy.value = null }
}

async function removeSource(domain: string) {
  const ok = await askConfirm({
    title: 'Remove source', danger: true, okLabel: 'Remove',
    message: `Remove ${domain} from this list and forget what longbox learned about it? Your titles keep their source links — clear those per title, on the title itself.`,
  })
  if (!ok) return
  busy.value = domain
  try {
    await api.removeSource(domain)
    await refreshLibrary()
  } catch (e) { store.error = String(e) } finally { busy.value = null }
}
</script>

<template>
  <div class="viewcol">
    <div class="head">
      <h1 class="h1">Sources</h1>
      <span class="count">{{ store.sources.length }} domain{{ store.sources.length === 1 ? '' : 's' }}</span>
      <div style="flex:1"></div>
      <button class="btn accent" @click="newTab()"><Icon name="plus" :size="14" :sw="2.2" />Open browser</button>
    </div>
    <div class="viewscroll scroll">

    <div v-if="!store.sources.length" class="emptyv">
      <div class="et">No sources yet</div>
      <div class="es">Sources appear here once your titles reference them. Open the browser, capture a title, and its
        domain shows up with the recipe it learned.</div>
      <button class="btn accent" @click="newTab()"><Icon name="plus" :size="14" :sw="2.2" />Open browser</button>
    </div>

    <div v-else class="list">
      <div v-for="s in store.sources" :key="s.id" class="scard" :class="{ busy: busy === s.domain }">
        <!-- identity -->
        <div class="identity">
          <span class="init">
            <img v-if="!noIcon[s.domain]" class="favimg" :src="faviconFor(s.domain)" alt=""
                 @error="noIcon[s.domain] = true" />
            <template v-else>{{ (s.domain.slice(0, 2) || '?').toUpperCase() }}</template>
          </span>
          <div style="min-width:0">
            <div class="namerow">
              <span class="sname">{{ s.domain }}</span>
            </div>
            <div class="stats mono">{{ s.titles }} title{{ s.titles === 1 ? '' : 's' }} tracked</div>
          </div>
        </div>

        <!-- recipe -->
        <div class="recipe">
          <div class="recrow">
            <span class="eyebrow">RECIPE</span>
            <template v-if="s.hasRecipe">
              <span class="ver mono">v{{ s.recipeVer }}</span>
              <span class="rhint">learned from captures · updates automatically as you edit</span>
            </template>
            <span v-else class="rhint none">not learned yet — capture a title from this site</span>
          </div>
          <div v-if="s.hasRecipe && s.fields.length" class="fields">
            <span v-for="f in s.fields" :key="f" class="field">{{ f }}</span>
          </div>
        </div>

        <!-- actions: every operation is a real, labeled button in one place -->
        <div class="actions">
          <button class="btn" style="width:100%" :disabled="!s.homepage" @click="newTab(s.homepage)">
            <Icon name="compass" :size="14" />Open in browser
          </button>
          <button v-if="s.hasRecipe" class="btn ghost" style="width:100%" :disabled="busy === s.domain"
                  title="Forget the learned selectors; titles are untouched" @click="forgetRecipe(s.domain)">
            <Icon name="refresh" :size="13" :sw="2" />Forget recipe
          </button>
          <button class="btn ghost danger" style="width:100%" :disabled="busy === s.domain"
                  title="Hide from this list and forget the recipe; titles keep their links" @click="removeSource(s.domain)">
            <Icon name="x" :size="13" :sw="2" />Remove source
          </button>
        </div>
      </div>
    </div>
    </div>
    <div class="lfoot">
      <span class="mono lfinfo">{{ store.sources.length }} source{{ store.sources.length === 1 ? '' : 's' }}</span>
    </div>
  </div>
</template>

<style scoped>
.head { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 12px; padding: 0 24px; }
.count { font: 500 12px/1 system-ui; color: var(--tx3); }
.emptyv { padding: 66px 30px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.et { font: 600 16px/1 system-ui; color: var(--tx); }
.es { font: 400 13px/1.5 system-ui; color: var(--tx3); max-width: 48ch; text-align: center; margin-bottom: 4px; }
.list { padding: 20px 24px 24px; display: flex; flex-direction: column; gap: 12px; }
.scard { display: flex; gap: 22px; padding: 16px; border: 1px solid var(--line); border-radius: 11px; background: var(--panel); align-items: center; }
.scard.busy { opacity: .6; pointer-events: none; }
.identity { flex: 0 1 300px; min-width: 230px; display: flex; align-items: center; gap: 13px; }
.init { width: 44px; height: 44px; flex: none; border-radius: 10px; display: flex; align-items: center; justify-content: center; font: 700 14px/1 system-ui; color: #fff; background: var(--panel2); border: 1px solid var(--line); overflow: hidden; }
.favimg { width: 26px; height: 26px; object-fit: contain; }
.namerow { display: flex; align-items: center; gap: 8px; }
.sname { font: 600 15px/1.1 system-ui; color: var(--tx); }
.stats { margin-top: 5px; font-size: 11px; color: var(--tx3); }
.recipe { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 9px; }
.recrow { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.eyebrow { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); }
.ver { font: 700 10px/1 ui-monospace, monospace; letter-spacing: .05em; color: var(--good); border: 1px solid color-mix(in srgb, var(--good) 40%, var(--line)); padding: 3px 7px; border-radius: 5px; }
.rhint { font: 400 11px/1.3 system-ui; color: var(--tx3); }
.rhint.none { color: var(--warn); }
.fields { display: flex; flex-wrap: wrap; gap: 6px; }
.field { font: 500 11px/1 system-ui; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); padding: 5px 9px; border-radius: 6px; }
.actions { flex: none; width: 172px; display: flex; flex-direction: column; gap: 7px; }
.btn:disabled { opacity: .45; cursor: default; }
.btn.danger { color: var(--adult); border-color: color-mix(in srgb, var(--adult) 40%, var(--line)); }
.btn.danger:hover { background: color-mix(in srgb, var(--adult) 12%, transparent); }
</style>
