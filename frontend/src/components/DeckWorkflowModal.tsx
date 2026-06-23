/**
 * DeckWorkflowModal -- the step-by-step "How to Build the Final
 * Presentation Deck" guide shown when the user clicks the Info icon
 * on the Final Presentation Deck card in Generate Documents.
 *
 * Model: BriefWorkflowModal. Section / Step layout helpers and the
 * Submission Checklist pattern (interactive checkboxes, all-checked
 * banner, reset-on-close) are duplicated here rather than extracted
 * because the two guides currently have small per-doc differences
 * in structure -- promoting to shared helpers belongs in a later
 * refactor pass once the appendix and script guides land too.
 */
import { useEffect, useState } from 'react'
import { Check, X } from 'lucide-react'

export interface DeckWorkflowModalProps {
  open: boolean
  onClose: () => void
}

const CHECKLIST_ITEMS: readonly string[] = [
  'All 12 slides have SO WHAT titles and no [DATA PENDING]',
  'Speaker notes present on all slides including slide 9 demo sequence',
  'No doubled symbols on slide 1',
  'Deck Review shows no Needs Work findings',
  '.pptx downloaded and reviewed before submission',
] as const

export function DeckWorkflowModal(
  { open, onClose }: DeckWorkflowModalProps,
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
      data-testid="deck-workflow-modal">
      <div
        className="card p-5 max-w-lg w-full max-h-[90vh]
                   overflow-y-auto space-y-3"
        onClick={(e) => e.stopPropagation()}>

        <div className="flex items-start justify-between gap-3">
          <h3 className="text-sm font-semibold text-white">
            How to Build the Final Presentation Deck
          </h3>
          <button
            type="button"
            onClick={handleClose}
            data-testid="deck-workflow-modal-close"
            aria-label="Close"
            className="text-muted hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>

        <Section title="Before You Generate">
          <Step n="1" heading="Generate the Executive Brief first">
            The deck narrative is grounded in the brief. If you have
            not yet generated and reviewed the brief, do that first.
            The deck story plan builds on the brief&apos;s central
            argument.
          </Step>
          <Step n="2" heading="Upload slide guidance (optional)">
            Use the Slide Guidance panel inside this tile to upload a
            JSON override file if you want to adjust per-slide
            direction. Download the template first to see the
            editable fields. Guidance uploads are ignored while a
            deck job is in progress.
          </Step>
        </Section>

        <Section title="Generating the Deck">
          <Step n="3" heading="Click Regenerate">
            Generation runs in two passes:
            <ul className="list-disc list-inside ml-2 mt-1
                          text-2xs text-slate-300 space-y-0.5">
              <li>Pass 1a: lean slide plan (Opus, under 4000 tokens)</li>
              <li>
                Pass 1b: speaker notes including the live demo
                sequence for slide 9 (ceiling 7000 tokens)
              </li>
            </ul>
            <p className="mt-1 text-2xs text-muted">
              This takes approximately 90-120 seconds.
            </p>
          </Step>
        </Section>

        <Section title="Quality Verification (allow 15 minutes)">
          <Step n="4" heading="Check all 12 slides">
            Confirm each slide has a populated SO WHAT title, no
            more than 3 bullets, and no [DATA PENDING] placeholders.
            Slides 3, 4, and 6 should show crisis window drawdown
            figures. Slide 8 watchpoint values should be live.
          </Step>
          <Step n="5" heading="Check speaker notes">
            Speaker notes should be present on all slides. Slide 9
            should include the LIVE_DEMO_SEQUENCE. If notes are
            missing or truncated, regenerate.
          </Step>
          <Step n="6" heading="Check symbols">
            Slide 1 percentage figures should show clean symbols
            with no doubling (e.g. +98% not +98%%).
          </Step>
          <Step n="7" heading="Run Deck Review">
            Click &quot;Review Deck&quot; in the editor&apos;s
            Writing Assistant panel to run a focused academic review
            against the deck rubric. Address any Needs Work findings
            before exporting.
          </Step>
        </Section>

        <Section title="Exporting">
          <Step n="8" heading="Export">
            Click Export to download the .pptx file. The audit
            re-runs on export.
          </Step>
        </Section>

        <div className="pt-2 border-t border-border">
          <h4 className="text-2xs font-semibold uppercase
                         tracking-wide text-slate-200 mb-2">
            Submission Checklist
          </h4>
          <div className="space-y-1.5"
               data-testid="deck-workflow-checklist">
            {CHECKLIST_ITEMS.map((label, i) => (
              <label
                key={i}
                data-testid={`deck-checklist-item-${i}`}
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
                      data-testid={`deck-checklist-check-${i}`} />
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
              data-testid="deck-workflow-ready-banner"
              className="mt-3 p-2 rounded bg-green-600/10
                         border border-green-600/30 text-success
                         text-2xs text-center font-medium">
              Ready to submit. Export your .pptx and upload.
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
