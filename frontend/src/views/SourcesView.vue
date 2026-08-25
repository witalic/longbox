<script setup lang="ts">
// Sources: one row per site, grouped the way the user grouped them. The weight
// (recipe, actions, saved links) belongs to the row you OPEN — a wall of cards
// where two of the three buttons are destructive is not a list you can scan.
import { computed, reactive, ref } from 'vue'
import Combo from '../components/Combo.vue'
import Icon from '../components/Icon.vue'
import SearchBox from '../components/SearchBox.vue'
import MenuButton from '../components/MenuButton.vue'
import RailSection from '../components/RailSection.vue'
import { askConfirm, refreshDerived, refreshLibrary, store } from '../store'
import { newTab } from '../browser'
import { api } from '../api'
import { faviconFor, type Bookmark, type Source } from '../data'

const UNGROUPED = '—'

// domains whose favicon failed to load → fall back to the initial letters
const noIcon = reactive<Record<string, boolean>>({})
const busy = ref<string | null>(null)
const open = ref<string | null>(null)
const q = ref('')
const SORTS = ['Titles', 'Name'] as const
const sort = ref<(typeof SORTS)[number]>('Titles')

// A search here means "find the site" — including by a link saved under it.
const matches = computed(() => {
  const needle = q.value.trim().toLowerCase()
  const hit = (s: Source) => !needle || s.domain.toLowerCase().includes(needle)
    || s.bookmarks.some((b) => `${b.name} ${b.url}`.toLowerCase().includes(needle))
  return [...store.sources].filter(hit).sort((a, b) => (
    sort.value === 'Name' ? a.domain.localeCompare(b.domain) : b.titles - a.titles
  ))
})
// Named groups keep the user's order; whatever is left lands in one bucket.
const sections = computed(() => {
  const names = picked.value ? [picked.value] : [...store.sourceGroups, UNGROUPED]
  return names
    .map((name) => ({
      name,
      rows: matches.value.filter((s) => (s.group || UNGROUPED) === name),
    }))
    .filter((sec) => sec.rows.length)
})
// The rail picks a group; the list shows that one, or every section at once.
const picked = ref('')
const railGroups = computed(() => [...store.sourceGroups, UNGROUPED].map((name) => ({
  name,
  label: name === UNGROUPED ? 'Ungrouped' : name,
  n: store.sources.filter((s) => (s.group || UNGROUPED) === name).length,
})).filter((g) => g.n))

const bookmarkCount = computed(() => store.sources.reduce((n, s) => n + s.bookmarks.length, 0))
const closed = reactive<Record<string, boolean>>({})

// Typing a name that does not exist yet CREATES the group: being able to file a
// source only into groups you made somewhere else is not filing, it is paperwork.
async function setGroup(s: Source, name: string) {
  const g = name.trim()
  if (g && !store.sourceGroups.includes(g)) {
    try {
      store.sourceGroups = await api.putSourceGroups([...store.sourceGroups, g])
    } catch (e) {
      store.error = String(e)
      return
    }
  }
  await savePrefs(s, { group: g })
}

async function savePrefs(s: Source, body: { group?: string; bookmarks?: Bookmark[] }) {
  busy.value = s.domain
  try {
    store.sources = await api.putSourcePrefs(s.domain, body)
  } catch (e) {
    store.error = String(e)
  } finally {
    busy.value = null
  }
}

async function newGroup() {
  const name = window.prompt('Name the group:', '')?.trim()
  if (!name) return
  try {
    store.sourceGroups = await api.putSourceGroups([...store.sourceGroups, name])
  } catch (e) { store.error = String(e) }
}

// ---- bookmarks ----
const adding = ref<string | null>(null)
const bName = ref('')
const bUrl = ref('')
function startAdd(s: Source) {
  adding.value = s.domain
  bName.value = ''
  bUrl.value = s.homepage
}
async function addBookmark(s: Source) {
  const url = bUrl.value.trim()
  if (!url) return
  await savePrefs(s, { bookmarks: [...s.bookmarks, { name: bName.value.trim() || url, url }] })
  adding.value = null
}
async function removeBookmark(s: Source, i: number) {
  await savePrefs(s, { bookmarks: s.bookmarks.filter((_, x) => x !== i) })
}

// ---- the site itself ----
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

