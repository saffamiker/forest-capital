/**
 * frontend/src/components/GeminiAssistantPanel.tsx
 *
 * Sliding right-side panel that lets the user ask Gemini to rewrite the
 * current storyboard slide or document section. Shows the suggestion
 * with paragraph-level diff highlighting (red removed / green added)
 * and Apply / Skip controls per paragraph.
 *
 * Constraints enforced server-side (see /api/documents/:id/assistant):
 *   - No statistics introduced that weren't in the input
 *   - No citations outside references.json
 *   - Scope guard rejects off-topic requests
 *
 * The panel is intentionally stateful for the conversation thread —
 * sending multiple messages while keeping the same context_content
 * is a multi-turn refinement workflow. Per-message we store the
 * request, the suggestion, and the user's accept/skip decision.
 */
import { useState } from 'react'
import { Bot, Send, Loader2, Check, X, AlertTriangle } from 'lucide-react'
import axios from 'axios'
import type { AssistantResponse } from '../types/storyboard'

interface ConversationTurn {
  message:    string
  response:   AssistantResponse | null
  loading:    boolean
  applied:    boolean
}

interface Props {
  /** UUID of the parent document. Embedded in the request URL. */
  documentId:      string | null
  /** Identifies what the user is editing — 'slide' | 'section'. Passed
   *  to the backend for log context. */
  contextType:     'slide' | 'section'
  /** Current content the user wants to edit. */
  contextContent:  string
  /** Called when the user clicks Apply on a suggestion — caller updates
   *  the underlying slide/section state. */
  onApply:         (newContent: string) => void
  onClose?:        () => void
}

export default function GeminiAssistantPanel({
  documentId,
  contextType,
  contextContent,
  onApply,
  onClose,
}: Props) {
  const [draft, setDraft] = useState('')
  const [turns, setTurns] = useState<ConversationTurn[]>([])

  const send = async () => {
    if (!draft.trim() || !documentId) return
    const message = draft.trim()
    setDraft('')
    const turn: ConversationTurn = { message, response: null, loading: true, applied: false }
    setTurns((prev) => [...prev, turn])

    try {
      const res = await axios.post<AssistantResponse>(
        `/api/documents/${documentId}/assistant`,
        {
          message,
          context_type: contextType,
          context_content: contextContent,
        },
      )
      setTurns((prev) => prev.map((t) =>
        t === turn ? { ...t, response: res.data, loading: false } : t,
      ))
    } catch (err) {
      const errMsg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Gemini request failed'
      setTurns((prev) => prev.map((t) =>
        t === turn ? {
          ...t,
          loading: false,
          response: {
            suggestion: '',
            diff: { removed: [], added: [] },
            explanation: String(errMsg),
            confidence: 0,
          },
        } : t,
      ))
    }
  }

  const applyTurn = (t: ConversationTurn) => {
    if (!t.response?.suggestion) return
    onApply(t.response.suggestion)
    setTurns((prev) => prev.map((x) => x === t ? { ...x, applied: true } : x))
  }

  return (
    <aside
      className="flex flex-col h-full bg-navy-900 border-l shadow-2xl"
      style={{ borderColor: '#8b5cf640', width: 360 }}
      data-testid="gemini-assistant-panel"
    >
      <header className="px-4 py-3 border-b border-border flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4" style={{ color: '#8b5cf6' }} />
          <h2 className="text-white font-semibold text-sm">Gemini Assistant</h2>
          <span
            className="text-2xs px-1.5 py-0.5 rounded border"
            style={{ color: '#8b5cf6', borderColor: '#8b5cf650', background: '#8b5cf615' }}
          >
            gemini-1.5-pro
          </span>
        </div>
        {onClose && (
          <button onClick={onClose} className="text-muted hover:text-white p-1">
            <X className="w-4 h-4" />
          </button>
        )}
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        <div className="text-xs text-muted leading-relaxed">
          Ask Gemini to rewrite the current {contextType}. It can tighten,
          expand, restructure, or change tone — but it won't introduce
          numbers or citations that weren't already in your content.
        </div>

        {turns.map((t, i) => (
          <ConversationBlock
            key={i}
            turn={t}
            onApply={() => applyTurn(t)}
          />
        ))}
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); void send() }}
        className="border-t border-border px-3 py-3 flex items-end gap-2 shrink-0"
      >
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask Gemini…  (e.g. 'tighten this to 40 words', 'lead with the worst-case number')"
          rows={2}
          maxLength={1000}
          disabled={!documentId}
          className="flex-1 bg-navy-800 border border-border rounded text-xs text-white placeholder-muted px-2 py-1.5 resize-none focus:outline-none focus:border-electric"
        />
        <button
          type="submit"
          disabled={!draft.trim() || !documentId}
          className="rounded p-2 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          style={{ background: '#8b5cf615', border: '1px solid #8b5cf650', color: '#8b5cf6' }}
          aria-label="Send to Gemini"
        >
          <Send className="w-3.5 h-3.5" />
        </button>
      </form>
    </aside>
  )
}


function ConversationBlock({
  turn, onApply,
}: { turn: ConversationTurn; onApply: () => void }) {
  return (
    <div className="space-y-2">
      {/* User message */}
      <div className="flex justify-end">
        <div className="bg-navy-700 text-white text-xs rounded px-3 py-1.5 max-w-[80%]">
          {turn.message}
        </div>
      </div>

      {/* Gemini response */}
      {turn.loading && (
        <div className="flex items-center gap-2 text-xs text-muted">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Gemini is thinking…
        </div>
      )}

      {turn.response && (
        <div
          className="rounded p-3 text-xs space-y-2"
          style={{ background: '#8b5cf608', border: '1px solid #8b5cf625' }}
        >
          {turn.response.out_of_scope ? (
            <div className="flex items-start gap-2 text-warning">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>{turn.response.explanation}</span>
            </div>
          ) : (
            <>
              {turn.response.mock && (
                <div className="text-2xs text-warning italic">
                  Mock response — set GOOGLE_API_KEY on Render for real Gemini suggestions.
                </div>
              )}
              <DiffView diff={turn.response.diff} />
              <div className="flex items-center gap-2 pt-2 border-t border-border/40">
                <button
                  onClick={onApply}
                  disabled={turn.applied}
                  className="flex items-center gap-1 text-2xs px-2 py-1 rounded border border-success/40 bg-success/10 text-success hover:bg-success/20 disabled:opacity-40"
                >
                  <Check className="w-3 h-3" />
                  {turn.applied ? 'Applied' : 'Apply'}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}


function DiffView({ diff }: { diff: { removed: string[]; added: string[] } }) {
  // Render removed and added paragraphs side by side. Backend already
  // filtered the unchanged paragraphs out so the diff is tight.
  if (diff.removed.length === 0 && diff.added.length === 0) {
    return <div className="text-2xs text-muted italic">No paragraph-level changes — punctuation or whitespace only.</div>
  }
  return (
    <div className="space-y-2">
      {diff.removed.map((p, i) => (
        <p
          key={`r-${i}`}
          className="px-2 py-1 rounded text-2xs leading-relaxed line-through"
          style={{ background: 'rgba(239, 68, 68, 0.10)', color: '#fca5a5' }}
        >
          − {p}
        </p>
      ))}
      {diff.added.map((p, i) => (
        <p
          key={`a-${i}`}
          className="px-2 py-1 rounded text-2xs leading-relaxed"
          style={{ background: 'rgba(34, 197, 94, 0.10)', color: '#86efac' }}
        >
          + {p}
        </p>
      ))}
    </div>
  )
}
