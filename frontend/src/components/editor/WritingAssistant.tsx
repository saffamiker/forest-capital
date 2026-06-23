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
} from 'lucide-react'

import Markdown from '../Markdown'

import type { EditorDocumentType } from '../../types/editor'



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


// Per-document-type review button label. The label names the rubric
// that will be applied so the user knows what criteria the verdict
// scores against. June 22 2026 -- replaces the generic "Run Academic
// Review" label that gave no signal about rubric.
function reviewButtonLabel(
  documentType: EditorDocumentType | undefined,
): string {
  switch (documentType) {
    case 'executive_brief':
      return 'Run Executive Brief Review'
    case 'analytical_appendix':
      return 'Run Appendix Review'
    case 'presentation_deck':
      return 'Run Deck Review'
    case 'presentation_script':
      return 'Run Script Review'
    default:
      return 'Run Academic Review'
  }
}


// Per-document-type rubric note rendered below the review button.
// Frames WHICH rubric the council will apply so the verdict reads
// against the right standard. June 22 2026 -- expanded from script-
// only to all four supported document types after the backend's
// document-specific rubric switch landed but the frontend wasn't
// passing the document_type param.
function reviewFramingNote(
  documentType: EditorDocumentType,
): string {
  switch (documentType) {
    case 'executive_brief':
      return (
        'Executive Brief review applies the 6-section brief rubric '
        + '(Executive Summary, Methodology, Key Findings, Limitations, '
        + 'Final Recommendations, Visuals) with weights 15/20/25/15/20/5.')
    case 'analytical_appendix':
      return (
        'Analytical Appendix review evaluates the 8-section appendix '
        + 'structure (Data + Methodology, Full Performance, Statistical '
        + 'Tests, Bootstrap CI, Factor Loadings, Crisis Windows, Cost '
        + 'Sensitivity, Audit Summary) against the appendix-specific '
        + 'rubric.')
    case 'presentation_deck':
      return (
        'Presentation Deck review evaluates slide flow, visual / data '
        + 'balance, narrative arc, and time budget against the deck-'
        + 'specific rubric. Citation formatting does not apply.')
    case 'presentation_script':
      return (
        'Presentation Script review evaluates argument coherence, '
        + 'audience clarity, slide coverage, and speaker '
        + 'differentiation. Citation formatting and paragraph '
        + 'structure do not apply.')
    default:
      return ''
  }
}

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
      // June 22 2026 -- pass document_type for ALL four supported
      // document types so the backend routes to the document-specific
      // rubric (executive_brief / analytical_appendix /
      // presentation_deck / presentation_script). Previously only
      // presentation_script passed the param; the other three doc
      // types hit the endpoint with no param and got the midpoint
      // rubric -- wrong rubric for every document past the May 27
      // midpoint deadline. The endpoint at
      // POST /api/council/academic-review already accepts the param
      // and switches to the matching rubric.
      const qs = documentType
        ? `?document_type=${encodeURIComponent(documentType)}`
        : ''
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
          data-testid="editor-document-review-button"
          className="w-full flex items-center justify-center gap-1.5 text-xs
                     bg-warning/15 text-warning border border-warning/40
                     rounded py-2 hover:bg-warning/25 disabled:opacity-60">
          {reviewPhase === 'running'
            ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Consulting the council…</>
            : <><GraduationCap className="w-3.5 h-3.5" />
                {' '}{reviewButtonLabel(documentType)}</>}
        </button>
        {documentType && (
          // Per-doc framing -- which rubric the review will use.
          // The user reads this BEFORE clicking so they understand
          // what criteria the verdict will be scored against.
          // June 22 2026 -- extended from script-only to all four
          // supported document types; previously only the script
          // had a framing note.
          <p className="text-2xs text-muted mt-1.5 leading-relaxed"
             data-testid="editor-document-review-framing">
            {reviewFramingNote(documentType)}
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
