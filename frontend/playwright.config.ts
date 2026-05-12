/**
 * frontend/playwright.config.ts
 *
 * E2E tests point at the live Render + Vercel deployments rather than
 * spinning up local services.  This eliminates the uvicorn startup race
 * that was causing timeouts in GitHub Actions CI (issue #1).
 *
 * PLAYWRIGHT_BASE_URL env var overrides the default so CI can pass the
 * live URL without hardcoding it here.  Locally, tests also hit the live
 * deployments — no local server required.
 */

import { defineConfig, devices } from '@playwright/test'

const BASE_URL =
  process.env.PLAYWRIGHT_BASE_URL ?? 'https://forest-capital.vercel.app'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['html', { open: 'never' }]],

  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
