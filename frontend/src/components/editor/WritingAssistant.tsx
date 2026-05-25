/**
 * WritingAssistant — the editor's right panel (300px, collapsible).
 *
 * Top: Run Academic Review — streams the council's verdict against the
 * current draft. A warning shows first if unresolved [[VERIFY]] / [[BOB]]
 * markers remain. Below: an AI chat that answers writing questions with
 * the draft's text as context (the document-assistant endpoint).
 */
import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import {
  GraduationCap, Loader2, Send, AlertTriangle, Sparkles,
  ClipboardCheck, Check, X,
} from 'lucide-react'

import Markdown from '../Markdown'

import type { EditorDocumentType } from '../../types/editor'


// May 25 2026 — Review Against Rubric. A single fast Gemini call against
// the FNA 670 midpoint rubric. Returns per-section pass/fail, optional
// edits with reasoning, and an overall readiness verdict. Distinct from
// the Run Academic Review path (the full council pass, expensive).
type RubricVerdict = 'pass' | 'fail'
type OverallVerdict = 'ready' | 'needs_work' | 'not_ready'

interface RubricSection {
  verdict: RubricVerdict
  reasoning: string
}

interface RubricEdit {
  section: string
  suggestion: string
  reasoning: string
}

interface RubricReview {
  sections: Record<string, RubricSection>
  edits: RubricEdit[]
  overall: { verdict: OverallVerdict; reasoning: string }
  unavailable?: boolean
}

const RUBRIC_SECTION_LABEL: Record<string, string> = {
  methodology: 'Data and Methodology',
  results:     'Preliminary Results',
  roles:       'Roles and Division of Labor',
  next_steps:  'Next Steps and Open Questions',
}

const OVERALL_LABEL: Record<OverallVerdict, string> = {
  ready:      'Ready to submit',
  needs_work: 'Needs work',
  not_ready:  'Not ready',
}

const OVERALL_STYLE: Record<OverallVerdict,
                            { bg: string; text: string; border: string }> = {
  ready:      { bg: 'bg-success/10',  text: 'text-success', border: 'border-success/40' },
  needs_work: { bg: 'bg-warning/10',  text: 'text-warning', border: 'border-warning/40' },
  not_ready:  { bg: 'bg-danger/10',   text: 'text-danger',  border: 'border-danger/40' },
}

interface Props {
  draftId: number
  unresolvedMarkers: number
  /** A passage to drop into the chat input — set by the editor's
   *  "Ask AI" selection action. The nonce re-triggers on each request. */
  prefill?: { text: string; nonce: number } | null
  /** The draft's type — drives a per-type note below the Review button
   *  (e.g. the script editor reminds the user that Academic Review is
   *  optimised for written submissions). */
  documentType?: EditorDocumentType | undefined
}

const SUGGESTED = [
  'Strengthen my conclusion',
  'Is my argument coherent?',
  'Improve the flow of Section 2',
  'Check my methodology section',
]

interface ChatMessage {
  role: 'user' | 'assistant'
  text: string
}

