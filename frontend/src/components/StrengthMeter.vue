<script setup lang="ts">
// How strong a new master password looks (logins.ts `strength`): four bars and
// a word — what keeps a copied vault's logins, so it is shown while it is typed.
import { computed } from 'vue'
import { MIN_MASTER, strength } from '../logins'

const props = defineProps<{ value: string }>()
const score = computed(() => strength(props.value))
const SAYS = [`at least ${MIN_MASTER} characters`, 'weak', 'fair', 'good', 'strong']
</script>

<template>
  <div class="meter">
    <span class="bars"><i v-for="n in 4" :key="n" :class="{ on: n <= score }"></i></span>
    <span class="says">{{ SAYS[score] }}</span>
  </div>
</template>

<style scoped>
.meter { display: flex; align-items: center; gap: 10px; }
.bars { flex: 1; display: flex; gap: 4px; }
.bars i { flex: 1; height: 4px; border-radius: 999px; background: var(--line); }
.bars i.on { background: var(--good); }
.says { font: 400 11px/1 system-ui; color: var(--tx3); width: 130px; text-align: right; }
</style>
