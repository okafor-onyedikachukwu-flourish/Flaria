import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

// On Render, a service's own public address is known at build time.
process.env.VITE_SITE_URL ||= process.env.RENDER_EXTERNAL_URL;

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  // Dev proxy keeps API, refresh cookie and WebSocket same-origin with the app.
  const target = env.FLARIA_API_TARGET || 'http://127.0.0.1:8000';
  return {
    plugins: [react()],
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    server: {
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': { target, changeOrigin: false },
        '/health': { target, changeOrigin: false },
        '/ws': { target: target.replace(/^http/, 'ws'), ws: true, changeOrigin: false },
      },
    },
    build: {
      target: 'es2020',
      sourcemap: true,
      chunkSizeWarningLimit: 1200,
      rollupOptions: {
        output: {
          manualChunks: {
            react: ['react', 'react-dom', 'react-router-dom'],
            charts: ['chart.js', 'react-chartjs-2'],
            mediapipe: ['@mediapipe/tasks-vision'],
          },
        },
      },
    },
  };
});
