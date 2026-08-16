// The desktop shell bridge (shell/app-preload.js) — absent in a plain browser.
export {}

declare global {
  interface Window {
    longbox?: {
      pickFolder: (title?: string) => Promise<string | null>
      clearBrowsing: (what: 'cookies' | 'cache') => Promise<boolean>
      setTitleBar: (opts: { color: string; symbolColor: string }) => void
      // page capture: the main process fetches with session cookies + referer,
      // free of the site's CORS (which blocks an in-page fetch of its own CDN)
      fetchImage: (url: string, referer?: string)
        => Promise<{ data?: string; contentType?: string; error?: string } | null>
    }
  }
}
