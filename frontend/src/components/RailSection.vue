<script setup lang="ts">
// One section of the contextual rail (shelves, sources, groups). The VIEW owns
// what goes in it — it has the data and the state — while this widget owns the
// grammar, so three views cannot drift into three different-looking rails.
// Rows are plain <button>s in the slot; the styling below claims them.
//
// A section can FOLD when the caller says so: a rail listing a dozen source
// groups is a wall, and which of them you are working in changes by the hour.
// The open state belongs to the caller (it is the thing worth remembering), so
// it arrives as a model rather than living here.
import Icon from './Icon.vue'

const props = withDefaults(
  defineProps<{ label: string; hint?: string; collapsible?: boolean }>(),
  { hint: '', collapsible: false })
const open = defineModel<boolean>('open', { default: true })
</script>

<template>
  <div class="railsec">
    <component :is="props.collapsible ? 'button' : 'div'" class="navlbl"
               :class="{ fold: props.collapsible }"
               :title="props.collapsible ? (open ? `Hide ${label}` : `Show ${label}`) : undefined"
               @click="props.collapsible && (open = !open)">
      <Icon v-if="props.collapsible" name="chevron" :size="9" :sw="2.8" class="secchev"
            :style="open ? '' : 'transform: rotate(-90deg)'" />
      <span>{{ label }}</span>
      <span v-if="hint" class="navhint">{{ hint }}</span>
    </component>
    <div v-show="open" class="navsec"><slot /></div>
  </div>
</template>

<style scoped>
.railsec { display: block; }
.navlbl { display: flex; align-items: center; gap: 6px; font: 700 9.5px/1 ui-monospace, monospace; letter-spacing: .12em; color: var(--tx3); padding: 14px 12px 6px; }
.navlbl.fold { width: 100%; border: none; background: transparent; cursor: pointer; text-align: left; }
.navlbl.fold:hover { color: var(--tx2); }
.secchev { flex: none; transition: transform .12s ease; }
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
