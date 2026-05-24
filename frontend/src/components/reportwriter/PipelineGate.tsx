/**
 * frontend/src/components/reportwriter/PipelineGate.tsx
 *
 * Interactive eleven-step pipeline. Each step is its own row with a
 * Run button (or "Auto" badge for the two automatic steps), a status
 * pill, and a result panel that renders the response payload inline
 * once the step has run.
 *
 * Replaces the read-only PipelineSteps display from Commit 3 — the
 * spec requires every step to be independently runnable so Bob can
 * verify each gate before generation.
 *
 * Dependency contract (enforced by `gatedDisabledReason`):
 *
 *   Step 1  Stage Findings        — unconditional
 *   Step 2  Source Citations      — requires Step 1
 *   Step 3  Pull Team Activity    — requires Step 1
 *   Step 4  Pull Validation Data  — requires Step 1
 *   Step 5  Cross-Reference Check — AUTO; fires when Steps 1-4 done
 *   Step 6  Thesis Validation     — AUTO; fires when Step 5 done
 *   Step 7  Generate Draft        — requires Steps 1-4 + Step 5 amber
 *                                   or green + Step 6 green
 *   Step 8  Review and Edit Draft — driven by the editor itself
 *   Step 9  Run Final Check       — handled by ReportWriter.tsx
 *   Step 10 Run Academic Review   — handled by ReportWriter.tsx
 *   Step 11 Download              — handled by ReportWriter.tsx
 *
 * The component only owns Steps 1-7; Steps 8-11 stay on the editor
 * panel below (handled by the existing buttons in ReportWriter.tsx).
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CheckCircle, AlertCircle, Loader2, Play, Circle,
  ExternalLink, Search, X, ShieldCheck, Lock,
} from 'lucide-react'

export type StepStatus =
  | 'idle'
  | 'running'
  | 'complete'
  | 'warning'
  | 'failed'

export interface StepResult {
  status: StepStatus
  message: string
  detail?: string | undefined
  payload?: unknown
}

export type StepResults = Partial<Record<number, StepResult>>

interface Props {
  results: StepResults
  generating: boolean
  generateDisabledReason: string | null
  onRunStep: (stepNumber: number) => Promise<void>
  onGenerate: () => Promise<void>
  /** May 24 2026 — Step 2b is now a first-class pipeline step
   *  between Step 2 and Step 3. The ReportWriter computes the
   *  citation adjudication state (count of untrusted citations
   *  remaining + a jump handler) and passes both in. */
  step2b?: {
    untrustedCount: number
    onJump: () => void
    /** True while the parent-controlled citation fetch the
     *  onJump handler kicked off is still in flight. Drives the
     *  Open Review button's "Loading…" feedback. UAT 2026-05-24. */
    loading?: boolean
  }
}


// May 24 2026 — Step 2b sits between 2 and 3 visually, but is keyed
// as 2.5 in the internal results so the rest of the gating logic
// stays in integer-step land. The pipeline rendering treats 2.5
// like any other row but skips the Run button (Step 2b is satisfied
// by adjudicating citations in the Citation Review panel, not by
// firing an endpoint).
const STEP_2B_KEY = 2.5

const STEP_LABELS: Record<number, string> = {
  1: 'Stage Findings',
  2: 'Source Citations',
  [STEP_2B_KEY]: 'Adjudicate Citations',
  3: 'Pull Team Activity',
  4: 'Pull Validation Data',
  5: 'Cross-Reference Check',
  6: 'Thesis Validation',
  7: 'Generate Draft',
}

const AUTO_STEPS = new Set<number>([5, 6])


