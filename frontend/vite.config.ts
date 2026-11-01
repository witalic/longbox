import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// The built UI is served by the FastAPI sidecar under /app/ (single origin, no CORS).
export default defineConfig({
  base: '/app/',
  // `webview` is Electron's embedded-browser tag, not a Vue component.
  plugins: [vue({ template: { compilerOptions: { isCustomElement: (tag) => tag === 'webview' } } })],
  server: {
    port: 5178,
    // Dev: forward the API to the FastAPI sidecar. In production the sidecar
    // serves this built UI at /app/, so /api is same-origin and no proxy is used.
    proxy: { '/api': 'http://127.0.0.1:8787' },
  },
  // One bundle on purpose: it is read from the sidecar on this machine, not
  // downloaded, so Rollup's 500 kB warning (sized for the network) does not
  // apply — and lazy views would make each screen wait on its first opening.
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 1024 },
})
