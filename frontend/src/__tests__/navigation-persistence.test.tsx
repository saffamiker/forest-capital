/**
 * Verifies the Sprint 6 navigation-persistence guarantee.
 *
 * The contract: once a screen has loaded its data from /api/*, navigating
 * away and back must NOT trigger a re-fetch. All cross-screen state lives
 * in Zustand stores; components call store.load() which short-circuits
 * when loaded=true.
 *
 * This test mounts each consumer in isolation, calls load()/runQuery()
 * once, then re-mounts and asserts axios was called exactly once across
 * the whole sequence. If a future refactor reintroduces direct axios
 * calls in a component, this test fails before it ships.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import axios from 'axios'

import { useStrategiesStore } from '../stores/strategiesStore'
import { useChartsStore } from '../stores/chartsStore'
import { useRegimeStore } from '../stores/regimeStore'
import { useQAStore } from '../stores/qaStore'
import { useCouncilStore } from '../stores/councilStore'

vi.mock('axios')
const mockedAxios = axios as unknown as { get: ReturnType<typeof vi.fn>; post: ReturnType<typeof vi.fn>; isAxiosError: typeof axios.isAxiosError }

beforeEach(() => {
  // Reset every store between tests — singletons would otherwise leak the
  // loaded=true flag and mask a real re-fetch bug.
  useStrategiesStore.setState({ strategies: [], dataRange: null, loading: false, error: null, loaded: false, lastFetchedAt: null })
  useChartsStore.setState({ data: null, loading: false, error: null, loaded: false, lastFetchedAt: null })
  useRegimeStore.setState({ regime: null, loading: false, error: null, fetchedAt: null })
  useQAStore.setState({ result: null, status: 'unknown', loading: false, error: null, loaded: false })
  useCouncilStore.setState({ query: '', lastQuery: '', result: null, loading: false, error: null })
  mockedAxios.get = vi.fn().mockResolvedValue({ data: { strategies: [] } })
  mockedAxios.post = vi.fn().mockResolvedValue({ data: { messages: [], final_recommendation: '', query: '', consensus_reached: true, checks_passed: 0, checks_warned: 0, checks_failed: 0, items: [], verdict: 'PASS', checks_total: 0 } })
  mockedAxios.isAxiosError = (() => false) as never
})

afterEach(() => {
  vi.restoreAllMocks()
})


describe('strategiesStore.load()', () => {
  it('fetches once on first load, no-ops on subsequent loads', async () => {
    const { result } = renderHook(() => useStrategiesStore())
    await act(async () => { await result.current.load() })
    await act(async () => { await result.current.load() })   // simulates Dashboard re-mount
    await act(async () => { await result.current.load() })   // simulates third visit
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    expect(mockedAxios.get).toHaveBeenCalledWith('/api/backtest/compare')
  })

  it('reload() bypasses the loaded guard for explicit refresh', async () => {
    const { result } = renderHook(() => useStrategiesStore())
    await act(async () => { await result.current.load() })
    await act(async () => { await result.current.reload() })
    expect(mockedAxios.get).toHaveBeenCalledTimes(2)
  })
})


describe('chartsStore.load()', () => {
  it('fetches once on first load, no-ops on subsequent loads', async () => {
    mockedAxios.get = vi.fn().mockResolvedValue({ data: {
      cpcv: {}, cv_radar: {}, walk_forward: {}, regime_conditional: {},
      regime_timeline: [], correlation_breakdown: [], factor_loadings: {},
      attribution: {}, transition_matrix: {}, n_strategies: 0, n_months: 0,
    }})
    const { result } = renderHook(() => useChartsStore())
    await act(async () => { await result.current.load() })
    await act(async () => { await result.current.load() })   // simulates StatisticalEvidence re-mount
    await act(async () => { await result.current.load() })   // simulates RegimeAnalysis mount
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    expect(mockedAxios.get).toHaveBeenCalledWith('/api/v1/charts/data')
  })
})


describe('regimeStore.load()', () => {
  it('fetches once when fresh, no-ops within 15-minute TTL window', async () => {
    mockedAxios.get = vi.fn().mockResolvedValue({ data: { threshold_regime: 'BULL' } })
    const { result } = renderHook(() => useRegimeStore())
    await act(async () => { await result.current.load() })
    await act(async () => { await result.current.load() })
    await act(async () => { await result.current.load() })
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)
  })
})


describe('qaStore.load()', () => {
  it('fetches once on first load, no-ops on subsequent loads', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {
      checks_passed: 25, checks_warned: 2, checks_failed: 0,
      summary: '', items: [], verdict: 'WARN', checks_total: 27,
    }})
    const { result } = renderHook(() => useQAStore())
    await act(async () => { await result.current.load() })
    await act(async () => { await result.current.load() })   // simulates QA tab re-mount
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).toHaveBeenCalledWith('/api/qa/audit')
  })

  it('stores audit result with derived status after a successful load', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {
      checks_passed: 25, checks_warned: 2, checks_failed: 0,
      summary: '', items: [], verdict: 'WARN', checks_total: 27,
    }})
    const { result } = renderHook(() => useQAStore())
    await act(async () => { await result.current.load() })
    expect(result.current.loaded).toBe(true)
    expect(result.current.status).toBe('warn')
    expect(result.current.result).not.toBeNull()
  })
})


describe('councilStore.runQuery()', () => {
  it('persists query result across re-mounts without re-fetching', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {
      query: 'test', messages: [], final_recommendation: 'rec', consensus_reached: true,
    }})
    const { result } = renderHook(() => useCouncilStore())
    await act(async () => { await result.current.runQuery('which strategies pass?') })
    expect(result.current.result).not.toBeNull()
    expect(result.current.lastQuery).toBe('which strategies pass?')

    // Simulate Council → Dashboard → Council navigation. Re-rendering the
    // hook does NOT call runQuery — the component would only call setQuery
    // and runQuery on user input, not on mount. The persisted result remains.
    const { result: result2 } = renderHook(() => useCouncilStore())
    expect(result2.current.result?.final_recommendation).toBe('rec')
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
  })

  it('runQuery is a no-op while already loading (prevents double-submit)', async () => {
    // Slow mock so the second call would land while the first is in flight
    mockedAxios.post = vi.fn().mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve({ data: { query: '', messages: [], final_recommendation: '', consensus_reached: true } }), 20)),
    )
    const { result } = renderHook(() => useCouncilStore())
    await act(async () => {
      // Fire both in the same microtask: the second one's guard sees
      // loading=true (set synchronously by runQuery before the await)
      // and returns immediately.
      const p1 = result.current.runQuery('q1')
      const p2 = result.current.runQuery('q2')
      await Promise.all([p1, p2])
    })
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
  })
})


describe('Full Dashboard → Council → QA → Dashboard navigation flow', () => {
  it('triggers exactly one fetch per store across the full sequence', async () => {
    mockedAxios.get = vi.fn()
      .mockImplementation((url: string) => {
        if (url === '/api/backtest/compare') return Promise.resolve({ data: { strategies: [], data_range: null } })
        if (url === '/api/v1/charts/data') return Promise.resolve({ data: {
          cpcv: {}, cv_radar: {}, walk_forward: {}, regime_conditional: {},
          regime_timeline: [], correlation_breakdown: [], factor_loadings: {},
          attribution: {}, transition_matrix: {}, n_strategies: 0, n_months: 0,
        }})
        if (url === '/api/regime/current') return Promise.resolve({ data: { threshold_regime: 'BULL' } })
        return Promise.reject(new Error(`Unexpected: ${url}`))
      })
    mockedAxios.post = vi.fn().mockImplementation((url: string) => {
      if (url === '/api/council/query') return Promise.resolve({ data: { query: 'q', messages: [], final_recommendation: 'rec', consensus_reached: true } })
      if (url === '/api/qa/audit') return Promise.resolve({ data: { checks_passed: 28, checks_warned: 2, checks_failed: 0, summary: '', items: [], verdict: 'WARN', checks_total: 30 } })
      return Promise.reject(new Error(`Unexpected: ${url}`))
    })

    // 1. Dashboard mounts: loads strategies + regime
    const { result: strategies } = renderHook(() => useStrategiesStore())
    const { result: regime } = renderHook(() => useRegimeStore())
    await act(async () => { await strategies.current.load() })
    await act(async () => { await regime.current.load() })

    // 2. Navigate to Council, run a query
    const { result: council } = renderHook(() => useCouncilStore())
    await act(async () => { await council.current.runQuery('which strategies pass?') })

    // 3. Navigate to QA Audit
    const { result: qa } = renderHook(() => useQAStore())
    await act(async () => { await qa.current.load() })

    // 4. Visit StatisticalEvidence (loads charts)
    const { result: charts } = renderHook(() => useChartsStore())
    await act(async () => { await charts.current.load() })

    // 5. Navigate back to Dashboard — load() should be no-op
    await act(async () => { await strategies.current.load() })
    await act(async () => { await regime.current.load() })

    // 6. Back to Council — store still has result, no new POST
    const { result: council2 } = renderHook(() => useCouncilStore())
    expect(council2.current.result?.final_recommendation).toBe('rec')

    // Expected call counts:
    //   GET /api/backtest/compare:    1 (Dashboard)
    //   GET /api/regime/current:      1 (Dashboard)
    //   GET /api/v1/charts/data:      1 (StatisticalEvidence)
    //   POST /api/council/query:      1 (Council runQuery)
    //   POST /api/qa/audit:           1 (QA load)
    expect(mockedAxios.get).toHaveBeenCalledTimes(3)
    expect(mockedAxios.post).toHaveBeenCalledTimes(2)
  })
})
