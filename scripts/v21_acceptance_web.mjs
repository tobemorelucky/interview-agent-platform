// Dedicated Vue listener; real HTTP API, no interception or fixture response routes.
import { createServer } from '../apps/web/node_modules/vite/dist/node/index.js'
import { fileURLToPath } from 'node:url'
const root = fileURLToPath(new URL('../apps/web', import.meta.url))
const server = await createServer({ root, configFile: `${root}/vite.config.ts`,
  server: { host: '127.0.0.1', port: 5177, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8007', changeOrigin: true } } } })
await server.listen()
server.printUrls()
