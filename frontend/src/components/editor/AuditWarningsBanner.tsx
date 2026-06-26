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
 *
 * June 26 2026 -- expanded view now renders all NINE check
 * categories (was four). The backend has been emitting story_plan,
 * required_citations, section_word_count, unresolved_placeholders,
 * and raw_numeric flags into flags_by_check, and counts.total sums
 * them -- but the previous banner only rendered FlagGroup blocks
 * for numeric / direction / consistency / citation. A draft with
 * 11 story_plan flags + 3 numeric flags showed "Audit flagged 14
 * items" in the header but only the 3 numeric ones in the
 * expander. Now every category renders. Skip-reason raw codes
 * also get human-readable labels (see SKIPPED_REASON_LABELS).
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


// Human-readable labels for the skipped-check name + reason
// codes. Keep this map narrow and authoritative; an unknown code
// falls back to the raw value with a one-line note so the user
// still sees something useful.
const SKIPPED_CHECK_LABELS: Record<string, string> = {
  numeric:                 'Numeric cross-reference',
  direction:               'Label direction',
  consistency:             'Cross-section consistency',
  citation:                'Citation completeness',
  story_plan:              'Story plan alignment',
  required_citations:      'Required citations',
  section_word_count:      'Section word count',
  unresolved_placeholders: 'Unresolved placeholders',
  raw_numeric:             'Raw numeric tokens',
}

const SKIPPED_REASON_LABELS: Record<string, string> = {
  substitution_architecture_supersedes_this_check:
    ('Skipped because the substitution-architecture checks '
     + 'already cover this invariant by construction.'),
  no_plan_or_no_slides:
    ('No story plan or slide context was available, so the '
     + 'check could not run.'),
  not_a_brief:
    ('Only applies to the executive brief; this document type '
     + 'is exempt.'),
  no_manifest_or_helper_unavailable:
    ('No value manifest or helper module is available, so this '
     + 'check could not run.'),
}

function labelForCheck(check: string): string {
  return SKIPPED_CHECK_LABELS[check] ?? check
}

function labelForReason(reason: string): string {
  return SKIPPED_REASON_LABELS[reason] ?? reason
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

  // The mobile/short-summary line shows the counts that have flags.
  // Verbose form on sm+ lists the per-check labels; compact form
  // on smaller viewports drops the labels and just shows numbers.
  const presentCounts: [string, number][] = [
    ['numeric', counts.numeric],
    ['direction', counts.direction],
    ['consistency', counts.consistency],
    ['citation', counts.citation],
    ['story plan', counts.story_plan ?? 0],
    ['required citations', counts.required_citations ?? 0],
    ['section word count', counts.section_word_count ?? 0],
    ['unresolved placeholders', counts.unresolved_placeholders ?? 0],
    ['raw numeric', counts.raw_numeric ?? 0],
  ].filter(([, n]) => (n as number) > 0) as [string, number][]

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
            ({presentCounts.map(([label, n]) =>
              `${n} ${label}`).join(' · ')})
          </span>
          <span className="opacity-80 sm:hidden">
            ({presentCounts.map(([, n]) => n).join('/')})
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
          <FlagGroup title="Story plan alignment"
            description="Generated section deviates from its locked story-plan anchor (numeric anchor, key message, or required reference)."
            flags={audit.flags_by_check.story_plan}
            render={(f) => (
              <>
                {f.section ? (
                  <span className="font-mono text-white/90">
                    {String(f.section)}
                    {f.slide ? ` · slide ${String(f.slide)}` : ''}
                  </span>
                ) : null}
                <div className="text-white/80 mt-0.5">
                  {String(f.description ?? f.message ?? f.detail ?? '')}
                </div>
              </>
            )} />
          <FlagGroup title="Required citations"
            description="An author the rubric requires (Hamilton, Carhart, Markowitz, etc.) is missing from the brief."
            flags={audit.flags_by_check.required_citations}
            render={(f) => (
              <span className="font-mono text-white/90">
                {String(f.author ?? f.required ?? f.name ?? '?')}
                {f.year ? ` (${String(f.year)})` : ''}
                {f.section ? ` · ${String(f.section)}` : ''}
              </span>
            )} />
          <FlagGroup title="Section word count"
            description="A section is over/under its rubric-specified word budget."
            flags={audit.flags_by_check.section_word_count}
            render={(f) => (
              <>
                <span className="font-mono text-white/90">
                  {String(f.section ?? '?')}
                </span>
                <span className="ml-2">
                  <code className="text-warning">
                    {String(f.actual ?? f.words ?? '?')} words
                  </code>
                  {f.target || f.target_min || f.target_max ? (
                    <>
                      {' '}target{' '}
                      <code className="text-white/80">
                        {String(
                          f.target
                          ?? `${f.target_min ?? '?'}-${f.target_max ?? '?'}`)}
                      </code>
                    </>
                  ) : null}
                </span>
              </>
            )} />
          <FlagGroup title="Unresolved placeholders"
            description="The generated text still contains {{TOKEN}} placeholders that the substitution table didn't resolve."
            flags={audit.flags_by_check.unresolved_placeholders}
            render={(f) => (
              <>
                <span className="font-mono text-warning">
                  {String(f.token ?? f.placeholder ?? '?')}
                </span>
                {f.section ? (
                  <span className="ml-2 text-white/70">
                    in {String(f.section)}
                  </span>
                ) : null}
                {f.sentence ? (
                  <div className="text-white/60 mt-0.5 italic">
                    {String(f.sentence)}
                  </div>
                ) : null}
              </>
            )} />
          <FlagGroup title="Raw numeric tokens"
            description="A raw number escaped into the prose where a {{TOKEN}} placeholder was expected — cache-disconnected risk."
            flags={audit.flags_by_check.raw_numeric}
            render={(f) => (
              <>
                <span className="font-mono text-warning">
                  {String(f.value ?? f.number ?? '?')}
                </span>
                {f.section ? (
                  <span className="ml-2 text-white/70">
                    in {String(f.section)}
                  </span>
                ) : null}
                {f.sentence ? (
                  <div className="text-white/60 mt-0.5 italic">
                    {String(f.sentence)}
                  </div>
                ) : null}
              </>
            )} />
          {audit.skipped && Object.keys(audit.skipped).length > 0 && (
            <div className="text-2xs text-warning/80 border-t border-warning/30
                            pt-1.5 space-y-0.5">
              <div className="font-semibold">Skipped checks:</div>
              {Object.entries(audit.skipped).map(([check, reason]) => (
                <div key={check} className="pl-2">
                  <span className="text-warning">
                    {labelForCheck(check)}
                  </span>
                  <span className="text-white/70 ml-1">
                    — {labelForReason(reason)}
                  </span>
                </div>
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
  flags: AuditFlag[] | undefined
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
