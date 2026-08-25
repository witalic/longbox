// The desktop shell bridge (shell/app-preload.js) — absent in a plain browser.
export {}

declare global {
  interface Window {
    longbox?: {
      pickFolder: (title?: string) => Promise<string | null>
      clearBrowsing: (what: 'cookies' | 'cache') => Promise<boolean>
      setTitleBar: (opts: { color: string; symbolColor: string }) => void
      // stop a download in flight; false when the shell no longer holds it
      cancelDownload: (id: string) => Promise<boolean>
      // the window is trying to close with transfers running
      onCloseBlocked: (fn: (running: number) => void) => () => void
      closeNow: () => Promise<boolean>
      // pick an interrupted transfer up from where it stopped
      resumeDownload: (rec: unknown) => Promise<boolean>
      // a shortcut pressed inside a guest page; returns an unsubscribe
      onPageKey: (fn: (k: { code: string; ctrl: boolean; shift: boolean; alt: boolean }) => void)
        => () => void
      // page capture: the main process fetches with session cookies + referer,
      // free of the site's CORS (which blocks an in-page fetch of its own CDN)
      fetchImage: (url: string, referer?: string)
        => Promise<{ data?: string; contentType?: string; error?: string } | null>
    }
  }
}
