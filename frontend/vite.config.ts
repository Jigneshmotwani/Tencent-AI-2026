import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Ports come from the root .env written by the launcher (defaults match .env.example).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', 'LOCAL_')
  return { plugins: [react()], server: { host: '127.0.0.1', port: Number(env.LOCAL_PORT_APP || 5173), strictPort: true, proxy: { '/api': `http://127.0.0.1:${env.LOCAL_PORT_API || 8000}` } } }
})
