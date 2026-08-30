<script setup lang="ts">
// One field's definition — the same form whether it is being created or edited.
//
// It appears in two places at once (unfolded under the row you opened, and at
// the bottom for a new one), so it is a component rather than two copies that
// would have drifted on the first change to either. The list owns the registry
// and the API calls; this owns the inputs and what a valid definition is.
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import PassButton from './PassButton.vue'
import type { FieldDef } from '../data'
import { stopVaultPass, vaultPass } from '../store'

const props = defineProps<{
  field: FieldDef | null   // null while creating
  usedCount: number        // titles holding a value, for the retype warning
  busy: boolean
  error: string
}>()
const emit = defineEmits<{
  (e: 'save', v: { id: string; label: string; type: string; facet: boolean; join: string }): void
  (e: 'cancel'): void
  (e: 'remove', values: boolean): void
}>()

const FIELD_TYPES = [
  { v: 'text', label: 'Text', hint: 'One line of free text — a note, an id, a title. Can be a filter.' },
  { v: 'description', label: 'Description',
    hint: 'Prose: a summary, a note to yourself. Never a filter and never an axis — there is no '
      + 'vocabulary to tick in a paragraph.' },
  { v: 'number', label: 'Number', hint: 'A score or a count. Stored and shown; ranges come later.' },
  { v: 'list', label: 'List', hint: 'Many values per title, filtered by ticking them — like tags.' },
  { v: 'date', label: 'Date', hint: 'A calendar day, stored as YYYY-MM-DD.' },
] as const

// Which type a field can become is a fact about the two TYPES, not about what
// happens to be stored.
//
// Number and date can be LEFT but never ENTERED: a number is a number because
// it was created as one, and letting arbitrary text be declared one is the same
// category error as calling a list of names a number. Text, description and
// list convert freely — joining, splitting and re-reading a string are always
// possible, whatever it says.
const STRICT = ['number', 'date']

function blocked(v: string): boolean {
  const from = props.field?.type
  return !!from && v !== from && STRICT.includes(v)
}
function whyBlocked(v: string, name: string): string {
  return blocked(v)
    ? `A field is a ${name} because it was created as one — ${props.field?.type} cannot become it`
    : `Store this field as a ${name}`
}

const label = ref('')
const type = ref<string>('list')
const facet = ref(true)
// What a list folds into when it becomes text, and what a text splits back on.
// Asked for at the moment of the change, because only the person making it knows
// what the values look like: a comma is right for tags and wrong for names.
const join = ref(', ')
// Presets fill the box; the box is the answer. A separator is whatever the
// values were written with, and only the person who wrote them knows it.
const JOINS = [
  { v: ', ', label: 'comma' }, { v: ' | ', label: 'bar' },
  { v: ' · ', label: 'dot' }, { v: ' ', label: 'space' },
] as const

watch(() => props.field, (f) => {
  label.value = f?.label ?? ''
  type.value = f?.type ?? 'list'
  // Prose reports itself as never-a-filter, so a field coming BACK from it
  // would arrive with the box unticked and no hint that it once was one.
  facet.value = f ? (f.control === 'multiline' || f.facet) : true
}, { immediate: true })

// Only a change between a list and a single value has anything to join or split.
const wasList = computed(() => props.field?.type === 'list')
const isList = computed(() => type.value === 'list')
const needsJoin = computed(() => !!props.field && wasList.value !== isList.value)
const prose = computed(() => type.value === 'description')

// The id is what every stored value hangs on, so it is derived once from the
// first label and then frozen — renaming the label later is free.
const slug = computed(() => label.value.toLowerCase().replace(/[^a-z0-9]+/g, '_')
  .replace(/^_+|_+$/g, '').slice(0, 32))
const id = computed(() => props.field?.id || slug.value)
const canSave = computed(() => !!label.value.trim() && !!id.value)
const typeHint = computed(() => FIELD_TYPES.find((t) => t.v === type.value)?.hint ?? '')
// Only a type CHANGE puts stored values at risk; the count alone would warn
// every time you opened a field that has any.
const retyping = computed(() => !!props.field && props.field.type !== type.value)

function submit() {
  if (!canSave.value) return
  emit('save', { id: id.value, label: label.value.trim(), type: type.value,
                 facet: facet.value && !prose.value, join: join.value })
}
</script>

