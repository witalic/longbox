'use strict'
// The browser's tabs that have been shown at least once — the window says which.
// What a page starts playing before its tab was ever shown is held back until
// it shows (pick-preload.js, which asks here once per document); what starts in
// a tab already seen is not, whatever document it is — the next track of a
// player, a player frame made anew — since what the owner leaves playing
// behind is theirs.
const { ipcMain, webContents } = require('electron')

function wire(win) {
  const seen = new WeakSet()
  ipcMain.on('lb-tab-shown', (e, guestId) => {
    if (e.sender !== win.webContents) return
    const guest = webContents.fromId(Number(guestId))
    if (guest && guest.getType() === 'webview' && guest.hostWebContents === win.webContents) seen.add(guest)
  })
  // asked synchronously by a page's preload: always answered
  ipcMain.on('lb-tab-seen', (e) => { e.returnValue = seen.has(e.sender) })
}

module.exports = { wire }
