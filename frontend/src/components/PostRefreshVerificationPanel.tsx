/**
 * PostRefreshVerificationPanel -- June 27 2026.
 *
 * Auto-fires POST /api/v1/data/verify-post-refresh whenever the
 * triggerKey prop changes (typically the timestamp from a
 * successful light refresh). Renders a 3-colour summary:
 *
 *   GREEN  "Verified -- ready for submission"
 *   AMBER  "X warnings -- review before submitting"
 *   RED    "X failures -- not ready for submission"
 *
 * Expandable detail rows surface every failed / warning token,
 * its expected vs actual value, the scope it falls under, and
 * the verifier's per-token message. A separate rounding-summary
 * block reports how many values passed the canonical rule and
 * lists any inconsistent tokens.
 *
 * The panel replaces the legacy "All drafts already current"
 * message on the Light Refresh card -- the verifier is the
 * post-refresh confirmation. Also mounted on the Data Reference
 * Sheet page (via the "Verify submission data" button) so an
 * operator can run it on demand without triggering a full
 * refresh.
 *
 * triggerKey == null -> idle (no fetch yet)
 * triggerKey changed  -> fire fetch, render the response
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import {
  CheckCircle, AlertTriangle, AlertCircle, Loader2,
  ChevronDown, ChevronRight,
} from 'lucide-react'


export type VerificationStatus = 'pass' | 'fail' | 'warning'

export type VerificationScope =
  | 'IN_SCOPE_LOCKED'
  | 'IN_SCOPE_CONSTANT'
  | 'IN_SCOPE_FULL_DATASET'
  | 'OUT_OF_SCOPE_LIVE'


export interface VerificationResult {
  token:              string
  label:              string
  scope:              VerificationScope
  expected:           string
  actual:             string
  rounded_correctly:  boolean
  status:             VerificationStatus
  message:            string
}

export interface RoundingSummary {
  checked:              number
  consistent:           number
  inconsistent:         number
  inconsistent_tokens:  string[]
}

export interface VerificationResponse {
  verified_at:            string
  freeze_active:          boolean
  freeze_hash:            string | null
  effective_hash:         string
  passed:                 number
  failed:                 number
  warnings:               number
  results:                VerificationResult[]
  rounding_summary:       RoundingSummary
  ready_for_submission:   boolean
}


export interface PostRefreshVerificationPanelProps {
  /** Bumping this prop re-fires the verification fetch. Pass
   *  the light-refresh response timestamp, or a random nonce
   *  from a manual "Verify submission data" button. */
  triggerKey: string | number | null
}