export default function PipelineGate({
  results, generating, generateDisabledReason,
  onRunStep, onGenerate, step2b,
}: Props) {
  // May 24 2026 — Synthesise Step 2b's result from the adjudication
  // state passed in. 2b is satisfied when 0 untrusted citations
  // remain (status 'complete'); it shows 'warning' while citations
  // still need a decision and 'idle' before Step 2 has run.
  const step2bResult: StepResult | undefined =
    step2b !== undefined && _stepPassed(results, 2)
      ? (step2b.untrustedCount === 0
          ? {
              status: 'complete',
              message: 'All citations adjudicated.',
            }
          : {
              status: 'warning',
              message: `${step2b.untrustedCount} citation`
                + `${step2b.untrustedCount === 1 ? '' : 's'}`
                + ' still need a decision.',
              detail: 'Open the Citation Review panel and Accept,'
                + ' Reject, or Manually add each one.',
            })
      : undefined

  return (
    <section
      data-testid="pipeline-gate"
      className="bg-navy-900 border border-navy-700 rounded p-3 space-y-2">
      <header className="flex items-center justify-between">
        <h3 className="text-white font-medium text-sm">
          Generation pipeline
        </h3>
        <span className="text-text-muted text-2xs">
          Click Run on each step. Steps 5–6 fire automatically.
        </span>
      </header>
      <ol className="space-y-2">
        {[1, 2, STEP_2B_KEY, 3, 4, 5, 6, 7].map((n) => {
          if (n === STEP_2B_KEY) {
            // Step 2b is a citation-adjudication gate, not a
            // Run-button step. It renders without a Run button;
            // its action is the "Open Citation Review" link
            // which scrolls to the adjudication panel.
            return (
              <Step2bRow
                key="2b"
                result={step2bResult}
                untrustedCount={step2b?.untrustedCount ?? 0}
                onJump={step2b?.onJump ?? (() => {})}
                loading={step2b?.loading ?? false}
                disabledReason={
                  _stepPassed(results, 2)
                    ? null : 'Run Step 2 first'
                }
              />
            )
          }
          return (
            <StepRow
              key={n}
              number={n}
              label={STEP_LABELS[n]}
              result={results[n]}
              disabledReason={
                n === 7 ? generateDisabledReason
                : disabledReasonFor(n, results, step2b)
              }
              isAuto={AUTO_STEPS.has(n)}
              isGenerating={generating}
              onRun={
                n === 7
                  ? onGenerate
                  : () => onRunStep(n)
              }
            />
          )
        })}
      </ol>
    </section>
  )
}


function disabledReasonFor(
  step: number, results: StepResults,
  step2b?: { untrustedCount: number; onJump: () => void },
): string | null {
  // May 24 2026 RW3 strict-sequential — Steps 2 → 3 → 4 unlock in
  // order. Each one gates on the IMMEDIATELY PRIOR step having
  // passed, not just Step 1. The previous version gated all of
  // 2/3/4 on Step 1, which let Bob run them in any order and
  // surfaced the "all steps fire when Step 1 completes" bug the
  // user reported.
  //
  // A step "passes" the gate when its status is 'complete' OR
  // 'warning' AND it carries no _no_audit bypass flag. Restored-
  // from-cache states with one of those statuses count too — the
  // status reflects what was true on the previous run.
  if (step === 1) return null
  if (step === 2) {
    if (!_stepPassed(results, 1)) return 'Run Step 1 first'
    return null
  }
  if (step === 3) {
    if (!_stepPassed(results, 1)) return 'Run Step 1 first'
    if (!_stepPassed(results, 2)) return 'Run Step 2 first'
    // Step 2b — citation adjudication MUST be complete before
    // Step 3 unlocks. The Citation Review panel surfaces every
    // untrusted citation with Accept/Reject/Manual-add controls;
    // Step 2b is satisfied when zero untrusted remain.
    if (step2b !== undefined && step2b.untrustedCount > 0) {
      return `Adjudicate ${step2b.untrustedCount} citation`
        + `${step2b.untrustedCount === 1 ? '' : 's'} (Step 2b)`
    }
    return null
  }
  if (step === 4) {
    if (!_stepPassed(results, 1)) return 'Run Step 1 first'
    if (!_stepPassed(results, 2)) return 'Run Step 2 first'
    if (step2b !== undefined && step2b.untrustedCount > 0) {
      return `Adjudicate ${step2b.untrustedCount} citation`
        + `${step2b.untrustedCount === 1 ? '' : 's'} (Step 2b)`
    }
    if (!_stepPassed(results, 3)) return 'Run Step 3 first'
    return null
  }
  // Auto steps cannot be clicked manually.
  if (step === 5 || step === 6) return 'Runs automatically'
  return null
}


/**
 * Step2bRow — citation adjudication gate. Renders as a pipeline row
 * but instead of a Run button it shows an "Open Citation Review"
 * link that scrolls to the adjudication panel. Status is derived
 * from the count of untrusted citations remaining: 0 → complete,
 * > 0 → warning, before Step 2 runs → idle/locked.
 */
