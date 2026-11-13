// The vault's logins, as the window knows them (design/password-manager.md):
// whether there is a master password and whether it is given, and the one
// dialog that asks for it (MasterDialog.vue). The logins themselves stay in the
// shell (shell/passwords.js); a password reaches the window only when the
// owner reveals one.
import { reactive, ref, watch } from 'vue'
import { hostOf } from './data'
import { readLocalOne, writeLocalOne } from './local'

export const loginsState = reactive<LoginsState>({ exists: false, unlocked: false, restored: false })

// After how many minutes with no use of the computer the logins lock by
// themselves ('0': never for that) — the window's preference, told to the shell
// at start and on every change. Sleep and a locked screen lock them always.
export const IDLE_LOCKS = ['5', '15', '30', '60', '0'] as const
export const idleLock = ref(readLocalOne('lb.loginsIdle', IDLE_LOCKS, '15'))

// The master password asked for: 'setup' the first time, 'unlock' after;
// `resolve` answers whoever asked, true once given.
export const masterAsk = reactive<{ mode: 'unlock' | 'setup' | null; error: string; busy: boolean;
  resolve: ((ok: boolean) => void) | null }>({ mode: null, error: '', busy: false, resolve: null })

export class LoginsError extends Error {
  constructor(readonly code: string, message: string) { super(message) }
}

// what an answer holds, or the error the owner is told
export async function settled<T>(answer: Promise<LoginsAnswer<T>>): Promise<T> {
  const got = await answer
  if (!got) throw new LoginsError('ENOSHELL', 'the logins need the desktop app')
  if (!got.ok) throw new LoginsError(got.code, got.message)
  return got.value
}

export async function watchLogins() {
  const shell = window.longbox?.logins
  if (!shell) return
  shell.onState((s) => { Object.assign(loginsState, s) }) // nothing handed back: the bridge would clone it
  const tell = (v: string) => { void settled(shell.lockWhenIdle(Number(v))).catch(() => {}) }
  tell(idleLock.value)
  watch(idleLock, (v) => {
    writeLocalOne('lb.loginsIdle', v)
    tell(v)
  })
  Object.assign(loginsState, await settled(shell.state()))
}

// Unlocked when it resolves true: at once if it is, else once the owner gives
// the master password — or sets one, the first time.
export function askMaster(): Promise<boolean> {
  if (loginsState.unlocked) return Promise.resolve(true)
  if (!window.longbox?.logins) return Promise.resolve(false)
  masterAsk.resolve?.(false)
  return new Promise((resolve) => {
    Object.assign(masterAsk, { mode: loginsState.exists ? 'unlock' : 'setup', error: '', busy: false, resolve })
  })
}

export async function giveMaster(master: string) {
  const shell = window.longbox?.logins
  if (!shell || !masterAsk.mode || masterAsk.busy) return
  masterAsk.busy = true
  masterAsk.error = ''
  try {
    await settled(masterAsk.mode === 'setup' ? shell.setup(master) : shell.unlock(master))
    Object.assign(loginsState, await settled(shell.state()))
    const done = masterAsk.resolve
    Object.assign(masterAsk, { mode: null, resolve: null })
    done?.(true)
  } catch (e) {
    const code = e instanceof LoginsError ? e.code : ''
    // the vault turned out other than the window thought: set up meanwhile, or never
    if (code === 'EEXIST') masterAsk.mode = 'unlock'
    if (code === 'ENOENT') masterAsk.mode = 'setup'
    masterAsk.error = code === 'EPASSWORD' ? 'That is not the master password.'
      : code === 'EEXIST' ? 'This vault has a master password already — give it to unlock.'
        : code === 'ENOENT' ? 'This vault has no master password yet — set one.'
          : e instanceof Error ? e.message : String(e)
  } finally {
    masterAsk.busy = false
  }
}

export function dropMasterAsk() {
  const done = masterAsk.resolve
  Object.assign(masterAsk, { mode: null, error: '', busy: false, resolve: null })
  done?.(false)
}

// A Sources site's logins: every origin of its domain (an http and an https
// login, a port of its own, are still the one site there).
export async function loginsOf(domain: string): Promise<LoginSummary[]> {
  const shell = window.longbox?.logins
  if (!shell || !loginsState.unlocked) return []
  return (await settled(shell.list())).filter((l) => hostOf(l.origin) === domain)
}

// Sources asked to open a site on its Logins tab (from the key's popover);
// Sources takes the request and clears it.
export const showLoginsIn = reactive({ domain: '' })

export async function lockLogins() {
  const shell = window.longbox?.logins
  if (shell) await settled(shell.lock())
}

// A rough strength for a new master password: what it is made of, and its
// length — at least MIN_MASTER characters, as the shell asks (secrets.js).
export const MIN_MASTER = 10
export function strength(pw: string): 0 | 1 | 2 | 3 | 4 {
  if (pw.length < MIN_MASTER) return 0
  const kinds = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^a-zA-Z0-9]/].filter((re) => re.test(pw)).length
  const score = (pw.length >= 14 ? 2 : 1) + (kinds >= 3 ? 2 : kinds === 2 ? 1 : 0) + (pw.length >= 20 ? 1 : 0)
  return Math.min(4, score) as 0 | 1 | 2 | 3 | 4
}

// "replaced 12 Oct" — the year too, when it is not this one
export function shortDate(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const sameYear = d.getFullYear() === new Date().getFullYear()
  return d.toLocaleDateString(undefined, sameYear ? { day: 'numeric', month: 'short' }
    : { day: 'numeric', month: 'short', year: 'numeric' })
}
