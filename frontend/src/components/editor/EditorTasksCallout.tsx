/**
 * EditorTasksCallout — the "YOUR TASKS" panel at the top of the editor.
 *
 * Two modes:
 *
 *   Pre-review (no completed review for this draft)
 *     Static list of generic checklist items, owner-specific (Bob's
 *     for paper/brief/appendix, Molly's for deck/script). The
 *     pre-review copy hasn't changed and is appropriate for any
 *     draft that hasn't been council-reviewed yet.
 *
 *   Post-review (academic-review-status.has_review === true)
 *     Dynamic task list built from the latest review's findings:
 *       1. One task per FATAL / MAJOR critic finding (HIGH priority)
 *       2. One task per section rated "Needs Work" in section_ratings
 *       3. Fixed closing tasks (re-run review, save version, export)
 *       4. MINOR critic findings collapsed under "N additional
 *          improvements" expandable section so the main list stays
 *          focused on blockers.
 *     Each task has a checkbox persisted in localStorage by
 *     (draftId, taskId) so the state survives page reload.
 *     Banner title changes by state:
 *       - HIGH items unresolved -> "BOB -- ACTION REQUIRED BEFORE
 *           SUBMISSION (N items)" in amber
 *       - All HIGH resolved, Needs Work remain -> "BOB -- ALMOST
 *           THERE (N items)" in amber
 *       - All clear -> "BOB -- READY TO SUBMIT" in green
 *
 * Dismissible per draft (sessionStorage); the dismissal applies to
 * both modes -- if Bob has cleared the banner he doesn't need it
 * back when a review lands.
 */
import { useEffect, useMemo, useState } from 'react'
import axios from 'axios'
import {
  ClipboardList, X, ChevronDown, ChevronRight,
  CheckCircle,
} from 'lucide-react'

import type { EditorDocumentType } from '../../types/editor'
import {
  useAcademicReviewStore, type CriticFinding,
} from '../../stores/academicReviewStore'


const TRACKING_NOTE =
  'Everything you do here is tracked. Every edit, every resolved marker, '
  + 'every version save is part of your submission record. Make it count.'

const MOLLY_TRACKING_NOTE =
  'Everything you do here is tracked. Every edit, every verified data '
  + 'point, every set of speaker notes is part of your submission '
  + 'record. Make it count.'


interface StaticTaskSet {
  owner: string
  note: string
  tasks: string[]
}


const STATIC_TASKS:
  Partial<Record<EditorDocumentType, StaticTaskSet>> = {
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
      'Run Presentation Preview and rehearse before the July 1st '
        + 'final presentation. Use Rehearsal Mode again before the '
        + 'July 3rd panel.',
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
  analytical_appendix: {
    owner: 'BOB',
    note: TRACKING_NOTE,
    tasks: [
      'Read through each section\'s introduction paragraph',
      'Verify every table value against the live Analytics page',
      'Resolve every amber marker before submitting',
      'Confirm the data hash in the Reproducibility section matches '
        + 'the current strategy_results_cache row',
      'Save a named version before submitting',
      'Export DOCX for the Analytical Appendix submission',
    ],
  },
}


interface DynamicTask {
  id:       string
  text:     string
  priority: 'high' | 'needswork' | 'fixed'
}


interface Props {
  documentType: EditorDocumentType
  draftId:      number
}


// Stable id for a critic finding so the checkbox state keyed off it
// survives page reloads. Hash-light approach: lowercase + collapse
// whitespace + cap to 80 chars; collision risk is negligible at the
// per-draft scale (a draft has dozens of findings at most).
function _findingId(f: CriticFinding): string {
  const parts = [
    f.severity || 'X',
    f.category || '',
    f.location || '',
    (f.description || '').slice(0, 80),
  ]
  return parts.join('|').toLowerCase().replace(/\s+/g, ' ').trim()
}


function _truncate(s: string, n: number): string {
  if (!s) return ''
  return s.length > n ? `${s.slice(0, n).trim()}…` : s
}


function _ownerForType(t: EditorDocumentType): string {
  if (t === 'presentation_deck' || t === 'presentation_script') {
    return 'MOLLY'
  }
  return 'BOB'
}