function Step2bRow({
  result, untrustedCount, onJump, disabledReason, loading = false,
}: {
  result?: StepResult | undefined
  untrustedCount: number
  onJump: () => void
  disabledReason: string | null
  // True while the citation fetch the button kicked off is still in
  // flight. UAT (May 24 2026): the citation fetch can take seconds
  // on a cold cache, and the prior button gave no feedback during
  // the wait — testers clicked it repeatedly thinking the click had
  // not registered. The button now shows "Loading…" + a spinner +
  // disables itself while loading is true.
  loading?: boolean
}) {
  const status = result?.status ?? 'idle'
  const locked = !!disabledReason
  let Icon = Circle
  let iconCls = 'text-text-muted'
  if (status === 'complete') { Icon = CheckCircle; iconCls = 'text-green-400' }
  else if (status === 'warning') { Icon = AlertCircle; iconCls = 'text-amber-400' }
  return (
    <li
      data-testid="pipeline-step-2b"
      className={
        'border border-navy-700 rounded p-2 '
        + (status === 'complete' ? 'bg-green-500/5 ' : '')
        + (status === 'warning' ? 'bg-amber-500/5 ' : '')
      }>
      <div className="flex items-center gap-2">
        {locked ? (
          <Lock
            className="w-4 h-4 flex-shrink-0 text-text-muted"
            data-testid="pipeline-step-2b-locked"
            aria-label={`Step 2b locked — ${disabledReason}`}
          />
        ) : (
          <Icon className={`w-4 h-4 flex-shrink-0 ${iconCls}`} />
        )}
        <span className="text-text-secondary text-xs flex-1">
          <span className="text-text-muted">2b.</span>{' '}
          Adjudicate Citations
          {locked ? (
            <span className="ml-1.5 text-2xs text-text-muted italic">
              · {disabledReason}
            </span>
          ) : status === 'warning' ? (
            <span className="ml-1.5 text-2xs text-amber-300">
              · {untrustedCount} untrusted
            </span>
          ) : null}
        </span>
        {!locked ? (
          <button
            type="button"
            onClick={onJump}
            data-testid="pipeline-step-2b-button"
            disabled={status === 'complete' || loading}
            aria-busy={loading}
            className={
              'inline-flex items-center gap-1 px-2.5 py-1 ' +
              (status === 'complete'
                ? 'bg-navy-700 text-text-muted cursor-default'
                : loading
                  ? 'bg-amber-500/20 text-amber-200/80 cursor-wait'
                  : 'bg-amber-500/30 hover:bg-amber-500/40 text-amber-100') +
              ' text-2xs font-medium rounded transition-colors'
            }>
            {loading ? (
              <>
                <Loader2
                  className="w-3 h-3 animate-spin"
                  aria-hidden="true" />
                <span>Loading…</span>
              </>
            ) : status === 'complete' ? 'Done' : 'Open Review'}
          </button>
        ) : null}
      </div>
      {result?.message ? (
        <p className={
          'mt-1.5 pl-6 text-2xs ' + (
            status === 'warning' ? 'text-amber-300' : 'text-text-secondary'
          )
        }>
          {result.message}
        </p>
      ) : null}
      {result?.detail ? (
        <p className="mt-0.5 pl-6 text-2xs text-text-muted italic">
          {result.detail}
        </p>
      ) : null}
    </li>
  )
}


function _stepPassed(results: StepResults, n: number): boolean {
  // A step has "passed" the gate when its status is 'complete' OR
  // 'warning' AND the payload does NOT carry a `_bypass_*` flag.
  // _no_audit (Step 4 specifically) is the canonical bypass flag —
  // a Step 4 in warning state with _no_audit: true does NOT count
  // as passed for the purpose of gating Step 7. Restored-from-
  // cache counts as passed (the status reflects the cache state).
  const r = results[n]
  if (!r) return false
  if (r.status !== 'complete' && r.status !== 'warning') return false
  const payload = (r.payload as Record<string, unknown> | undefined) || {}
  if (payload['_no_audit'] === true) return false
  return true
}


