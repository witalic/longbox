'use strict'
// electron-builder afterPack hook: Electron's fuses, then — for macOS — the
// ad-hoc signature (adhoc-sign.js). The order is the point: flipping a fuse
// rewrites the Electron binary and breaks any signature already on it, and
// electron-builder's own fuse step (`electronFuses`) runs AFTER this hook — past
// the signing, so it is not used.
const path = require('node:path')
const { flipFuses, getCurrentFuseWire, FuseVersion, FuseV1Options } = require('@electron/fuses')
const adhocSign = require('./adhoc-sign')

// Off: the ways to run the released app as plain Node, hand its runtime options,
// or attach a debugger to it. Cookie encryption stays off ON PURPOSE: once on it
// can never be turned off again, and where sign-ins live is the user's choice
// (ROADMAP.md, 3.5.0).
const FUSES = {
  [FuseV1Options.RunAsNode]: false,
  [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
  [FuseV1Options.EnableNodeCliInspectArguments]: false,
  [FuseV1Options.EnableCookieEncryption]: false,
}
const DISABLED = '0'.charCodeAt(0) // what the binary's fuse wire holds for "off"

function binaryOf(context) {
  const { appOutDir, electronPlatformName, packager } = context
  if (electronPlatformName === 'darwin') {
    const name = packager.appInfo.productFilename
    return path.join(appOutDir, `${name}.app`, 'Contents', 'MacOS', name)
  }
  if (electronPlatformName === 'win32') return path.join(appOutDir, `${packager.appInfo.productFilename}.exe`)
  return path.join(appOutDir, packager.executableName)
}

exports.default = async function afterPack(context) {
  const binary = binaryOf(context)
  await flipFuses(binary, { version: FuseVersion.V1, ...FUSES })
  // read back: a build that ships with a fuse still on must fail here, not quietly
  const wire = await getCurrentFuseWire(binary)
  const left = Object.keys(FUSES).filter((k) => wire[k] !== DISABLED)
  if (left.length) {
    throw new Error(`fuses still on in ${binary}: ${left.map((k) => FuseV1Options[k]).join(', ')}`)
  }
  console.log(`fuses off (${Object.keys(FUSES).map((k) => FuseV1Options[k]).join(', ')}): ${binary}`)
  await adhocSign.default(context)
}