export default function WritingAssistant({
  draftId, unresolvedMarkers, prefill, documentType,
}: Props) {
  const [reviewPhase, setReviewPhase] =
    useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [verdict, setVerdict] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  // May 25 2026 — Review Against Rubric state. Separate from the
  // Run Academic Review path (verdict/reviewPhase) so a user can
  // run both passes independently and see both results side by side.
  const [rubricPhase, setRubricPhase] =
    useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [rubricReview, setRubricReview] = useState<RubricReview | null>(null)
  const [rubricError, setRubricError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const inputRef = useRef<HTMLInputElement | null>(null)

  // The editor's "Ask AI" action pushes a quoted passage into the input.
  useEffect(() => {
    if (prefill?.text) {
      setInput(prefill.text)
      inputRef.current?.focus()
    }
  }, [prefill?.nonce])  // eslint-disable-line react-hooks/exhaustive-deps

  const runReview = async () => {
    setReviewPhase('running')
    setVerdict('')
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const token = localStorage.getItem('fc_session_token') ?? ''
      // For a presentation_script draft, signal to the endpoint that
      // the SCRIPT-SPECIFIC rubric should be applied — coherence /
      // clarity / coverage / speaker differentiation, skipping the
      // written-submission criteria (citation formatting, paragraph
      // structure). Other document types use the default rubric.
      const qs = documentType === 'presentation_script'
        ? '?document_type=presentation_script' : ''
      const res = await fetch(`/api/council/academic-review${qs}`, {
        method: 'POST',
        headers: { 'X-API-Key': token },
        signal: controller.signal,
      })
      if (!res.ok || !res.body) throw new Error(`Request failed (${res.status})`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let arbiter = ''
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let sep: number
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sep).trim()
          buffer = buffer.slice(sep + 2)
          if (!frame.startsWith('data:')) continue
          const payload = frame.slice(5).trim()
          if (payload === '[DONE]') { setReviewPhase('done'); continue }
          try {
            const evt = JSON.parse(payload) as { type?: string; text?: string }
            if (evt.type === 'arbiter_chunk' && evt.text) {
              arbiter += evt.text
              setVerdict(arbiter)
            }
          } catch { /* ignore a partial frame */ }
        }
      }
      setReviewPhase('done')
    } catch {
      setReviewPhase('error')
    }
  }

  const runRubricReview = async () => {
    setRubricPhase('running')
    setRubricError(null)
    setRubricReview(null)
    try {
      const res = await axios.post<RubricReview>(
        `/api/v1/documents/drafts/${draftId}/rubric-review`)
      setRubricReview(res.data)
      setRubricPhase('done')
    } catch (err) {
      // 422 with detail (e.g. wrong document_type) gets shown verbatim;
      // any other failure falls through to a generic retry message.
      let msg = 'The rubric review is unavailable — please retry.'
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail
        if (typeof detail === 'string' && detail) msg = detail
      }
      setRubricError(msg)
      setRubricPhase('error')
    }
  }

  const send = async (text: string) => {
    const message = text.trim()
    if (!message || chatLoading) return
    // The last six exchanges travel as history — enough context without
    // token bloat.
    const history = messages.slice(-6).map((m) => ({
      role: m.role, content: m.text,
    }))
    setMessages((m) => [...m, { role: 'user', text: message }])
    setInput('')
    setChatLoading(true)
    try {
      const res = await axios.post(
        `/api/v1/documents/drafts/${draftId}/chat`,
        { message, history, selection: null })
      const reply = res.data?.response || 'No response.'
      setMessages((m) => [...m, { role: 'assistant', text: reply }])
    } catch {
      setMessages((m) => [...m,
        { role: 'assistant', text: 'The assistant is unavailable — try again.' }])
    } finally {
      setChatLoading(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto p-3 space-y-4">
      {/* Academic Review */}
      <div>
        {unresolvedMarkers > 0 && (
          <div className="flex items-start gap-1.5 text-2xs text-warning
                          bg-warning/10 border border-warning/30 rounded
                          px-2 py-1.5 mb-2">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>
              You have {unresolvedMarkers} unresolved marker
              {unresolvedMarkers === 1 ? '' : 's'}. Running the review
              anyway — resolve markers for a stronger verdict.
            </span>
          </div>
        )}
        <button type="button" onClick={runReview}
          disabled={reviewPhase === 'running'}
          data-tour="editor-academic-review"
          className="w-full flex items-center justify-center gap-1.5 text-xs
                     bg-warning/15 text-warning border border-warning/40
                     rounded py-2 hover:bg-warning/25 disabled:opacity-60">
          {reviewPhase === 'running'
            ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Consulting the council…</>
            : <><GraduationCap className="w-3.5 h-3.5" /> Run Academic Review</>}
        </button>
        {documentType === 'presentation_script' && (
          // Script-specific framing — the arbiter now applies a rubric
          // tuned for spoken delivery. Surface what it DOES evaluate
          // so the presenter reads the verdict correctly.
          <p className="text-2xs text-muted mt-1.5 leading-relaxed">
            Academic Review for presentation scripts evaluates argument
            coherence, audience clarity, and slide coverage. Formatting
            scores do not apply.
          </p>
        )}
        {reviewPhase === 'error' && (
          <p className="text-2xs text-danger mt-1">
            The review could not be completed — please retry.
          </p>
        )}
        {verdict && (
          <div className="mt-2 card p-2.5 text-xs max-h-72 overflow-y-auto">
            <Markdown content={verdict} />
          </div>
        )}
      </div>

      {/* Review Against Rubric (May 25 2026) — Gemini single-call pass
          against the FNA 670 midpoint rubric. Scoped to midpoint_paper
          drafts; the button is hidden for other document types. */}
      {documentType === 'midpoint_paper' && (
        <div className="border-t border-border pt-3">
          <button
            type="button"
            onClick={() => void runRubricReview()}
            disabled={rubricPhase === 'running'}
            data-testid="rubric-review-button"
            className="w-full flex items-center justify-center gap-1.5
                       text-xs bg-electric/10 text-electric
                       border border-electric/40 rounded py-2
                       hover:bg-electric/20 disabled:opacity-60">
            {rubricPhase === 'running'
              ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Reviewing against rubric…</>
              : <><ClipboardCheck className="w-3.5 h-3.5" /> Review Against Rubric</>}
          </button>
          <p className="text-2xs text-muted mt-1.5 leading-relaxed">
            Sends the current draft to Gemini with the FNA 670 midpoint
            rubric. Per-section pass/fail + optional edits. Suggestions
            only — the draft is never modified.
          </p>
          {rubricPhase === 'error' && rubricError && (
            <p className="text-2xs text-danger mt-1">{rubricError}</p>
          )}
          {rubricReview && (
            <div
              data-testid="rubric-review-result"
              className="mt-2 card p-2.5 text-xs space-y-3 max-h-[28rem]
                         overflow-y-auto">
              {rubricReview.unavailable && (
                <div className="text-2xs text-warning bg-warning/10
                                border border-warning/30 rounded
                                px-2 py-1.5">
                  Rubric review unavailable —
                  {' '}{rubricReview.overall.reasoning}
                </div>
              )}
              {/* Overall verdict — anchors the top of the result card
                  so the user reads readiness first, then drills in. */}
              <div
                data-testid="rubric-overall"
                className={`flex items-start gap-2 rounded
                            px-2.5 py-2 border ${
                  OVERALL_STYLE[rubricReview.overall.verdict].bg
                } ${
                  OVERALL_STYLE[rubricReview.overall.verdict].border
                }`}>
                <div className="flex-1">
                  <div className={`text-xs font-semibold ${
                    OVERALL_STYLE[rubricReview.overall.verdict].text
                  }`}>
                    {OVERALL_LABEL[rubricReview.overall.verdict]}
                  </div>
                  <div className="text-2xs text-slate-300 mt-0.5 leading-relaxed">
                    {rubricReview.overall.reasoning}
                  </div>
                </div>
              </div>

              {/* Per-section pass/fail. Order anchored to the rubric
                  itself so the user reads the sections in the order
                  they appear in the paper. */}
              <div className="space-y-1.5">
                <div className="text-2xs uppercase tracking-wide
                                text-muted">
                  By Section
                </div>
                {(['methodology', 'results', 'roles', 'next_steps'] as const)
                  .map((key) => {
                    const s = rubricReview.sections[key]
                    if (!s) return null
                    const pass = s.verdict === 'pass'
                    return (
                      <div
                        key={key}
                        data-testid={`rubric-section-${key}`}
                        data-verdict={s.verdict}
                        className="flex items-start gap-2 px-2 py-1.5
                                   rounded border border-border bg-navy-900">
                        {pass
                          ? <Check className="w-3.5 h-3.5 text-success
                                              shrink-0 mt-0.5" />
                          : <X className="w-3.5 h-3.5 text-danger
                                          shrink-0 mt-0.5" />}
                        <div className="flex-1 min-w-0">
                          <div className="text-2xs font-medium text-white">
                            {RUBRIC_SECTION_LABEL[key]}
                            <span className={`ml-1.5 ${
                              pass ? 'text-success' : 'text-danger'
                            }`}>
                              {pass ? 'Pass' : 'Fail'}
                            </span>
                          </div>
                          <div className="text-2xs text-slate-300
                                          leading-relaxed mt-0.5">
                            {s.reasoning}
                          </div>
                        </div>
                      </div>
                    )
                  })}
              </div>

              {/* Suggested edits — optional, the user decides what to
                  apply. The card is read-only; clicking an edit does
                  not modify the draft. */}
              {rubricReview.edits.length > 0 && (
                <div className="space-y-1.5">
                  <div className="text-2xs uppercase tracking-wide
                                  text-muted">
                    Suggested Edits ({rubricReview.edits.length})
                  </div>
                  {rubricReview.edits.map((edit, i) => (
                    <div
                      key={i}
                      data-testid={`rubric-edit-${i}`}
                      className="border border-electric/30 bg-electric/5
                                 rounded px-2 py-1.5">
                      <div className="text-2xs text-electric font-medium">
                        {RUBRIC_SECTION_LABEL[edit.section] || edit.section}
                      </div>
                      <div className="text-2xs text-white mt-0.5
                                      leading-relaxed">
                        {edit.suggestion}
                      </div>
                      <div className="text-2xs text-muted mt-0.5 italic
                                      leading-relaxed">
                        Why: {edit.reasoning}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* AI writing chat */}
      <div className="border-t border-border pt-3">
        <div className="text-2xs text-muted uppercase tracking-wide mb-1.5
                        flex items-center gap-1">
          <Sparkles className="w-3 h-3" /> Writing assistant
        </div>
        <div className="space-y-2 mb-2">
          {messages.length === 0 && (
            <div className="flex flex-wrap gap-1">
              {SUGGESTED.map((p) => (
                <button key={p} type="button" onClick={() => send(p)}
                  className="text-2xs px-2 py-1 rounded border border-border
                             text-muted hover:text-white hover:border-electric/40">
                  {p}
                </button>
              ))}
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`text-xs rounded px-2 py-1.5 ${m.role === 'user'
              ? 'bg-navy-700 text-white' : 'bg-navy-800 text-slate-300'}`}>
              {m.role === 'assistant'
                ? <Markdown content={m.text} />
                : m.text}
            </div>
          ))}
          {chatLoading && (
            <div className="text-2xs text-muted flex items-center gap-1">
              <Loader2 className="w-3 h-3 animate-spin" /> Thinking…
            </div>
          )}
        </div>
        <div className="flex items-center gap-1">
          <input ref={inputRef} value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') send(input) }}
            placeholder="Ask about your document, request improvements…"
            className="flex-1 bg-navy-800 border border-border rounded
                       text-xs text-white px-2 py-1.5" />
          <button type="button" onClick={() => send(input)}
            disabled={chatLoading || !input.trim()}
            aria-label="Send"
            className="text-electric hover:text-white disabled:opacity-40 p-1">
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  )
}
