import { useState, useEffect, useRef, useCallback } from 'react'
import axios from 'axios'
import {
  CheckCircle, XCircle, AlertTriangle, ChevronDown, ChevronUp, RefreshCw,
  HelpCircle, Flag, ShieldCheck, Clipboard, ClipboardCheck, Lock,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { Verdict, QACheck, QAActionType } from '../types/agents'
import type { QAItemExplanation } from '../types/glossary'
import { useQAStore } from '../stores/qaStore'
import { useGlossaryStore } from '../stores/glossaryStore'

interface VerdictStyle {
  Icon: LucideIcon
  color: string
  bg: string
  border: string
  badge: string
}

// May 22 2026 — INCOMPLETE added as a fourth verdict. The slate styling
// is deliberately distinct from WARN's amber so a row of INCOMPLETE
// items does NOT read as "the audit has 38 concerns" — it reads as
// "the audit did not finish 38 checks". The HelpCircle icon reinforces
// the unknown/unexamined semantic.
const VERDICT_CONFIG: Record<Verdict, VerdictStyle> = {
  PASS: { Icon: CheckCircle,   color: 'text-success', bg: 'bg-success/10', border: 'border-success/20', badge: 'badge-pass' },
  FAIL: { Icon: XCircle,       color: 'text-danger',  bg: 'bg-danger/10',  border: 'border-danger/20',  badge: 'badge-fail' },
  WARN: { Icon: AlertTriangle, color: 'text-warning', bg: 'bg-warning/10', border: 'border-warning/20', badge: 'badge-warn' },
  INCOMPLETE: { Icon: HelpCircle, color: 'text-slate-300', bg: 'bg-slate-500/10', border: 'border-slate-400/20', badge: 'badge-incomplete' },
}

function VerdictBadge({ verdict }: { verdict: Verdict }) {
  const cfg = VERDICT_CONFIG[verdict]
  return <span className={cfg.badge}>{verdict}</span>
}


// May 24 2026 — submission-readiness badge taxonomy. Each
// submission_label maps to one badge style. The colours follow the
// user's spec: green pass, amber blocking, amber informational,
// red blocking, grey planned, orange non-deterministic.
//
// The labels are deliberately verbose ("WARN – disclosure required")
// because the user's spec asks for the classification to be
// visible AT THE BADGE, not behind a hover.
const SUBMISSION_BADGES: Record<string, {
  label: string
  bg: string
  text: string
  border: string
}> = {
  pass: {
    label: 'PASS',
    bg: 'bg-green-500/15',
    text: 'text-green-300',
    border: 'border-green-500/30',
  },
  warn_disclosure: {
    label: 'WARN — disclosure required',
    bg: 'bg-amber-500/15',
    text: 'text-amber-300',
    border: 'border-amber-500/40',
  },
  warn_non_blocking: {
    label: 'WARN — non-blocking',
    bg: 'bg-amber-500/10',
    text: 'text-amber-300/80',
    border: 'border-amber-500/20',
  },
  warn_blocking: {
    label: 'WARN — blocks submission',
    bg: 'bg-red-500/15',
    text: 'text-red-300',
    border: 'border-red-500/30',
  },
  incomplete_blocking: {
    label: 'INCOMPLETE — blocks submission',
    bg: 'bg-red-500/15',
    text: 'text-red-300',
    border: 'border-red-500/30',
  },
  incomplete_planned: {
    label: 'INCOMPLETE — planned extension',
    bg: 'bg-slate-500/15',
    text: 'text-slate-300',
    border: 'border-slate-400/30',
  },
  fail_blocking: {
    label: 'FAIL — blocks submission',
    bg: 'bg-red-500/15',
    text: 'text-red-300',
    border: 'border-red-500/30',
  },
}

// Orange overlay applied when the audit runner flagged the check as
// non-deterministic (two consecutive runs disagreed on its verdict).
// The overlay style replaces the submission_label style — see
// SubmissionBadge below.
const NON_DETERMINISTIC_STYLE = {
  label: 'NON-DETERMINISTIC — requires human review',
  bg: 'bg-orange-500/15',
  text: 'text-orange-300',
  border: 'border-orange-500/40',
}


function SubmissionBadge({
  check,
}: {
  check: { submission_label?: string | null; non_deterministic?: boolean | null; status: Verdict }
}) {
  // Non-deterministic overlay wins regardless of submission_label —
  // an AI verdict that flickers across runs is never a clean PASS.
  if (check.non_deterministic) {
    const s = NON_DETERMINISTIC_STYLE
    return (
      <span className={`px-2 py-0.5 rounded text-2xs font-medium border ${s.bg} ${s.text} ${s.border}`}>
        {s.label}
      </span>
    )
  }
  const label = check.submission_label
  if (!label || !(label in SUBMISSION_BADGES)) {
    // Legacy fall-back — pre-May-24 audit rows have no submission_label.
    // Render the plain status badge so the panel still shows something.
    return <VerdictBadge verdict={check.status} />
  }
  const s = SUBMISSION_BADGES[label]
  return (
    <span className={`px-2 py-0.5 rounded text-2xs font-medium border ${s.bg} ${s.text} ${s.border}`}>
      {s.label}
    </span>
  )
}


// May 24 2026 — top-of-page submission readiness banner. Reads
// `submission_status` + `submission_banner` from the audit
// response (set by the backend's _build_report).
function SubmissionReadinessBanner({
  status, banner, counts,
}: {
  status?: 'ready' | 'ready_with_acknowledgements' | 'not_ready' | undefined
  banner?: string | undefined
  counts?: {
    blocking_total?: number
    warn_disclosure?: number
    incomplete_blocking?: number
    fail_blocking?: number
    warn_blocking?: number
  } | undefined
}) {
  if (!status || !banner) return null

  const styles: Record<typeof status, {
    bg: string; text: string; border: string;
  }> = {
    ready: {
      bg: 'bg-green-500/10', text: 'text-green-200',
      border: 'border-green-500/40',
    },
    ready_with_acknowledgements: {
      bg: 'bg-amber-500/10', text: 'text-amber-200',
      border: 'border-amber-500/40',
    },
    not_ready: {
      bg: 'bg-red-500/10', text: 'text-red-200',
      border: 'border-red-500/50',
    },
  }
  const s = styles[status]
  return (
    <div
      data-testid="qa-submission-banner"
      data-status={status}
      className={`mb-3 px-4 py-3 rounded-lg border ${s.bg} ${s.text} ${s.border}`}>
      <p className="text-sm font-semibold">{banner}</p>
      {counts && (counts.warn_disclosure || counts.incomplete_blocking ||
                   counts.fail_blocking || counts.warn_blocking) ? (
        <p className="text-xs mt-1 leading-snug opacity-90">
          {counts.fail_blocking ? `${counts.fail_blocking} fail · ` : ''}
          {counts.incomplete_blocking ? `${counts.incomplete_blocking} incomplete · ` : ''}
          {counts.warn_blocking ? `${counts.warn_blocking} warn blocking · ` : ''}
          {counts.warn_disclosure ? `${counts.warn_disclosure} disclosure(s) required` : ''}
        </p>
      ) : null}
    </div>
  )
}

// One short label per action_type — surfaced as the heading of the
// Action Required card so the user reads what's expected of them.
const ACTION_HEADINGS: Record<QAActionType, string> = {
  code_fix:             'Code fix needed',
  methodology_decision: 'Methodology decision needed',
  disclosure_required:  'Disclosure required',
  rerun_required:       'Re-run required',
}

// Disambiguation copy below the heading. The methodology_decision
// variant explicitly names both interpretations so the team knows the
// finding is ambiguous before the buttons render.
const ACTION_BLURB: Record<QAActionType, string> = {
  code_fix: (
    'The platform has a defect to fix in code. The remediation below '
    + 'describes the change.'),
  methodology_decision: (
    'The finding is ambiguous — it could be an intentional design '
    + 'choice or an error. Read both interpretations in the '
    + 'remediation, then decide.'),
  disclosure_required: (
    'The condition is acceptable but must be disclosed in the '
    + 'academic report. The disclosure text is ready to paste.'),
  rerun_required: (
    'The agent could not complete this check. Re-run the audit so it '
    + 'can examine the data.'),
}

/**
 * ActionCard — the Finding / Implication / Action Required block on
 * an expanded WARN, FAIL, or INCOMPLETE check. Rendered only when the
 * check carries at least one structured field; PASS sections (no
 * structured fields) and deterministic checks (where the arithmetic
 * IS the finding) suppress it.
 *
 * Button variants by action_type:
 *   code_fix              → Flag for Fix (stubbed — TODO toast)
 *   methodology_decision  → Mark as Intentional + Flag for Fix
 *   disclosure_required   → Copy Disclosure Text (real clipboard copy)
 *   rerun_required        → Re-run Audit (calls qaStore.reload)
 *
 * The Flag for Fix and Mark as Intentional buttons are stubbed in this
 * commit — the backend endpoints land in a subsequent commit. Each
 * shows a TODO toast naming the action so a tester reviewing the UI
 * card layout sees the affordance and the placement.
 */
// Server payload from /api/v1/qa/intentional-overrides — keyed by
// check_id. When a row exists for a check, the Action Required card
// is replaced with a "Confirmed intentional" badge so the team's
// methodology judgement persists across audit runs.
interface IntentionalOverride {
  marked_at: string | null
  marked_by: string
  note: string | null
  audit_run_hash: string | null
}

// Workstream F (May 28 2026) — Revoke disclosure control. When the
// team later concludes a finding is not actually intentional, the
// override row must be removed and the Action Required card must
// re-render on the next audit read. The confirmation modal prevents
// an accidental click from dropping a deliberately-recorded
// disclosure with no undo. The DELETE endpoint is idempotent — a
// revoke on a missing row returns deleted=false rather than 404 — so
// the frontend never has to pre-check existence.
function RevokeDisclosureControl({
  checkId, currentNote, onRevoked,
}: {
  checkId: string
  currentNote: string
  onRevoked?: () => void
}) {
  const [open, setOpen] = useState(false)
  const [revoking, setRevoking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setRevoking(true)
    setError(null)
    try {
      await axios.delete(
        `/api/v1/qa/findings/${checkId}/mark-intentional`)
      setOpen(false)
      // The parent re-fetches /intentional-overrides so the Action
      // Required card re-renders without a page reload. The
      // report-readiness gate (workstream C) re-evaluates because
      // the row is now absent from the overrides list.
      onRevoked?.()
    } catch {
      setError('Could not revoke — please retry.')
    } finally {
      setRevoking(false)
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => { setError(null); setOpen(true) }}
        data-testid={`qa-revoke-disclosure-${checkId}`}
        className="text-2xs text-danger hover:underline mt-1.5 ml-3">
        Revoke disclosure
      </button>
      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center
                     bg-black/60 p-4"
          onClick={() => { if (!revoking) setOpen(false) }}
          data-testid={`qa-revoke-disclosure-modal-${checkId}`}>
          <div className="card p-5 max-w-md w-full space-y-3"
               onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-white">
              Revoke this disclosure?
            </h3>
            <p className="text-xs text-muted leading-relaxed">
              This removes the intentional-design override on{' '}
              <span className="text-slate-200">{checkId}</span>. The
              Action Required card will re-render on the next audit
              read and the report-readiness gate will re-evaluate the
              warning. The current note is shown below for reference
              and will be discarded.
            </p>
            {currentNote && (
              <div className="rounded border border-border bg-navy-900
                              px-3 py-2 text-2xs text-slate-300
                              leading-relaxed italic">
                {currentNote}
              </div>
            )}
            {error && (
              <p className="text-2xs text-danger" role="status">{error}</p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setOpen(false)}
                disabled={revoking}
                data-testid={`qa-revoke-disclosure-cancel-${checkId}`}
                className="px-3 py-1.5 rounded text-xs border border-border
                           text-muted hover:text-white transition-colors
                           disabled:opacity-50 disabled:cursor-not-allowed">
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void submit()}
                disabled={revoking}
                data-testid={`qa-revoke-disclosure-confirm-${checkId}`}
                className="px-3 py-1.5 rounded text-xs font-medium
                           bg-danger/10 border border-danger/40 text-danger
                           hover:bg-danger/20 transition-colors
                           disabled:opacity-50 disabled:cursor-not-allowed">
                {revoking ? 'Revoking…' : 'Revoke disclosure'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}


// Workstream E (May 28 2026) — Edit disclosure control. When a check
// has been Marked Intentional, the team needs to refine the note
// without losing the existing entry. The control reopens the same
// disclosure modal pre-populated with the override's current note;
// the mark-intentional endpoint upserts (ON CONFLICT DO UPDATE) so a
// second POST updates the row in place — no new route required. The
// modal mirrors the Mark-as-Intentional pattern in ActionButtons (20-
// character minimum, char counter, Confirm-disabled-below-threshold)
// so editing and creating feel identical to the user.
function EditDisclosureControl({
  checkId, currentNote, onSaved,
}: {
  checkId: string
  currentNote: string
  onSaved?: () => void
}) {
  const MIN_NOTE_LEN = 20
  const [open, setOpen] = useState(false)
  const [note, setNote] = useState(currentNote)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Pre-populate every time the modal is reopened — if the parent
  // refreshes the override (e.g. after another tab saved), the user
  // sees the latest note when reopening rather than a stale draft.
  const openModal = () => {
    setNote(currentNote)
    setError(null)
    setOpen(true)
  }

  const submit = async () => {
    if (note.trim().length < MIN_NOTE_LEN) return
    setSaving(true)
    setError(null)
    try {
      await axios.post(
        `/api/v1/qa/findings/${checkId}/mark-intentional`,
        { note: note.trim() })
      setOpen(false)
      onSaved?.()
    } catch {
      setError('Could not save — please retry.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={openModal}
        data-testid={`qa-edit-disclosure-${checkId}`}
        className="text-2xs text-electric hover:underline mt-1.5">
        Edit disclosure
      </button>
      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center
                     bg-black/60 p-4"
          onClick={() => { if (!saving) setOpen(false) }}
          data-testid={`qa-edit-disclosure-modal-${checkId}`}>
          <div className="card p-5 max-w-md w-full space-y-3"
               onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-white">
              Edit Disclosure
            </h3>
            <p className="text-xs text-muted leading-relaxed">
              Update the team's recorded rationale for marking this
              finding intentional. The new note replaces the existing
              entry in the audit trail.
            </p>
            <textarea
              data-testid={`qa-edit-disclosure-note-${checkId}`}
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={saving}
              rows={5}
              minLength={MIN_NOTE_LEN}
              placeholder="Describe the intentional design decision and why it is acceptable..."
              className="w-full rounded border border-border bg-navy-900
                         text-xs text-slate-200 placeholder-muted p-2.5
                         focus:outline-none focus:border-electric
                         disabled:opacity-60 disabled:cursor-not-allowed
                         leading-relaxed resize-none"
            />
            <div className="flex items-center justify-between text-2xs">
              <span className="text-muted">
                {note.trim().length} / {MIN_NOTE_LEN} characters minimum
              </span>
            </div>
            {error && (
              <p className="text-2xs text-danger" role="status">{error}</p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setOpen(false)}
                disabled={saving}
                data-testid={`qa-edit-disclosure-cancel-${checkId}`}
                className="px-3 py-1.5 rounded text-xs border border-border
                           text-muted hover:text-white transition-colors
                           disabled:opacity-50 disabled:cursor-not-allowed">
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void submit()}
                disabled={saving || note.trim().length < MIN_NOTE_LEN}
                data-testid={`qa-edit-disclosure-confirm-${checkId}`}
                className="px-3 py-1.5 rounded text-xs font-medium
                           bg-success/10 border border-success/40 text-success
                           hover:bg-success/20 transition-colors
                           disabled:opacity-50 disabled:cursor-not-allowed">
                {saving ? 'Saving…' : 'Save changes'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function ActionCard({
  check, onReRun, isReRunning, override, onIntentionalMarked,
}: {
  check: QACheck
  onReRun: () => void
  isReRunning: boolean
  override?: IntentionalOverride
  onIntentionalMarked?: () => void
}) {
  const action = check.action_type
  const hasStructured = !!(
    check.finding || check.implication || check.remediation
    || check.disclosure_text || action
  )
  if (!hasStructured) return null

  // The team previously confirmed this check is intentional — render
  // a permanent badge in place of the Action Required block. The
  // finding / implication still render above so the audit trail of
  // what was reviewed stays visible.
  if (override) {
    const dateLabel = override.marked_at
      ? new Date(override.marked_at).toLocaleDateString(undefined,
          { year: 'numeric', month: 'short', day: 'numeric' })
      : 'unknown date'
    return (
      <div
        data-testid={`qa-action-card-${check.check_id}`}
        className="mt-2 rounded border border-border bg-navy-800 px-3 py-2.5
                   space-y-2.5">
        {check.finding && (
          <div>
            <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">
              Finding
            </div>
            <p className="text-slate-200 text-xs leading-relaxed">
              {check.finding}
            </p>
          </div>
        )}
        {check.implication && (
          <div>
            <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">
              Implication
            </div>
            <p className="text-slate-300 text-xs leading-relaxed">
              {check.implication}
            </p>
          </div>
        )}
        <div
          data-testid={`qa-intentional-badge-${check.check_id}`}
          className="flex items-start gap-2 rounded border border-success/30
                     bg-success/10 px-3 py-2.5">
          <Lock className="w-3.5 h-3.5 text-success shrink-0 mt-0.5" />
          <div className="min-w-0">
            <div className="text-2xs uppercase tracking-wide text-success
                            font-semibold">
              Confirmed Intentional — recorded {dateLabel}
            </div>
            <p className="text-slate-300 text-2xs mt-0.5">
              Reviewed by {override.marked_by}. The team has confirmed this
              behaviour is intentional methodology, not a defect. The
              override persists across audit runs.
            </p>
            {override.note && (
              <p className="text-slate-400 text-2xs mt-1 italic">
                Note: {override.note}
              </p>
            )}
            {/* Edit / Revoke disclosure controls. Edit reopens the
                Mark-as-Intentional modal pre-populated with the
                existing note (the endpoint upserts on check_id so a
                second POST UPDATEs in place). Revoke deletes the
                override after a confirmation modal — the Action
                Required card re-renders on the next audit read and
                the report-readiness gate re-evaluates. Both share
                the onIntentionalMarked callback so the badge state
                refreshes without a page reload. */}
            <div className="flex items-center">
              <EditDisclosureControl
                checkId={check.check_id}
                currentNote={override.note ?? ''}
                {...(onIntentionalMarked
                  ? { onSaved: onIntentionalMarked } : {})}
              />
              <RevokeDisclosureControl
                checkId={check.check_id}
                currentNote={override.note ?? ''}
                {...(onIntentionalMarked
                  ? { onRevoked: onIntentionalMarked } : {})}
              />
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div
      data-testid={`qa-action-card-${check.check_id}`}
      className="mt-2 rounded border border-border bg-navy-800 px-3 py-2.5
                 space-y-2.5">
      {check.finding && (
        <div>
          <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">
            Finding
          </div>
          <p className="text-slate-200 text-xs leading-relaxed">
            {check.finding}
          </p>
        </div>
      )}
      {check.implication && (
        <div>
          <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">
            Implication
          </div>
          <p className="text-slate-300 text-xs leading-relaxed">
            {check.implication}
          </p>
        </div>
      )}
      {action && (
        <div className="rounded border border-electric/20 bg-electric/5
                        px-2.5 py-2 space-y-2">
          <div>
            <div className="text-2xs uppercase tracking-wide text-electric
                            font-semibold mb-0.5">
              Action Required — {ACTION_HEADINGS[action]}
            </div>
            <p className="text-slate-300 text-2xs leading-relaxed">
              {ACTION_BLURB[action]}
            </p>
          </div>
          {check.remediation && (
            <p className="text-slate-200 text-xs leading-relaxed
                          whitespace-pre-wrap">
              {check.remediation}
            </p>
          )}
          <ActionButtons
            check={check}
            onReRun={onReRun}
            isReRunning={isReRunning}
            {...(onIntentionalMarked
              ? { onIntentionalMarked } : {})}
          />
        </div>
      )}
    </div>
  )
}

function ActionButtons({
  check, onReRun, isReRunning, onIntentionalMarked,
}: {
  check: QACheck
  onReRun: () => void
  isReRunning: boolean
  // Parent callback — the QAAuditPanel re-fetches the overrides list
  // after a successful Mark Intentional so the badge swap is
  // immediate (no need to wait for the next audit run).
  onIntentionalMarked?: () => void
}) {
  const [toast, setToast] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [flagging, setFlagging] = useState(false)
  const [marking, setMarking] = useState(false)
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 2-second auto-dismiss so the toast doesn't linger after the user
  // moves on. Cleared on unmount to prevent a memory leak.
  useEffect(() => () => {
    if (toastTimer.current) clearTimeout(toastTimer.current)
  }, [])

  const showToast = (msg: string) => {
    setToast(msg)
    if (toastTimer.current) clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 3000)
  }

  // Real endpoints — wired May 22 2026. The TODO toasts that
  // previously surfaced the affordance placement now do real work.
  // Both POSTs are fire-and-forget from the user's POV: the toast
  // confirms success / failure but the audit display only refreshes
  // for Mark Intentional (so the "Confirmed intentional" badge
  // appears immediately).
  const onFlagForFix = async () => {
    setFlagging(true)
    try {
      const res = await axios.post(
        `/api/v1/qa/findings/${check.check_id}/flag-for-fix`,
        {
          check_title: check.check,
          finding: check.finding ?? null,
          implication: check.implication ?? null,
          remediation: check.remediation ?? null,
          severity: check.status === 'FAIL' ? 'blocking' : 'major',
        },
      )
      const itemId = res.data?.triage_item_id as number | undefined
      showToast(
        `Flagged ${check.check_id} for fix · triage item ${
          itemId ? `#${itemId}` : 'created'}`)
    } catch {
      showToast(`Could not flag ${check.check_id} — please retry.`)
    } finally {
      setFlagging(false)
    }
  }

  // Mark-as-Intentional disclosure modal (May 28 2026 hotfix). The
  // previous direct-click path sent the AI-generated check.finding
  // as the "disclosure note" — strictly a rephrasing of the warning,
  // not a documented team decision. The modal now requires a
  // 20-character user-typed note before the endpoint fires. The
  // backend mirrors the gate (Pydantic min_length=20) so a stale
  // frontend cannot bypass it.
  const [intentModalOpen, setIntentModalOpen] = useState(false)
  const [intentNote, setIntentNote] = useState('')
  const MIN_NOTE_LEN = 20

  const openIntentModal = () => {
    setIntentNote('')
    setIntentModalOpen(true)
  }

  const submitIntent = async () => {
    if (intentNote.trim().length < MIN_NOTE_LEN) return
    setMarking(true)
    try {
      await axios.post(
        `/api/v1/qa/findings/${check.check_id}/mark-intentional`,
        { note: intentNote.trim() },
      )
      showToast(
        `${check.check_id} marked as intentional · recorded in the `
        + `audit trail`)
      setIntentModalOpen(false)
      // Tell the parent so the panel re-fetches /overrides and the
      // badge swaps to "Confirmed intentional" without a page reload.
      onIntentionalMarked?.()
    } catch {
      showToast(`Could not mark ${check.check_id} intentional — please retry.`)
    } finally {
      setMarking(false)
    }
  }

  // Real clipboard copy — no backend needed. Falls back to a no-op
  // toast if the browser blocks the clipboard write (jsdom test env
  // does not implement navigator.clipboard).
  const onCopyDisclosure = async () => {
    if (!check.disclosure_text) return
    try {
      await navigator.clipboard.writeText(check.disclosure_text)
      setCopied(true)
      if (toastTimer.current) clearTimeout(toastTimer.current)
      toastTimer.current = setTimeout(() => setCopied(false), 2000)
    } catch {
      showToast('Clipboard unavailable — copy manually from the disclosure text above.')
    }
  }

  const action = check.action_type
  if (!action) return null

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {(action === 'code_fix' || action === 'methodology_decision') && (
          <button
            type="button"
            onClick={() => void onFlagForFix()}
            disabled={flagging}
            data-testid={`qa-flag-${check.check_id}`}
            className="inline-flex items-center gap-1.5 px-2.5 py-1
                       rounded border border-warning/30 bg-warning/10
                       text-warning text-2xs font-semibold
                       hover:bg-warning/20 transition-colors
                       disabled:opacity-50 disabled:cursor-not-allowed
                       min-h-[28px]">
            <Flag className={`w-3 h-3 ${flagging ? 'animate-pulse' : ''}`} />
            {flagging ? 'Flagging…' : 'Flag for Fix'}
          </button>
        )}
        {action === 'methodology_decision' && (
          <button
            type="button"
            onClick={openIntentModal}
            disabled={marking}
            data-testid={`qa-intentional-${check.check_id}`}
            className="inline-flex items-center gap-1.5 px-2.5 py-1
                       rounded border border-success/30 bg-success/10
                       text-success text-2xs font-semibold
                       hover:bg-success/20 transition-colors
                       disabled:opacity-50 disabled:cursor-not-allowed
                       min-h-[28px]">
            <ShieldCheck className={`w-3 h-3 ${marking ? 'animate-pulse' : ''}`} />
            {marking ? 'Recording…' : 'Mark as Intentional'}
          </button>
        )}
        {action === 'disclosure_required' && check.disclosure_text && (
          <button
            type="button"
            onClick={() => void onCopyDisclosure()}
            data-testid={`qa-copy-disclosure-${check.check_id}`}
            className="inline-flex items-center gap-1.5 px-2.5 py-1
                       rounded border border-electric/30 bg-electric/10
                       text-electric text-2xs font-semibold
                       hover:bg-electric/20 transition-colors min-h-[28px]">
            {copied
              ? <ClipboardCheck className="w-3 h-3" />
              : <Clipboard className="w-3 h-3" />}
            {copied ? 'Copied' : 'Copy Disclosure Text'}
          </button>
        )}
        {action === 'rerun_required' && (
          <button
            type="button"
            onClick={onReRun}
            disabled={isReRunning}
            data-testid={`qa-rerun-${check.check_id}`}
            className="inline-flex items-center gap-1.5 px-2.5 py-1
                       rounded border border-electric/30 bg-electric/10
                       text-electric text-2xs font-semibold
                       hover:bg-electric/20 transition-colors
                       disabled:opacity-50 disabled:cursor-not-allowed
                       min-h-[28px]">
            <RefreshCw className={`w-3 h-3 ${isReRunning ? 'animate-spin' : ''}`} />
            {isReRunning ? 'Re-running…' : 'Re-run Audit'}
          </button>
        )}
      </div>
      {/* Render the disclosure text as a copyable pre-formatted block
          so the user can verify what's on the clipboard before
          pasting. Only the disclosure_required path produces this. */}
      {action === 'disclosure_required' && check.disclosure_text && (
        <pre className="text-2xs text-slate-300 leading-relaxed
                        whitespace-pre-wrap break-words bg-navy-900
                        border border-border rounded px-2.5 py-2
                        font-sans">
          {check.disclosure_text}
        </pre>
      )}
      {toast && (
        <div className="text-2xs text-muted italic" role="status">
          {toast}
        </div>
      )}

      {/* Disclosure modal — Mark as Intentional now requires a real
          team-written note. The backend's QAMarkIntentionalRequest
          enforces min_length=20 too, so a stale frontend cannot
          bypass the gate. Same modal pattern as the Run Live Demo
          confirm in QAHub.tsx. */}
      {intentModalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center
                     bg-black/60 p-4"
          onClick={() => { if (!marking) setIntentModalOpen(false) }}
          data-testid={`qa-intentional-modal-${check.check_id}`}>
          <div className="card p-5 max-w-md w-full space-y-3"
               onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-white">
              Mark as Intentional
            </h3>
            <p className="text-xs text-muted leading-relaxed">
              Document why this finding is an intentional design
              choice. This note will appear in the audit report.
            </p>
            <textarea
              data-testid={`qa-intentional-note-${check.check_id}`}
              value={intentNote}
              onChange={(e) => setIntentNote(e.target.value)}
              disabled={marking}
              rows={5}
              minLength={MIN_NOTE_LEN}
              placeholder="Describe the intentional design decision and why it is acceptable..."
              className="w-full rounded border border-border bg-navy-900
                         text-xs text-slate-200 placeholder-muted p-2.5
                         focus:outline-none focus:border-electric
                         disabled:opacity-60 disabled:cursor-not-allowed
                         leading-relaxed resize-none"
            />
            <div className="flex items-center justify-between text-2xs">
              <span className="text-muted">
                {intentNote.trim().length} / {MIN_NOTE_LEN} characters minimum
              </span>
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setIntentModalOpen(false)}
                disabled={marking}
                data-testid={`qa-intentional-cancel-${check.check_id}`}
                className="px-3 py-1.5 rounded text-xs border border-border
                           text-muted hover:text-white transition-colors
                           disabled:opacity-50 disabled:cursor-not-allowed">
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void submitIntent()}
                disabled={marking || intentNote.trim().length < MIN_NOTE_LEN}
                data-testid={`qa-intentional-confirm-${check.check_id}`}
                className="px-3 py-1.5 rounded text-xs font-medium
                           bg-success/10 border border-success/40 text-success
                           hover:bg-success/20 transition-colors
                           disabled:opacity-50 disabled:cursor-not-allowed">
                {marking ? 'Recording…' : 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

interface CheckRowProps {
  check: QACheck
  open: boolean
  onToggle: () => void
  // Commentary-mode QA narrative, loaded per audit run from the Explainer
  // Agent. Optional — when undefined (audit hasn't been explained yet, or
  // the Explainer call failed), the row falls back to evidence/fix only.
  explanation?: QAItemExplanation
  onReRun: () => void
  isReRunning: boolean
  override?: IntentionalOverride
  onIntentionalMarked?: () => void
}

function CheckRow({
  check, open, onToggle, explanation, onReRun, isReRunning,
  override, onIntentionalMarked,
}: CheckRowProps) {
  const cfg = VERDICT_CONFIG[check.status]
  const { Icon } = cfg
  return (
    <div className={`border rounded-lg overflow-hidden mb-1.5 ${cfg.border}`}>
      <button
        onClick={onToggle}
        className={`w-full flex items-center gap-3 px-3 py-2.5 text-left hover:opacity-90 transition-opacity ${cfg.bg}`}
      >
        <Icon className={`w-4 h-4 shrink-0 ${cfg.color}`} />
        <span className="font-mono text-2xs text-muted w-7 shrink-0">{check.check_id}</span>
        <span className="text-white text-xs flex-1">{check.description}</span>
        <SubmissionBadge check={check} />
        {open ? (
          <ChevronUp className="w-3.5 h-3.5 text-muted ml-1 shrink-0" />
        ) : (
          <ChevronDown className="w-3.5 h-3.5 text-muted ml-1 shrink-0" />
        )}
      </button>
      {open && (
        <div className="px-3 py-2 border-t border-border/50 bg-navy-900 space-y-2">
          {/* evidence is this check's own analysis section — the backend
              splits the QA analysis per check id, so each tile shows only
              its relevant reasoning, not the whole blob. */}
          <p className="text-slate-300 text-xs whitespace-pre-wrap leading-relaxed">
            {check.evidence}
          </p>
          {check.fix && (
            <p className="text-warning text-xs"><strong>Fix:</strong> {check.fix}</p>
          )}
          {/* Structured Finding / Implication / Action Required block —
              May 22 2026 contract. Renders only when the check carries
              structured fields (PASS sections and deterministic checks
              do not have them and the block is suppressed). When an
              intentional override exists for this check, ActionCard
              renders the "Confirmed Intentional" badge in place of
              the Action Required section. */}
          <ActionCard
            check={check}
            onReRun={onReRun}
            isReRunning={isReRunning}
            {...(override ? { override } : {})}
            {...(onIntentionalMarked ? { onIntentionalMarked } : {})}
          />
          {explanation && (
            <div className="pt-2 mt-1 border-t border-border/40 space-y-2">
              <div>
                <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">What this check tests</div>
                <p className="text-slate-300 text-xs">{explanation.what}</p>
              </div>
              <div>
                <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">Why it matters</div>
                <p className="text-slate-300 text-xs">{explanation.why}</p>
              </div>
              <div>
                <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">What a failure would mean</div>
                <p className="text-slate-300 text-xs">{explanation.failure_meaning}</p>
              </div>
              <div>
                <div className="text-2xs uppercase tracking-wide text-muted mb-0.5">How it was tested</div>
                <p className="text-slate-300 text-xs">{explanation.how_tested}</p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * CategoryAccordion — mobile-only grouped view of QA checks.
 *
 * Collapsed: a tappable header with the category name and three count
 * pills (pass / warn / fail). Tap the header to expand; expanded view
 * renders the same CheckRow each check uses on desktop. Per-category
 * open state is local — opening one category does not close another.
 */
function CategoryAccordion(
  { category, items, openChecks, onToggleCheck, explanations, onReRun, isReRunning, overrides, onIntentionalMarked }: {
    category: string
    items: QACheck[]
    openChecks: Set<string>
    onToggleCheck: (id: string) => void
    explanations: Record<string, QAItemExplanation>
    onReRun: () => void
    isReRunning: boolean
    overrides: Record<string, IntentionalOverride>
    onIntentionalMarked: () => void
  },
) {
  const [open, setOpen] = useState(false)
  const pass       = items.filter((i) => i.status === 'PASS').length
  const warn       = items.filter((i) => i.status === 'WARN').length
  const fail       = items.filter((i) => i.status === 'FAIL').length
  const incomplete = items.filter((i) => i.status === 'INCOMPLETE').length
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-left
                   bg-navy-800 hover:bg-navy-700 transition-colors
                   min-h-[44px]">
        <span className="text-white text-xs font-semibold flex-1
                         truncate">
          {category}
        </span>
        <div className="flex items-center gap-1.5 shrink-0 text-2xs
                        font-mono">
          {pass > 0 && (
            <span className="text-success">{pass}P</span>
          )}
          {warn > 0 && (
            <span className="text-warning">{warn}W</span>
          )}
          {fail > 0 && (
            <span className="text-danger">{fail}F</span>
          )}
          {incomplete > 0 && (
            <span className="text-slate-300">{incomplete}?</span>
          )}
        </div>
        {open ? (
          <ChevronUp className="w-3.5 h-3.5 text-muted shrink-0" />
        ) : (
          <ChevronDown className="w-3.5 h-3.5 text-muted shrink-0" />
        )}
      </button>
      {open && (
        <div className="p-2 bg-navy-900">
          {items.map((check) => (
            <CheckRow
              key={check.check_id}
              check={check}
              open={openChecks.has(check.check_id)}
              onToggle={() => onToggleCheck(check.check_id)}
              explanation={explanations[check.check_id]}
              onReRun={onReRun}
              isReRunning={isReRunning}
              {...(overrides[check.check_id]
                ? { override: overrides[check.check_id] } : {})}
              onIntentionalMarked={onIntentionalMarked}
            />
          ))}
        </div>
      )}
    </div>
  )
}


interface QAAuditPanelProps {
  /** May 25 2026 — when supplied, the inline "Re-run audit" button in
   *  the summary card fires a UNIFIED re-run (both the methodology
   *  checklist /api/qa/audit AND the statistical audit
   *  /api/v1/audit/run) rather than firing methodology alone.
   *  QAHub passes runFullQA here so the button matches the labelled
   *  intent — the user's complaint that "Re-run Audit" only fires
   *  one endpoint. When omitted (standalone use), the button falls
   *  back to the methodology-only qaStore.reload(). */
  onFullRerun?: () => void
  /** True while a parent-coordinated full run is in flight — drives
   *  the running-state overlay on the existing results so the user
   *  has immediate visual confirmation rather than seeing stale
   *  results with no feedback. */
  isFullRunActive?: boolean
}


export default function QAAuditPanel(
  { onFullRerun, isFullRunActive = false }: QAAuditPanelProps = {},
) {
  // Audit result lives in qaStore — survives navigation away and back.
  // load() is a no-op when loaded=true, so re-entering this tab is instant
  // and never triggers a second 10-second audit run.
  const { result: audit, loading, load, reload } = useQAStore()
  // The inline "Re-run audit" button is busy when EITHER the local
  // qaStore.reload() call is in flight (standalone use) OR the parent
  // is coordinating a full run (QAHub use). Both states mean the user
  // shouldn't be able to re-click and the button should show feedback.
  const isReRunning = loading || isFullRunActive
  // Per-audit Commentary narrative. Loaded once per audit-items array —
  // the Explainer Agent generates fresh what/why/failure/how text for
  // the checks based on their actual pass/warn/fail state in this run.
  const qaExplanations = useGlossaryStore((s) => s.qa)
  const loadQA = useGlossaryStore((s) => s.loadQA)
  const [openChecks, setOpenChecks] = useState<Set<string>>(new Set())
  const [activeCategory, setActiveCategory] = useState('ALL')
  // Intentional overrides — keyed by check_id. The QA Action Required
  // card is replaced with a "Confirmed intentional" badge for any
  // check present here. Refreshed after a successful Mark Intentional
  // POST so the badge swap is immediate (no audit re-run needed).
  const [overrides, setOverrides] = useState<Record<string, IntentionalOverride>>({})

  const loadOverrides = useCallback(async () => {
    try {
      const res = await axios.get<{
        overrides: Record<string, IntentionalOverride>
      }>('/api/v1/qa/intentional-overrides')
      setOverrides(res.data.overrides || {})
    } catch {
      // Fail-open — an unreachable endpoint just means no overrides
      // are surfaced; the action cards render normally.
      setOverrides({})
    }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => { void loadOverrides() }, [loadOverrides])

  useEffect(() => {
    if (!audit?.items?.length) return
    void loadQA(audit.items as unknown as Array<Record<string, unknown>>)
  }, [audit, loadQA])

  const toggleCheck = (id: string) => {
    setOpenChecks((prev) => {
      const next = new Set(prev)
      if (next.has(id)) { next.delete(id) } else { next.add(id) }
      return next
    })
  }

  if (!audit && !loading) return (
    <div className="p-6 text-center text-muted text-sm">
      <button onClick={() => void reload()} className="text-electric underline">Load QA Audit</button>
    </div>
  )

  if (loading && !audit) return (
    <div className="p-6 flex items-center gap-2 text-muted text-sm">
      <RefreshCw className="w-4 h-4 animate-spin" />
      Running methodology audit…
    </div>
  )

  if (!audit) return null

  const items = audit.items
  const categories = ['ALL', ...new Set(items.map((c) => c.category))]
  const filtered = activeCategory === 'ALL' ? items : items.filter((c) => c.category === activeCategory)

  const overallCfg = VERDICT_CONFIG[audit.verdict]
  const { Icon: OverallIcon } = overallCfg

  return (
    // No page chrome here — QAHub owns the page container so this panel
    // embeds cleanly as the hub's Methodology Review section.
    // Wrapped in a relative div so an in-flight re-run can overlay a
    // visible "Running…" badge + dim the existing results — the
    // user's complaint that clicking re-run leaves the page looking
    // static. data-running is also a test handle.
    <div
      className="space-y-5 relative"
      data-running={isReRunning ? 'true' : 'false'}>
      {isReRunning && (
        <div
          data-testid="qa-panel-running-overlay"
          className="absolute top-0 right-0 flex items-center gap-1.5
                     px-3 py-1.5 rounded text-xs font-medium
                     bg-electric/15 border border-electric/40
                     text-electric shadow-lg z-10">
          <RefreshCw className="w-3.5 h-3.5 animate-spin" />
          Re-running methodology checklist…
        </div>
      )}
      <div className={isReRunning
        ? 'opacity-60 pointer-events-none transition-opacity'
        : 'transition-opacity'}>
      {/* May 24 2026 — submission readiness banner. Reads
          submission_status / submission_banner / submission_counts
          from the audit response. Falls back silently when the
          fields are absent (e.g. a cached pre-May-24 audit row). */}
      <SubmissionReadinessBanner
        status={audit.submission_status}
        banner={audit.submission_banner}
        counts={audit.submission_counts}
      />
      {/* Summary card */}
      <div className={`card p-5 border ${overallCfg.border} ${overallCfg.bg}`}>
        <div className="flex items-center gap-4">
          <OverallIcon className={`w-8 h-8 ${overallCfg.color}`} />
          <div className="flex-1">
            <div className="flex items-center gap-3">
              <h2 className="text-white font-bold text-lg">QA Audit Report</h2>
              <VerdictBadge verdict={audit.verdict} />
            </div>
            <p className="text-muted text-sm mt-0.5">
              {audit.checks_total}-point methodology checklist · Sprint {audit.sprint ?? '4'} results
            </p>
          </div>
          <div className="text-right shrink-0">
            <div className="text-3xl font-mono font-bold text-white">
              {audit.checks_passed}<span className="text-muted text-xl">/{audit.checks_total}</span>
            </div>
            <div className="text-xs text-muted mt-0.5">checks passed</div>
          </div>
        </div>

        {/* Mini breakdown */}
        <div className="flex flex-wrap gap-3 mt-4 pt-4 border-t border-border/50">
          <div className="flex items-center gap-1.5">
            <CheckCircle className="w-3.5 h-3.5 text-success" />
            <span className="font-mono text-sm text-success">{audit.checks_passed}</span>
            <span className="text-muted text-xs">passed</span>
          </div>
          <div className="flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5 text-warning" />
            <span className="font-mono text-sm text-warning">{audit.checks_warned}</span>
            <span className="text-muted text-xs">warned</span>
          </div>
          <div className="flex items-center gap-1.5">
            <XCircle className="w-3.5 h-3.5 text-danger" />
            <span className="font-mono text-sm text-danger">{audit.checks_failed}</span>
            <span className="text-muted text-xs">failed</span>
          </div>
          {/* INCOMPLETE counter — rendered alongside the others when > 0
              so the user sees the audit gap rather than assuming
              completion. The May 22 2026 contract: INCOMPLETE means
              "the audit did not finish this check", not "this check
              has a concern". The verdict (the prominent badge above)
              is unaffected by INCOMPLETE — it derives only from
              FAIL / WARN / PASS counts. */}
          {(audit.checks_incomplete ?? 0) > 0 && (
            <div className="flex items-center gap-1.5"
                 data-testid="qa-summary-incomplete-count">
              <HelpCircle className="w-3.5 h-3.5 text-slate-300" />
              <span className="font-mono text-sm text-slate-300">
                {audit.checks_incomplete}
              </span>
              <span className="text-muted text-xs">incomplete</span>
            </div>
          )}
          {/* Re-run audit button (May 25 2026 hotfix):
              - Fires the UNIFIED runner (methodology + statistical audit)
                via onFullRerun when supplied by QAHub; falls back to
                methodology-only qaStore.reload() in standalone use.
              - disabled:opacity-50 + disabled:cursor-not-allowed give a
                visible disabled state during a run — previously the
                button had no visual feedback and clicks during a
                long-running audit appeared 'unresponsive'.
              - Label flips to 'Re-running…' during the run so the user
                has explicit feedback that the click was accepted. */}
          <button
            type="button"
            onClick={() => {
              if (onFullRerun) onFullRerun()
              else void reload()
            }}
            disabled={isReRunning}
            data-testid="qa-summary-rerun-button"
            className="ml-auto flex items-center gap-1.5 text-electric
                       hover:text-white text-xs font-medium transition-colors
                       disabled:opacity-50 disabled:cursor-not-allowed
                       min-h-[28px]"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isReRunning ? 'animate-spin' : ''}`} />
            {isReRunning ? 'Re-running…' : 'Re-run audit'}
          </button>
        </div>
        {/* Honest disclosure line when checks are incomplete — the
            audit is not complete and the user needs to know. Distinct
            from the FAIL/WARN messaging so a baseline-PASS audit with
            incompletes is not misrepresented as "ready". */}
        {(audit.checks_incomplete ?? 0) > 0 && (
          <p
            data-testid="qa-summary-incomplete-notice"
            className="text-2xs text-slate-300 mt-3 leading-relaxed">
            {audit.checks_incomplete} check
            {audit.checks_incomplete === 1 ? '' : 's'} incomplete —
            re-run to complete analysis.
          </p>
        )}
      </div>

      {/* Category filter — sm: and up only. Below sm: the checklist
          renders as a grouped accordion (see the mobile block below). */}
      <div className="hidden sm:flex gap-1.5 flex-wrap">
        {categories.map((cat) => (
          <button
            key={cat}
            onClick={() => setActiveCategory(cat)}
            className={`text-xs px-2.5 py-1 rounded border transition-colors ${
              activeCategory === cat
                ? 'border-electric bg-electric/10 text-electric'
                : 'border-border text-muted hover:text-white hover:border-border/80'
            }`}
          >
            {cat}
          </button>
        ))}
      </div>

      {/* Checklist — desktop flat list. */}
      <div className="hidden sm:block">
        {filtered.map((check) => (
          <CheckRow
            key={check.check_id}
            check={check}
            open={openChecks.has(check.check_id)}
            onToggle={() => toggleCheck(check.check_id)}
            explanation={qaExplanations[check.check_id]}
            onReRun={() => void reload()}
            isReRunning={loading}
            {...(overrides[check.check_id]
              ? { override: overrides[check.check_id] } : {})}
            onIntentionalMarked={() => void loadOverrides()}
          />
        ))}
      </div>

      {/* Checklist — mobile (below sm:) grouped accordion view. Each
          category becomes a collapsible section; the header carries
          the pass/warn/fail count badges for that group. Tapping the
          header expands the group to the same CheckRow set used on
          desktop. Keeps the QA checklist scannable on a narrow phone
          where 39 checks in a flat list would require a long scroll. */}
      <div className="sm:hidden space-y-2">
        {categories
          .filter((c) => c !== 'ALL')
          .map((cat) => (
            <CategoryAccordion
              key={cat}
              category={cat}
              items={items.filter((c) => c.category === cat)}
              openChecks={openChecks}
              onToggleCheck={toggleCheck}
              explanations={qaExplanations}
              onReRun={() => void reload()}
              isReRunning={loading}
              overrides={overrides}
              onIntentionalMarked={() => void loadOverrides()}
            />
          ))}
      </div>

      {/* Legend — May 22 2026 update: INCOMPLETE is a fourth verdict
          alongside PASS / WARN / FAIL. The copy is deliberate — it
          says "the audit did not finish this check", NOT "this check
          has a concern", so a row of INCOMPLETE badges reads as an
          audit-completeness signal rather than a quality concern. */}
      <div className="card p-4">
        <div className="section-header mb-3">Verdict Definitions</div>
        <div className="space-y-2">
          {([
            { v: 'PASS'       as Verdict, d: 'Methodology is sound on this dimension.' },
            { v: 'WARN'       as Verdict, d: 'A specific, nameable concern was found — the agent examined the data. Should be addressed or explicitly disclosed.' },
            { v: 'FAIL'       as Verdict, d: 'A clear violation was found that invalidates the analysis. Must be fixed before presenting.' },
            { v: 'INCOMPLETE' as Verdict, d: 'The agent could not examine the data for this check. Re-run the audit to generate a full report. This is NOT a quality concern — it is an audit-completeness signal.' },
          ]).map(({ v, d }) => (
            <div key={v} className="flex items-start gap-2">
              <VerdictBadge verdict={v} />
              <span className="text-muted text-xs">{d}</span>
            </div>
          ))}
        </div>
      </div>
      </div>{/* /isReRunning opacity wrapper */}
    </div>
  )
}
