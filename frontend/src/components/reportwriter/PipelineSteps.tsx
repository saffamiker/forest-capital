/**
 * frontend/src/components/reportwriter/PipelineSteps.tsx
 *
 * The 11-step status display along the top of the report writer.
 * Each step shows a number, label, status pill, and (when relevant)
 * a count badge — verified citations, BOB blocks remaining, flag
 * count, readiness, etc.
 *
 * Steps:
 *   1.  Stage Findings          (run via the existing endpoint)
 *   2.  Source Citations
 *   3.  Pull Team Activity
 *   4.  Pull Validation Data
 *   5.  Cross-Reference Check   (auto, surfaced from generation)
 *   6.  Thesis Validation       (auto, blocks generation on fail)
 *   7.  Generate Draft
 *   8.  Review and Edit Draft   (resolve [BOB] blocks)
 *   9.  Run Final Check
 *  10.  Run Academic Review
 *  11.  Download
 */
import {
  CheckCircle, Circle, AlertCircle, Loader2,
} from 'lucide-react'

export type StepStatus =
  | 'idle'
  | 'pending'
  | 'in_progress'
  | 'complete'
  | 'warning'
  | 'failed'

export interface PipelineStep {
  number: number
  label: string
  status: StepStatus
  detail?: string
}

interface Props {
  steps: PipelineStep[]
}

const STATUS_STYLE: Record<StepStatus, {
  pill: string; label: string;
}> = {
  idle:         { pill: 'bg-navy-800 text-text-muted border-navy-700',
                  label: 'Pending' },
  pending:      { pill: 'bg-navy-800 text-text-muted border-navy-700',
                  label: 'Pending' },
  in_progress:  { pill: 'bg-electric-blue/20 text-electric-blue border-electric-blue/40',
                  label: 'Running' },
  complete:     { pill: 'bg-green-500/15 text-green-300 border-green-500/40',
                  label: 'Complete' },
  warning:      { pill: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
                  label: 'Warning' },
  failed:       { pill: 'bg-red-500/15 text-red-300 border-red-500/40',
                  label: 'Failed' },
}

export default function PipelineSteps({ steps }: Props) {
  return (
    <section
      data-testid="pipeline-steps"
      className="bg-navy-900 border border-navy-700 rounded p-3">
      <h3 className="text-white font-medium text-sm mb-2">
        Generation pipeline
      </h3>
      <ol className="space-y-1">
        {steps.map((step) => (
          <Step key={step.number} step={step} />
        ))}
      </ol>
    </section>
  )
}


function Step({ step }: { step: PipelineStep }) {
  const style = STATUS_STYLE[step.status] ?? STATUS_STYLE.idle
  let Icon = Circle
  if (step.status === 'complete') Icon = CheckCircle
  else if (step.status === 'in_progress') Icon = Loader2
  else if (step.status === 'warning' || step.status === 'failed') Icon = AlertCircle

  return (
    <li
      data-testid={`step-${step.number}`}
      className="flex items-center justify-between gap-2 py-1">
      <span className="flex items-center gap-2 flex-1 min-w-0">
        <Icon
          className={
            'w-3.5 h-3.5 flex-shrink-0 ' +
            (step.status === 'complete' ? 'text-green-400 ' : '') +
            (step.status === 'in_progress' ? 'text-electric-blue animate-spin ' : '') +
            (step.status === 'warning' ? 'text-amber-400 ' : '') +
            (step.status === 'failed' ? 'text-red-400 ' : '') +
            (step.status === 'idle' || step.status === 'pending' ? 'text-text-muted ' : '')
          }
        />
        <span className="text-text-secondary text-xs">
          <span className="text-text-muted">{step.number}.</span>{' '}
          {step.label}
        </span>
        {step.detail ? (
          <span className="text-text-muted text-2xs truncate">
            ({step.detail})
          </span>
        ) : null}
      </span>
      <span
        className={`px-2 py-0.5 border rounded text-2xs font-medium ${style.pill}`}>
        {style.label}
      </span>
    </li>
  )
}
