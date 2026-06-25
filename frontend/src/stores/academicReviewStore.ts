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

import type { EditorDocumentType } from '../types/editor'

export type AcademicReviewPhase =
  | 'idle' | 'consulting' | 'streaming' | 'done' | 'error'

// June 23 2026 -- per-document review surfaces. Each editor page
// (Brief / Deck / Appendix / Script) gets its own labeled review
// trigger that POSTs /api/council/academic-review?document_type=X.
// The verdict for each doc type is cached in its own slice so
// switching between editors doesn't drop a recent verdict and a
// stale verdict surfaces a banner. The cross-document review (no
// query param) continues to use the top-level store fields.
export interface PerDocumentReviewSlice {
  result:      AcademicReviewResult | null
  dataHash:    string | null
  completedAt: string | null
  phase:       AcademicReviewPhase
  errorMsg:    string
}

export const EMPTY_PER_DOC_SLICE: PerDocumentReviewSlice = {
  result:      null,
  dataHash:    null,
  completedAt: null,
  phase:       'idle',
  errorMsg:    '',
}

export type IndependentVerdict = 'Plausible' | 'Concerns' | 'Implausible'

export interface IndependentPerFinding {
  finding:    string
  label:      string
  assessment: string
  concern:    string
}

export interface IndependentReview {
  verdict:           IndependentVerdict
  overall_reasoning: string
  per_finding:       IndependentPerFinding[]
  model:             string
  findings_seen:     Record<string, string>
}

// Concern 7 (revised) -- the integrated critic + debate round SSE
// frames. critic_findings carries the structured CriticResult JSON;
// debate_round_arbiter streams the council's response text in
// arbiter_chunk-style chunks; critic_minor_only is an empty marker
// frame the UI uses to render the "no debate -- minor findings only"
// banner.
export type CriticSeverity = 'Fatal' | 'Major' | 'Minor'

export interface CriticFinding {
  severity:        CriticSeverity
  category?:       string
  target_document?: string
  document?:       string
  location?:       string
  description?:    string
  evidence?:       string
  recommendation?: string
  agreed?:         boolean
  raised_by?:      'gemini' | 'grok' | 'both'
}

export interface CriticResult {
  document_scope:  string
  gemini_findings: CriticFinding[]
  grok_findings:   CriticFinding[]
  merged_findings: CriticFinding[]
  gemini_prose:    string
  grok_prose:      string
  fatal_count:     number
  major_count:     number
  minor_count:     number
  partial_failure: boolean
}

export interface AcademicReviewResult {
  /** The full text-event-stream arbiter output as it landed. Parsed
   *  into structured sections by the rendering component via
   *  lib/academicVerdict.ts — we keep the raw form here so the
   *  parsing layer can evolve without re-running the review. */
  arbiterText:   string
  /** {agentId → markdown body} for the peer responses panel. */
  peerResponses: Record<string, string>
  /** Advisory second-opinion verdict from an independent agent
   *  (Gemini Pro). Lands on the `independent_review` SSE frame
   *  after the arbiter completes. Never affects the primary verdict
   *  or any gates — purely informational. Optional in the type so
   *  legacy test fixtures don't need to pass null; runtime store
   *  setters always include the field. Reader-side code defaults
   *  to null on a missing field. */
  independentReview?: IndependentReview | null
  /** Concern 7 (revised) -- the merged critic findings payload
   *  delivered on the `critic_findings` SSE frame. Null when the
   *  critic step never ran (legacy review, frontend that hasn't
   *  refreshed). */
  criticResult?:    CriticResult | null
  /** Concern 7 (revised) -- the debate round arbiter text streamed
   *  on the `debate_round_arbiter` SSE frame. Empty string when
   *  the debate round did not fire (minor-only findings). */
  debateRoundText?: string
  /** Concern 7 (revised) -- True when the backend emitted the
   *  `critic_minor_only` frame (no debate round fired but the
   *  critic still surfaced minor findings). */
  criticMinorOnly?: boolean
  /** Concern 7k -- council_debates row id from the
   *  `debate_recorded` SSE frame. The UI uses this to route
   *  propose-fix and apply-fix calls. */
  debateId?:        number | null
  /** Concern 7k-v -- auto-fired Fatal fix proposals delivered on
   *  the `debate_recorded` frame, keyed by finding_id. */
  fixProposals?:    Record<number, FixProposalPayload>
}

