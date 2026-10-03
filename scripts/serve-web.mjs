// Operational Vite launcher; normal proxy remains the existing 8000 endpoint.
import { createServer } from '../apps/web/node_modules/vite/dist/node/index.js';
import vue from '../apps/web/node_modules/@vitejs/plugin-vue/dist/index.mjs';
import { fileURLToPath } from 'node:url';
const demo = process.argv.includes('--demo');
const server = await createServer({
  root: fileURLToPath(new URL('../apps/web', import.meta.url)),
  configFile: false,
  plugins: [vue()],
  server: { host: '127.0.0.1', port: demo ? 5174 : 5173, strictPort: true,
    proxy: { '/api': { target: `http://127.0.0.1:${demo ? 8013 : 8000}`, changeOrigin: true } } },
});
await server.listen();
server.printUrls();
