// A state that holds for a moment and lapses: what an action reports on the
// button that did it ("Saved"), or in a line beside it. A new moment replaces
// the one showing; a component that goes away takes its timer with it.
import { onBeforeUnmount, ref, type Ref } from 'vue'

export function useMoment<T>(idle: T, ms = 2600): { now: Ref<T>; show: (v: T) => void } {
  const now = ref(idle) as Ref<T>
  let timer: ReturnType<typeof setTimeout> | undefined
  function show(v: T) {
    now.value = v
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => { now.value = idle }, ms)
  }
  onBeforeUnmount(() => { if (timer) clearTimeout(timer) })
  return { now, show }
}
