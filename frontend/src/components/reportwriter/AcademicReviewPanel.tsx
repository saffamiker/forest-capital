/**
 * frontend/src/components/reportwriter/AcademicReviewPanel.tsx
 *
 * Displays the academic review results inline below the editor.
 * Four criteria in a 2x2 grid with score badges (Strong / Developing
 * / Needs Work). Additional flag lists for data gaps, citation gaps,
 * tone violations, thesis coherence, length compliance. Overall
 * readiness badge at the top.
 */
import { ChevronDown, ChevronUp, AlertTriangle, CheckCircle, XCircle } from 'lucide-react'
import { useState } from 'react'

type Score = 'strong' | 'developing' | 'needs_work' | string
type Readiness =
  | 'ready_to_submit'
  | 'needs_minor_revision'
  | 'needs_significant_revision'
  | string

export interface CriterionScore {
  criterion_id: string
  score: Score
  evidence: string
  gap: string
  suggestion: string
}

export interface AcademicReview {
  per_criterion: CriterionScore[]
  data_gaps: string[]
  citation_gaps: string[]
  thesis_coherence: string[]
  tone_violations: string[]
  length_compliance: string[]
  readiness: Readiness
  summary: string
}

interface Props {
  review: AcademicReview | null
  loading?: boolean
  onApplySuggestion?:
    | ((criterionId: string, suggestion: string) => void)
    | undefined
}

const CRITERION_LABEL: Record<string, string> = {
  clarity_and_rigor:   'Clarity and rigor',
  analytical_progress: 'Analytical progress',
  results_quality:     'Results quality',
  division_of_labor:   'Division of labor',
}

const SCORE_STYLE: Record<Score, string> = {
  strong:     'bg-green-500/15 text-green-300 border-green-500/40',
  developing: 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  needs_work: 'bg-red-500/15 text-red-300 border-red-500/40',
}

const READINESS_STYLE: Record<Readiness, string> = {
  ready_to_submit:
    'bg-green-500/20 text-green-200 border-green-500/40',
  needs_minor_revision:
    'bg-amber-500/20 text-amber-200 border-amber-500/40',
  needs_significant_revision:
    'bg-red-500/20 text-red-200 border-red-500/40',
}

const READINESS_LABEL: Record<Readiness, string> = {
  ready_to_submit:             'Ready to submit',
  needs_minor_revision:        'Needs minor revision',
  needs_significant_revision:  'Needs significant revision',
}

export default function AcademicReviewPanel({
  review, loading, onApplySuggestion,
}: Props) {
  if (loading) {
    return (
      <div className="p-4 bg-navy-900 border border-navy-700 rounded">
        <p className="text-text-secondary text-sm">
          Running academic review…
        </p>
      </div>
    )
  }
  if (!review) return null

  const readinessClass =
    READINESS_STYLE[review.readiness] ?? READINESS_STYLE.needs_minor_revision
  const readinessLabel =
    READINESS_LABEL[review.readiness] ?? review.readiness

  return (
    <section
      data-testid="academic-review-panel"
      className="space-y-4">
      <header className="flex items-center justify-between flex-wrap gap-2">
        <h3 className="text-white font-semibold text-base">
          Academic Review Results
        </h3>
        <span
          className={`px-3 py-1 border rounded text-xs font-medium ${readinessClass}`}
          data-testid="academic-review-readiness">
          {readinessLabel}
        </span>
      </header>

      {review.summary ? (
        <p className="text-text-secondary text-sm italic">{review.summary}</p>
      ) : null}

      {/* Per-criterion 2x2 grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {review.per_criterion.map((c) => (
          <CriterionCard
            key={c.criterion_id}
            criterion={c}
            onApplySuggestion={onApplySuggestion}
          />
        ))}
      </div>

      <FlagList title="Data gaps" items={review.data_gaps} />
      <FlagList title="Citation gaps" items={review.citation_gaps} />
      <FlagList title="Thesis coherence" items={review.thesis_coherence} />
      <FlagList title="Tone violations" items={review.tone_violations} />
      <FlagList title="Length compliance" items={review.length_compliance} />
    </section>
  )
}


function CriterionCard({
  criterion, onApplySuggestion,
}: {
  criterion: CriterionScore
  onApplySuggestion?:
    | ((criterionId: string, suggestion: string) => void)
    | undefined
}) {
  const [open, setOpen] = useState(false)
  const label = CRITERION_LABEL[criterion.criterion_id] ?? criterion.criterion_id
  const cls = SCORE_STYLE[criterion.score] ?? SCORE_STYLE.developing

  return (
    <div className="p-3 bg-navy-900 border border-navy-700 rounded">
      <header className="flex items-center justify-between mb-2">
        <h4 className="text-white font-medium text-sm">{label}</h4>
        <span
          className={`px-2 py-0.5 border rounded text-2xs font-medium ${cls}`}>
          {criterion.score.replace('_', ' ')}
        </span>
      </header>
      {criterion.gap ? (
        <p className="text-text-secondary text-xs mb-2">{criterion.gap}</p>
      ) : null}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-electric-blue hover:text-electric-blue/80 text-xs flex items-center gap-1">
        {open ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        {open ? 'Hide' : 'Show'} suggestion
      </button>
      {open ? (
        <div className="mt-2 p-2 bg-navy-950 border-l-2 border-electric-blue rounded">
          {criterion.evidence ? (
            <p className="text-text-muted text-2xs italic mb-1">
              Evidence: {criterion.evidence}
            </p>
          ) : null}
          <p className="text-text-secondary text-xs whitespace-pre-wrap">
            {criterion.suggestion}
          </p>
          {onApplySuggestion && criterion.suggestion ? (
            <button
              type="button"
              onClick={() => onApplySuggestion(criterion.criterion_id, criterion.suggestion)}
              className={
                'mt-2 px-2 py-1 bg-electric-blue/15 ' +
                'hover:bg-electric-blue/25 text-electric-blue ' +
                'text-2xs rounded'
              }>
              Apply suggestion
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}


function FlagList({ title, items }: { title: string; items: string[] }) {
  if (!items || items.length === 0) return null
  return (
    <div className="p-3 bg-amber-500/5 border border-amber-500/30 rounded">
      <h4 className="text-amber-300 font-medium text-sm flex items-center gap-1.5 mb-1.5">
        <AlertTriangle className="w-3.5 h-3.5" />
        {title}
      </h4>
      <ul className="space-y-1">
        {items.map((item, i) => (
          <li key={i} className="text-amber-100/80 text-xs">• {item}</li>
        ))}
      </ul>
    </div>
  )
}

export { CheckCircle, XCircle }
