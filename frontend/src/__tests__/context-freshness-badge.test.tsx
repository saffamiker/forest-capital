/**
 * context-freshness-badge.test.tsx — Item 5 freshness badge.
 *
 * Verifies the three-layer freshness aggregation, the popover
 * detail, and the error/loading states.
 *
 * May 24 2026 — switched the mocks from global.fetch to axios.get
 * after the badge was migrated to axios so the session token
 * (X-API-Key on axios.defaults.headers.common) is attached. The
 * old fetch-based call was 401-ing on every request, breaking
 * the Citation Review "Open Review" button (Step 2b) entirely.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import axios from 'axios'

import ContextFreshnessBadge from
  '../components/ContextFreshnessBadge'


vi.mock('axios')


function nowISO() {
  return new Date().toISOString()
}
function hoursAgo(h: number) {
  return new Date(Date.now() - h * 3_600_000).toISOString()
}


function _mockAxiosGet(data: unknown) {
  ;(axios.get as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
    data,
    status: 200,
    statusText: 'OK',
    headers: {},
    config: { headers: {} as never },
  })
}

function _mockAxiosGetError(status: number) {
  // Mimic an axios error so the badge's error branch fires.
  const err = Object.assign(new Error(`HTTP ${status}`), {
    isAxiosError: true,
    response: { status, data: { detail: 'Unauthorised' } },
    config: {}, name: 'AxiosError',
  })
  ;(axios.isAxiosError as unknown as ReturnType<typeof vi.fn>) =
    vi.fn().mockReturnValue(true)
  ;(axios.get as unknown as ReturnType<typeof vi.fn>).mockRejectedValueOnce(err)
}


beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  // isAxiosError is referenced in the badge's catch block — make
  // sure it's available even when the mocked error path is unused.
  if (!(axios.isAxiosError as unknown as { mock?: unknown }).mock) {
    ;(axios.isAxiosError as unknown as ReturnType<typeof vi.fn>) =
      vi.fn().mockReturnValue(false)
  }
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})


describe('ContextFreshnessBadge — overall status', () => {
  it('shows fresh when all three layers are under 24h', async () => {
    _mockAxiosGet({
      macro_context:           hoursAgo(2),
      analytics_context:       hoursAgo(0.5),
      diversification_context: hoursAgo(3),
    })

    render(<ContextFreshnessBadge />)
    await waitFor(() => {
      expect(screen.getByTestId('context-freshness-badge')).toBeTruthy()
    })
    expect(screen.getByLabelText(/Context freshness: fresh/i)).toBeTruthy()
  })

  it('shows stale when at least one layer is 24h–7d old', async () => {
    _mockAxiosGet({
      macro_context:           hoursAgo(2),
      analytics_context:       hoursAgo(36),  // stale
      diversification_context: hoursAgo(3),
    })

    render(<ContextFreshnessBadge />)
    await waitFor(() => {
      expect(screen.getByLabelText(/Context freshness: stale/i)).toBeTruthy()
    })
  })

  it('shows missing when at least one layer is null', async () => {
    _mockAxiosGet({
      macro_context:           null,
      analytics_context:       hoursAgo(2),
      diversification_context: hoursAgo(3),
    })

    render(<ContextFreshnessBadge />)
    await waitFor(() => {
      expect(screen.getByLabelText(/Context freshness: missing/i)).toBeTruthy()
    })
  })

  it('shows error state on a failed request', async () => {
    _mockAxiosGetError(500)

    render(<ContextFreshnessBadge />)
    await waitFor(() => {
      expect(screen.getByTestId('context-freshness-error')).toBeTruthy()
    })
  })
})


describe('ContextFreshnessBadge — popover detail', () => {
  it('click expands a popover with per-layer rows', async () => {
    _mockAxiosGet({
      macro_context:           hoursAgo(5),
      analytics_context:       hoursAgo(0.1),
      diversification_context: null,
    })

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
    const row = screen.getByTestId(
      'context-freshness-row-diversification_context')
    expect(row.textContent).toMatch(/never/i)
  })

  it('click again collapses the popover', async () => {
    _mockAxiosGet({
      macro_context:           nowISO(),
      analytics_context:       nowISO(),
      diversification_context: nowISO(),
    })

    render(<ContextFreshnessBadge />)
    await waitFor(() => screen.getByTestId('context-freshness-badge'))
    fireEvent.click(screen.getByTestId('context-freshness-badge'))
    expect(screen.queryByTestId('context-freshness-popover')).toBeTruthy()
    fireEvent.click(screen.getByTestId('context-freshness-badge'))
    expect(screen.queryByTestId('context-freshness-popover')).toBeNull()
  })
})
