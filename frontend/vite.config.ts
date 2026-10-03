import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The dev proxy is what makes the `/api` base URL work in development.
//
// Two facts compose here. `backend/config.py` mounts every router under
// `API_V1_PREFIX` = `/api/v1`, and `VITE_API_BASE_URL` defaults to `/api`, so
// a feature path of `/v1/accounts` resolves to `/api/v1/accounts`. And
// `backend/main.py` installs NO CORS middleware at all, so a cross-origin
// request from :5173 to :8000 would be blocked by the browser no matter what
// the server answered. Proxying keeps the browser same-origin and removes CORS
// from the picture entirely, and it is also what the compose stack already does
// in nginx — so dev and production take the same path.
const API_TARGET = process.env.LIFEOS_DEV_API ?? 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    // ABSOLUTE, and identical to the alias in vitest.config.ts and the `paths`
    // in tsconfig.app.json. A bare relative `'src'` resolves against the
    // importing module's own directory rather than the project root, so
    // `vite dev` failed to resolve `@/features/...` while `vite build` — which
    // rewrites the specifier during transform — happened not to. Three files,
    // one alias.
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
      },
    },
  },
})