void refreshDerived()
</script>

<template>
  <div class="viewcol">
    <div class="head">
      <h1 class="h1">Sources</h1>
      <span class="count mono">{{ store.sources.length }} domain{{ store.sources.length === 1 ? '' : 's' }} · {{ bookmarkCount }} bookmark{{ bookmarkCount === 1 ? '' : 's' }}</span>
      <div style="flex:1"></div>
      <SearchBox v-model="q" placeholder="Search a domain or a bookmark…" />
      <button class="btn ghost" title="Sort the list" @click="sort = sort === 'Titles' ? 'Name' : 'Titles'">
        <Icon name="rows" :size="13" :sw="1.9" />{{ sort }}
      </button>
      <MenuButton v-slot="{ close }">
        <button @click="close(); newTab()"><Icon name="browser" :size="14" :sw="1.9" />Open browser</button>
        <button @click="close(); newGroup()"><Icon name="plus" :size="14" :sw="2.2" />New group</button>
      </MenuButton>
    </div>

    <Teleport to="#siderail">
      <RailSection label="GROUPS" hint="click to narrow">
        <button :class="{ on: !picked }" @click="picked = ''">
          <span class="slbl">All sources</span><span class="n mono">{{ store.sources.length }}</span>
        </button>
        <button v-for="g in railGroups" :key="g.name" :class="{ on: picked === g.name }"
                @click="picked = picked === g.name ? '' : g.name">
          <span class="slbl">{{ g.label }}</span><span class="n mono">{{ g.n }}</span>
        </button>
        <button class="add" @click="newGroup">
          <Icon name="plus" :size="13" :sw="2.2" /><span class="slbl">New group</span>
        </button>
      </RailSection>
    </Teleport>

    <div class="viewscroll scroll">
      <div v-if="!store.sources.length" class="emptyv">
        <div class="et">No sources yet</div>
        <div class="es">Sources appear here once your titles reference them. Open the browser, capture a title, and its
          domain shows up with the recipe it learned.</div>
        <button class="btn accent" @click="newTab()"><Icon name="plus" :size="14" :sw="2.2" />Open browser</button>
      </div>

      <div v-else class="list">
        <template v-for="sec in sections" :key="sec.name">
          <div class="ghead" @click="closed[sec.name] = !closed[sec.name]">
            <Icon name="chevron" :size="10" :sw="2.4" :style="{ transform: closed[sec.name] ? 'rotate(-90deg)' : '' }" />
            <span class="glbl">{{ sec.name === UNGROUPED ? 'UNGROUPED' : sec.name.toUpperCase() }} · {{ sec.rows.length }}</span>
            <span class="grule"></span>
          </div>

          <template v-if="!closed[sec.name]">
            <template v-for="s in sec.rows" :key="s.id">
              <div class="srow" :class="{ on: open === s.domain, busy: busy === s.domain }"
                   @click="open = open === s.domain ? null : s.domain">
                <span class="init">
                  <img v-if="!noIcon[s.domain]" class="favimg" :src="faviconFor(s.domain)" alt=""
                       @error="noIcon[s.domain] = true" />
                  <template v-else>{{ (s.domain.slice(0, 2) || '?').toUpperCase() }}</template>
                </span>
                <span class="sname">{{ s.domain }}</span>
                <span class="rmark" :class="{ none: !s.hasRecipe }">
                  <Icon v-if="s.hasRecipe" name="check" :size="11" :sw="2.6" />
                  {{ s.hasRecipe ? `v${s.recipeVer}` : 'no recipe' }}
                </span>
                <span style="flex:1"></span>
                <span class="snum mono">{{ s.titles }} title{{ s.titles === 1 ? '' : 's' }}</span>
                <span class="sbm mono" :class="{ has: s.bookmarks.length }">
                  <Icon name="star" :size="11" :sw="1.8" :fill="s.bookmarks.length ? 'currentColor' : 'none'" />
                  {{ s.bookmarks.length }}
                </span>
                <Icon name="chevron" :size="12" :sw="2.2" class="chev"
                      :style="{ transform: open === s.domain ? '' : 'rotate(-90deg)' }" />
              </div>

              <div v-if="open === s.domain" class="sopen" @click.stop>
                <div class="bmcol">
                  <div class="collbl">BOOKMARKS</div>
                  <div v-for="(b, i) in s.bookmarks" :key="b.url + i" class="bmrow" @click="newTab(b.url)">
                    <Icon name="star" :size="12" :sw="1.8" fill="currentColor" class="star" />
                    <span class="bmname">{{ b.name }}</span>
                    <span class="bmurl mono">{{ b.url }}</span>
                    <button class="bmact" title="Open in a new tab" @click.stop="newTab(b.url)">
                      <Icon name="forward" :size="12" :sw="2" />
                    </button>
                    <button class="bmact" title="Remove this bookmark" @click.stop="removeBookmark(s, i)">
                      <Icon name="x" :size="12" :sw="2.4" />
                    </button>
                  </div>
                  <div v-if="!s.bookmarks.length && adding !== s.domain" class="bmnone">
                    Nothing saved yet. In the browser, the star in the address bar files the open page here.
                  </div>

                  <div v-if="adding === s.domain" class="bmform" @click.stop>
                    <input v-model="bName" class="in" placeholder="Name it — Follows, Latest, a saved search…" />
                    <input v-model="bUrl" class="in mono" placeholder="https://…" />
                    <div class="bmformrow">
                      <button class="btn ghost sm" @click="adding = null">Cancel</button>
                      <button class="btn accent sm" :disabled="!bUrl.trim()" @click="addBookmark(s)">Save link</button>
                    </div>
                  </div>
                  <div v-else class="bmadd" @click.stop="startAdd(s)">
                    <Icon name="plus" :size="13" :sw="2.2" />Add a link
                  </div>
                </div>

                <div class="metacol">
                  <div class="collbl">RECIPE</div>
                  <div class="mline">
                    <template v-if="s.hasRecipe">
                      <span class="ver mono">v{{ s.recipeVer }}</span>
                      <span class="rhint">learned from captures · updates as you edit</span>
                    </template>
                    <span v-else class="rhint none">not learned yet — capture a title from this site</span>
                  </div>
                  <div v-if="s.hasRecipe && s.fields.length" class="fields">
                    <span v-for="f in s.fields" :key="f" class="field">{{ f }}</span>
                  </div>

                  <div class="collbl" style="margin-top:4px">GROUP</div>
                  <Combo :model-value="s.group" :suggestions="store.sourceGroups" wide lazy
                         placeholder="Ungrouped — type a name to create one"
                         @update:model-value="setGroup(s, $event)" />

                  <div class="acts">
                    <button class="btn sm" :disabled="!s.homepage" @click="newTab(s.homepage)">
                      <Icon name="browser" :size="12" :sw="1.9" />Open homepage
                    </button>
                    <button v-if="s.hasRecipe" class="btn ghost sm" :disabled="busy === s.domain"
                            title="Forget the learned selectors; titles are untouched"
                            @click="forgetRecipe(s.domain)">Forget recipe</button>
                    <button class="btn ghost sm danger" :disabled="busy === s.domain"
                            title="Hide from this list and forget the recipe; titles keep their links"
                            @click="removeSource(s.domain)">Remove</button>
                  </div>
                </div>
              </div>
            </template>
          </template>
        </template>
      </div>
    </div>

    <div class="lfoot">
      <span class="mono lfinfo">{{ matches.length }} of {{ store.sources.length }} shown</span>
    </div>
  </div>
