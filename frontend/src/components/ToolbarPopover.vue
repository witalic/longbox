<script setup lang="ts">
// A button of the browser's toolbar that opens a popover hanging from it — the
// key's logins, the shield's ad blocking (design/password-manager.md). The
// button is the toolbar's own grammar: an icon, a count beside it, its state in
// its colour (`tone`), lit while the popover is open. The popover is the app's,
// drawn over the page: a page can neither draw one nor click in it.
import { onBeforeUnmount, watch } from 'vue'
import { usePopover } from '../popover'

// `width`: where it is placed from until it is drawn — then it is as wide as
// what it holds, and follows that
const props = withDefaults(defineProps<{
  title?: string
  width?: number
  tone?: '' | 'quiet' | 'lit' | 'good'
}>(), { title: '', width: 340, tone: '' })
const emit = defineEmits<{ (e: 'closed'): void }>()

const { open, root, panel, pos, show, close, place } = usePopover(() => props.width)
// however it closes — the button, a click outside, the trigger gone — it says so
watch(open, (now) => { if (!now) emit('closed') })
const resized = new ResizeObserver(() => place())
watch(panel, (el, was) => {
  if (was) resized.unobserve(was)
  if (el) resized.observe(el)
})
onBeforeUnmount(() => resized.disconnect())
function toggle() {
  return open.value ? close() : show()
}
defineExpose({ show })
</script>

<template>
  <div ref="root" class="tpwrap">
    <span class="tpb" :class="[tone, { act: open }]" :title="title" @click="toggle"><slot name="button" /></span>
    <Teleport to="body">
      <div v-if="open" ref="panel" class="tpop" :class="{ above: pos.above }"
           :style="{ left: `${pos.left}px`, top: `${pos.top}px` }">
        <span class="caret" :style="{ left: `${pos.caret - 5}px` }"></span>
        <slot :close="close" />
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.tpwrap { position: relative; display: inline-flex; flex: none; }
.tpb { height: 28px; min-width: 28px; padding: 0 6px; border-radius: 7px; flex: none; display: inline-flex; align-items: center; justify-content: center; gap: 4px; color: var(--tx2); cursor: pointer; }
.tpb:hover { background: var(--hover); color: var(--tx); }
.tpb.quiet { color: var(--tx3); }
.tpb.lit { color: var(--accent); }
.tpb.good { color: var(--good); }
.tpb.act { background: var(--accentSoft); color: var(--accent); }
.tpb :deep(.cnt) { font: 500 10px/1 ui-monospace, monospace; color: inherit; opacity: .85; }
.tpop { position: fixed; z-index: 200; background: var(--panel); border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 22px 50px rgba(0,0,0,.6); }
.caret { position: absolute; top: -6px; width: 10px; height: 10px; background: var(--panel); border-left: 1px solid var(--line); border-top: 1px solid var(--line); transform: rotate(45deg); }
.tpop.above .caret { top: auto; bottom: -6px; transform: rotate(225deg); }
</style>
