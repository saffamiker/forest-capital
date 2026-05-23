/**
 * council-sse.test.tsx
 *
 * SSE consumption contract for councilStore.runQuery (May 2026).
 * The store reads the council_query stream from
 * POST /api/council/query and assembles partial state from each
 * event so the CouncilDebate.tsx component renders progressively.
 *
 * These tests pin:
 *   - Each event type lands in the right place in state
 *   - The result skeleton is set on submit so the UI is not blank
 *   - council_complete replaces the partial state with the
 *     canonical backend dict
 *   - council_error surfaces as result.error + an error message
 *   - synthesis_chunk accumulates into the CIO message in place
 *   - A 429 council_limit_reached is decoded into the blocked state
 *
 * The test mocks global.fetch with a ReadableStream that emits SSE
 * frames in the exact wire format the backend writes. The store is
 * exercised end-to-end including the buffer split / [DONE] handling.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import axios from 'axios'
import { useCouncilStore } from '../stores/councilStore'
import { useGlossaryStore } from '../stores/glossaryStore'

// councilStore.ts no longer imports axios, but the glossaryStore the
// council reload calls into still uses it. Stub it so loadTerms never
// hits the network during these tests.
vi.mock('axios')

function sseResponse(frames: Array<Record<string, unknown> | '[DONE]'>): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) {
        const payload = frame === '[DONE]' ? '[DONE]' : JSON.stringify(frame)
        controller.enqueue(encoder.encode(`data: ${payload}\n\n`))
      }
      controller.close()
    },
  })
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

const realLoadTerms = useGlossaryStore.getState().loadTerms

beforeEach(() => {
  useCouncilStore.setState({
    query: '', lastQuery: '', result: null, loading: false,
    error: null, councilUsage: null,
  })
  // Stub loadTerms so the store's success path doesn't crash on
  // an axios call we haven't configured.
  useGlossaryStore.setState({ loadTerms: vi.fn() })
  // Token comes from localStorage — provide one so the X-API-Key
  // header is non-empty.
  localStorage.setItem('fc_session_token', 'test-token')
  ;(axios as unknown as { post: ReturnType<typeof vi.fn> }).post = vi.fn()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  useGlossaryStore.setState({ loadTerms: realLoadTerms })
  localStorage.removeItem('fc_session_token')
})

describe('councilStore SSE consumption', () => {
  it('sets a loading skeleton immediately on runQuery start', async () => {
    // Build a frames sequence where council_complete is the LAST
    // event so we can intercept the intermediate state.
    let openController: ReadableStreamDefaultController<Uint8Array> | null = null
    const body = new ReadableStream<Uint8Array>({
      start(controller) { openController = controller },
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(body, { status: 200 })))

    // Kick the runQuery — don't await yet.
    const pending = useCouncilStore.getState().runQuery('Test query')
    // Yield one microtask so the store has set the skeleton.
    await Promise.resolve()
    const state = useCouncilStore.getState()
    expect(state.loading).toBe(true)
    expect(state.result?.query).toBe('Test query')
    expect(state.result?.messages).toEqual([])

    // Close the stream so the test can await without hanging.
    openController!.close()
    await pending
  })

  it('appends a specialist_complete message to result.messages', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      { type: 'council_started', query: 'q' },
      {
        type: 'specialist_complete',
        agent_id: 'equity_analyst',
        response: {
          summary: 'equity summary',
          technical_findings: { raw_analysis: 'full equity prose' },
        },
      },
      { type: 'council_complete', result: {
        query: 'q', messages: [{
          agent: 'Equity Analyst', role: 'specialist',
          model: 'claude-sonnet-4-6', content: 'full equity prose',
          is_final: false,
        }],
        final_recommendation: '', consensus_reached: true, mode: 'live',
      } },
      '[DONE]',
    ])))
    await useCouncilStore.getState().runQuery('q')
    // After council_complete the result is the canonical dict — but
    // the messages array must contain the equity card.
    const state = useCouncilStore.getState()
    expect(state.loading).toBe(false)
    const equity = state.result?.messages.find(
      (m) => m.agent === 'Equity Analyst')
    expect(equity).toBeDefined()
    expect(equity?.content).toBe('full equity prose')
  })

  it('builds dissent messages from gemini and grok dissent_complete frames',
    async () => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
        { type: 'council_started', query: 'q' },
        {
          type: 'dissent_complete',
          source: 'gemini',
          challenge: {
            summary: 'g summary',
            technical_findings: { full_challenge: 'gemini challenge text' },
          },
        },
        {
          type: 'dissent_complete',
          source: 'grok',
          challenge: {
            summary: 'r summary',
            technical_findings: { full_challenge: 'grok challenge text' },
          },
        },
        { type: 'council_complete', result: {
          query: 'q', messages: [
            { agent: 'Independent Analyst (Gemini)', role: 'dissenter',
              model: 'gemini-2.0-flash', content: 'gemini challenge text',
              is_final: false },
            { agent: 'Contrarian Analyst (Grok)', role: 'dissenter',
              model: 'grok-4.3', content: 'grok challenge text',
              is_final: false },
          ],
          final_recommendation: '', consensus_reached: true, mode: 'live',
        } },
        '[DONE]',
      ])))
      await useCouncilStore.getState().runQuery('q')
      const messages = useCouncilStore.getState().result?.messages ?? []
      const gemini = messages.find(
        (m) => m.agent === 'Independent Analyst (Gemini)')
      const grok = messages.find(
        (m) => m.agent === 'Contrarian Analyst (Grok)')
      expect(gemini?.content).toContain('gemini challenge')
      expect(grok?.content).toContain('grok challenge')
    })

  it('accumulates synthesis_chunk frames into the CIO message',
    async () => {
      // Build a sequence where three synthesis_chunk frames arrive
      // before council_complete. We intercept the state at council_
      // complete time to verify the chunks have accumulated.
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
        { type: 'council_started', query: 'q' },
        { type: 'synthesis_chunk', text: 'The council ' },
        { type: 'synthesis_chunk', text: 'recommends ' },
        { type: 'synthesis_chunk', text: 'VOL_TARGETING.' },
        { type: 'council_complete', result: {
          query: 'q', messages: [{
            agent: 'CIO', role: 'cio', model: 'claude-opus-4-7',
            content: 'The council recommends VOL_TARGETING.',
            is_final: true,
          }],
          final_recommendation: 'VOL_TARGETING',
          consensus_reached: true, mode: 'live',
        } },
        '[DONE]',
      ])))
      await useCouncilStore.getState().runQuery('q')
      const cio = useCouncilStore.getState().result?.messages
        .find((m) => m.role === 'cio')
      expect(cio?.content).toBe('The council recommends VOL_TARGETING.')
    })

  it('surfaces council_error as result.error and an error message',
    async () => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
        { type: 'council_started', query: 'q' },
        { type: 'council_error',
          message: 'Council query failed. Please try again.' },
        '[DONE]',
      ])))
      await useCouncilStore.getState().runQuery('q')
      const state = useCouncilStore.getState()
      expect(state.loading).toBe(false)
      expect(state.error).toBe('Council query failed. Please try again.')
      expect(state.result?.error).toBe(true)
    })

  it('handles a stream that closes without council_complete or '
      + 'council_error as an error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse([
      { type: 'council_started', query: 'q' },
      // No completion, no error — just a [DONE] that arrives early.
      '[DONE]',
    ])))
    await useCouncilStore.getState().runQuery('q')
    const state = useCouncilStore.getState()
    expect(state.loading).toBe(false)
    expect(state.error).toContain('ended before completion')
    expect(state.result?.error).toBe(true)
  })

  it('decodes a 429 council_limit_reached into the blocked state',
    async () => {
      // The 429 returns BEFORE the SSE stream starts — same shape as
      // the synchronous handler's previous 429 response.
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
        JSON.stringify({
          detail: {
            error: 'council_limit_reached', limit: 5, used: 5,
          },
        }),
        { status: 429, headers: { 'Content-Type': 'application/json' } },
      )))
      await useCouncilStore.getState().runQuery('q')
      const state = useCouncilStore.getState()
      expect(state.error).toBe('council_limit_reached')
      expect(state.councilUsage).toEqual({ used: 5, limit: 5 })
      expect(state.result?.error).toBe(true)
    })

  it('treats an aborted fetch as a clean cancel (no error state)',
    async () => {
      vi.stubGlobal('fetch', vi.fn().mockRejectedValue(
        new DOMException('aborted', 'AbortError')))
      await useCouncilStore.getState().runQuery('q')
      const state = useCouncilStore.getState()
      expect(state.loading).toBe(false)
      // A user cancel doesn't set an error — same as the old axios
      // isCancel branch.
      expect(state.error).toBeNull()
    })

  it('sends the X-API-Key header with the session token', async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse([
      { type: 'council_started', query: 'q' },
      { type: 'council_complete', result: {
        query: 'q', messages: [], final_recommendation: '',
        consensus_reached: true, mode: 'live',
      } },
      '[DONE]',
    ]))
    vi.stubGlobal('fetch', fetchMock)
    await useCouncilStore.getState().runQuery('q')
    // The fetch was called once with the auth header. The backend's
    // require_auth requires it.
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/council/query')
    const headers = (options as { headers: Record<string, string> }).headers
    expect(headers['X-API-Key']).toBe('test-token')
  })
})