</template>

<style scoped>
.head { height: 44px; flex: none; border-bottom: 1px solid var(--line); display: flex; flex-wrap: nowrap; min-width: 0; align-items: center; gap: 10px; padding: 0 16px 0 24px; }
.count { font: 500 11.5px/1 ui-monospace, monospace; color: var(--tx3); white-space: nowrap; flex: none; }

.emptyv { padding: 66px 30px; display: flex; flex-direction: column; align-items: center; gap: 10px; }
.et { font: 600 16px/1 system-ui; color: var(--tx); }
.es { font: 400 13px/1.5 system-ui; color: var(--tx3); max-width: 48ch; text-align: center; margin-bottom: 4px; }

.slbl { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.n { font: 500 10.5px/1 ui-monospace, monospace; color: var(--tx3); flex: none; }
.list { padding: 10px 24px 20px; }
.ghead { display: flex; align-items: center; gap: 9px; height: 34px; padding: 0 8px; margin-top: 6px; cursor: pointer; color: var(--tx3); }
.ghead:hover { color: var(--tx2); }
.glbl { font: 700 9.5px/1 ui-monospace, monospace; letter-spacing: .12em; }
.grule { flex: 1; height: 1px; background: var(--line); }

.srow { display: flex; align-items: center; gap: 12px; height: 44px; padding: 0 8px; border-radius: 8px; cursor: pointer; color: var(--tx2); }
.srow:hover { background: var(--hover); }
.srow.on { background: var(--panel); }
.srow.busy { opacity: .6; pointer-events: none; }
.init { width: 24px; height: 24px; flex: none; border-radius: 6px; display: flex; align-items: center; justify-content: center; font: 700 9px/1 system-ui; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); overflow: hidden; }
.favimg { width: 15px; height: 15px; object-fit: contain; }
.sname { flex: none; font: 600 13px/1 system-ui; color: var(--tx); }
.rmark { display: inline-flex; align-items: center; gap: 6px; flex: none; font: 500 10px/1 ui-monospace, monospace; color: var(--good); }
.rmark.none { color: var(--warn); }
.snum { flex: none; font-size: 10.5px; color: var(--tx3); }
.sbm { display: inline-flex; align-items: center; gap: 6px; flex: none; width: 52px; justify-content: flex-end; font-size: 10.5px; color: var(--tx3); }
.sbm.has { color: var(--tx2); }
.chev { flex: none; color: var(--tx3); }

