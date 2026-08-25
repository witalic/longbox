<script setup lang="ts">
// Which fields a surface offers, in ONE grammar: a row per field, the eye hides
// it, and everything hidden collapses into HIDDEN · n at the end of the same
// list. No separate settings screen — the list IS the setting.
// The scope is the caller's business: this widget only shows what it is given.
import { computed, ref } from 'vue'
import type { FieldDef } from '../data'
import Icon from './Icon.vue'

const props = defineProps<{
  fields: FieldDef[]  // what this surface can offer, in registry order
  hidden: FieldDef[]  // the ones currently hidden here
  title?: string   // '' = no header at all (a caller that already has one)
  note?: string
  // Filters use the same list to CHOOSE the field whose values are shown below.
  // Left undefined the rows only hide, which is all the other surfaces need.
  selected?: string
  counts?: Record<string, number>
}>()
const emit = defineEmits<{
  (e: 'set', id: string, hidden: boolean): void
  (e: 'pick', id: string): void
}>()

const openHidden = ref(false)
const shown = computed(() => props.fields)
</script>

<template>
  <div class="fv">
    <div v-if="title" class="fvhead">
      <span class="fvtitle">{{ title }}</span>
      <span class="fvhint">the eye hides one</span>
    </div>
    <div v-if="note" class="fvnote">{{ note }}</div>
    <div class="fvlist scroll">
      <div v-for="f in shown" :key="f.id" class="frow"
           :class="{ pick: props.selected !== undefined, on: props.selected === f.id }"
           @click="props.selected !== undefined && emit('pick', f.id)">
        <span class="fname">{{ f.label }}</span>
        <span v-if="props.counts?.[f.id]" class="numbadge">{{ props.counts[f.id] }}</span>
        <span v-if="!f.builtin" class="custtag">CUSTOM</span>
        <span v-if="f.required" class="reqtag" title="Required — it cannot be hidden">REQUIRED</span>
        <button v-else class="eye" title="Hide this field here" @click.stop="emit('set', f.id, true)">
          <Icon name="eyeoff" :size="13" :sw="1.9" />
        </button>
      </div>

      <template v-if="hidden.length">
        <button class="hidrow" @click="openHidden = !openHidden">
          <Icon name="chevron" :size="10" :sw="2.4" :style="{ transform: openHidden ? '' : 'rotate(-90deg)' }" />
          <span class="hidlbl">HIDDEN · {{ hidden.length }}</span>
        </button>
        <button v-for="f in (openHidden ? hidden : [])" :key="f.id" class="hidrow sub"
                @click="emit('set', f.id, false)">
          <span class="fname">{{ f.label }}</span>
          <span class="showlbl">show</span>
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.fv { display: flex; flex-direction: column; min-height: 0; }
.fvhead { height: 32px; flex: none; display: flex; align-items: center; gap: 8px; padding: 0 6px; }
.fvtitle { font: 700 9.5px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx2); }
.fvhint { margin-left: auto; font: 400 10px/1 system-ui; color: var(--tx3); }
.fvnote { padding: 6px; font: 400 10.5px/1.5 system-ui; color: var(--tx3); border-bottom: 1px solid var(--line); margin-bottom: 4px; }
.fvlist { display: flex; flex-direction: column; gap: 1px; max-height: 320px; overflow-y: auto; }
.frow { display: flex; align-items: center; gap: 8px; height: 30px; padding: 0 8px; border-radius: 6px; color: var(--tx2); font: 500 12.5px/1 system-ui; }
.frow:hover { background: var(--hover); color: var(--tx); }
.frow.pick { cursor: pointer; }
.frow.on { background: var(--accentSoft); color: var(--accent); }
.fname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.custtag { font: 700 7.5px/1 ui-monospace, monospace; letter-spacing: .1em; color: var(--accent); background: var(--accentSoft); padding: 3px 5px; border-radius: 4px; flex: none; }
.reqtag { font: 700 7.5px/1 ui-monospace, monospace; letter-spacing: .1em; color: var(--tx3); padding: 3px 5px; flex: none; }
.eye { width: 22px; height: 22px; flex: none; border: none; background: transparent; border-radius: 5px; display: inline-flex; align-items: center; justify-content: center; color: var(--tx3); opacity: .55; cursor: pointer; }
.eye:hover { background: var(--panel2); color: var(--tx); opacity: 1; }
.hidrow { display: flex; align-items: center; gap: 8px; width: 100%; height: 27px; padding: 0 8px; border: none; background: transparent; border-radius: 6px; color: var(--tx3); font: 500 12px/1 system-ui; cursor: pointer; text-align: left; }
.hidrow:hover { background: var(--hover); color: var(--tx2); }
.hidrow.sub { padding-left: 22px; }
.hidlbl { font: 700 9px/1 ui-monospace, monospace; letter-spacing: .12em; }
.showlbl { font: 500 10px/1 ui-monospace, monospace; flex: none; }
</style>
