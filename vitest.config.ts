import { defineConfig } from 'vitest/config';

export default defineConfig({
  // Vite 8 / Vitest 5 transform JSX with OXC; the widget uses Preact.
  oxc: { jsx: { runtime: 'automatic', importSource: 'preact' } },
  test: {
    include: ['packages/*/src/**/*.test.ts', 'apps/*/src/**/*.test.ts', 'apps/*/src/**/*.test.tsx'],
    environment: 'node',
    // Keep `?inline` CSS as a real string in tests (Vitest blanks CSS by default).
    css: { include: [/\.css(\?|$)/] },
  },
});
