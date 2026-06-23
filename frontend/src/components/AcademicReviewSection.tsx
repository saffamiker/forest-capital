/**
 * AcademicReviewSection — the Academic Review surface on the QA Audit
 * page. May 28 2026 relocation: previously a panel on the Council
 * screen, now a peer of the Methodology Review and Statistical Audit.
 *
 * ACCESS PATTERN (read-visible / write-team-gated, mirrors the UAT
 * shared-visibility work in migration 042):
 *   - The section + verdict + peer responses are visible to every
 *     authenticated user.
 *   - The Run / Re-run / Cancel buttons are enabled only for project
 *     team members (useIsTeamMember). Non-team users see the buttons
 *     disabled with a tooltip explaining the gate.
 *
 * STATE PERSISTENCE
 *   Result lives in academicReviewStore (Zustand, in-memory). Keyed
 *   on data_hash — when the dashboard's audit data_hash changes, the
 *   cached verdict is surfaced as stale (the user has a real prior
 *   verdict to read, but the section also nudges a re-run because
 *   the underlying data has moved on). Survives navigation; resets
 *   on page reload.
 *
 *   The data_hash is read from /api/v1/audit/runs/latest — the same
 *   endpoint QAHub already polls for the audit currency badge — so
 *   no extra round-trip on this section's mount.
 *
 * NOT IN SCOPE FOR THIS PR
 *   The backend endpoint POST /api/council/academic-review and its
 *   streaming SSE shape are untouched. Only the UI entry point moved.
 */
import { useEffect, useState } from 'react'
import axios from 'axios'
import {
  GraduationCap, Loader2, X, ChevronDown, ChevronRight, AlertTriangle,
} from 'lucide-react'
import Markdown from './Markdown'
import CriticFindingsPanel from './CriticFindingsPanel'
import IndependentReviewCard from './IndependentReviewCard'
import { useIsTeamMember } from '../hooks/usePermissions'
import { trackFeature } from '../lib/activityLogger'
import {
  parseVerdict,
  extractTopPriority,
  type OverallRatings,
} from '../lib/academicVerdict'
import {
  extractMacroCategories,
  MacroAttributionFooter,
} from './MacroCitation'
import { useAcademicReviewStore } from '../stores/academicReviewStore'
import CrossDocumentReviewConfirmModal
  from './CrossDocumentReviewConfirmModal'


// Peer agent id → display name for the accordion. Mirrors the
// original AcademicReviewButton's map exactly.
const PEER_NAMES: Record<string, string> = {
  equity_analyst:        'Equity Analyst',
  fixed_income_analyst:  'Fixed Income Analyst',
  risk_manager:          'Risk Manager',
  quant_backtester:      'Quant Backtester',
  cio:                   'Chief Investment Officer',
  independent_analyst:   'Independent Analyst (Gemini)',
  contrarian_analyst:    'Contrarian Analyst (Grok)',
}

const RATING_STYLE: Record<string, string> = {
  Strong:        'bg-success/15 text-success border-success/30',
  Developing:    'bg-warning/15 text-warning border-warning/30',
  'Needs Work':  'bg-danger/15 text-danger border-danger/30',
  Incomplete:    'bg-danger/15 text-danger border-danger/30',
}


function RatingBadge({ rating }: { rating: string | null }) {
  if (!rating) return null
  const cls = RATING_STYLE[rating] ?? 'bg-navy-700 text-muted border-border'
  return (
    <span className={`text-2xs px-2 py-0.5 rounded-full border ${cls}`}>
      {rating}
    </span>
  )
}


function OverallRatingsBlock({ overall }: { overall: OverallRatings }) {
  return (
    <div
      data-testid="academic-overall-ratings"
      className="flex flex-wrap gap-x-6 gap-y-2 pb-3 mb-4 border-b border-border">
      {overall.academic && (
        <div className="flex items-center gap-2">
          <span className="text-2xs uppercase tracking-wide text-muted">
            Academic Rigour
          </span>
          <RatingBadge rating={overall.academic} />
        </div>
      )}
      {overall.pm && (
        <div className="flex items-center gap-2">
          <span className="text-2xs uppercase tracking-wide text-muted">
            Portfolio Manager Insight
          </span>
          <RatingBadge rating={overall.pm} />
        </div>
      )}
    </div>
  )
}


