/**
 * SubmissionReadinessReview -- June 23 2026.
 *
 * The capstone submission-readiness panel on the Reports page.
 * Distinct from QA Hub's Cross-Document Review (iterative drafting
 * tool): this panel is the FINAL go/no-go pass before the June 30
 * deadline. It bundles two checks behind a single button:
 *
 *   Section A -- Data Cross-Reference
 *     POST /api/v1/export/verify-all. As of June 23 2026 this
 *     covers ALL FOUR deliverables (script included; the script
 *     exclusion identified in the audit is closed in the same
 *     commit). Per-doc breakdown shows status chip,
 *     n_values_verified, hash match indicator, and expandable
 *     errors/warnings lists with token / expected / found.
 *
 *   Section B -- Cross-Document Academic Review
 *     Re-uses academicReviewStore.runReview path (no document_type
 *     -- full cross-document rubric, now extended to flag the
 *     non-numeric gaps the data cross-reference cannot catch:
 *     regime labels, dates, citation years, narrative coherence,
 *     freehand figures). One store, two surfaces -- the verdict
 *     also lands on QA Hub's Cross-Document Review section.
 *
 * Pre-flight gate runs against GET /api/v1/documents/drafts and
 * verifies all four deliverable types have non-empty drafts.
 * Missing types are surfaced inline; the confirmation modal is
 * suppressed in that case.
 *
 * Composite verdict (after both sections complete):
 *   Green:  verify-all ready + no academic-review HIGH findings
 *   Amber:  verify-all needs_attention OR academic review has
 *           MEDIUM-only findings
 *   Red:    verify-all blocked OR academic review has HIGH findings
 *
 * TeamGate wraps the trigger -- viewers cannot kick the run.
 */
import { useState } from 'react'
import axios from 'axios'
import {
  ShieldCheck, ChevronDown, ChevronRight, Loader2, CheckCircle,
  AlertCircle, XCircle, AlertTriangle,
} from 'lucide-react'

import AuditExportButton from './AuditExportButton'
import Markdown from './Markdown'
import SubmissionReadinessReviewConfirmModal
  from './SubmissionReadinessReviewConfirmModal'
import TeamGate from './TeamGate'
import { useIsTeamMember } from '../hooks/usePermissions'
import {
  useAcademicReviewStore,
} from '../stores/academicReviewStore'
import type { EditorDocumentType } from '../types/editor'


const DOC_LABELS: Record<EditorDocumentType, string> = {
  // midpoint_paper is retired post-May 27 but remains in the
  // EditorDocumentType union for historical drafts. Include here so
  // tsc --noEmit (Vercel build) accepts the exhaustive Record.
  midpoint_paper:      'Midpoint Paper',
  executive_brief:     'Executive Brief',
  presentation_deck:   'Final Presentation Deck',
  analytical_appendix: 'Analytical Appendix',
  presentation_script: 'Presentation Script',
}

const ALL_DOC_TYPES: readonly EditorDocumentType[] = [
  'executive_brief',
  'presentation_deck',
  'analytical_appendix',
  'presentation_script',
] as const

interface VerifyFlag {
  type?:           string
  severity?:       string
  token?:          string
  expected?:       unknown
  expected_value?: unknown
  found?:          unknown
  document?:       string
  message?:        string
}

interface DocStatus {
  status: 'verified' | 'warned' | 'failed' | 'not_generated'
  passed: boolean
  errors:   VerifyFlag[]
  warnings: VerifyFlag[]
  data_hash_match: boolean
  last_verified_at: string | null
  skipped?: string
  n_values_verified?: number
}

interface VerifyAllResponse {
  overall: 'ready' | 'needs_attention' | 'blocked'
  submission_recommendation: string
  brief:    DocStatus
  deck:     DocStatus
  appendix: DocStatus
  script:   DocStatus
  cross_deliverable: { passed: boolean; flags: VerifyFlag[] }
}

interface DraftListItem {
  id: number
  document_type: string
  content_text?: string | null
  is_current?: boolean
}


