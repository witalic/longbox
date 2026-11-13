// The launch window's bridge (shell/launcher-preload.js) — design/vault-home.md.
export {}

declare global {
  type LauncherScreen = 'first' | 'opening' | 'starting' | 'unreachable' | 'notvault' | 'create' | 'busy'
    | 'left' | 'moving' | 'movefailed' | 'signins'
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
    // `kind`: what is moving — this computer's data into the vault (3.0), or a
    // vault's sign-ins toward 'computer' or 'vault'; what failed, on
    // 'movefailed': 'drop' (an old profile's removal), 'save' (where they live),
    // 'look' (finding them). `skip`: the way past a failed sign-ins step —
    // 'keep' lets the change go, 'later' leaves what is left for the next start
    move: { kind: 'legacy' | 'computer' | 'vault' | 'drop' | 'save' | 'look'; name?: string; skip?: 'keep' | 'later';
      parts: { what: string; done: number; total: number }[] } | null
    // the vault being asked where its sign-ins live; `sizes`: undefined while
    // measured, null when it has no profile yet
    signins: { name: string; path: string; sizes?: { data: number; cache: number } | null } | null
    // a line the starting screen says (a leftover profile that was removed, a
    // copied vault given sign-ins of its own)
    notice: string
    error: string
  }
  type LauncherAct = 'retry' | 'quit' | 'open' | 'choose' | 'anyway' | 'create' | 'create-parent' | 'create-name'
    | 'create-go' | 'back' | 'signins-vault' | 'signins-computer' | 'skip'
  interface Window {
    launcher?: {
      state: () => Promise<LauncherState | null>
      act: (act: LauncherAct, arg?: string) => Promise<LauncherState | null>
      onState: (fn: (s: LauncherState) => void) => () => void
    }
  }
}
