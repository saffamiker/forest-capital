import { useState, useEffect, useRef } from 'react'
import {
  CheckCircle, XCircle, AlertTriangle, ChevronDown, ChevronUp, RefreshCw,
  HelpCircle, Flag, ShieldCheck, Clipboard, ClipboardCheck,
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
function ActionCard({
  check, onReRun, isReRunning,
}: {
  check: QACheck
  onReRun: () => void
  isReRunning: boolean
}) {
  const action = check.action_type
  const hasStructured = !!(
    check.finding || check.implication || check.remediation
    || check.disclosure_text || action
  )
  if (!hasStructured) return null

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
          />
        </div>
      )}
    </div>
  )
}

function ActionButtons({
  check, onReRun, isReRunning,
}: {
  check: QACheck
  onReRun: () => void
  isReRunning: boolean
}) {
  const [toast, setToast] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 2-second auto-dismiss so the toast doesn't linger after the user
  // moves on. Cleared on unmount to prevent a memory leak.
  useEffect(() => () => {
    if (toastTimer.current) clearTimeout(toastTimer.current)
  }, [])

  const showToast = (msg: string) => {
    setToast(msg)
    if (toastTimer.current) clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), 2500)
  }

  // Stubbed for this commit — the POST /api/v1/qa/findings/{check_id}/
  // flag-for-fix endpoint and the qa_intentional_overrides table land
  // in subsequent commits. The toasts surface the affordance + placement
  // so testers can review the card layout now.
  const onFlagForFix = () => {
    showToast(
      `TODO — flag-for-fix endpoint not yet wired. Will create a `
      + `triage item for ${check.check_id} on the next commit.`)
  }
  const onMarkIntentional = () => {
    showToast(
      `TODO — mark-as-intentional not yet wired. Will record the `
      + `override in qa_intentional_overrides on the next commit.`)
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
            onClick={onFlagForFix}
            data-testid={`qa-flag-${check.check_id}`}
            className="inline-flex items-center gap-1.5 px-2.5 py-1
                       rounded border border-warning/30 bg-warning/10
                       text-warning text-2xs font-semibold
                       hover:bg-warning/20 transition-colors min-h-[28px]">
            <Flag className="w-3 h-3" />
            Flag for Fix
          </button>
        )}
        {action === 'methodology_decision' && (
          <button
            type="button"
            onClick={onMarkIntentional}
            data-testid={`qa-intentional-${check.check_id}`}
            className="inline-flex items-center gap-1.5 px-2.5 py-1
                       rounded border border-success/30 bg-success/10
                       text-success text-2xs font-semibold
                       hover:bg-success/20 transition-colors min-h-[28px]">
            <ShieldCheck className="w-3 h-3" />
            Mark as Intentional
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
}

function CheckRow({ check, open, onToggle, explanation, onReRun, isReRunning }: CheckRowProps) {
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
        <VerdictBadge verdict={check.status} />
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
              do not have them and the block is suppressed). */}
          <ActionCard
            check={check}
            onReRun={onReRun}
            isReRunning={isReRunning}
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
  { category, items, openChecks, onToggleCheck, explanations, onReRun, isReRunning }: {
    category: string
    items: QACheck[]
    openChecks: Set<string>
    onToggleCheck: (id: string) => void
    explanations: Record<string, QAItemExplanation>
    onReRun: () => void
    isReRunning: boolean
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
            />
          ))}
        </div>
      )}
    </div>
  )
}


export default function QAAuditPanel() {
  // Audit result lives in qaStore — survives navigation away and back.
  // load() is a no-op when loaded=true, so re-entering this tab is instant
  // and never triggers a second 10-second audit run.
  const { result: audit, loading, load, reload } = useQAStore()
  // Per-audit Commentary narrative. Loaded once per audit-items array —
  // the Explainer Agent generates fresh what/why/failure/how text for
  // the checks based on their actual pass/warn/fail state in this run.
  const qaExplanations = useGlossaryStore((s) => s.qa)
  const loadQA = useGlossaryStore((s) => s.loadQA)
  const [openChecks, setOpenChecks] = useState<Set<string>>(new Set())
  const [activeCategory, setActiveCategory] = useState('ALL')

  useEffect(() => { void load() }, [load])

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
    <div className="space-y-5">
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
          <button
            onClick={() => void reload()}
            disabled={loading}
            className="ml-auto flex items-center gap-1.5 text-muted hover:text-white text-xs transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            Re-run audit
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
    </div>
  )
}
