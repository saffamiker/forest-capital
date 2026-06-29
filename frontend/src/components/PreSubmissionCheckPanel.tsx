/**
 * PreSubmissionCheckPanel -- Layer 3b (June 21 2026).
 *
 * Wraps the POST /api/v1/export/verify-all endpoint with a one-click
 * "Verify All for Submission" button + an inline result panel.
 *
 * On click the panel fires the verify-all POST and renders an inline
 * verdict tile from the response:
 *
 *   overall === 'ready'           -> green tile, "All deliverables
 *                                    verified", + submission_recommendation
 *   overall === 'needs_attention' -> amber tile, "Review recommended
 *                                    before submitting", per-document
 *                                    warnings, + submission_recommendation
 *   overall === 'blocked'         -> red tile, "Issues found -- do not
 *                                    submit yet", per-document errors,
 *                                    + submission_recommendation
 *   any status === 'not_generated' surfaces "<Doc name> has not been
 *     generated yet" within the panel
 *
 * The result panel replaces on each click; it does NOT persist across
 * page loads. That's intentional -- pre-submission verification is a
 * point-in-time read of the editor drafts against the platform cache,
 * and the user should trigger a fresh check before each submission.
 */
import { useState } from 'react'
import axios from 'axios'
import {
  ShieldCheck, Loader2, CheckCircle2, AlertTriangle, XCircle,
  FileWarning,
} from 'lucide-react'


type DocVerdict = {
  status?: string
  passed?: boolean
  errors?: Array<{ message?: string; expected?: string; found?: string }>
  warnings?: Array<{ message?: string }>
  data_hash_match?: boolean
  last_verified_at?: string | null
  n_values_verified?: number
  skipped?: string | null
}

type CrossDeliverableFlag = {
  message?: string
  token?: string
  values?: string[]
  documents?: string[]
}

export interface VerifyAllResponse {
  overall: 'ready' | 'needs_attention' | 'blocked'
  submission_recommendation: string
  brief: DocVerdict
  deck: DocVerdict
  appendix: DocVerdict
  cross_deliverable?: {
    passed?: boolean
    flags?: CrossDeliverableFlag[]
  }
}

const DOC_LABELS: Record<string, string> = {
  brief: 'Executive Brief',
  deck: 'Presentation Deck',
  appendix: 'Analytical Appendix',
}


export function PreSubmissionCheckPanel() {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<VerifyAllResponse | null>(null)

  const handleClick = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await axios.post<VerifyAllResponse>(
        '/api/v1/export/verify-all', {})
      setResult(res.data)
    } catch (err) {
      let msg = 'Verification check failed. Try again.'
      if (axios.isAxiosError(err)) {
        const detail = (err.response?.data as { detail?: string })?.detail
        msg = (typeof detail === 'string' ? detail : '') || err.message
      }
      setError(msg)
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section
      data-testid="pre-submission-check-panel"
      className="space-y-3">
      <div className="flex items-baseline gap-3">
        <h2 className="text-white font-semibold text-sm">
          Pre-Submission Check
        </h2>
        <span className="text-2xs text-muted uppercase tracking-wide">
          Verify cache + draft alignment before submitting
        </span>
      </div>

      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={loading}
        data-testid="verify-all-for-submission-button"
        className="flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm
                   font-semibold bg-electric text-white hover:bg-blue-500
                   disabled:opacity-60 disabled:cursor-wait
                   transition-colors">
        {loading ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            Verifying all deliverables against cache...
          </>
        ) : (
          <>
            <ShieldCheck className="w-4 h-4" />
            Verify All for Submission
          </>
        )}
      </button>

      {error && (
        <div
          data-testid="pre-submission-check-error"
          className="rounded border border-danger/30 bg-danger/5
                     px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      {result && <VerdictPanel result={result} />}
    </section>
  )
}


