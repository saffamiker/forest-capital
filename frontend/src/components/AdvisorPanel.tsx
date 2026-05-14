/**
 * frontend/src/components/AdvisorPanel.tsx
 *
 * Academic Advisor (Agent 10) — floating button + slide-out panel.
 *
 * Visible on every screen EXCEPT Present mode. The advisor is an internal
 * tool for the team (Michael, Bob, Molly); presenting it to Forest Capital
 * would expose the grade-awareness scaffolding behind the academic
 * deliverables. Hiding it in Present mode keeps the demo clean.
 *
 * Gold accent (#f59e0b) — distinct from every other agent in the system:
 *   Equity Analyst       #3b82f6 (blue)
 *   Fixed Income Analyst #0d9488 (teal)
 *   Risk Manager         #f59e0b (amber — different shade, never paired)
 *   Quant Backtester     #64748b (slate)
 *   Independent (Gemini) #8b5cf6 (purple)
 *   Contrarian (Grok)    #f97316 (orange)
 *   CIO                  #1e40af (deep blue)
 *   QA                   #be123c (crimson)
 *   Academic Advisor     #f59e0b (gold)  ← us
 *
 * The advisor's gold is intentionally close to but distinct from the
 * Risk Manager's amber — they are never adjacent in the UI, so the
 * collision is purely conceptual (both are "warning" colours by feel
 * but they live on different surfaces).
 */
import { useState, useMemo } from 'react'
import { GraduationCap, X, AlertTriangle, CheckCircle, Loader2, ExternalLink } from 'lucide-react'
import { useAdvisorStore } from '../stores/advisorStore'
import { useUI } from '../context/UIContext'
import type { DeliverableType, AdvisorAnalysis } from '../types/advisor'

const GOLD = '#f59e0b'

interface AdvisorPanelProps {
  // Optional: caller can pin the advisor to a specific deliverable —
  // used by the Reports screen "Get Advisor Feedback" buttons. When
  // omitted, the user picks from the dropdown inside the panel.
  initialDeliverable?: DeliverableType
  // Optional: caller can pass strategy results so the advisor can ground
  // its feedback. When omitted the advisor still responds — guidance is
  // just less data-anchored.
  strategyResults?: Record<string, unknown>
  // Controlled-open prop for the Reports-screen integration. When undefined,
  // the panel manages its own open state via the floating button.
  open?: boolean
  onClose?: () => void
}

const DELIVERABLE_OPTIONS: Array<{ value: DeliverableType; label: string; grade: string }> = [
  { value: 'midpoint',     label: 'Midpoint Paper',      grade: '10%' },
  { value: 'appendix',     label: 'Analytical Appendix', grade: '35%' },
  { value: 'brief',        label: 'Executive Brief',     grade: '20%' },
  { value: 'presentation', label: 'Final Presentation',  grade: '35%' },
]

const DEFAULT_QUERIES: Record<DeliverableType, string> = {
  midpoint:     'What should we focus on for the midpoint paper?',
  appendix:     'What gaps should we close in the analytical appendix?',
  brief:        'What is the strongest recommendation for the executive brief?',
  presentation: 'What are the must-show findings for the final presentation?',
}

