<script setup lang="ts">
// The ONE metadata editor (design/state-model.md §4): used by the Title view's
// edit mode and the browser's capture panel. Every field is the same widget —
// one label row (name · provenance badge · pick/clear actions) over a full-width
// control, in a single column so it works in the 344px capture dock as-is.
// A human edit marks the field `manual`, making it untouchable for auto capture.
import { computed } from 'vue'
import { DEFAULT_STATUSES, DEFAULT_TYPES } from '../data'
import { clearField, draftState, setCoverUrl, setManual, type EditableField } from '../draft'
import { store } from '../store'
import Combo from './Combo.vue'
import Icon from './Icon.vue'

const props = withDefaults(defineProps<{
  capture?: boolean // show per-field pick buttons (browser context)
}>(), { capture: false })

const emit = defineEmits<{ (e: 'capture', field: EditableField): void }>()

const d = computed(() => draftState.cur)

// open vocabularies: common values + whatever already exists in the library;
// the Combo lets the user type a value that isn't there yet
const typeOptions = computed(() =>
  [...new Set([...DEFAULT_TYPES, ...store.vocab.types])])
const statusOptions = computed(() =>
  [...new Set([...DEFAULT_STATUSES, ...store.vocab.statuses])])

function badge(field: string): 'auto' | 'manual' | '' {
  return d.value?.provenance[field]?.origin ?? ''
}
function markManual(field: string) { setManual(field) }

// chip lists
function addChip(field: 'authors' | 'artists' | 'characters' | 'genres' | 'tags', value: string) {
  const s = value.trim()
  const list = d.value!.meta[field]
  if (s && !list.includes(s)) { list.push(s); setManual(field) }
}
function removeChip(field: 'authors' | 'artists' | 'characters' | 'genres' | 'tags', i: number) {
  d.value!.meta[field].splice(i, 1)
  setManual(field)
}
const authorSuggest = computed(() => store.vocab.authors.filter((a) => !d.value?.meta.authors.includes(a)))
const characterSuggest = computed(() => store.vocab.characters.filter((c) => !d.value?.meta.characters.includes(c)))
const artistSuggest = computed(() => store.vocab.authors.filter((a) => !d.value?.meta.artists.includes(a)))
const genreSuggest = computed(() => store.vocab.genres.filter((g) => !d.value?.meta.genres.includes(g)))
const tagSuggest = computed(() => store.vocab.tags.filter((g) => !d.value?.meta.tags.includes(g)))

const flagDefs = [
  { key: 'adult', label: '18+', color: 'var(--adult)' },
  { key: 'ai', label: 'AI', color: 'var(--ai)' },
  { key: 'censored', label: 'Censored', color: 'var(--cens)' },
] as const

