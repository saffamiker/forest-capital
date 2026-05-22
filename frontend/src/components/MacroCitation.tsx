/**
 * MacroCitation — inline badge that surfaces a [Macro: <category>]
 * citation emitted by an agent that drew on the current macro digest.
 *
 * Backend (May 22 2026): tools/macro_context.py instructs every
 * macro-context-aware agent to cite digest signals inline with
 * [Macro: <category>] using the same category label from the digest's
 * key_signals block. The frontend parses those tags into styled
 * teal/cyan pills (distinct from the Strong/Developing/Needs Work
 * verdict badges so the user reads "this claim is grounded in current
 * macro data" rather than confusing the signal with a quality
 * verdict).
 *
 * Surfaces using this component:
 *   - ExplainerPanel (CIO follow-up thread)
 *   - AcademicReviewButton (verdict + peer responses)
 *   - CouncilDebate (agent narratives)
 *   - Anywhere else agent text is rendered.
 *
 * Hover tooltip — when the citation matches a signal in the latest
 * digest, the tooltip shows the digest date and the signal text.
 * Falls back to "Macro context: <date>" when no matching signal is
 * available (e.g. an agent cited a category not represented in the
 * current digest, or the digest has rotated since the agent wrote).
 */
import { useEffect, useState, useMemo } from 'react'
import axios from 'axios'
import { Newspaper } from 'lucide-react'


interface MacroSignal {
  category:    string
  signal:      string
  implication: string
  source_url:  string
}

interface MacroDigest {
  generated_at:    string | null
  summary_text:    string
  key_signals:     MacroSignal[]
  citation_urls:   string[]
}

// Module-level singleton cache — every consumer reads the same fetch
// result rather than hammering /api/v1/research/latest. Mirrors the
// existing module-level patterns in the frontend (generationJobs,
// tourBus).
let _digestPromise: Promise<MacroDigest | null> | null = null

async function _fetchDigest(): Promise<MacroDigest | null> {
  if (_digestPromise) return _digestPromise
  _digestPromise = axios.get<{ digest: MacroDigest | null }>(
    '/api/v1/research/latest',
  ).then((r) => r.data?.digest ?? null)
   .catch(() => null)
  return _digestPromise
}

/** Hook returning the latest digest. Cached for the page lifetime. */
export function useMacroDigest(): MacroDigest | null {
  const [digest, setDigest] = useState<MacroDigest | null>(null)
  useEffect(() => {
    let cancelled = false
    void _fetchDigest().then((d) => { if (!cancelled) setDigest(d) })
    return () => { cancelled = true }
  }, [])
  return digest
}


/** A single [Macro: <category>] inline badge. */
export function MacroCitationBadge({ category }: { category: string }) {
  const digest = useMacroDigest()
  const signal = useMemo(
    () => digest?.key_signals?.find(
      (s) => s.category?.toLowerCase() === category.toLowerCase()),
    [digest, category],
  )
  const dateLabel = useMemo(() => {
    if (!digest?.generated_at) return null
    try {
      const d = new Date(digest.generated_at)
      if (Number.isNaN(d.getTime())) return null
      return d.toLocaleDateString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
      })
    } catch { return null }
  }, [digest?.generated_at])

  // Tooltip body: prefer the matched signal text + date; fall back to
  // "Macro context: <date>" when no signal matches.
  const tooltipBody = signal
    ? `${signal.signal}${signal.implication ? `\nImplication: ${signal.implication}` : ''}\n— Macro digest, ${dateLabel ?? 'unknown date'}`
    : `Macro context${dateLabel ? `: ${dateLabel}` : ''}`

  return (
    <span
      role="note"
      title={tooltipBody}
      data-testid={`macro-citation-${category}`}
      className="inline-flex items-center gap-1 align-baseline mx-0.5
                 px-1.5 py-0.5 rounded-full border text-2xs font-semibold
                 border-teal-400/40 bg-teal-500/10 text-teal-200
                 whitespace-nowrap cursor-help"
    >
      <Newspaper className="w-2.5 h-2.5" />
      Macro: {category}
    </span>
  )
}


