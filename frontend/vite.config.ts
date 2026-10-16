import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backend = 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': backend,
      // Signed Apply links from the digest email are handled by the backend.
      '/apply': backend,
      '/health': backend,
      '/ready': backend,
    },
  },
})
