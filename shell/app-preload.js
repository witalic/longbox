'use strict'
// Preload for the APP window only (not the <webview> guests). Exposes exactly
// one capability: asking the OS for a folder via the native dialog — the
// renderer never sees the filesystem, only the path the user chose.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('longbox', {
  pickFolder: (title) => ipcRenderer.invoke('lb-pick-folder', String(title || '')),
  // global embedded-browser hygiene: 'cookies' (site cookies; the app's own
  // auth cookie survives) or 'cache' (HTTP cache)
  clearBrowsing: (what) => ipcRenderer.invoke('lb-clear-browsing', String(what || '')),
  // recolor the native window-controls overlay when the theme flips
  setTitleBar: (opts) => ipcRenderer.send('lb-titlebar', {
    color: String(opts?.color || ''), symbolColor: String(opts?.symbolColor || ''),
  }),
  // a shortcut pressed INSIDE a guest page: the physical key, for the app to read
  onPageKey: (fn) => {
    const h = (_e, chord) => fn(chord)
    ipcRenderer.on('lb-page-key', h)
    return () => ipcRenderer.removeListener('lb-page-key', h)
  },
  // the window wants to close while transfers are running; returns unsubscribe
  onCloseBlocked: (fn) => {
    const h = (_e, n) => fn(Number(n) || 0)
    ipcRenderer.on('lb-close-blocked', h)
    return () => ipcRenderer.removeListener('lb-close-blocked', h)
  },
  // stop everything still streaming (keeping its place) and quit
  closeNow: () => ipcRenderer.invoke('lb-close-now'),
  // pick an interrupted transfer up from its stored offset
  resumeDownload: (rec) => ipcRenderer.invoke('lb-resume-download', rec),
  // stop a download that is streaming (the id the sidecar gave it)
  cancelDownload: (id) => ipcRenderer.invoke('lb-cancel-download', String(id || '')),
  // page capture: pull one image with the browser session's cookies + referer
  // (the main process is not bound by the site's CORS)
  fetchImage: (url, referer) => ipcRenderer.invoke('lb-fetch-image', {
    url: String(url || ''), referer: String(referer || ''),
  }),
})
