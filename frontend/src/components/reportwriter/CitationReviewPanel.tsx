/**
 * CitationReviewPanel — 3-level Finding ▸ Type ▸ Citation hierarchy.
 *
 * REDESIGNED May 26 2026 (migration 045 + design doc).
 *
 * Before: a flat list of citations grouped only by verification_status
 * (Needs review / Verified / Rejected). The reviewer could see WHICH
 * citations needed action but not WHICH FINDINGS each citation was
 * supporting — so a high-priority statistical-audit failure or QA
 * methodology check with NO supporting citation was invisible.
 *
 * After: the panel surfaces every Level-1 finding (the high+medium-rank
 * rows from the latest substantive statistical audit and the latest QA
 * methodology verdict) as a collapsible section. Under each finding,
 * citations are sub-grouped by citation_type (theoretical / empirical /
 * methodological / regulatory / data_source / practitioner). The Level-3
 * citation row carries a checkbox that records the citation→finding
 * match in the citation_finding_matches table.
 *
 * Every citation appears under every finding — checked when matched,
 * unchecked-and-dimmed otherwise — so the reviewer can recruit any
 * citation as supporting evidence for any finding without navigating
 * away from the finding's context. A citation may be matched to
 * multiple findings (the redesign explicitly supports many-to-many).
 *
 * GAP FLAG: a finding with zero matched citations renders an amber
 * "no supporting citations yet" warning, surfacing the coverage gap.
 *
 * SOFT REFRESH: loadFindings() on the store re-seeds the findings
 * table on every call from the live audit + QA state, so the panel
 * always reflects the current analytical findings — not a snapshot
 * from when citation review was first opened. The team's match work
 * survives the re-seed (UPSERT on (generation_id, source, source_id)).
 *
 * The existing CitationRow / AlternativeCard / EvidenceSection
 * components are reused unchanged — the per-row review controls
 * (accept primary / reject / manual override / select alternative)
 * keep their behaviour. Only the surrounding hierarchy is new.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import {
  AlertCircle, AlertTriangle, ChevronDown, ChevronRight,
  ExternalLink, Loader2, Info,
} from 'lucide-react'
import {
  useCitationReviewStore,
  type Citation, type CitationAlternative, type Finding,
} from '../../stores/citationReviewStore'


// The four actions the backend accepts. Matches
// CITATION_REVIEW_ACTIONS in tools/template_pipeline.py.
type ReviewAction =
  | 'accept_untrusted'
  | 'select_alternative'
  | 'reject'
  | 'manual_add'


// Re-export the shared types so callers that imported them from the
// component continue to work — they now live in citationReviewStore
// so the persistence layer and the component agree on the contract.
export type { Citation, CitationAlternative }


const NEEDS_REVIEW_STATES = new Set([
  'pending_review', 'untrusted_source', 'not_found',
])

const VERIFIED_STATES = new Set([
  'verified', 'human_verified', 'search_selected', 'manually_added',
])


// Human-readable label for each search pass. Drives the
// "Why ranked below" line on every alternative card. Keys mirror
// the pass_source values written by template_pipeline.source_citations.
const PASS_SOURCE_LABEL: Record<string, string> = {
  'pass_1_off_trusted':     'Pass 1 — off-trusted domain',
  'pass_2_academic':        'Pass 2 — academic',
  'pass_3_widest':           'Pass 3 — widest publishable',
  'pass_3_off_publishable': 'Pass 3 — off-publishable',
  'previously_primary':     'Previously primary (demoted on swap)',
}


const PASS_RANK_REASON: Record<string, string> = {
  'pass_1_off_trusted': (
    'Found in the first pass but on a domain outside the trusted '
    + 'set (Journal of Finance, NBER, BIS, Fed, AQR, CFA, SSRN, '
    + 'JSTOR). Authority is lower than a trusted-domain hit but '
    + 'the source remains close to the original claim.'),
  'pass_2_academic': (
    'Surfaced by the wider academic pass (university press, '
    + 'regional Fed, SEC, OECD, World Bank). Authority is below '
    + 'a trusted-domain primary source.'),
  'pass_3_widest': (
    'Surfaced by the widest publishable pass — a .org / .gov / '
    + '.edu / .int domain outside the academic set. Use as '
    + 'evidence of last resort.'),
  'pass_3_off_publishable': (
    'Surfaced by the widest pass but landed off the publishable '
    + 'domain set. Treat with caution.'),
  'previously_primary': (
    'This citation was the primary choice before the current '
    + 'selection. Demoted when a different alternative was '
    + 'accepted — still available if the swap is reverted.'),
}


// Status-badge style per verification_status.
const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
  verified:         { label: 'Verified',       cls: 'bg-green-500/15 text-green-300' },
  human_verified:   { label: 'Human verified', cls: 'bg-green-500/15 text-green-300' },
  search_selected:  { label: 'Selected',       cls: 'bg-green-500/15 text-green-300' },
  manually_added:   { label: 'Manual',         cls: 'bg-green-500/15 text-green-300' },
  pending_review:   { label: 'Needs review',   cls: 'bg-amber-500/15 text-amber-300' },
  untrusted_source: { label: 'Needs review',   cls: 'bg-amber-500/15 text-amber-300' },
  not_found:        { label: 'Not found',      cls: 'bg-amber-500/15 text-amber-300' },
  rejected:         { label: 'Rejected',       cls: 'bg-red-500/15 text-red-400' },
}


// Citation-type display label + colour. Aligned with the six-value
// taxonomy (migration 045): theoretical | empirical | methodological |
// regulatory | data_source | practitioner.
const CITATION_TYPE_STYLES: Record<string, { label: string; cls: string }> = {
  theoretical: {
    label: 'Theoretical',
    cls: 'bg-blue-900/40 text-blue-300 border border-blue-700/40',
  },
  empirical: {
    label: 'Empirical',
    cls: 'bg-green-900/40 text-green-300 border border-green-700/40',
  },
  methodological: {
    label: 'Methodological',
    cls: 'bg-purple-900/40 text-purple-300 border border-purple-700/40',
  },
  regulatory: {
    label: 'Regulatory',
    cls: 'bg-teal-900/40 text-teal-300 border border-teal-700/40',
  },
  data_source: {
    label: 'Data source',
    cls: 'bg-cyan-900/40 text-cyan-300 border border-cyan-700/40',
  },
  practitioner: {
    label: 'Practitioner',
    cls: 'bg-amber-900/40 text-amber-300 border border-amber-700/40',
  },
}


// Display order for the Level-2 type sub-groups. Theory first, then
// empirical evidence, then method, then regulatory/data context, then
// practitioner — the order a reviewer would prefer to read a finding's
// support stack in.
const CITATION_TYPE_ORDER: readonly string[] = [
  'theoretical', 'empirical', 'methodological',
  'regulatory', 'data_source', 'practitioner',
]


function _confidenceLabel(score: number | null | undefined): string {
  if (score === null || score === undefined) return '—'
  return score.toFixed(2)
}


function _confidenceCls(score: number | null | undefined): string {
  if (score === null || score === undefined) {
    return 'bg-navy-800 text-text-muted border-navy-700'
  }
  if (score >= 0.85) {
    return 'bg-green-500/15 text-green-300 border-green-500/30'
  }
  if (score >= 0.65) {
    return 'bg-amber-500/15 text-amber-300 border-amber-500/30'
  }
  return 'bg-red-500/15 text-red-400 border-red-500/30'
}


export interface CitationReviewPanelProps {
  generationId: number | null
  /** Called whenever an action lands so the parent can refresh
   *  pipeline state (the citation_quality colour changes). */
  onReviewed?: () => void
}


