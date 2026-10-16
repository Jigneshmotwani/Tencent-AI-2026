import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Ports come from the root .env written by the launcher (defaults match .env.example).
// A hosted demo through an ngrok tunnel: its hostnames are allowed, and an ngrok page origin is presented to the API as
// the local app origin (the API's Origin check accepts exactly one origin). Every other origin passes through unchanged.
const TUNNEL = /^https:\/\/[a-z0-9-]+\.ngrok-free\.(app|dev)$/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '..', 'LOCAL_')
  const port = Number(env.LOCAL_PORT_APP || 5173)
  return { plugins: [react()], server: { host: '127.0.0.1', port, strictPort: true, allowedHosts: ['.ngrok-free.app', '.ngrok-free.dev'],
    proxy: { '/api': { target: `http://127.0.0.1:${env.LOCAL_PORT_API || 8000}`, configure: proxy => {
      proxy.on('proxyReq', (req, incoming) => {
        if (/\.ngrok-free\.(app|dev)$/.test(String(incoming.headers.host || ''))) req.setHeader('host', `localhost:${port}`)
        if (TUNNEL.test(String(incoming.headers.origin || ''))) req.setHeader('origin', `http://localhost:${port}`)
      })
    } } } } }
})
