/**
 * ExplainerPanel — a right-side slide-in drawer that streams a live,
 * data-anchored explanation of a metric or chart from the explainer
 * agent (POST /api/council/explain).
 *
 * A right drawer is used rather than an inline expansion so the panel
 * never obscures the chart or table being explained. It opens on an
 * InfoIcon click, streams the explanation token by token, and closes on
 * the X button, a backdrop click, or Escape.
 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import { X, Loader2, Users, Send, MessageCircle } from 'lucide-react'
import Markdown from './Markdown'
import {
  renderWithMacroCitations,
  extractMacroCategories,
  MacroAttributionFooter,
} from './MacroCitation'

interface ExplainerPanelProps {
  /** Metric/chart name — sent to the explainer and shown as the title. */
  metricLabel: string
  /** Current on-screen value, injected into the explainer prompt. */
  currentValue?: string
  /** Optional chart context — name + key values for the council
   *  handoff package. When set, the handoff route-state carries this
   *  so the receiving council session can include it as additional
   *  grounding. */
  chartContext?: { name?: string; values?: Record<string, unknown> }
  onClose: () => void
}

interface FollowupExchange {
  role: 'user' | 'cio'
  content: string
}

const FOLLOWUP_MAX_EXCHANGES = 3
const FOLLOWUP_MAX_CHARS = 300

