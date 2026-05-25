/**
 * academicReviewStore — Zustand cache for the Academic Review verdict
 * keyed on data_hash, in-memory only.
 *
 * May 28 2026. Background: Academic Review previously lived on the
 * Council screen and held its state in component-local useState, so
 * the verdict reset every time the user navigated away. The panel
 * also moved to the QA Audit page in this PR, alongside the
 * Methodology Review and Statistical Audit. Putting the state in a
 * Zustand store survives the navigation; keying on data_hash makes
 * sure a stale verdict (from a previous data ingestion) never
 * renders against newer numbers — when the hash flips, the store
 * recognises the verdict no longer matches and surfaces the result
 * as stale.
 *
 * NOT PERSISTED. In-memory only, session-lifetime. A page reload
 * starts fresh — same lifecycle policy as the other in-memory stores
 * (councilStore, qaStore). A persisted Academic Review would risk
 * showing a verdict generated against last-week's data on today's
 * dashboard.
 *
 * CONTRACT
 *   result        the parsed verdict + peer responses + raw arbiter text
 *   dataHash      the audit data_hash the verdict was generated against
 *   completedAt   ISO timestamp of when the run finished
 *   phase         idle | consulting | streaming | done | error
 *   errorMsg      surfaced verbatim when phase === 'error'
 *
 *   runReview(dataHash)  starts a fresh review; clears prior state
 *   cancel()             aborts the in-flight stream (no state change
 *                        beyond resetting phase to idle)
 *   clear()              drops every cached field (the user-facing
 *                        "Run again" path consumes this first, then
 *                        calls runReview)
 *   isCurrentFor(hash)   true when the cached verdict matches the
 *                        supplied hash AND a result has actually
 *                        landed (phase === 'done'). Mount-time render
 *                        check.
 */
import { create } from 'zustand'

export type AcademicReviewPhase =
  | 'idle' | 'consulting' | 'streaming' | 'done' | 'error'

export interface AcademicReviewResult {
  /** The full text-event-stream arbiter output as it landed. Parsed
   *  into structured sections by the rendering component via
   *  lib/academicVerdict.ts — we keep the raw form here so the
   *  parsing layer can evolve without re-running the review. */
  arbiterText:   string
  /** {agentId → markdown body} for the peer responses panel. */
  peerResponses: Record<string, string>
}

interface AcademicReviewStore {
  result:      AcademicReviewResult | null
  dataHash:    string | null
  completedAt: string | null
  phase:       AcademicReviewPhase
  errorMsg:    string

  // Internals — exposed for the cancel path; production callers use
  // the action helpers below.
  _controller: AbortController | null

  /** True when a cached verdict matches the supplied dataHash and the
   *  most recent run completed (phase === 'done'). The mounted
   *  AcademicReviewSection consults this on every render to decide
   *  whether to skip a re-run. */
  isCurrentFor: (dataHash: string | null) => boolean

  /** Starts a fresh review. Clears prior result + peerResponses
   *  before kicking the request. Idempotent — a call while a run is
   *  already in flight is a no-op. Resolves when the SSE stream
   *  terminates (or the cancel button fires). */
  runReview: (dataHash: string | null, sessionToken: string) => Promise<void>

  /** Aborts the in-flight SSE reader. Sets phase to idle; the
   *  result + dataHash state is preserved (a cancelled run keeps
   *  whatever partial output had landed so the user can review what
   *  the arbiter said before they pulled the plug). */
  cancel: () => void

  /** Drops every cached field. The user's "Re-run" gesture: clear
   *  state, then runReview kicks a fresh fetch. */
  clear: () => void
}


export const useAcademicReviewStore = create<AcademicReviewStore>(
  (set, get) => ({
    result:      null,
    dataHash:    null,
    completedAt: null,
    phase:       'idle',
    errorMsg:    '',
    _controller: null,

    isCurrentFor: (dataHash: string | null): boolean => {
      const s = get()
      if (s.phase !== 'done') return false
      if (s.result === null) return false
      // A null dataHash on either side counts as "we don't know" — the
      // safe default is to NOT auto-render the cached verdict against
      // an unknown hash. The user can still re-run explicitly.
      if (!dataHash || !s.dataHash) return false
      return s.dataHash === dataHash
    },

    runReview: async (dataHash, sessionToken) => {
      // Re-entrancy guard — never kick a second SSE stream while
      // one is already running. The UI also disables the button,
      // but defence in depth.
      const current = get()
      if (current.phase === 'consulting' || current.phase === 'streaming') {
        return
      }

      const controller = new AbortController()
      set({
        phase:        'consulting',
        result:       null,
        completedAt:  null,
        errorMsg:     '',
        _controller:  controller,
        // dataHash is captured AT START so the streaming result
        // commits to the hash it was generated against, even if the
        // upstream hash changes mid-stream. Stale verdicts get
        // surfaced as stale rather than silently re-keyed.
        dataHash,
      })

      try {
        const res = await fetch('/api/council/academic-review', {
          method: 'POST',
          headers: { 'X-API-Key': sessionToken },
          signal: controller.signal,
        })
        if (!res.ok || !res.body) {
          throw new Error(`Request failed (${res.status})`)
        }

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let arbiterText = ''
        let peerResponses: Record<string, string> = {}
        let phaseSeen: AcademicReviewPhase = 'consulting'

        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          let sep: number
          // SSE frames are separated by a blank line.
          while ((sep = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, sep).trim()
            buffer = buffer.slice(sep + 2)
            if (!frame.startsWith('data:')) continue
            const payload = frame.slice(5).trim()
            if (payload === '[DONE]') {
              phaseSeen = 'done'
              continue
            }
            let evt: {
              type?:    string
              data?:    Record<string, string>
              text?:    string
              message?: string
            }
            try { evt = JSON.parse(payload) } catch { continue }
            if (evt.type === 'peer_responses') {
              peerResponses = evt.data ?? {}
              phaseSeen = 'streaming'
              set({
                phase:  'streaming',
                result: { arbiterText, peerResponses },
              })
            } else if (evt.type === 'arbiter_chunk') {
              arbiterText += evt.text ?? ''
              set({
                result: { arbiterText, peerResponses },
              })
            } else if (evt.type === 'error') {
              set({
                phase:    'error',
                errorMsg: evt.message ?? 'Academic review failed.',
              })
              return
            }
          }
        }

        // Stream ended. Finalise — even if no explicit [DONE] frame
        // arrived, the closed reader means the server is done. The
        // error path inside the loop returns early, so phaseSeen is
        // guaranteed to be on the done track by here.
        void phaseSeen  // captured for tracing only
        set({
          phase:       'done',
          result:      { arbiterText, peerResponses },
          completedAt: new Date().toISOString(),
          _controller: null,
        })
      } catch (err) {
        // AbortController.abort() raises a DOMException on the reader
        // — that's the user's cancel, not an error path.
        if (controller.signal.aborted) {
          set({ phase: 'idle', _controller: null })
          return
        }
        set({
          phase:       'error',
          errorMsg:    err instanceof Error
                         ? err.message
                         : 'Academic review failed.',
          _controller: null,
        })
      }
    },

    cancel: () => {
      const ctrl = get()._controller
      if (ctrl) ctrl.abort()
      set({ phase: 'idle', _controller: null })
    },

    clear: () => {
      // Cancel any in-flight stream too — the user's Re-run gesture
      // implies "drop the old run, start over."
      const ctrl = get()._controller
      if (ctrl) ctrl.abort()
      set({
        result:      null,
        dataHash:    null,
        completedAt: null,
        phase:       'idle',
        errorMsg:    '',
        _controller: null,
      })
    },
  }),
)
