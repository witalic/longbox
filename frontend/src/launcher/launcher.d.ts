// The launch window's bridge (shell/launcher-preload.js) — design/vault-home.md.
export {}

declare global {
  type LauncherScreen = 'first' | 'opening' | 'starting' | 'unreachable' | 'notvault' | 'create' | 'busy'
    | 'left' | 'moving' | 'movefailed'
  interface LauncherVault { path: string; name: string; share: string }
  interface LauncherOther { path: string; name: string; state: 'checking' | 'answers' | 'silent' | 'notvault' }
  // `here`: the lock is this computer's — another copy of longbox has the vault
  interface LauncherLock { computer?: string; since?: string; seen?: string; here?: boolean }
  // `state` of a place for a new vault: 'new' (made there) and 'empty' may be used
  interface LauncherCheck { path: string; state: 'new' | 'empty' | 'files' | 'vault' | 'noparent' | 'badname' | 'unreachable'; files: number }
  interface LauncherState {
    screen: LauncherScreen
    vault: LauncherVault | null
    waited: number
    others: LauncherOther[]
    lock: LauncherLock | null
    create: { parent: string; name: string; check: LauncherCheck | null } | null
    notvault: { path: string; empty: boolean } | null
    move: { parts: { what: string; done: number; total: number }[] } | null
    error: string
  }
  type LauncherAct = 'retry' | 'quit' | 'open' | 'choose' | 'anyway' | 'create' | 'create-parent' | 'create-name'
    | 'create-go' | 'back'
  interface Window {
    launcher?: {
      state: () => Promise<LauncherState | null>
      act: (act: LauncherAct, arg?: string) => Promise<LauncherState | null>
      onState: (fn: (s: LauncherState) => void) => () => void
    }
  }
}
