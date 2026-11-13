// Knowing a release is out (design/update-check.md). Whether longbox may ask is
// the user's answer, kept with the window's preferences in the vault
// (`lb.updates`); the request itself is the shell's. A check is made at most
// once a day: at launch when the last one is older, then while the app runs.
import { computed, reactive } from 'vue'
import { isRecord, readLocal, writeLocal } from './local'
import { store } from './store'

export interface Release {
  latest: string
  published: string
  security: boolean
  // the version running when it was found: once that changes, it is old news
  over: string
}

interface Kept {
  check: boolean | null // null: not asked yet
  last: number          // when the last check was made (ms); 0: never
  ran: string           // the version that made it
  found: Release | null // the newer release the last good check found
  error: string         // why the last check failed; '' when it did not
  dismissed: string     // the version "Not now" was said to
}

const KEY = 'lb.updates'
const DAY = 24 * 3600 * 1000

const isRelease = (v: unknown): v is Release => isRecord(v)
  && typeof v.latest === 'string' && typeof v.published === 'string' && typeof v.security === 'boolean'
  && typeof v.over === 'string'
const isKept = (v: unknown): v is Kept => isRecord(v)
  && (v.check === null || typeof v.check === 'boolean') && typeof v.last === 'number' && typeof v.ran === 'string'
  && (v.found === null || isRelease(v.found)) && typeof v.error === 'string' && typeof v.dismissed === 'string'

export const updates = reactive({
  ...readLocal<Kept>(KEY, isKept, { check: null, last: 0, ran: '', found: null, error: '', dismissed: '' }),
  checking: false,
})

function keep(): void {
  const { check, last, ran, found, error, dismissed } = updates
  writeLocal(KEY, { check, last, ran, found, error, dismissed })
}

// the shell asks; a plain browser (the sidecar alone, in development) never does
export const canCheck = !!window.longbox?.checkUpdates

// a newer release than the one running — unless the app has been updated since
export const available = computed<Release | null>(() => {
  const found = updates.found
  return found && found.over === store.appMeta.version ? found : null
})
// what the sidebar shows: a newer release nobody said "Not now" to
export const noticed = computed(() => !!available.value && available.value.latest !== updates.dismissed)

export async function checkNow(): Promise<void> {
  const shell = window.longbox
  if (!shell?.checkUpdates || updates.checking) return
  updates.checking = true
  try {
    const got = await shell.checkUpdates().catch((e: unknown): UpdateCheck => ({ ok: false, error: String(e) }))
    const version = store.appMeta.version || ''
    updates.last = Date.now()
    updates.ran = version
    updates.error = got.ok ? '' : got.error || 'the check failed'
    if (got.ok) {
      updates.found = got.newer && got.latest
        ? { latest: got.latest, published: got.published || '', security: !!got.security, over: version }
        : null
    }
    keep()
  } finally {
    updates.checking = false
  }
}

// A day since the last check — or a check this version did not make: the vault
// is shared, and another computer's install (older or newer) may have made it.
// A last check dated in the future (a clock put back) is no reason to wait.
function due(): boolean {
  if (updates.check !== true) return false
  const since = Date.now() - updates.last
  return updates.ran !== (store.appMeta.version || '') || since < 0 || since >= DAY
}

export function answer(yes: boolean): void {
  updates.check = yes
  keep()
  if (due()) void checkNow()
}

export function dismiss(): void {
  if (!available.value) return
  updates.dismissed = available.value.latest
  keep()
}

// a version's release page, or 'latest' for the download — the shell makes the address
export function openRelease(which: string): void {
  void window.longbox?.openRelease(which)
}

// at launch, then hourly — a day is counted from the last check, and an hour's
// grain keeps a computer that slept from waiting a whole extra day
let timer = 0
export function watchUpdates(): void {
  if (!canCheck || timer) return
  if (due()) void checkNow()
  timer = window.setInterval(() => { if (due()) void checkNow() }, 3600 * 1000)
}
