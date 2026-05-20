/**
 * SubmissionGuidePanel — the deliverable submission guide, opened from a
 * button in the Reports header.
 *
 * Guide 1 (Bob, the midpoint paper) and Guide 2 (Molly, the final
 * presentation) each walk the editor-based workflow and lead with a
 * deadline countdown and the tracking note. The panel shows the guide
 * relevant to the signed-in user — Bob sees Guide 1, Molly sees
 * Guide 2, everyone else (Michael included) sees both.
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

interface Step {
  step: string
  detail?: string[]
}

interface Guide {
  id: string
  title: string
  owner: string
  ownerEmail: string
  deadline: string   // ISO date
  noun: string       // "submission" | "presentation"
  steps: Step[]
}

const GUIDES: Guide[] = [
  {
    id: 'guide-1',
    title: 'Guide 1 — Midpoint Paper',
    owner: 'Bob',
    ownerEmail: 'thaob@queens.edu',
    deadline: '2026-05-27',
    noun: 'submission',
    steps: [
      { step: 'Open the Reports screen and find Generate Documents.' },
      { step: 'Generate the Midpoint Submission Paper.' },
      {
        step: 'Generate and open your draft in the editor.',
        detail: ['Click Open in Editor after generation.'],
      },
      {
        step: 'Work through your draft.',
        detail: [
          'Resolve every amber data marker — verify each value against '
            + 'the Analytics page.',
          'Complete every BOB callout — these are the sections where '
            + 'your own analysis is required.',
          'Use the Writing Assistant panel to ask about any finding you '
            + 'are unsure of.',
        ],
      },
      {
        step: 'Run Academic Review from inside the editor.',
        detail: [
          'Click Run Academic Review in the Writing Assistant panel.',
          'Read every section verdict; improve the Needs Work sections.',
          'Re-run until no section shows Needs Work.',
        ],
      },
      {
        step: 'Save a named version.',
        detail: ['Click Save Version and label it "Final submission".'],
      },
      { step: 'Export DOCX for submission.' },
    ],
  },
  {
    id: 'guide-2',
    title: 'Guide 2 — Final Presentation',
    owner: 'Molly',
    ownerEmail: 'murdockm@queens.edu',
    deadline: '2026-06-03',
    noun: 'presentation',
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
      { step: 'Export PPTX for submission.' },
      { step: 'Run Academic Review against the final deck.' },
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
        step: 'Export scripts.',
        detail: [
          'Click [Export: {Name}] for each speaker’s individual script.',
          'Click [Export Master Script] for the full team version.',
        ],
      },
    ],
  },
]

/** Per-owner deadline data — the login-notification countdown reads this. */
export const SUBMISSION_DEADLINES = GUIDES.map((g) => ({
  ownerEmail: g.ownerEmail,
  deadline: g.deadline,
  noun: g.noun,
}))


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

const TONE_CLASS: Record<string, string> = {
  normal: 'bg-electric/10 text-electric border-electric/30',
  amber: 'bg-warning/10 text-warning border-warning/40',
  red: 'bg-danger/10 text-danger border-danger/40',
  passed: 'bg-navy-700 text-muted border-border',
}

function GuideCard({ guide }: { guide: Guide }) {
  const [open, setOpen] = useState(true)
  const countdown = deadlineCountdown(guide.deadline, guide.noun)
  return (
    <div className="card p-4">
      <button type="button" onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2">
        <span className="text-white font-semibold text-sm">{guide.title}</span>
        {open ? <ChevronDown className="w-4 h-4 text-muted" />
          : <ChevronRight className="w-4 h-4 text-muted" />}
      </button>

      {/* Deadline countdown */}
      <div className={`mt-2 text-2xs font-semibold rounded border px-2 py-1
                       inline-block ${TONE_CLASS[countdown.tone]}`}>
        {countdown.label}
      </div>

      {open && (
        <div className="mt-3 space-y-3">
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
