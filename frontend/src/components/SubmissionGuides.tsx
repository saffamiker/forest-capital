/**
 * SubmissionGuides — the two deliverable submission guides on the
 * Reports screen.
 *
 * Guide 1 (Bob, the midpoint paper) and Guide 2 (Molly, the final
 * presentation) each walk the editor-based workflow: generate → open in
 * the editor → resolve markers / write notes → Academic Review → export.
 * Both guides lead with the tracking note — work done on the platform is
 * the documented contribution record.
 */
import { useState } from 'react'
import { BookOpen, ChevronDown, ChevronRight } from 'lucide-react'

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
  steps: Step[]
}

const GUIDES: Guide[] = [
  {
    id: 'guide-1',
    title: 'Guide 1 — Midpoint Paper',
    owner: 'Bob',
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
    ],
  },
]

function GuideCard({ guide }: { guide: Guide }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="card p-4">
      <button type="button" onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2">
        <span className="flex items-center gap-2">
          <BookOpen className="w-4 h-4 text-electric" />
          <span className="text-white font-semibold text-sm">{guide.title}</span>
          <span className="text-2xs text-muted uppercase tracking-wide">
            {guide.owner}
          </span>
        </span>
        {open ? <ChevronDown className="w-4 h-4 text-muted" />
          : <ChevronRight className="w-4 h-4 text-muted" />}
      </button>

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

export default function SubmissionGuides() {
  return (
    <section>
      <div className="flex items-baseline gap-3 mb-3">
        <h2 className="text-white font-semibold text-sm">Submission Guides</h2>
        <span className="text-2xs text-muted uppercase tracking-wide">
          The editor-based workflow for each deliverable
        </span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {GUIDES.map((g) => <GuideCard key={g.id} guide={g} />)}
      </div>
    </section>
  )
}
