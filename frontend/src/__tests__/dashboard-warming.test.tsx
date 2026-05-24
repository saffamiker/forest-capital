/**
 * dashboard-warming.test.tsx
 *
 * Hotfix iteration 2 (May 23 2026): the cumulative-returns and
 * efficient-frontier endpoints now return a warming response
 * (`warming: true`) when the precomputed cache is cold instead
 * of blocking on the 10-30s inline compute. The store schedules
 * a retry after the backend's retry_after_ms (capped at 3 retries)
 * so the data appears as soon as the background refresh lands.
 *
 * These tests pin:
 *   - Warming flag from EITHER endpoint flips the store's warming
 *     state to true
 *   - The store schedules a retry that re-fetches both endpoints
 *   - The retry cap (3 attempts) is enforced
 *   - A successful response after warming clears the flag
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import axios from 'axios'

import { useDashboardDataStore } from '../stores/dashboardDataStore'


vi.mock('axios')

const mockedAxios = axios as unknown as {
  get: ReturnType<typeof vi.fn>
  post: ReturnType<typeof vi.fn>
  isAxiosError: (err: unknown) => boolean
}


beforeEach(() => {
  useDashboardDataStore.getState()._reset()
  mockedAxios.get = vi.fn()
  mockedAxios.post = vi.fn()
  mockedAxios.isAxiosError = (err) =>
    !!(err && (err as { isAxiosError?: boolean }).isAxiosError)
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})


function _stubWarmingResponses() {
  // Both endpoints return warming: true with retry_after_ms.
  mockedAxios.get.mockResolvedValueOnce({
    data: { warming: true, retry_after_ms: 10000,
            note: 'Analytics are being computed…' },
  })
  mockedAxios.post.mockResolvedValueOnce({
    data: {
      method: 'MAX_SHARPE',
      weights: {},
      warming: true,
      retry_after_ms: 10000,
      efficient_frontier: {
        frontier_points: [], portfolio_points: [],
        max_sharpe_point: null, min_variance_point: null,
        warming: true,
      },
    },
  })
}


function _stubWarmFollowup() {
  // After the warmup, both endpoints return real data.
  mockedAxios.get.mockResolvedValueOnce({
    data: {
      cumulative_returns: {
        strategies: ['BENCHMARK'],
        points: [{ date: '2024-01-31', BENCHMARK: 1.0 }],
      },
    },
  })
  mockedAxios.post.mockResolvedValueOnce({
    data: {
      method: 'MAX_SHARPE',
      weights: { EQUITY: 0.5, IG: 0.3, HY: 0.2 },
      efficient_frontier: {
        frontier_points: [
          { volatility: 0.10, expected_return: 0.08 },
        ],
        portfolio_points: [],
        max_sharpe_point: { volatility: 0.10, expected_return: 0.08 },
      },
    },
  })
}


describe('dashboardDataStore — warming state', () => {
  it('flips warming=true when either endpoint returns warming', async () => {
    _stubWarmingResponses()
    await useDashboardDataStore.getState().load()
    const s = useDashboardDataStore.getState()
    expect(s.warming).toBe(true)
    expect(s.cumulative).toBeNull()
    // Frontier ships as a placeholder object with empty points;
    // the Dashboard reads frontier_points.length to decide
    // whether to render the chart vs the warming card.
    expect(s.frontier?.frontier_points).toEqual([])
    // Loaded is true so the panel can render its warming state;
    // loading is also true because a retry is scheduled.
    expect(s.loaded).toBe(true)
    expect(s.loading).toBe(true)
  })

  it('a retry fires after retry_after_ms and clears warming on success', async () => {
    _stubWarmingResponses()
    _stubWarmFollowup()
    await useDashboardDataStore.getState().load()
    expect(useDashboardDataStore.getState().warming).toBe(true)

    // Advance the fake clock past the retry delay and flush the
    // pending microtasks so the retry's awaits resolve.
    await vi.advanceTimersByTimeAsync(11_000)

    const s = useDashboardDataStore.getState()
    expect(s.warming).toBe(false)
    expect(s.loading).toBe(false)
    expect(s.cumulative).not.toBeNull()
    expect(s.frontier?.frontier_points?.length).toBe(1)
    // Two fetch rounds: initial + retry.
    expect(mockedAxios.get).toHaveBeenCalledTimes(2)
    expect(mockedAxios.post).toHaveBeenCalledTimes(2)
  })

  it('caps retries at MAX_WARMING_RETRIES (8 — bumped May 24 2026)', async () => {
    // Always warming — backend never finishes.
    // May 24 2026 P0 hotfix bumped the cap 3 → 8 because the cold
    // analytics + frontier first-warm runs 40-60s on Render and the
    // 30s budget was timing out before the cache row landed.
    for (let i = 0; i < 12; i++) {
      mockedAxios.get.mockResolvedValueOnce({
        data: { warming: true, retry_after_ms: 10000 },
      })
      mockedAxios.post.mockResolvedValueOnce({
        data: {
          method: 'MAX_SHARPE', weights: {},
          warming: true, retry_after_ms: 10000,
          efficient_frontier: {
            frontier_points: [], portfolio_points: [],
            max_sharpe_point: null, min_variance_point: null,
            warming: true,
          },
        },
      })
    }
    await useDashboardDataStore.getState().load()

    // Advance well past 9 retries' worth of time.
    for (let i = 0; i < 10; i++) {
      await vi.advanceTimersByTimeAsync(11_000)
    }

    // 1 initial + 8 retries = 9 total calls. The 9th retry past
    // the cap does NOT fire.
    expect(mockedAxios.get).toHaveBeenCalledTimes(9)
    expect(mockedAxios.post).toHaveBeenCalledTimes(9)

    // Warming is still true (the backend never returned data) but
    // loading is false — we've stopped retrying.
    const s = useDashboardDataStore.getState()
    expect(s.warming).toBe(true)
    expect(s.loading).toBe(false)
  })

  it('a warming cumulative + warm frontier still flips warming=true', async () => {
    // Only one of the two endpoints returns warming. The store
    // must STILL set warming=true so the Dashboard renders the
    // warming state for the cold panel.
    mockedAxios.get.mockResolvedValueOnce({
      data: { warming: true, retry_after_ms: 10000 },
    })
    mockedAxios.post.mockResolvedValueOnce({
      data: {
        method: 'MAX_SHARPE',
        weights: { EQUITY: 0.5, IG: 0.3, HY: 0.2 },
        efficient_frontier: {
          frontier_points: [{ volatility: 0.1, expected_return: 0.07 }],
          portfolio_points: [],
          max_sharpe_point: { volatility: 0.1, expected_return: 0.07 },
        },
      },
    })
    await useDashboardDataStore.getState().load()
    const s = useDashboardDataStore.getState()
    expect(s.warming).toBe(true)
    expect(s.cumulative).toBeNull()
    // The frontier landed — it renders normally.
    expect(s.frontier?.frontier_points?.length).toBe(1)
  })
})