function StepRow({
  number, label, result, disabledReason, isAuto, isGenerating, onRun,
}: {
  number: number
  label: string
  result?: StepResult | undefined
  disabledReason: string | null
  isAuto: boolean
  isGenerating: boolean
  onRun: () => Promise<void>
}) {
  const [running, setRunning] = useState(false)
  const status = result?.status ?? 'idle'
  const disabled = (
    !!disabledReason
    || isAuto
    || running
    || status === 'running'
    || (number === 7 && isGenerating)
  )

  const handle = useCallback(async () => {
    if (disabled || isAuto) return
    setRunning(true)
    try { await onRun() } finally { setRunning(false) }
  }, [onRun, disabled, isAuto])

  let Icon = Circle
  let iconCls = 'text-text-muted'
  if (status === 'complete') { Icon = CheckCircle; iconCls = 'text-green-400' }
  else if (status === 'running') { Icon = Loader2; iconCls = 'text-electric-blue animate-spin' }
  else if (status === 'warning') { Icon = AlertCircle; iconCls = 'text-amber-400' }
  else if (status === 'failed') { Icon = AlertCircle; iconCls = 'text-red-400' }

  // May 24 2026 RW3 — show a visual locked indicator when the step
  // is disabled BECAUSE of an upstream dependency (not because it
  // is itself running, auto, or generating). The user reads the
  // lock icon as "this step is gated, click on the indicated
  // upstream step first".
  const isLockedByDependency = !!disabledReason
    && !isAuto && !running && status !== 'running'
    && !(number === 7 && isGenerating)
  return (
    <li
      data-testid={`pipeline-step-${number}`}
      className={
        'border border-navy-700 rounded p-2 '
        + (status === 'complete' ? 'bg-green-500/5 ' : '')
        + (status === 'warning' ? 'bg-amber-500/5 ' : '')
        + (status === 'failed' ? 'bg-red-500/5 ' : '')
      }>
      <div className="flex items-center gap-2">
        {isLockedByDependency ? (
          <Lock
            className="w-4 h-4 flex-shrink-0 text-text-muted"
            data-testid={`pipeline-step-${number}-locked`}
            aria-label={`Step ${number} locked — ${disabledReason}`}
          />
        ) : (
          <Icon className={`w-4 h-4 flex-shrink-0 ${iconCls}`} />
        )}
        <span className="text-text-secondary text-xs flex-1">
          <span className="text-text-muted">{number}.</span>{' '}
          {label}
          {isLockedByDependency ? (
            <span className="ml-1.5 text-2xs text-text-muted italic">
              · {disabledReason}
            </span>
          ) : null}
        </span>
        {isAuto ? (
          <span className={
            'px-2 py-0.5 text-2xs rounded ' +
            'bg-navy-800 text-text-muted border border-navy-700'
          }>
            Auto
          </span>
        ) : (
          <button
            type="button"
            disabled={disabled}
            onClick={handle}
            data-testid={`pipeline-step-${number}-button`}
            title={disabledReason || undefined}
            className={
              'inline-flex items-center gap-1 px-2.5 py-1 ' +
              'bg-electric-blue hover:bg-electric-blue/80 ' +
              'disabled:bg-navy-700 disabled:text-text-muted ' +
              'text-white text-2xs font-medium rounded transition-colors'
            }>
            {(running || status === 'running') ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Play className="w-3 h-3" />
            )}
            {status === 'complete' ? 'Re-run' : (
              number === 7 ? 'Generate Draft' : 'Run'
            )}
          </button>
        )}
      </div>
      {result?.message ? (
        <p className={
          'mt-1.5 pl-6 text-2xs ' + (
            status === 'failed' ? 'text-red-400' :
            status === 'warning' ? 'text-amber-300' :
            'text-text-secondary'
          )
        }>
          {result.message}
        </p>
      ) : null}
      {result?.detail ? (
        <p className="mt-0.5 pl-6 text-2xs text-text-muted italic">
          {result.detail}
        </p>
      ) : null}
      {/* Inline detail expansion. Renders only when the step has a
          terminal status (complete / warning / failed) AND its
          payload carries something to show. Click the View details
          chevron to expand; click again to collapse. */}
      {result && (status === 'complete' || status === 'warning'
        || status === 'failed') && _hasDetail(number, result) ? (
        <StepDetailToggle number={number} result={result} />
      ) : null}
    </li>
  )
}


function _hasDetail(n: number, result: StepResult): boolean {
  const p = (result.payload as Record<string, unknown> | undefined) || {}
  switch (n) {
    case 1: return Array.isArray(p['findings']) ||
                   typeof p['strategy_count'] === 'number'
    case 2: return typeof p['citations'] === 'object' && p['citations'] !== null
    case 3: return typeof p['activity'] === 'object' && p['activity'] !== null
    case 4: return Boolean(p['statistical_status']) ||
                   Boolean(p['layer1_status']) ||
                   Boolean(p['_no_audit'])
    case 5: return Array.isArray(p['flags']) ||
                   typeof p['mismatch_count'] === 'number'
    case 6: return Array.isArray(p['conditions'])
    case 7: return typeof p['generation_id'] === 'number'
    default: return false
  }
}


/**
 * StepDetailToggle — opens the StepDetailModal for a step.
 *
 * Replaces the prior inline expansion (which truncated tables to
 * fit the sidebar's narrow column). The modal gives the detail
 * tables full width and lets the reviewer keep the editor pane
 * visible behind a transparent backdrop while reading the detail.
 *
 * The button is also still labelled "View details" so users with
 * muscle memory from the inline expansion find it in the same
 * place. The chevron is dropped because the affordance is now a
 * modal-open, not a collapse.
 */
function StepDetailToggle({
  number, result, label,
}: { number: number; result: StepResult; label?: string | undefined }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-1.5 pl-6">
      <button
        type="button"
        onClick={() => setOpen(true)}
        data-testid={`pipeline-step-${number}-expand`}
        className={
          'inline-flex items-center gap-1 px-2 py-0.5 ' +
          'text-2xs text-electric-blue hover:text-electric-blue/80'
        }>
        <Search className="w-3 h-3" />
        View details
      </button>
      {open ? (
        <StepDetailModal
          number={number}
          result={result}
          label={label}
          onClose={() => setOpen(false)}
        />
      ) : null}
    </div>
  )
}


