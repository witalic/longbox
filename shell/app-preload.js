'use strict'
// Preload for the APP window only (not the <webview> guests). Each capability
// is one narrow request the main process answers only for this window — the
// renderer never sees the filesystem, only the paths the user chose.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('longbox', {
  pickFolder: (title) => ipcRenderer.invoke('lb-pick-folder', String(title || '')),
  // the vaults (design/vault-home.md): pick one to add, or an empty folder to
  // make one in ('open' | 'create'); open another (a restart into it); go to
  // the launch window; quit
  pickVault: (mode) => ipcRenderer.invoke('lb-vault-pick', mode === 'create' ? 'create' : 'open'),
  switchVault: (root) => ipcRenderer.invoke('lb-vault-switch', String(root || '')),
  openLauncher: () => ipcRenderer.invoke('lb-vault-launcher'),
  quit: () => ipcRenderer.invoke('lb-quit'),
  // the vault stopped answering ({ state: 'lost', since, path, name, share }); returns unsubscribe
  onVault: (fn) => {
    const h = (_e, v) => fn(v)
    ipcRenderer.on('lb-vault', h)
    return () => ipcRenderer.removeListener('lb-vault', h)
  },
  // a folder (never a file) opened in the system's file manager
  openFolder: (dir) => ipcRenderer.invoke('lb-open-folder', String(dir || '')),
  // global embedded-browser hygiene: 'cookies' (site cookies; the app's own
  // auth cookie survives) or 'cache' (HTTP cache)
  clearBrowsing: (what) => ipcRenderer.invoke('lb-clear-browsing', String(what || '')),
  // where this vault's sign-ins live ({ here: 'vault' | 'computer', owe, writable,
  // shared } — where the profile in use is, the move this computer owes, whether
  // this build may change it, the other folder that may use it too) and what the
  // profile holds ({ data, cache } in bytes);
  // changing it (restart: at once, else on the next start); and this computer's
  // sign-ins for a vault being forgotten (how many profiles went)
  signins: () => ipcRenderer.invoke('lb-signins'),
  signinsSizes: () => ipcRenderer.invoke('lb-signins-sizes'),
  setSignins: (keep, restart) => ipcRenderer.invoke('lb-signins-set', { keep: String(keep || ''), restart: !!restart }),
  forgetSignins: (root) => ipcRenderer.invoke('lb-signins-forget', String(root || '')),
  // the vault's logins (design/password-manager.md): every answer is { ok, value }
  // or { ok: false, code, message }. A password reaches the window only when the
  // owner reveals it; copying and filling happen in the shell
  logins: {
    state: () => ipcRenderer.invoke('lb-pw-state'),
    setup: (master) => ipcRenderer.invoke('lb-pw-setup', String(master ?? '')),
    unlock: (master) => ipcRenderer.invoke('lb-pw-unlock', String(master ?? '')),
    lock: () => ipcRenderer.invoke('lb-pw-lock'),
    list: (origin) => ipcRenderer.invoke('lb-pw-list', String(origin ?? '')),
    reveal: (id, previous) => ipcRenderer.invoke('lb-pw-reveal', String(id ?? ''), previous),
    copy: (id, previous) => ipcRenderer.invoke('lb-pw-copy', String(id ?? ''), previous),
    copyText: (text) => ipcRenderer.invoke('lb-pw-copy-text', String(text ?? '')),
    add: (login) => ipcRenderer.invoke('lb-pw-add', login),
    edit: (id, change) => ipcRenderer.invoke('lb-pw-edit', String(id ?? ''), change),
    remove: (id) => ipcRenderer.invoke('lb-pw-remove', String(id ?? '')),
    generate: (opts) => ipcRenderer.invoke('lb-pw-generate', opts),
    fill: (guestId, id) => ipcRenderer.invoke('lb-pw-fill', Number(guestId), String(id ?? '')),
    // after a page's sign-in, what to offer ({ guestId, kind, origin, username };
    // kind null: nothing any more) — and the owner's answer
    onOffer: (fn) => {
      const h = (_e, o) => fn(o)
      ipcRenderer.on('lb-pw-offer', h)
      return () => ipcRenderer.removeListener('lb-pw-offer', h)
    },
    answerOffer: (guestId, what) => ipcRenderer.invoke('lb-pw-offer-answer', Number(guestId), String(what ?? '')),
    onState: (fn) => {
      const h = (_e, s) => fn(s)
      ipcRenderer.on('lb-pw-state', h)
      return () => ipcRenderer.removeListener('lb-pw-state', h)
    },
  },
  // a release of longbox newer than this one? ({ ok, latest, newer, published,
  // security } or { ok: false, error }) — and a version's release page
  // ('latest': the download), opened in the system's browser
  checkUpdates: () => ipcRenderer.invoke('lb-updates-check'),
  openRelease: (which) => ipcRenderer.invoke('lb-updates-open', String(which || '')),
  // one site's share of the embedded browser (Sources): how many cookies it
  // holds ({ cookies }), and clearing them — or its storage and cached pages too
  siteData: (domain) => ipcRenderer.invoke('lb-site-data', String(domain || '')),
  clearSiteData: (domain, what) => ipcRenderer.invoke('lb-site-data-clear', {
    domain: String(domain || ''), cookies: !!what?.cookies, storage: !!what?.storage, cache: !!what?.cache,
  }),
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
  // a guest page asks for fullscreen: `fn` makes room for it (the guest's
  // webContents id), and the request is granted once it has; returns unsubscribe
  onPageFullscreen: (fn) => {
    const h = async (_e, req) => {
      try { await fn(Number(req?.guestId) || 0) } finally {
        ipcRenderer.send('lb-page-fullscreen-ready', String(req?.token || ''))
      }
    }
    ipcRenderer.on('lb-page-fullscreen', h)
    return () => ipcRenderer.removeListener('lb-page-fullscreen', h)
  },
  // ad blocking in the browser's pages: its state, switched everywhere ({ on }),
  // for cookie banners and pop-ups too ({ annoyances }) or paused on one site
  // ({ site, paused }), and the lists fetched again now
  adblockState: () => ipcRenderer.invoke('lb-adblock-state'),
  setAdblock: (change) => ipcRenderer.invoke('lb-adblock-set', {
    ...(typeof change?.on === 'boolean' ? { on: change.on } : {}),
    ...(typeof change?.annoyances === 'boolean' ? { annoyances: change.annoyances } : {}),
    ...(typeof change?.site === 'string' ? { site: change.site, paused: !!change.paused } : {}),
  }),
  updateAdblock: () => ipcRenderer.invoke('lb-adblock-update'),
  // the state changed (a switch, a pause, lists fetched); returns unsubscribe
  onAdblock: (fn) => {
    const h = (_e, s) => fn(s)
    ipcRenderer.on('lb-adblock-changed', h)
    return () => ipcRenderer.removeListener('lb-adblock-changed', h)
  },
  // how many requests were blocked on a guest's page so far; returns unsubscribe
  onAdblockBlocked: (fn) => {
    const h = (_e, b) => fn(Number(b?.guestId) || 0, Number(b?.count) || 0)
    ipcRenderer.on('lb-adblock-blocked', h)
    return () => ipcRenderer.removeListener('lb-adblock-blocked', h)
  },
  // a download began or ended — armed or not; returns unsubscribe
  onDownloadsChanged: (fn) => {
    const h = () => fn()
    ipcRenderer.on('lb-downloads-changed', h)
    return () => ipcRenderer.removeListener('lb-downloads-changed', h)
  },
  // the window wants to close while transfers are running; returns unsubscribe
  onCloseBlocked: (fn) => {
    const h = (_e, n) => fn(Number(n) || 0)
    ipcRenderer.on('lb-close-blocked', h)
    return () => ipcRenderer.removeListener('lb-close-blocked', h)
  },
  // stop everything still streaming (keeping its place) and quit
  closeNow: () => ipcRenderer.invoke('lb-close-now'),
  // pick an interrupted transfer up from its stored offset, in its own record
  resumeDownload: (rec) => ipcRenderer.invoke('lb-resume-download', rec),
  // stop a download that is streaming (the id the sidecar gave it)
  cancelDownload: (id) => ipcRenderer.invoke('lb-cancel-download', String(id || '')),
  // stop one with its place kept, to be picked up later
  pauseDownload: (id) => ipcRenderer.invoke('lb-pause-download', String(id || '')),
  // drop the partial file of a paused transfer that is forgotten
  forgetPartial: (file) => ipcRenderer.invoke('lb-forget-partial', String(file || '')),
  // page capture: pull one image with the browser session's cookies + referer
  // (the main process is not bound by the site's CORS)
  fetchImage: (url, referer) => ipcRenderer.invoke('lb-fetch-image', {
    url: String(url || ''), referer: String(referer || ''),
  }),
})