function VerdictPanel({ result }: { result: VerifyAllResponse }) {
  // First: per-document "not generated" tiles. These are surfaced as
  // their own card so the user sees exactly which deliverable is
  // missing before scanning the aggregate verdict.
  const notGenerated = (['brief', 'deck', 'appendix'] as const).filter(
    (k) => result[k].status === 'not_generated')

  if (result.overall === 'ready') {
    return (
      <div
        data-testid="verify-all-verdict-ready"
        className="rounded border border-success/40 bg-success/10
                   px-4 py-3 space-y-2">
        <div className="flex items-start gap-3">
          <CheckCircle2 className="w-4 h-4 text-success shrink-0 mt-0.5" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-success">
              All deliverables verified
            </p>
            <p className="text-2xs text-success/80 mt-0.5 leading-relaxed">
              {result.submission_recommendation}
            </p>
          </div>
        </div>
      </div>
    )
  }

  if (result.overall === 'needs_attention') {
    return (
      <div
        data-testid="verify-all-verdict-needs-attention"
        className="rounded border border-amber-500/40 bg-amber-500/10
                   px-4 py-3 space-y-2">
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-4 h-4 text-amber-300 shrink-0 mt-0.5" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-amber-200">
              Review recommended before submitting
            </p>
            <p className="text-2xs text-amber-200/80 mt-0.5 leading-relaxed">
              {result.submission_recommendation}
            </p>
          </div>
        </div>
        <PerDocumentDetails result={result} severity="warning" />
        <NotGeneratedList docs={notGenerated} severity="warning" />
      </div>
    )
  }

  // Blocked.
  return (
    <div
      data-testid="verify-all-verdict-blocked"
      className="rounded border border-danger/40 bg-danger/10
                 px-4 py-3 space-y-2">
      <div className="flex items-start gap-3">
        <XCircle className="w-4 h-4 text-danger shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-danger">
            Issues found -- do not submit yet
          </p>
          <p className="text-2xs text-danger/80 mt-0.5 leading-relaxed">
            {result.submission_recommendation}
          </p>
        </div>
      </div>
      <PerDocumentDetails result={result} severity="error" />
      <NotGeneratedList docs={notGenerated} severity="error" />
    </div>
  )
}


function PerDocumentDetails({
  result, severity,
}: { result: VerifyAllResponse; severity: 'warning' | 'error' }) {
  const rows: Array<{ key: string; label: string; doc: DocVerdict }> = [
    { key: 'brief', label: DOC_LABELS.brief, doc: result.brief },
    { key: 'deck', label: DOC_LABELS.deck, doc: result.deck },
    {
      key: 'appendix', label: DOC_LABELS.appendix, doc: result.appendix,
    },
  ]
  const relevant = rows.filter((r) => {
    if (severity === 'error') {
      return (r.doc.errors ?? []).length > 0 || r.doc.status === 'failed'
    }
    return (r.doc.warnings ?? []).length > 0
      || r.doc.status === 'warned'
  })
  if (relevant.length === 0) return null
  return (
    <div className="rounded border border-border bg-navy-900 px-3 py-2
                    space-y-1.5">
      {relevant.map((r) => (
        <div key={r.key}
             data-testid={`verify-all-doc-${r.key}-detail`}>
          <p className="text-2xs font-semibold text-slate-200">
            {r.label}
          </p>
          <ul className="text-2xs text-slate-300 leading-relaxed
                         list-disc list-inside ml-1">
            {(severity === 'error'
              ? (r.doc.errors ?? [])
              : (r.doc.warnings ?? [])
            ).slice(0, 5).map((flag, idx) => (
              <li key={idx}>{flag.message ?? '(no detail)'}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  )
}


function NotGeneratedList({
  docs, severity,
}: { docs: ReadonlyArray<'brief' | 'deck' | 'appendix'>
     severity: 'warning' | 'error' }) {
  if (docs.length === 0) return null
  const tone = severity === 'error'
    ? 'border-amber-500/40 bg-amber-500/10 text-amber-200'
    : 'border-amber-500/40 bg-amber-500/10 text-amber-200'
  return (
    <div className={`rounded border ${tone} px-3 py-2 space-y-0.5`}>
      {docs.map((k) => (
        <div key={k}
             data-testid={`verify-all-not-generated-${k}`}
             className="flex items-start gap-1.5 text-2xs">
          <FileWarning className="w-3 h-3 shrink-0 mt-0.5" />
          <span>{DOC_LABELS[k]} has not been generated yet.</span>
        </div>
      ))}
    </div>
  )
}


export default PreSubmissionCheckPanel
