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


// ── SSE event ingestion — independent_review frame (May 25 2026) ─────────────

describe('academicReviewStore — independent_review SSE frame', () => {
  beforeEach(() => {
    useAcademicReviewStore.getState().clear()
  })

  // The store's runReview consumes a text/event-stream response.
  // This helper builds a Response whose body iterates pre-built
  // SSE frames so the test can drive the reader without a real
  // network call. Each frame is a complete `data: {...}\n\n` block.
  function sseResponse(frames: string[]): Response {
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        for (const frame of frames) {
          controller.enqueue(encoder.encode(frame))
        }
        controller.close()
      },
    })
    return new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
  }

  it('captures the independent_review frame into result.independentReview',
    async () => {
      const frames = [
        'data: ' + JSON.stringify({
          type: 'peer_responses',
          data: { equity_analyst: 'OK.' },
        }) + '\n\n',
        'data: ' + JSON.stringify({
          type: 'arbiter_chunk',
          text: '## Section 1.\nAll good.',
        }) + '\n\n',
        'data: ' + JSON.stringify({
          type: 'independent_review',
          verdict: 'Plausible',
          overall_reasoning: 'Findings hang together.',
          per_finding: [
            { finding: 'best_strategy_sharpe',
              label: 'Best Strategy Sharpe',
              assessment: 'Plausible Sharpe.',
              concern: '' },
          ],
          model: 'gemini-2.5-pro',
          findings_seen: { best_strategy_sharpe: 'Sharpe 0.63' },
        }) + '\n\n',
        'data: [DONE]\n\n',
      ]
      const origFetch = globalThis.fetch
      globalThis.fetch = async () => sseResponse(frames)
      try {
        await useAcademicReviewStore.getState().runReview('hash1', 'token')
      } finally {
        globalThis.fetch = origFetch
      }
      const s = useAcademicReviewStore.getState()
      expect(s.phase).toBe('done')
      expect(s.result?.independentReview).not.toBeNull()
      expect(s.result?.independentReview?.verdict).toBe('Plausible')
      expect(s.result?.independentReview?.model).toBe('gemini-2.5-pro')
      // The per-finding entries surface intact.
      expect(s.result?.independentReview?.per_finding).toHaveLength(1)
      expect(s.result?.independentReview?.per_finding[0].finding)
        .toBe('best_strategy_sharpe')
    })

  it('independentReview stays null when the SSE stream never emits the frame',
    async () => {
      // Legacy producer that hasn't yet emitted independent_review —
      // the store must finalise the run with independentReview: null
      // rather than failing the parse.
      const frames = [
        'data: ' + JSON.stringify({
          type: 'peer_responses', data: {},
        }) + '\n\n',
        'data: ' + JSON.stringify({
          type: 'arbiter_chunk', text: 'verdict here.',
        }) + '\n\n',
        'data: [DONE]\n\n',
      ]
      const origFetch = globalThis.fetch
      globalThis.fetch = async () => sseResponse(frames)
      try {
        await useAcademicReviewStore.getState().runReview('hash2', 'token')
      } finally {
        globalThis.fetch = origFetch
      }
      const s = useAcademicReviewStore.getState()
      expect(s.phase).toBe('done')
      expect(s.result?.independentReview ?? null).toBeNull()
    })

  it('a Concerns verdict surfaces with its per-finding concerns intact',
    async () => {
      const frames = [
        'data: ' + JSON.stringify({
          type: 'independent_review',
          verdict: 'Concerns',
          overall_reasoning: 'One finding is borderline.',
          per_finding: [
            { finding: 'best_strategy_sharpe',
              label: 'Best Strategy Sharpe',
              assessment: 'High Sharpe.',
              concern: '1.2 Sharpe on monthly data is above literature.' },
          ],
          model: 'gemini-2.5-pro',
          findings_seen: {},
        }) + '\n\n',
        'data: [DONE]\n\n',
      ]
      const origFetch = globalThis.fetch
      globalThis.fetch = async () => sseResponse(frames)
      try {
        await useAcademicReviewStore.getState().runReview('hash3', 'token')
      } finally {
        globalThis.fetch = origFetch
      }
      const ir = useAcademicReviewStore.getState().result?.independentReview
      expect(ir?.verdict).toBe('Concerns')
      expect(ir?.per_finding[0].concern).toContain('1.2 Sharpe')
    })
})
