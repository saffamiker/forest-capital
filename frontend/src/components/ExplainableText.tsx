/**
 * frontend/src/components/ExplainableText.tsx
 *
 * Three-level explanation wrapper for any term, metric label, or value.
 *
 *   Level 1 — hover tooltip. Dotted underline + ⓘ icon. Native-feel
 *             tooltip with the 1-2 sentence `hover` text from
 *             glossaryStore.terms[term].
 *   Level 2 — click panel. Inline expansion below the term showing
 *             what/why/in_context from glossaryStore. Closes on
 *             outside-click or Escape.
 *   Level 3 — "Learn More" link inside the click panel. Opens a side
 *             drawer with academic context drawn from references.json
 *             (LearnMoreSidePanel).
 *
 * Renders children unchanged when:
 *   - mode is not 'commentary' (Analyst/Present mode → no chrome)
 *   - glossaryStore has no entry for `term` (no content to show)
 *
 * Failing silently rather than rendering a broken "no data" tooltip
 * keeps the dashboard usable when the Explainer endpoint is down.
 */
import { useEffect, useRef, useState } from 'react'
import { Info, BookOpen } from 'lucide-react'
import { useUI } from '../context/UIContext'
import { useGlossaryStore } from '../stores/glossaryStore'
import LearnMoreSidePanel from './LearnMoreSidePanel'

interface Props {
  /** Glossary key — must match a term returned by /api/explain/terms. */
  term: string
  /** Strategy context (optional): scopes "what this value means" to one strategy. */
  strategy?: string
  /** Display text to wrap — usually a metric label or column header. */
  children: React.ReactNode
}

export default function ExplainableText({ term, strategy, children }: Props) {
  const { mode } = useUI()
  const entry = useGlossaryStore((s) => s.terms[term])
  const termsLoading = useGlossaryStore((s) => s.termsLoading)
  const loadTerms = useGlossaryStore((s) => s.loadTerms)

  // Lazy-load the glossary on first Commentary-mode render. The store's
  // termsLoaded guard short-circuits subsequent calls, so 50 ExplainableText
  // instances mounting simultaneously still fire exactly one
  // /api/explain/terms request. Skipped in Analyst/Present mode because
  // the chrome isn't visible there — no point paying for the explanation
  // if the user can't see it.
  useEffect(() => {
    if (mode === 'commentary') {
      void loadTerms()
    }
  }, [mode, loadTerms])

  const [open, setOpen] = useState(false)
  const [learnMoreOpen, setLearnMoreOpen] = useState(false)
  const panelRef = useRef<HTMLSpanElement>(null)

  // Close panel on outside click or Escape — same UX as Settings cog.
  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Analyst / Present mode: render children with no chrome at all.
  // CLAUDE.md: "explanations still available on explicit right-click only"
  // — but for Sprint 6 we keep Analyst clean and rely on Commentary mode
  // for any hover affordance.
  if (mode !== 'commentary') {
    return <>{children}</>
  }

  // Commentary mode but glossary not yet loaded → render the inline ⓘ icon
  // but in a muted state so the user knows tooltips are coming.
  if (!entry) {
    return (
      <span className="inline-flex items-center gap-1 border-b border-dotted border-muted/40">
        <span>{children}</span>
        {termsLoading && <Info className="w-3 h-3 text-muted/40 animate-pulse" aria-label="loading" />}
      </span>
    )
  }

  return (
    <span ref={panelRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 border-b border-dotted border-electric/40 hover:border-electric cursor-help"
        title={entry.hover}
        aria-expanded={open}
        aria-label={`Explain ${term}`}
      >
        <span>{children}</span>
        <Info className="w-3 h-3 text-electric/70" />
      </button>

      {open && (
        <span
          className="absolute z-40 left-0 mt-1 w-72 card p-3 shadow-card text-xs leading-relaxed"
          style={{ borderColor: '#3b82f640' }}
          role="dialog"
        >
          <span className="block">
            <span className="text-2xs uppercase tracking-wide text-muted">What</span>
            <span className="block text-white mt-0.5">{entry.what}</span>
          </span>
          <span className="block mt-2">
            <span className="text-2xs uppercase tracking-wide text-muted">Why it matters</span>
            <span className="block text-white mt-0.5">{entry.why}</span>
          </span>
          {entry.in_context && (
            <span className="block mt-2">
              <span className="text-2xs uppercase tracking-wide text-muted">
                {strategy ? `For ${strategy.replace(/_/g, ' ')}` : 'This session'}
              </span>
              <span className="block text-white mt-0.5">{entry.in_context}</span>
            </span>
          )}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); setLearnMoreOpen(true) }}
            className="mt-3 inline-flex items-center gap-1.5 text-electric text-2xs hover:text-blue-300"
          >
            <BookOpen className="w-3 h-3" />
            Learn more · academic context
          </button>
        </span>
      )}

      {learnMoreOpen && (
        <LearnMoreSidePanel
          term={term}
          entry={entry}
          onClose={() => setLearnMoreOpen(false)}
        />
      )}
    </span>
  )
}
