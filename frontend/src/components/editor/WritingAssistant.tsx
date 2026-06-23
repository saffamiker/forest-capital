/**
 * WritingAssistant — the editor's right panel (300px, collapsible).
 *
 * Top: per-document Academic Review trigger -- the button label and
 * the labeled results panel both rename per doc type ("Review
 * Brief" / "Review Deck" / "Review Appendix" / "Review Script") and
 * the POST carries a document_type query param so the backend
 * applies the doc-specific rubric (June 23 2026). Review state lives
 * in academicReviewStore.perDocument[docType] so switching between
 * editors keeps the verdict cached.
 *
 * A warning shows first if unresolved [[VERIFY]] / [[BOB]] markers
 * remain. Below: an AI chat that answers writing questions with the
 * draft's text as context (the document-assistant endpoint).
 */
import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import {
  GraduationCap, Loader2, Send, AlertTriangle, Sparkles,
} from 'lucide-react'

import CriticFindingsPanel from '../CriticFindingsPanel'
import Markdown from '../Markdown'

import {
  useAcademicReviewStore,
} from '../../stores/academicReviewStore'
import type { EditorDocumentType } from '../../types/editor'


// Per-doc labels for the button + results panel + framing note. The
// labels here drive every doc-type-specific string in this surface
// so future renames need only edit this map.
const REVIEW_LABELS: Record<EditorDocumentType, {
  buttonLabel:   string
  panelHeading:  string
  framingNote:   string
}> = {
  // midpoint_paper is retired post-May 27 but stays in the type
  // union; ship a stable label here so tsc --noEmit on Vercel
  // builds accepts the exhaustive Record. The button still routes
  // through document_type=midpoint_paper on the off chance an
  // historical draft is opened.
  midpoint_paper: {
    buttonLabel:  'Run Academic Review',
    panelHeading: 'Midpoint Paper Review',
    framingNote: (
      'Midpoint paper review (retired May 27 2026) -- the rubric '
      + 'this evaluates against is the FNA 670 midpoint check '
      + 'rubric, kept available for historical drafts.'),
  },
  executive_brief: {
    buttonLabel:  'Review Brief',
    panelHeading: 'Executive Brief Review',
    framingNote: (
      'Executive Brief review applies the six-section brief rubric '
      + '(Executive Summary, Methodology, Key Findings, Limitations, '
      + 'Final Recommendations, Visuals) with weights 15/20/25/15/20/5.'),
  },
  analytical_appendix: {
    buttonLabel:  'Review Appendix',
    panelHeading: 'Appendix Review',
    framingNote: (
      'Appendix review applies the eight-section evidentiary rubric '
      + '(Data, Performance, Statistical Tests, Bootstrap CIs, '
      + 'Factors, Crisis Windows, Cost Sensitivity, Audit) -- evaluates '
      + 'rigour, completeness, and data-hash traceability.'),
  },
  presentation_deck: {
    buttonLabel:  'Review Deck',
    panelHeading: 'Deck Review',
    framingNote: (
      'Presentation Deck review evaluates slide flow, so-what '
      + 'argumentation, speaker-note coverage, and demo-readiness. '
      + 'Slide bullet density and speaker-note completeness are '
      + 'rubric items here, not in the brief.'),
  },
  presentation_script: {
    buttonLabel:  'Review Script',
    panelHeading: 'Script Review',
    framingNote: (
      'Presentation Script review evaluates argument coherence, '
      + 'audience clarity, and slide coverage. Formatting scores '
      + 'do not apply -- a script reads aloud, not on the page.'),
  },
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
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const inputRef = useRef<HTMLInputElement | null>(null)

  // Per-document review state lives in the store. Subscribe with a
  // selector so the panel re-renders on each SSE frame. When no
  // documentType is supplied (which should not happen on the four
  // canonical editors but defends test fixtures and a transient
  // mount state) we read the global cross-document slice instead,
  // matching the legacy behaviour.
  const runPerDocReview = useAcademicReviewStore(
    (s) => s.runPerDocReview)
  const slice = useAcademicReviewStore((s) =>
    documentType ? s.perDocument[documentType] : null)
  const reviewPhase = slice?.phase ?? 'idle'
  const verdict = slice?.result?.arbiterText ?? ''
  const labels = documentType ? REVIEW_LABELS[documentType] : null

  // The editor's "Ask AI" action pushes a quoted passage into the input.
  useEffect(() => {
    if (prefill?.text) {
      setInput(prefill.text)
      inputRef.current?.focus()
    }
  }, [prefill?.nonce])  // eslint-disable-line react-hooks/exhaustive-deps

  const runReview = async () => {
    if (!documentType) return  // see note above
    const token = localStorage.getItem('fc_session_token') ?? ''
    await runPerDocReview(documentType, null, token)
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
          disabled={reviewPhase === 'consulting'
            || reviewPhase === 'streaming'}
          data-tour="editor-academic-review"
          data-testid={labels
            ? `per-doc-review-button-${documentType}`
            : 'per-doc-review-button'}
          className="w-full flex items-center justify-center gap-1.5 text-xs
                     bg-warning/15 text-warning border border-warning/40
                     rounded py-2 hover:bg-warning/25 disabled:opacity-60">
          {(reviewPhase === 'consulting' || reviewPhase === 'streaming')
            ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Consulting the council…</>
            : <>
                <GraduationCap className="w-3.5 h-3.5" />
                {labels?.buttonLabel ?? 'Run Academic Review'}
              </>}
        </button>
        {labels && (
          // Per-doc framing -- spec calls for a short note below
          // the trigger that explains which rubric the verdict will
          // score against, so the user reads the verdict in the
          // correct interpretive frame.
          <p className="text-2xs text-muted mt-1.5 leading-relaxed"
            data-testid={`per-doc-review-framing-${documentType}`}>
            {labels.framingNote}
          </p>
        )}
        {reviewPhase === 'error' && (
          <p className="text-2xs text-danger mt-1">
            The review could not be completed — please retry.
          </p>
        )}
        {verdict && (
          <div
            className="mt-2 card p-2.5 text-xs max-h-72 overflow-y-auto"
            data-testid={labels
              ? `per-doc-review-panel-${documentType}`
              : 'per-doc-review-panel'}>
            {labels && (
              <h4 className="text-white font-semibold text-2xs uppercase
                             tracking-wide mb-1.5">
                {labels.panelHeading}
              </h4>
            )}
            <Markdown content={verdict} />
          </div>
        )}
      </div>

      {/* Concern 7 (revised) -- adversarial critic + debate-round
          panel in compact mode for the 300px editor right rail.
          Per-doc surface: documentType is the editor's doc type so
          fix proposals route correctly. */}
      <CriticFindingsPanel
        compact
        criticResult={slice?.result?.criticResult ?? null}
        debateRoundText={slice?.result?.debateRoundText ?? ''}
        criticMinorOnly={slice?.result?.criticMinorOnly ?? false}
        debateId={slice?.result?.debateId ?? null}
        fixProposals={slice?.result?.fixProposals ?? {}}
        documentType={documentType} />


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
