/**
 * SubmissionGuidePanel — the deliverable submission guide, opened from a
 * button in the Reports header.
 *
 * Guide 1 (Bob) covers the July 1st executive brief submission. Guide 2
 * (Molly) covers the July 1st final presentation, with a note about
 * the July 3rd panel presentation (where all three team members
 * present together). Each guide leads with one countdown chip per
 * deadline.
 *
 * The panel shows the guide relevant to the signed-in user — Bob sees
 * Guide 1, Molly sees Guide 2, everyone else (Michael included) sees
 * both.
 *
 * PR #338 (June 19 2026) — the midpoint paper has been submitted and
 * the legacy /reports/writer pipeline retired; this guide no longer
 * carries the midpoint sub-deliverable. The peer-review cohort event
 * (June 3rd) has passed and is no longer referenced here.
 */
import { X, ChevronDown, ChevronRight } from 'lucide-react'
import { useState } from 'react'

import { useAuth } from '../App'

const TRACKING_NOTE =
  'Everything you do on this platform is tracked and contributes to your '
  + 'documented contribution record. Run council sessions here, not in a '
  + 'separate chat. Write your draft here, not in Word. Ask questions '
  + 'here, not elsewhere. Your activity log is part of the project '
  + 'evidence. Make it count.'

// May 28 2026 — shared audit-gate callout. The report-readiness gate
// (workstream C) refuses to generate the executive brief or final
// presentation deck while any audit WARN finding is unreviewed. Both
// guides surface this at the top so the team sees the gate before
// the first Generate click rather than after a 422.
const AUDIT_GATE_NOTE =
  'Before generating any report: navigate to the QA Audit tab and '
  + 'ensure every WARN finding is either Acknowledged or Marked as '
  + 'Intentional with a disclosure note. The Generate button will '
  + 'return a blocking modal listing any outstanding items if this '
  + 'step is skipped.'

interface Step {
  step: string
  detail?: string[]
}

interface Deadline {
  date: string    // ISO date
  label: string   // "Midpoint paper" | "Executive Brief" | "Final Presentation"
  noun: string    // "submission" | "presentation"
}

interface Guide {
  id: string
  title: string
  owner: string
  ownerEmail: string
  deadlines: Deadline[]
  /** Optional footer note — e.g. Molly's panel-presentation reminder. */
  panelNote?: string
  steps: Step[]
}