export default function CitationReviewPanel({
  generationId, onReviewed,
}: CitationReviewPanelProps) {
  const [open, setOpen] = useState(true)
  const [busyId, setBusyId] = useState<number | null>(null)

  const citations = useCitationReviewStore(
    (s) => (generationId !== null
              ? s.citationsByGenerationId[generationId] ?? []
              : []))
  const findings = useCitationReviewStore(
    (s) => (generationId !== null
              ? s.findingsByGenerationId[generationId] ?? []
              : []))
  const lastFindingsAt = useCitationReviewStore(
    (s) => (generationId !== null
              ? s.lastFindingsFetchAt[generationId]
              : undefined))
  const error = useCitationReviewStore(
    (s) => (generationId !== null
              ? s.findingsErrorByGenerationId[generationId] ?? null
              : null))
  const inFlight = useCitationReviewStore(
    (s) => (generationId !== null
              ? Boolean(s.findingsInFlight[generationId])
              : false))
  const loadFindings = useCitationReviewStore((s) => s.loadFindings)
  const toggleMatch  = useCitationReviewStore((s) => s.toggleMatch)
  const upsertCitation = useCitationReviewStore((s) => s.upsertCitation)

  // loadFindings() backs the redesigned panel. It also populates the
  // citations slice (the /findings endpoint returns both in one round
  // trip), so we don't need the legacy load() call here.
  useEffect(() => {
    if (generationId !== null && generationId !== undefined) {
      void loadFindings(generationId)
    }
  }, [generationId, loadFindings])

  const loading =
    inFlight && findings.length === 0 && citations.length === 0
    && !lastFindingsAt

  const submitReview = useCallback(async (
    citationId: number,
    action: ReviewAction,
    payload: Record<string, unknown> = {},
  ) => {
    if (generationId === null || generationId === undefined) return
    setBusyId(citationId)
    try {
      const res = await axios.post<{ citation: Citation }>(
        `/api/v1/citations/${citationId}/review`,
        { action, ...payload })
      upsertCitation(generationId, res.data.citation)
      onReviewed?.()
    } catch (e) {
      const msg = axios.isAxiosError(e)
        ? (e.response?.data?.detail || e.message)
        : (e as Error).message
      useCitationReviewStore.setState((s) => ({
        findingsErrorByGenerationId: {
          ...s.findingsErrorByGenerationId,
          [generationId]: String(msg),
        },
      }))
    } finally {
      setBusyId(null)
    }
  }, [generationId, upsertCitation, onReviewed])

  const handleToggleMatch = useCallback(async (
    citationId: number,
    findingId: number,
    currentlyMatched: boolean,
  ) => {
    if (generationId === null || generationId === undefined) return
    try {
      await toggleMatch(
        generationId, citationId, findingId, currentlyMatched)
      onReviewed?.()
    } catch (e) {
      // toggleMatch already reverted optimistic state; surface the
      // error so the reviewer knows the toggle didn't land.
      const msg = axios.isAxiosError(e)
        ? (e.response?.data?.detail || e.message)
        : (e as Error).message
      useCitationReviewStore.setState((s) => ({
        findingsErrorByGenerationId: {
          ...s.findingsErrorByGenerationId,
          [generationId]: String(msg),
        },
      }))
    }
  }, [generationId, toggleMatch, onReviewed])

  // Compute summary numbers up-front so they're available for the
  // header chip even before any finding is expanded.
  const summary = useMemo(() => {
    const gaps = findings.filter((f) => f.matched_count === 0).length
    return {
      n_findings: findings.length,
      n_citations: citations.length,
      n_gaps: gaps,
    }
  }, [findings, citations])

  if (generationId === null || generationId === undefined) {
    return null
  }

  return (
    <section
      data-testid="citation-review-panel"
      className="bg-navy-900 border border-navy-700 rounded p-3 space-y-2">
      <header className="flex items-center justify-between gap-2">
        <button
          type="button"
          className="flex items-center gap-2 text-sm font-semibold
                     text-text-primary hover:text-electric-blue
                     transition-colors min-w-0"
          onClick={() => setOpen(!open)}>
          {open
            ? <ChevronDown className="w-4 h-4 shrink-0" />
            : <ChevronRight className="w-4 h-4 shrink-0" />}
          <span className="truncate">Citation Review</span>
          {summary.n_findings > 0 ? (
            <span className="text-2xs px-1.5 py-0.5 rounded
                             bg-navy-800 text-text-secondary shrink-0">
              {summary.n_findings} finding
              {summary.n_findings === 1 ? '' : 's'} ·{' '}
              {summary.n_citations} citation
              {summary.n_citations === 1 ? '' : 's'}
            </span>
          ) : null}
          {summary.n_gaps > 0 ? (
            <span className="text-2xs px-1.5 py-0.5 rounded
                             bg-amber-500/15 text-amber-300 shrink-0">
              {summary.n_gaps} gap{summary.n_gaps === 1 ? '' : 's'}
            </span>
          ) : null}
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

      {open && !loading && findings.length === 0
            && citations.length === 0 ? (
        <p className="text-xs text-text-muted italic">
          No findings or citations to review — citations only appear
          after Step 2 completes and findings only appear after a
          substantive audit or QA verdict has been recorded.
        </p>
      ) : null}

      {open && !loading && findings.length === 0
            && citations.length > 0 ? (
        <p className="text-xs text-text-muted italic">
          No high or medium-rank findings to match citations against —
          the analytical state is currently clean. Citations remain
          reviewable below.
        </p>
      ) : null}

      {open && findings.length > 0 ? (
        <div data-testid="findings-list" className="space-y-2 text-xs">
          {findings.map((finding) => (
            <FindingSection
              key={finding.id}
              finding={finding}
              citations={citations}
              busyId={busyId}
              onToggleMatch={handleToggleMatch}
              onAction={submitReview}
            />
          ))}
        </div>
      ) : null}
    </section>
  )
}


