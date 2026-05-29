import { defineConfig } from 'vite';

// Customer submission frontend.
//
// Runs on :5174 by default to stay clear of the annotation SPA on :5173.
// The build emits a static bundle in form-ui/dist/ that can be hosted by
// any web server fronting the form_api FastAPI app (which lives on :2600).

export default defineConfig({
  server: {
    port: Number(process.env.FORM_UI_PORT ?? 5174),
    strictPort: true,
  },
  build: {
    outDir: 'dist',
    target: 'es2022',
    sourcemap: true,
  },
});