export interface FixProposalPayload {
  finding_id:               number
  target:                   'section' | 'document'
  section_name:             string | null
  rationale:                string
  patch_instruction:        string
  severity:                 string
  auto_proposed:            boolean
  target_document?:         string | null
  source_of_truth_document?: string | null
}

interface AcademicReviewStore {
  result:      AcademicReviewResult | null
  dataHash:    string | null
  completedAt: string | null
  phase:       AcademicReviewPhase
  errorMsg:    string

  // June 23 2026 -- per-document caching, one slice per editor doc
  // type. The keys are the four EditorDocumentType values. Empty
  // slice = no run yet for that doc type.
  perDocument: Record<EditorDocumentType, PerDocumentReviewSlice>
  // Internal -- per-doc abort controllers, keyed the same way.
  _perDocControllers:
    Partial<Record<EditorDocumentType, AbortController>>

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

  // ── Per-document review actions ─────────────────────────────────
  // June 23 2026. The per-document review uses the SAME
  // /api/council/academic-review endpoint with a document_type query
  // param that routes to the doc-specific rubric. Each per-document
  // run lives in its own slice so the per-doc verdicts don't trample
  // each other or the cross-document verdict.

  /** Returns the slice for the given doc type. Reads default to the
   *  EMPTY_PER_DOC_SLICE so consumers can destructure safely. */
  getPerDoc: (docType: EditorDocumentType) => PerDocumentReviewSlice

  /** Starts a per-document review. document_type is the rubric the
   *  backend should apply. Same SSE shape as the cross-document
   *  runReview -- only the URL and the slice that the events land in
   *  differ. Idempotent per slice: a call while THIS doc type's
   *  slice is mid-stream is a no-op. Other slices are unaffected. */
  runPerDocReview: (
    docType:      EditorDocumentType,
    dataHash:     string | null,
    sessionToken: string,
  ) => Promise<void>

  /** Aborts the in-flight per-doc stream for the given doc type.
   *  Other slices keep running. */
  cancelPerDoc: (docType: EditorDocumentType) => void

  /** Drops the cached slice for the given doc type. */
  clearPerDoc: (docType: EditorDocumentType) => void
}


// June 25 2026 -- fallback polling. When the per-document SSE
// connection drops before [DONE] (silence timeout or transient
// network error), the backend pipeline may still be running and
// the council_debates row + agent_interactions row may still land.
// _pollAcademicReviewStatus walks the academic-review-status
// endpoint every POLL_INTERVAL_MS until has_review=true or the
// POLL_CEILING_MS budget is exhausted. On success it populates
// the per-doc slice with the late-arriving verdict so the user
// sees the result even though the SSE stream broke.
const POLL_INTERVAL_MS = 10_000
const POLL_CEILING_MS = 5 * 60_000


async function _resolveCurrentDraftId(
  docType: string,
): Promise<number | null> {
  try {
    const { default: axios } = await import('axios')
    const res = await axios.get<{
      drafts: Array<{
        id: number
        document_type: string
        is_current?: boolean
      }>
    }>('/api/v1/documents/drafts')
    const match = (res.data?.drafts ?? []).find(
      (d) => d.document_type === docType
        && d.is_current !== false)
    return match?.id ?? null
  } catch {
    return null
  }
}