export default function AdvisorPanel({
  initialDeliverable = 'midpoint',
  strategyResults,
  open: controlledOpen,
  onClose,
}: AdvisorPanelProps) {
  const { mode } = useUI()
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false)
  const [deliverable, setDeliverable] = useState<DeliverableType>(initialDeliverable)
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState(false)

  const { analyse, analyses, loading, error } = useAdvisorStore()

  // Hide in Present mode — the advisor is internal team scaffolding,
  // not Forest-Capital-facing content.
  if (mode === 'present') return null

  const isControlled = controlledOpen !== undefined
  const isOpen = isControlled ? controlledOpen : uncontrolledOpen

  const handleOpen = () => {
    if (isControlled) return
    setUncontrolledOpen(true)
  }

  const handleClose = () => {
    if (isControlled) {
      onClose?.()
    } else {
      setUncontrolledOpen(false)
    }
    setSubmitted(false)
  }

  // Cache key matches the store's so we can read the cached result
  // synchronously without re-rendering on every keystroke.
  const cacheKey = useMemo(() => {
    const q = query.trim() || DEFAULT_QUERIES[deliverable]
    return `${deliverable}:${q.slice(0, 200).toLowerCase()}`
  }, [deliverable, query])

  const cached: AdvisorAnalysis | undefined = analyses[cacheKey]

  const handleSubmit = async () => {
    const effective = query.trim() || DEFAULT_QUERIES[deliverable]
    setSubmitted(true)
    await analyse(effective, deliverable, strategyResults)
  }

  return (
    <>
      {/* Floating button — only when uncontrolled. Hidden when the
          parent (Reports screen) is managing open state. */}
      {!isControlled && !isOpen && (
        <button
          type="button"
          onClick={handleOpen}
          data-testid="advisor-floating-button"
          className="fixed bottom-6 right-6 z-40 flex items-center gap-2 px-4 py-2.5 rounded-full shadow-lg transition-transform hover:scale-105"
          style={{
            backgroundColor: GOLD,
            color: '#0a0e1a',
            fontWeight: 600,
          }}
          title="Academic Advisor — grade-aware guidance and citation verification"
          aria-label="Open Academic Advisor"
        >
          <GraduationCap className="w-4 h-4" />
          <span className="text-xs tracking-wide uppercase">Advisor</span>
        </button>
      )}

      {/* Slide-out panel — right side, fixed width, gold left accent.
          Doesn't use a backdrop overlay so the user can keep reading
          the page underneath while consulting the advisor. */}
      {isOpen && (
        <aside
          role="complementary"
          aria-label="Academic Advisor panel"
          data-testid="advisor-panel"
          className="fixed top-14 right-0 bottom-0 w-96 bg-navy-800 border-l shadow-2xl z-40 flex flex-col"
          style={{ borderLeftColor: GOLD, borderLeftWidth: '3px' }}
        >
          {/* Header */}
          <header
            className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0"
            style={{ backgroundColor: 'rgba(245,158,11,0.08)' }}
          >
            <div className="flex items-center gap-2">
              <GraduationCap className="w-4 h-4" style={{ color: GOLD }} />
              <h2 className="text-sm font-semibold text-white tracking-wide">
                Academic Advisor
              </h2>
            </div>
            <button
              type="button"
              onClick={handleClose}
              className="text-muted hover:text-white p-1 rounded transition-colors"
              aria-label="Close advisor panel"
            >
              <X className="w-4 h-4" />
            </button>
          </header>

          {/* Body — scrolls independently */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {/* Deliverable picker */}
            <div>
              <label className="block text-2xs uppercase tracking-wide text-muted mb-1.5">
                Deliverable
              </label>
              <select
                value={deliverable}
                onChange={(e) => setDeliverable(e.target.value as DeliverableType)}
                className="w-full bg-navy-900 border border-border rounded px-2.5 py-1.5 text-sm text-white"
                data-testid="advisor-deliverable-select"
              >
                {DELIVERABLE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label} ({opt.grade})
                  </option>
                ))}
              </select>
            </div>

            {/* Query */}
            <div>
              <label className="block text-2xs uppercase tracking-wide text-muted mb-1.5">
                Your question
              </label>
              <textarea
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={DEFAULT_QUERIES[deliverable]}
                rows={3}
                className="w-full bg-navy-900 border border-border rounded px-2.5 py-1.5 text-sm text-white placeholder-muted resize-none"
                data-testid="advisor-query-input"
              />
            </div>

            <button
              type="button"
              onClick={handleSubmit}
              disabled={loading}
              className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded text-xs font-medium transition-colors disabled:opacity-50"
              style={{
                backgroundColor: 'rgba(245,158,11,0.12)',
                border: `1px solid ${GOLD}40`,
                color: GOLD,
              }}
              data-testid="advisor-submit-button"
            >
              {loading
                ? <><Loader2 className="w-3 h-3 animate-spin" /> Searching…</>
                : <>Get Advisor Feedback</>}
            </button>

            {error && (
              <div className="flex items-start gap-2 px-3 py-2 rounded border border-danger/30 bg-danger/5 text-danger text-xs">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                <span>{error}</span>
              </div>
            )}

            {submitted && cached && <AdvisorResult result={cached} />}

            {!submitted && (
              <div className="text-xs text-muted leading-relaxed pt-2">
                <p>
                  The Academic Advisor cross-references your findings against
                  external academic sources, verifies every citation via web
                  search, and prioritises feedback by grade weight.
                </p>
                <p className="mt-2">
                  Citations are dropped automatically if the search tool could
                  not retrieve them — what you see has been verified to exist.
                </p>
              </div>
            )}
          </div>
        </aside>
      )}
    </>
  )
}

function AdvisorResult({ result }: { result: AdvisorAnalysis }) {
  return (
    <div className="space-y-4" data-testid="advisor-result">
      {result.key_findings.length > 0 && (
        <Section title="Key findings from your data" items={result.key_findings} />
      )}

      {result.guidance.length > 0 && (
        <Section title="What to focus on" items={result.guidance} accent={GOLD} />
      )}

      {result.citations.length > 0 && (
        <div>
          <div className="text-2xs uppercase tracking-wide text-muted mb-1.5">
            External evidence ({result.citations.length} verified)
          </div>
          <ul className="space-y-1.5">
            {result.citations.map((c, idx) => (
              <li
                key={`${c.url}-${idx}`}
                className="border border-border rounded p-2 bg-navy-900"
              >
                <a
                  href={c.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-electric text-xs hover:underline flex items-start gap-1.5"
                >
                  <ExternalLink className="w-3 h-3 mt-0.5 shrink-0" />
                  <span className="flex-1">{c.title}</span>
                </a>
                {c.relevance && (
                  <p className="text-muted text-2xs mt-1 leading-relaxed">{c.relevance}</p>
                )}
                <div className="flex items-center gap-1 mt-1.5">
                  <CheckCircle className="w-3 h-3 text-success" />
                  <span className="text-2xs text-success">Verified via web search</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.potential_issues.length > 0 && (
        <div>
          <div className="text-2xs uppercase tracking-wide text-warning mb-1.5 flex items-center gap-1">
            <AlertTriangle className="w-3 h-3" />
            Potential issues
          </div>
          <ul className="space-y-1.5">
            {result.potential_issues.map((issue, idx) => (
              <li
                key={idx}
                className="text-xs text-slate-300 border-l-2 border-warning/40 pl-2 py-0.5"
              >
                {issue}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.error && (
        <div className="text-xs text-danger px-2 py-1.5 rounded border border-danger/30 bg-danger/5">
          {result.error}
        </div>
      )}
    </div>
  )
}

function Section({ title, items, accent }: { title: string; items: string[]; accent?: string }) {
  return (
    <div>
      <div className="text-2xs uppercase tracking-wide text-muted mb-1.5">{title}</div>
      <ul className="space-y-1.5">
        {items.map((item, idx) => (
          <li
            key={idx}
            className="text-xs text-slate-300 leading-relaxed border-l-2 pl-2 py-0.5"
            style={{ borderLeftColor: accent ?? 'rgba(148,163,184,0.3)' }}
          >
            {item}
          </li>
        ))}
      </ul>
    </div>
  )
}
