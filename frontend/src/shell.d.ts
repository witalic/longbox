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
