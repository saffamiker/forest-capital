/**
 * IndependentReviewCard.tsx — May 25 2026.
 *
 * Advisory second-opinion verdict from a SEPARATE agent (Gemini Pro)
 * that saw only the headline findings extracted from the primary
 * arbiter's verdict — no platform context, no analytics, no
 * documents. Renders below the primary verdict card on the Academic
 * Review surface.
 *
 * Three states keyed off `IndependentReview | null`:
 *   - null while the arbiter is still streaming → no card
 *   - landed with verdict → full card (overall reasoning + per-finding)
 *   - verdict = 'Concerns' with model='stub' → render the stub
 *     reasoning prominently so the operator knows this is a fallback
 *
 * NEVER affects the primary score or any gates. The card carries an
 * explicit "Advisory only" line so a reviewer doesn't confuse this
 * with the actual academic readiness verdict.
 */
import { Shield, AlertTriangle, AlertCircle } from 'lucide-react'

import type {
  IndependentReview, IndependentVerdict,
} from '../stores/academicReviewStore'

interface VerdictTone {
  label:    string
  icon:     typeof Shield
  bgClass:  string
  border:   string
  iconCls:  string
}

// Per-verdict styling. Plausible → green; Concerns → amber;
// Implausible → red. All three carry a clear icon so the verdict
// reads at a glance.
const VERDICT_TONE: Record<IndependentVerdict, VerdictTone> = {
  Plausible: {
    label:   'Plausible',
    icon:    Shield,
    bgClass: 'bg-success/10',
    border:  'border-success/40',
    iconCls: 'text-success',
  },
  Concerns: {
    label:   'Concerns',
    icon:    AlertTriangle,
    bgClass: 'bg-warning/10',
    border:  'border-warning/40',
    iconCls: 'text-warning',
  },
  Implausible: {
    label:   'Implausible',
    icon:    AlertCircle,
    bgClass: 'bg-negative/10',
    border:  'border-negative/40',
    iconCls: 'text-negative',
  },
}

interface IndependentReviewCardProps {
  review: IndependentReview | null
}

export default function IndependentReviewCard(
  { review }: IndependentReviewCardProps,
) {
  if (review === null) {
    return null
  }

  const tone = VERDICT_TONE[review.verdict] ?? VERDICT_TONE.Concerns
  const Icon = tone.icon

  return (
    <div
      data-testid="independent-review-card"
      data-verdict={review.verdict}
      className={`card p-4 border-l-[3px] ${tone.border}`}
      style={{ borderLeftColor: 'currentColor' }}
    >
      {/* Header — verdict pill + advisory disclaimer */}
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <Icon className={`w-4 h-4 ${tone.iconCls}`} />
          <h3 className="text-white font-semibold text-sm">
            Independent Review
          </h3>
          <span
            data-testid="independent-verdict-pill"
            className={`text-2xs px-2 py-0.5 rounded-full border
                       ${tone.bgClass} ${tone.iconCls} ${tone.border}`}
          >
            {tone.label}
          </span>
        </div>
        <span className="text-2xs text-muted italic shrink-0">
          Advisory only — does not affect score or gates
        </span>
      </div>

      {/* Model attribution — names which agent produced this verdict
          so the reviewer sees explicitly that it's not the same one
          that produced the primary verdict. */}
      <div className="text-2xs text-muted mb-2">
        Second opinion from <span className="text-text-secondary">
          {review.model}
        </span> — saw ONLY the headline findings as plain text, no
        platform context, no underlying data.
      </div>

      {/* Overall reasoning paragraph */}
      {review.overall_reasoning && (
        <p
          data-testid="independent-overall-reasoning"
          className="text-sm text-text-secondary mb-4"
        >
          {review.overall_reasoning}
        </p>
      )}

      {/* Per-finding assessments */}
      {review.per_finding.length > 0 && (
        <div className="space-y-3">
          {review.per_finding.map((f) => (
            <div
              key={f.finding}
              data-testid={`independent-finding-${f.finding}`}
              className="border-l-2 border-border pl-3"
            >
              <div className="text-2xs uppercase tracking-wide
                              text-muted mb-0.5">
                {f.label}
              </div>
              <div className="text-xs text-text-secondary">
                {f.assessment}
              </div>
              {f.concern && (
                <div
                  data-testid={`independent-concern-${f.finding}`}
                  className="text-xs text-warning mt-1"
                >
                  ⚠ {f.concern}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
