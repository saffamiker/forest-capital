/**
 * frontend/src/stores/councilStore.ts
 *
 * Persists the most recent council query and response for the session.
 * When the user navigates away from the Council tab and returns, they
 * see the previous response immediately rather than an empty screen or
 * a duplicate API call. A new query replaces the previous response.
 *
 * runQuery() consumes a Server-Sent Events stream from
 * POST /api/council/query (the backend replaced its synchronous JSON
 * response with SSE in May 2026 to fix Render gateway 502s — the
 * deliberation routinely runs 50-100s and was being killed by the
 * proxy timeout). Specialist responses arrive one-by-one as each
 * agent completes; the CouncilDebate.tsx component renders them
 * progressively because it iterates a fixed agent order and reads
 * each agent's message from the array as it lands.
 *
 * SSE event types consumed (matches main.py:_chunk_synthesis +
 * council_query):
 *   council_started      — fires immediately; we already initialised
 *                          the result skeleton, no-op here
 *   specialist_complete  — append the agent's message
 *   draft_ready          — internal to the CIO, no UI surface
 *   dissent_complete     — append Gemini / Grok message
 *   synthesis_chunk      — accumulate into the CIO message,
 *                          rendering progressively
 *   council_complete     — replace the partial state with the
 *                          authoritative final dict (which
 *                          _deliberate_to_frontend assembles on
 *                          the backend)
 *   council_error        — surface as an error state with a retry-
 *                          friendly result.error flag
 *   [DONE]               — end-of-stream sentinel
 *
 * Auth: the backend's require_auth uses the X-API-Key header (same
 * token the AcademicReviewButton uses for SSE). fetch is used
 * instead of axios because axios doesn't expose ReadableStream on
 * the response body.
 */

import { create } from 'zustand'
import { useGlossaryStore } from './glossaryStore'
import type { AgentMessage, CouncilResponse } from '../types/agents'

interface CouncilResult extends CouncilResponse {
  error?: boolean
}

interface CouncilState {
  query: string                  // current text in the input box
  lastQuery: string              // the query that produced `result`
  result: CouncilResult | null   // last response (survives navigation)
  loading: boolean
  error: string | null
  // Viewer council allocation — {used, limit} for a limited user, null
  // for an unlimited user (or until the first query/me-fetch resolves).
  councilUsage: { used: number; limit: number } | null

  setQuery: (q: string) => void
  runQuery: (q: string) => Promise<void>
  abort: () => void
  clear: () => void
}

// Module-level so abort() can reach the in-flight request without
// threading a non-serialisable AbortController through Zustand state.
let _controller: AbortController | null = null

// Mirror of backend _AGENT_META (main.py:1671). Keep these in sync —
// the backend SSE emits snake_case agent_ids, and the frontend's
// CouncilDebate.tsx renders by display name (AGENT_STYLE there is
// keyed by these exact display strings). Touching either side without
// the other will produce blank council cards.
interface AgentMeta { label: string; role: string; model: string }
const AGENT_META: Record<string, AgentMeta> = {
  equity_analyst:       { label: 'Equity Analyst',
                          role: 'specialist', model: 'claude-sonnet-4-6' },
  fixed_income_analyst: { label: 'Fixed Income Analyst',
                          role: 'specialist', model: 'claude-sonnet-4-6' },
  risk_manager:         { label: 'Risk Manager',
                          role: 'specialist', model: 'claude-sonnet-4-6' },
  quant_backtester:     { label: 'Quant Backtester',
                          role: 'specialist', model: 'claude-sonnet-4-6' },
  independent_analyst:  { label: 'Independent Analyst (Gemini)',
                          role: 'dissenter',  model: 'gemini-2.0-flash' },
  contrarian_analyst:   { label: 'Contrarian Analyst (Grok)',
                          role: 'dissenter',  model: 'grok-4.3' },
  cio:                  { label: 'CIO',
                          role: 'cio',        model: 'claude-opus-4-7' },
}

