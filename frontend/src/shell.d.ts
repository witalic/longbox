// The desktop shell bridge (shell/app-preload.js) — absent in a plain browser.
export {}

declare global {
  interface Window {
    longbox?: {
      pickFolder: (title?: string) => Promise<string | null>
      clearBrowsing: (what: 'cookies' | 'cache') => Promise<boolean>
      setTitleBar: (opts: { color: string; symbolColor: string }) => void
    }
  }
}
