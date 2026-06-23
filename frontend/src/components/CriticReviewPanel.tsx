/**
 * CriticReviewPanel -- June 23 2026, Concern 7.
 *
 * Adversarial critic review surface. Gemini + Grok independently
 * review the target document(s) and the response merges their
 * findings (deduped where both surfaced the same issue) plus prose
 * summary and severity counts.
 *
 * Two variants from one component:
 *
 *   variant="editor" + documentType present
 *     Used in the editor's Writing Assistant below the per-doc
 *     academic review button. One-click (no confirm modal). Hits
 *     POST /api/council/critic-review?document_type=<X>.
 *
 *   variant="submission" (no documentType)
 *     Used in the Submission Readiness Review as Section C.
 *     Confirmation modal fires before the POST. Hits
 *     POST /api/council/critic-review (no query param =
 *     full-package mode).
 *
 * Fatal findings are surfaced prominently but NEVER block
 * submission -- the team makes the final call. The non-blocking
 * advisory banner reads "Fatal findings require attention before
 * submission. The team makes the final call."
 *
 * TeamGate wraps the trigger -- viewers cannot kick the run.
 */
import { useState } from 'react'
import axios from 'axios'
import {
  AlertOctagon, AlertTriangle, AlertCircle, Loader2, Sword,
  CheckCircle,
} from 'lucide-react'

import { useContext } from 'react'
import CriticReviewConfirmModal from './CriticReviewConfirmModal'
import Markdown from './Markdown'
import { AuthContext } from '../App'


export type CriticPanelVariant = 'editor' | 'submission'

export interface CriticReviewPanelProps {
  /** When provided, the panel runs a per-document critic review.
   *  Omit for full-package (used by variant="submission"). */
  documentType?: string
  variant: CriticPanelVariant
  /** Concern 7h -- the SubmissionReadinessReview consumes the
   *  critic response to fold Fatal/Major counts into its
   *  composite verdict. The callback fires on every successful
   *  run; the panel keeps its OWN local state too so the editor
   *  variant works without a parent listener. */
  onResultsChange?: (response: CriticReviewResponse | null) => void
}

export type CriticReviewResponse = CriticResponse

type Severity = 'Fatal' | 'Major' | 'Minor'

interface CriticFinding {
  severity:        Severity
  category:        string
  document?:       string
  location?:       string
  description?:    string
  evidence?:       string
  recommendation?: string
  agreed?:         boolean
  raised_by?:      'gemini' | 'grok' | 'both'
}

interface CriticResponse {
  document_scope:  string
  gemini_findings: CriticFinding[]
  grok_findings:   CriticFinding[]
  merged_findings: CriticFinding[]
  prose_summary:   string
  fatal_count:     number
  major_count:     number
  minor_count:     number
  model_agreement: string
  partial_failure: boolean
}


