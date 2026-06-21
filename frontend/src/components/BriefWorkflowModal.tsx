/**
 * BriefWorkflowModal -- the step-by-step "How to Build the Executive Brief"
 * guide shown when the user clicks the Info icon on the Executive Brief
 * card in the Generate Documents panel.
 *
 * Why this exists: the brief pipeline has evolved across multiple PRs
 * (#326 rubric rewrite, #333 story plan integration, #335 seven-citation
 * grounding, #336 audit gaps wiring). The platform now does a lot
 * automatically -- locks the structural plan via Opus, fires quality
 * checks against numeric anchors and required citations, re-runs the
 * audit on export -- but the user (Bob) still needs to manually verify
 * several rubric requirements (citation placement, section word counts,
 * rebalancing disclosure accuracy, recommendations framing). This modal
 * is the canonical operator runbook for a submission-quality brief.
 *
 * The submission checklist at the bottom is interactive: each item is a
 * stateful checkbox that strikes through when toggled. When all six are
 * checked, a green confirmation banner unlocks at the bottom. State is
 * LOCAL to this component -- nothing persists between modal opens.
 * Closing the modal resets every checkbox to unchecked.
 */
import { useEffect, useState } from 'react'
import { Check, X } from 'lucide-react'

export interface BriefWorkflowModalProps {
  open: boolean
  onClose: () => void
}

// The six submission-checklist items rendered as interactive checkboxes.
// Exact labels are pinned by tests so a future edit cannot quietly drop
// or alter a rubric verification step.
//
// Layer 3b (June 21 2026) -- item 1 was rewritten from "Audit banner
// shows clean" to point at the new Pre-Submission Check verdict on
// the Reports page. The audit banner alone could clear while the
// substitution layer's cross-deliverable / numeric-presence checks
// caught issues unique to the export. The Verify All flow on Reports
// runs both surfaces together, so the checklist now references the
// single canonical pre-submission verdict.
const CHECKLIST_ITEMS: readonly string[] = [
  'Pre-Submission Check shows green (all deliverables verified)',
  'All seven citations present in body and References',
  'All six sections within word count targets',
  'Rebalancing disclosure accurate (monthly / 2pp)',
  'Final recommendations framed as investment conclusions',
  '.docx downloaded and reviewed in Word before upload',
] as const

