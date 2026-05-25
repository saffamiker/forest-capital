/**
 * qa-store-force.test.tsx — May 25 2026.
 *
 * Pins the qaStore's force parameter contract. The backend's
 * /api/qa/audit gates on a strategy-hash cache; a manual re-run after
 * an Academic Review (which doesn't change the strategy hash) needs
 * force=true to bypass the cache and re-evaluate IN02.
 *
 *   reload()        — default force=true.  Manual "Re-run audit"
 *                     buttons pass nothing and get the bypass.
 *   reload(false)   — explicit cache-friendly. load() uses this on
 *                     first tab visit.
 *   load()          — passes force=false through reload(false).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('axios', () => ({
  default: { post: vi.fn(), get: vi.fn() },
  isAxiosError: () => false,
}))

import axios from 'axios'
import { useQAStore } from '../stores/qaStore'

const mockedAxios = axios as unknown as {
  post: ReturnType<typeof vi.fn>
  get: ReturnType<typeof vi.fn>
}


const SUCCESS_PAYLOAD = {
  sprint: '4', checks_total: 30, checks_passed: 25,
  checks_warned: 3, checks_failed: 2,
  verdict: 'WARN', summary: 'ok',
  items: [],
}


beforeEach(() => {
  mockedAxios.post.mockReset()
  mockedAxios.get.mockReset()
  // Reset the store between tests so loaded / loading / result don't
  // leak. useQAStore is a singleton Zustand store across tests.
  useQAStore.setState({
    result: null, status: 'unknown', tieredStatus: null,
    loading: false, error: null, loaded: false,
  })
})


describe('qaStore.reload — default force=true', () => {
  it('POSTs {force: true} when called with no argument', async () => {
    mockedAxios.post.mockResolvedValue({ data: SUCCESS_PAYLOAD })
    await useQAStore.getState().reload()
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/qa/audit', { force: true })
  })

  it('POSTs {force: true} when called with force=true explicitly',
    async () => {
      mockedAxios.post.mockResolvedValue({ data: SUCCESS_PAYLOAD })
      await useQAStore.getState().reload(true)
      expect(mockedAxios.post).toHaveBeenCalledWith(
        '/api/qa/audit', { force: true })
    })

  it('POSTs {force: false} when called with force=false', async () => {
    mockedAxios.post.mockResolvedValue({ data: SUCCESS_PAYLOAD })
    await useQAStore.getState().reload(false)
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/qa/audit', { force: false })
  })
})


describe('qaStore.load — cache-friendly', () => {
  it('POSTs {force: false} on first load', async () => {
    mockedAxios.post.mockResolvedValue({ data: SUCCESS_PAYLOAD })
    await useQAStore.getState().load()
    // First call is the load → reload(false) path.
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
    expect(mockedAxios.post).toHaveBeenCalledWith(
      '/api/qa/audit', { force: false })
  })

  it('is a no-op when already loaded', async () => {
    mockedAxios.post.mockResolvedValue({ data: SUCCESS_PAYLOAD })
    await useQAStore.getState().load()  // first load
    mockedAxios.post.mockClear()
    await useQAStore.getState().load()  // already loaded
    expect(mockedAxios.post).not.toHaveBeenCalled()
  })

  it('reload() AFTER load() forces the bypass', async () => {
    // The canonical sequence for the QAHub: load on mount, then
    // the user clicks Re-run. Load should NOT force; reload should.
    mockedAxios.post.mockResolvedValue({ data: SUCCESS_PAYLOAD })
    await useQAStore.getState().load()
    expect(mockedAxios.post).toHaveBeenLastCalledWith(
      '/api/qa/audit', { force: false })
    await useQAStore.getState().reload()
    expect(mockedAxios.post).toHaveBeenLastCalledWith(
      '/api/qa/audit', { force: true })
  })
})
