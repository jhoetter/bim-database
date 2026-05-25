import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Dev: serve from :5173; proxy /houses, /ontology, /static to the FastAPI on :2500.
// Prod: built bundle in ui/dist/ is served directly by FastAPI at /.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/houses': 'http://127.0.0.1:2500',
      '/ontology': 'http://127.0.0.1:2500',
      '/static': 'http://127.0.0.1:2500',
    },
  },
});
