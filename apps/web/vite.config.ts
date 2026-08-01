import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import {defineConfig} from 'vite';

// The API binds 127.0.0.1 (IPv4 only) while `localhost` resolves to ::1 first on macOS, so
// the browser must not address it directly. Proxying keeps every request same-origin: no
// CORS, no address-family mismatch, and a boot-order miss returns 502 instead of a
// connection refusal.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://127.0.0.1:8001';

// Order matters: Vite applies proxy entries in insertion order, so the more specific
// CopilotKit runtime (a separate Express process on 4000) has to win over `/api`.
const proxy = {
  '/api/copilotkit': {
    target: process.env.COPILOTKIT_URL ?? 'http://localhost:4000',
    changeOrigin: true,
  },
  '/api': {target: apiTarget, changeOrigin: true},
  // Study previews and segmentation overlays served from the repo-root `data/` mount.
  '/data': {target: apiTarget, changeOrigin: true},
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
      // Cornerstone3D's dependency chain extends Node's EventEmitter. Vite externalizes
      // `events` for the browser, leaving the base class undefined ("Class extends value
      // undefined"), so point it at the browser polyfill instead.
      events: 'events',
    },
  },
  optimizeDeps: {
    // The DICOM loader spawns its decode worker via `new Worker(new URL('./decodeImageFrameWorker.js',
    // import.meta.url))`. Pre-bundling rewrites the module into .vite/deps/, where that relative URL
    // no longer resolves and every frame fails to decode — so serve this package from source.
    exclude: ['@cornerstonejs/dicom-image-loader'],
    // …but its deps still need pre-bundling for ESM interop. The codecs are UMD emscripten
    // builds reached through subpath exports, so each exact subpath must be listed — a bare
    // package name does not cover them, and without interop they have no default export.
    include: [
      'dicom-parser',
      'events',
      '@cornerstonejs/codec-charls/decodewasmjs',
      '@cornerstonejs/codec-libjpeg-turbo-8bit/decodewasmjs',
      '@cornerstonejs/codec-openjpeg/decodewasmjs',
      '@cornerstonejs/codec-openjph/wasmjs',
    ],
  },
  worker: {format: 'es'},
  server: {port: 3000, proxy},
  preview: {proxy},
});
