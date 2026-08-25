<script setup lang="ts">
// The ⋯ action menu that every view band ends with. Dropdown.vue is NOT this:
// it picks a value and emits `update:modelValue`; this one runs commands. Same
// outside-click idiom, different job — so they stay two widgets.
//
// The panel is TELEPORTED and fixed-positioned on purpose: every band sits in a
// `.viewcol`, which clips its overflow, so an absolutely-positioned menu came
// out sliced. Measuring the trigger and drawing over the page is the only way a
// menu can be wider or taller than the strip that owns it.
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import Icon from './Icon.vue'

const props = withDefaults(defineProps<{ title?: string; width?: number; icon?: string }>(),
  { title: 'More', width: 230, icon: 'more' })

const open = ref(false)
const root = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const pos = ref({ left: 0, top: 0 })

function place() {
  const el = root.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const h = panel.value?.offsetHeight ?? 0
  // right-aligned to the trigger, flipped above when the bottom is too close
  const below = r.bottom + 6
  pos.value = {
    left: Math.max(8, Math.min(r.right - props.width, window.innerWidth - props.width - 8)),
    top: h && below + h > window.innerHeight - 8 ? Math.max(8, r.top - 6 - h) : below,
  }
}

async function toggle() {
  open.value = !open.value
  if (!open.value) return
  place()
  await nextTick()
  place() // again with the real height, now that it is in the DOM
}

function onDocDown(e: MouseEvent) {
  const t = e.target as Node
  if (root.value?.contains(t) || panel.value?.contains(t)) return
  open.value = false
}
function onLeave() { open.value = false }
// The panel is fixed-positioned, so it has to FOLLOW the trigger when the page
// moves under it, and give up only when the trigger is gone from the viewport.
// Closing on any scroll looked right until a menu whose own click changed the
// list behind it: removing a row re-flows that list, the container scrolls by a
// pixel, and the menu shut itself the moment it was used.
function onScroll(e: Event) {
  if (panel.value?.contains(e.target as Node)) return // reading a long list
  const r = root.value?.getBoundingClientRect()
  if (!r || r.bottom < 0 || r.top > window.innerHeight) {
    open.value = false // nothing left to hang from
    return
  }
  place()
}

onMounted(() => {
  document.addEventListener('mousedown', onDocDown)
  window.addEventListener('resize', onLeave)
  window.addEventListener('scroll', onScroll, true)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocDown)
  window.removeEventListener('resize', onLeave)
  window.removeEventListener('scroll', onScroll, true)
})
</script>

<template>
  <div ref="root" class="mbwrap">
    <button class="iconbtn" :class="{ on: open }" :title="title" @click="toggle">
      <Icon :name="icon" :size="16" />
    </button>
    <Teleport to="body">
      <div v-if="open" ref="panel" class="menu"
           :style="{ width: `${width}px`, left: `${pos.left}px`, top: `${pos.top}px` }">
        <slot :close="() => (open = false)" />
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