/**
 * StepDetailModal — full-width inspection of a pipeline step's
 * payload. Renders the same Step{1..6}Detail components the inline
 * expansion used, but gives them room to breathe — tables show full
 * widths instead of truncating to fit the sidebar.
 *
 * Backdrop click + Escape close. The modal is single-column on
 * mobile (overlay covers the screen) and a centred card from sm:
 * up. Pinned header carries the step number + name + a status
 * pill; pinned footer carries the elapsed time.
 *
 * The inner Step{1..6}Detail render is wrapped in an
 * overflow-x-auto container so a wide table scrolls inside the
 * modal rather than expanding the modal itself past the viewport.
 */
function StepDetailModal({
  number, result, label, onClose,
}: {
  number: number
  result: StepResult
  label?: string | undefined
  onClose: () => void
}) {
  // Escape closes the modal.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const status = result.status
  const ms = (result.payload && typeof result.payload === 'object'
    ? (result.payload as Record<string, unknown>)['_ms']
    : undefined) as number | undefined

  const statusKind: 'green' | 'amber' | 'red' | 'info' =
    status === 'complete' ? 'green' :
    status === 'warning'  ? 'amber' :
    status === 'failed'   ? 'red'   :
    'info'

  return (
    <div
      data-testid={`pipeline-step-${number}-modal`}
      role="presentation"
      onClick={onClose}
      className="fixed inset-0 z-[80] flex items-center justify-center
                 bg-black/60 p-0 sm:p-4">
      <div
        role="dialog"
        aria-label={`Step ${number} details`}
        onClick={(e) => e.stopPropagation()}
        className="w-full h-full flex flex-col bg-navy-900 shadow-2xl
                   sm:h-auto sm:max-h-[85vh] sm:max-w-4xl
                   sm:rounded-lg sm:border sm:border-border">
        {/* Header — step number, name, status pill, close X */}
        <header className="flex items-start justify-between gap-2
                            px-4 py-3 border-b border-navy-700 shrink-0">
          <div className="min-w-0">
            <p className="text-2xs uppercase tracking-wider text-text-muted">
              Pipeline step {number}
            </p>
            <h2 className="text-sm font-semibold text-text-primary">
              {label ?? _defaultStepLabel(number)}
            </h2>
          </div>
          <div className="flex items-center gap-2">
            <Pill text={status} kind={statusKind} />
            <button
              type="button"
              onClick={onClose}
              data-testid={`pipeline-step-${number}-modal-close`}
              aria-label="Close details"
              className="text-text-muted hover:text-text-primary
                         transition-colors p-1">
              <X className="w-4 h-4" />
            </button>
          </div>
        </header>

        {/* Body — the existing per-step detail component, but with
            room to breathe. overflow-x-auto so wide tables scroll
            within the modal rather than expanding it. */}
        <div
          data-testid={`pipeline-step-${number}-detail`}
          className="flex-1 overflow-y-auto p-4
                     text-xs text-text-secondary">
          <div className="overflow-x-auto">
            {_renderDetail(number, result)}
          </div>
        </div>

        {/* Footer — elapsed time on the right; help cue on the left */}
        <footer className="flex items-center justify-between gap-2
                            px-4 py-2 border-t border-navy-700 shrink-0
                            text-2xs text-text-muted">
          <span>Press Esc to close</span>
          {ms !== undefined ? (
            <span>elapsed {Math.round(ms)} ms</span>
          ) : null}
        </footer>
      </div>
    </div>
  )
}


function _defaultStepLabel(n: number): string {
  return ({
    1: 'Stage Findings',
    2: 'Source Citations',
    3: 'Pull Team Activity',
    4: 'Pull Validation Data',
    5: 'Cross-Reference Check',
    6: 'Thesis Validation',
    7: 'Generate Draft',
  } as Record<number, string>)[n] ?? `Step ${n}`
}


// ── Per-step detail renderers ──────────────────────────────────────────────


function _renderDetail(n: number, result: StepResult): JSX.Element {
  const p = (result.payload as Record<string, unknown> | undefined) || {}
  switch (n) {
    case 1: return <Step1Detail payload={p} />
    case 2: return <Step2Detail payload={p} />
    case 3: return <Step3Detail payload={p} />
    case 4: return <Step4Detail payload={p} />
    case 5: return <Step5Detail payload={p} />
    case 6: return <Step6Detail payload={p} />
    default: return <pre className="whitespace-pre-wrap">
      {JSON.stringify(p, null, 2)}
    </pre>
  }
}


