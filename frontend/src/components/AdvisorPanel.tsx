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
// May 24 2026 — `useLocation` requires a Router ancestor; the test
// suite renders this component standalone. `useSafeLocation` falls
// back to a noop pathname when there is no Router, so the
// HIDDEN_ROUTES check degrades gracefully (advisor shows by default).
import * as Router from 'react-router-dom'
import {
  GraduationCap, X, AlertTriangle, CheckCircle, Loader2, ExternalLink, Quote,
} from 'lucide-react'
import { useAdvisorStore } from '../stores/advisorStore'
import { useUI } from '../context/UIContext'
import type { DeliverableType, AdvisorAnalysis, VerifiedCitation } from '../types/advisor'


// May 24 2026 — context-aware floating button visibility per user spec.
// The advisor button is hidden on routes where an inline AI surface
// already covers the use case:
//
//   /report-writer  — the editor's "Ask the Writer" panel is the
//                     inline AI for this page.
//   /peer-review    — both tabs (Peer Review Assistant + Thesis
//                     Defense Prep) already host harness-gated AI
//                     flows. A floating advisor would be a
//                     redundant third AI affordance on the page.
//
// Every other page (Dashboard, Analytics, Statistical Evidence,
// Regime Analysis, QA Audit, Council, Reports) keeps the floating
// button so the team always has a one-click route to ad-hoc
// academic guidance from anywhere in the app.
const HIDDEN_ROUTES: ReadonlySet<string> = new Set([
  '/report-writer',
  '/reports/writer',  // legacy alias for the report writer route
  '/peer-review',
])

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