/**
 * MACRO_CITATION_RE — extracts [Macro: <category>] tags from agent
 * text. The category is captured as group 1; categories permitted are
 * letters, digits, underscore, dash, and dot (matching the digest
 * category strings: monetary_policy, equity_correlation, etc.). The
 * pattern is intentionally permissive on whitespace so an agent that
 * wrote [Macro:monetary_policy] or [Macro: monetary_policy] both
 * resolve. Used by renderWithMacroCitations below.
 */
export const MACRO_CITATION_RE = /\[Macro:\s*([A-Za-z0-9_.-]+)\s*\]/g


/**
 * Parses a string for [Macro: ...] tags and returns a React element
 * tree where each tag is replaced with a MacroCitationBadge. Used by
 * surfaces that don't render through Markdown (the AcademicReview
 * verdict sections render through Markdown and pick up the tags via
 * the Markdown renderer; the CIO thread bubbles in ExplainerPanel
 * render as plain text and use this helper).
 *
 * Returns React.ReactNode[] so the caller can spread the result into
 * a wrapper element. Plain-text segments are interleaved with badge
 * nodes in document order so reading flow is preserved.
 */
export function renderWithMacroCitations(text: string): React.ReactNode[] {
  if (!text) return []
  const nodes: React.ReactNode[] = []
  let lastIdx = 0
  let m: RegExpExecArray | null
  // Reset lastIndex on the shared regex so a parallel call does not
  // leak state. RegExp objects with the `g` flag carry per-regex
  // lastIndex which is mutated by exec().
  MACRO_CITATION_RE.lastIndex = 0
  while ((m = MACRO_CITATION_RE.exec(text)) !== null) {
    if (m.index > lastIdx) {
      nodes.push(text.slice(lastIdx, m.index))
    }
    nodes.push(
      <MacroCitationBadge key={`macro-${m.index}`} category={m[1]} />,
    )
    lastIdx = MACRO_CITATION_RE.lastIndex
  }
  if (lastIdx < text.length) {
    nodes.push(text.slice(lastIdx))
  }
  return nodes
}


/**
 * extractMacroCategories — pulls out the unique macro categories
 * cited in a block of text. Used by surfaces that need to render an
 * attribution footer when at least one citation is present (the
 * ExplainerPanel footer, the AcademicReview verdict footer). Order-
 * preserving and de-duplicated.
 */
export function extractMacroCategories(text: string): string[] {
  if (!text) return []
  const out: string[] = []
  MACRO_CITATION_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = MACRO_CITATION_RE.exec(text)) !== null) {
    const cat = m[1].trim().toLowerCase()
    if (!out.includes(cat)) out.push(cat)
  }
  return out
}


/**
 * MacroAttributionFooter — small footer surfaced on any panel that
 * rendered at least one macro citation. Names the digest source
 * (Forest Capital Research Digest) and the digest date so the reader
 * knows the agent's grounding without having to navigate away from
 * the panel.
 */
export function MacroAttributionFooter(
  { categories }: { categories: string[] },
) {
  const digest = useMacroDigest()
  if (!categories.length) return null
  const dateLabel = (() => {
    if (!digest?.generated_at) return ''
    try {
      const d = new Date(digest.generated_at)
      if (Number.isNaN(d.getTime())) return ''
      return d.toLocaleDateString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
      })
    } catch { return '' }
  })()
  return (
    <div
      data-testid="macro-attribution-footer"
      className="mt-2 pt-2 border-t border-border/40 flex items-start gap-1.5"
    >
      <Newspaper className="w-3 h-3 text-teal-300 shrink-0 mt-0.5" />
      <div className="text-2xs text-muted leading-relaxed">
        Macro context: Forest Capital Research Digest
        {dateLabel ? `, ${dateLabel}` : ''}
        {' · '}
        <a href="/qa#macro-research"
           className="underline hover:text-teal-200">
          View full digest
        </a>
      </div>
    </div>
  )
}