function Step1Detail({ payload }: { payload: Record<string, unknown> }) {
  const findings = (payload['findings'] as Array<Record<string, unknown>>) || []
  const total = payload['strategy_count'] ?? '—'
  const surprises = payload['surprise_count'] ?? 0
  const high = payload['high_strength_count']
            ?? payload['n_high_strength']
            ?? 0
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2 text-2xs">
        <Pill text={`${total} strategies`} kind="info" />
        <Pill text={`${high} HIGH`} kind="green" />
        <Pill text={`${surprises} surprises`} kind="amber" />
      </div>
      {findings.length > 0 ? (
        <table className="w-full text-2xs">
          <thead className="text-text-muted text-left">
            <tr>
              <th className="py-0.5 pr-2">#</th>
              <th className="py-0.5 pr-2">Title</th>
              <th className="py-0.5 pr-2">Strength</th>
              <th className="py-0.5">Finding</th>
            </tr>
          </thead>
          <tbody>
            {findings.slice(0, 11).map((f, i) => (
              <tr key={i} className="border-t border-navy-800">
                <td className="py-0.5 pr-2 text-text-muted">{i + 1}</td>
                <td className="py-0.5 pr-2">{String(f['title'] ?? '—')}</td>
                <td className="py-0.5 pr-2">
                  <StrengthBadge value={String(f['nugget_strength'] ?? 'LOW')} />
                </td>
                <td className="py-0.5 truncate max-w-[40ch]">
                  {String(f['finding'] ?? '—')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="italic text-text-muted">
          No findings detail in payload.
        </p>
      )}
    </div>
  )
}


function Step2Detail({ payload }: { payload: Record<string, unknown> }) {
  const citations = (payload['citations'] as Record<string, Record<string, unknown>>) || {}
  const verified = payload['verified_count'] ?? 0
  const total = payload['concept_count'] ?? Object.keys(citations).length
  const quality = String(payload['quality'] ?? 'red')
  const entries = Object.entries(citations)
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2 text-2xs">
        <Pill text={`${verified} verified`} kind="green" />
        <Pill text={`${Number(total) - Number(verified)} need action`}
              kind="amber" />
        <Pill text={`Quality: ${quality}`} kind={
          quality === 'green' ? 'green' :
          quality === 'amber' ? 'amber' : 'red'
        } />
      </div>
      {entries.length > 0 ? (
        <table className="w-full text-2xs">
          <thead className="text-text-muted text-left">
            <tr>
              <th className="py-0.5 pr-2">Concept</th>
              <th className="py-0.5 pr-2">Status</th>
              <th className="py-0.5 pr-2">Source</th>
              <th className="py-0.5">URL</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([cid, c]) => (
              <tr key={cid} className="border-t border-navy-800">
                <td className="py-0.5 pr-2 font-mono">{cid}</td>
                <td className="py-0.5 pr-2">
                  <VerificationBadge
                    state={String(c['verification_status'] ?? 'not_found')} />
                </td>
                <td className="py-0.5 pr-2 truncate max-w-[28ch]">
                  {c['author']
                    ? `${String(c['author'])}, ${String(c['year'] ?? '—')}`
                    : <em className="text-text-muted">{
                        String(c['search_query_used'] || '—')
                      }</em>}
                </td>
                <td className="py-0.5">
                  {c['url'] ? (
                    <a
                      href={String(c['url'])}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-electric-blue hover:underline inline-flex items-center gap-1">
                      <ExternalLink className="w-3 h-3" />
                      link
                    </a>
                  ) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="italic text-text-muted">
          No citation cache in payload.
        </p>
      )}
      <p className="text-2xs text-text-muted italic mt-2">
        {Number(total) - Number(verified)} concept
        {Number(total) - Number(verified) === 1 ? '' : 's'} still
        need review. The 3-pass search has already captured
        alternatives where it could; the pipeline continues with
        the current cache. After Step 7 generates the draft, the
        Citation Review panel appears in the editor sidebar and
        lets you accept / reject / replace each unverified
        citation before the final check.
      </p>
    </div>
  )
}


function Step3Detail({ payload }: { payload: Record<string, unknown> }) {
  const activity = (payload['activity'] as Record<string, number>) || {}
  const flags = (payload['cross_check_flags'] as string[]) || []
  // Group activity counts by member prefix.
  const groups: Record<string, Array<[string, number]>> = {
    Michael: [], Bob: [], Molly: [], Platform: [],
  }
  for (const [k, v] of Object.entries(activity)) {
    if (k.startsWith('michael_'))      groups.Michael.push([k, v])
    else if (k.startsWith('bob_'))     groups.Bob.push([k, v])
    else if (k.startsWith('molly_'))   groups.Molly.push([k, v])
    else if (k.startsWith('team_total_')) groups.Platform.push([k, v])
  }
  const empty = Object.keys(activity).length === 0
  return (
    <div className="space-y-2">
      {empty ? (
        <p className="italic text-text-muted">
          No activity rows in payload — likely a fresh deployment with
          no UAT or council activity yet recorded.
        </p>
      ) : null}
      {!empty ? (
        <table className="w-full text-2xs">
          <thead className="text-text-muted text-left">
            <tr>
              <th className="py-0.5 pr-2">Member / Total</th>
              <th className="py-0.5 pr-2">Activity</th>
              <th className="py-0.5 text-right">Count</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(groups).flatMap(([member, rows]) =>
              rows.map(([k, v]) => (
                <tr key={`${member}-${k}`} className="border-t border-navy-800">
                  <td className="py-0.5 pr-2">{member}</td>
                  <td className="py-0.5 pr-2 font-mono text-text-muted">
                    {k.replace(/^(michael_|bob_|molly_|team_total_)/, '')
                      .replace(/_/g, ' ')}
                  </td>
                  <td className="py-0.5 text-right font-mono">{v}</td>
                </tr>
              )))}
          </tbody>
        </table>
      ) : null}
      {flags.length > 0 ? (
        <div className="p-1.5 bg-amber-500/10 border border-amber-500/30 rounded">
          <p className="text-amber-300 font-medium mb-0.5">
            Cross-check flags:
          </p>
          {flags.map((f, i) => (
            <p key={i} className="text-amber-100/80">• {f}</p>
          ))}
        </div>
      ) : null}
    </div>
  )
}


function Step4Detail({ payload }: { payload: Record<string, unknown> }) {
  const navigate = useNavigate()
  if (payload['_no_audit']) {
    // May 24 2026 RW1 hotfix — Step 4 false-green was undermining
    // confidence in the platform: it showed a green checkmark and
    // "pipeline proceeds" even when no QA audit had ever run. The
    // step is now AMBER + this panel surfaces a Run QA Audit CTA
    // that takes the user to the QA tab where they can fire the
    // independent three-layer audit. Step 7 (Generate Draft) is
    // also gated until the audit has run (generateDisabledReason
    // in ReportWriter.tsx).
    return (
      <div className="space-y-2">
        <p className="text-text-secondary">
          No QA audit has been run yet. Step 7 (Generate Draft) is
          gated on the independent three-layer audit so the
          submission's figures all carry validation. Run the audit
          before generating the draft.
        </p>
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={() => navigate('/qa')}
            data-testid="step4-run-qa-audit"
            className="inline-flex items-center gap-1.5 px-3 py-1.5
                       bg-electric-blue hover:bg-electric-blue/80
                       text-white text-2xs font-medium rounded
                       transition-colors">
            <ShieldCheck className="w-3.5 h-3.5" />
            Run QA Audit
          </button>
          <span className="text-text-muted text-2xs italic">
            Opens the QA tab in a new view — return here when the
            audit completes.
          </span>
        </div>
      </div>
    )
  }
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2 text-2xs">
        <Pill text={`Stat: ${String(payload['statistical_status'] ?? '—')}`}
              kind={payload['statistical_status'] === 'pass' ? 'green' : 'amber'} />
        {payload['qa_status'] ? (
          <Pill text={`QA: ${String(payload['qa_status'])}`}
                kind={payload['qa_status'] === 'pass' ? 'green' : 'amber'} />
        ) : null}
        {payload['passed'] !== undefined ? (
          <Pill text={`${payload['passed']} passed`} kind="green" />
        ) : null}
        {Number(payload['failed'] ?? 0) > 0 ? (
          <Pill text={`${payload['failed']} failed`} kind="red" />
        ) : null}
        {Number(payload['warning'] ?? 0) > 0 ? (
          <Pill text={`${payload['warning']} warnings`} kind="amber" />
        ) : null}
      </div>
      {payload['run_at'] ? (
        <p className="text-text-muted italic">
          Last run: {String(payload['run_at'])}
        </p>
      ) : null}
    </div>
  )
}


function Step5Detail({ payload }: { payload: Record<string, unknown> }) {
  const mismatches = Number(payload['mismatch_count'] ?? 0)
  const flags = (payload['flags'] as string[]) || []
  if (mismatches === 0) {
    return (
      <p className="text-green-300">
        All verified figures match live analytics data. Zero
        cross-reference mismatches.
      </p>
    )
  }
  return (
    <div className="space-y-2">
      <p className="text-amber-300">
        {mismatches} mismatch{mismatches === 1 ? '' : 'es'} found.
        Live values will be used in the paper.
      </p>
      {flags.length > 0 ? (
        <ul className="space-y-1">
          {flags.map((f, i) => (
            <li key={i} className="font-mono text-amber-100/80">• {f}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}


function Step6Detail({ payload }: { payload: Record<string, unknown> }) {
  const conditions =
    (payload['conditions'] as Array<Record<string, unknown>>) || []
  return (
    <div className="space-y-2">
      {conditions.length === 0 ? (
        <p className="italic text-text-muted">No conditions in payload.</p>
      ) : null}
      {conditions.map((c, i) => {
        const passed = Boolean(c['passed'])
        return (
          <div
            key={i}
            data-testid={`step6-condition-${c['id']}`}
            className={
              'p-1.5 border rounded ' +
              (passed
                ? 'bg-green-500/5 border-green-500/30'
                : 'bg-red-500/5 border-red-500/30')
            }>
            <div className="flex items-center justify-between">
              <span className="font-medium">
                {String(c['description'] ?? c['id'] ?? '—')}
              </span>
              <Pill
                text={passed ? 'PASS' : 'FAIL'}
                kind={passed ? 'green' : 'red'} />
            </div>
            <p className="text-text-muted mt-0.5">
              <span className="font-mono">value = {String(c['value'] ?? '—')}</span>
              {' · '}
              <span className="font-mono">threshold &gt; {String(c['threshold'] ?? '—')}</span>
            </p>
          </div>
        )
      })}
    </div>
  )
}


// ── Tiny inline UI helpers (kept local — no other surface uses them) ──────


function Pill({
  text, kind,
}: { text: string; kind: 'green' | 'amber' | 'red' | 'info' }) {
  const cls =
    kind === 'green' ? 'bg-green-500/15 text-green-300' :
    kind === 'amber' ? 'bg-amber-500/15 text-amber-300' :
    kind === 'red'   ? 'bg-red-500/15 text-red-300' :
    'bg-navy-800 text-text-secondary'
  return (
    <span className={`px-1.5 py-0.5 text-2xs rounded ${cls}`}>{text}</span>
  )
}


function StrengthBadge({ value }: { value: string }) {
  const cls =
    value === 'HIGH'   ? 'bg-green-500/15 text-green-300' :
    value === 'MEDIUM' ? 'bg-amber-500/15 text-amber-300' :
    'bg-navy-800 text-text-muted'
  return (
    <span className={`px-1.5 py-0.5 text-2xs rounded ${cls}`}>
      {value}
    </span>
  )
}


function VerificationBadge({ state }: { state: string }) {
  // Recognises both the legacy "verified" / "not_found" /
  // "untrusted_source" states and the future state-machine values
  // queued in the deferred citation review commit.
  const verified = new Set([
    'verified', 'human_verified', 'search_selected', 'manually_added',
  ])
  if (verified.has(state)) {
    return (
      <span className="inline-flex items-center gap-1 text-green-300">
        <CheckCircle className="w-3 h-3" />
        verified
      </span>
    )
  }
  if (state === 'untrusted_source' || state === 'pending_review') {
    return (
      <span className="inline-flex items-center gap-1 text-amber-300">
        <AlertCircle className="w-3 h-3" />
        untrusted
      </span>
    )
  }
  if (state === 'rejected_no_citation' || state === 'rejected') {
    return (
      <span className="inline-flex items-center gap-1 text-text-muted">
        rejected
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-red-400">
      <AlertCircle className="w-3 h-3" />
      not found
    </span>
  )
}


/**
 * Tiny effect helper — fires auto steps when their prerequisites
 * become complete. The ReportWriter page composes this with its own
 * step state. Returns nothing — it triggers the callback.
 */
export function useAutoFireStep5And6(
  results: StepResults,
  fireStep: (n: number) => Promise<void>,
): void {
  // May 24 2026 — strict-sequential auto-fire. Only Steps 5 and 6
  // auto-fire; everything else is manual. Step 5 fires when Step 4
  // completes with a REAL QA audit (no _no_audit bypass). Step 6
  // fires when Step 5 lands. Both gates require passage by the
  // _stepPassed contract (complete or warning AND no _no_audit
  // flag) — a Step 4 in warning state with _no_audit: true does
  // NOT trigger Step 5.

  useEffect(() => {
    const step5Idle = !results[5] || results[5].status === 'idle'
    if (_stepPassed(results, 4) && step5Idle) {
      void fireStep(5)
    }
    // The hook intentionally depends only on `results` so a
    // status change in Step 4 re-evaluates the gate; fireStep is
    // the action.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results, fireStep])

  useEffect(() => {
    const step6Idle = !results[6] || results[6].status === 'idle'
    if (_stepPassed(results, 5) && step6Idle) {
      void fireStep(6)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results, fireStep])
}
