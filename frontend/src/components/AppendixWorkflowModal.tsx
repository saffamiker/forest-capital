/**
 * AppendixWorkflowModal -- the step-by-step "How to Build the
 * Analytical Appendix" guide shown when the user clicks the Info
 * icon on the Analytical Appendix card in Generate Documents.
 *
 * Model: BriefWorkflowModal. Section / Step / interactive checklist
 * mirror the brief guide; checklist content + step content are
 * doc-specific (appendix data hash, section G placeholder
 * check, appendix-specific review).
 */
import { useEffect, useState } from 'react'
import { Check, X } from 'lucide-react'

export interface AppendixWorkflowModalProps {
  open: boolean
  onClose: () => void
}

const CHECKLIST_ITEMS: readonly string[] = [
  'Data hash in footer matches Reports page hash',
  'Section G table present with no placeholder note',
  'Appendix Review shows no Needs Work findings',
  'Submission Readiness Review on Reports page shows green',
  '.docx downloaded and reviewed before submission',
] as const

export function AppendixWorkflowModal(
  { open, onClose }: AppendixWorkflowModalProps,
) {
  const [checked, setChecked] = useState<boolean[]>(
    () => Array<boolean>(CHECKLIST_ITEMS.length).fill(false))

  const handleClose = () => {
    setChecked(Array<boolean>(CHECKLIST_ITEMS.length).fill(false))
    onClose()
  }

  const toggle = (i: number) => {
    setChecked((prev) =>
      prev.map((v, idx) => (idx === i ? !v : v)))
  }

  const allChecked = checked.every(Boolean)

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 p-4"
      onClick={handleClose}
      data-testid="appendix-workflow-modal">
      <div
        className="card p-5 max-w-lg w-full max-h-[90vh]
                   overflow-y-auto space-y-3"
        onClick={(e) => e.stopPropagation()}>

        <div className="flex items-start justify-between gap-3">
          <h3 className="text-sm font-semibold text-white">
            How to Build the Analytical Appendix
          </h3>
          <button
            type="button"
            onClick={handleClose}
            data-testid="appendix-workflow-modal-close"
            aria-label="Close"
            className="text-muted hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>

        <Section title="Before You Generate">
          <Step n="1" heading="Generate the Executive Brief first">
            The appendix is the evidentiary record behind the brief.
            Generate and review the brief before generating the
            appendix.
          </Step>
        </Section>

        <Section title="Generating the Appendix">
          <Step n="2" heading="Click Regenerate">
            The appendix covers eight sections: data, performance,
            statistical tests, bootstrap CIs, factors, crisis
            windows, cost sensitivity, and audit. Every figure
            traces to the data hash in the footer.
            <p className="mt-1 text-2xs text-muted">
              Generation takes approximately 60-90 seconds.
            </p>
          </Step>
        </Section>

        <Section title="Quality Verification (allow 10 minutes)">
          <Step n="3" heading="Verify the data hash">
            The footer of every appendix page should show the
            canonical data hash. Confirm it matches the hash shown
            in Key Metrics on the Reports page.
          </Step>
          <Step n="4" heading="Check Section G (appendix table)">
            Section G should show the data table with no placeholder
            note above it. If a placeholder note appears alongside
            the table, flag to Mike.
          </Step>
          <Step n="5" heading="Run Appendix Review">
            Click &quot;Review Appendix&quot; in the editor&apos;s
            Writing Assistant panel to run a focused academic review
            against the appendix rubric. Address any Needs Work
            findings before exporting.
          </Step>
        </Section>

        <Section title="Exporting">
          <Step n="6" heading="Export">
            Click Export to download the .docx file.
          </Step>
        </Section>

        <div className="pt-2 border-t border-border">
          <h4 className="text-2xs font-semibold uppercase
                         tracking-wide text-slate-200 mb-2">
            Submission Checklist
          </h4>
          <div className="space-y-1.5"
               data-testid="appendix-workflow-checklist">
            {CHECKLIST_ITEMS.map((label, i) => (
              <label
                key={i}
                data-testid={`appendix-checklist-item-${i}`}
                className="flex items-start gap-2 cursor-pointer
                           text-2xs leading-relaxed"
                onClick={(e) => {
                  e.preventDefault()
                  toggle(i)
                }}>
                <span
                  className={`mt-0.5 flex-shrink-0 w-4 h-4 rounded
                             border flex items-center justify-center
                             ${checked[i]
                               ? 'bg-green-600 border-green-600'
                               : 'border-gray-400'}`}>
                  {checked[i] && (
                    <Check size={12} color="white"
                      data-testid={`appendix-checklist-check-${i}`} />
                  )}
                </span>
                <span
                  className={checked[i]
                    ? 'line-through text-muted'
                    : 'text-slate-200'}>
                  {label}
                </span>
              </label>
            ))}
          </div>
          {allChecked && (
            <div
              data-testid="appendix-workflow-ready-banner"
              className="mt-3 p-2 rounded bg-green-600/10
                         border border-green-600/30 text-success
                         text-2xs text-center font-medium">
              Ready to submit. Export your .docx and upload.
            </div>
          )}
        </div>

        <div className="flex justify-end pt-1">
          <button
            type="button"
            onClick={handleClose}
            className="px-3 py-1.5 rounded text-xs font-medium
                       bg-electric/10 border border-electric/40
                       text-electric hover:bg-electric/20
                       transition-colors">
            Close
          </button>
        </div>
      </div>
    </div>
  )
}


function Section(
  { title, children }: { title: string; children: React.ReactNode },
) {
  return (
    <div className="space-y-1.5">
      <h4 className="text-2xs font-semibold uppercase tracking-wide
                     text-slate-300">
        {title}
      </h4>
      {children}
    </div>
  )
}

function Step(
  { n, heading, children }: {
    n: string; heading: string; children: React.ReactNode
  },
) {
  return (
    <div className="space-y-0.5">
      <p className="text-2xs text-slate-200">
        <span className="font-semibold text-electric">
          Step {n}
        </span>
        {' -- '}
        <span className="font-semibold">{heading}</span>
      </p>
      <div className="text-2xs text-slate-300 leading-relaxed pl-1">
        {children}
      </div>
    </div>
  )
}
