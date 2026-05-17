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
import axios from 'axios'
import { X, Loader2 } from 'lucide-react'

interface ExplainerPanelProps {
  /** Metric/chart name — sent to the explainer and shown as the title. */
  metricLabel: string
  /** Current on-screen value, injected into the explainer prompt. */
  currentValue?: string
  onClose: () => void
}

/** Minimal markdown: render **bold** spans, keep everything else literal. */
function renderInline(line: string): React.ReactNode {
  const parts = line.split(/(\*\*[^*]+\*\*)/g)
  return parts.map((p, i) =>
    p.startsWith('**') && p.endsWith('**')
      ? <strong key={i} className="text-white">{p.slice(2, -2)}</strong>
      : <span key={i}>{p}</span>,
  )
}

export default function ExplainerPanel({
  metricLabel, currentValue, onClose,
}: ExplainerPanelProps) {
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
      <aside
        role="dialog"
        aria-label={`Explanation: ${metricLabel}`}
        className="fixed inset-y-0 right-0 z-[61] w-[360px] max-w-[90vw]
                   bg-navy-800 border-l border-border shadow-2xl
                   flex flex-col"
      >
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

        <div className="flex-1 overflow-y-auto px-4 py-3 text-sm leading-relaxed">
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
            <div className="text-slate-200 space-y-2">
              {text.split('\n').filter((l) => l.trim()).map((line, i) => (
                <p key={i}>{renderInline(line)}</p>
              ))}
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
