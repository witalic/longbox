<script setup lang="ts">
// The one progress bar in the app.
//
// Downloads had it first — a transfer's share of its total. The vault passes
// need exactly the same thing at control scale, and a second copy would have
// drifted from this one the moment either was restyled. `wide` is the only
// difference that survived: a panel row spans its card, a control-band bar is
// as long as a word.
const props = withDefaults(
  defineProps<{ done: number; total: number; wide?: boolean }>(), { wide: false })

// An unknown total (a download whose length the server never sent) draws an
// empty track rather than a lie about being finished.
const pct = () => (props.total > 0 ? Math.min(100, (props.done / props.total) * 100) : 0)
</script>

<template>
  <span class="pbar" :class="{ wide: props.wide }"><span :style="{ width: `${pct()}%` }" /></span>
</template>

<style scoped>
.pbar { display: block; width: 84px; height: 4px; flex: none; border-radius: 999px; background: var(--line); overflow: hidden; }
.pbar.wide { width: auto; }
.pbar > span { display: block; height: 100%; background: var(--accent); transition: width .3s ease; }
</style>