export default function CriticReviewPanel(
  {
    documentType, variant, onResultsChange,
  }: CriticReviewPanelProps,
): React.ReactElement {
  const [running, setRunning]     = useState(false)
  const [response, setResponse]   = useState<CriticResponse | null>(null)
  const [error, setError]         = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  // Read auth defensively -- tests that mount DocumentEditor /
  // WritingAssistant without an AuthProvider must not blow up
  // because this panel was added below the per-doc review button.
  // useIsTeamMember would throw in that environment; reading from
  // useContext directly with a null fallback degrades gracefully:
  // the button stays disabled (TeamGate also renders a disabled
  // surface in the same case), the rest of the panel renders.
  const ctx = useContext(AuthContext)
  const isTeam = !!ctx?.session?.permissions?.includes('team_member')

  const isFullPackage = variant === 'submission' && !documentType

  const subtext = variant === 'submission'
    ? 'Full-package adversarial review across all four deliverables.'
    : 'Gemini + Grok independently review this document for '
      + 'methodological, factual, and logical errors. Findings are '
      + 'advisory -- the team makes the final call.'

  const triggerRun = (): void => {
    if (isFullPackage) {
      setConfirmOpen(true)
    } else {
      void doRun()
    }
  }

  const doRun = async (): Promise<void> => {
    setConfirmOpen(false)
    setRunning(true)
    setError(null)
    try {
      const url = documentType
        ? `/api/council/critic-review?document_type=${
            encodeURIComponent(documentType)}`
        : '/api/council/critic-review'
      const res = await axios.post<CriticResponse>(url)
      setResponse(res.data)
      if (onResultsChange) onResultsChange(res.data)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Critic review failed'
      setError(String(msg))
    } finally {
      setRunning(false)
    }
  }

  // The submission variant takes its own card chrome; the editor
  // variant inherits the WritingAssistant's padded container so it
  // renders as a sub-section.
  const containerCls = variant === 'submission'
    ? 'card p-4 space-y-3'
    : 'border-t border-border pt-3 space-y-2'
  const headingCls = variant === 'submission'
    ? 'text-white font-semibold text-sm flex items-center gap-1.5'
    : 'text-white font-semibold text-2xs uppercase '
      + 'tracking-wide flex items-center gap-1'

  return (
    <section
      data-testid={
        `critic-review-panel-${documentType ?? 'full-package'}`}
      className={containerCls}>
      <CriticReviewConfirmModal
        open={confirmOpen}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={() => { void doRun() }} />
      <div>
        <h3 className={headingCls}>
          <Sword className={
            variant === 'submission' ? 'w-4 h-4 text-danger'
              : 'w-3 h-3 text-danger'} />
          Adversarial Critic Review
        </h3>
        <p className={
          variant === 'submission'
            ? 'text-xs text-muted mt-1 leading-relaxed'
            : 'text-2xs text-muted mt-1 leading-relaxed'}>
          {subtext}
        </p>
      </div>

      <button
        type="button"
        onClick={triggerRun}
        disabled={running || !isTeam}
        title={isTeam ? undefined
          : 'Critic review is available to the project team'}
        data-testid={
          `critic-review-run-${documentType ?? 'full-package'}`}
        className={
          variant === 'submission'
            ? 'flex items-center gap-1.5 px-4 py-2 rounded '
              + 'text-xs font-semibold bg-danger text-white '
              + 'hover:bg-rose-500 disabled:opacity-50 '
              + 'disabled:cursor-not-allowed'
            : 'w-full flex items-center justify-center gap-1.5 '
              + 'text-xs bg-danger/15 text-danger border '
              + 'border-danger/40 rounded py-2 hover:bg-danger/25 '
              + 'disabled:opacity-60'}>
        {running
          ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />
              Gemini and Grok are reviewing…</>
          : <><Sword className="w-3.5 h-3.5" />
              Run Critic Review</>}
      </button>

      {error && (
        <div className="text-2xs text-danger flex items-start
                        gap-1.5">
          <AlertCircle className="w-3 h-3 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {response && (
        <CriticResults data={response} variant={variant} />
      )}
    </section>
  )
}


