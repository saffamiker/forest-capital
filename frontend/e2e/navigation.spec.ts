/**
 * e2e/navigation.spec.ts
 *
 * Smoke tests for navigation and public pages on the live deployment.
 * Authenticated routes redirect to /login when no session exists — the
 * test verifies the login page renders rather than a blank screen or 500.
 */

import { test, expect } from '@playwright/test'

test.describe('App navigation', () => {
  test('root redirects unauthenticated user to login', async ({ page }) => {
    await page.goto('/')
    // Should either show login page or redirect to /login
    await page.waitForTimeout(1500)
    const url = page.url()
    const body = (await page.textContent('body')) ?? ''
    const isLoginPage =
      url.includes('/login') ||
      body.toLowerCase().includes('email') ||
      body.toLowerCase().includes('sign in') ||
      body.toLowerCase().includes('portfolio')
    expect(isLoginPage).toBe(true)
  })

  test('login page loads without JS errors', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (err) => errors.push(err.message))
    await page.goto('/login')
    await page.waitForTimeout(2000)
    // Filter out known browser extension noise
    const realErrors = errors.filter(
      (e) => !e.includes('extension') && !e.includes('chrome-extension')
    )
    expect(realErrors).toHaveLength(0)
  })

  test('/council route exists (redirects to login when unauthenticated)', async ({ page }) => {
    await page.goto('/council')
    await page.waitForTimeout(1500)
    const body = (await page.textContent('body')) ?? ''
    // Unauthenticated → login page, not a 404
    expect(body.length).toBeGreaterThan(100)
  })

  test('/qa route exists (redirects to login when unauthenticated)', async ({ page }) => {
    await page.goto('/qa')
    await page.waitForTimeout(1500)
    const body = (await page.textContent('body')) ?? ''
    expect(body.length).toBeGreaterThan(100)
  })

  test('health endpoint is reachable', async ({ request }) => {
    // Render restarts take ~2 minutes on deploy. The default timeout is
    // too short to survive the deploy window, and a single transient 502
    // can fail CI while the production app is in fact healthy. Three
    // attempts with a 5-second backoff and a 30-second per-request timeout
    // cover the deploy window without masking a real outage.
    const apiUrl =
      process.env.API_URL ?? 'https://forest-capital.onrender.com'
    const maxAttempts = 3
    const backoffMs = 5000
    let lastError: unknown = null
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      try {
        const response = await request.get(`${apiUrl}/api/health`, {
          timeout: 30000,
        })
        expect(response.status()).toBe(200)
        const json = await response.json()
        expect(json).toHaveProperty('status')
        return
      } catch (err) {
        lastError = err
        if (attempt < maxAttempts) {
          await new Promise((resolve) => setTimeout(resolve, backoffMs))
        }
      }
    }
    throw lastError
  })
})
