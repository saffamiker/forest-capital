/**
 * AuditWarningsBanner — surfaces the post-generation audit flags.
 *
 * Rendered above the editor toolbar when a freshly-generated draft
 * carries audit_warnings (PR June 3 2026 / migration 051). Shows a
 * single-line summary with a count badge per check, and a
 * "Show details" expander that lists the per-flag detail.
 *
 * The banner is INFORMATIONAL. It never blocks editing or saving —
 * the user reviews, decides whether the flag is a real issue, and
 * fixes the prose accordingly. Dismissing the banner is a
 * session-local choice (sessionStorage keyed on draft id); the
 * audit_warnings column on the row stays so a re-open re-surfaces.
 */
import { useState } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, X } from 'lucide-react'
import type { AuditFlag, AuditWarnings } from '../../types/editor'


interface Props {
  draftId: number
  audit: AuditWarnings
}


function dismissKey(draftId: number): string {
  return `fc_audit_dismissed_${draftId}`
}


export default function AuditWarningsBanner({ draftId, audit }: Props) {
  const [expanded, setExpanded] = useState(false)
  const [dismissed, setDismissed] = useState<boolean>(
    () => sessionStorage.getItem(dismissKey(draftId)) === '1')
  if (dismissed) return null
  const counts = audit.flag_counts
  if (!counts || counts.total <= 0) return null

  const dismiss = () => {
    sessionStorage.setItem(dismissKey(draftId), '1')
    setDismissed(true)
  }

  return (
    <div className="border-b border-warning/40 bg-warning/10 text-warning
                    text-xs px-3 py-2"
         data-testid="audit-warnings-banner">
      <div className="flex items-center justify-between gap-3">
        <button type="button"
          onClick={() => setExpanded((e) => !e)}
          className="flex items-center gap-2 text-left flex-1 min-w-0">
          {expanded
            ? <ChevronDown className="w-3.5 h-3.5 shrink-0" />
            : <ChevronRight className="w-3.5 h-3.5 shrink-0" />}
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span className="font-semibold">
            Audit flagged {counts.total} item{counts.total === 1 ? '' : 's'}
          </span>
          <span className="opacity-80 hidden sm:inline">
            ({counts.numeric} numeric · {counts.direction} direction ·{' '}
            {counts.consistency} consistency · {counts.citation} citation)
          </span>
          <span className="opacity-80 sm:hidden">
            ({counts.numeric}/{counts.direction}/{counts.consistency}/{counts.citation})
          </span>
        </button>
        <button type="button" onClick={dismiss}
          aria-label="Dismiss for this session"
          className="text-warning/70 hover:text-warning shrink-0">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      {expanded && (
        <div className="mt-2 space-y-2">
          <FlagGroup title="Numeric cross-reference"
            description="Cited number disagrees with the cache by > 0.005."
            flags={audit.flags_by_check.numeric}
            render={(f) => (
              <>
                <span className="font-mono text-white/90">
                  {String(f.strategy)} · {String(f.metric)}
                </span>
                <span className="ml-2">
                  generated <code className="text-warning">{String(f.generated)}</code>
                  {' '}vs cache{' '}
                  <code className="text-white/80">{String(f.cache)}</code>
                </span>
              </>
            )} />
          <FlagGroup title="Label direction"
            description="Superlative paired with a loss metric — ambiguous direction."
            flags={audit.flags_by_check.direction}
            render={(f) => (
              <>
                <span className="font-mono text-white/90">
                  &quot;{String(f.superlative)}&quot; · {String(f.metric)}
                </span>
                <div className="text-white/70 mt-0.5 italic">
                  {String(f.sentence)}
                </div>
              </>
            )} />
          <FlagGroup title="Cross-section consistency"
            description="Same (strategy, metric) carries values >0.05 apart across sections — add a window label if these come from different periods."
            flags={audit.flags_by_check.consistency}
            render={(f) => (
              <>
                <span className="font-mono text-white/90">
                  {String(f.strategy)} · {String(f.metric)}
                </span>
                <span className="ml-2">
                  values <code className="text-warning">
                    {Array.isArray(f.values) ? (f.values as number[]).join(', ') : '?'}
                  </code>
                  {' '}(spread{' '}
                  <code>{String(f.spread)}</code>)
                </span>
              </>
            )} />
          <FlagGroup title="Citation completeness"
            description="Author cited in the body but not present in the References section."
            flags={audit.flags_by_check.citation}
            render={(f) => (
              <span className="font-mono text-white/90">
                {String(f.author)} ({String(f.year)})
              </span>
            )} />
          {audit.skipped && Object.keys(audit.skipped).length > 0 && (
            <div className="text-2xs text-warning/80 border-t border-warning/30 pt-1.5">
              <span className="font-semibold">Skipped:</span>{' '}
              {Object.entries(audit.skipped).map(([check, reason], i, arr) => (
                <span key={check}>
                  {check} ({reason}){i < arr.length - 1 ? ' · ' : ''}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}


interface FlagGroupProps {
  title: string
  description: string
  flags: AuditFlag[]
  render: (flag: AuditFlag) => React.ReactNode
}


function FlagGroup({ title, description, flags, render }: FlagGroupProps) {
  if (!flags || flags.length === 0) return null
  return (
    <div className="border-l-2 border-warning/40 pl-2">
      <div className="text-2xs font-semibold uppercase tracking-wide text-warning">
        {title} ({flags.length})
      </div>
      <div className="text-2xs text-white/60 mb-1">{description}</div>
      <ul className="space-y-1 text-2xs">
        {flags.map((flag, i) => (
          <li key={i} className="text-white/80">
            {render(flag)}
          </li>
        ))}
      </ul>
    </div>
  )
}
