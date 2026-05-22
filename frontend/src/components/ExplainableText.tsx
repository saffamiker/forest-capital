/**
 * frontend/src/components/ExplainableText.tsx
 *
 * Three-level explanation wrapper for any term, metric label, or value.
 *
 *   Level 1 — hover tooltip. Dotted underline + ⓘ icon. A small styled
 *             tooltip (matching the click panel) with the 1-2 sentence
 *             `hover` text from glossaryStore.terms[term].
 *   Level 2 — click panel. Inline expansion below the term showing
 *             what/why/this_session from glossaryStore. Closes on
 *             outside-click or Escape.
 *   Level 3 — "Learn More" link inside the click panel. Opens a side
 *             drawer with academic context drawn from references.json
 *             (LearnMoreSidePanel).
 *
 * Renders children bare — no underline, no icon, no chrome — when:
 *   - mode is not 'commentary' (Analyst/Present mode), OR
 *   - the glossary has no entry for `term` (still loading, or the
 *     Explainer returned nothing).
 *
 * The dotted underline appears ONLY when a hover/click would actually
 * do something — there is no inert underline that leads nowhere.
 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Info, BookOpen, Users } from 'lucide-react'
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
  const loadTerms = useGlossaryStore((s) => s.loadTerms)

  // Lazy-load the glossary on first Commentary-mode render. The store
  // uses single-flight + a 60-second debounce on termsLastLoadedAt, so
  // 50 ExplainableText instances mounting simultaneously still fire
  // exactly one /api/explain/terms request — and a completed council
  // session re-anchors the glossary on the next call after the window.
  // Skipped in Analyst/Present mode because the chrome isn't visible
  // there — no point paying for the explanation if the user can't see it.
  useEffect(() => {
    if (mode === 'commentary') {
      void loadTerms()
    }
  }, [mode, loadTerms])

  const [open, setOpen] = useState(false)
  const [hovered, setHovered] = useState(false)
  const [learnMoreOpen, setLearnMoreOpen] = useState(false)
  const panelRef = useRef<HTMLSpanElement>(null)
  const navigate = useNavigate()

  // Hand off to the council with the term as a contextual question
  // pre-filled. Mirrors the askCouncil flow in ExplainerPanel and
  // DataExplainPanel — the council screen reads the question from
  // route state, focuses the input, and does NOT auto-submit so the
  // user reviews and convenes when ready. Added May 22 2026 (Molly
  // UAT Group 3): the Ask-the-Council affordance lived on both
  // drawer-style explainers but was missing from the inline term-
  // explanation panel, breaking the continuation path on every
  // Commentary-mode click.
  const askCouncil = () => {
    if (!entry) return
    const termLabel = entry.what
      ? `${term} (${entry.what.split('.')[0]})`
      : term
    const strategyPart = strategy
      ? ` for the ${strategy.replace(/_/g, ' ')} strategy`
      : ''
    const prefillQuestion =
      `Can you explain ${termLabel}${strategyPart} in the context of `
      + 'our asset allocation analysis and the 2022 correlation regime '
      + 'break?'
    navigate('/council', { state: { prefillQuestion } })
    setOpen(false)
  }

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

  // Analyst / Present mode, or no glossary entry yet (still loading, or
  // the Explainer returned nothing for this term): render children bare.
  // No inert underline — the dotted underline is shown only when a
  // hover/click would actually surface an explanation.
  if (mode !== 'commentary' || !entry) {
    return <>{children}</>
  }

  return (
    <span ref={panelRef} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        className="inline-flex items-center gap-1 border-b border-dotted border-electric/40 hover:border-electric cursor-help"
        aria-expanded={open}
        aria-label={`Explain ${term}`}
      >
        <span>{children}</span>
        <Info className="w-3 h-3 text-electric/70" />
      </button>

      {/* Level 1 — custom hover tooltip. Styled to match the click
          panel; suppressed while the click panel is open so the two
          never stack. pointer-events-none so it never steals the
          mouse-out that dismisses it.

          Width / overflow rules mirror the InfoIcon tooltip — caps at
          the design width on desktop, shrinks to fit narrow viewports
          (iPhone SE), wraps long unbreakable strings, scrolls
          vertically when content exceeds the height cap. UAT feedback
          flagged column tooltips clipping; the cap makes the wrap +
          scroll behaviour predictable across viewports. */}
      {hovered && !open && (
        <span
          role="tooltip"
          style={{
            maxWidth: 'min(224px, calc(100vw - 24px))',
            maxHeight: 'min(60vh, 320px)',
            overflowY: 'auto',
          }}
          className="absolute z-50 left-0 mt-1 w-56 card px-3 py-1.5
                     text-2xs leading-relaxed text-slate-200 shadow-card
                     pointer-events-none
                     break-words [overflow-wrap:anywhere] whitespace-normal"
        >
          {entry.hover}
        </span>
      )}

      {open && (
        <span
          className="absolute z-40 left-0 mt-1 w-72 card p-3 shadow-card
                     text-xs leading-relaxed
                     break-words [overflow-wrap:anywhere]"
          style={{
            borderColor: '#3b82f640',
            maxWidth: 'min(288px, calc(100vw - 24px))',
            maxHeight: 'min(70vh, 480px)',
            overflowY: 'auto',
          }}
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
          {entry.this_session && (
            <span className="block mt-2">
              <span className="text-2xs uppercase tracking-wide text-muted">
                {strategy ? `For ${strategy.replace(/_/g, ' ')}` : 'This session'}
              </span>
              <span className="block text-white mt-0.5">{entry.this_session}</span>
            </span>
          )}
          <span className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); setLearnMoreOpen(true) }}
              className="inline-flex items-center gap-1.5 text-electric text-2xs hover:text-blue-300"
            >
              <BookOpen className="w-3 h-3" />
              Learn more · academic context
            </button>
            {/* Ask the Council — opens /council with a contextual
                question pre-filled. Mirrors the affordance the
                ExplainerPanel and DataExplainPanel drawer surfaces
                already carry, so every Commentary-mode explainer
                surface offers the same continuation path. */}
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); askCouncil() }}
              data-testid={`explainable-ask-council-${term}`}
              className="inline-flex items-center gap-1.5 text-electric text-2xs hover:text-blue-300"
            >
              <Users className="w-3 h-3" />
              Ask the Council about this
            </button>
          </span>
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
