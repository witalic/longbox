<script setup lang="ts">
// The question before a login is filled into an address of its site other than
// its own (design/password-manager.md, "A login's site"): both addresses named,
// asked once for each until the logins lock (logins.ts, fillInto).
import { addressOf } from '../data'

defineProps<{ from: string; here: string }>()
const emit = defineEmits<{ (e: 'yes'): void; (e: 'no'): void }>()
</script>

<template>
  <div class="ask">
    <span>Fill the login saved for <b class="mono">{{ addressOf(from) }}</b> into this page, on
      <b class="mono">{{ addressOf(here) }}</b>? Asked once for each address, until the logins lock.</span>
    <button class="btn small accent" @click="emit('yes')">Fill here</button>
    <button class="btn small ghost" @click="emit('no')">Cancel</button>
  </div>
</template>

<style scoped>
.ask { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; padding: 2px 9px 10px; font: 400 12px/1.45 system-ui; color: var(--tx2); }
.ask span { flex: 1 1 100%; }
.ask b { font-weight: 600; color: var(--tx); }
</style>
