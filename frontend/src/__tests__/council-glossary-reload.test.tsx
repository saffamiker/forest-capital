/**
 * council-glossary-reload.test.tsx
 *
 * On a successful council session, councilStore re-anchors the
 * Commentary-mode glossary by calling loadTerms(councilOutput). The
 * store's single-flight + 60-second debounce decides whether the call
 * actually fires an API request or is dropped (the next loadTerms()
 * after the window will refresh with the now-current council result).
 *
 * The reload fires only on success — never on a council error or
 * cancellation — which this file pins down with a spy stub on
 * useGlossaryStore.loadTerms. A separate section exercises the REAL
 * loadTerms to cover the debounce + force semantics directly.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import axios from 'axios'
import { useCouncilStore } from '../stores/councilStore'
import { useGlossaryStore, TERMS_DEBOUNCE_MS } from '../stores/glossaryStore'

vi.mock('axios')
const mockedAxios = axios as unknown as {
  post: ReturnType<typeof vi.fn>
  isCancel: (e: unknown) => boolean
  isAxiosError: (e: unknown) => boolean
}

// The real loadTerms — captured once so afterEach restores it after a
// test replaces it with a spy.
const realLoadTerms = useGlossaryStore.getState().loadTerms

function councilResponse(query: string) {
  return {
    data: {
      query, messages: [], final_recommendation: 'rec',
      consensus_reached: true,
      significant_strategies: ['REGIME_SWITCHING'],
    },
  }
}

function loadTermsSpy() {
  return useGlossaryStore.getState().loadTerms as ReturnType<typeof vi.fn>
}

beforeEach(() => {
  useCouncilStore.setState({
    query: '', lastQuery: '', result: null, loading: false,
    error: null, councilUsage: null,
  })
  useGlossaryStore.setState({
    terms: {}, parameters: {}, personas: {}, qa: {}, charts: {},
    termsLastLoadedAt: Date.now(), termsLoading: false,
    inflight: new Set<string>(),
    loadTerms: vi.fn(),   // spy — councilStore must call this on success
  })
  mockedAxios.isCancel = () => false
  mockedAxios.isAxiosError = () => false
})

afterEach(() => {
  vi.restoreAllMocks()
  useGlossaryStore.setState({ loadTerms: realLoadTerms })
})

describe('council completion → glossary reload', () => {
  it('calls loadTerms with the completed council output on success',
    async () => {
      mockedAxios.post = vi.fn().mockResolvedValue(councilResponse('q1'))
      await useCouncilStore.getState().runQuery('q1')
      const loadTerms = loadTermsSpy()
      expect(loadTerms).toHaveBeenCalledTimes(1)
      expect(loadTerms.mock.calls[0][0]).toMatchObject({
        significant_strategies: ['REGIME_SWITCHING'],
      })
    })

  it('does not pass force:true — the store decides via debounce',
    async () => {
      // Per GROUP 2B, the council completion path no longer bypasses
      // the store's guards. It just calls loadTerms() with the new
      // output and the store's debounce takes over. A test exercising
      // multiple rapid council completions cannot rely on a force flag.
      mockedAxios.post = vi.fn().mockResolvedValue(councilResponse('q1'))
      await useCouncilStore.getState().runQuery('q1')
      const loadTerms = loadTermsSpy()
      // The second argument should be undefined (no options object).
      expect(loadTerms.mock.calls[0][1]).toBeUndefined()
    })

  it('does not reload the glossary on a council error', async () => {
    mockedAxios.isAxiosError = () => true
    mockedAxios.post = vi.fn().mockRejectedValue(
      Object.assign(new Error('500'),
        { response: { status: 500, data: {} } }))
    await useCouncilStore.getState().runQuery('q1')
    expect(loadTermsSpy()).not.toHaveBeenCalled()
  })

  it('does not reload the glossary on a cancelled council query', async () => {
    mockedAxios.isCancel = () => true
    mockedAxios.post = vi.fn().mockRejectedValue(new Error('canceled'))
    await useCouncilStore.getState().runQuery('q1')
    expect(loadTermsSpy()).not.toHaveBeenCalled()
  })

  it('re-anchors again on a second council session with the new output',
    async () => {
      // Note: each call goes through the spy stub here, so both fire
      // unconditionally. The REAL loadTerms (tested below) would
      // debounce a second call inside the 60-second window.
      mockedAxios.post = vi.fn()
        .mockResolvedValueOnce(councilResponse('q1'))
        .mockResolvedValueOnce(councilResponse('q2'))
      await useCouncilStore.getState().runQuery('q1')
      await useCouncilStore.getState().runQuery('q2')
      const loadTerms = loadTermsSpy()
      expect(loadTerms).toHaveBeenCalledTimes(2)
      expect(loadTerms.mock.calls[1][0]).toMatchObject({ query: 'q2' })
    })
})

// ── GROUP 2B — single-flight + 60-second debounce on loadTerms itself ─────────

import { useGlossaryStore as glossaryStoreRaw } from '../stores/glossaryStore'

describe('glossaryStore.loadTerms — single-flight + debounce', () => {
  // These tests exercise the REAL loadTerms, not the spy used above.
  beforeEach(() => {
    glossaryStoreRaw.setState({
      terms: { sharpe: { hover: 'old', what: '', why: '',
                          this_session: 'mount', verdict: '' } },
      parameters: {}, personas: {}, qa: {}, charts: {},
      termsLastLoadedAt: null, termsLoading: false,
      inflight: new Set<string>(),
      loadTerms: realLoadTerms,
    })
    mockedAxios.isCancel = () => false
    mockedAxios.isAxiosError = () => false
  })

  it('first call (no prior load) fires the API and stamps the timestamp',
    async () => {
      mockedAxios.post = vi.fn().mockResolvedValue({
        data: { sharpe: { hover: 'new', what: '', why: '',
                           this_session: 'session', verdict: '' } } })
      await glossaryStoreRaw.getState().loadTerms({})
      expect(mockedAxios.post).toHaveBeenCalledTimes(1)
      const last = glossaryStoreRaw.getState().termsLastLoadedAt
      expect(last).not.toBeNull()
      expect(Date.now() - (last ?? 0)).toBeLessThan(1000)
    })

  it('second call within 60s is debounced — no API call', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {} })
    // Stamp a load 5 seconds ago.
    glossaryStoreRaw.setState({ termsLastLoadedAt: Date.now() - 5_000 })
    await glossaryStoreRaw.getState().loadTerms({})
    expect(mockedAxios.post).not.toHaveBeenCalled()
  })

  it('call AFTER 60s window has elapsed fires again', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: {} })
    glossaryStoreRaw.setState({
      termsLastLoadedAt: Date.now() - (TERMS_DEBOUNCE_MS + 1_000),
    })
    await glossaryStoreRaw.getState().loadTerms({})
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
  })

  it('single-flight — a second call while the first is in flight is dropped',
    async () => {
      glossaryStoreRaw.setState({ termsLoading: true })
      mockedAxios.post = vi.fn().mockResolvedValue({ data: {} })
      await glossaryStoreRaw.getState().loadTerms({})
      expect(mockedAxios.post).not.toHaveBeenCalled()
    })

  it('force:true bypasses BOTH the debounce and the in-flight guard',
    async () => {
      // 10s after last load AND a load is supposedly in flight — both
      // guards would normally block. force:true overrides both.
      glossaryStoreRaw.setState({
        termsLastLoadedAt: Date.now() - 10_000,
        termsLoading: true,
      })
      mockedAxios.post = vi.fn().mockResolvedValue({
        data: { sharpe: { hover: 'forced', what: '', why: '',
                           this_session: 'forced', verdict: '' } } })
      await glossaryStoreRaw.getState().loadTerms({}, { force: true })
      expect(mockedAxios.post).toHaveBeenCalledTimes(1)
      expect(glossaryStoreRaw.getState()
        .terms.sharpe?.this_session).toBe('forced')
    })

  it('stamps termsLastLoadedAt even on error to prevent retry storms',
    async () => {
      mockedAxios.post = vi.fn().mockRejectedValue(new Error('boom'))
      await glossaryStoreRaw.getState().loadTerms({})
      // A failed fetch leaves terms unchanged but stamps the timestamp
      // so the next 60 seconds are debounced — no busy-loop retries.
      const last = glossaryStoreRaw.getState().termsLastLoadedAt
      expect(last).not.toBeNull()
    })

  it('overwrites terms with the fresh response on success', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({
      data: { sharpe: { hover: 'new', what: '', why: '',
                         this_session: 'session', verdict: '' } } })
    await glossaryStoreRaw.getState().loadTerms({})
    expect(glossaryStoreRaw.getState().terms.sharpe?.this_session)
      .toBe('session')
  })
})
