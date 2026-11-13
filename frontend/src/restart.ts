// A restart of the app — into another vault (Settings → Storage), or to move
// the sign-ins (Settings → Browser) — cuts off what runs in this one. It is
// refused while a pass writes into the vault, which would stop mid-way; what it
// stops is said in the question that asks for it; and the window's last
// preferences reach the vault before it goes.
import { askConfirm } from './confirm'
import { flushPrefs } from './local'
import { runningDownloads, store, vault, vaultName, vaultPass } from './store'

// why the app may not restart now, or ''
export function restartRefused(): string {
  return vaultPass.running ? `${vaultPass.op || 'a vault pass'} is running — let it finish first` : ''
}

// What a restart stops, as a sentence or two for its question — `resumed`: when
// the downloads it stops are picked up — and whether a page being edited goes
// with it.
export function restartStops(resumed: string): { text: string; danger: boolean } {
  const held = store.tabs.find((t) => t.hold)
  const moving = runningDownloads().length
  return {
    text: (moving ? ` ${moving} download${moving === 1 ? '' : 's'} in progress stop, to be picked up ${resumed}.` : '')
      + (held ? ` ${held.hold}` : ''),
    danger: !!held,
  }
}

// the restart itself, once it was asked for
export async function restart(go: () => Promise<boolean>): Promise<boolean> {
  await flushPrefs()
  return go()
}

// Opening another vault. Its sign-ins are its own: in it, or on this computer,
// as it chose (design/sign-ins.md).
export async function switchVault(path: string): Promise<boolean> {
  if (!window.longbox) return false
  const refused = restartRefused()
  if (refused) {
    store.error = refused
    return false
  }
  const name = vaultName(path)
  const here = vaultName(vault.path || store.libraryPath)
  const stops = restartStops(`when ${here} is open again`)
  const ok = await askConfirm({
    title: `Switch to ${name}?`, okLabel: `Restart into ${name}`, danger: stops.danger,
    message: `longbox restarts into ${path}. Its titles, recipes, settings and the browser's sign-ins are its own; `
      + `${here}'s stay with ${here}.${stops.text}`,
  })
  if (!ok) return false
  return restart(() => window.longbox!.switchVault(path))
}
