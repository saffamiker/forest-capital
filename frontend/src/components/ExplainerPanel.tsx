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
import { X, Loader2, Users } from 'lucide-react'
import Markdown from './Markdown'

interface ExplainerPanelProps {
  /** Metric/chart name — sent to the explainer and shown as the title. */
  metricLabel: string
  /** Current on-screen value, injected into the explainer prompt. */
  currentValue?: string
  onClose: () => void
}

export default function ExplainerPanel({
  metricLabel, currentValue, onClose,
}: ExplainerPanelProps) {
  const [text, setText] = useState('')
  const [streaming, setStreaming] = useState(true)
  const [error, setError] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const navigate = useNavigate()

  // Hand off to the council with a contextual question pre-filled. The
  // council screen sets the field and focuses it — it never auto-submits,
  // so the user reviews and confirms before convening the council.
  const askCouncil = () => {
    const valuePart = currentValue ? ` (${currentValue})` : ''
    const prefillQuestion =
      `Can you explain ${metricLabel}${valuePart} in the context of our `
      + 'asset allocation analysis and the 2022 correlation regime break?'
    navigate('/council', { state: { prefillQuestion } })
    onClose()
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

        {/* Hand-off to the council — continue this metric as a full
            council question, pre-filled and focused (never auto-sent). */}
        <div className="border-t border-border px-4 py-3 shrink-0">
          <button
            type="button"
            onClick={askCouncil}
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
