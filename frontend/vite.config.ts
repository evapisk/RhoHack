import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// /api/* is proxied to the Python backend so the browser talks to one origin.
// SSE (/api/stream) streams through the proxy without buffering.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
