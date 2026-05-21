/**
 * SuggestedResolutions — Banner + Review Modal (Suggested Resolutions, 4/7).
 *
 * The banner sits at the top of the Failure Reports tab. When
 * GET /api/v1/testing/suggestions returns >= 1 pending_review row,
 * the banner renders with a count and a "Review Now" button. Click
 * → the review modal opens with one card per suggestion (paginated
 * with Prev/Next).
 *
 * Each card has three parts:
 *   FAILURE HALF — step title, feature, original tester, severity,
 *     reported date + age, failure description (3-line truncate),
 *     actual result (truncate).
 *   PR HALF — PR number + title (linked), merged by + at, the
 *     verbatim matched_on citation, commit SHAs (first 7 chars,
 *     linked to GitHub commit URLs).
 *   RESOLUTION FIELDS — pre-populated:
 *     resolution_type = code_fix_deployed (changeable)
 *     fix_reference = #{pr_number} (editable)
 *     root_cause = '' (required text)
 *     remediation_note = '' (required when type = code_fix_deployed)
 *
 * Actions:
 *   [Confirm Resolution] — disabled until required fields filled;
 *     POSTs to /suggestions/{id}/approve, removes the card on
 *     success. The backend auto-dismisses sibling pending
 *     suggestions for the same failure_id (decision point 4) — the
 *     modal removes those cards too via the response's
 *     siblings_dismissed list.
 *   [Dismiss Suggestion] — POSTs to /suggestions/{id}/dismiss,
 *     removes the card. No resolution recorded.
 *
 * Scoping — the SuggestionReviewModal accepts `scopedToFailureId` so
 * the row badge in Commit 5 can open the modal showing only that
 * failure's suggestion cards (bypassing the full queue).
 *
 * Session dismissal — the banner stores its dismissed-once flag in
 * sessionStorage. A user who dismisses the banner sees it again on
 * next login if suggestions remain (deliberate — important enough
 * to nag once per session).
 */
import {
  useCallback, useEffect, useMemo, useState,
} from 'react'
import axios from 'axios'
import {
  AlertCircle, ChevronLeft, ChevronRight, ExternalLink, X,
} from 'lucide-react'

import { getTestScript } from '../constants/testScripts'
import { isValidFixReference } from './TestRunnerSettings'


// ── Types — mirror the backend GET /suggestions response shape ───────────────

export interface SuggestionFailure {
  id: number
  script_id: string
  step_id: string
  user_email: string
  failure_description: string | null
  actual_result: string | null
  severity: string | null
  attested_at: string | null
}

export interface PRSuggestion {
  suggestion_id: number
  failure_report_id: number
  pr_number: number
  pr_title: string
  pr_url: string
  pr_merged_at: string | null
  pr_author: string | null
  matched_commit_shas: string[]
  matched_on: string | null
  created_at: string | null
  failure: SuggestionFailure
}


// ── Frontend-side derivations — same helpers Failure Reports uses ────────────

// Mirrors ROUTE_TO_FEATURE in TestRunnerSettings.tsx — re-implemented
// here rather than imported to avoid a circular dependency (the
// banner is imported BY TestRunnerSettings).
const ROUTE_TO_FEATURE: Record<string, string> = {
  '/':          'Dashboard',
  '/analytics': 'Analytics',
  '/council':   'Council',
  '/reports':   'Reports',
  '/settings':  'Settings',
}

function featureForStep(scriptId: string, stepId: string): string {
  const route = getTestScript(scriptId)?.steps
    .find((s) => s.id === stepId)?.route
  return route ? (ROUTE_TO_FEATURE[route] ?? route) : '—'
}

function stepTitleForFailure(scriptId: string, stepId: string): string {
  return getTestScript(scriptId)?.steps
    .find((s) => s.id === stepId)?.title ?? stepId
}