// SSE event payload shapes — typed narrowly so the dispatch is
// exhaustive. The endpoint's _sse helper serialises {type, ...payload}
// so every frame carries `type`.
type SseSpecialist = {
  type: 'specialist_complete'
  agent_id: string
  response: Record<string, unknown> | null
}
type SseDraft = { type: 'draft_ready'; draft: string }
type SseDissent = {
  type: 'dissent_complete'
  source: 'gemini' | 'grok'
  challenge: Record<string, unknown> | null
}
type SseSynthesis = { type: 'synthesis_chunk'; text: string }
type SseComplete = { type: 'council_complete'; result: CouncilResponse }
type SseError = { type: 'council_error'; message: string }
type SseStarted = { type: 'council_started'; query: string }
type SseEvent = SseStarted | SseSpecialist | SseDraft | SseDissent
              | SseSynthesis | SseComplete | SseError

/**
 * Builds an AgentMessage for the council_started skeleton or a
 * specialist_complete event. Selects content the same way the backend
 * _deliberate_to_frontend does (raw_analysis → summary → placeholder),
 * so the partial state matches what council_complete will replace it
 * with — minimising visual flicker at the swap.
 */
function specialistToMessage(
  agentId: string, response: Record<string, unknown> | null,
): AgentMessage {
  const meta = AGENT_META[agentId] ?? { label: agentId, role: 'agent', model: '' }
  const tech = ((response as { technical_findings?: Record<string, unknown> })
    ?.technical_findings ?? {}) as Record<string, unknown>
  const content = String(
    tech.raw_analysis
      ?? (response as { summary?: string })?.summary
      ?? '(Narrative unavailable — agent ran but no text returned.)'
  )
  return { agent: meta.label, role: meta.role, model: meta.model,
           content, is_final: false }
}

/**
 * Builds an AgentMessage for a Gemini / Grok dissent_complete event.
 * Same content-selection contract as the backend (technical_findings
 * .full_challenge → summary → placeholder).
 */
function dissentToMessage(
  source: 'gemini' | 'grok', report: Record<string, unknown> | null,
): AgentMessage {
  const agentId = source === 'gemini' ? 'independent_analyst' : 'contrarian_analyst'
  const meta = AGENT_META[agentId]
  const tech = ((report as { technical_findings?: Record<string, unknown> })
    ?.technical_findings ?? {}) as Record<string, unknown>
  const content = String(
    tech.full_challenge
      ?? (report as { summary?: string })?.summary
      ?? '(Challenge unavailable.)'
  )
  return { agent: meta.label, role: meta.role, model: meta.model,
           content, is_final: false }
}

/**
 * Appends or replaces a message in the partial result.messages array.
 * Specialists and dissenters APPEND; the CIO synthesis arrives as
 * chunks and REPLACES the running CIO message so its content grows
 * progressively in place.
 */
function withMessage(
  state: CouncilResult | null, msg: AgentMessage, replaceCio: boolean,
): CouncilResult | null {
  if (!state) return null
  const filtered = replaceCio
    ? state.messages.filter((m) => m.role !== 'cio')
    : state.messages
  return { ...state, messages: [...filtered, msg] }
}