export default function ExplainerPanel({
  metricLabel, currentValue, chartContext, onClose,
}: ExplainerPanelProps) {
  const [text, setText] = useState('')
  const [streaming, setStreaming] = useState(true)
  const [error, setError] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const navigate = useNavigate()

  // Follow-up thread state. Up to FOLLOWUP_MAX_EXCHANGES user/cio
  // pairs per panel session — closing the panel resets the thread.
  const [thread, setThread] = useState<FollowupExchange[]>([])
  const [pending, setPending] = useState('')
  const [followupBusy, setFollowupBusy] = useState(false)
  const [suggestCouncil, setSuggestCouncil] = useState(false)
  const exchangesUsed = thread.filter((e) => e.role === 'user').length
  const limitReached = exchangesUsed >= FOLLOWUP_MAX_EXCHANGES

  // Hand off to the council with a contextual question pre-filled +
  // the full handoff context package on route state. The council
  // screen reads handoff_question into the input, focuses it (never
  // auto-submits), and renders a Continuing-From banner with the
  // prior thread. Universal — every chart and metric routes through
  // this same handoff shape so the council always sees full prior
  // context.
  //
  // handoff_question defaults to the last user follow-up if any;
  // otherwise the metric-anchored opening question. The frontend
  // passes the whole package (thread + topic + macro summary + chart
  // context) so the council session can show the user what carries
  // over.
  const askCouncil = (handoffQuestionOverride?: string) => {
    const valuePart = currentValue ? ` (${currentValue})` : ''
    const fallbackQuestion =
      `Can you explain ${metricLabel}${valuePart} in the context of our `
      + 'asset allocation analysis and the 2022 correlation regime break?'
    const lastUserQ = [...thread].reverse().find((e) => e.role === 'user')
    const prefillQuestion = (
      handoffQuestionOverride
      ?? lastUserQ?.content
      ?? fallbackQuestion)
    const handoffPackage = {
      handoff_source: 'explainer_panel',
      explainer_topic: metricLabel,
      explainer_content: text,
      ...(chartContext ? { chart_context: chartContext } : {}),
      thread: thread.map((e) => ({ role: e.role, content: e.content })),
      handoff_question: prefillQuestion,
    }
    navigate('/council', {
      state: { prefillQuestion, handoff: handoffPackage },
    })
    onClose()
  }

  const askFollowup = async () => {
    const q = pending.trim()
    if (!q || followupBusy || limitReached) return
    setPending('')
    setFollowupBusy(true)
    // Append the user message immediately so the thread feels
    // responsive. The CIO response lands after the stream resolves.
    setThread((t) => [...t, { role: 'user', content: q }])
    try {
      const common = axios.defaults.headers.common as Record<string, unknown>
      const headers: Record<string, string> = { 'Content-Type': 'application/json' }
      for (const h of ['X-API-Key', 'X-Session-ID', 'X-Session-Type']) {
        const v = common[h]
        if (typeof v === 'string') headers[h] = v
      }
      const res = await fetch('/api/v1/council/explainer-followup', {
        method: 'POST',
        headers,
        body: JSON.stringify({
          explainer_topic: metricLabel,
          explainer_content: text,
          ...(chartContext ? { chart_context: chartContext } : {}),
          thread,
          question: q,
        }),
      })
      if (!res.ok || !res.body) {
        throw new Error(`status ${res.status}`)
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let chunkText = ''
      let didSuggest = false
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        // SSE frames separated by blank lines.
        let sep: number
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, sep).trim()
          buffer = buffer.slice(sep + 2)
          if (!frame.startsWith('data:')) continue
          const payload = frame.slice(5).trim()
          if (payload === '[DONE]') continue
          try {
            const evt = JSON.parse(payload) as {
              type?: string; text?: string
              exchanges_used?: number; suggest_council?: boolean
            }
            if (evt.type === 'chunk' && typeof evt.text === 'string') {
              chunkText += evt.text
            } else if (evt.type === 'meta') {
              didSuggest = !!evt.suggest_council
            }
          } catch { /* ignore malformed frame */ }
        }
      }
      setThread((t) => [...t, { role: 'cio', content: chunkText }])
      setSuggestCouncil(didSuggest)
    } catch {
      setThread((t) => [...t, {
        role: 'cio',
        content: 'The CIO follow-up is unavailable right now. '
                 + 'Take the question to the council.',
      }])
    } finally {
      setFollowupBusy(false)
    }
  }

  // Stream the explanation on mount.
  useEffect(() => {
    const controller = new AbortController()
    abortRef.current = controller

    async function run() {
      try {
        const common = axios.defaults.headers.common as Record<string, unknown>
        const headers: Record<string, string> = { 'Content-Type': 'application/json' }
        for (const h of ['X-API-Key', 'X-Session-ID', 'X-Session-Type']) {
          const v = common[h]
          if (typeof v === 'string') headers[h] = v
        }
        const res = await fetch('/api/council/explain', {
          method: 'POST',
          headers,
          body: JSON.stringify({
            metric: metricLabel,
            current_value: currentValue ?? null,
            context: 'academic_project',
          }),
          signal: controller.signal,
        })
        if (!res.ok || !res.body) throw new Error(`status ${res.status}`)

        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          setText((t) => t + decoder.decode(value, { stream: true }))
        }
      } catch (err) {
        if (controller.signal.aborted) return
        setError(true)
      } finally {
        if (!controller.signal.aborted) setStreaming(false)
      }
    }
    void run()
    return () => controller.abort()
  }, [metricLabel, currentValue])

  // Escape closes the panel.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <>
      {/* Backdrop — a click anywhere outside the drawer closes it. */}
      <div
        className="fixed inset-0 z-[60] bg-black/30"
        onClick={onClose}
        aria-hidden="true"
      />
      {/* Bottom sheet on mobile, right-side drawer from sm: up. */}
      <aside
        role="dialog"
        aria-label={`Explanation: ${metricLabel}`}
        className="fixed z-[61] bg-navy-800 shadow-2xl flex flex-col
                   inset-x-0 bottom-0 h-[60vh] rounded-t-xl border-t border-border
                   animate-[fc-slide-up_200ms_ease-out]
                   sm:inset-x-auto sm:inset-y-0 sm:right-0 sm:h-auto
                   sm:w-[360px] sm:max-w-[90vw] sm:rounded-t-none
                   sm:border-t-0 sm:border-l sm:animate-none"
      >
        {/* Drag handle — mobile bottom-sheet dismissal affordance. */}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close explainer"
          className="sm:hidden flex justify-center pt-2 pb-1 shrink-0"
        >
          <span className="block w-10 h-1 rounded-full bg-border" />
        </button>
        <header className="flex items-start justify-between gap-3 px-4 py-3
                           border-b border-border shrink-0">
          <div className="min-w-0">
            <div className="text-2xs uppercase tracking-wide text-muted">
              Explainer
            </div>
            <h2 className="text-sm font-semibold text-white truncate">
              {metricLabel}
            </h2>
            {currentValue && (
              <div className="text-2xs font-mono text-electric mt-0.5">
                {currentValue}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close explainer"
            className="text-muted hover:text-white shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </header>

        {/* break-words + overflow-wrap-anywhere stop long unbroken
            strings (URLs, identifiers) from pushing the panel content
            past its width — UAT feedback #3 flagged horizontal
            scrolling on the drawer. overflow-x-hidden caps any
            residual overflow that escapes the wrap rules. */}
        <div className="flex-1 overflow-y-auto overflow-x-hidden px-4 py-3
                        text-sm leading-relaxed break-words
                        [overflow-wrap:anywhere]">
          {streaming && text === '' && !error && (
            <div className="flex items-center gap-2 text-muted text-xs animate-pulse">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Explaining {metricLabel}…
            </div>
          )}
          {error && text === '' && (
            <div className="text-xs text-warning">
              The explainer is unavailable right now. The hover tooltip
              still describes this metric.
            </div>
          )}
          {text && (
            <div>
              <Markdown content={text} />
              {streaming && (
                <span className="inline-block w-1.5 h-3.5 bg-electric/60
                                 animate-pulse align-middle ml-0.5" />
              )}
            </div>
          )}
        </div>

        {/* CIO follow-up thread — sits below the explainer content. Up
            to 3 exchanges per panel session; closing the panel resets
            the thread. The thread scrolls independently within the
            panel so it does not push the panel taller than the
            viewport. Hidden until the initial explainer stream has
            finished — the follow-up is meaningful only after the user
            has read the static explanation. */}
        {!streaming && !error && text && (
          <div
            data-testid="explainer-followup-thread"
            className="border-t border-border px-4 py-3 shrink-0 max-h-[40vh]
                       overflow-y-auto space-y-2.5 bg-navy-900/40">
            <div className="flex items-center gap-1.5 mb-1">
              <MessageCircle className="w-3 h-3 text-electric" />
              <span className="text-2xs uppercase tracking-wide text-muted">
                Follow-up with the CIO
              </span>
            </div>
            {thread.map((ex, i) => (
              <div key={i} className={ex.role === 'user'
                ? 'flex justify-end'
                : 'flex justify-start'}>
                <div className={`max-w-[85%] rounded-lg px-3 py-1.5 ${
                  ex.role === 'user'
                    ? 'bg-navy-700 text-slate-200'
                    : 'bg-electric/10 border border-electric/20 text-slate-100'
                }`}>
                  <div className="text-2xs uppercase tracking-wide
                                  text-muted mb-0.5">
                    {ex.role === 'user' ? 'You' : 'CIO'}
                  </div>
                  <div className="text-xs leading-relaxed
                                  break-words [overflow-wrap:anywhere]">
                    {/* CIO messages get inline macro citation badges
                        when the agent emitted [Macro: <category>] tags.
                        User messages render as plain text. */}
                    {ex.role === 'cio'
                      ? renderWithMacroCitations(ex.content)
                      : ex.content}
                  </div>
                </div>
              </div>
            ))}
            {/* PART 3 — Macro attribution footer. Surfaces when at
                least one CIO message in the thread carries a
                [Macro: <category>] citation. The agent's evidence
                source is named so the reader knows the response is
                grounded in current macro data, not training memory. */}
            {(() => {
              const allCios = thread
                .filter((e) => e.role === 'cio')
                .map((e) => e.content)
                .join('\n')
              const cats = extractMacroCategories(allCios)
              return cats.length > 0
                ? <MacroAttributionFooter categories={cats} />
                : null
            })()}
            {followupBusy && (
              <div className="flex items-center gap-2 text-muted text-xs">
                <Loader2 className="w-3 h-3 animate-spin" />
                CIO is responding…
              </div>
            )}
            {suggestCouncil && !limitReached && (
              <div
                data-testid="explainer-suggest-council"
                className="rounded border border-warning/30 bg-warning/10
                           px-2.5 py-2 text-2xs text-warning">
                This question may benefit from full council
                deliberation.
                <button
                  type="button"
                  onClick={() => askCouncil()}
                  className="ml-2 underline hover:text-amber-200">
                  Take this to the Council →
                </button>
              </div>
            )}
            <div className="pt-1 text-2xs text-muted">
              {exchangesUsed} of {FOLLOWUP_MAX_EXCHANGES} follow-ups used.
            </div>
          </div>
        )}

        {/* Input area — or the limit-reached handoff prompt */}
        {!streaming && !error && text && (
          <div className="border-t border-border px-4 py-3 shrink-0">
            {limitReached ? (
              <div className="text-2xs text-muted mb-2 leading-relaxed">
                You've used all {FOLLOWUP_MAX_EXCHANGES} follow-ups for
                this explainer. Take this question to the full council.
              </div>
            ) : (
              <form
                onSubmit={(e) => { e.preventDefault(); void askFollowup() }}
                className="flex gap-1.5">
                <input
                  type="text"
                  value={pending}
                  onChange={(e) => setPending(e.target.value)}
                  maxLength={FOLLOWUP_MAX_CHARS}
                  placeholder={`Ask a follow-up about ${metricLabel}…`}
                  disabled={followupBusy}
                  data-testid="explainer-followup-input"
                  className="flex-1 bg-navy-800 border border-border rounded
                             px-2.5 py-1.5 text-xs text-white placeholder-muted
                             focus:outline-none focus:border-electric
                             disabled:opacity-50 transition-colors min-w-0"
                />
                <button
                  type="submit"
                  disabled={!pending.trim() || followupBusy || limitReached}
                  data-testid="explainer-followup-submit"
                  className="px-2.5 py-1.5 rounded text-2xs font-semibold
                             border border-electric/30 bg-electric/10
                             text-electric hover:bg-electric/20
                             disabled:opacity-50 disabled:cursor-not-allowed
                             transition-colors shrink-0"
                >
                  <Send className="w-3 h-3" />
                </button>
              </form>
            )}
            {pending.length > FOLLOWUP_MAX_CHARS - 50 && (
              <div className="text-2xs text-muted mt-1">
                {FOLLOWUP_MAX_CHARS - pending.length} chars left
              </div>
            )}
          </div>
        )}

        {/* Hand-off to the council — continue this metric as a full
            council question, pre-filled and focused (never auto-sent).
            Same affordance whether or not the thread has been used —
            but when used, the full thread + topic + chart context +
            macro summary travel in the handoff package on route state. */}
        <div className="border-t border-border px-4 py-3 shrink-0">
          <button
            type="button"
            onClick={() => askCouncil()}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg
                       text-sm font-medium border border-electric/30 bg-electric/10
                       text-electric hover:bg-electric/20 transition-colors"
          >
            <Users className="w-4 h-4" />
            Ask the Council about this
          </button>
        </div>
      </aside>
    </>
  )
}