// ── FindingSection — Level 1 wrapper for one finding ────────────────────────


interface FindingSectionProps {
  finding: Finding
  citations: Citation[]
  busyId: number | null
  onToggleMatch: (
    citationId: number,
    findingId: number,
    currentlyMatched: boolean,
  ) => Promise<void>
  onAction: (
    citationId: number,
    action: ReviewAction,
    payload?: Record<string, unknown>,
  ) => Promise<void>
}


function FindingSection({
  finding, citations, busyId, onToggleMatch, onAction,
}: FindingSectionProps) {
  // Default-expand any finding that has a match or is unmatched
  // (a gap demands attention). Collapsed-by-default would hide both
  // the work-so-far AND the gaps. Local UI state — fine to reset
  // on remount; the per-row tile state is what really matters and
  // that lives in the store via CitationRow.
  const [expanded, setExpanded] = useState(true)

  const rankBadge = finding.rank === 'high'
    ? { label: 'HIGH',   cls: 'bg-red-500/20 text-red-300 border border-red-500/40' }
    : { label: 'MEDIUM', cls: 'bg-amber-500/15 text-amber-300 border border-amber-500/40' }

  // Three Level-1 source streams (May 26 2026, citation_findings.py).
  // ANALYTICAL is the primary citation target — Sharpe / regime /
  // factor claims that need a supporting reference. AUDIT and QA are
  // methodology / operational findings. The badge colour is the
  // reader's at-a-glance cue for which kind of claim each finding is.
  const sourceBadge = (() => {
    switch (finding.source) {
      case 'analytical':
        return {
          label: 'Analytical',
          cls: 'bg-emerald-900/40 text-emerald-300 border border-emerald-700/40',
        }
      case 'audit':
        return {
          label: 'Audit',
          cls: 'bg-blue-900/40 text-blue-300 border border-blue-700/40',
        }
      case 'qa':
        return {
          label: 'QA',
          cls: 'bg-purple-900/40 text-purple-300 border border-purple-700/40',
        }
      default:
        return {
          label: finding.source,
          cls: 'bg-navy-800 text-text-muted border border-navy-700',
        }
    }
  })()

  const hasGap = finding.matched_count === 0

  // Group citations by type for the Level-2 sub-headers. Citations
  // matched to THIS finding sort to the top within each type so the
  // reviewer's work-so-far is visible at a glance. Within each bucket
  // the matched citations are also sorted by confidence_score desc.
  const groupedByType = useMemo(() => {
    const groups: Record<string, Citation[]> = {}
    for (const c of citations) {
      const type = (c.citation_type || 'theoretical').toLowerCase()
      const bucket = groups[type] ?? (groups[type] = [])
      bucket.push(c)
    }
    // Sort each bucket: matched-to-this-finding first, then by
    // confidence desc within each matched/unmatched group.
    for (const bucket of Object.values(groups)) {
      bucket.sort((a, b) => {
        const aMatched = (a.matched_finding_ids ?? []).includes(finding.id)
        const bMatched = (b.matched_finding_ids ?? []).includes(finding.id)
        if (aMatched !== bMatched) return aMatched ? -1 : 1
        const aConf = a.confidence_score ?? -1
        const bConf = b.confidence_score ?? -1
        return bConf - aConf
      })
    }
    return groups
  }, [citations, finding.id])

  const orderedTypes = useMemo(() => {
    const present = Object.keys(groupedByType)
    return CITATION_TYPE_ORDER
      .filter((t) => present.includes(t))
      .concat(present.filter((t) => !CITATION_TYPE_ORDER.includes(t)))
  }, [groupedByType])

  return (
    <div
      data-testid={`finding-section-${finding.id}`}
      className={`border rounded ${
        hasGap
          ? 'border-amber-700/50 bg-amber-500/5'
          : 'border-navy-700 bg-navy-800/40'
      }`}>
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        data-testid={`finding-toggle-${finding.id}`}
        className="w-full p-2 text-left flex items-start gap-2
                   hover:bg-navy-800/60 transition-colors rounded">
        <div className="mt-0.5 shrink-0">
          {expanded
            ? <ChevronDown className="w-3.5 h-3.5 text-text-muted" />
            : <ChevronRight className="w-3.5 h-3.5 text-text-muted" />}
        </div>
        <div className="min-w-0 flex-1 space-y-0.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className={`text-2xs px-1.5 py-0.5 rounded ${rankBadge.cls}`}>
              {rankBadge.label}
            </span>
            <span className={`text-2xs px-1.5 py-0.5 rounded ${sourceBadge.cls}`}>
              {sourceBadge.label}
            </span>
            <span className="font-mono text-2xs text-electric-blue">
              {finding.source_id}
            </span>
            <span className={`text-2xs px-1.5 py-0.5 rounded ${
              hasGap
                ? 'bg-amber-500/15 text-amber-300'
                : 'bg-green-500/15 text-green-300'
            }`}>
              {finding.matched_count} matched
            </span>
          </div>
          <p className="text-text-primary text-2xs leading-snug font-medium">
            {finding.title}
          </p>
          {finding.description ? (
            <p className="text-text-muted text-2xs leading-snug">
              {finding.description}
            </p>
          ) : null}
        </div>
      </button>

      {expanded ? (
        <div className="border-t border-navy-700 p-2 space-y-2">
          {hasGap ? (
            <div data-testid={`finding-gap-${finding.id}`}
                 className="flex items-start gap-1.5 text-2xs
                            text-amber-300 bg-amber-500/10 border
                            border-amber-500/30 rounded p-1.5">
              <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
              <span>
                No supporting citations yet — tick a citation below
                to record it as evidence for this finding.
              </span>
            </div>
          ) : null}

          {orderedTypes.length === 0 ? (
            <p className="text-2xs text-text-muted italic">
              No citations available yet — re-run Step 2 to source
              citations before recording matches.
            </p>
          ) : null}

          {orderedTypes.map((type) => (
            <TypeSubgroup
              key={`${finding.id}-${type}`}
              type={type}
              citations={groupedByType[type] ?? []}
              findingId={finding.id}
              busyId={busyId}
              onToggleMatch={onToggleMatch}
              onAction={onAction}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}


// ── TypeSubgroup — Level 2 sub-header for a citation type ───────────────────


interface TypeSubgroupProps {
  type: string
  citations: Citation[]
  findingId: number
  busyId: number | null
  onToggleMatch: (
    citationId: number,
    findingId: number,
    currentlyMatched: boolean,
  ) => Promise<void>
  onAction: (
    citationId: number,
    action: ReviewAction,
    payload?: Record<string, unknown>,
  ) => Promise<void>
}


function TypeSubgroup({
  type, citations, findingId, busyId, onToggleMatch, onAction,
}: TypeSubgroupProps) {
  const style = CITATION_TYPE_STYLES[type] ?? {
    label: type, cls: 'bg-navy-800 text-text-muted border border-navy-700',
  }
  const matchedCount = citations.filter(
    (c) => (c.matched_finding_ids ?? []).includes(findingId)).length

  return (
    <div
      data-testid={`type-subgroup-${findingId}-${type}`}
      className="space-y-1">
      <div className="flex items-center gap-1.5">
        <span className={`text-2xs px-1.5 py-0.5 rounded ${style.cls}`}>
          {style.label}
        </span>
        <span className="text-2xs text-text-muted">
          {matchedCount} of {citations.length} matched
        </span>
      </div>
      <div className="space-y-1 pl-1">
        {citations.map((c) => (
          <CitationFindingRow
            key={`${findingId}-${c.id}`}
            citation={c}
            findingId={findingId}
            busy={busyId === c.id}
            onToggleMatch={onToggleMatch}
            onAction={onAction}
          />
        ))}
      </div>
    </div>
  )
}


// ── CitationFindingRow — Level 3 wrapper that adds the match checkbox ──────
//
// Wraps the existing CitationRow with a checkbox indicating whether
// this citation is matched to the surrounding finding. Citations not
// matched to the current finding render at lower opacity so the eye
// reads "this finding's support stack" at a glance.


interface CitationFindingRowProps {
  citation: Citation
  findingId: number
  busy: boolean
  onToggleMatch: (
    citationId: number,
    findingId: number,
    currentlyMatched: boolean,
  ) => Promise<void>
  onAction: (
    citationId: number,
    action: ReviewAction,
    payload?: Record<string, unknown>,
  ) => Promise<void>
}


function CitationFindingRow({
  citation, findingId, busy, onToggleMatch, onAction,
}: CitationFindingRowProps) {
  const matched = (citation.matched_finding_ids ?? []).includes(findingId)
  const [toggling, setToggling] = useState(false)

  const handleToggle = async (e: React.MouseEvent<HTMLInputElement>) => {
    e.stopPropagation()
    setToggling(true)
    try {
      await onToggleMatch(citation.id, findingId, matched)
    } finally {
      setToggling(false)
    }
  }

  return (
    <div className={`flex items-start gap-1.5 transition-opacity
                     ${matched ? '' : 'opacity-60'}`}>
      <input
        type="checkbox"
        checked={matched}
        disabled={toggling}
        onClick={handleToggle}
        onChange={() => { /* handled in onClick */ }}
        data-testid={`citation-match-${findingId}-${citation.id}`}
        aria-label={
          matched ? 'Remove match' : 'Match citation to finding'}
        className="mt-2 ml-1.5 shrink-0 cursor-pointer accent-electric-blue
                   disabled:opacity-50 disabled:cursor-not-allowed" />
      <div className="min-w-0 flex-1">
        <CitationRow
          citation={citation}
          busy={busy}
          onAction={onAction}
        />
      </div>
    </div>
  )
}


// ── CitationRow — single tile with collapsed + expanded modes ───────────────


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
  const expanded = useCitationReviewStore(
    (s) => Boolean(s.expandedByCitationId[citation.id]))
  const setExpanded = useCitationReviewStore((s) => s.setExpanded)

  const showManual = useCitationReviewStore(
    (s) => Boolean(s.manualOpenByCitationId[citation.id]))
  const setManualOpen = useCitationReviewStore((s) => s.setManualOpen)
  const setShowManual = (open: boolean) => setManualOpen(citation.id, open)

  const [manualAuthor, setManualAuthor] = useState('')
  const [manualYear,   setManualYear]   = useState('')
  const [manualTitle,  setManualTitle]  = useState('')
  const [manualJournal, setManualJournal] = useState('')
  const [manualUrl,    setManualUrl]    = useState('')

  const isNeedsReview = NEEDS_REVIEW_STATES.has(citation.verification_status)
  const isVerified = VERIFIED_STATES.has(citation.verification_status)
  const canAcceptUntrusted =
    citation.verification_status === 'pending_review'
    || citation.verification_status === 'untrusted_source'

  const totalOptions =
    (citation.url ? 1 : 0) + (citation.alternatives?.length ?? 0)
  const limitedAlternatives = totalOptions < 3

  const status = STATUS_BADGE[citation.verification_status] ?? {
    label: citation.verification_status, cls: 'bg-navy-800 text-text-muted',
  }

  return (
    <div data-testid={`citation-row-${citation.concept_id}`}
         className="border border-navy-700 rounded bg-navy-800/40">
      <button
        type="button"
        onClick={() => setExpanded(citation.id, !expanded)}
        data-testid={`citation-toggle-${citation.concept_id}`}
        className="w-full p-2 text-left flex items-start gap-2
                   hover:bg-navy-800/60 transition-colors rounded">
        <div className="mt-0.5 shrink-0">
          {expanded
            ? <ChevronDown className="w-3.5 h-3.5 text-text-muted" />
            : <ChevronRight className="w-3.5 h-3.5 text-text-muted" />}
        </div>
        <div className="min-w-0 flex-1 space-y-0.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-mono text-2xs text-electric-blue">
              {citation.concept_id}
            </span>
            <span className={`text-2xs px-1.5 py-0.5 rounded ${status.cls}`}>
              {status.label}
            </span>
            {citation.trust_flag ? (
              <span
                data-testid={`citation-trust-${citation.concept_id}`}
                className={`text-2xs px-1.5 py-0.5 rounded border ${
                  citation.trust_flag === 'verified'
                    ? 'bg-green-900/30 text-green-300 border-green-700/30'
                    : citation.trust_flag === 'paywalled'
                      ? 'bg-amber-900/30 text-amber-300 border-amber-700/30'
                      : citation.trust_flag === 'stale'
                        ? 'bg-orange-900/30 text-orange-300 border-orange-700/30'
                        : 'bg-navy-800 text-text-muted border-navy-600'
                }`}
                title={`Trust: ${citation.trust_flag}`}>
                {citation.trust_flag}
              </span>
            ) : null}
          </div>
          {citation.author ? (
            <p className="text-text-secondary text-2xs leading-tight">
              {citation.author} ({citation.year ?? '—'}).{' '}
              <em>{citation.title ?? '—'}</em>
            </p>
          ) : (
            <p className="text-text-muted italic text-2xs">
              No primary citation yet — alternatives may be available below.
            </p>
          )}
          {/* Match rationale — visible in the collapsed row so a
              reviewer can scan the support stack without expanding
              every tile. Per the design doc: "Match rationale and
              score always visible." */}
          {citation.finding_supported ? (
            <p className="text-2xs text-text-muted leading-snug">
              <span className="font-medium">Why: </span>
              {citation.finding_supported}
            </p>
          ) : null}
        </div>
        <div className="shrink-0 flex items-center gap-1">
          <span
            data-testid={`citation-confidence-${citation.concept_id}`}
            className={`text-2xs px-1.5 py-0.5 rounded border
                        ${_confidenceCls(citation.confidence_score)}`}
            title="Confidence score (0.0-1.0) — pass tier plus URL trust">
            {_confidenceLabel(citation.confidence_score)}
          </span>
        </div>
      </button>

      {expanded ? (
        <div data-testid={`citation-expanded-${citation.concept_id}`}
             className="border-t border-navy-700 p-2 space-y-2">

          {limitedAlternatives ? (
            <div className="flex items-start gap-1.5 text-2xs
                            text-amber-300 bg-amber-500/10 border
                            border-amber-500/30 rounded p-1.5">
              <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" />
              <span>
                Limited alternatives — the 3-pass search produced
                fewer than three options for this concept. Manual
                review recommended.
              </span>
            </div>
          ) : null}

          <EvidenceSection
            label="Finding supported"
            empty="Not captured for this citation — re-generate the draft to populate.">
            {citation.finding_supported}
          </EvidenceSection>

          <EvidenceSection
            label="Supporting extract"
            empty="Extract not captured for this citation — the source URL is the authoritative reference.">
            {citation.supporting_extract
              ? <q className="italic">{citation.supporting_extract}</q>
              : null}
          </EvidenceSection>

          <EvidenceSection
            label="Selection rationale"
            empty="Rationale not captured — derived from search pass tier (see source URL).">
            {citation.selection_rationale}
          </EvidenceSection>

          <EvidenceSection label="Confidence">
            <span className={`inline-flex items-center gap-1 text-2xs
                              px-1.5 py-0.5 rounded border
                              ${_confidenceCls(citation.confidence_score)}`}>
              <Info className="w-2.5 h-2.5" />
              {_confidenceLabel(citation.confidence_score)} of 1.00
            </span>
            <span className="ml-1.5 text-text-muted">
              Pass tier + URL trust heuristic. 0.95 = trusted-domain
              primary; 0.75 = academic; 0.55 = widest publishable.
            </span>
          </EvidenceSection>

          {citation.url ? (
            <div>
              <p className="text-2xs uppercase tracking-wider
                            text-text-muted mb-0.5">
                Primary source
              </p>
              <a href={citation.url}
                 target="_blank"
                 rel="noopener noreferrer"
                 className="inline-flex items-center gap-1 text-2xs
                            text-electric-blue hover:underline">
                <ExternalLink className="w-2.5 h-2.5" />
                {citation.journal_or_institution ?? citation.url}
              </a>
            </div>
          ) : null}

          {citation.alternatives && citation.alternatives.length > 0 ? (
            <div className="space-y-1.5">
              <p className="text-2xs uppercase tracking-wider
                            text-text-muted">
                Alternative citations ({citation.alternatives.length})
              </p>
              {citation.alternatives.map((alt, i) => (
                <AlternativeCard
                  key={`${citation.id}-alt-${i}`}
                  alt={alt}
                  rank={i + 2}
                  busy={busy}
                  canAct={!isVerified || canAcceptUntrusted}
                  onAccept={() => onAction(
                    citation.id, 'select_alternative',
                    { selected_alternative: alt })}
                />
              ))}
            </div>
          ) : null}

          {isNeedsReview ? (
            <div className="flex flex-wrap items-center gap-1 pt-1
                            border-t border-navy-700">
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
                  Accept primary
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
                {showManual ? 'Cancel manual' : 'Manual override'}
              </button>
              {busy ? (
                <Loader2 className="w-3 h-3 animate-spin text-text-muted" />
              ) : null}
            </div>
          ) : null}

          {showManual && isNeedsReview ? (
            <div className="pt-1 border-t border-navy-700 space-y-1">
              <p className="text-2xs text-text-muted">
                Paste any citation if none of the options above are
                suitable.
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
      ) : null}
    </div>
  )
}


// ── EvidenceSection — small consistent wrapper for the six fields ───────────


interface EvidenceSectionProps {
  label: string
  empty?: string
  children?: React.ReactNode
}


function EvidenceSection({ label, empty, children }: EvidenceSectionProps) {
  const hasContent = children !== null && children !== undefined
    && children !== false && children !== '' && children !== 0
  return (
    <div>
      <p className="text-2xs uppercase tracking-wider text-text-muted
                    mb-0.5">
        {label}
      </p>
      {hasContent ? (
        <div className="text-2xs text-text-secondary leading-snug">
          {children}
        </div>
      ) : (
        <p className="text-2xs text-text-muted italic">
          {empty ?? 'Not available.'}
        </p>
      )}
    </div>
  )
}


// ── AlternativeCard — one ranked alternative with its own evidence ──────────


interface AlternativeCardProps {
  alt: CitationAlternative
  rank: number   // 2 for first alternative, 3 for second, etc.
  busy: boolean
  canAct: boolean
  onAccept: () => void
}


function AlternativeCard({
  alt, rank, busy, canAct, onAccept,
}: AlternativeCardProps) {
  const passLabel = alt.pass_source
    ? PASS_SOURCE_LABEL[alt.pass_source] ?? alt.pass_source
    : '—'
  const rankReason = alt.pass_source
    ? PASS_RANK_REASON[alt.pass_source]
    : null

  return (
    <div data-testid="citation-alternative-card"
         className="border border-navy-700 rounded p-1.5 space-y-1
                    bg-navy-900/40">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-2xs text-text-muted">
            Alternative #{rank} · {passLabel}
          </p>
          <p className="text-2xs text-text-secondary leading-tight">
            {alt.author ?? '?'} ({alt.year ?? '?'}).{' '}
            <em>{alt.title ?? alt.url ?? '—'}</em>
          </p>
          {alt.url ? (
            <a href={alt.url}
               target="_blank"
               rel="noopener noreferrer"
               className="inline-flex items-center gap-1 text-2xs
                          text-electric-blue hover:underline">
              <ExternalLink className="w-2.5 h-2.5" />
              {alt.journal_or_institution ?? alt.url}
            </a>
          ) : null}
        </div>
        <span className={`shrink-0 text-2xs px-1.5 py-0.5 rounded border
                          ${_confidenceCls(alt.confidence_score)}`}
              title="Confidence score (0.0-1.0)">
          {_confidenceLabel(alt.confidence_score)}
        </span>
      </div>

      {alt.supporting_extract ? (
        <p className="text-2xs text-text-secondary italic leading-snug">
          “{alt.supporting_extract}”
        </p>
      ) : null}

      {rankReason ? (
        <p className="text-2xs text-text-muted leading-snug">
          <span className="font-medium">Why ranked below: </span>
          {rankReason}
        </p>
      ) : null}

      {canAct ? (
        <button
          type="button"
          disabled={busy}
          onClick={onAccept}
          data-testid="citation-accept-alternative"
          className="text-2xs px-2 py-0.5 rounded
                     border border-electric-blue/60 text-electric-blue
                     hover:bg-electric-blue/10 disabled:opacity-50
                     disabled:cursor-not-allowed">
          Accept this instead
        </button>
      ) : null}
    </div>
  )
}