export const useCouncilStore = create<CouncilState>((set, get) => ({
  query: '',
  lastQuery: '',
  result: null,
  loading: false,
  error: null,
  councilUsage: null,

  setQuery: (q) => set({ query: q }),

  runQuery: async (q) => {
    const trimmed = q.trim()
    if (!trimmed || get().loading) return
    _controller = new AbortController()
    // Initialise the result skeleton so CouncilDebate.tsx renders
    // empty council slots while specialists stream in, instead of
    // a static "no result yet" placeholder.
    set({
      loading: true,
      error: null,
      lastQuery: trimmed,
      result: {
        query: trimmed,
        messages: [],
        final_recommendation: '',
        consensus_reached: false,
        mode: 'live',
      },
    })

    // Accumulator for synthesis_chunk frames — each chunk grows the
    // CIO message in place via withMessage(..., replaceCio=true).
    let synthesisAcc = ''
    // Flag set when council_complete fires so an early stream close
    // (no [DONE]) is treated as an error rather than a clean finish.
    let completed = false

    try {
      const token = localStorage.getItem('fc_session_token') ?? ''
      const res = await fetch('/api/council/query', {
        method: 'POST',
        headers: {
          'X-API-Key': token,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ query: trimmed }),
        signal: _controller.signal,
      })

      // 429 council_limit_reached — same blocked-state outcome as the
      // axios path. The detail block carries limit + used so the screen
      // shows the contact-Michael message with the right numbers.
      if (res.status === 429) {
        const data = await res.json().catch(() => null) as
          | { detail?: { error?: string; limit?: number; used?: number } }
          | null
        const d = data?.detail
        if (d && d.error === 'council_limit_reached') {
          set({
            loading: false,
            error: 'council_limit_reached',
            councilUsage: { used: Number(d.used), limit: Number(d.limit) },
            result: {
              error: true, query: trimmed, messages: [],
              final_recommendation: '', consensus_reached: false,
            },
          })
          return
        }
      }

      if (!res.ok || !res.body) {
        throw new Error(`Council request failed (${res.status})`)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let streamDone = false

      // SSE framing: each frame ends with \n\n. A `data: [DONE]\n\n`
      // sentinel from the server signals clean completion; we also
      // honour a stream-level close (reader.read returns done=true).
      // eslint-disable-next-line no-constant-condition
      while (!streamDone) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        let sep: number
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sep).trim()
          buffer = buffer.slice(sep + 2)
          if (!frame.startsWith('data:')) continue
          const payload = frame.slice(5).trim()
          if (payload === '[DONE]') {
            streamDone = true
            continue
          }
          let evt: SseEvent
          try {
            evt = JSON.parse(payload) as SseEvent
          } catch {
            continue
          }

          if (evt.type === 'council_started') {
            // Skeleton is already set above — nothing to do here.
            // The frame exists to flush bytes immediately so the
            // gateway sees activity before the heavy data loads.
          } else if (evt.type === 'specialist_complete') {
            const msg = specialistToMessage(evt.agent_id, evt.response)
            set((state) => ({
              result: withMessage(state.result, msg, false),
            }))
          } else if (evt.type === 'draft_ready') {
            // Draft consensus is internal to the CIO — not rendered.
          } else if (evt.type === 'dissent_complete') {
            const msg = dissentToMessage(evt.source, evt.challenge)
            set((state) => ({
              result: withMessage(state.result, msg, false),
            }))
          } else if (evt.type === 'synthesis_chunk') {
            synthesisAcc += evt.text
            const meta = AGENT_META.cio
            const cioMsg: AgentMessage = {
              agent: meta.label, role: meta.role, model: meta.model,
              content: synthesisAcc, is_final: true,
            }
            set((state) => ({
              result: withMessage(state.result, cioMsg, true),
            }))
          } else if (evt.type === 'council_complete') {
            // Replace partial state with the canonical backend-assembled
            // shape — _deliberate_to_frontend has run, the council
            // allocation fields are included, and content selection
            // matches the previous synchronous response exactly.
            const final = evt.result as CouncilResponse
            const usage = final.council_queries_limit != null
              ? { used: final.council_queries_used ?? 0,
                  limit: final.council_queries_limit }
              : get().councilUsage
            set({ result: final, loading: false, councilUsage: usage })
            completed = true
            // Re-anchor glossary — same call the old synchronous path
            // made on completion. Fire-and-forget; loadTerms is
            // single-flight + 60-second-debounced internally.
            void useGlossaryStore.getState().loadTerms(
              final as unknown as Record<string, unknown>)
          } else if (evt.type === 'council_error') {
            set({
              loading: false,
              error: String(evt.message ?? 'Council query failed'),
              result: {
                error: true, query: trimmed, messages: [],
                final_recommendation: '', consensus_reached: false,
              },
            })
            completed = true
          }
        }
      }

      // The stream closed without council_complete or council_error —
      // treat as a partial/failed run. Surface as an error so the UI
      // does not stay stuck loading.
      if (!completed) {
        set({
          loading: false,
          error: 'Council stream ended before completion. Please retry.',
          result: {
            error: true, query: trimmed, messages: [],
            final_recommendation: '', consensus_reached: false,
          },
        })
      }
    } catch (err) {
      // A user-initiated cancel is not an error — clear loading.
      if (_controller?.signal.aborted
          || (err instanceof DOMException && err.name === 'AbortError')) {
        set({ loading: false })
        return
      }
      const msg = err instanceof Error ? err.message : 'Council query failed'
      set({
        loading: false,
        error: String(msg),
        result: {
          error: true, query: trimmed, messages: [],
          final_recommendation: '', consensus_reached: false,
        },
      })
    } finally {
      _controller = null
    }
  },

  abort: () => {
    _controller?.abort()
    _controller = null
    set({ loading: false })
  },

  clear: () => {
    _controller?.abort()
    _controller = null
    set({ query: '', lastQuery: '', result: null, loading: false, error: null })
  },
}))