export default function EditorTasksCallout({ documentType, draftId }: Props) {
  const dismissKey = `fc_editor_tasks_dismissed_${draftId}`
  const checkedKey = `fc_editor_tasks_completed_${draftId}`
  const expandedKey = `fc_editor_tasks_expanded_${draftId}`

  const [dismissed, setDismissed] = useState<boolean>(
    () => sessionStorage.getItem(dismissKey) === '1')
  const [checked, setChecked] = useState<Record<string, boolean>>(
    () => {
      try {
        return JSON.parse(localStorage.getItem(checkedKey) || '{}')
      } catch { return {} }
    })
  const [extraExpanded, setExtraExpanded] = useState<boolean>(
    () => localStorage.getItem(expandedKey) === '1')
  const [hasReview, setHasReview] = useState<boolean>(false)
  const [sectionRatings, setSectionRatings] =
    useState<Record<string, string>>({})

  // The in-session per-doc slice carries the criticResult populated
  // when a review streamed in this tab. We use it directly when
  // present (one source of truth for the freshest findings); when the
  // editor opened cold and a review previously completed, the slice
  // is empty and we fall back to section_ratings only (HIGH tasks
  // can't be reconstructed from /academic-review-status alone, which
  // is a deliberate compromise -- the user spec accepts this).
  const slice = useAcademicReviewStore((s) => s.perDocument[documentType])
  const criticResult = slice?.result?.criticResult ?? null

  useEffect(() => {
    let cancelled = false
    async function fetchStatus() {
      try {
        const r = await axios.get<{
          has_review?:      boolean
          section_ratings?: Record<string, string>
        }>(
          `/api/v1/documents/drafts/${draftId}`
          + '/academic-review-status')
        if (cancelled) return
        setHasReview(Boolean(r.data?.has_review))
        setSectionRatings(r.data?.section_ratings || {})
      } catch {
        if (!cancelled) {
          setHasReview(false)
          setSectionRatings({})
        }
      }
    }
    void fetchStatus()
    return () => { cancelled = true }
  }, [draftId])

  // Persist checked state on every flip.
  useEffect(() => {
    try {
      localStorage.setItem(checkedKey, JSON.stringify(checked))
    } catch { /* quota */ }
  }, [checked, checkedKey])

  useEffect(() => {
    localStorage.setItem(expandedKey, extraExpanded ? '1' : '0')
  }, [extraExpanded, expandedKey])

  const dynamic = useMemo<{
    high:     DynamicTask[]
    medium:   DynamicTask[]
    needswork: DynamicTask[]
    fixed:    DynamicTask[]
  } | null>(() => {
    if (!hasReview) return null
    const merged = criticResult?.merged_findings ?? []
    const high: DynamicTask[] = []
    const medium: DynamicTask[] = []
    for (const f of merged) {
      const sev = (f.severity || '').toString()
      const id = _findingId(f)
      const loc = f.location ? `${f.location}: ` : ''
      const desc = _truncate(
        (f.recommendation || f.description || '').trim(), 240)
      const text = `${loc}${desc}` || 'Address critic finding'
      if (sev === 'Fatal' || sev === 'Major') {
        high.push({ id, text, priority: 'high' })
      } else if (sev === 'Minor') {
        medium.push({ id, text, priority: 'high' })
      }
    }
    const needswork: DynamicTask[] = []
    const ratingsOrdered = Object.entries(sectionRatings)
      .filter(([, r]) =>
        (r || '').toLowerCase().includes('needs work'))
    for (const [section, _r] of ratingsOrdered) {
      void _r
      const cleanName = section.replace(/_/g, ' ')
        .replace(/^[a-z]/, (c) => c.toUpperCase())
      needswork.push({
        id: `needswork-${section}`,
        text: `${cleanName} — rated Needs Work. Strengthen this `
          + 'section based on the council\'s rubric guidance.',
        priority: 'needswork',
      })
    }
    const fixed: DynamicTask[] = [
      {
        id: 'fixed-rerun',
        text: 'Run Academic Review again after fixes — target no '
          + 'Needs Work sections.',
        priority: 'fixed',
      },
      {
        id: 'fixed-version',
        text: 'Save a named version before submitting.',
        priority: 'fixed',
      },
      {
        id: 'fixed-export',
        text: 'Export DOCX for submission.',
        priority: 'fixed',
      },
    ]
    return { high, medium, needswork, fixed }
  }, [hasReview, criticResult, sectionRatings])

  if (dismissed) return null

  const owner = _ownerForType(documentType)

  // Pre-review fallback -- existing static list. Stay close to the
  // legacy chrome so the banner reads identically until a review
  // lands. midpoint_paper is retired and excluded.
  if (!dynamic || !hasReview) {
    const set = STATIC_TASKS[documentType]
    if (!set) return null
    return (
      <div
        data-testid="editor-tasks-callout-static"
        className="m-3 rounded border border-warning/40
                   bg-warning/10 p-3">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-1.5 text-warning
                          font-semibold text-xs">
            <ClipboardList className="w-3.5 h-3.5" />
            {set.owner} — YOUR TASKS
          </div>
          <button type="button" aria-label="Dismiss"
            onClick={() => {
              sessionStorage.setItem(dismissKey, '1')
              setDismissed(true)
            }}
            className="text-warning/70 hover:text-warning">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
        <p className="text-xs text-amber-100/90 mt-1.5">{set.note}</p>
        <ol className="mt-2 space-y-0.5 text-xs text-amber-100/90
                       list-decimal pl-5">
          {set.tasks.map((t) => <li key={t}>{t}</li>)}
        </ol>
      </div>
    )
  }

  // Post-review dynamic mode.
  const highCount = dynamic.high.length
  const needsworkCount = dynamic.needswork.length
  const highOpen = dynamic.high.filter((t) => !checked[t.id]).length
  const allHighDone = highCount > 0 && highOpen === 0
  const allClear = highOpen === 0 && needsworkCount === 0

  let title: string
  let titleColour: string
  let chrome: string
  if (allClear) {
    title = `${owner} — READY TO SUBMIT`
    titleColour = 'text-success'
    chrome = 'border-success/40 bg-success/10'
  } else if (allHighDone) {
    title = `${owner} — ALMOST THERE (${needsworkCount} items)`
    titleColour = 'text-warning'
    chrome = 'border-warning/40 bg-warning/10'
  } else if (highCount > 0) {
    title = `${owner} — ACTION REQUIRED BEFORE SUBMISSION (${highOpen} items)`
    titleColour = 'text-warning'
    chrome = 'border-warning/40 bg-warning/10'
  } else if (needsworkCount > 0) {
    title = `${owner} — ALMOST THERE (${needsworkCount} items)`
    titleColour = 'text-warning'
    chrome = 'border-warning/40 bg-warning/10'
  } else {
    title = `${owner} — READY TO SUBMIT`
    titleColour = 'text-success'
    chrome = 'border-success/40 bg-success/10'
  }

  const toggle = (id: string): void => {
    setChecked((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const renderTask = (t: DynamicTask) => (
    <li key={t.id} className="flex items-start gap-2 mb-1">
      <input type="checkbox" checked={Boolean(checked[t.id])}
        onChange={() => toggle(t.id)}
        data-testid={`task-checkbox-${t.id}`}
        className="mt-0.5 w-3.5 h-3.5 shrink-0" />
      <span className={
        checked[t.id]
          ? 'line-through text-muted'
          : 'text-amber-100/90'}>
        {t.text}
      </span>
    </li>
  )

  return (
    <div
      data-testid="editor-tasks-callout-dynamic"
      className={`m-3 rounded border ${chrome} p-3`}>
      <div className="flex items-start justify-between gap-3">
        <div className={`flex items-center gap-1.5 font-semibold
                         text-xs ${titleColour}`}>
          {allClear
            ? <CheckCircle className="w-3.5 h-3.5" />
            : <ClipboardList className="w-3.5 h-3.5" />}
          {title}
        </div>
        <button type="button" aria-label="Dismiss"
          onClick={() => {
            sessionStorage.setItem(dismissKey, '1')
            setDismissed(true)
          }}
          className="text-amber-100/70 hover:text-amber-100">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      {dynamic.high.length > 0 && (
        <ul className="mt-2 text-xs space-y-0">
          {dynamic.high.map(renderTask)}
        </ul>
      )}
      {dynamic.needswork.length > 0 && (
        <>
          {dynamic.high.length > 0 && (
            <div className="border-t border-warning/20 my-2" />
          )}
          <ul className="text-xs space-y-0">
            {dynamic.needswork.map(renderTask)}
          </ul>
        </>
      )}

      {/* Fixed closing tasks always shown post-review. */}
      <div className="border-t border-warning/20 my-2" />
      <ul className="text-xs space-y-0">
        {dynamic.fixed.map(renderTask)}
      </ul>

      {/* Minor items collapsed under expandable. */}
      {dynamic.medium.length > 0 && (
        <div className="mt-2">
          <button type="button"
            onClick={() => setExtraExpanded(!extraExpanded)}
            data-testid="editor-tasks-medium-toggle"
            className="flex items-center gap-1 text-2xs text-muted
                       hover:text-amber-100/90">
            {extraExpanded
              ? <ChevronDown className="w-3 h-3" />
              : <ChevronRight className="w-3 h-3" />}
            {dynamic.medium.length} additional improvement
            {dynamic.medium.length === 1 ? '' : 's'}
          </button>
          {extraExpanded && (
            <ul className="text-xs space-y-0 mt-1.5 pl-4">
              {dynamic.medium.map(renderTask)}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