async function _pollAcademicReviewStatus(
  draftId: number,
  docType: EditorDocumentType,
  set: (
    fn: (s: AcademicReviewStore) => Partial<AcademicReviewStore>,
  ) => void,
): Promise<void> {
  const { default: axios } = await import('axios')
  const startedAt = Date.now()
  while (Date.now() - startedAt < POLL_CEILING_MS) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
    try {
      const r = await axios.get<{
        has_review?:      boolean
        last_review_at?:  string | null
        arbiter_score?:   number | null
        verdict_summary?: string | null
      }>(
        `/api/v1/documents/drafts/${draftId}`
        + '/academic-review-status')
      if (r.data?.has_review) {
        // Populate the slice with the late-arriving verdict.
        // verdict_summary lands in arbiterText so the existing
        // renderer surfaces it; the user can re-run the review
        // for the full peer + critic stream.
        set((s) => ({
          perDocument: {
            ...s.perDocument,
            [docType]: {
              ...s.perDocument[docType],
              phase: 'done',
              errorMsg: '',
              completedAt: r.data?.last_review_at
                ?? new Date().toISOString(),
              result: {
                arbiterText: r.data?.verdict_summary
                  ?? 'Review completed but verdict text was not '
                    + 'returned by the fallback status endpoint. '
                    + 'Re-run the review to see the full output.',
                peerResponses: {},
              },
            },
          },
        }))
        return
      }
    } catch {
      // Network blip mid-poll -- keep trying until the ceiling.
    }
  }
}


const EMPTY_PER_DOC_MAP: Record<EditorDocumentType, PerDocumentReviewSlice> = {
  // midpoint_paper stays in EditorDocumentType post-retirement so
  // historical drafts still type-check; seed an empty slice to keep
  // the Record exhaustive (Vercel's tsc --noEmit catches missing keys).
  midpoint_paper:      { ...EMPTY_PER_DOC_SLICE },
  executive_brief:     { ...EMPTY_PER_DOC_SLICE },
  analytical_appendix: { ...EMPTY_PER_DOC_SLICE },
  presentation_deck:   { ...EMPTY_PER_DOC_SLICE },
  presentation_script: { ...EMPTY_PER_DOC_SLICE },
}


