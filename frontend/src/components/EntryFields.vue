<script setup lang="ts">
// The shared LABEL / LANG / GROUP / LINK rows of an entry form — the add form
// and both inline edits on the title page render the SAME fields; only the
// action buttons differ and stay with the caller. Row styling comes from the
// host's `.entryform :deep()` rules, so the grammar has one CSS source.
//
// Every one of the three is a Combo: the list opens with what is actually in
// use (this title, then this source), and widens to everything known once you
// start typing. A label additionally offers the next few numbers.
import Combo from './Combo.vue'

withDefaults(defineProps<{
  langSuggest: string[]
  groupSuggest: string[]
  labelSuggest?: string[]
  langMore?: string[]
  groupMore?: string[]
  labelPlaceholder?: string
}>(), { labelSuggest: () => [], langMore: () => [], groupMore: () => [], labelPlaceholder: '' })

const label = defineModel<string>('label', { required: true })
const lang = defineModel<string>('lang', { required: true })
const group = defineModel<string>('group', { required: true })
const url = defineModel<string>('url', { required: true })
</script>

<template>
  <div class="irow">
    <span class="flabel">LABEL *</span>
    <Combo v-model="label" :suggestions="labelSuggest" wide
           :placeholder="labelPlaceholder" style="flex:1" />
  </div>
  <div class="irow">
    <span class="flabel">LANG</span>
    <Combo v-model="lang" :suggestions="langSuggest" :more-suggestions="langMore" wide
           placeholder="EN" style="width:96px;flex:none" />
    <span class="flabel">GROUP</span>
    <Combo v-model="group" :suggestions="groupSuggest" :more-suggestions="groupMore" wide
           placeholder="translator / site" style="flex:1;min-width:120px" />
  </div>
  <div class="irow"><span class="flabel">LINK</span><input v-model="url" class="iin mono" style="flex:1;font-size:11px" placeholder="https://…" /></div>
</template>
