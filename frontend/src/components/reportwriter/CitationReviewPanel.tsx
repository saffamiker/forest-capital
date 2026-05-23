/**
 * CitationReviewPanel — the review surface for the 7-state citation
 * machine.
 *
 * Fetches every citation row for the current report generation from
 * GET /api/v1/citations/<generation_id>, groups them by state, and
 * renders action buttons for the ones in a needs-review state
 * (pending_review, untrusted_source, not_found). Each action POSTs
 * to /api/v1/citations/<citation_id>/review which applies the
 * 7-state transition and stamps reviewer_email + reviewed_at on the
 * row.
 *
 * Actions:
 *   accept_untrusted   pending_review → human_verified
 *                      "the search result is fine — keep it"
 *   select_alternative any → search_selected
 *                      "pick this entry from passes 2/3 instead"
 *   reject             any → rejected
 *                      "no citation for this concept — skip it"
 *   manual_add         any → manually_added
 *                      "I have a citation that wasn't found by search"
 *
 * Lives in the right-hand main column of the ReportWriter so the
 * panel is visible alongside the paper text. It is collapsed by
 * default when every citation is verified or rejected — only
 * unfolds when there is actually work to do.
 */
import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, CheckCircle, ChevronDown, ChevronRight,
         ExternalLink, Loader2 } from 'lucide-react'


// The four actions the backend accepts. Matches
// CITATION_REVIEW_ACTIONS in tools/template_pipeline.py.
type ReviewAction =
  | 'accept_untrusted'
  | 'select_alternative'
  | 'reject'
  | 'manual_add'


export interface CitationAlternative {
  author?: string | null
  year?: string | null
  title?: string | null
  journal_or_institution?: string | null
  volume_issue_pages?: string | null
  url?: string | null
  pass_source?: string | null
}


export interface Citation {
  id: number
  concept_id: string
  author: string | null
  year: string | null
  title: string | null
  journal_or_institution: string | null
  volume_issue_pages: string | null
  url: string | null
  verification_status: string
  search_query_used: string | null
  alternatives: CitationAlternative[]
  reviewer_email: string | null
  reviewed_at: string | null
  review_action: string | null
  formatted: string | null
}


const NEEDS_REVIEW_STATES = new Set([
  'pending_review', 'untrusted_source', 'not_found',
])

const VERIFIED_STATES = new Set([
  'verified', 'human_verified', 'search_selected', 'manually_added',
])


export interface CitationReviewPanelProps {
  generationId: number | null
  /** Called whenever an action lands so the parent can refresh
   *  pipeline state (the citation_quality colour changes). */
  onReviewed?: () => void
}