const GUIDES: Guide[] = [
  {
    id: 'guide-1',
    title: 'Guide 1 — Bob: Executive Brief',
    owner: 'Bob',
    ownerEmail: 'thaob@queens.edu',
    deadlines: [
      { date: '2026-07-01', label: 'Executive Brief', noun: 'submission' },
    ],
    steps: [
      // ── Executive brief (July 1st) ─────────────────────────────────
      { step: 'Open the Reports screen and find Generate Documents.' },
      {
        step: 'Generate your Executive Brief.',
        detail: [
          'Click [Generate Executive Brief] from the Reports page.',
          'Click [Open in Editor] after generation.',
        ],
      },
      {
        step: 'Work through your Executive Brief.',
        detail: [
          'Complete every BOB callout.',
          'Run Academic Review from the QA Audit page (canonical) — '
            + 'the editor Writing Assistant still surfaces an inline '
            + 'trigger as a convenience.',
          'Re-run until no Needs Work sections remain.',
          'Clear every WARN finding on the QA Audit tab before the '
            + 'final export — the gate applies to the brief too.',
        ],
      },
      {
        step: 'Save and export.',
        detail: [
          'Save a named version labelled "Final submission".',
          'Verify the Audit Disclosure Appendix at the end of the '
            + 'document carries the team\'s reviewed disclosures.',
          'Export to DOCX and submit by July 1st.',
        ],
      },
    ],
  },
  {
    id: 'guide-2',
    title: 'Guide 2 — Final Presentation',
    owner: 'Molly',
    ownerEmail: 'murdockm@queens.edu',
    deadlines: [
      { date: '2026-07-01', label: 'Final Presentation', noun: 'presentation' },
    ],
    panelNote:
      'Panel presentation: July 3rd. Use Rehearsal Mode to practise '
      + 'before the panel.',
    steps: [
      { step: 'Open the Reports screen and find Generate Documents.' },
      { step: 'Generate the Final Presentation Deck.' },
      {
        step: 'Generate and open your presentation in the editor.',
        detail: ['Click Open in Editor after generation.'],
      },
      {
        step: 'For each slide:',
        detail: [
          'Verify all data points (the amber markers).',
          'Write your speaker notes.',
          'Use Generate Talking Points as a starting point — rewrite in '
            + 'your own voice.',
        ],
      },
      {
        step: 'Run Presentation Preview.',
        detail: [
          'Rehearse with your notes visible.',
          'Time yourself — aim for 20-25 minutes.',
        ],
      },
      // May 28 2026 — Academic Review now runs BEFORE the final
      // PPTX export. The previous order ran Academic Review AFTER
      // export, which meant submitting a deck the team had not yet
      // improved against the review verdict. The Academic Review
      // panel is on the QA Audit page (PR #152); the deck editor's
      // Writing Assistant also surfaces an inline trigger.
      {
        step: 'Run Academic Review against the final deck.',
        detail: [
          'Navigate to the QA Audit page — the Academic Review panel '
            + 'is the canonical location.',
          'Read every section verdict; revise the deck for any Needs '
            + 'Work sections before exporting.',
          'Re-run until the verdict is satisfactory — the review '
            + 'verdict should inform the final deck, not follow it.',
        ],
      },
      {
        // May 28 2026 — QA Audit disclosure workflow before export.
        // Same disclosure gate applies to the presentation deck;
        // the team must clear every WARN before the gate clears.
        step: 'QA Audit review: clear every audit warning.',
        detail: [
          'Navigate to the QA Audit tab.',
          'Acknowledge or Mark as Intentional every WARN finding '
            + 'with a disclosure note of at least 20 characters.',
          'All findings must show green before the report gate clears.',
        ],
      },
      { step: 'Export PPTX for the July 1st submission.' },
      {
        step: 'Assign speakers to slides.',
        detail: [
          'In the presentation canvas editor, click [+ Speaker] on each '
            + 'slide and assign a presenter name.',
          'Every slide should have a speaker before generating the script.',
        ],
      },
      {
        step: 'Generate your script.',
        detail: [
          'Click [Generate Script] in the editor header.',
          'The script opens automatically in a new editor tab.',
          'Generation takes 30-60 seconds.',
        ],
      },
      {
        step: 'Rewrite in your own voice.',
        detail: [
          'Work through every section. The generated script is a starting '
            + 'point — the substance is correct but the voice needs to be '
            + 'yours and your team’s.',
          'Use the Writing Assistant panel for help with phrasing.',
          'Watch the delivery time indicator — aim for 18-27 minutes.',
          'Use [Rehearse] in the script editor to practise with your '
            + 'slides and script side by side.',
        ],
      },
      {
        // May 28 2026 — per-speaker DOCX export emphasised. Both
        // exports are available from the script editor header.
        step: 'Export per-speaker scripts.',
        detail: [
          'Click [Export Master Script] for the full team version — '
            + 'every section in one document with stable per-speaker '
            + 'colour coding.',
          'Click [Export: {Name}] for each speaker\'s individual '
            + 'script — only their sections, with slide numbers and '
            + 'titles retained for cross-reference.',
          'Both exports are available from the script editor header.',
        ],
      },
    ],
  },
]

/** Per-owner deadline data — the login-notification countdown reads this.
 *  Flattened across guides: a guide with two deadlines emits two entries,
 *  so the consumer can pick the nearest unpassed one per owner. */
export const SUBMISSION_DEADLINES = GUIDES.flatMap((g) => g.deadlines.map((d) => ({
  ownerEmail: g.ownerEmail,
  deadline: d.date,
  noun: d.noun,
  label: d.label,
})))


/** Whole days from today (local midnight) to a deadline date. */
export function daysUntil(deadlineISO: string, now: Date = new Date()): number {
  const today = new Date(now)
  today.setHours(0, 0, 0, 0)
  const deadline = new Date(`${deadlineISO}T00:00:00`)
  return Math.round((deadline.getTime() - today.getTime()) / 86_400_000)
}

/** The countdown label + urgency colour for a deadline. */
export function deadlineCountdown(deadlineISO: string, noun: string,
                                  now: Date = new Date()):
  { label: string; tone: 'normal' | 'amber' | 'red' | 'passed' } {
  const days = daysUntil(deadlineISO, now)
  if (days < 0) return { label: 'Deadline passed', tone: 'passed' }
  if (days === 0) {
    return {
      label: `${noun.charAt(0).toUpperCase()}${noun.slice(1)} today`,
      tone: 'red',
    }
  }
  const label = `${days} day${days === 1 ? '' : 's'} until ${noun}`
  if (days <= 2) return { label, tone: 'red' }
  if (days <= 5) return { label, tone: 'amber' }
  return { label, tone: 'normal' }
}

/** Compact "<Label>: <n> days" — used when a guide carries more than one
 *  deadline so each chip names which deliverable it is counting down to. */
export function compactCountdown(deadlineISO: string, label: string,
                                 now: Date = new Date()):
  { label: string; tone: 'normal' | 'amber' | 'red' | 'passed' } {
  const days = daysUntil(deadlineISO, now)
  if (days < 0) return { label: `${label}: passed`, tone: 'passed' }
  if (days === 0) return { label: `${label}: today`, tone: 'red' }
  const dayText = `${days} day${days === 1 ? '' : 's'}`
  const tone: 'normal' | 'amber' | 'red' =
    days <= 2 ? 'red' : days <= 5 ? 'amber' : 'normal'
  return { label: `${label}: ${dayText}`, tone }
}

