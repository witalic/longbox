import { createApp } from 'vue'
import './styles.css'
import { logError } from './api'
import { loadPrefs } from './local'

// whatever throws in the window, from its first module on, reaches the app's log
const stackOf = (e: unknown) => (e instanceof Error ? e.stack || e.message : String(e))
window.addEventListener('error', (e) => logError(`${e.message} (${e.filename}:${e.lineno})\n${stackOf(e.error)}`))
window.addEventListener('unhandledrejection', (e) => logError(`unhandled: ${stackOf(e.reason)}`))

// The window's preferences live in the vault (local.ts) and are read before any
// view is: modules read them as they load, so the app is loaded only after —
// nothing draws with a default it then has to change.
void loadPrefs().then(async () => {
  const { default: App } = await import('./App.vue')
  const app = createApp(App)
  app.config.errorHandler = (err, _vm, info) => {
    console.error(err)
    logError(`${info}: ${stackOf(err)}`)
  }
  app.mount('#app')
})
