<script setup lang="ts">
// One section of the contextual rail (shelves, sources, groups). The VIEW owns
// what goes in it — it has the data and the state — while this widget owns the
// grammar, so three views cannot drift into three different-looking rails.
// Rows are plain <button>s in the slot; the styling below claims them.
withDefaults(defineProps<{ label: string; hint?: string }>(), { hint: '' })
</script>

<template>
  <div class="railsec">
    <div class="navlbl">
      <span>{{ label }}</span>
      <span v-if="hint" class="navhint">{{ hint }}</span>
    </div>
    <div class="navsec"><slot /></div>
  </div>
</template>

<style scoped>
.railsec { display: block; }
.navlbl { display: flex; align-items: center; gap: 6px; font: 700 9.5px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); padding: 14px 12px 6px; }
.navhint { margin-left: auto; font: 400 10px/1 system-ui; letter-spacing: 0; text-transform: none; }
.navsec { padding: 0 10px; display: flex; flex-direction: column; gap: 1px; }

/* the rows are the caller's markup; their grammar is this widget's */
.navsec :slotted(button) { height: 30px; width: 100%; display: flex; align-items: center; gap: 10px; padding: 0 12px; border-radius: 7px; border: none; background: transparent; color: var(--tx2); font: 500 12.5px/1 system-ui; cursor: pointer; text-align: left; }
.navsec :slotted(button:hover) { background: var(--hover); color: var(--tx); }
.navsec :slotted(button.on) { background: var(--accentSoft); color: var(--accent); }
.navsec :slotted(button.sub) { height: 26px; padding-left: 30px; color: var(--tx3); font-size: 11.5px; }
.navsec :slotted(button.add) { color: var(--tx3); }
.navsec :slotted(button.add:hover) { color: var(--accent); }
</style>
