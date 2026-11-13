'use strict'
// Give macOS builds an AD-HOC signature — called by the afterPack hook
// (after-pack.js) once the fuses are flipped, since flipping breaks a signature.
//
// There is no Apple Developer certificate here, and that is a deliberate
// choice — but shipping an UNSIGNED bundle is not the same thing. On Apple
// Silicon an unsigned binary cannot run at all, and a downloaded one is
// reported as "damaged", which reads as a broken app rather than an unknown
// developer. An ad-hoc signature (`codesign --sign -`) makes the app runnable
// and turns that into the honest Gatekeeper warning: an unidentified developer,
// which the user accepts once — on macOS 15 and later through System Settings →
// Privacy & Security → Open Anyway (right-click → Open no longer offers it).
//
// The frozen sidecar and its shared libraries are signed FIRST: a bundle is
// only as valid as the nested code it carries.
const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

function sign(target, extra = []) {
  execFileSync('codesign', ['--force', '--timestamp=none', '--sign', '-', ...extra, target],
    { stdio: 'inherit' })
}

function machOFilesIn(dir) {
  const out = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name)
    if (entry.isDirectory()) out.push(...machOFilesIn(p))
    else if (/\.(so|dylib)$/.test(entry.name) || (entry.isFile() && isExecutable(p))) out.push(p)
  }
  return out
}

function isExecutable(p) {
  try {
    return (fs.statSync(p).mode & 0o111) !== 0
  } catch {
    return false
  }
}

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return
  const appPath = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)
  const sidecar = path.join(appPath, 'Contents', 'Resources', 'sidecar')
  if (fs.existsSync(sidecar)) {
    for (const file of machOFilesIn(sidecar)) sign(file)
  }
  sign(appPath, ['--deep'])
  execFileSync('codesign', ['--verify', '--verbose=2', appPath], { stdio: 'inherit' })
  console.log(`ad-hoc signed ${appPath}`)
}
