/**
 * dashboard-remount.test.tsx — May 23 2026 production fire fix.
 *
 * Pre-fix: cumulative + frontier lived in Dashboard component state.
 * Every navigation away from /dashboard unmounted the component
 * (clearing the state) and remounted it (firing fresh GETs that
 * raced through silent catch blocks). Users reported the
 * "Cumulative return series unavailable" + empty frontier stuck
 * state on navigation return.
 *
 * Post-fix: dashboardDataStore (Zustand) caches both across the
 * session. load() is a no-op when loaded === true.
 *
 * This file pins the contract:
 *   1. Default state is cleared (null + null + loaded=false).
 *   2. load() fires both fetches in parallel.
 *   3. A second load() on the same session is a no-op (cache hit).
 *   4. Each fetch's error is independent — a frontier failure does
 *      NOT clear cumulative.
 *   5. refresh() forces a re-fetch.
 *   6. Errors are surfaced (cumulativeError / frontierError) rather
 *      than swallowed silently.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

import { useDashboardDataStore } from '../stores/dashboardDataStore'

import axios from 'axios'

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
})

afterEach(() => {
  vi.clearAllMocks()
})


function _stubCumulativeOK() {
  const series = {
    strategies: ['BENCHMARK', 'REGIME_SWITCHING'],
    points: [
      { date: '2024-01-31', BENCHMARK: 1.0, REGIME_SWITCHING: 1.0 },
      { date: '2024-02-29', BENCHMARK: 1.02, REGIME_SWITCHING: 1.04 },
    ],
  }
  mockedAxios.get.mockResolvedValue({
    data: { cumulative_returns: series },
  })
  return series
}


function _stubFrontierOK() {
  const data = {
    frontier_points: [
      { volatility: 0.10, expected_return: 0.08 },
      { volatility: 0.15, expected_return: 0.10 },
    ],
    portfolio_points: [],
    max_sharpe_point: { volatility: 0.10, expected_return: 0.08 },
  }
  mockedAxios.post.mockResolvedValue({
    data: { efficient_frontier: data },
  })
  return data
}


describe('dashboardDataStore — default state', () => {
  it('starts cleared', () => {
    const s = useDashboardDataStore.getState()
    expect(s.cumulative).toBeNull()
    expect(s.frontier).toBeNull()
    expect(s.cumulativeError).toBeNull()
    expect(s.frontierError).toBeNull()
    expect(s.loaded).toBe(false)
    expect(s.loading).toBe(false)
  })
})


describe('dashboardDataStore — load() happy path', () => {
  it('fetches cumulative + frontier in parallel and populates', async () => {
    const cum = _stubCumulativeOK()
    const fr = _stubFrontierOK()
    await useDashboardDataStore.getState().load()
    const s = useDashboardDataStore.getState()
    expect(s.cumulative).toEqual(cum)
    expect(s.frontier).toEqual(fr)
    expect(s.loaded).toBe(true)
    expect(s.loading).toBe(false)
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
    // Confirm the right URLs were hit.
    expect(mockedAxios.get).toHaveBeenCalledWith(
      '/api/v1/analytics/academic', expect.any(Object))
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/optimize/weights',
      { method: 'MAX_SHARPE' },
      expect.any(Object))
  })

  it('second load() on the same session is a no-op (cache hit)', async () => {
    _stubCumulativeOK()
    _stubFrontierOK()
    await useDashboardDataStore.getState().load()
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    // Second call.
    await useDashboardDataStore.getState().load()
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
  })
})


describe('dashboardDataStore — error surfacing', () => {
  it('a cumulative failure does not blank the frontier', async () => {
    mockedAxios.get.mockRejectedValue(
      Object.assign(new Error('500 server error'),
                    { isAxiosError: true }))
    const fr = _stubFrontierOK()
    await useDashboardDataStore.getState().load()
    const s = useDashboardDataStore.getState()
    expect(s.cumulative).toBeNull()
    expect(s.cumulativeError).toMatch(/Load failed/)
    expect(s.frontier).toEqual(fr)
    expect(s.frontierError).toBeNull()
    expect(s.loaded).toBe(true)
  })

  it('a frontier failure does not blank the cumulative', async () => {
    const cum = _stubCumulativeOK()
    mockedAxios.post.mockRejectedValue(
      Object.assign(new Error('network down'),
                    { isAxiosError: true }))
    await useDashboardDataStore.getState().load()
    const s = useDashboardDataStore.getState()
    expect(s.cumulative).toEqual(cum)
    expect(s.cumulativeError).toBeNull()
    expect(s.frontier).toBeNull()
    expect(s.frontierError).toMatch(/Load failed/)
  })

  it('empty cumulative_returns surfaces an error message', async () => {
    mockedAxios.get.mockResolvedValue({ data: {} })
    _stubFrontierOK()
    await useDashboardDataStore.getState().load()
    const s = useDashboardDataStore.getState()
    expect(s.cumulative).toBeNull()
    expect(s.cumulativeError).toMatch(/unavailable in cache/)
  })
})


describe('dashboardDataStore — refresh()', () => {
  it('forces a re-fetch even when loaded', async () => {
    _stubCumulativeOK()
    _stubFrontierOK()
    await useDashboardDataStore.getState().load()
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    // load again — no-op.
    await useDashboardDataStore.getState().load()
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    // refresh — fetches again.
    await useDashboardDataStore.getState().refresh()
    expect(mockedAxios.get).toHaveBeenCalledTimes(2)
    expect(mockedAxios.post).toHaveBeenCalledTimes(2)
  })

  it('clears previous errors on refresh', async () => {
    mockedAxios.get.mockRejectedValueOnce(
      Object.assign(new Error('first error'),
                    { isAxiosError: true }))
    _stubFrontierOK()
    await useDashboardDataStore.getState().load()
    expect(useDashboardDataStore.getState().cumulativeError).toMatch(/first error/)
    // Now succeed.
    _stubCumulativeOK()
    _stubFrontierOK()
    await useDashboardDataStore.getState().refresh()
    expect(useDashboardDataStore.getState().cumulativeError).toBeNull()
  })
})
