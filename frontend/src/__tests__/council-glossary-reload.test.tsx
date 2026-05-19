/**
 * council-glossary-reload.test.tsx
 *
 * On a successful council session, councilStore re-anchors the
 * Commentary-mode glossary: it clears the once-per-session termsLoaded
 * guard and reloads loadTerms() with the completed council output, so
 * each term's `this_session` reflects the actual results. The reload
 * fires only on success — never on a council error or cancellation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import axios from 'axios'
import { useCouncilStore } from '../stores/councilStore'
import { useGlossaryStore } from '../stores/glossaryStore'

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
    termsLoaded: true, termsLoading: false, inflight: new Set<string>(),
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
  it('clears the termsLoaded guard on a successful council session', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue(councilResponse('q1'))
    await useCouncilStore.getState().runQuery('q1')
    expect(useGlossaryStore.getState().termsLoaded).toBe(false)
  })

  it('reloads loadTerms with the completed council output', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue(councilResponse('q1'))
    await useCouncilStore.getState().runQuery('q1')
    const loadTerms = loadTermsSpy()
    expect(loadTerms).toHaveBeenCalledTimes(1)
    expect(loadTerms.mock.calls[0][0]).toMatchObject({
      significant_strategies: ['REGIME_SWITCHING'],
    })
  })

  it('does not reload the glossary on a council error', async () => {
    mockedAxios.isAxiosError = () => true
    mockedAxios.post = vi.fn().mockRejectedValue(
      Object.assign(new Error('500'),
        { response: { status: 500, data: {} } }))
    await useCouncilStore.getState().runQuery('q1')
    expect(loadTermsSpy()).not.toHaveBeenCalled()
    // The guard is left intact — no reload was triggered.
    expect(useGlossaryStore.getState().termsLoaded).toBe(true)
  })

  it('does not reload the glossary on a cancelled council query', async () => {
    mockedAxios.isCancel = () => true
    mockedAxios.post = vi.fn().mockRejectedValue(new Error('canceled'))
    await useCouncilStore.getState().runQuery('q1')
    expect(loadTermsSpy()).not.toHaveBeenCalled()
    expect(useGlossaryStore.getState().termsLoaded).toBe(true)
  })

  it('re-anchors again on a second council session with the new output', async () => {
    mockedAxios.post = vi.fn()
      .mockResolvedValueOnce(councilResponse('q1'))
      .mockResolvedValueOnce(councilResponse('q2'))
    await useCouncilStore.getState().runQuery('q1')
    await useCouncilStore.getState().runQuery('q2')
    const loadTerms = loadTermsSpy()
    expect(loadTerms).toHaveBeenCalledTimes(2)
    expect(loadTerms.mock.calls[1][0]).toMatchObject({ query: 'q2' })
  })

  it('passes force:true so the reload bypasses the loadTerms guards', async () => {
    // FIX 1 — the reset+call alone races with an in-flight initial load.
    // force:true bypasses both termsLoaded and termsLoading guards so the
    // reload always runs and the reload is silent (no termsLoading flash).
    mockedAxios.post = vi.fn().mockResolvedValue(councilResponse('q1'))
    await useCouncilStore.getState().runQuery('q1')
    const loadTerms = loadTermsSpy()
    expect(loadTerms.mock.calls[0][1]).toEqual({ force: true })
  })
})

// ── FIX 1 — the force path on loadTerms itself ────────────────────────────────

import { useGlossaryStore as glossaryStoreRaw } from '../stores/glossaryStore'

describe('glossaryStore.loadTerms — force reload', () => {
  // These tests exercise the REAL loadTerms, not the spy used above.
  beforeEach(() => {
    glossaryStoreRaw.setState({
      terms: { sharpe: { hover: 'old', what: '', why: '',
                          this_session: 'mount', verdict: '' } },
      parameters: {}, personas: {}, qa: {}, charts: {},
      termsLoaded: true, termsLoading: false, inflight: new Set<string>(),
      loadTerms: realLoadTerms,
    })
    mockedAxios.isCancel = () => false
    mockedAxios.isAxiosError = () => false
  })

  it('non-force call returns early when termsLoaded is true', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({ data: { sharpe: { hover: 'new', what: '', why: '', this_session: 'session', verdict: '' } } })
    await glossaryStoreRaw.getState().loadTerms({}, undefined)
    expect(mockedAxios.post).not.toHaveBeenCalled()
    expect(glossaryStoreRaw.getState().terms.sharpe?.this_session).toBe('mount')
  })

  it('force:true bypasses termsLoaded guard and overwrites terms', async () => {
    mockedAxios.post = vi.fn().mockResolvedValue({
      data: { sharpe: { hover: 'new', what: '', why: '',
                         this_session: 'session', verdict: '' } } })
    await glossaryStoreRaw.getState().loadTerms({}, { force: true })
    expect(mockedAxios.post).toHaveBeenCalledTimes(1)
    expect(glossaryStoreRaw.getState().terms.sharpe?.this_session).toBe('session')
  })

  it('force:true does NOT toggle termsLoading (silent reload)', async () => {
    // Capture the termsLoading state DURING the in-flight request — if
    // the force path were to set it true, this would observe true.
    let observedDuringFetch: boolean | null = null
    mockedAxios.post = vi.fn().mockImplementation(async () => {
      observedDuringFetch = glossaryStoreRaw.getState().termsLoading
      return { data: {} }
    })
    await glossaryStoreRaw.getState().loadTerms({}, { force: true })
    expect(observedDuringFetch).toBe(false)
  })

  it('force:true bypasses an in-flight initial load (termsLoading=true)',
    async () => {
      glossaryStoreRaw.setState({ termsLoaded: false, termsLoading: true })
      mockedAxios.post = vi.fn().mockResolvedValue({
        data: { sharpe: { hover: 'new', what: '', why: '',
                           this_session: 'session', verdict: '' } } })
      await glossaryStoreRaw.getState().loadTerms({}, { force: true })
      // The fetch ran even though termsLoading was true — without
      // force:true the guard would have returned early with no call.
      expect(mockedAxios.post).toHaveBeenCalledTimes(1)
    })
})
