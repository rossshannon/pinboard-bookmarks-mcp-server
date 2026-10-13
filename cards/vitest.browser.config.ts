import { defineConfig } from 'vitest/config'

/**
 * The `browser/*.browser.test.ts` suites: the built documents, served exactly
 * as the Python server serves them, driven through the SDK's own `AppBridge`
 * in a real Chromium. Separate because they need a browser and a fresh build
 * (`npm run build` first), neither of which the unit gate has.
 */
export default defineConfig({
  test: {
    env: { TZ: 'UTC' },
    environment: 'node',
    hookTimeout: 120_000,
    include: ['browser/**/*.browser.test.ts'],
    testTimeout: 60_000,
  },
})
