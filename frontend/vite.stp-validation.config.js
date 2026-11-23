import base from './vite.config.js'
export default {
  ...base,
  server: { host: '127.0.0.1', port: 3017, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:5057', changeOrigin: true } } },
}