export const useAcademicReviewStore = create<AcademicReviewStore>(
  (set, get) => ({
    result:      null,
    dataHash:    null,
    completedAt: null,
    phase:       'idle',
    errorMsg:    '',
    _controller: null,
    perDocument:        { ...EMPTY_PER_DOC_MAP },
    _perDocControllers: {},

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
        let independentReview: IndependentReview | null = null
        // Concern 7 (revised) -- streaming accumulators for the
        // critic + debate-round SSE frames. criticResult lands as
        // a single JSON payload on `critic_findings`. debateRoundText
        // arrives as `debate_round_arbiter` chunks (one chunk per
        // SSE frame, mirrors the arbiter_chunk shape). criticMinorOnly
        // is a marker flag flipped to true by the `critic_minor_only`
        // empty-payload frame.
        let criticResult: CriticResult | null = null
        let debateRoundText = ''
        let criticMinorOnly = false
        let debateId: number | null = null
        const fixProposals: Record<number, FixProposalPayload> = {}
        let phaseSeen: AcademicReviewPhase = 'consulting'

        // June 25 2026 -- rolling timeout. The backend now emits
        // ': keepalive' comment frames every 20s during long
        // pipeline phases (PR earlier in this session). The
        // client resets lastActivity on EVERY chunk read,
        // including those keepalive comment frames; the timer
        // only fires when no chunk has arrived for SILENCE_MS.
        // 60s is comfortably above the 20s keepalive cadence so
        // a single missed keepalive doesn't trip the timeout,
        // but two missed in a row signals a genuinely dropped
        // connection. controller.abort() on timeout makes the
        // next reader.read() throw an AbortError which the outer
        // catch routes to a non-failure 'taking longer than
        // expected' phase so fallback polling can still pick up
        // the result when the backend completes.
        const SILENCE_MS = 60_000
        let lastActivity = Date.now()
        let timedOut = false
        const silenceTimer: ReturnType<typeof setInterval> =
          setInterval(() => {
            if (Date.now() - lastActivity > SILENCE_MS) {
              timedOut = true
              try { controller.abort() } catch { /* noop */ }
              clearInterval(silenceTimer)
            }
          }, 5_000)

        // eslint-disable-next-line no-constant-condition
        while (true) {
          let chunk: { done: boolean; value: Uint8Array | undefined }
          try {
            chunk = await reader.read()
          } catch (readErr) {
            // AbortError fired by the silence timer or by a
            // user-initiated cancel. Either way, exit the loop;
            // the outer catch handles routing (timedOut -> error
            // phase with the non-failure message; cancel ->
            // existing idle phase).
            clearInterval(silenceTimer)
            if (timedOut) {
              throw new Error(
                'Review is taking longer than expected. The '
                + 'council is still deliberating — please wait '
                + 'or retry.')
            }
            throw readErr
          }
          lastActivity = Date.now()
          const { done, value } = chunk
          if (done) {
            clearInterval(silenceTimer)
            break
          }
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
              type?:              string
              data?:              Record<string, string>
              text?:              string
              message?:           string
              verdict?:           IndependentVerdict
              overall_reasoning?: string
              per_finding?:       IndependentPerFinding[]
              model?:             string
              findings_seen?:     Record<string, string>
              // Concern 7 (revised) -- critic + debate frame fields.
              // Loose typing here -- the handler narrows below.
              document_scope?:    string
              gemini_findings?:   unknown[]
              grok_findings?:     unknown[]
              merged_findings?:   unknown[]
              gemini_prose?:      string
              grok_prose?:        string
              fatal_count?:       number
              major_count?:       number
              minor_count?:       number
              partial_failure?:   boolean
            }
            try { evt = JSON.parse(payload) } catch { continue }
            if (evt.type === 'peer_responses') {
              peerResponses = evt.data ?? {}
              phaseSeen = 'streaming'
              set({
                phase:  'streaming',
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'arbiter_chunk') {
              arbiterText += evt.text ?? ''
              set({
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'independent_review') {
              // Advisory second-opinion verdict from Gemini Pro.
              // Lands after the arbiter completes; never affects
              // the primary verdict or any gates.
              independentReview = {
                verdict:           evt.verdict ?? 'Concerns',
                overall_reasoning: evt.overall_reasoning ?? '',
                per_finding:       evt.per_finding ?? [],
                model:             evt.model ?? 'unknown',
                findings_seen:     evt.findings_seen ?? {},
              }
              set({
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'critic_findings') {
              // Concern 7 (revised). Single payload carrying both
              // models' findings + the merged list + severity counts.
              const e = evt as unknown as Record<string, unknown>
              criticResult = {
                document_scope:
                  String(e.document_scope ?? 'full_package'),
                gemini_findings: (
                  e.gemini_findings as CriticFinding[]) ?? [],
                grok_findings:   (
                  e.grok_findings as CriticFinding[]) ?? [],
                merged_findings: (
                  e.merged_findings as CriticFinding[]) ?? [],
                gemini_prose:    String(e.gemini_prose ?? ''),
                grok_prose:      String(e.grok_prose ?? ''),
                fatal_count:     Number(e.fatal_count ?? 0),
                major_count:     Number(e.major_count ?? 0),
                minor_count:     Number(e.minor_count ?? 0),
                partial_failure:
                  Boolean(e.partial_failure),
              }
              set({
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'debate_round_arbiter') {
              debateRoundText += evt.text ?? ''
              set({
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'critic_minor_only') {
              criticMinorOnly = true
              set({
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'debate_recorded') {
              const e = evt as unknown as Record<string, unknown>
              debateId = (e.debate_id as number | null) ?? null
              const incoming = (
                e.fix_proposals as FixProposalPayload[])
                ?? []
              for (const p of incoming) {
                fixProposals[p.finding_id] = p
              }
              set({
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
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
          result:      {
            arbiterText, peerResponses, independentReview,
            criticResult, debateRoundText, criticMinorOnly,
            debateId, fixProposals,
          },
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

    getPerDoc: (docType) => {
      return get().perDocument[docType] ?? EMPTY_PER_DOC_SLICE
    },

    runPerDocReview: async (docType, dataHash, sessionToken) => {
      // Re-entrancy guard for THIS doc type only.
      const slice = get().perDocument[docType]
      if (slice && (slice.phase === 'consulting'
        || slice.phase === 'streaming')) {
        return
      }

      const controller = new AbortController()
      set((s) => ({
        perDocument: {
          ...s.perDocument,
          [docType]: {
            ...EMPTY_PER_DOC_SLICE,
            phase:    'consulting',
            dataHash,
          },
        },
        _perDocControllers: {
          ...s._perDocControllers,
          [docType]: controller,
        },
      }))

      // Hoisted to the function scope so the catch block can read it
      // and distinguish a silence-timeout abort from a user-initiated
      // cancel. The inner try block flips this to true via the
      // setInterval below.
      let timedOut = false

      try {
        const res = await fetch(
          `/api/council/academic-review?document_type=${
            encodeURIComponent(docType)}`,
          {
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
        let independentReview: IndependentReview | null = null
        // Concern 7 (revised) -- same critic + debate accumulators
        // as the cross-document path.
        let criticResult: CriticResult | null = null
        let debateRoundText = ''
        let criticMinorOnly = false
        let debateId: number | null = null
        const fixProposals: Record<number, FixProposalPayload> = {}

        const writeSlice = (partial: Partial<PerDocumentReviewSlice>):
          void => {
          set((s) => ({
            perDocument: {
              ...s.perDocument,
              [docType]: { ...s.perDocument[docType], ...partial },
            },
          }))
        }

        // Rolling silence timeout, mirrors the cross-document path
        // above. See the SILENCE_MS comment there for the rationale.
        const SILENCE_MS = 60_000
        let lastActivity = Date.now()
        const silenceTimer: ReturnType<typeof setInterval> =
          setInterval(() => {
            if (Date.now() - lastActivity > SILENCE_MS) {
              timedOut = true
              try { controller.abort() } catch { /* noop */ }
              clearInterval(silenceTimer)
            }
          }, 5_000)

        // eslint-disable-next-line no-constant-condition
        while (true) {
          let chunk: { done: boolean; value: Uint8Array | undefined }
          try {
            chunk = await reader.read()
          } catch (readErr) {
            clearInterval(silenceTimer)
            if (timedOut) {
              throw new Error(
                'Review is taking longer than expected. The '
                + 'council is still deliberating — please wait '
                + 'or retry.')
            }
            throw readErr
          }
          lastActivity = Date.now()
          const { done, value } = chunk
          if (done) {
            clearInterval(silenceTimer)
            break
          }
          buffer += decoder.decode(value, { stream: true })
          let sep: number
          while ((sep = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, sep).trim()
            buffer = buffer.slice(sep + 2)
            if (!frame.startsWith('data:')) continue
            const payload = frame.slice(5).trim()
            if (payload === '[DONE]') continue
            let evt: {
              type?:              string
              data?:              Record<string, string>
              text?:              string
              message?:           string
              verdict?:           IndependentVerdict
              overall_reasoning?: string
              per_finding?:       IndependentPerFinding[]
              model?:             string
              findings_seen?:     Record<string, string>
              document_scope?:    string
              gemini_findings?:   unknown[]
              grok_findings?:     unknown[]
              merged_findings?:   unknown[]
              gemini_prose?:      string
              grok_prose?:        string
              fatal_count?:       number
              major_count?:       number
              minor_count?:       number
              partial_failure?:   boolean
            }
            try { evt = JSON.parse(payload) } catch { continue }
            if (evt.type === 'peer_responses') {
              peerResponses = evt.data ?? {}
              writeSlice({
                phase: 'streaming',
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'arbiter_chunk') {
              arbiterText += evt.text ?? ''
              writeSlice({
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'independent_review') {
              independentReview = {
                verdict:           evt.verdict ?? 'Concerns',
                overall_reasoning: evt.overall_reasoning ?? '',
                per_finding:       evt.per_finding ?? [],
                model:             evt.model ?? 'unknown',
                findings_seen:     evt.findings_seen ?? {},
              }
              writeSlice({
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'critic_findings') {
              const e = evt as unknown as Record<string, unknown>
              criticResult = {
                document_scope:
                  String(e.document_scope ?? 'full_package'),
                gemini_findings: (
                  e.gemini_findings as CriticFinding[]) ?? [],
                grok_findings:   (
                  e.grok_findings as CriticFinding[]) ?? [],
                merged_findings: (
                  e.merged_findings as CriticFinding[]) ?? [],
                gemini_prose:    String(e.gemini_prose ?? ''),
                grok_prose:      String(e.grok_prose ?? ''),
                fatal_count:     Number(e.fatal_count ?? 0),
                major_count:     Number(e.major_count ?? 0),
                minor_count:     Number(e.minor_count ?? 0),
                partial_failure:
                  Boolean(e.partial_failure),
              }
              writeSlice({
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'debate_round_arbiter') {
              debateRoundText += evt.text ?? ''
              writeSlice({
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'critic_minor_only') {
              criticMinorOnly = true
              writeSlice({
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'debate_recorded') {
              const e = evt as unknown as Record<string, unknown>
              debateId = (e.debate_id as number | null) ?? null
              const incoming = (
                e.fix_proposals as FixProposalPayload[])
                ?? []
              for (const p of incoming) {
                fixProposals[p.finding_id] = p
              }
              writeSlice({
                result: {
                  arbiterText, peerResponses, independentReview,
                  criticResult, debateRoundText, criticMinorOnly,
                  debateId, fixProposals,
                },
              })
            } else if (evt.type === 'error') {
              writeSlice({
                phase:    'error',
                errorMsg: evt.message ?? 'Per-document review failed.',
              })
              return
            }
          }
        }

        writeSlice({
          phase:       'done',
          result:      {
            arbiterText, peerResponses, independentReview,
            criticResult, debateRoundText, criticMinorOnly,
            debateId, fixProposals,
          },
          completedAt: new Date().toISOString(),
        })
      } catch (err) {
        if (controller.signal.aborted && !timedOut) {
          // User-initiated cancel (not the silence-timer abort).
          set((s) => ({
            perDocument: {
              ...s.perDocument,
              [docType]: {
                ...s.perDocument[docType], phase: 'idle',
              },
            },
          }))
          return
        }
        // June 25 2026 -- fallback status polling. The SSE
        // connection dropped (timeout or transient error) but the
        // backend may still complete; poll
        // /api/v1/documents/drafts/{id}/academic-review-status
        // until has_review=true OR the 5-minute ceiling fires.
        // On success, populate the slice with the late-arriving
        // verdict so the user sees the result even though the SSE
        // stream broke. Skipped when no draft_id can be resolved
        // for this docType.
        const errMsg = err instanceof Error
          ? err.message : 'Per-document review failed.'
        set((s) => ({
          perDocument: {
            ...s.perDocument,
            [docType]: {
              ...s.perDocument[docType],
              phase:    'error',
              errorMsg: errMsg,
            },
          },
        }))
        try {
          const draftId = await _resolveCurrentDraftId(docType)
          if (draftId !== null) {
            void _pollAcademicReviewStatus(
              draftId, docType, set)
          }
        } catch { /* polling kickoff failed -- error phase stands */ }
      } finally {
        set((s) => {
          const next = { ...s._perDocControllers }
          delete next[docType]
          return { _perDocControllers: next }
        })
      }
    },

    cancelPerDoc: (docType) => {
      const ctrl = get()._perDocControllers[docType]
      if (ctrl) ctrl.abort()
      set((s) => ({
        perDocument: {
          ...s.perDocument,
          [docType]: { ...s.perDocument[docType], phase: 'idle' },
        },
      }))
    },

    clearPerDoc: (docType) => {
      const ctrl = get()._perDocControllers[docType]
      if (ctrl) ctrl.abort()
      set((s) => {
        const ctrls = { ...s._perDocControllers }
        delete ctrls[docType]
        return {
          perDocument: {
            ...s.perDocument,
            [docType]: { ...EMPTY_PER_DOC_SLICE },
          },
          _perDocControllers: ctrls,
        }
      })
    },
  }),
)