export default function SubmissionReadinessReview(): React.ReactElement {
  const [expanded, setExpanded] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [missingDocs, setMissingDocs] = useState<EditorDocumentType[]>([])
  const [phase, setPhase] = useState<
    'idle' | 'section-a' | 'section-b' | 'done'>('idle')
  const [verifyData, setVerifyData] =
    useState<VerifyAllResponse | null>(null)
  const [verifyError, setVerifyError] = useState<string | null>(null)
  const [errorsOpen, setErrorsOpen] = useState<
    Record<string, boolean>>({})
  const isTeam = useIsTeamMember()

  const reviewPhase = useAcademicReviewStore((s) => s.phase)
  const reviewResult = useAcademicReviewStore((s) => s.result)
  const runCrossDocReview = useAcademicReviewStore(
    (s) => s.runReview)

  // Composite verdict: derived from both sections' results. Uses
  // the same spec: green = verify ready + no HIGH academic findings;
  // amber = needs_attention OR MEDIUM-only findings; red = verify
  // blocked OR HIGH academic findings. The academic review verdict
  // landing is the cross-document arbiter text -- we scan it for
  // the explicit "Needs Work" rating or NON_NUMERIC_CONSISTENCY
  // HIGH labels as the cheap heuristic; the canonical surface is
  // the arbiter prose rendered below.
  const arbiterText = reviewResult?.arbiterText ?? ''
  const academicHasHigh = /\bsevere?ity[:\s]*HIGH\b/i
    .test(arbiterText)
    || /Needs Work/.test(arbiterText)
  const academicHasMedium = /\bsevere?ity[:\s]*MEDIUM\b/i
    .test(arbiterText)

  const compositeVerdict: 'idle' | 'green' | 'amber' | 'red' =
    phase !== 'done' || !verifyData
      ? 'idle'
      : verifyData.overall === 'blocked' || academicHasHigh
        ? 'red'
        : verifyData.overall === 'needs_attention'
          || academicHasMedium
          ? 'amber'
          : 'green'

  const checkAllDocsExist = async (): Promise<EditorDocumentType[]> => {
    try {
      const res = await axios.get<{ drafts: DraftListItem[] }>(
        '/api/v1/documents/drafts')
      const drafts = res.data.drafts ?? []
      const present = new Set<string>()
      for (const d of drafts) {
        const txt = (d.content_text || '').trim()
        if (txt) present.add(d.document_type)
      }
      return ALL_DOC_TYPES.filter((t) => !present.has(t))
    } catch {
      return []
    }
  }

  const handleRunClick = async (): Promise<void> => {
    setMissingDocs([])
    const missing = await checkAllDocsExist()
    if (missing.length > 0) {
      setMissingDocs(missing)
      return
    }
    setConfirmOpen(true)
  }

  const handleConfirmRun = async (): Promise<void> => {
    setConfirmOpen(false)
    setPhase('section-a')
    setVerifyError(null)
    try {
      const res = await axios.post<VerifyAllResponse>(
        '/api/v1/export/verify-all')
      setVerifyData(res.data)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Verification request failed'
      setVerifyError(String(msg))
    }
    setPhase('section-b')
    try {
      const token = localStorage.getItem('fc_session_token') ?? ''
      await runCrossDocReview(null, token)
    } catch {
      /* error surfaced via store phase/errorMsg */
    } finally {
      setPhase('done')
    }
  }

  const docRows: { key: string; label: string; status: DocStatus }[] =
    verifyData
      ? [
        { key: 'brief',
          label: 'Executive Brief',
          status: verifyData.brief },
        { key: 'deck',
          label: 'Final Presentation Deck',
          status: verifyData.deck },
        { key: 'appendix',
          label: 'Analytical Appendix',
          status: verifyData.appendix },
        { key: 'script',
          label: 'Presentation Script',
          status: verifyData.script },
      ]
      : []

  const crossFlags = verifyData?.cross_deliverable.flags ?? []
  const crossPassed = verifyData?.cross_deliverable.passed ?? true

  return (
    <section
      data-section-id="submission-readiness-review"
      data-section-label="Submission Readiness Review"
      className="card"
      data-testid="submission-readiness-review-panel">
      <SubmissionReadinessReviewConfirmModal
        open={confirmOpen}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => { void handleConfirmRun() }} />
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        data-testid="submission-readiness-toggle"
        className="w-full flex items-center justify-between gap-3
                   px-4 py-3 hover:bg-navy-700/30 transition-colors
                   rounded">
        <div className="flex items-center gap-2 min-w-0">
          {expanded
            ? <ChevronDown className="w-4 h-4 text-muted shrink-0" />
            : <ChevronRight className="w-4 h-4 text-muted shrink-0" />}
          <ShieldCheck className="w-4 h-4 text-success shrink-0" />
          <div className="text-left min-w-0">
            <h2 className="text-white font-semibold text-sm">
              Submission Readiness Review
            </h2>
            <p className="text-2xs text-muted mt-0.5">
              Full cross-document academic review + data
              cross-reference. Run this as the final check before
              June 30.
            </p>
          </div>
        </div>
        <CompositeVerdictChip verdict={compositeVerdict} />
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-4 border-t border-border
                       pt-3" data-testid="submission-readiness-body">

          {missingDocs.length > 0 && (
            <div
              data-testid="submission-readiness-missing-banner"
              className="rounded border border-warning/40
                         bg-warning/5 p-3 text-xs text-warning
                         leading-relaxed flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              <span>
                <strong className="font-semibold">
                  Cannot run Submission Readiness Review --
                </strong>{' '}
                the following documents have not been generated
                yet:{' '}
                <strong className="font-semibold text-white">
                  {missingDocs.map((d) => DOC_LABELS[d]).join(', ')}
                </strong>
                . Generate all four deliverables before running this
                review.
              </span>
            </div>
          )}

          <div className="flex justify-end items-center gap-2">
            {phase === 'section-a' && (
              <span className="text-2xs text-muted flex items-center
                                gap-1.5">
                <Loader2 className="w-3 h-3 animate-spin" />
                Step 1 of 2 -- data cross-reference…
              </span>
            )}
            {phase === 'section-b' && (
              <span className="text-2xs text-muted flex items-center
                                gap-1.5">
                <Loader2 className="w-3 h-3 animate-spin" />
                Step 2 of 2 -- cross-document academic review…
              </span>
            )}
            <TeamGate
              block
              permission="team_member"
              tooltip="Submission Readiness Review is available to the project team">
              <button
                type="button"
                onClick={() => { void handleRunClick() }}
                disabled={phase === 'section-a'
                  || phase === 'section-b' || !isTeam}
                data-testid="submission-readiness-run"
                className="flex items-center gap-1.5 px-4 py-2 rounded
                           text-xs font-semibold bg-success
                           text-navy-900 hover:bg-green-400
                           disabled:opacity-50
                           disabled:cursor-not-allowed">
                <ShieldCheck className="w-3.5 h-3.5" />
                Run Submission Readiness Review
              </button>
            </TeamGate>
            {/* Concern 7m-iii -- audit trail export dropdown.
                Renders even when no review has run; the backend
                returns whatever rounds exist. */}
            <AuditExportButton mode="dropdown" isTeam={isTeam} />
          </div>

          {/* ── Section A -- Data Cross-Reference ─────────────── */}
          <div data-testid="submission-readiness-section-a">
            <h3 className="text-xs font-semibold uppercase
                          tracking-wide text-slate-300 mb-2">
              Section A -- Data Cross-Reference
            </h3>
            {!verifyData && !verifyError && phase === 'idle' && (
              <p className="text-2xs text-muted leading-relaxed">
                Not yet run. Reuses the Pre-Submission Check
                endpoint -- per-document hash match, value
                presence, and cross-deliverable consistency.
              </p>
            )}
            {verifyError && (
              <div className="text-2xs text-danger flex items-start
                              gap-1.5">
                <AlertCircle className="w-3 h-3 shrink-0 mt-0.5" />
                <span>{verifyError}</span>
              </div>
            )}
            {verifyData && (
              <div className="space-y-3">
                {/* Plain-English recommendation -- prominent. */}
                <div className={`rounded p-2.5 text-2xs
                                leading-relaxed ${
                  verifyData.overall === 'ready'
                    ? 'bg-success/10 border border-success/30 '
                      + 'text-success'
                    : verifyData.overall === 'needs_attention'
                      ? 'bg-warning/10 border border-warning/30 '
                        + 'text-warning'
                      : 'bg-danger/10 border border-danger/30 '
                        + 'text-danger'
                }`}
                  data-testid="submission-readiness-recommendation">
                  {verifyData.submission_recommendation}
                </div>

                {/* Per-doc breakdown. */}
                <div className="space-y-1.5">
                  {docRows.map(({ key, label, status }) => (
                    <DocRow
                      key={key}
                      docKey={key}
                      label={label}
                      status={status}
                      expanded={errorsOpen[key] ?? false}
                      onToggle={() =>
                        setErrorsOpen((p) =>
                          ({ ...p, [key]: !p[key] }))} />
                  ))}
                </div>

                {/* Cross-deliverable section. */}
                <div data-testid="submission-readiness-cross-section"
                  className="border-t border-border pt-2">
                  <div className="flex items-center gap-2">
                    {crossPassed
                      ? <CheckCircle
                          className="w-3.5 h-3.5 text-success" />
                      : <XCircle
                          className="w-3.5 h-3.5 text-danger" />}
                    <span className="text-2xs font-semibold
                                       text-slate-200">
                      Cross-deliverable consistency:{' '}
                    </span>
                    <span className={`text-2xs ${crossPassed
                      ? 'text-success' : 'text-danger'}`}>
                      {crossPassed
                        ? 'no drift detected'
                        : `${crossFlags.length} drift `
                          + (crossFlags.length === 1
                            ? 'flag' : 'flags')}
                    </span>
                  </div>
                  {!crossPassed && crossFlags.length > 0 && (
                    <ul className="mt-1 space-y-0.5 text-2xs
                                   text-slate-300 list-disc
                                   list-inside">
                      {crossFlags.map((f, i) => (
                        <li key={i}>
                          <span className="font-mono text-electric">
                            {f.token ?? '(token?)'}
                          </span>
                          {' '}in{' '}
                          <span className="text-warning">
                            {f.document ?? '(doc?)'}
                          </span>
                          {' '}-- expected{' '}
                          <span className="font-mono">
                            {String(f.expected ?? '')}
                          </span>
                          , found{' '}
                          <span className="font-mono">
                            {String(f.found ?? '')}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                {/* Known limitations notice -- always visible after
                    a run lands. Calibrates expectations. */}
                <div
                  data-testid="submission-readiness-limitations"
                  className="rounded border border-border bg-navy-800
                             p-2.5 text-2xs text-muted leading-relaxed
                             italic">
                  This check verifies substituted numeric tokens
                  only. Regime labels, dates, citation years, chart
                  contents, and figures written as freehand prose
                  are not covered. Review these manually before
                  submitting.
                </div>
              </div>
            )}
          </div>

          {/* ── Section B -- Cross-Document Academic Review ────── */}
          <div data-testid="submission-readiness-section-b">
            <h3 className="text-xs font-semibold uppercase
                          tracking-wide text-slate-300 mb-2">
              Section B -- Cross-Document Panel Review
            </h3>
            {reviewPhase === 'idle' && !reviewResult
              && phase !== 'section-b' && (
              <p className="text-2xs text-muted leading-relaxed">
                Not yet run. Full council pass across the four
                deliverables, extended to flag the non-numeric
                gaps the data cross-reference cannot catch (regime
                labels, dates, citation years, narrative coherence,
                freehand figures).
              </p>
            )}
            {(reviewPhase === 'consulting'
              || reviewPhase === 'streaming') && (
              <p className="text-2xs text-muted flex items-center
                            gap-1.5">
                <Loader2 className="w-3 h-3 animate-spin" />
                {reviewPhase === 'consulting'
                  ? 'Consulting the council…'
                  : 'Synthesising the verdict…'}
              </p>
            )}
            {reviewResult && reviewResult.arbiterText && (
              <div className="card p-3 text-xs max-h-96
                              overflow-y-auto"
                data-testid="submission-readiness-arbiter">
                <Markdown content={reviewResult.arbiterText} />
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  )
}


function CompositeVerdictChip(
  { verdict }: {
    verdict: 'idle' | 'green' | 'amber' | 'red'
  },
): React.ReactElement {
  const map = {
    idle:  { label: 'Not yet run',
             cls: 'bg-slate-700/40 text-slate-300' },
    green: { label: 'Pass',
             cls: 'bg-success/15 text-success' },
    amber: { label: 'Issues found',
             cls: 'bg-warning/15 text-warning' },
    red:   { label: 'Issues found',
             cls: 'bg-danger/15 text-danger' },
  } as const
  const { label, cls } = map[verdict]
  return (
    <span
      data-testid="submission-readiness-status-chip"
      data-status={verdict}
      className={
        `text-2xs px-2 py-0.5 rounded font-semibold ${cls}`}>
      {label}
    </span>
  )
}


function DocRow(
  { docKey, label, status, expanded, onToggle }: {
    docKey: string
    label: string
    status: DocStatus
    expanded: boolean
    onToggle: () => void
  },
): React.ReactElement {
  let Icon = CheckCircle
  let cls = 'text-success'
  let chipLabel = 'ready'
  if (status.status === 'not_generated') {
    Icon = XCircle
    cls = 'text-danger'
    chipLabel = 'not generated'
  } else if (status.status === 'failed') {
    Icon = XCircle
    cls = 'text-danger'
    chipLabel = 'blocked'
  } else if (status.status === 'warned') {
    Icon = AlertTriangle
    cls = 'text-warning'
    chipLabel = 'needs attention'
  }
  const allFlags = [...(status.errors || []),
                    ...(status.warnings || [])]
  const hasFlags = allFlags.length > 0
  return (
    <div
      className="text-2xs"
      data-testid={`submission-readiness-doc-row-${docKey}`}>
      <button
        type="button"
        onClick={hasFlags ? onToggle : undefined}
        className={`w-full flex items-center gap-2 text-left ${
          hasFlags ? 'cursor-pointer hover:bg-navy-800' : ''}
          rounded px-2 py-1`}>
        {hasFlags && (expanded
          ? <ChevronDown className="w-3 h-3 text-muted shrink-0" />
          : <ChevronRight className="w-3 h-3 text-muted shrink-0" />)}
        {!hasFlags && <span className="w-3 shrink-0" />}
        <Icon className={`w-3.5 h-3.5 shrink-0 ${cls}`} />
        <span className="text-slate-200 w-40 shrink-0">{label}</span>
        <span className={`${cls} shrink-0`}>{chipLabel}</span>
        <span className="text-muted ml-auto shrink-0">
          {status.n_values_verified ?? 0} values verified
          {' · hash '}
          {status.data_hash_match ? 'match' : 'mismatch'}
        </span>
      </button>
      {expanded && hasFlags && (
        <ul className="mt-1 ml-7 space-y-1 text-2xs text-slate-300">
          {allFlags.map((f, i) => (
            <li key={i} className="border-l-2 border-border pl-2">
              <span className="font-mono text-electric">
                {f.token ?? f.type ?? '(unknown)'}
              </span>
              {' -- expected '}
              <span className="font-mono">
                {String(f.expected ?? f.expected_value ?? '?')}
              </span>
              {f.found !== undefined && (
                <>
                  {', found '}
                  <span className="font-mono">
                    {String(f.found)}
                  </span>
                </>
              )}
              {f.message && (
                <div className="text-muted italic mt-0.5">
                  {f.message}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
