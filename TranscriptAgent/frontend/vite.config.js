import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// Sends a request on to Django. changeOrigin: false keeps the browser's Host (localhost:5173): Django's CSRF check
// refuses a POST whose Origin (localhost:5173) doesn't match the Host it sees. A plain string target would turn
// changeOrigin on and send Host: 127.0.0.1:8000, so every sign-in and chatbot question would be refused.
const toDjango = () => ({ target: 'http://127.0.0.1:8000', changeOrigin: false })

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // Everything Django serves goes through here, so the browser sees one site and Django's sign-in cookie works on
    // both. /static is the admin's CSS and scripts.
    proxy: {
      '/api': toDjango(),
      '/accounts': toDjango(),
      '/admin': toDjango(),
      '/chat': toDjango(),
      '/static': toDjango(),
    },
  },
})
