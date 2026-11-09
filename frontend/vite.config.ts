import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Two pages. The app is served by the FastAPI sidecar under /app/ (single origin,
// no CORS); the launch window (launcher.html) is loaded from disk by the shell
// before any sidecar exists (design/vault-home.md). So every URL in the build is
// relative — it resolves under /app/ and beside a file alike.
export default defineConfig({
  base: './',
  // `webview` is Electron's embedded-browser tag, not a Vue component.
  plugins: [vue({ template: { compilerOptions: { isCustomElement: (tag) => tag === 'webview' } } })],
  server: {
    port: 5178,
    // Dev: forward the API to the FastAPI sidecar. In production the sidecar
    // serves this built UI at /app/, so /api is same-origin and no proxy is used.
    proxy: { '/api': 'http://127.0.0.1:8787' },
  },
  // Read from this machine, not downloaded: Rollup's 500 kB warning (sized for
  // the network) does not apply, and no view is split off to wait on its first
  // opening — the app arrives as one chunk once its preferences are read.
  build: {
    outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 1024,
    rollupOptions: { input: { main: 'index.html', launcher: 'launcher.html' } },
  },
})
