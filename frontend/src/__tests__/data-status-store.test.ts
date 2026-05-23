/**
 * data-status-store.test.ts — pins the F3 fix (May 22 2026).
 *
 * Audit F3 identified Dashboard + DataCurrencyBar + AcademicAnalytics
 * each firing their own /api/v1/admin/data-status request on mount.
 * The fix lifts the fetch into a Zustand store with a 5-minute TTL so
 * every consumer reads the same cached value and only the first mount
 * per session fires the underlying HTTP request.
 *
 * Three contracts pinned here:
 *   1. load() fires axios exactly once for a burst of seven mounts
 *      within the TTL window (the worst case is the Analytics page,
 *      where seven children + the page itself can all call load()
 *      on the same render tick).
 *   2. load() is a no-op when fresh data is in the store (TTL within
 *      bounds AND status is non-null).
 *   3. reload() bypasses the TTL and forces a new fetch (the Settings
 *      "force refresh" path needs this).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import axios from 'axios'

vi.mock('axios')
const mockedAxios = vi.mocked(axios, true)

import {
  useDataStatusStore, tableOf,
} from '../stores/dataStatusStore'


const SAMPLE = {
  available: true,
  study_period: { start: '2002-07-31', end: '2025-12-31', n_months: 282 },
  tables: [
    { name: 'strategy_results_cache', row_count: 10, min_date: null,
      max_date: null, display_label: null,
      last_updated: '2026-05-22T12:00:00Z', staleness: 'green' },
    { name: 'market_data_monthly', row_count: 282, min_date: '2002-07-31',
      max_date: '2025-12-31', display_label: 'December 2025',
      last_updated: null, staleness: 'green' },
  ],
}


beforeEach(() => {
  // Reset the store between tests — Zustand stores are module
  // singletons, so prior state leaks into the next test otherwise.
  useDataStatusStore.setState({
    status: null, loading: false, fetchedAt: null,
  })
  mockedAxios.get = vi.fn().mockResolvedValue({ data: SAMPLE })
})


describe('useDataStatusStore', () => {
  it('load() fires axios exactly once for a burst of seven calls',
    async () => {
      // Simulate the Analytics page mount: seven consumers (Dashboard
      // + DataCurrencyBar + AcademicAnalytics + four section cards)
      // all call load() on the same tick. The first one wins; the
      // other six see loading=true and skip.
      const calls = Array.from({ length: 7 }, () =>
        useDataStatusStore.getState().load())
      await Promise.all(calls)

      expect(mockedAxios.get).toHaveBeenCalledTimes(1)
      expect(mockedAxios.get).toHaveBeenCalledWith(
        '/api/v1/admin/data-status')
      expect(useDataStatusStore.getState().status).toEqual(SAMPLE)
    })

  it('load() is a no-op when fresh data is already in the store',
    async () => {
      // First load populates the store.
      await useDataStatusStore.getState().load()
      expect(mockedAxios.get).toHaveBeenCalledTimes(1)

      // Second load within the TTL window — no fetch.
      await useDataStatusStore.getState().load()
      expect(mockedAxios.get).toHaveBeenCalledTimes(1)
    })

  it('reload() forces a fresh fetch regardless of TTL', async () => {
    // Populate the store.
    await useDataStatusStore.getState().load()
    expect(mockedAxios.get).toHaveBeenCalledTimes(1)

    // reload() bypasses isStale + loading checks and fires again.
    await useDataStatusStore.getState().reload()
    expect(mockedAxios.get).toHaveBeenCalledTimes(2)
  })

  it('fail-open: axios error leaves status null without throwing',
    async () => {
      mockedAxios.get = vi.fn().mockRejectedValue(new Error('500 boom'))
      // load() must not propagate the error — the freshness pill
      // simply renders nothing when status is null.
      await useDataStatusStore.getState().load()
      expect(useDataStatusStore.getState().status).toBeNull()
      expect(useDataStatusStore.getState().loading).toBe(false)
    })

  it('tableOf returns the named table or null', () => {
    const t = tableOf(SAMPLE, 'strategy_results_cache')
    expect(t).not.toBeNull()
    expect(t?.staleness).toBe('green')
    expect(tableOf(SAMPLE, 'nonexistent')).toBeNull()
    expect(tableOf(null, 'anything')).toBeNull()
  })
})