export default function PostRefreshVerificationPanel(
  { triggerKey }: PostRefreshVerificationPanelProps,
): React.ReactElement | null {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<VerificationResponse | null>(
    null)
  const [error, setError] = useState<string | null>(null)
  const [detailsOpen, setDetailsOpen] = useState(false)

  useEffect(() => {
    if (triggerKey === null) return
    let cancelled = false
    setBusy(true)
    setError(null)
    setResult(null)
    void (async () => {
      try {
        const res = await axios.post<VerificationResponse>(
          '/api/v1/data/verify-post-refresh')
        if (!cancelled) setResult(res.data)
      } catch (err) {
        if (cancelled) return
        const msg = axios.isAxiosError(err)
          ? (err.response?.data?.detail ?? err.message)
          : 'Verification failed'
        setError(
          typeof msg === 'string' ? msg : JSON.stringify(msg))
      } finally {
        if (!cancelled) setBusy(false)
      }
    })()
    return () => { cancelled = true }
  }, [triggerKey])

  if (triggerKey === null) return null

  if (busy) {
    return (
      <div
        data-testid="post-refresh-verification-loading"
        className="rounded border border-slate-500/40
                   bg-slate-700/20 p-2.5 text-2xs text-slate-300
                   flex items-center gap-1.5">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
        <span>Running verification pass...</span>
      </div>
    )
  }

  if (error) {
    return (
      <div
        data-testid="post-refresh-verification-error"
        className="rounded border border-danger/40
                   bg-danger/5 p-2.5 text-2xs text-danger
                   flex items-start gap-1.5">
        <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          Verification request failed: {error}
        </span>
      </div>
    )
  }

  if (!result) return null

  // Summary-line classification per spec:
  //   ready_for_submission=true -> GREEN "Verified -- ready"
  //   failed > 0                -> RED "X failures -- not ready"
  //   warnings > 0 (no fails)   -> AMBER "X warnings -- review"
  //   else                      -> GREEN (covered by ready path)
  let summaryColour: 'success' | 'warning' | 'danger' = 'success'
  let summaryIcon = (
    <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />)
  let summaryText: string
  if (result.failed > 0) {
    summaryColour = 'danger'
    summaryIcon = (
      <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />)
    summaryText = (
      `${result.failed} failure(s) -- NOT ready for submission`)
  } else if (result.warnings > 0) {
    summaryColour = 'warning'
    summaryIcon = (
      <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />)
    summaryText = (
      `${result.warnings} warning(s) -- review before submitting`)
  } else if (result.ready_for_submission) {
    summaryText = 'Verified -- ready for submission'
  } else {
    summaryColour = 'warning'
    summaryIcon = (
      <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />)
    summaryText = (
      'Verification incomplete -- review before submitting')
  }

  const summaryClass = (
    'rounded border p-2.5 text-2xs flex items-start gap-1.5 '
    + (summaryColour === 'success'
       ? 'border-success/40 bg-success/10 text-success'
       : (summaryColour === 'warning'
          ? 'border-warning/40 bg-warning/10 text-warning'
          : 'border-danger/40 bg-danger/10 text-danger')))

  const showableResults = result.results.filter(
    (r) => r.status !== 'pass')

  return (
    <div
      data-testid="post-refresh-verification-panel"
      className="space-y-2">
      <div
        data-testid="post-refresh-verification-summary"
        className={summaryClass}>
        {summaryIcon}
        <div className="flex-1">
          <div className="font-medium">{summaryText}</div>
          <div className="text-muted text-2xs mt-0.5">
            {result.passed} passed | {result.warnings} warnings
            {' | '}{result.failed} failed
            {result.freeze_active && result.freeze_hash ? (
              <span> | freeze {result.freeze_hash.slice(0, 8)}</span>
            ) : null}
          </div>
        </div>
      </div>

      {/* Rounding summary */}
      <div
        data-testid="post-refresh-rounding-summary"
        className={(
          'rounded border p-2.5 text-2xs '
          + (result.rounding_summary.inconsistent === 0
             ? 'border-slate-500/40 bg-slate-700/20 text-slate-300'
             : 'border-warning/40 bg-warning/5 text-warning'))}>
        {result.rounding_summary.inconsistent === 0
          ? `All ${result.rounding_summary.checked} values rounded `
            + 'consistently with the canonical rules.'
          : (
            <>
              <div>
                {result.rounding_summary.inconsistent} value(s)
                {' '}have rounding inconsistent with the canonical
                rule (of {result.rounding_summary.checked} checked).
                Inconsistent tokens:
              </div>
              <ul className="font-mono text-2xs mt-1
                            list-disc list-inside">
                {result.rounding_summary.inconsistent_tokens.map(
                  (t) => (
                    <li key={t}>{t}</li>
                  ))}
              </ul>
            </>
          )}
      </div>

      {/* Expandable detail of failed + warning tokens */}
      {showableResults.length > 0 ? (
        <div
          data-testid="post-refresh-verification-details"
          className="rounded border border-slate-500/40
                     bg-slate-700/10 text-2xs text-slate-300">
          <button
            type="button"
            onClick={() => setDetailsOpen(!detailsOpen)}
            className="w-full flex items-center gap-1
                       px-2.5 py-1.5 font-medium text-slate-100
                       hover:bg-slate-700/30">
            {detailsOpen
              ? <ChevronDown className="w-3 h-3" />
              : <ChevronRight className="w-3 h-3" />}
            {showableResults.length} token(s) flagged -- click to
            {detailsOpen ? ' collapse' : ' expand'}
          </button>
          {detailsOpen ? (
            <div className="px-2.5 pb-2.5 space-y-1.5">
              {showableResults.map((r) => (
                <div
                  key={r.token}
                  data-testid={`verification-detail-${
                    r.token.replace(/[{}]/g, '')}`}
                  className={(
                    'rounded border p-2 '
                    + (r.status === 'fail'
                       ? 'border-danger/40 bg-danger/5'
                       : 'border-warning/40 bg-warning/5'))}>
                  <div className="flex items-center gap-2">
                    <span className={(
                      'font-mono text-2xs '
                      + (r.status === 'fail'
                         ? 'text-danger' : 'text-warning'))}>
                      {r.token}
                    </span>
                    <span className="text-muted text-2xs">
                      ({r.scope.replace('IN_SCOPE_', '')
                              .replace('OUT_OF_SCOPE_', '')})
                    </span>
                  </div>
                  <div className="mt-0.5 text-slate-300">
                    {r.message}
                  </div>
                  <div className="mt-0.5 text-muted text-2xs
                                  font-mono">
                    value: {r.actual || '—'}
                    {!r.rounded_correctly
                      ? ' | rounding inconsistent'
                      : ''}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