function promptCoverUrl() {
  const url = window.prompt('Cover image URL:', d.value?.cover.sourceUrl || '')
  if (url && /^https?:\/\//.test(url.trim())) setCoverUrl(url.trim(), d.value?.meta.source.url || '')
}
function coverBg(preview: string) {
  return preview ? { background: `#181a1f url('${preview.replace(/'/g, '%27')}') center/cover no-repeat` } : {}
}
</script>

<template>
  <div v-if="d" class="me">
    <!-- title -->
    <div class="fieldrow">
      <div class="lblrow">
        <span class="lbl">TITLE<span class="req">*</span></span>
        <span v-if="badge('title')" class="pbadge" :class="badge('title')">{{ badge('title') }}</span>
        <span class="acts">
          <button v-if="props.capture" class="fact" title="Pick on page" @click="emit('capture', 'title')"><Icon name="pick" :size="12" :sw="1.9" /></button>
          <button class="fact" title="Clear" @click="clearField('title')"><Icon name="x" :size="10" :sw="2.4" /></button>
        </span>
      </div>
      <input v-model="d.meta.title" class="in tin" :class="{ needed: !d.meta.title.trim() }"
             placeholder="Title (required)" @input="markManual('title')" />
    </div>

    <!-- alt titles -->
    <div class="fieldrow">
      <div class="lblrow">
        <span class="lbl">ALT TITLES</span>
        <span v-if="badge('alt')" class="pbadge" :class="badge('alt')">{{ badge('alt') }}</span>
        <span class="acts">
          <button v-if="props.capture" class="fact" title="Pick on page" @click="emit('capture', 'alt')"><Icon name="pick" :size="12" :sw="1.9" /></button>
          <button class="fact" title="Clear" @click="clearField('alt')"><Icon name="x" :size="10" :sw="2.4" /></button>
        </span>
      </div>
      <input v-model="d.meta.alt" class="in" placeholder="Alternate titles" @input="markManual('alt')" />
    </div>

    <!-- cover: same widget shape — label row + a body -->
    <div class="fieldrow">
      <div class="lblrow">
        <span class="lbl">COVER</span>
        <span v-if="badge('cover')" class="pbadge" :class="badge('cover')">{{ badge('cover') }}</span>
        <span class="acts">
          <button v-if="props.capture" class="fact" title="Pick the cover on the page" @click="emit('capture', 'cover')"><Icon name="pick" :size="12" :sw="1.9" /></button>
          <button class="fact wide" title="Use an image URL" @click="promptCoverUrl">URL</button>
          <button class="fact" :disabled="d.cover.kind === 'none'" title="Remove cover" @click="clearField('cover')"><Icon name="x" :size="10" :sw="2.4" /></button>
        </span>
      </div>
      <div class="coverrow">
        <div class="coverthumb" :style="coverBg(d.cover.preview)">
          <Icon v-if="!d.cover.preview" name="image" :size="16" style="color:var(--tx3)" />
        </div>
        <div class="coverhint">
          <template v-if="d.cover.kind === 'captured'">Captured from the page — the stored bytes are exactly this image.</template>
          <template v-else-if="d.cover.kind === 'url'">From URL — fetched when you save.</template>
          <template v-else-if="d.cover.kind === 'keep'">Keeping the stored cover.</template>
          <template v-else>No cover yet{{ props.capture ? ' — pick it on the page or paste a URL' : '' }}.</template>
        </div>
      </div>
    </div>

    <!-- type: dropdown-with-typing (custom values welcome), pickable like any field -->
    <div class="fieldrow">
      <div class="lblrow">
        <span class="lbl">TYPE</span>
        <span v-if="badge('type')" class="pbadge" :class="badge('type')">{{ badge('type') }}</span>
        <span class="acts">
          <button v-if="props.capture" class="fact" title="Pick on page" @click="emit('capture', 'type')"><Icon name="pick" :size="12" :sw="1.9" /></button>
          <button class="fact" :disabled="!d.meta.type" title="Clear — type is optional" @click="clearField('type')"><Icon name="x" :size="10" :sw="2.4" /></button>
        </span>
      </div>
      <Combo :model-value="d.meta.type" :suggestions="typeOptions" wide placeholder="manga / manhwa / your own…"
             @update:model-value="d.meta.type = $event; markManual('type')" />
    </div>

    <!-- status: same open vocabulary widget -->
    <div class="fieldrow">
      <div class="lblrow">
        <span class="lbl">STATUS</span>
        <span v-if="badge('status')" class="pbadge" :class="badge('status')">{{ badge('status') }}</span>
        <span class="acts">
          <button v-if="props.capture" class="fact" title="Pick on page" @click="emit('capture', 'status')"><Icon name="pick" :size="12" :sw="1.9" /></button>
          <button class="fact" :disabled="!d.meta.status" title="Clear — status is optional" @click="clearField('status')"><Icon name="x" :size="10" :sw="2.4" /></button>
        </span>
      </div>
      <Combo :model-value="d.meta.status" :suggestions="statusOptions" wide placeholder="ongoing / completed / your own…"
             @update:model-value="d.meta.status = $event; markManual('status')" />
    </div>

    <!-- year -->
    <div class="fieldrow">
      <div class="lblrow">
        <span class="lbl">YEAR</span>
        <span v-if="badge('year')" class="pbadge" :class="badge('year')">{{ badge('year') }}</span>
        <span class="acts">
          <button v-if="props.capture" class="fact" title="Pick on page" @click="emit('capture', 'year')"><Icon name="pick" :size="12" :sw="1.9" /></button>
          <button class="fact" title="Clear" @click="clearField('year')"><Icon name="x" :size="10" :sw="2.4" /></button>
        </span>
      </div>
      <input v-model="d.meta.year" class="in yearin" placeholder="Year" @input="markManual('year')" />
    </div>

    <!-- flags -->
    <div class="fieldrow">
      <div class="lblrow"><span class="lbl">FLAGS</span></div>
      <div class="flagrow">
        <label v-for="f in flagDefs" :key="f.key" class="flag"
               :style="{ color: d.meta.flags[f.key] ? f.color : 'var(--tx2)', borderColor: d.meta.flags[f.key] ? f.color : 'var(--line)' }"
               @click="d.meta.flags[f.key] = !d.meta.flags[f.key]">
          <span class="box" :style="{ borderColor: d.meta.flags[f.key] ? f.color : 'var(--line)', background: d.meta.flags[f.key] ? f.color : 'transparent' }">
            <Icon v-if="d.meta.flags[f.key]" name="check" :size="9" :sw="3.2" style="color:var(--accent-ink)" />
          </span>{{ f.label }}
        </label>
      </div>
    </div>

    <!-- people & taxonomy: the same list widget four times -->
    <div v-for="lf in ([
           { key: 'authors', label: 'AUTHORS', suggest: authorSuggest, ph: 'add author…' },
           { key: 'artists', label: 'ARTISTS', suggest: artistSuggest, ph: 'add artist…' },
           { key: 'characters', label: 'CHARACTERS', suggest: characterSuggest, ph: 'add character…' },
           { key: 'genres', label: 'GENRES', suggest: genreSuggest, ph: 'add genre…' },
           { key: 'tags', label: 'TAGS', suggest: tagSuggest, ph: 'add tag…' },
         ] as const)" :key="lf.key" class="fieldrow">
      <div class="lblrow">
        <span class="lbl">{{ lf.label }}</span>
        <span v-if="badge(lf.key)" class="pbadge" :class="badge(lf.key)">{{ badge(lf.key) }}</span>
        <span class="acts">
          <button v-if="props.capture" class="fact" title="Pick on page" @click="emit('capture', lf.key)"><Icon name="pick" :size="12" :sw="1.9" /></button>
          <button class="fact" title="Clear" @click="clearField(lf.key)"><Icon name="x" :size="10" :sw="2.4" /></button>
        </span>
      </div>
      <div class="chips">
        <span v-for="(v, i) in d.meta[lf.key]" :key="v" class="chip">{{ v }}<span class="rm" @click="removeChip(lf.key, i)">×</span></span>
        <Combo :suggestions="lf.suggest" add-mode :placeholder="lf.ph" @add="addChip(lf.key, $event)" />
      </div>
    </div>

    <!-- description -->
    <div class="fieldrow">
      <div class="lblrow">
        <span class="lbl">DESCRIPTION</span>
        <span v-if="badge('desc')" class="pbadge" :class="badge('desc')">{{ badge('desc') }}</span>
        <span class="acts">
          <button v-if="props.capture" class="fact" title="Pick on page" @click="emit('capture', 'desc')"><Icon name="pick" :size="12" :sw="1.9" /></button>
          <button class="fact" title="Clear" @click="clearField('desc')"><Icon name="x" :size="10" :sw="2.4" /></button>
        </span>
      </div>
      <textarea v-model="d.meta.desc" class="in ta" placeholder="Description" @input="markManual('desc')"></textarea>
    </div>

  </div>