export function BriefWorkflowModal(
  { open, onClose }: BriefWorkflowModalProps,
) {
  // Local checkbox state. Resets to all-false on every modal close so
  // the user gets a fresh checklist each time they open the guide --
  // this prevents a stale "all checked" state from masking a real
  // submission attempt that bypassed the verification steps.
  const [checked, setChecked] = useState<boolean[]>(
    () => Array<boolean>(CHECKLIST_ITEMS.length).fill(false))

  // Reset checklist when the modal closes. The reset on close pattern
  // (rather than reset on open) means a parent that re-opens the modal
  // does not race the state reset against the open transition.
  const handleClose = () => {
    setChecked(Array<boolean>(CHECKLIST_ITEMS.length).fill(false))
    onClose()
  }

  const toggle = (i: number) => {
    setChecked((prev) =>
      prev.map((v, idx) => (idx === i ? !v : v)))
  }

  const allChecked = checked.every(Boolean)

  // Esc-to-close. The modal is non-destructive so unconditional dismiss
  // is safe.
  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  // handleClose closes over the local reset state; the dependency on
  // `open` is the meaningful one (the listener attaches when open
  // flips true and detaches on close), so the linter rule for stale
  // closures does not apply here.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center
                 bg-black/60 p-4"
      onClick={handleClose}
      data-testid="brief-workflow-modal">
      <div
        className="card p-5 max-w-lg w-full max-h-[90vh]
                   overflow-y-auto space-y-3"
        onClick={(e) => e.stopPropagation()}>

        {/* ── Header ───────────────────────────────────────────── */}
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-sm font-semibold text-white">
            How to Build the Executive Brief
          </h3>
          <button
            type="button"
            onClick={handleClose}
            data-testid="brief-workflow-modal-close"
            aria-label="Close"
            className="text-muted hover:text-white">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* ── Step list ────────────────────────────────────────── */}
        <Section title="Before You Generate">
          <Step n="1" heading="Confirm caches are warm">
            Check Settings -&gt; Cache Status. If any cache shows cold,
            click Warm Caches and wait ~3 minutes before generating.
            A 422 error on generation means caches are cold.
          </Step>
          <Step n="2" heading="Clear stale story plan (if needed)">
            If the brief prompt has been updated since your last
            generation, a stale story plan may be cached. Ask Mike to
            run the cache clear command, or use the admin panel if
            available. Skip this step if this is your first
            generation.
          </Step>
        </Section>

        <Section title="Generating the Brief">
          <Step n="3" heading="Click Generate">
            The platform will automatically:
            <ul className="list-disc list-inside ml-2 mt-1
                          text-2xs text-slate-300 space-y-0.5">
              <li>
                Generate a locked section plan (Opus, with quality
                scoring -- retries if below threshold)
              </li>
              <li>Write each section with the plan injected</li>
              <li>
                Cross-reference all numbers against the data cache
              </li>
              <li>Flag any quality issues for your review</li>
            </ul>
            <p className="mt-1 text-2xs text-muted">
              This takes approximately 60-90 seconds.
            </p>
          </Step>
        </Section>

        <Section title="Quality Verification (allow 15 minutes)">
          <Step n="4" heading="Open in Editor">
            Click &quot;Open in Editor&quot; on the completed job
            card.
          </Step>
          <Step n="5" heading="Review audit flags">
            If the Audit banner appears at the top of the editor:
            <ul className="list-disc list-inside ml-2 mt-1
                          text-2xs text-slate-300 space-y-0.5">
              <li>
                Numeric flags: verify the disputed figure against
                the live platform data
              </li>
              <li>
                Citation flags: add the missing References entry or
                remove the orphaned in-text citation
              </li>
            </ul>
          </Step>
          <Step n="6" heading="Verify the seven required citations">
            The following must appear in-text AND in the References
            section at the end of the brief:
            <ul className="list-disc list-inside ml-2 mt-1
                          text-2xs text-slate-300 space-y-0.5">
              <li>Hamilton (1989) -- in Methodology</li>
              <li>Markowitz (1952) -- in Methodology</li>
              <li>
                Sharpe (1994) -- when presenting Sharpe ratio result
              </li>
              <li>Lo (2002) -- with the Deflated Sharpe Ratio</li>
              <li>Fama and French (1993) -- in factor attribution</li>
              <li>Carhart (1997) -- in factor attribution</li>
              <li>
                Ang and Bekaert (2002) -- regime-conditional
                allocation
              </li>
            </ul>
          </Step>
          <Step n="7" heading="Verify section word counts">
            <ul className="list-disc list-inside ml-2 mt-1
                          text-2xs text-slate-300 space-y-0.5">
              <li>Executive Summary: 200-300 words</li>
              <li>Methodology: 300-400 words</li>
              <li>Key Findings: 480-620 words</li>
              <li>Limitations and Risks: 250-350 words</li>
              <li>Final Recommendations: 300-400 words</li>
              <li>Visuals: 200-300 words</li>
            </ul>
          </Step>
          <Step n="8" heading="Verify rebalancing disclosure">
            Section 2 (Methodology) must state that the platform
            evaluates monthly and rebalances when any strategy
            weight crosses 2 percentage points. Add this manually
            if absent.
          </Step>
          <Step n="9" heading="Verify final recommendations framing">
            Section 5 must read as an investment committee conclusion
            -- &quot;We recommend regime-conditional dynamic
            allocation as the core portfolio framework&quot; -- not
            as academic hedges or next steps.
          </Step>
          <Step n="10" heading="Edit and save">
            Make any corrections in the editor. Save before
            exporting.
          </Step>
        </Section>

        <Section title="Exporting">
          <Step n="11" heading="Export">
            Click Export in the editor toolbar to download the
            .docx file for submission.
          </Step>
          <p className="text-2xs text-muted leading-relaxed mt-1
                        italic">
            Note: the audit re-runs on export to catch any issues
            introduced during editing.
          </p>
          <Step n="12" heading="Run Pre-Submission Check">
            Back on the Reports page, click{' '}
            <span className="font-semibold text-electric">
              Verify All for Submission
            </span>{' '}
            to confirm every numeric figure in the brief, deck, and
            appendix still matches the platform analytics cache. A
            green verdict is the final go-ahead; an amber or red
            verdict surfaces the document and figure to reopen and
            fix before submission.
          </Step>
        </Section>

        {/* ── Interactive submission checklist ────────────────── */}
        <div className="pt-2 border-t border-border">
          <h4 className="text-2xs font-semibold uppercase
                         tracking-wide text-slate-200 mb-2">
            Submission Checklist
          </h4>
          <div className="space-y-1.5"
               data-testid="brief-workflow-checklist">
            {CHECKLIST_ITEMS.map((label, i) => (
              <label
                key={i}
                data-testid={`brief-checklist-item-${i}`}
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
                      data-testid={`brief-checklist-check-${i}`} />
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
              data-testid="brief-workflow-ready-banner"
              className="mt-3 p-2 rounded bg-green-600/10
                         border border-green-600/30 text-success
                         text-2xs text-center font-medium">
              Ready to submit. Export your .docx and upload.
            </div>
          )}
        </div>

        {/* ── Dismiss button ──────────────────────────────────── */}
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


// ── Local layout helpers ───────────────────────────────────────────

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
