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
  detail?: string
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
    </li>
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