function CriticResults(
  { data, variant }: {
    data: CriticResponse; variant: CriticPanelVariant
  },
): React.ReactElement {
  const hasFatal = data.fatal_count > 0
  return (
    <div className="space-y-3" data-testid="critic-review-results">
      <div className="flex flex-wrap items-center gap-2 text-2xs">
        <span className={`px-2 py-0.5 rounded font-semibold ${
          hasFatal ? 'bg-danger/20 text-danger'
            : 'bg-slate-700/40 text-slate-300'}`}>
          Fatal: {data.fatal_count}
        </span>
        <span className={`px-2 py-0.5 rounded font-semibold ${
          data.major_count > 0
            ? 'bg-warning/20 text-warning'
            : 'bg-slate-700/40 text-slate-300'}`}>
          Major: {data.major_count}
        </span>
        <span className="px-2 py-0.5 rounded font-semibold
                          bg-slate-700/40 text-slate-300">
          Minor: {data.minor_count}
        </span>
        {data.partial_failure && (
          <span
            data-testid="critic-review-partial-failure"
            className="px-2 py-0.5 rounded font-semibold
                       bg-warning/15 text-warning"
            title="One of the two models did not return a parseable response">
            Partial result
          </span>
        )}
      </div>

      {hasFatal && (
        <div
          data-testid="critic-review-fatal-banner"
          className="rounded border border-warning/40 bg-warning/5
                     p-2.5 text-2xs text-warning leading-relaxed
                     flex items-start gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>
            Fatal findings require attention before submission. The
            team makes the final call.
          </span>
        </div>
      )}

      <p className="text-2xs text-muted italic leading-relaxed">
        {data.model_agreement}
      </p>

      {/* Findings grouped by severity. */}
      {(['Fatal', 'Major', 'Minor'] as const).map((sev) => {
        const items = data.merged_findings.filter(
          (f) => f.severity === sev)
        if (items.length === 0) return null
        return (
          <div key={sev}
            data-testid={`critic-review-group-${sev.toLowerCase()}`}>
            <h4 className="text-2xs font-semibold uppercase
                           tracking-wide text-slate-300 mb-1.5">
              {sev} ({items.length})
            </h4>
            <div className="space-y-2">
              {items.map((f, i) => (
                <FindingCard key={`${sev}-${i}`} finding={f} />
              ))}
            </div>
          </div>
        )
      })}

      {data.merged_findings.length === 0 && !data.partial_failure && (
        <div className="text-2xs text-success flex items-start
                        gap-1.5">
          <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>
            No findings raised. Both critics report no significant
            issues at this pass.
          </span>
        </div>
      )}

      {/* Overall assessment from the model prose summary. */}
      <div className="border-t border-border pt-2">
        <h4 className="text-2xs font-semibold uppercase
                       tracking-wide text-slate-200 mb-1">
          Overall Assessment
        </h4>
        <div className={variant === 'submission'
          ? 'text-xs text-slate-300 leading-relaxed'
          : 'text-2xs text-slate-300 leading-relaxed'}>
          <Markdown content={data.prose_summary} />
        </div>
      </div>
    </div>
  )
}


function FindingCard(
  { finding }: { finding: CriticFinding },
): React.ReactElement {
  const sevCls = finding.severity === 'Fatal'
    ? 'bg-danger/20 text-danger'
    : finding.severity === 'Major'
      ? 'bg-warning/20 text-warning'
      : 'bg-slate-700/40 text-slate-300'
  const agreeLabel = finding.agreed
    ? 'Gemini + Grok agreed'
    : finding.raised_by === 'gemini'
      ? 'Gemini only'
      : finding.raised_by === 'grok'
        ? 'Grok only'
        : ''
  const Icon = finding.severity === 'Fatal'
    ? AlertOctagon
    : finding.severity === 'Major'
      ? AlertTriangle
      : AlertCircle
  return (
    <div className="rounded border border-border bg-navy-800 p-2.5
                    space-y-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <Icon className={`w-3 h-3 shrink-0 ${
          finding.severity === 'Fatal' ? 'text-danger'
            : finding.severity === 'Major' ? 'text-warning'
              : 'text-muted'}`} />
        <span className={`text-2xs px-1.5 py-0.5 rounded
                          font-semibold ${sevCls}`}>
          {finding.severity}
        </span>
        <span className="text-2xs px-1.5 py-0.5 rounded
                          bg-electric/10 text-electric font-semibold">
          {finding.category}
        </span>
        {agreeLabel && (
          <span className={`text-2xs px-1.5 py-0.5 rounded
                            font-semibold ${finding.agreed
                              ? 'bg-success/15 text-success'
                              : 'bg-slate-700/40 text-slate-300'}`}>
            {agreeLabel}
          </span>
        )}
      </div>
      {(finding.document || finding.location) && (
        <div className="text-2xs text-muted font-mono">
          {finding.document}
          {finding.document && finding.location ? ' · ' : ''}
          {finding.location}
        </div>
      )}
      {finding.description && (
        <div className="text-2xs text-slate-200">
          {finding.description}
        </div>
      )}
      {finding.evidence && (
        <blockquote className="text-2xs text-slate-300 italic
                                border-l-2 border-electric/40 pl-2">
          {finding.evidence}
        </blockquote>
      )}
      {finding.recommendation && (
        <div className="text-2xs text-slate-300">
          <span className="text-electric font-semibold">
            Recommendation:
          </span>{' '}
          {finding.recommendation}
        </div>
      )}
    </div>
  )
}
