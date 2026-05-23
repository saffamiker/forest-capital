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
import {
  CheckCircle, AlertCircle, Loader2, Play, Circle,
  ChevronDown, ChevronUp, ExternalLink,
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
}


const STEP_LABELS: Record<number, string> = {
  1: 'Stage Findings',
  2: 'Source Citations',
  3: 'Pull Team Activity',
  4: 'Pull Validation Data',
  5: 'Cross-Reference Check',
  6: 'Thesis Validation',
  7: 'Generate Draft',
}

const AUTO_STEPS = new Set<number>([5, 6])


export default function PipelineGate({
  results, generating, generateDisabledReason,
  onRunStep, onGenerate,
}: Props) {
  return (
    <section
      data-testid="pipeline-gate"
      className="bg-navy-900 border border-navy-700 rounded p-3 space-y-2">
      <header className="flex items-center justify-between">
        <h3 className="text-white font-medium text-sm">
          Generation pipeline
        </h3>
        <span className="text-text-muted text-2xs">
          Run each step in order. Steps 5–6 fire automatically.
        </span>
      </header>
      <ol className="space-y-2">
        {[1, 2, 3, 4, 5, 6, 7].map((n) => (
          <StepRow
            key={n}
            number={n}
            label={STEP_LABELS[n]}
            result={results[n]}
            disabledReason={
              n === 7 ? generateDisabledReason
              : disabledReasonFor(n, results)
            }
            isAuto={AUTO_STEPS.has(n)}
            isGenerating={generating}
            onRun={
              n === 7
                ? onGenerate
                : () => onRunStep(n)
            }
          />
        ))}
      </ol>
    </section>
  )
}


function disabledReasonFor(
  step: number, results: StepResults,
): string | null {
  // Step 1 has no prerequisites.
  if (step === 1) return null
  // Steps 2-4 require Step 1 to be complete.
  if (step === 2 || step === 3 || step === 4) {
    return results[1]?.status === 'complete'
      ? null
      : 'Run Step 1 first'
  }
  // Auto steps cannot be clicked.
  if (step === 5 || step === 6) return 'Runs automatically'
  return null
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
        <Icon className={`w-4 h-4 flex-shrink-0 ${iconCls}`} />
        <span className="text-text-secondary text-xs flex-1">
          <span className="text-text-muted">{number}.</span>{' '}
          {label}
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


function StepDetailToggle({
  number, result,
}: { number: number; result: StepResult }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-1.5 pl-6">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        data-testid={`pipeline-step-${number}-expand`}
        className={
          'inline-flex items-center gap-1 px-2 py-0.5 ' +
          'text-2xs text-electric-blue hover:text-electric-blue/80'
        }>
        {open ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        {open ? 'Hide details' : 'View details'}
      </button>
      {open ? (
        <div
          data-testid={`pipeline-step-${number}-detail`}
          className={
            'mt-2 p-2 bg-navy-950 border border-navy-700 rounded ' +
            'text-2xs text-text-secondary overflow-x-auto'
          }>
          {_renderDetail(number, result)}
        </div>
      ) : null}
    </div>
  )
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
        alternatives where it could; review and apply them via the
        Citation Review panel on the editor screen.
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
  if (payload['_no_audit']) {
    return (
      <div className="space-y-2">
        <p className="text-text-secondary">
          No audit has been run yet on this deployment. The pipeline
          proceeds without validation data — validation is
          informational, not a hard gate.
        </p>
        <p className="text-text-muted italic">
          Run the QA Audit (in the QA tab) before submitting the
          final paper so the independent three-layer validation
          appears in Appendix D.
        </p>
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
  useEffect(() => {
    const step1Done = results[1]?.status === 'complete'
    const step2Done = results[2]?.status === 'complete'
    const step3Done = results[3]?.status === 'complete'
    const step4Done = results[4]?.status === 'complete'
    const step5Idle = !results[5] || results[5].status === 'idle'
    if (step1Done && step2Done && step3Done && step4Done && step5Idle) {
      void fireStep(5)
    }
  }, [results, fireStep])

  useEffect(() => {
    const step5OK = (
      results[5]?.status === 'complete'
      || results[5]?.status === 'warning')
    const step6Idle = !results[6] || results[6].status === 'idle'
    if (step5OK && step6Idle) {
      void fireStep(6)
    }
  }, [results, fireStep])
}
