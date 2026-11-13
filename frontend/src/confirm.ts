// The one confirmation dialog (ConfirmModal.vue): a question, its optional
// checkboxes, and the answer. `askConfirm` answers yes or no; `askChoice` also
// answers 'alt' for the second way to agree a request may name (`altLabel`).
import { reactive } from 'vue'

export interface ConfirmChoice { label: string; hint?: string; on: boolean }
export interface ConfirmReq {
  title?: string; message: string; okLabel?: string; danger?: boolean; choices?: ConfirmChoice[]
  altLabel?: string
}
export const confirmState = reactive<{ open: boolean; req: ConfirmReq; resolve: ((v: 'ok' | 'alt' | false) => void) | null }>({
  open: false, req: { message: '' }, resolve: null,
})
export function askChoice(req: ConfirmReq): Promise<'ok' | 'alt' | false> {
  return new Promise((resolve) => { confirmState.open = true; confirmState.req = req; confirmState.resolve = resolve })
}
export function askConfirm(req: ConfirmReq): Promise<boolean> {
  return askChoice(req).then((v) => v === 'ok')
}
export function resolveConfirm(value: boolean | 'alt') {
  confirmState.resolve?.(value === true ? 'ok' : value)
  confirmState.open = false
  confirmState.resolve = null
}
