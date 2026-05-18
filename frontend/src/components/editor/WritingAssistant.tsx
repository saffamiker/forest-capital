/**
 * WritingAssistant — the editor's right panel (300px, collapsible).
 *
 * Top: Run Academic Review — streams the council's verdict against the
 * current draft. A warning shows first if unresolved [[VERIFY]] / [[BOB]]
 * markers remain. Below: an AI chat that answers writing questions with
 * the draft's text as context (the document-assistant endpoint).
 */
import { useRef, useState } from 'react'
import axios from 'axios'
import {
  GraduationCap, Loader2, Send, AlertTriangle, Sparkles,
} from 'lucide-react'

import Markdown from '../Markdown'

interface Props {
  draftId: number
  contentText: string
  unresolvedMarkers: number
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
  draftId, contentText, unresolvedMarkers,
}: Props) {
  const [reviewPhase, setReviewPhase] =
    useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [verdict, setVerdict] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [chatLoading, setChatLoading] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const runReview = async () => {
    setReviewPhase('running')
    setVerdict('')
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const token = localStorage.getItem('fc_session_token') ?? ''
      const res = await fetch('/api/council/academic-review', {
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
    setMessages((m) => [...m, { role: 'user', text: message }])
    setInput('')
    setChatLoading(true)
    try {
      const res = await axios.post(`/api/documents/${draftId}/assistant`, {
        message,
        context_content: contentText.slice(0, 6000),
        context_type: 'section',
      })
      const reply = [res.data?.explanation, res.data?.suggestion]
        .filter(Boolean).join('\n\n') || 'No response.'
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
          <input value={input} onChange={(e) => setInput(e.target.value)}
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