<template>
  <div class="fform" @click.stop>
    <div class="fgrid">
      <label class="flabel2">NAME</label>
      <input v-model="label" class="in" style="height:30px"
             placeholder="Shelf, Bought on, MAL score…" />
      <label class="flabel2">ID</label>
      <div class="idfixed mono">
        {{ props.field ? props.field.id : (slug || '—') }} ·
        {{ props.field ? 'fixed, every stored value hangs on it' : 'derived from the name, then fixed' }}
      </div>
      <label class="flabel2">TYPE</label>
      <div>
        <div class="seg" style="width: fit-content">
          <button v-for="t in FIELD_TYPES" :key="t.v" class="opt"
                  :class="{ on: type === t.v, off: blocked(t.v) }" :disabled="blocked(t.v)"
                  :title="whyBlocked(t.v, t.label.toLowerCase())"
                  @click="type = t.v">{{ t.label }}</button>
        </div>
        <div class="hint">{{ typeHint }}</div>
      </div>
    </div>

    <div v-if="needsJoin" class="fgrid">
      <label class="flabel2">JOIN</label>
      <div>
        <div class="joinrow">
          <input v-model="join" class="in joinin" placeholder=", " spellcheck="false" />
          <button v-for="j in JOINS" :key="j.v" class="btn ghost small"
                  :title="`Use ${j.label}`" @click="join = j.v">{{ j.label }}</button>
        </div>
        <div class="hint">
          {{ isList ? 'What the stored text is split on to become a list.'
             : 'What the stored values are joined with to become one line.' }}
          Type any separator — a single space is one.
        </div>
      </div>
    </div>

    <div v-if="!prose" class="fcheck" @click="facet = !facet">
      <span class="fbox" :class="{ on: facet }">
        <Icon v-if="facet" name="check" :size="9" :sw="3.2" />
      </span>Offer it as a library filter
    </div>
    <div v-else class="hint">Prose is never offered as a filter or an axis.</div>

    <div v-if="retyping && props.usedCount" class="fwarn">
      {{ props.usedCount }} title(s) already hold a value here. Changing the type converts what
      converts and leaves the rest in the vault untouched.
    </div>
    <div v-if="props.error" class="fwarn err">{{ props.error }}</div>

    <div class="frowend">
      <!-- two different intentions, and only one of them touches the data -->
      <template v-if="props.field">
        <button class="btn ghost" :disabled="props.busy"
                title="Stop offering the field — every value stays in the vault"
                @click="emit('remove', false)">Remove field</button>
        <!-- clearing the values is a pass over every title that holds one, so
             it reports like one instead of looking like nothing happened -->
        <PassButton op="clear" :active="vaultPass.key" :done="vaultPass.done"
                    :total="vaultPass.total" :running="vaultPass.op" danger icon="x"
                    :disabled="props.busy" label="Remove with values"
                    @run="emit('remove', true)" @stop="stopVaultPass" />
      </template>
      <div style="flex:1"></div>
      <button class="btn ghost" :disabled="props.busy" @click="emit('cancel')">Cancel</button>
      <!-- Saving a type change converts what titles hold, which is a pass over
           the library like any other — so it reports like one, in place. -->
      <PassButton op="retype" :active="vaultPass.key" :done="vaultPass.done"
                  :total="vaultPass.total" :running="vaultPass.op" accent icon="none"
                  :disabled="!canSave || props.busy"
                  :label="props.field ? 'Save' : 'Create field'"
                  @run="submit" @stop="stopVaultPass" />
    </div>
  </div>
</template>

<style scoped>
.fform { display: flex; flex-direction: column; gap: 10px; padding: 12px; margin: 2px 0 6px; border: 1px solid var(--line); border-radius: 9px; background: var(--panel); }
/* The label names the CONTROL, not the whole cell: a row whose value is a
   control plus a line of help centres the label between the two, which is the
   crooked TYPE row. Aligned to the top and given the control's own line box,
   every label sits against the first thing in its row. */
.fgrid { display: grid; grid-template-columns: 62px 1fr; gap: 9px 12px; align-items: start; }
.joinrow { display: flex; align-items: center; gap: 6px; }
.joinin { width: 96px; height: 30px; font-family: ui-monospace, monospace; }
.seg .opt.off { opacity: .38; cursor: not-allowed; }
.flabel2 { font: 700 8.5px/30px ui-monospace, monospace; letter-spacing: .08em; color: var(--tx3); }
.idfixed { font: 400 11px/30px ui-monospace, monospace; color: var(--tx3); }
.fcheck { display: inline-flex; align-items: center; gap: 9px; font: 500 12px/1 system-ui; color: var(--tx2); cursor: pointer; user-select: none; }
.fcheck:hover { color: var(--tx); }
.fwarn { padding: 9px 11px; border-radius: 8px; font: 400 11.5px/1.5 system-ui; color: var(--tx2); border: 1px solid color-mix(in srgb, var(--warn) 40%, var(--line)); background: color-mix(in srgb, var(--warn) 8%, transparent); }
.fwarn.err { border-color: color-mix(in srgb, var(--adult) 45%, var(--line)); background: color-mix(in srgb, var(--adult) 8%, transparent); }
.frowend { display: flex; align-items: center; gap: 8px; }
</style>
