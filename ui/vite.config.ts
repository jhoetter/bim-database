import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Ports are env-driven so `make dev` / `make dev-forwarded` can shift them
// (e.g. SSH-tunnel offset) without editing this file. Defaults match the
// local-dev port pair documented in AGENTS.md (API :2500, web :5173).
const apiPort = process.env.API_PORT ?? '2500';
const webPort = parseInt(process.env.WEB_PORT ?? '5173', 10);
const apiTarget = `http://127.0.0.1:${apiPort}`;

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: webPort,
    strictPort: true,
    proxy: {
      '/datasets': apiTarget,
      '/labels': apiTarget,
      '/pdfs': apiTarget,
      '/exports': apiTarget,
      '/static': apiTarget,
    },
  },
});