export default function AdvisorPanel({
  initialDeliverable = 'midpoint',
  strategyResults,
  open: controlledOpen,
  onClose,
}: AdvisorPanelProps) {
  const { mode } = useUI()
  // Wrap useLocation in a try/catch so a Router-less render
  // (the standalone test cases) degrades to "show everywhere"
  // instead of crashing on `useLocation() may be used only in
  // the context of a <Router>`.
  let pathname = ''
  try {
    pathname = Router.useLocation().pathname
  } catch {
    pathname = ''
  }
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false)
  const [deliverable, setDeliverable] = useState<DeliverableType>(initialDeliverable)
  const [query, setQuery] = useState('')
  const [submitted, setSubmitted] = useState(false)

  const { analyse, analyses, loading, error } = useAdvisorStore()

  // Cache key matches the store's so we can read the cached result
  // synchronously without re-rendering on every keystroke. MUST run
  // before any early return — react-hooks/rules-of-hooks forbids
  // conditional hook calls, and the Present-mode guard below is an
  // early return that would otherwise skip this useMemo on some
  // renders and change the hook call order between renders.
  // Cache key matches the store's cacheKeyForAnalysis. When the query is
  // empty the key is harmless (it just never matches any cached entry) —
  // submit is gated separately by hasValidQuery so we never actually fire
  // a request with an empty query.
  const cacheKey = useMemo(
    () => `${deliverable}:${query.trim().slice(0, 200).toLowerCase()}`,
    [deliverable, query],
  )

  // Hide in Present mode — the advisor is internal team scaffolding,
  // not Forest-Capital-facing content.
  if (mode === 'present') return null

  // May 24 2026 — context-aware visibility. The floating button is
  // hidden on pages with their own inline AI surfaces. Controlled
  // opens (Reports screen "Get Advisor Feedback") still work — the
  // hide-route gate only suppresses the standalone floating button.
  const isControlled = controlledOpen !== undefined
  if (!isControlled && HIDDEN_ROUTES.has(pathname)) {
    return null
  }
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

  const cached: AdvisorAnalysis | undefined = analyses[cacheKey]

  // Pre-validate the query: at least one non-whitespace character.
  // Disables the submit button until Bob/Molly types a real question —
  // keeps the user from firing a $0.04-0.06 web-search call against an
  // empty string and getting a generic placeholder response.
  const trimmedQuery = query.trim()
  const hasValidQuery = trimmedQuery.length > 0
  const submitDisabled = loading || !hasValidQuery

  const handleSubmit = async () => {
    if (!hasValidQuery) return
    setSubmitted(true)
    await analyse(trimmedQuery, deliverable, strategyResults)
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
          title="Ask a question — grade-aware academic guidance with verified citations"
          aria-label="Ask a question — Academic Advisor"
        >
          <GraduationCap className="w-4 h-4" />
          <span className="text-xs tracking-wide">Ask a question</span>
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
          className="fixed top-14 right-0 bottom-0 w-96 max-w-[92vw] bg-navy-800 border-l shadow-2xl z-40 flex flex-col"
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
                placeholder="Ask about your findings, deliverables, or what to focus on..."
                rows={3}
                className="w-full bg-navy-900 border border-border rounded px-2.5 py-1.5 text-sm text-white placeholder-muted resize-none"
                data-testid="advisor-query-input"
              />
            </div>

            <button
              type="button"
              onClick={handleSubmit}
              disabled={submitDisabled}
              className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded text-xs font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              style={{
                backgroundColor: 'rgba(245,158,11,0.12)',
                border: `1px solid ${GOLD}40`,
                color: GOLD,
              }}
              title={
                !hasValidQuery
                  ? 'Type a question first'
                  : loading
                    ? 'Searching…'
                    : 'Submit query to the Academic Advisor'
              }
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
                  search, and prioritizes feedback by grade weight.
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
              <CitationItem key={`${c.url}-${idx}`} citation={c} />
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

/**
 * One citation row: title links to the source (opens new tab), hover
 * reveals the web_fetch excerpt so the team can audit the corroborating
 * passage without leaving the panel. When excerpt is null, the tooltip
 * surfaces the fallback message — telling the user the page couldn't
 * be fetched and they need to click through to verify.
 */
const FALLBACK_EXCERPT = 'Excerpt unavailable — click to verify directly'

function CitationItem({ citation }: { citation: VerifiedCitation }) {
  const [hovered, setHovered] = useState(false)
  const hasExcerpt = typeof citation.excerpt === 'string' && citation.excerpt.length > 0
  const tooltipText = hasExcerpt ? (citation.excerpt as string) : FALLBACK_EXCERPT

  return (
    <li
      className="border border-border rounded p-2 bg-navy-900 relative"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setHovered(true)}
      onBlur={() => setHovered(false)}
    >
      <a
        href={citation.url}
        target="_blank"
        rel="noopener noreferrer"
        // Native title attribute serves as the accessible fallback for
        // screen readers and the no-JS case. The visual tooltip below
        // is the primary UX surface — it renders the full passage
        // without truncation.
        title={tooltipText}
        aria-describedby={`excerpt-${citation.url}`}
        className="text-electric text-xs hover:underline flex items-start gap-1.5"
        data-testid="advisor-citation-link"
      >
        <ExternalLink className="w-3 h-3 mt-0.5 shrink-0" />
        <span className="flex-1">{citation.title}</span>
      </a>

      {citation.relevance && (
        <p className="text-muted text-2xs mt-1 leading-relaxed">{citation.relevance}</p>
      )}

      <div className="flex items-center gap-1 mt-1.5">
        <CheckCircle className="w-3 h-3 text-success" />
        <span className="text-2xs text-success">
          {hasExcerpt ? 'Verified — passage retrieved' : 'Verified — passage not retrievable'}
        </span>
      </div>

      {/* Visible tooltip — appears on hover/focus, positioned above the
          citation row so it never covers the link the user is hovering.
          Width pinned to the panel width minus margins so the excerpt
          wraps readably. Uses role=tooltip so assistive tech announces
          it as supplementary, not as a focusable region. */}
      {hovered && (
        <div
          id={`excerpt-${citation.url}`}
          role="tooltip"
          data-testid="advisor-citation-tooltip"
          className="absolute left-2 right-2 bottom-full mb-1 z-50 rounded shadow-lg p-2.5"
          style={{
            backgroundColor: '#1a2438',
            border: '1px solid rgba(245,158,11,0.4)',
            // Subtle gold left accent ties the tooltip back to the
            // advisor's brand colour and signals "advisor surfaced this".
            borderLeftColor: '#f59e0b',
            borderLeftWidth: '2px',
          }}
        >
          <div className="flex items-start gap-1.5">
            {hasExcerpt ? (
              <Quote className="w-3 h-3 mt-0.5 shrink-0" style={{ color: '#f59e0b' }} />
            ) : (
              <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0 text-warning" />
            )}
            <p
              className="text-xs leading-relaxed"
              style={{ color: hasExcerpt ? '#cbd5e1' : '#fcd34d' }}
            >
              {tooltipText}
            </p>
          </div>
        </div>
      )}
    </li>
  )
}