function relativeAge(iso: string | null): string {
  if (!iso) return '—'
  const ms = Date.now() - new Date(iso).getTime()
  if (Number.isNaN(ms)) return iso
  const days = Math.floor(ms / 86_400_000)
  if (days < 1) {
    const hours = Math.floor(ms / 3_600_000)
    return hours < 1 ? 'just now' : `${hours}h ago`
  }
  if (days === 1) return '1 day ago'
  if (days < 30) return `${days} days ago`
  if (days < 365) return `${Math.floor(days / 30)}mo ago`
  return `${Math.floor(days / 365)}y ago`
}

function shortSha(sha: string): string {
  return sha.length > 7 ? sha.slice(0, 7) : sha
}

const REPO = 'saffamiker/forest-capital'

// Order shown in the modal radio — code_fix_deployed first because
// it's the pre-selected default. Extracted to a const so the JSX
// stays readable (the inline `(['…'] as const).map` form triggered
// the TSX generic-parser disambiguation rule).
const RESOLUTION_TYPES_ORDERED = [
  'code_fix_deployed',
  'no_bug_detected',
  'wont_fix',
] as const

function commitUrl(sha: string): string {
  return `https://github.com/${REPO}/commit/${sha}`
}


// ── Session-dismiss helpers ─────────────────────────────────────────────────

const BANNER_DISMISS_KEY = 'fc_suggested_resolutions_banner_dismissed'

function readBannerDismissed(): boolean {
  try {
    return sessionStorage.getItem(BANNER_DISMISS_KEY) === '1'
  } catch {
    return false
  }
}

function writeBannerDismissed(): void {
  try {
    sessionStorage.setItem(BANNER_DISMISS_KEY, '1')
  } catch { /* sessionStorage unavailable — banner reappears */ }
}


// ── SuggestionsBanner ───────────────────────────────────────────────────────

interface SuggestionsBannerProps {
  /** Hook to open the review modal — the parent owns the modal so it
   *  can also be opened from the row badge in Commit 5. */
  onReview: (suggestions: PRSuggestion[]) => void
  /** External refresh trigger — the parent bumps this when an action
   *  in the modal might have changed the suggestion set. */
  refreshKey?: number
}

export function SuggestionsBanner({
  onReview, refreshKey = 0,
}: SuggestionsBannerProps) {
  const [suggestions, setSuggestions] = useState<PRSuggestion[]>([])
  const [dismissed, setDismissed] = useState<boolean>(() =>
    readBannerDismissed())

  const load = useCallback(async () => {
    try {
      const r = await axios.get<{ suggestions: PRSuggestion[] }>(
        '/api/v1/testing/suggestions')
      setSuggestions(r.data.suggestions ?? [])
    } catch {
      // Fail-open: banner renders nothing rather than an error.
      setSuggestions([])
    }
  }, [])

  useEffect(() => { void load() }, [load, refreshKey])

  if (suggestions.length === 0 || dismissed) return null

  const count = suggestions.length

  return (
    <div className="flex items-center justify-between gap-3 rounded
                    border border-electric/40 bg-electric/10 px-3 py-2
                    text-xs"
         role="region" aria-label="Suggested Resolutions">
      <div className="flex items-center gap-2 text-slate-200">
        <AlertCircle className="w-4 h-4 text-electric shrink-0" />
        <span>
          {count} failure{count === 1 ? '' : 's'} may be resolved by
          {' '}recently merged PR{count === 1 ? '' : 's'}. Review and confirm.
        </span>
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <button type="button"
          onClick={() => onReview(suggestions)}
          className="px-3 py-1 rounded text-xs font-medium
                     bg-electric text-white hover:bg-electric/90">
          Review Now
        </button>
        <button type="button"
          onClick={() => { writeBannerDismissed(); setDismissed(true) }}
          aria-label="Dismiss for this session"
          className="text-muted hover:text-white">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  )
}


// ── SuggestionReviewModal — paginated cards ─────────────────────────────────

