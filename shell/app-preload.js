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
  // page capture: pull one image with the browser session's cookies + referer
  // (the main process is not bound by the site's CORS)
  fetchImage: (url, referer) => ipcRenderer.invoke('lb-fetch-image', {
    url: String(url || ''), referer: String(referer || ''),
  }),
})
