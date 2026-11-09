import { createApp } from 'vue'
import '../styles.css'
import LauncherApp from './LauncherApp.vue'

// The theme the human chose lives in the vault this window has not opened yet,
// so it follows the system's.
const dark = window.matchMedia('(prefers-color-scheme: dark)')
const theme = () => { document.documentElement.dataset.theme = dark.matches ? 'dark' : 'light' }
theme()
dark.addEventListener('change', theme)

createApp(LauncherApp).mount('#root')
