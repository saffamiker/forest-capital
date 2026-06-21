/**
 * key-metrics-panel.test.tsx -- June 21 2026.
 *
 * Pins the contract on the cache-verified Key Metrics panel:
 *   - Collapsed by default (no fetch on mount)
 *   - Expanding fires GET /api/v1/strategy-cache/key-metrics once
 *   - Displays the data_hash + the "Cache verified" pill
 *   - Renders each metric row's label + value + source
 *   - Surface graceful fallback when the cache is cold
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import axios from 'axios'
import KeyMetricsPanel from '../components/KeyMetricsPanel'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

const FAKE_PAYLOAD = {
  data_hash: 'c421fb89',
  available: true,
  computed_at: '2026-06-21T10:00:00Z',
  metrics: {
    strategy_performance: [
      { label: 'Benchmark Sharpe', value: '0.54',
        source: 'strategy_cache.BENCHMARK.sharpe_ratio' },
    ],
    oos_metrics: [
      { label: 'OOS window',
        value: 'January 2022 through May 2026 (53 months)',
        source: 'academic_deck.OOS window constant' },
      { label: 'Blend OOS Sharpe', value: '0.86',
        source: 'academic_deck.OOS_SHARPE_REGIME_CONDITIONAL' },
    ],
  },
}

describe('KeyMetricsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockedAxios.isAxiosError = vi.fn().mockReturnValue(false) as never
    mockedAxios.get = vi.fn().mockResolvedValue({ data: FAKE_PAYLOAD })
  })

  it('renders collapsed and does not fetch on mount', () => {
    render(<KeyMetricsPanel />)
    expect(screen.getByText('Key Metrics — Cache Verified'))
      .toBeInTheDocument()
    // No fetch yet -- the panel only loads when the user expands it.
    expect(mockedAxios.get).not.toHaveBeenCalled()
    // The expand affordance is present.
    expect(screen.getByTestId('key-metrics-toggle'))
      .toBeInTheDocument()
  })

  it('fetches /api/v1/strategy-cache/key-metrics on first expand',
    async () => {
      render(<KeyMetricsPanel />)
      fireEvent.click(screen.getByTestId('key-metrics-toggle'))
      await waitFor(() => {
        expect(mockedAxios.get).toHaveBeenCalledWith(
          '/api/v1/strategy-cache/key-metrics')
      })
    })

  it('does not refetch on a second expand-collapse-expand', async () => {
    render(<KeyMetricsPanel />)
    const toggle = screen.getByTestId('key-metrics-toggle')
    fireEvent.click(toggle)
    await waitFor(() => {
      expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    })
    fireEvent.click(toggle)  // collapse
    fireEvent.click(toggle)  // expand again
    // Data is cached in component state -- no second fetch.
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
  })

  it('displays the data_hash and Cache verified pill', async () => {
    render(<KeyMetricsPanel />)
    fireEvent.click(screen.getByTestId('key-metrics-toggle'))
    await waitFor(() => {
      expect(screen.getByTestId('key-metrics-data-hash'))
        .toHaveTextContent(/c421fb89/i)
    })
    expect(screen.getByText('Cache verified')).toBeInTheDocument()
  })

  it('renders metric rows with label + value', async () => {
    render(<KeyMetricsPanel />)
    fireEvent.click(screen.getByTestId('key-metrics-toggle'))
    await waitFor(() => {
      expect(screen.getByText('Benchmark Sharpe')).toBeInTheDocument()
    })
    expect(screen.getByText('0.54')).toBeInTheDocument()
    expect(screen.getByText('Blend OOS Sharpe')).toBeInTheDocument()
    expect(screen.getByText('0.86')).toBeInTheDocument()
    // OOS window definition surfaces verbatim.
    expect(screen.getByText(
      /January 2022 through May 2026/i)).toBeInTheDocument()
  })

  it('surfaces a placeholder when the cache is cold', async () => {
    mockedAxios.get = vi.fn().mockResolvedValue({
      data: {
        data_hash: '', available: false, metrics: {},
        computed_at: null,
        message: 'Strategy cache is empty -- run the backtester first.',
      },
    })
    render(<KeyMetricsPanel />)
    fireEvent.click(screen.getByTestId('key-metrics-toggle'))
    await waitFor(() => {
      expect(screen.getByText(/Strategy cache is empty/i))
        .toBeInTheDocument()
    })
    // The "Cache verified" pill does NOT render on the cold path.
    expect(screen.queryByText('Cache verified'))
      .not.toBeInTheDocument()
  })

  it('surfaces an error when the request fails', async () => {
    mockedAxios.isAxiosError = vi.fn().mockReturnValue(true) as never
    mockedAxios.get = vi.fn().mockRejectedValue({
      isAxiosError: true,
      response: { data: { detail: 'auth failed' } },
      message: 'boom',
    })
    render(<KeyMetricsPanel />)
    fireEvent.click(screen.getByTestId('key-metrics-toggle'))
    await waitFor(() => {
      expect(screen.getByText(/auth failed/i)).toBeInTheDocument()
    })
  })
})
