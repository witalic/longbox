<script setup lang="ts">
// Run / progress / stop for one vault pass.
//
// The passes all walk the whole library, and only one may run at a time — so a
// row has three states, not two: mine is running (how far, and a way out),
// another one is (wait), or neither (go). Every row needing exactly that is why
// this is a component and not a copy of the same template per row.
import Icon from './Icon.vue'
import ProgressBar from './ProgressBar.vue'

const props = defineProps<{
  op: string          // this row's pass
  active: string      // the pass running right now, '' when none is
  done: number
  total: number
  label: string       // what the button says when nothing is running
  // What the RUNNING pass is, in its own words — the backend names it when the
  // pass starts, so changing a control mid-run cannot relabel what is already
  // going. Without it a progress bar is a number with no subject.
  running?: string
  // A pass that DESTROYS something is not a refresh, and it sits beside panel
  // buttons rather than in a band — so both the icon and the height are the
  // caller's to say.
  icon?: string
  small?: boolean
  danger?: boolean
  accent?: boolean
  // the caller's own reason to refuse — a Save with nothing valid to save
  disabled?: boolean
}>()
const emit = defineEmits<{ (e: 'run'): void; (e: 'stop'): void }>()

const mine = () => props.active === props.op
</script>

<template>
  <template v-if="mine()">
    <span v-if="props.running" class="pwhat">{{ props.running }}</span>
    <ProgressBar :done="props.done" :total="props.total" />
    <span class="pcount">{{ props.total ? `${props.done} / ${props.total}` : 'starting…' }}</span>
    <button class="btn ghost" :class="{ small: props.small }"
            title="Put the pass down at the next title" @click="emit('stop')">
      Stop
    </button>
  </template>
  <button v-else class="btn"
          :class="{ small: props.small, danger: props.danger, accent: props.accent }"
          :disabled="!!props.active || props.disabled"
          :title="props.active ? 'another vault pass is running' : ''" @click="emit('run')">
    <Icon v-if="props.icon !== 'none'" :name="props.icon || 'refresh'"
          :size="props.small ? 12 : 14" />{{ props.label }}
  </button>
</template>

<style scoped>
.pwhat { font: 500 11px/1 system-ui; color: var(--tx2); white-space: nowrap; }
.pcount { font: 500 11px/1 ui-monospace, monospace; color: var(--tx2); white-space: nowrap; font-variant-numeric: tabular-nums; }
</style>
