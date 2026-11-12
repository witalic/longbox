// Focus for an input Vue puts on the page later: `autofocus` is honoured once
// per document, so a field that appears on a click is focused here instead —
// the element itself, or the first field inside it (a Combo's).
import type { Directive } from 'vue'

export const vFocus: Directive<HTMLElement> = {
  mounted: (el) => (el.matches('input, textarea') ? el : el.querySelector<HTMLElement>('input, textarea'))?.focus(),
}
