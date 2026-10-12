import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  test: {
    // Cards print dates in the reader's zone, so a run fixes one; set in the
    // worker's environment so it is in place before any formatter is built.
    env: { TZ: 'UTC' },
    environment: 'jsdom',
    // The browser checks need a real Chromium and built documents; they run
    // from vitest.browser.config.ts instead.
    exclude: ['**/node_modules/**', 'browser/**'],
    include: ['src/**/*.test.{ts,tsx}', 'scripts/**/*.test.ts'],
    setupFiles: ['test-support/rtl-setup.ts'],
  },
})
