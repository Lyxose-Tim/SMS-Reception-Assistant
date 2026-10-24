import preact from '@preact/preset-vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [preact()],
  // Library mode does not replace process.env.NODE_ENV; dependencies may reference it.
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    lib: { entry: 'src/main.tsx', formats: ['iife'], name: 'ReceptionWidget', fileName: () => 'widget.js' },
    cssCodeSplit: false,
    minify: true,
    sourcemap: false,
    emptyOutDir: true,
  },
});