function TopPriorityCallout({ text }: { text: string }) {
  return (
    <div
      data-testid="academic-top-priority"
      className="rounded border border-warning/40 bg-warning/10 p-3 mb-4">
      <div className="flex items-center gap-1.5 mb-1">
        <AlertTriangle className="w-3.5 h-3.5 text-warning" />
        <span className="text-2xs uppercase tracking-wide text-warning
                         font-semibold">
          Top priority for further investigation
        </span>
      </div>
      <Markdown content={text} />
    </div>
  )
}


/** When the cached verdict's data_hash doesn't match the current
 *  audit data_hash, the user is reading a verdict generated against
 *  data that has since moved on. The verdict still renders — the
 *  reader has something concrete — but the banner makes the staleness
 *  explicit and invites a re-run. */
function StaleVerdictBanner({ canRerun }: { canRerun: boolean }) {
  return (
    <div data-testid="academic-stale-banner"
         className="rounded border border-warning/40 bg-warning/5 p-3 mb-3
                    flex items-start gap-2">
      <AlertTriangle className="w-3.5 h-3.5 text-warning shrink-0 mt-0.5" />
      <p className="text-2xs text-warning leading-relaxed">
        This verdict was generated against an earlier data state. The
        underlying analytics have changed since.{' '}
        {canRerun
          ? 'Re-run the review to evaluate against the current data.'
          : 'Ask a team member to re-run the review.'}
      </p>
    </div>
  )
}


/** Wraps a button so non-team users see it disabled with a tooltip.
 *  Mirrors the read-visible / write-team-gated pattern (UAT shared
 *  visibility, migration 042). */
function TeamActionButton({
  isTeam,
  disabled,
  onClick,
  className,
  children,
  testId,
}: {
  isTeam:    boolean
  disabled:  boolean
  onClick:   () => void
  className: string
  children:  React.ReactNode
  testId?:   string
}) {
  const tooltip = isTeam
    ? undefined
    : 'Available to project team members only'
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      disabled={!isTeam || disabled}
      title={tooltip}
      aria-disabled={!isTeam || disabled}
      className={className}
    >
      {children}
    </button>
  )
}


/** Cheap fetch of the current audit data_hash. Used to key the
 *  cached verdict — when the hash changes, the cache is treated as
 *  stale. Mirrors the auth-side endpoint QAHub already calls so the
 *  same response can land in two render frames without an extra
 *  round-trip (in practice we call it once on mount; the audit
 *  panel polls separately). */
function useCurrentDataHash(): string | null {
  const [hash, setHash] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    void axios.get<{ current_data_hash?: string }>(
      '/api/v1/audit/runs/latest',
    ).then((res) => {
      if (!cancelled) setHash(res.data.current_data_hash ?? null)
    }).catch(() => {
      if (!cancelled) setHash(null)
    })
    return () => { cancelled = true }
  }, [])
  return hash
}


