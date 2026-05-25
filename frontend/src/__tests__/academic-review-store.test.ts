/**
 * academic-review-store.test.ts — pins the data_hash cache key + the
 * read/clear contracts for the Academic Review Zustand store
 * (May 28 2026 relocation from the Council screen to the QA Audit
 * page).
 *
 * The streaming runReview() path is not exercised here — fetch +
 * ReadableStream mocking is heavy and is covered at the integration
 * level via the component test. This file pins the store's pure
 * behaviour: isCurrentFor, cancel, clear, initial state.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { useAcademicReviewStore } from '../stores/academicReviewStore'


describe('academicReviewStore — initial state', () => {
  beforeEach(() => {
    useAcademicReviewStore.getState().clear()
  })

  it('starts in idle phase with no result', () => {
    const s = useAcademicReviewStore.getState()
    expect(s.phase).toBe('idle')
    expect(s.result).toBeNull()
    expect(s.dataHash).toBeNull()
    expect(s.completedAt).toBeNull()
    expect(s.errorMsg).toBe('')
  })

  it('isCurrentFor returns false on initial state', () => {
    const s = useAcademicReviewStore.getState()
    expect(s.isCurrentFor('anyhash')).toBe(false)
    expect(s.isCurrentFor(null)).toBe(false)
  })
})


describe('academicReviewStore — isCurrentFor contract', () => {
  beforeEach(() => {
    useAcademicReviewStore.getState().clear()
  })

  it('returns true when phase is done and hash matches', () => {
    // Simulate a completed run by setting state directly — the
    // streaming path is exercised at the integration layer.
    useAcademicReviewStore.setState({
      phase: 'done',
      dataHash: 'abc123',
      result: { arbiterText: '...', peerResponses: {} },
    })
    expect(
      useAcademicReviewStore.getState().isCurrentFor('abc123')
    ).toBe(true)
  })

  it('returns false when hash differs', () => {
    useAcademicReviewStore.setState({
      phase: 'done',
      dataHash: 'abc123',
      result: { arbiterText: '...', peerResponses: {} },
    })
    expect(
      useAcademicReviewStore.getState().isCurrentFor('xyz999')
    ).toBe(false)
  })

  it('returns false when phase is not done (mid-stream)', () => {
    // The middle of a streaming run shouldn't auto-render as cached
    // — the partial verdict isn't a usable cached result yet.
    useAcademicReviewStore.setState({
      phase: 'streaming',
      dataHash: 'abc123',
      result: { arbiterText: 'partial...', peerResponses: {} },
    })
    expect(
      useAcademicReviewStore.getState().isCurrentFor('abc123')
    ).toBe(false)
  })

  it('returns false when supplied hash is null', () => {
    // Null hash from the audit endpoint (e.g. no audit on record yet)
    // means we don't know what to compare against — safer to not
    // auto-render a cached verdict against an unknown data state.
    useAcademicReviewStore.setState({
      phase: 'done',
      dataHash: 'abc123',
      result: { arbiterText: '...', peerResponses: {} },
    })
    expect(useAcademicReviewStore.getState().isCurrentFor(null)).toBe(false)
  })

  it('returns false when result is null even if hash matches', () => {
    // Edge case — phase says done but result somehow null. Don't
    // pretend there's something to render.
    useAcademicReviewStore.setState({
      phase: 'done',
      dataHash: 'abc123',
      result: null,
    })
    expect(
      useAcademicReviewStore.getState().isCurrentFor('abc123')
    ).toBe(false)
  })
})


describe('academicReviewStore — clear / cancel', () => {
  beforeEach(() => {
    useAcademicReviewStore.getState().clear()
  })

  it('clear() resets every cached field', () => {
    useAcademicReviewStore.setState({
      phase: 'done',
      dataHash: 'abc123',
      result: { arbiterText: 'verdict', peerResponses: { a: 'b' } },
      completedAt: '2026-05-28T10:00:00Z',
      errorMsg: '',
    })
    useAcademicReviewStore.getState().clear()
    const s = useAcademicReviewStore.getState()
    expect(s.phase).toBe('idle')
    expect(s.result).toBeNull()
    expect(s.dataHash).toBeNull()
    expect(s.completedAt).toBeNull()
    expect(s.errorMsg).toBe('')
  })

  it('cancel() aborts any in-flight controller', () => {
    const ctrl = new AbortController()
    useAcademicReviewStore.setState({
      phase: 'streaming',
      _controller: ctrl,
    })
    useAcademicReviewStore.getState().cancel()
    expect(ctrl.signal.aborted).toBe(true)
    expect(useAcademicReviewStore.getState().phase).toBe('idle')
    // The controller reference is dropped after cancel.
    expect(useAcademicReviewStore.getState()._controller).toBeNull()
  })

  it('cancel() preserves the partial result (user can review it)', () => {
    // A cancelled run intentionally keeps whatever partial verdict
    // had landed so the user can scroll back through what the
    // arbiter said before they hit Cancel.
    useAcademicReviewStore.setState({
      phase: 'streaming',
      result: { arbiterText: 'partial verdict', peerResponses: {} },
      dataHash: 'abc123',
      _controller: new AbortController(),
    })
    useAcademicReviewStore.getState().cancel()
    const s = useAcademicReviewStore.getState()
    expect(s.result?.arbiterText).toBe('partial verdict')
    expect(s.dataHash).toBe('abc123')
    expect(s.phase).toBe('idle')
  })

  it('clear() aborts an in-flight controller too', () => {
    // The user's "Re-run" path: clear THEN runReview. clear() must
    // abort any prior in-flight stream before dropping state.
    const ctrl = new AbortController()
    useAcademicReviewStore.setState({
      phase: 'streaming',
      _controller: ctrl,
      result: { arbiterText: 'something', peerResponses: {} },
    })
    useAcademicReviewStore.getState().clear()
    expect(ctrl.signal.aborted).toBe(true)
    expect(useAcademicReviewStore.getState().result).toBeNull()
  })
})
