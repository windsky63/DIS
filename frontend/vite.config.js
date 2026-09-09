import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8768'
    }
  },
  build: {
    target: 'es2020',
    cssTarget: 'safari15',
    outDir: 'dist',
    emptyOutDir: true
  }
})
