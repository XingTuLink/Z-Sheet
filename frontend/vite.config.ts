import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// During `pnpm dev`, API calls are proxied to the FastAPI backend on :8000.
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
