// A panel hanging from its trigger — MenuButton.vue's menu, ToolbarPopover.vue's
// popover. It is TELEPORTED and fixed-positioned: every band sits in a
// `.viewcol`, which clips its overflow, so an absolutely-positioned panel came
// out sliced. Measuring the trigger and drawing over the page is the only way a
// panel can be wider or taller than the strip that owns it. It hangs 6px under
// the trigger, its right edge on the trigger's (flipped above when the bottom
// is too close), and `pos.caret` is where the trigger's middle falls on it.
// `width` is what it is drawn at until it is in the page and can be measured.
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

export function usePopover(width: () => number) {
  const open = ref(false)
  const root = ref<HTMLElement | null>(null)
  const panel = ref<HTMLElement | null>(null)
  const pos = ref({ left: 0, top: 0, caret: 0, above: false })

  function place() {
    const el = root.value
    if (!el) return
    const r = el.getBoundingClientRect()
    const w = panel.value?.offsetWidth || width() // as it is drawn, once it is
    const h = panel.value?.offsetHeight ?? 0
    const below = r.bottom + 6
    const above = !!h && below + h > window.innerHeight - 8
    const left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8))
    pos.value = { left, top: above ? Math.max(8, r.top - 6 - h) : below, caret: r.left + r.width / 2 - left, above }
  }

  async function show() {
    open.value = true
    place()
    await nextTick()
    place() // again with the real height, now that it is in the DOM
  }
  function close() { open.value = false }
  function toggle() { return open.value ? close() : show() }

  // a dialog the panel opened (a confirmation, the master password) is part of it
  function onDocDown(e: MouseEvent) {
    const t = e.target as Node
    if (root.value?.contains(t) || panel.value?.contains(t)) return
    if (t instanceof Element && t.closest('[data-modal]')) return
    open.value = false
  }
  // Fixed-positioned, it FOLLOWS the trigger when the page moves under it, and
  // gives up only when the trigger is gone from the viewport. Closing on any
  // scroll looked right until a panel whose own click changed the list behind
  // it: removing a row re-flows that list, the container scrolls by a pixel,
  // and the panel shut itself the moment it was used.
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
    window.addEventListener('resize', close)
    window.addEventListener('scroll', onScroll, true)
  })
  onBeforeUnmount(() => {
    document.removeEventListener('mousedown', onDocDown)
    window.removeEventListener('resize', close)
    window.removeEventListener('scroll', onScroll, true)
  })
  return { open, root, panel, pos, place, show, close, toggle }
}
