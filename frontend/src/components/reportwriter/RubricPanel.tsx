/**
 * frontend/src/components/reportwriter/RubricPanel.tsx
 *
 * Collapsible panel showing the active rubric for the selected
 * template. Reader can reference the criteria at any point during
 * editing without leaving the page.
 *
 * For the FNA670 midpoint template the rubric is seeded by migration
 * 032 — clarity_and_rigor / analytical_progress / results_quality /
 * division_of_labor. Other templates upload their own rubric via the
 * Upload Rubric button.
 */
import { useState } from 'react'
import { ChevronDown, ChevronUp, FileText } from 'lucide-react'

export interface RubricCriterion {
  criterion_id: string
  section?: string | null
  description: string
  weight?: number | null
  indicators_of_success?: string[]
}

export interface Rubric {
  id: number
  template_id: string
  version: number
  rubric_text: string
  criteria: RubricCriterion[]
  uploaded_by?: string | null
  source_filename?: string | null
  uploaded_at?: string | null
}

interface Props {
  rubric: Rubric | null
  formatSpec?: Record<string, unknown> | null
}

const CRITERION_LABEL: Record<string, string> = {
  clarity_and_rigor:   'Clarity and rigor',
  analytical_progress: 'Analytical progress',
  results_quality:     'Results quality',
  division_of_labor:   'Division of labor',
}

export default function RubricPanel({ rubric, formatSpec }: Props) {
  const [expanded, setExpanded] = useState(false)

  if (!rubric) {
    return (
      <div className="p-3 bg-navy-900 border border-navy-700 rounded">
        <p className="text-text-muted text-xs italic">
          No rubric uploaded for this template yet.
        </p>
      </div>
    )
  }

  return (
    <section
      data-testid="rubric-panel"
      className="bg-navy-900 border border-navy-700 rounded">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={
          'w-full flex items-center justify-between p-3 ' +
          'hover:bg-navy-800 transition-colors'
        }>
        <span className="flex items-center gap-2">
          <FileText className="w-4 h-4 text-electric-blue" />
          <span className="text-white font-medium text-sm">
            Grading rubric
          </span>
          <span className="text-text-muted text-2xs">
            v{rubric.version}
          </span>
        </span>
        {expanded ? (
          <ChevronUp className="w-4 h-4 text-text-secondary" />
        ) : (
          <ChevronDown className="w-4 h-4 text-text-secondary" />
        )}
      </button>
      {expanded ? (
        <div className="p-3 pt-0 space-y-3">
          {/* Criteria */}
          <div className="space-y-2">
            {rubric.criteria.map((c) => (
              <details
                key={c.criterion_id}
                className="bg-navy-950 border border-navy-700 rounded p-2">
                <summary className="text-white text-sm font-medium cursor-pointer">
                  {CRITERION_LABEL[c.criterion_id] ?? c.criterion_id}
                </summary>
                <p className="text-text-secondary text-xs mt-2">
                  {c.description}
                </p>
                {c.indicators_of_success && c.indicators_of_success.length > 0 ? (
                  <ul className="mt-2 space-y-1">
                    {c.indicators_of_success.map((ind, i) => (
                      <li key={i} className="text-text-muted text-2xs">
                        • {ind}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </details>
            ))}
          </div>

          {/* Format requirements from format_spec */}
          {formatSpec ? (
            <div className="p-2 bg-navy-950 border border-navy-700 rounded">
              <h4 className="text-white text-xs font-medium mb-1.5">
                Format requirements
              </h4>
              <ul className="space-y-0.5">
                {Object.entries(formatSpec).map(([k, v]) => (
                  <li key={k} className="text-text-muted text-2xs">
                    <span className="text-text-secondary">{k}:</span>{' '}
                    {String(v)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {rubric.source_filename ? (
            <p className="text-text-muted text-2xs italic">
              Uploaded from {rubric.source_filename}
              {rubric.uploaded_by ? ` by ${rubric.uploaded_by}` : ''}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