.sopen { display: flex; gap: 24px; padding: 6px 8px 16px 44px; }
.bmcol { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.collbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); padding: 6px 6px 7px; }
.bmrow { display: flex; align-items: center; gap: 10px; height: 30px; padding: 0 6px; border-radius: 6px; color: var(--tx2); cursor: pointer; }
.bmrow:hover { background: var(--hover); }
.star { flex: none; color: var(--fav); }
.bmname { flex: none; font: 500 12px/1 system-ui; color: var(--tx); }
.bmurl { flex: 1; min-width: 0; font-size: 10.5px; color: var(--tx3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bmact { width: 24px; height: 24px; flex: none; border: 1px solid transparent; background: transparent; border-radius: 6px; color: var(--tx3); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; }
.bmrow:hover .bmact { border-color: var(--line); }
.bmact:hover { border-color: var(--accent); color: var(--accent); }
.bmnone { padding: 8px 6px 4px; font: 400 11px/1.5 system-ui; color: var(--tx3); }
.bmadd { display: flex; align-items: center; gap: 9px; height: 30px; padding: 0 6px; border-radius: 6px; color: var(--tx3); font: 500 11.5px/1 system-ui; cursor: pointer; }
.bmadd:hover { background: var(--hover); color: var(--accent); }
.bmform { display: flex; flex-direction: column; gap: 7px; padding: 10px; margin: 2px 0; border: 1px solid var(--line); border-radius: 9px; background: var(--panel); }
.bmformrow { display: flex; justify-content: flex-end; gap: 7px; }
.in.mono { font: 400 11px/1 ui-monospace, monospace; }

.metacol { flex: none; width: 320px; display: flex; flex-direction: column; gap: 9px; }
.mline { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.ver { font: 700 10px/1 ui-monospace, monospace; color: var(--good); border: 1px solid color-mix(in srgb, var(--good) 40%, var(--line)); padding: 4px 7px; border-radius: 5px; flex: none; }
.rhint { font: 400 11px/1.4 system-ui; color: var(--tx3); }
.rhint.none { color: var(--warn); }
.fields { display: flex; flex-wrap: wrap; gap: 5px; }
.field { font: 500 10.5px/1 system-ui; color: var(--tx2); background: var(--panel2); border: 1px solid var(--line); padding: 5px 8px; border-radius: 5px; }
.acts { display: flex; gap: 7px; flex-wrap: wrap; margin-top: 4px; }
.btn.sm { height: 26px; padding: 0 10px; font-size: 11.5px; }
.btn:disabled { opacity: .45; cursor: default; }
.btn.danger { color: var(--adult); border-color: color-mix(in srgb, var(--adult) 40%, var(--line)); }
.btn.danger:hover { background: color-mix(in srgb, var(--adult) 12%, transparent); }
.lfinfo { font-size: 11px; color: var(--tx3); }
</style>
