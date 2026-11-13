<script setup lang="ts">
// The ⋯ action menu that every view band ends with. Dropdown.vue is NOT this:
// it picks a value and emits `update:modelValue`; this one runs commands. Same
// outside-click idiom, different job — so they stay two widgets. It hangs from
// its button as every panel of the app does (popover.ts).
import Icon from './Icon.vue'
import { usePopover } from '../popover'

const props = withDefaults(defineProps<{ title?: string; width?: number; icon?: string }>(),
  { title: 'More', width: 230, icon: 'more' })

const { open, root, panel, pos, toggle, close } = usePopover(() => props.width)
</script>

<template>
  <div ref="root" class="mbwrap">
    <button class="iconbtn" :class="{ on: open }" :title="title" @click="toggle">
      <Icon :name="icon" :size="16" />
    </button>
    <Teleport to="body">
      <div v-if="open" ref="panel" class="menu"
           :style="{ width: `${width}px`, left: `${pos.left}px`, top: `${pos.top}px` }">
        <slot :close="close" />
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.mbwrap { position: relative; display: inline-flex; flex: none; }
.iconbtn { width: 30px; height: 30px; flex: none; border: 1px solid var(--line); background: var(--panel); border-radius: 6px; cursor: pointer; display: inline-flex; align-items: center; justify-content: center; color: var(--tx2); }
.iconbtn:hover { border-color: var(--accent); color: var(--accent); }
.iconbtn.on { background: var(--accentSoft); border-color: var(--accent); color: var(--accent); }
.menu { position: fixed; z-index: 200; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 22px 50px rgba(0,0,0,.6); padding: 6px; max-height: calc(100vh - 24px); overflow-y: auto; }
/* the items are the caller's, but their grammar is this widget's */
.menu :slotted(button) { width: 100%; display: flex; align-items: center; gap: 10px; height: 32px; padding: 0 9px; border: none; background: transparent; border-radius: 6px; color: var(--tx2); font: 500 12.5px/1 system-ui; cursor: pointer; text-align: left; }
.menu :slotted(button:hover) { background: var(--hover); color: var(--tx); }
.menu :slotted(button:disabled) { opacity: .45; cursor: default; }
.menu :slotted(button:disabled:hover) { background: transparent; color: var(--tx2); }
.menu :slotted(button.danger) { color: var(--adult); }
.menu :slotted(hr) { height: 1px; border: none; background: var(--line); margin: 5px 4px; }
</style>