const TONE_CLASS: Record<string, string> = {
  normal: 'bg-electric/10 text-electric border-electric/30',
  amber: 'bg-warning/10 text-warning border-warning/40',
  red: 'bg-danger/10 text-danger border-danger/40',
  passed: 'bg-navy-700 text-muted border-border',
}

function GuideCard({ guide }: { guide: Guide }) {
  const [open, setOpen] = useState(true)
  // One chip per deadline. A guide with one deadline renders the
  // legacy "N days until submission" copy; a guide with two or more
  // uses the compact "<Label>: N days" so the chip identifies which.
  const chips = guide.deadlines.map((d) => {
    const cd = guide.deadlines.length > 1
      ? compactCountdown(d.date, d.label)
      : deadlineCountdown(d.date, d.noun)
    return { key: d.date, ...cd }
  })
  return (
    <div className="card p-4">
      <button type="button" onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2">
        <span className="text-white font-semibold text-sm">{guide.title}</span>
        {open ? <ChevronDown className="w-4 h-4 text-muted" />
          : <ChevronRight className="w-4 h-4 text-muted" />}
      </button>

      {/* Deadline countdown — one chip per deadline. */}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {chips.map((c) => (
          <div key={c.key}
               className={`text-2xs font-semibold rounded border px-2 py-1
                           ${TONE_CLASS[c.tone]}`}>
            {c.label}
          </div>
        ))}
      </div>

      {open && (
        <div className="mt-3 space-y-3">
          {/* May 28 2026 — shared audit-gate callout. The report-
              readiness gate intercepts every Generate click; this
              callout surfaces the gate to the user BEFORE the first
              attempt rather than after a 422. Rendered on both
              guides. */}
          <div data-testid="submission-guide-audit-gate-callout"
            className="rounded border border-danger/40 bg-danger/10
                       px-3 py-2 text-xs text-red-100/90">
            <strong className="text-red-100">Audit gate.</strong>{' '}
            {AUDIT_GATE_NOTE}
          </div>
          <div className="rounded border border-warning/40 bg-warning/10
                          px-3 py-2 text-xs text-amber-100/90">
            {TRACKING_NOTE}
          </div>
          <ol className="space-y-2 text-xs list-decimal pl-5">
            {guide.steps.map((s) => (
              <li key={s.step} className="text-slate-200">
                {s.step}
                {s.detail && (
                  <ul className="list-disc pl-4 mt-0.5 space-y-0.5 text-muted">
                    {s.detail.map((d) => <li key={d}>{d}</li>)}
                  </ul>
                )}
              </li>
            ))}
          </ol>
          {guide.panelNote && (
            <div className="rounded border border-electric/30 bg-electric/10
                            px-3 py-2 text-xs text-electric">
              {guide.panelNote}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function SubmissionGuidePanel({ onClose }: { onClose: () => void }) {
  const { session } = useAuth()
  const email = session?.email ?? ''
  // Bob sees Guide 1, Molly sees Guide 2; everyone else sees both.
  const owned = GUIDES.filter((g) => g.ownerEmail === email)
  const guides = owned.length > 0 ? owned : GUIDES

  return (
    <>
      <div className="fixed inset-0 bg-black/40 z-[69]" onClick={onClose} />
      {/* On lg+ the guide renders as a right-side drawer (420 px aside).
          Below lg it slides up from the bottom as a full-width sheet
          (max-h 80vh, scrollable) so the narrow-viewport user sees the
          guide content rather than a 200-300 px side column. The
          rounded-t-lg drag-handle look is the established mobile-sheet
          pattern (matches TestRunner, ExplainerPanel). */}
      <aside className="fixed bg-navy-900 border-border z-[70] flex flex-col
                        max-lg:inset-x-0 max-lg:bottom-0 max-lg:rounded-t-lg
                        max-lg:border-t max-lg:max-h-[80vh]
                        lg:right-0 lg:top-0 lg:h-full lg:w-[420px]
                        lg:border-l">
        {/* Drag-handle pill — mobile sheet visual only. */}
        <div className="lg:hidden flex justify-center pt-2 pb-1">
          <div className="w-10 h-1 rounded-full bg-border" />
        </div>
        <div className="flex items-center justify-between px-4 py-3
                        border-b border-border">
          <h2 className="text-white font-semibold text-sm">
            📋 Submission Guide
          </h2>
          <button type="button" onClick={onClose} aria-label="Close"
            className="text-muted hover:text-white min-h-[44px] min-w-[44px]
                       sm:min-h-0 sm:min-w-0 flex items-center justify-center">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-3 space-y-3">
          <p className="text-2xs text-muted">
            The editor-based workflow for each deliverable.
          </p>
          {guides.map((g) => <GuideCard key={g.id} guide={g} />)}
        </div>
      </aside>
    </>
  )
}
