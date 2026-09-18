import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig({
  plugins: [vue(), vuetify({ autoImport: true })],
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
