/**
 * DataExplainPanel — a right-side slide-in drawer that streams a
 * contextual explanation of the SPECIFIC values currently on screen,
 * from the explainer agent (POST /api/council/explain-data).
 *
 * Deliberately distinct from ExplainerPanel / the InfoIcon:
 *   ⓘ InfoIcon  → "what does this metric mean?"      (explain)
 *   ✨ Data Explain → "what do these specific values mean?" (explain_data)
 *
 * Same drawer pattern as ExplainerPanel — opens on the "Explain this
 * data" button, streams the explanation token by token, and closes on
 * the X button, a backdrop click, or Escape.
 */
import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { X, Loader2, Sparkles } from 'lucide-react'
import Markdown from './Markdown'

interface DataExplainPanelProps {
  /** Metric/chart/strategy name — sent to the explainer and shown as the title. */
  metric: string
  /** Compact summary of the values on screen, injected into the prompt. */
  currentValue?: string
  /** Free-text framing hint, e.g. "academic_project". */
  context?: string
  onClose: () => void
}

export default function DataExplainPanel({
  metric, currentValue, context, onClose,
}: DataExplainPanelProps) {
  const [text, setText] = useState('')
  const [streaming, setStreaming] = useState(true)
  const [error, setError] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

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
        const res = await fetch('/api/council/explain-data', {
          method: 'POST',
          headers,
          body: JSON.stringify({
            metric,
            current_value: currentValue ?? null,
            context: context ?? 'academic_project',
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
      } catch {
        if (controller.signal.aborted) return
        setError(true)
      } finally {
        if (!controller.signal.aborted) setStreaming(false)
      }
    }
    void run()
    return () => controller.abort()
  }, [metric, currentValue, context])

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
        aria-label={`Data explanation: ${metric}`}
        className="fixed z-[61] bg-navy-800 shadow-2xl flex flex-col
                   inset-x-0 bottom-0 h-[60vh] rounded-t-xl border-t border-border
                   animate-[fc-slide-up_200ms_ease-out]
                   sm:inset-x-auto sm:inset-y-0 sm:right-0 sm:h-auto
                   sm:w-[380px] sm:max-w-[90vw] sm:rounded-t-none
                   sm:border-t-0 sm:border-l sm:animate-none"
      >
        {/* Drag handle — mobile bottom-sheet dismissal affordance. */}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close data explanation"
          className="sm:hidden flex justify-center pt-2 pb-1 shrink-0"
        >
          <span className="block w-10 h-1 rounded-full bg-border" />
        </button>
        <header className="flex items-start justify-between gap-3 px-4 py-3
                           border-b border-border shrink-0">
          <div className="min-w-0">
            <div className="text-2xs uppercase tracking-wide text-muted
                            flex items-center gap-1">
              <Sparkles className="w-3 h-3 text-electric" />
              Data Explain
            </div>
            <h2 className="text-sm font-semibold text-white truncate">
              {metric}
            </h2>
            {currentValue && (
              <div className="text-2xs font-mono text-electric mt-0.5 line-clamp-2">
                {currentValue}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close data explanation"
            className="text-muted hover:text-white shrink-0"
          >
            <X className="w-4 h-4" />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-4 py-3 text-sm leading-relaxed">
          {streaming && text === '' && !error && (
            <div className="flex items-center gap-2 text-muted text-xs animate-pulse">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              Explaining this data…
            </div>
          )}
          {error && text === '' && (
            <div className="text-xs text-warning">
              The explainer is unavailable right now. The metric tooltips
              still describe each figure.
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
      </aside>
    </>
  )
}