export default function CitationReviewPanel({
  generationId, onReviewed,
}: CitationReviewPanelProps) {
  const [citations, setCitations] = useState<Citation[]>([])
  const [loading,   setLoading]   = useState(false)
  const [error,     setError]     = useState<string | null>(null)
  const [open,      setOpen]      = useState(true)
  const [busyId,    setBusyId]    = useState<number | null>(null)

  const fetchCitations = useCallback(async () => {
    if (generationId === null || generationId === undefined) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(
        `/api/v1/citations/${generationId}`,
        { credentials: 'include' })
      if (!res.ok) {
        throw new Error(`Citation fetch returned ${res.status}`)
      }
      const data = await res.json() as { citations: Citation[] }
      setCitations(data.citations ?? [])
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [generationId])

  useEffect(() => {
    if (generationId !== null) {
      void fetchCitations()
    }
  }, [generationId, fetchCitations])

  const submitReview = useCallback(async (
    citationId: number,
    action: ReviewAction,
    payload: Record<string, unknown> = {},
  ) => {
    setBusyId(citationId)
    setError(null)
    try {
      const res = await fetch(
        `/api/v1/citations/${citationId}/review`,
        {
          method:  'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ action, ...payload }),
        })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Review failed (${res.status})`)
      }
      const data = await res.json() as { citation: Citation }
      // Optimistic state update — replace the row in place so the
      // panel doesn't refetch the whole list on every click.
      setCitations((prev) => prev.map((c) =>
        c.id === data.citation.id ? data.citation : c))
      onReviewed?.()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyId(null)
    }
  }, [onReviewed])

  if (generationId === null || generationId === undefined) {
    return null
  }

  const needsReview = citations.filter((c) =>
    NEEDS_REVIEW_STATES.has(c.verification_status))
  const verified = citations.filter((c) =>
    VERIFIED_STATES.has(c.verification_status))
  const rejected = citations.filter((c) =>
    c.verification_status === 'rejected')

  // Collapse by default when everything is reviewed (nothing in the
  // needs-review bucket) — the panel only takes screen space when
  // there's work for Bob to do.
  const defaultClosed = needsReview.length === 0

  return (
    <section
      data-testid="citation-review-panel"
      className="bg-navy-900 border border-navy-700 rounded p-3 space-y-2">
      <header className="flex items-center justify-between">
        <button
          type="button"
          className="flex items-center gap-2 text-sm font-semibold
                     text-text-primary hover:text-electric-blue
                     transition-colors"
          onClick={() => setOpen(!open)}>
          {(open && !defaultClosed) || (open && needsReview.length > 0)
            ? <ChevronDown className="w-4 h-4" />
            : <ChevronRight className="w-4 h-4" />}
          Citation Review
          {needsReview.length > 0 ? (
            <span className="text-2xs px-1.5 py-0.5 rounded
                             bg-amber-500/15 text-amber-300">
              {needsReview.length} need
              {needsReview.length === 1 ? 's' : ''} review
            </span>
          ) : (
            <span className="text-2xs px-1.5 py-0.5 rounded
                             bg-green-500/15 text-green-300">
              All reviewed
            </span>
          )}
        </button>
        {loading ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin text-text-muted" />
        ) : null}
      </header>

      {error ? (
        <p className="text-xs text-red-400 flex items-start gap-1">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          {error}
        </p>
      ) : null}

      {open && !loading && citations.length === 0 ? (
        <p className="text-xs text-text-muted italic">
          No citations to review — citations only appear after
          Step 2 completes.
        </p>
      ) : null}

      {open ? (
        <div className="space-y-2 text-xs">
          {needsReview.length > 0 ? (
            <div className="space-y-1.5">
              <h4 className="text-2xs uppercase tracking-wider
                             text-amber-300">
                Needs review ({needsReview.length})
              </h4>
              {needsReview.map((c) => (
                <CitationRow
                  key={c.id}
                  citation={c}
                  busy={busyId === c.id}
                  onAction={submitReview}
                />
              ))}
            </div>
          ) : null}

          {verified.length > 0 ? (
            <details className="text-2xs">
              <summary className="cursor-pointer text-text-muted
                                   hover:text-text-primary">
                Verified ({verified.length})
              </summary>
              <ul className="mt-1 space-y-0.5 pl-2">
                {verified.map((c) => (
                  <li key={c.id}
                      className="flex items-center gap-1 text-text-secondary">
                    <CheckCircle
                      className="w-3 h-3 text-green-400 shrink-0" />
                    <span className="font-mono">{c.concept_id}</span>
                    <span className="text-text-muted truncate">
                      {c.author ? `— ${c.author}, ${c.year ?? '—'}` : '—'}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}

          {rejected.length > 0 ? (
            <details className="text-2xs">
              <summary className="cursor-pointer text-text-muted
                                   hover:text-text-primary">
                Rejected ({rejected.length})
              </summary>
              <ul className="mt-1 space-y-0.5 pl-2 text-text-muted">
                {rejected.map((c) => (
                  <li key={c.id} className="font-mono">
                    {c.concept_id}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}


interface CitationRowProps {
  citation: Citation
  busy: boolean
  onAction: (
    citationId: number,
    action: ReviewAction,
    payload?: Record<string, unknown>,
  ) => Promise<void>
}


function CitationRow({ citation, busy, onAction }: CitationRowProps) {
  const [showManual, setShowManual] = useState(false)
  const [manualAuthor, setManualAuthor] = useState('')
  const [manualYear,   setManualYear]   = useState('')
  const [manualTitle,  setManualTitle]  = useState('')
  const [manualJournal, setManualJournal] = useState('')
  const [manualUrl,    setManualUrl]    = useState('')

  const canAcceptUntrusted =
    citation.verification_status === 'pending_review'
    || citation.verification_status === 'untrusted_source'

  return (
    <div data-testid={`citation-row-${citation.concept_id}`}
         className="border border-navy-700 rounded p-2 space-y-1.5
                    bg-navy-800/40">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="font-mono text-2xs text-electric-blue">
            {citation.concept_id}
          </p>
          {citation.author ? (
            <p className="text-text-secondary mt-0.5">
              {citation.author} ({citation.year ?? '—'}).{' '}
              <em>{citation.title ?? '—'}</em>
            </p>
          ) : (
            <p className="text-text-muted italic mt-0.5">
              Search query: {citation.search_query_used ?? '—'}
            </p>
          )}
          {citation.url ? (
            <a href={citation.url}
               target="_blank"
               rel="noopener noreferrer"
               className="inline-flex items-center gap-1 text-2xs
                          text-electric-blue hover:underline mt-0.5">
              <ExternalLink className="w-2.5 h-2.5" />
              {citation.journal_or_institution ?? citation.url}
            </a>
          ) : null}
        </div>
      </div>

      {/* Action row — primary actions on top, manual entry collapses
          below to keep the row compact. */}
      <div className="flex flex-wrap items-center gap-1">
        {canAcceptUntrusted ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => onAction(citation.id, 'accept_untrusted')}
            data-testid={`citation-accept-${citation.concept_id}`}
            className="text-2xs px-2 py-0.5 rounded
                       border border-green-500/40 text-green-300
                       hover:bg-green-500/15 disabled:opacity-50
                       disabled:cursor-not-allowed">
            Accept
          </button>
        ) : null}

        <button
          type="button"
          disabled={busy}
          onClick={() => onAction(citation.id, 'reject')}
          data-testid={`citation-reject-${citation.concept_id}`}
          className="text-2xs px-2 py-0.5 rounded
                     border border-red-500/40 text-red-400
                     hover:bg-red-500/15 disabled:opacity-50
                     disabled:cursor-not-allowed">
          Reject
        </button>

        <button
          type="button"
          disabled={busy}
          onClick={() => setShowManual(!showManual)}
          data-testid={`citation-manual-toggle-${citation.concept_id}`}
          className="text-2xs px-2 py-0.5 rounded
                     border border-navy-600 text-text-secondary
                     hover:bg-navy-700/40 disabled:opacity-50
                     disabled:cursor-not-allowed">
          {showManual ? 'Cancel manual' : 'Add manually'}
        </button>

        {busy ? (
          <Loader2 className="w-3 h-3 animate-spin text-text-muted" />
        ) : null}
      </div>

      {/* Alternatives row — only when search returned any. */}
      {citation.alternatives && citation.alternatives.length > 0 ? (
        <div className="pt-1 border-t border-navy-700 space-y-1">
          <p className="text-2xs text-text-muted">
            Alternative results found by wider searches:
          </p>
          {citation.alternatives.map((alt, i) => (
            <button
              key={`${citation.id}-alt-${i}`}
              type="button"
              disabled={busy}
              onClick={() => onAction(
                citation.id, 'select_alternative',
                { selected_alternative: alt })}
              data-testid={`citation-alt-${citation.concept_id}-${i}`}
              className="block w-full text-left text-2xs p-1 rounded
                         border border-navy-700 hover:border-electric-blue
                         hover:bg-navy-800 disabled:opacity-50
                         disabled:cursor-not-allowed transition-colors">
              <span className="text-electric-blue">Use this →</span>{' '}
              {alt.author ?? '?'} ({alt.year ?? '?'}).{' '}
              <em className="text-text-secondary">
                {alt.title ?? alt.url ?? '—'}
              </em>
              {alt.pass_source ? (
                <span className="ml-1 text-text-muted">
                  [{alt.pass_source.replace(/_/g, ' ')}]
                </span>
              ) : null}
            </button>
          ))}
        </div>
      ) : null}

      {/* Manual entry — only when toggled. */}
      {showManual ? (
        <div className="pt-1 border-t border-navy-700 space-y-1">
          <p className="text-2xs text-text-muted">
            Enter the citation manually:
          </p>
          <input
            type="text" placeholder="Author (Surname, A. A.)"
            value={manualAuthor}
            onChange={(e) => setManualAuthor(e.target.value)}
            data-testid={`citation-manual-author-${citation.concept_id}`}
            className="w-full text-2xs px-2 py-1 rounded
                       bg-navy-800 border border-navy-700
                       text-text-primary placeholder:text-text-muted" />
          <div className="flex gap-1">
            <input
              type="text" placeholder="Year"
              value={manualYear}
              onChange={(e) => setManualYear(e.target.value)}
              className="w-20 text-2xs px-2 py-1 rounded
                         bg-navy-800 border border-navy-700
                         text-text-primary placeholder:text-text-muted" />
            <input
              type="text" placeholder="Title"
              value={manualTitle}
              onChange={(e) => setManualTitle(e.target.value)}
              className="flex-1 text-2xs px-2 py-1 rounded
                         bg-navy-800 border border-navy-700
                         text-text-primary placeholder:text-text-muted" />
          </div>
          <input
            type="text" placeholder="Journal / institution"
            value={manualJournal}
            onChange={(e) => setManualJournal(e.target.value)}
            className="w-full text-2xs px-2 py-1 rounded
                       bg-navy-800 border border-navy-700
                       text-text-primary placeholder:text-text-muted" />
          <input
            type="text" placeholder="URL"
            value={manualUrl}
            onChange={(e) => setManualUrl(e.target.value)}
            className="w-full text-2xs px-2 py-1 rounded
                       bg-navy-800 border border-navy-700
                       text-text-primary placeholder:text-text-muted" />
          <button
            type="button"
            disabled={busy || !manualAuthor || !manualYear || !manualTitle}
            onClick={() => onAction(
              citation.id, 'manual_add', {
                manual_citation: {
                  author:                 manualAuthor,
                  year:                   manualYear,
                  title:                  manualTitle,
                  journal_or_institution: manualJournal || null,
                  volume_issue_pages:     null,
                  url:                    manualUrl || null,
                },
              })}
            data-testid={`citation-manual-submit-${citation.concept_id}`}
            className="text-2xs px-2 py-1 rounded
                       bg-electric-blue text-navy-950 font-medium
                       hover:bg-electric-blue/90 disabled:opacity-50
                       disabled:cursor-not-allowed">
            Save manual citation
          </button>
        </div>
      ) : null}
    </div>
  )
}
