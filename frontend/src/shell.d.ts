// The desktop shell bridge (shell/app-preload.js) — absent in a plain browser.
export {}

declare global {
  // `annoyances`: cookie banners and pop-ups hidden too; `paused`: the sites
  // blocking is paused on, named as data.ts `hostOf` names them; `updatedAt`: when
  // the lists in use were fetched, `listsAt`: when EasyList itself last changed
  // (ms, 0 when unknown)
  interface AdblockState {
    on: boolean
    annoyances: boolean
    ready: boolean
    updating: boolean
    updatedAt: number
    listsAt: number
    failed: string
    paused: string[]
  }
  // the vault stopped answering (design/vault-home.md); `share`: what a mapped
  // drive letter names, '' when it is none
  interface VaultEvent {
    state: 'lost' | 'ok'
    since: string
    path: string
    name: string
    share: string
  }
  // what a check for a newer longbox found (shell/updates.js)
  interface UpdateCheck {
    ok: boolean
    error?: string
    latest?: string
    newer?: boolean
    published?: string
    security?: boolean
  }
  // the vault's logins (shell/passwords.js): an answer, or what the owner is told
  type LoginsAnswer<T> = { ok: true; value: T } | { ok: false; code: string; message: string }
  // `restored`: the file did not read, and its second copy was opened
  interface LoginsState { exists: boolean; unlocked: boolean; restored: boolean }
  // a login as a list shows it — never its password; `previous`: the passwords it replaced, newest first
  interface LoginSummary {
    id: string
    origin: string
    username: string
    note: string
    created: string
    updated: string
    used: string
    previous: { replaced: string }[]
  }
  // after a page's sign-in: offer to save it ('locked': which, once unlocked), to update the saved one, or nothing
  interface LoginOffer { guestId: number; kind: 'save' | 'update' | 'locked' | null; origin?: string; username?: string }
  // an editor's Save, in the order it was made: a new password, a previous one used again, one forgotten
  type LoginStep = { password: string } | { use: number } | { forget: number }
  interface Window {
    longbox?: {
      pickFolder: (title?: string) => Promise<string | null>
      // a vault to add (mode 'open'), or an empty folder made one ('create'); registered when ok
      pickVault: (mode: 'open' | 'create') => Promise<{ ok: boolean; path?: string; reason?: string }>
      // a restart into another registered vault; false when it is not one
      switchVault: (root: string) => Promise<boolean>
      // a restart into the launch window
      openLauncher: () => Promise<boolean>
      quit: () => Promise<void>
      onVault: (fn: (v: VaultEvent) => void) => () => void
      // a folder in the system's file manager; false when it is not one
      openFolder: (dir: string) => Promise<boolean>
      clearBrowsing: (what: 'cookies' | 'cache') => Promise<boolean>
      // where this vault's sign-ins live (design/sign-ins.md): where the profile in
      // use is, the move this computer owes the vault (made the next time it opens
      // it), whether this build may change it, the other folder that may use the
      // profile too (a copy: then it is not moved); what the profile holds, in bytes;
      // asking for the move, restarting at once or not (false: not saved); and this
      // computer's sign-ins for a vault being forgotten (how many profiles went)
      signins: () => Promise<{ here: 'vault' | 'computer'; owe: 'vault' | 'computer' | null; writable: boolean;
        shared: string } | null>
      signinsSizes: () => Promise<{ data: number; cache: number } | null>
      setSignins: (keep: 'vault' | 'computer', restart: boolean) => Promise<boolean>
      forgetSignins: (root: string) => Promise<number>
      // ask GitHub once whether a newer longbox is out; a version's release page
      // ('latest': the download) in the system's browser — false for anything else
      checkUpdates: () => Promise<UpdateCheck>
      openRelease: (which: string) => Promise<boolean>
      logins: {
        state: () => Promise<LoginsAnswer<LoginsState>>
        setup: (master: string) => Promise<LoginsAnswer<void>>
        unlock: (master: string) => Promise<LoginsAnswer<void>>
        lock: () => Promise<LoginsAnswer<void>>
        // minutes with no use of the computer before they lock by themselves; 0: never for that
        lockWhenIdle: (minutes: number) => Promise<LoginsAnswer<void>>
        list: (origin?: string) => Promise<LoginsAnswer<LoginSummary[]>>
        reveal: (id: string, previous?: number) => Promise<LoginsAnswer<string>>
        copy: (id: string, previous?: number) => Promise<LoginsAnswer<void>>
        copyText: (text: string) => Promise<LoginsAnswer<void>>
        add: (login: { origin: string; username: string; password: string; note: string }) => Promise<LoginsAnswer<string>>
        edit: (id: string, change: { username?: string; note?: string; steps?: LoginStep[]; updated?: string }) =>
          Promise<LoginsAnswer<void>>
        remove: (id: string) => Promise<LoginsAnswer<void>>
        generate: (opts?: { length?: number; letters?: boolean; digits?: boolean; symbols?: boolean }) => Promise<LoginsAnswer<string>>
        fill: (guestId: number, id: string) => Promise<LoginsAnswer<boolean>>
        onOffer: (fn: (offer: LoginOffer) => void) => () => void
        // `shown`: what the bar said as it was answered; 'changed': it turned out to mean other, and is shown anew
        answerOffer: (guestId: number, what: 'save' | 'never' | 'dismiss', shown: { kind: LoginOffer['kind']; origin: string }) =>
          Promise<LoginsAnswer<'saved' | 'updated' | 'same' | 'never' | 'dropped' | 'gone' | 'changed'>>
        changeMaster: (current: string, next: string) => Promise<LoginsAnswer<void>>
        // the addresses never asked on: all, or those of an address's site
        nevers: (origin?: string) => Promise<LoginsAnswer<string[]>>
        setNever: (origin: string, never: boolean) => Promise<LoginsAnswer<void>>
        onState: (fn: (s: LoginsState) => void) => () => void
      }
      // one site's share of the embedded browser: its cookie count, and clearing
      // them — or its storage and cached pages too; null/false when refused
      siteData: (domain: string) => Promise<{ cookies: number } | null>
      clearSiteData: (domain: string, what: { cookies?: boolean; storage?: boolean; cache?: boolean }) => Promise<boolean>
      setTitleBar: (opts: { color: string; symbolColor: string }) => void
      // stop a download in flight; false when the shell no longer holds it
      cancelDownload: (id: string) => Promise<boolean>
      // the window is trying to close with transfers running
      onDownloadsChanged: (fn: () => void) => () => void
      onCloseBlocked: (fn: (running: number) => void) => () => void
      closeNow: () => Promise<boolean>
      // pick an interrupted transfer up from where it stopped, in its own record
      resumeDownload: (rec: unknown) => Promise<boolean>
      // stop one in flight with its place kept; false when the shell no longer holds it
      pauseDownload: (id: string) => Promise<boolean>
      // drop a forgotten transfer's partial file (only ever one in the shell's own folder)
      forgetPartial: (file: string) => Promise<boolean>
      // a shortcut pressed inside a guest page; returns an unsubscribe
      onPageKey: (fn: (k: { code: string; ctrl: boolean; shift: boolean; alt: boolean }) => void)
        => () => void
      // ad blocking in the browser's pages (shell/adblock.js)
      adblockState: () => Promise<AdblockState | null>
      setAdblock: (change: { on?: boolean; annoyances?: boolean; site?: string; paused?: boolean })
        => Promise<AdblockState | null>
      updateAdblock: () => Promise<AdblockState | null>
      onAdblock: (fn: (s: AdblockState) => void) => () => void
      // requests blocked on a guest's page so far (by the guest's webContents id)
      onAdblockBlocked: (fn: (guestId: number, count: number) => void) => () => void
      // a guest page asks for fullscreen; it is granted once `fn` has made room
      // for the <webview> (by the guest's webContents id); returns an unsubscribe
      onPageFullscreen: (fn: (guestId: number) => Promise<void>) => () => void
      // a page image or a cover found by URL: the main process fetches it as the
      // page's own <img> would (session cookies, the page's Referer), free of the
      // site's CORS (which blocks an in-page fetch of its own CDN)
      fetchImage: (url: string, referer?: string)
        => Promise<{ data?: string; contentType?: string; error?: string } | null>
    }
  }
}
