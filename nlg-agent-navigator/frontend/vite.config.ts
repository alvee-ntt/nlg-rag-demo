import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/prompt-api': {
        target: 'http://127.0.0.1:8001',
        rewrite: (path) => path.replace(/^\/prompt-api/, '/api'),
      },
    },
  },
})
