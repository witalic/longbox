'use strict'
// Preload for the LAUNCH window only (design/vault-home.md): what the shell knows
// about the vault it is trying to open, and what the human can do about it.
// Folders are chosen through the native dialog; the page never sees the disk.
const { contextBridge, ipcRenderer } = require('electron')

const ACTS = new Set(['retry', 'quit', 'open', 'choose', 'anyway', 'create', 'create-parent', 'create-name',
  'create-go', 'back'])

contextBridge.exposeInMainWorld('launcher', {
  state: () => ipcRenderer.invoke('lb-launcher-get'),
  act: (act, arg) => (ACTS.has(act) ? ipcRenderer.invoke('lb-launcher-act', act, typeof arg === 'string' ? arg : '') : null),
  onState: (fn) => {
    const h = (_e, s) => fn(s)
    ipcRenderer.on('lb-launcher-state', h)
    return () => ipcRenderer.removeListener('lb-launcher-state', h)
  },
})
