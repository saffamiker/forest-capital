/**
 * context-freshness-badge.test.tsx — Item 5 freshness badge.
 *
 * Verifies the three-layer freshness aggregation, the popover
 * detail, and the error/loading states.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

import ContextFreshnessBadge from
  '../components/ContextFreshnessBadge'


function nowISO() {
  return new Date().toISOString()
}
function hoursAgo(h: number) {
  return new Date(Date.now() - h * 3_600_000).toISOString()
}


let originalFetch: typeof global.fetch

beforeEach(() => {
  originalFetch = global.fetch
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  global.fetch = originalFetch
  vi.useRealTimers()
  vi.clearAllMocks()
})


describe('ContextFreshnessBadge — overall status', () => {
  it('shows fresh when all three layers are under 24h', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        macro_context:           hoursAgo(2),
        analytics_context:       hoursAgo(0.5),
        diversification_context: hoursAgo(3),
      }),
    } as Response) as unknown as typeof fetch

    render(<ContextFreshnessBadge />)
    await waitFor(() => {
      expect(screen.getByTestId('context-freshness-badge')).toBeTruthy()
    })
    expect(screen.getByLabelText(/Context freshness: fresh/i)).toBeTruthy()
  })

  it('shows stale when at least one layer is 24h–7d old', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        macro_context:           hoursAgo(2),
        analytics_context:       hoursAgo(36),  // stale
        diversification_context: hoursAgo(3),
      }),
    } as Response) as unknown as typeof fetch

    render(<ContextFreshnessBadge />)
    await waitFor(() => {
      expect(screen.getByLabelText(/Context freshness: stale/i)).toBeTruthy()
    })
  })

  it('shows missing when at least one layer is null', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        macro_context:           null,
        analytics_context:       hoursAgo(2),
        diversification_context: hoursAgo(3),
      }),
    } as Response) as unknown as typeof fetch

    render(<ContextFreshnessBadge />)
    await waitFor(() => {
      expect(screen.getByLabelText(/Context freshness: missing/i)).toBeTruthy()
    })
  })

  it('shows error state on a failed fetch', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: false, status: 500,
      json: async () => ({}),
    } as Response) as unknown as typeof fetch

    render(<ContextFreshnessBadge />)
    await waitFor(() => {
      expect(screen.getByTestId('context-freshness-error')).toBeTruthy()
    })
  })
})


describe('ContextFreshnessBadge — popover detail', () => {
  it('click expands a popover with per-layer rows', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        macro_context:           hoursAgo(5),
        analytics_context:       hoursAgo(0.1),
        diversification_context: null,
      }),
    } as Response) as unknown as typeof fetch

    render(<ContextFreshnessBadge />)
    await waitFor(() => screen.getByTestId('context-freshness-badge'))

    fireEvent.click(screen.getByTestId('context-freshness-badge'))
    expect(
      screen.getByTestId('context-freshness-row-macro_context'),
    ).toBeTruthy()
    expect(
      screen.getByTestId('context-freshness-row-analytics_context'),
    ).toBeTruthy()
    expect(
      screen.getByTestId('context-freshness-row-diversification_context'),
    ).toBeTruthy()
    // The missing layer reads "never".
    const row = screen.getByTestId(
      'context-freshness-row-diversification_context')
    expect(row.textContent).toMatch(/never/i)
  })

  it('click again collapses the popover', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        macro_context:           nowISO(),
        analytics_context:       nowISO(),
        diversification_context: nowISO(),
      }),
    } as Response) as unknown as typeof fetch

    render(<ContextFreshnessBadge />)
    await waitFor(() => screen.getByTestId('context-freshness-badge'))
    fireEvent.click(screen.getByTestId('context-freshness-badge'))
    expect(screen.queryByTestId('context-freshness-popover')).toBeTruthy()
    fireEvent.click(screen.getByTestId('context-freshness-badge'))
    expect(screen.queryByTestId('context-freshness-popover')).toBeNull()
  })
})