</template>

<style scoped>
.me { display: flex; flex-direction: column; gap: 15px; min-width: 0; }

.fieldrow { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.lblrow { display: flex; align-items: center; gap: 7px; min-height: 20px; }
.lbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); }
.req { color: var(--warn); margin-left: 2px; }
.acts { margin-left: auto; display: flex; gap: 3px; align-items: center; }
.fact { height: 20px; min-width: 20px; display: inline-flex; align-items: center; justify-content: center; border: none; border-radius: 5px; background: transparent; color: var(--tx3); cursor: pointer; padding: 0 3px; }
.fact:hover { background: var(--hover); color: var(--tx); }
.fact.wide { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .06em; padding: 0 6px; }
.fact:disabled { opacity: .4; cursor: default; }

/* provenance badge — the field widget's origin mark */
.pbadge { font: 700 8px/1 ui-monospace, monospace; letter-spacing: .1em; text-transform: uppercase; padding: 3px 6px; border-radius: 4px; flex: none; }
.pbadge.auto { color: var(--accent); background: var(--accentSoft); }
.pbadge.manual { color: var(--warn); background: rgba(214,164,79,.12); }

.in { font: 500 12.5px/1.3 system-ui; color: var(--tx); background: var(--panel); border: 1px solid var(--line); border-radius: 7px; padding: 8px 10px; outline: none; width: 100%; }
.in:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accentSoft); }
.tin { font-weight: 600; }
.tin.needed { border-color: color-mix(in srgb, var(--warn) 55%, var(--line)); }
.yearin { width: 110px; }
.ta { min-height: 76px; resize: vertical; }

.coverrow { display: flex; gap: 12px; align-items: flex-start; }
.coverthumb { width: 76px; height: 110px; flex: none; border-radius: 6px; border: 1px solid var(--line); background: var(--panel2); display: flex; align-items: center; justify-content: center; overflow: hidden; }
.coverhint { font: 400 11px/1.5 system-ui; color: var(--tx3); padding-top: 4px; min-width: 0; }


.flagrow { display: flex; gap: 7px; flex-wrap: wrap; }
.flag { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font: 500 10.5px/1 system-ui; border: 1px solid; padding: 6px 9px; border-radius: 6px; }
.flag .box { width: 12px; height: 12px; border-radius: 3px; border: 1.5px solid; display: inline-flex; align-items: center; justify-content: center; }

.chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-width: 0; }
.chip { display: inline-flex; align-items: center; gap: 6px; font: 500 11.5px/1 system-ui; color: var(--tx); background: var(--panel2); border: 1px solid var(--line); padding: 5px 9px; border-radius: 6px; max-width: 100%; }
.rm { color: var(--tx3); cursor: pointer; font-size: 13px; }
.rm:hover { color: var(--adult); }
</style>
