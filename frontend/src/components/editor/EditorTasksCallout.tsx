/**
 * EditorTasksCallout — the amber "YOUR TASKS" panel shown at the top of
 * the editor when a draft is first opened.
 *
 * It carries the tracking note (everything done in the editor is part of
 * the submission record) and the task checklist for the document type —
 * Bob's for a paper/brief, Molly's for a presentation deck. Dismissible
 * per draft (the dismissal is remembered in sessionStorage).
 */
import { useState } from 'react'
import { ClipboardList, X } from 'lucide-react'

import type { EditorDocumentType } from '../../types/editor'

const TRACKING_NOTE =
  'Everything you do here is tracked. Every edit, every resolved marker, '
  + 'every version save is part of your submission record. Make it count.'

const MOLLY_TRACKING_NOTE =
  'Everything you do here is tracked. Every edit, every verified data '
  + 'point, every set of speaker notes is part of your submission '
  + 'record. Make it count.'

interface TaskSet {
  owner: string
  note: string
  tasks: string[]
}

const TASKS: Record<EditorDocumentType, TaskSet> = {
  midpoint_paper: {
    owner: 'BOB',
    note: TRACKING_NOTE,
    tasks: [
      'Resolve every amber marker (verify the data points)',
      'Complete every BOB callout (add your own analysis)',
      'Run Academic Review and reach no Needs Work sections',
      'Save a named version before submitting',
      'Export DOCX for submission',
    ],
  },
  executive_brief: {
    owner: 'BOB',
    note: TRACKING_NOTE,
    tasks: [
      'Resolve every amber marker (verify the data points)',
      'Complete every BOB callout (add your own analysis)',
      'Run Academic Review and reach no Needs Work sections',
      'Save a named version before submitting',
      'Export DOCX for submission',
    ],
  },
  presentation_deck: {
    owner: 'MOLLY',
    note: MOLLY_TRACKING_NOTE,
    tasks: [
      'Verify every amber data point on each slide',
      'Write your speaker notes for every slide',
      'Use Generate Talking Points for a starting point — rewrite in '
        + 'your own voice',
      'Run Presentation Preview and rehearse before June 3rd',
    ],
  },
  presentation_script: {
    owner: 'MOLLY',
    note: 'This script was generated from your deck and academic context. '
      + 'It is a starting point — rewrite every section in your own voice '
      + 'before rehearsing.',
    tasks: [
      'Read through the full script',
      'Rewrite each section in your own voice',
      'Time yourself — aim for 20-25 minutes',
      'Export individual scripts for each speaker',
      'Export the master script for the full team',
    ],
  },
}

interface Props {
  documentType: EditorDocumentType
  draftId: number
}

export default function EditorTasksCallout({ documentType, draftId }: Props) {
  const key = `fc_editor_tasks_dismissed_${draftId}`
  const [dismissed, setDismissed] = useState<boolean>(
    () => sessionStorage.getItem(key) === '1')

  if (dismissed) return null
  const set = TASKS[documentType]

  return (
    <div className="m-3 rounded border border-warning/40 bg-warning/10 p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-1.5 text-warning font-semibold text-xs">
          <ClipboardList className="w-3.5 h-3.5" />
          {set.owner} — YOUR TASKS
        </div>
        <button type="button" aria-label="Dismiss"
          onClick={() => { sessionStorage.setItem(key, '1'); setDismissed(true) }}
          className="text-warning/70 hover:text-warning">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      <p className="text-xs text-amber-100/90 mt-1.5">{set.note}</p>
      <ol className="mt-2 space-y-0.5 text-xs text-amber-100/90 list-decimal pl-5">
        {set.tasks.map((t) => <li key={t}>{t}</li>)}
      </ol>
    </div>
  )
}
