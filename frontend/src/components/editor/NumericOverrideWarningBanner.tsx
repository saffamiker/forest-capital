/**
 * NumericOverrideWarningBanner.tsx -- June 28 2026.
 *
 * Touchpoint 5 of the hard-lock numeric guardrail. Dismissible
 * banner that surfaces when the editor's PATCH save endpoint
 * returns one or more `numeric_warnings` -- untoken-backed
 * numerics introduced via direct editor typing.
 *
 * NON-BLOCKING. The save already succeeded; the banner is the
 * operator's awareness signal. Every offender is also persisted
 * to editor_numeric_overrides (migration 066) for the
 * permanent audit trail.
 *
 * Per warning the banner shows:
 *   - the offending numeric string
 *   - 200 chars of surrounding sentence context
 *   - the closest matching {{TOKEN}} from the substitution
 *     table when severity is 'token_available' (so the
 *     operator can swap for the live-resolving placeholder)
 *
 * Dismiss action clears the local state but does NOT touch the
 * audit-trail rows -- those persist permanently for downstream
 * review.
 */
import { AlertTriangle, X } from 'lucide-react'


export interface NumericOverrideWarning {
  offending_value: string
  sentence:        string
  suggested_token: string | null
  severity:        'token_available' | 'unsupported'
}


export interface NumericOverrideWarningBannerProps {
  warnings:  NumericOverrideWarning[]
  onDismiss: () => void
}


export default function NumericOverrideWarningBanner(
  { warnings, onDismiss }: NumericOverrideWarningBannerProps,
): React.ReactElement | null {
  if (warnings.length === 0) return null
  return (
    <section
      data-testid="numeric-override-warning-banner"
      className="rounded border border-warning/40 bg-warning/5
                 p-3 mb-3 text-2xs text-warning relative">
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss warning"
        data-testid="numeric-override-warning-dismiss"
        className="absolute top-2 right-2 text-muted
                   hover:text-white">
        <X className="w-3.5 h-3.5" />
      </button>
      <div className="flex items-start gap-1.5 pr-6">
        <AlertTriangle
          className="w-4 h-4 text-warning shrink-0 mt-0.5" />
        <div className="flex-1">
          <div className="font-medium text-slate-100 mb-1">
            {warnings.length} untoken-backed numeric value{
              warnings.length === 1 ? '' : 's'} in saved content
          </div>
          <p className="text-slate-300 leading-relaxed">
            The values below were saved into the draft but are not
            backed by a token in the substitution table. The save
            succeeded; this is a warning, not a block. Every
            offender has been logged for audit. Review each value
            and either swap for the suggested token (when
            available) or rephrase the sentence.
          </p>
          <ul className="mt-2 space-y-1.5">
            {warnings.map((w, i) => (
              <li
                key={i}
                data-testid={`numeric-override-warning-row-${i}`}
                className="border-t border-warning/20 pt-1.5
                           first:border-t-0 first:pt-0">
                <div className="font-mono text-slate-100">
                  {w.offending_value}
                </div>
                <div className="text-slate-400 italic">
                  ...{w.sentence}...
                </div>
                {w.severity === 'token_available'
                    && w.suggested_token ? (
                  <div className="text-slate-300 mt-0.5">
                    Suggested swap:{' '}
                    <code className="font-mono text-electric">
                      {w.suggested_token}
                    </code>
                  </div>
                ) : (
                  <div className="text-slate-400 mt-0.5 italic">
                    No matching token; consider rephrasing.
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  )
}
