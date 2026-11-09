import { createApp } from 'vue'
import './styles.css'
import { loadPrefs } from './local'

// The window's preferences live in the vault (local.ts) and are read before any
// view is: modules read them as they load, so the app is loaded only after —
// nothing draws with a default it then has to change.
void loadPrefs().then(async () => {
  const { default: App } = await import('./App.vue')
  createApp(App).mount('#app')
})