interface SuggestionReviewModalProps {
  /** The full suggestion set this modal is reviewing. Owner provides
   *  it (either from the banner's loaded list, or from a targeted
   *  fetch for the row-badge case in Commit 5). */
  suggestions: PRSuggestion[]
  /** When set, the modal filters its cards to only this failure's
   *  suggestions. Used by the row badge in Commit 5 to open a
   *  single-card modal scoped to one failure. */
  scopedToFailureId?: number | null
  onClose: () => void
  /** Called whenever a suggestion is approved or dismissed so the
   *  parent can refresh the banner / badge state. */
  onActioned: () => void
}

export function SuggestionReviewModal({
  suggestions, scopedToFailureId = null, onClose, onActioned,
}: SuggestionReviewModalProps) {
  // Apply scoping once; the working list shrinks as the reviewer
  // actions each card.
  const initial = useMemo(() =>
    scopedToFailureId
      ? suggestions.filter((s) => s.failure_report_id === scopedToFailureId)
      : suggestions,
    [suggestions, scopedToFailureId])

  const [working, setWorking] = useState<PRSuggestion[]>(initial)
  const [index, setIndex] = useState(0)

  // Per-card form state. Keyed by suggestion_id so navigating
  // Prev/Next preserves what the reviewer has typed but not yet
  // submitted.
  const [forms, setForms] = useState<Record<number, {
    resolution_type: 'no_bug_detected' | 'code_fix_deployed' | 'wont_fix'
    fix_reference: string
    root_cause: string
    remediation_note: string
  }>>(() => {
    const out: Record<number, any> = {}
    for (const s of initial) {
      out[s.suggestion_id] = {
        resolution_type: 'code_fix_deployed',
        fix_reference: `#${s.pr_number}`,
        root_cause: '',
        remediation_note: '',
      }
    }
    return out
  })

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Esc closes the modal. Native semantics for a dialog.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  // If the working list empties, close the modal — the queue is done.
  useEffect(() => {
    if (working.length === 0) onClose()
  }, [working, onClose])

  if (working.length === 0) return null

  const current = working[Math.min(index, working.length - 1)]!
  const form = forms[current.suggestion_id]!

  const updateForm = (patch: Partial<typeof form>) => {
    setForms((f) => ({
      ...f,
      [current.suggestion_id]: { ...f[current.suggestion_id]!, ...patch },
    }))
  }

  const isCodeFix = form.resolution_type === 'code_fix_deployed'
  const fixOk = !isCodeFix || isValidFixReference(form.fix_reference)
  const remedOk = !isCodeFix || form.remediation_note.trim().length > 0
  const canConfirm =
    form.root_cause.trim().length > 0 && fixOk && remedOk && !submitting

  // Remove a card from the working list and any sibling cards the
  // approve flow auto-dismissed. The next card is whatever lands at
  // the same index, or the last one when we've gone past the end.
  const removeCards = (ids: number[]) => {
    setWorking((w) => {
      const next = w.filter((s) => !ids.includes(s.suggestion_id))
      // Clamp the index so we don't fall off the end.
      setIndex((i) => Math.min(i, Math.max(0, next.length - 1)))
      return next
    })
  }

  const confirm = async () => {
    if (!canConfirm) return
    setSubmitting(true)
    setError(null)
    try {
      const resp = await axios.post<{
        approved: boolean
        failure_id: number
        siblings_dismissed: number[]
      }>(`/api/v1/testing/suggestions/${current.suggestion_id}/approve`,
        {
          root_cause: form.root_cause.trim(),
          remediation_note: form.remediation_note.trim(),
        })
      // The approve flow uses fix_reference = #{pr_number} server-side
      // — it ignores any client-side override of fix_reference. If the
      // reviewer wanted a different reference, they'd dismiss and use
      // the manual modal on the row. (Documented in the endpoint.)
      const removed = [current.suggestion_id,
                       ...(resp.data.siblings_dismissed ?? [])]
      removeCards(removed)
      onActioned()
    } catch (exc) {
      const detail = (exc as {
        response?: { data?: { detail?: string }, status?: number }
      }).response
      if (detail?.status === 409) {
        // Stale — refresh the queue and remove this card.
        removeCards([current.suggestion_id])
        onActioned()
        setError(null)
      } else {
        setError(detail?.data?.detail
          ?? 'Could not approve. Please retry.')
      }
    } finally {
      setSubmitting(false)
    }
  }

  const dismiss = async () => {
    setSubmitting(true)
    setError(null)
    try {
      await axios.post(
        `/api/v1/testing/suggestions/${current.suggestion_id}/dismiss`)
      removeCards([current.suggestion_id])
      onActioned()
    } catch (exc) {
      const detail = (exc as { response?: { data?: { detail?: string } } })
        .response?.data?.detail
      setError(detail ?? 'Could not dismiss. Please retry.')
    } finally {
      setSubmitting(false)
    }
  }

  const stepTitle = stepTitleForFailure(
    current.failure.script_id, current.failure.step_id)
  const feature = featureForStep(
    current.failure.script_id, current.failure.step_id)

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center
                    bg-black/60 p-4" role="presentation" onClick={onClose}>
      <div role="dialog" aria-label="Suggested Resolutions"
           onClick={(e) => e.stopPropagation()}
           className="w-full max-w-2xl rounded-lg border border-border
                      bg-navy-800 shadow-2xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-4 py-3
                        border-b border-border shrink-0">
          <h2 className="text-sm font-semibold text-white">
            Suggested Resolutions
            {working.length > 1 && (
              <span className="text-muted font-normal ml-2">
                · {index + 1} of {working.length}
              </span>
            )}
          </h2>
          <button type="button" onClick={onClose} aria-label="Close"
                  className="text-muted hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-4 py-3 space-y-4 overflow-y-auto flex-1">
          {/* Failure half */}
          <div className="rounded border border-danger/30 bg-danger/5 p-3
                          space-y-1.5 text-2xs">
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="text-white font-medium">{stepTitle}</span>
              <span className="px-1 py-0.5 rounded bg-navy-700 text-muted">
                {feature}
              </span>
              {current.failure.severity && (
                <span className="px-1 py-0.5 rounded bg-navy-700 text-muted">
                  {current.failure.severity}
                </span>
              )}
              <span className="text-muted">{current.failure.user_email}</span>
            </div>
            <div className="text-muted">
              Reported{' '}
              <span title={current.failure.attested_at ?? ''}>
                {relativeAge(current.failure.attested_at)}
              </span>
            </div>
            {current.failure.failure_description && (
              <p className="text-slate-300 line-clamp-3">
                {current.failure.failure_description}
              </p>
            )}
            {current.failure.actual_result && (
              <p className="text-muted line-clamp-2">
                Actual: {current.failure.actual_result}
              </p>
            )}
          </div>

          {/* PR half */}
          <div className="rounded border border-electric/30 bg-electric/5
                          p-3 space-y-1.5 text-2xs">
            <div>
              <a href={current.pr_url} target="_blank" rel="noopener noreferrer"
                 className="text-electric hover:underline inline-flex
                            items-center gap-1 font-medium">
                #{current.pr_number} {current.pr_title}
                <ExternalLink className="w-2.5 h-2.5" />
              </a>
            </div>
            {current.pr_author && current.pr_merged_at && (
              <div className="text-muted">
                Merged by {current.pr_author} at{' '}
                {new Date(current.pr_merged_at).toLocaleString()}
              </div>
            )}
            {current.matched_on && (
              <div className="text-muted">
                Matched on:{' '}
                <span className="text-slate-300 font-mono">
                  "{current.matched_on}"
                </span>
              </div>
            )}
            {current.matched_commit_shas.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                <span className="text-muted">Commits:</span>
                {current.matched_commit_shas.map((s) => (
                  <a key={s} href={commitUrl(s)} target="_blank"
                     rel="noopener noreferrer"
                     className="font-mono text-electric hover:underline">
                    {shortSha(s)}
                  </a>
                ))}
              </div>
            )}
          </div>

          {/* Resolution fields — pre-populated, editable. */}
          <div className="space-y-3">
            <div>
              <label className="text-2xs uppercase tracking-wider text-muted
                                block mb-1.5">
                Resolution type
              </label>
              <div className="space-y-1">
                {RESOLUTION_TYPES_ORDERED.map((t) => (
                  <label key={t}
                         className="flex items-start gap-2 text-xs
                                    text-slate-200 cursor-pointer">
                    <input type="radio" name="suggest_res_type" value={t}
                      checked={form.resolution_type === t}
                      onChange={() => updateForm({ resolution_type: t })}
                      className="mt-0.5 accent-electric" />
                    <span>
                      {t === 'code_fix_deployed' && 'Code fix deployed'}
                      {t === 'no_bug_detected' && 'No bug detected'}
                      {t === 'wont_fix' && "Won't fix / by design"}
                    </span>
                  </label>
                ))}
              </div>
            </div>

            <div>
              <label className="text-2xs uppercase tracking-wider text-muted
                                block mb-1">
                Fix reference
              </label>
              <input value={form.fix_reference}
                onChange={(e) => updateForm({ fix_reference: e.target.value })}
                placeholder="#PR-number, commit SHA, or GitHub URL"
                className={`w-full rounded border bg-navy-900 px-2 py-1.5
                            text-xs text-white font-mono ${
                              form.fix_reference && !fixOk
                                ? 'border-danger/60'
                                : 'border-border'}`} />
              {form.fix_reference && !fixOk && (
                <p className="text-2xs text-danger mt-1">
                  Must be 7+ hex characters, #NNN, or a GitHub URL.
                </p>
              )}
            </div>

            <div>
              <label className="text-2xs uppercase tracking-wider text-muted
                                block mb-1">
                Root cause <span className="text-danger">*</span>
              </label>
              <textarea value={form.root_cause}
                onChange={(e) => updateForm({ root_cause: e.target.value })}
                rows={3} placeholder="What caused this failure?"
                className="w-full rounded border border-border bg-navy-900
                           px-2 py-1.5 text-xs text-white" />
            </div>

            {isCodeFix && (
              <div>
                <label className="text-2xs uppercase tracking-wider text-muted
                                  block mb-1">
                  Remediation note <span className="text-danger">*</span>
                </label>
                <textarea value={form.remediation_note}
                  onChange={(e) =>
                    updateForm({ remediation_note: e.target.value })}
                  rows={3}
                  placeholder="What was changed and how does it address the failure?"
                  className="w-full rounded border border-border bg-navy-900
                             px-2 py-1.5 text-xs text-white" />
              </div>
            )}

            {error && (
              <p className="text-2xs text-danger" role="alert">{error}</p>
            )}
          </div>
        </div>

        {/* Footer — Prev/Next on the left, actions on the right. */}
        <div className="flex items-center justify-between gap-2 px-4 py-3
                        border-t border-border shrink-0">
          <div className="flex items-center gap-1">
            {working.length > 1 && (
              <>
                <button type="button"
                  onClick={() => setIndex((i) => Math.max(0, i - 1))}
                  disabled={index === 0}
                  aria-label="Previous suggestion"
                  className="p-1 text-muted hover:text-white
                             disabled:opacity-30 disabled:cursor-not-allowed">
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <button type="button"
                  onClick={() => setIndex((i) =>
                    Math.min(working.length - 1, i + 1))}
                  disabled={index >= working.length - 1}
                  aria-label="Next suggestion"
                  className="p-1 text-muted hover:text-white
                             disabled:opacity-30 disabled:cursor-not-allowed">
                  <ChevronRight className="w-4 h-4" />
                </button>
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={() => void dismiss()}
              disabled={submitting}
              className="px-3 py-1.5 text-xs text-muted hover:text-white">
              Dismiss Suggestion
            </button>
            <button type="button" onClick={() => void confirm()}
              disabled={!canConfirm}
              className="px-4 py-1.5 rounded text-xs font-medium
                         bg-electric text-white
                         disabled:bg-navy-700 disabled:text-muted
                         disabled:cursor-not-allowed">
              {submitting ? 'Saving…' : 'Confirm Resolution'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