export default function AcademicReviewSection() {
  const isTeam = useIsTeamMember()
  const dataHash = useCurrentDataHash()

  // Pull every slice individually so a partial state update doesn't
  // re-render the whole component over the entire result text on
  // each streamed chunk. (Zustand returns referentially-stable
  // primitives for primitive selectors.)
  const phase       = useAcademicReviewStore((s) => s.phase)
  const result      = useAcademicReviewStore((s) => s.result)
  const errorMsg    = useAcademicReviewStore((s) => s.errorMsg)
  const cachedHash  = useAcademicReviewStore((s) => s.dataHash)
  const isCurrent   = useAcademicReviewStore((s) => s.isCurrentFor)
  const runReview   = useAcademicReviewStore((s) => s.runReview)
  const cancel      = useAcademicReviewStore((s) => s.cancel)
  const clearStore  = useAcademicReviewStore((s) => s.clear)

  const [peersOpen, setPeersOpen] = useState(false)
  // June 22 2026 -- confirmation gate before the expensive cross-
  // document pass kicks off. onRun opens the modal; onConfirmRun
  // is the modal's onConfirm callback that actually fires the SSE
  // POST.
  const [confirmOpen, setConfirmOpen] = useState(false)

  const running = phase === 'consulting' || phase === 'streaming'
  const hasResult = result !== null
  const isStale = hasResult
    && cachedHash !== null
    && dataHash !== null
    && cachedHash !== dataHash

  const onRun = () => {
    // Open the confirm modal -- DO NOT fire the SSE yet.
    setConfirmOpen(true)
  }

  const onConfirmRun = () => {
    setConfirmOpen(false)
    trackFeature('academic_review_trigger')
    // Re-run path: clear first, then kick a fresh run. The store's
    // runReview also clears its own internal result, so this clear
    // is redundant in steady state but explicit for readability.
    clearStore()
    const token = localStorage.getItem('fc_session_token') ?? ''
    void runReview(dataHash, token)
  }

  // Auto-restore from cache on mount — purely a read; the
  // isCurrentFor check decides whether to render the cached verdict
  // immediately. No effect needed because the store IS the state;
  // the component reads it on every render. The effect we DO need is
  // to surface peerResponses' open/closed accordion state when a
  // cached verdict has fewer peers than the current run (the user
  // closed it last time → keep it closed this time).
  // Intentionally left to derive from peersOpen as-is.
  void isCurrent  // keep the import live; used by render path below

  // Parse the verdict for rendering — the same lib the original
  // button used. parseVerdict handles partial / streaming text
  // gracefully.
  const arbiterText = result?.arbiterText ?? ''
  const peerResponses = result?.peerResponses ?? {}
  const peerEntries = Object.entries(peerResponses)
  const independentReview = result?.independentReview ?? null
  const { overall, sections } = parseVerdict(arbiterText)
  const topPriority = extractTopPriority(sections)

  return (
    <div className="space-y-3">
      <CrossDocumentReviewConfirmModal
        open={confirmOpen}
        onCancel={() => setConfirmOpen(false)}
        onConfirm={onConfirmRun} />
      {/* Trigger card — prominent border so the action is visible.
          Read content (verdict + peers) renders below; this card is
          the only team-gated surface. */}
      <div className="card p-4 border border-warning/30 bg-warning/5"
           data-testid="academic-review-trigger">
        <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-white font-semibold text-sm flex items-center gap-1.5">
              <GraduationCap className="w-4 h-4 text-warning" />
              Cross-Document Review
            </h3>
            {phase === 'idle' && !hasResult && (
              <p className="text-muted text-xs mt-1 leading-relaxed">
                Full council pass across all four deliverables --
                brief, deck, appendix, and presentation script.
                Rubric-mapped verdict (Strong / Developing /
                Needs Work). Resource-intensive; run as a final
                check after every document has been generated and
                edited. For lighter feedback on a single document,
                each editor's Writing Assistant has its own
                per-document review.
              </p>
            )}
            {phase === 'idle' && hasResult && (
              <p className="text-muted text-xs mt-1 leading-relaxed">
                A prior verdict is shown below. Re-run to evaluate
                against the current data state.
              </p>
            )}
            {running && (
              <p className="text-xs text-muted mt-1">
                {phase === 'consulting'
                  ? 'Step 1 of 2 — consulting the council (peer fan-out)…'
                  : 'Step 2 of 2 — synthesising the arbiter verdict…'}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 sm:shrink-0">
            {running && (
              <TeamActionButton
                isTeam={isTeam}
                disabled={false}
                onClick={cancel}
                testId="academic-review-cancel"
                className="flex items-center gap-1 text-xs text-muted
                           hover:text-danger transition-colors
                           disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <X className="w-3.5 h-3.5" /> Cancel
              </TeamActionButton>
            )}
            <TeamActionButton
              isTeam={isTeam}
              disabled={running}
              onClick={onRun}
              testId="academic-review-run"
              className="flex flex-1 sm:flex-none items-center justify-center
                         gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold
                         bg-warning text-navy-900 hover:bg-amber-400
                         disabled:opacity-50 disabled:cursor-not-allowed
                         transition-colors"
            >
              {running
                ? <Loader2 className="w-4 h-4 animate-spin" />
                : <GraduationCap className="w-4 h-4" />}
              {running
                ? 'Reviewing…'
                : (hasResult
                  ? 'Re-run Cross-Document Review'
                  : 'Run Cross-Document Review')}
            </TeamActionButton>
          </div>
        </div>
      </div>

      {phase === 'error' && (
        <div className="card border border-danger/30 bg-danger/5 p-3
                        text-danger text-xs"
             data-testid="academic-review-error">
          {errorMsg}
        </div>
      )}

      {/* Stale-verdict banner — the cached verdict's data_hash no
          longer matches the current audit data_hash. The verdict
          still renders below; the banner is the explicit signal. */}
      {isStale && !running && (
        <StaleVerdictBanner canRerun={isTeam} />
      )}

      {/* Arbiter verdict — same render shape as the previous Council
          panel. Read-visible to every authenticated user. */}
      {(running || hasResult)
        && (sections.length > 0
            || overall
            || (phase === 'done' && arbiterText.trim().length > 200)) && (
        <div className="card p-4"
             style={{ borderLeft: '3px solid #f59e0b' }}
             data-testid="academic-review-verdict">
          <div className="flex items-center gap-2 mb-3">
            <GraduationCap className="w-4 h-4 text-warning" />
            <h3 className="text-white font-semibold text-sm">
              Academic Review — Council Verdict
            </h3>
          </div>

          {overall && <OverallRatingsBlock overall={overall} />}
          {topPriority && <TopPriorityCallout text={topPriority} />}

          {sections.length > 0 ? (
            <div className="space-y-4">
              {sections.map((s, i) => (
                <div key={i}>
                  <div className="flex items-center gap-2 flex-wrap">
                    <h4 className="text-white font-medium text-sm">{s.heading}</h4>
                    <RatingBadge rating={s.rating} />
                  </div>
                  {s.body && <Markdown content={s.body} className="mt-1" />}
                </div>
              ))}
            </div>
          ) : phase === 'done' && arbiterText.trim().length > 200 ? (
            // Defensive fallback — parser produced no sections but the
            // stream completed with substantive text. Same fallback
            // surface as the original Council panel (UAT #75).
            <div data-testid="academic-verdict-fallback">
              <p className="text-2xs text-muted italic mb-3">
                Verdict structure could not be parsed — rendering raw
                markdown. The rating badges may not appear if the
                arbiter used an unrecognised heading format; the
                content below is the complete verdict text.
              </p>
              <Markdown content={arbiterText} />
            </div>
          ) : null}

          {(() => {
            const cats = extractMacroCategories(arbiterText)
            return cats.length > 0
              ? <MacroAttributionFooter categories={cats} />
              : null
          })()}
        </div>
      )}

      {/* Independent Review — second-opinion advisory card. Renders
          below the primary verdict; ONLY when the independent_review
          SSE frame has landed. Never affects the primary score or
          gates. */}
      <IndependentReviewCard review={independentReview} />

      {/* Concern 7 (revised) -- adversarial critic + debate-round
          panel. Renders nothing if the SSE pass hasn't reached the
          critic step yet; once the critic_findings frame lands the
          panel surfaces the structured findings + the streamed
          council response from debate_round_arbiter chunks. */}
      <CriticFindingsPanel
        criticResult={result?.criticResult ?? null}
        debateRoundText={result?.debateRoundText ?? ''}
        criticMinorOnly={result?.criticMinorOnly ?? false} />

      {/* Peer responses — supporting detail, collapsed by default. */}
      {peerEntries.length > 0 && (
        <div className="card overflow-hidden"
             data-testid="academic-review-peers">
          <button
            type="button"
            onClick={() => setPeersOpen((o) => !o)}
            className="w-full flex items-center gap-2 px-4 py-2.5 min-h-[44px]
                       text-sm text-white hover:bg-navy-700 transition-colors"
          >
            {peersOpen
              ? <ChevronDown className="w-4 h-4 text-muted" />
              : <ChevronRight className="w-4 h-4 text-muted" />}
            <span>Peer reviews ({peerEntries.length})</span>
            <span className="text-2xs text-muted ml-1">supporting detail</span>
          </button>
          {peersOpen && (
            <div className="border-t border-border divide-y divide-border">
              {peerEntries.map(([agentId, text]) => (
                <div key={agentId} className="px-4 py-3">
                  <div className="text-xs font-semibold text-electric mb-1">
                    {PEER_NAMES[agentId] ?? agentId}
                  </div>
                  <Markdown content={text} />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
