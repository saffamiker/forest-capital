/**
 * LightRefreshButton -- June 25 2026.
 *
 * Self-service analytics refresh trigger for team members.
 * Mounts on the Reports page next to Key Metrics.
 *
 * What it does: POSTs /api/v1/data/light-refresh and renders
 * the per-step status report inline. Does NOT touch story
 * plans, drafts, or document content -- regeneration of any
 * document remains the user's explicit action on the
 * Generate / Regenerate tile. The refresh DOES update the
 * data_hash column on every current draft so the per-tile
 * data-current chips reflect the new hash automatically.
 *
 * Pre-refresh hash status table: the panel reads the live
 * strategy_hash + every current draft's data_hash on mount
 * and surfaces a 4-row table (one per deliverable document
 * type) so the team can see at a glance which documents are
 * stale before deciding to run a refresh.
 *
 * Stale callout: a one-line summary above the Run button
 * tells the user whether any drafts are stale, all are
 * current, or no drafts exist yet.
 *
 * Post-refresh: re-fetches the drafts list to surface the
 * updated hash on the table + chips, and renders a success
 * message tallying how many drafts were updated.
 *
 * Gated behind TeamGate (generate_documents permission --
 * same permission the doc generators use, so anyone with
 * regen rights can self-serve a refresh without sysadmin
 * escalation).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import {
  CheckCircle, AlertTriangle, RefreshCw, Loader2,
} from 'lucide-react'

import TeamGate from './TeamGate'
import { useGenerationJobs } from '../lib/generationJobs'
import PostRefreshVerificationPanel
  from './PostRefreshVerificationPanel'


interface RefreshStep {
  step:           string
  ok:             boolean
  error?:         string
  data_hash?:     string
  strategy_hash?: string
  n_strategies?:  number
}

interface RefreshResponse {
  ok:             boolean
  strategy_hash:  string | null
  steps:          RefreshStep[]
  note?:          string
}


interface DraftSummary {
  id:            number
  document_type: string
  is_current?:   boolean
  data_hash?:    string | null
}


type HashRowStatus =
  | 'current'         // draft hash matches live hash (freeze inactive)
  | 'current_frozen'  // freeze active and draft hash == freeze hash
  | 'stale'           // draft hash differs from the comparison hash
  | 'no_draft'        // no current draft of this type
  | 'no_hash'         // draft exists but data_hash is null


interface HashRow {
  documentType: string
  label:        string
  draftHash:    string | null
  liveHash:     string | null
  status:       HashRowStatus
}


// June 27 2026 (Task 2) -- submission-freeze status shape returned
// by GET /api/v1/admin/submission-status. We only consume the two
// fields needed for the freeze-aware comparison; the endpoint also
// returns frozen_documents + hash_drift + freeze_date which the
// panel ignores here.
interface FreezeStatus {
  freeze_active:     boolean
  freeze_hash:       string | null
  current_live_hash: string
}


const DELIVERABLES: Array<{ key: string; label: string }> = [
  { key: 'executive_brief',     label: 'Executive Brief' },
  { key: 'presentation_deck',   label: 'Presentation Deck' },
  { key: 'analytical_appendix', label: 'Analytical Appendix' },
  { key: 'presentation_script', label: 'Presentation Script' },
]


function _shortHash(h: string | null | undefined): string {
  if (!h) return '—'
  return h.slice(0, 8)
}


function _classifyRow(
  draftHash: string | null,
  liveHash: string | null,
  hasDraft: boolean,
  freezeStatus: FreezeStatus | null,
): HashRowStatus {
  if (!hasDraft) return 'no_draft'
  if (!draftHash) return 'no_hash'
  // June 27 2026 (Task 2) -- under freeze, compare against the
  // freeze hash, not the live hash. Without this, drafts that
  // CORRECTLY carry the freeze hash were flagged 'stale' against
  // the live hash, producing the misleading "N documents have
  // stale data" warning + amber "Stale" pill.
  if (freezeStatus?.freeze_active && freezeStatus.freeze_hash) {
    if (draftHash === freezeStatus.freeze_hash) return 'current_frozen'
    // A draft NOT matching the freeze hash IS genuinely stale --
    // it was generated under the live hash and needs a regen
    // against the frozen cache. Keep the 'stale' classification.
    return 'stale'
  }
  if (!liveHash) return 'no_hash'
  if (draftHash === liveHash) return 'current'
  return 'stale'
}


export interface LightRefreshButtonProps {
  /** The data hash currently shown on the page (e.g. from Key
   *  Metrics). When the refresh returns a different hash, the
   *  panel surfaces a "new data detected -- consider regenerating"
   *  warning so the user knows the docs may be stale. */
  currentDataHash?: string | null | undefined
}


export default function LightRefreshButton(
  { currentDataHash }: LightRefreshButtonProps = {},
): React.ReactElement {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<RefreshResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Live strategy hash + per-doc drafts. Refreshed on mount and
  // again after a successful refresh so the table and the
  // post-refresh success message reflect the new hash.
  const [liveHash, setLiveHash] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<DraftSummary[]>([])
  // June 27 2026 (Task 2) -- submission-freeze status. When freeze
  // is active, the per-row comparison + summary callout copy +
  // header urgency all change so the panel reflects the locked
  // state rather than warning the user about drafts that are
  // CORRECTLY on the freeze hash.
  const [freezeStatus, setFreezeStatus]
    = useState<FreezeStatus | null>(null)
  // Snapshot of every current draft's data_hash BEFORE the refresh
  // fires, keyed by document_type. Used post-refresh to tally how
  // many drafts the refresh actually updated (drafts whose hash
  // changed from the snapshot to the new fetch).
  const [
    preRefreshHashes, setPreRefreshHashes,
  ] = useState<Record<string, string | null>>({})
  const [draftsUpdated, setDraftsUpdated] = useState<number | null>(null)

  const loadStatus = useCallback(async (): Promise<{
    drafts:     DraftSummary[]
    liveHash:   string | null
  }> => {
    // June 27 2026 (Task 2) -- add the submission-status fetch
    // alongside drafts + live-hash so the freeze state lands in
    // the same allSettled batch. Any failure of the freeze fetch
    // degrades to freeze_active=false (fail-open -- the panel
    // shows the live-hash comparison, which is the legacy
    // behaviour).
    const [draftsRes, liveRes, freezeRes] = await Promise.allSettled([
      axios.get<{ drafts: DraftSummary[] }>(
        '/api/v1/documents/drafts'),
      axios.get<{ current_data_hash?: string | null }>(
        '/api/v1/audit/runs/latest'),
      axios.get<FreezeStatus>(
        '/api/v1/admin/submission-status'),
    ])
    const draftsOut: DraftSummary[]
      = draftsRes.status === 'fulfilled'
        ? (draftsRes.value.data?.drafts ?? []).filter(
          (d) => d.is_current !== false)
        : []
    const liveOut: string | null
      = liveRes.status === 'fulfilled'
        ? (liveRes.value.data?.current_data_hash ?? null)
        : null
    const freezeOut: FreezeStatus | null
      = freezeRes.status === 'fulfilled'
        ? (freezeRes.value.data ?? null)
        : null
    setDrafts(draftsOut)
    setLiveHash(liveOut)
    setFreezeStatus(freezeOut)
    return { drafts: draftsOut, liveHash: liveOut }
  }, [])

  useEffect(() => {
    void loadStatus()
  }, [loadStatus])

  // June 25 2026 -- mirror the DocumentGenerationPanel's
  // job-completion refresh (PR #422). The Light Refresh status
  // table reads from /api/v1/documents/drafts, which only updates
  // its mount-time fetch -- a brief / appendix / deck regen
  // landed but the table still showed the OLD draft hash until
  // the user F5'd. Subscribing to the same useGenerationJobs
  // store the parent panel uses means a job transitioning to
  // complete / failed re-fires loadStatus here too.
  //
  // recorded ref de-dupes per job_id so a re-render that
  // doesn't carry a new terminal job is a no-op (and a job
  // we've already refreshed against doesn't re-fire on every
  // panel re-render).
  const jobs = useGenerationJobs()
  const refreshedJobIds = useRef<Set<string>>(new Set())
  useEffect(() => {
    let sawNewTerminal = false
    for (const job of jobs) {
      const terminal = (
        job.status === 'complete' || job.status === 'failed')
      if (!terminal) continue
      if (refreshedJobIds.current.has(job.job_id)) continue
      refreshedJobIds.current.add(job.job_id)
      sawNewTerminal = true
    }
    if (sawNewTerminal) {
      void loadStatus()
    }
  }, [jobs, loadStatus])

  const handleRefresh = async (): Promise<void> => {
    setBusy(true)
    setError(null)
    setResult(null)
    setDraftsUpdated(null)
    // Snapshot the BEFORE hashes so we can tally drafts_updated
    // after the refresh. Keyed by document_type for the diff.
    const snapshot: Record<string, string | null> = {}
    for (const d of drafts) {
      snapshot[d.document_type] = d.data_hash ?? null
    }
    setPreRefreshHashes(snapshot)
    try {
      const res = await axios.post<RefreshResponse>(
        '/api/v1/data/light-refresh')
      setResult(res.data)
      // Re-fetch drafts + live hash so the table and the chips
      // (via the parent panel's own fetch) surface the new state.
      const after = await loadStatus()
      let updated = 0
      for (const d of after.drafts) {
        const prev = snapshot[d.document_type] ?? null
        const now = d.data_hash ?? null
        if (prev !== now) updated += 1
      }
      setDraftsUpdated(updated)
    } catch (err) {
      const msg = axios.isAxiosError(err)
        ? (err.response?.data?.detail ?? err.message)
        : 'Light refresh failed.'
      // The detail can be a structured object (steps + blocked_at)
      // -- stringify for display rather than rendering [object Object].
      setError(
        typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally {
      setBusy(false)
    }
  }

  const hashChanged = (
    result !== null
    && result.strategy_hash !== null
    && currentDataHash !== null
    && currentDataHash !== undefined
    && result.strategy_hash !== currentDataHash)

  // Build the per-doc hash status rows. drafts is keyed by
  // document_type so a single pass picks up the matching row;
  // missing doc types fall through to no_draft.
  const draftByType: Record<string, DraftSummary> = {}
  for (const d of drafts) {
    draftByType[d.document_type] = d
  }
  const hashRows: HashRow[] = DELIVERABLES.map((dl) => {
    const draft = draftByType[dl.key] ?? null
    const hasDraft = draft !== null
    const draftHash = draft?.data_hash ?? null
    return {
      documentType: dl.key,
      label:        dl.label,
      draftHash,
      liveHash,
      status:       _classifyRow(
        draftHash, liveHash, hasDraft, freezeStatus),
    }
  })

  // Pre-refresh summary callout colour + copy keyed off the row
  // statuses (not currentDataHash) so it stays accurate even when
  // the parent didn't pass a hash.
  const totalDrafts = hashRows.filter(
    (r) => r.status !== 'no_draft').length
  // June 27 2026 (Task 2) -- 'current_frozen' counts as NOT
  // stale. Under freeze, drafts on the freeze hash are EXACTLY
  // what we want; the panel must not warn the user about them.
  const staleCount = hashRows.filter(
    (r) => r.status === 'stale' || r.status === 'no_hash').length
  const frozenCurrentCount = hashRows.filter(
    (r) => r.status === 'current_frozen').length
  const freezeActive = Boolean(freezeStatus?.freeze_active)

  return (
    <section
      data-testid="light-refresh-button"
      data-section-id="light-refresh"
      data-section-label="Light Refresh"
      className="card p-4 space-y-3">
      <div>
        <h2 className="text-white font-semibold text-sm flex
                       items-center gap-1.5">
          <RefreshCw className="w-4 h-4 text-electric" />
          Light Refresh
        </h2>
        <p className="text-xs text-muted mt-1 leading-relaxed">
          Re-runs the analytics cache (backtester, academic
          analytics, OOS cost sensitivity) for the current data
          hash. Updates the data hash on ALL current drafts
          (brief, deck, appendix, script) automatically. Does
          NOT touch story plans, draft content, or document text.
        </p>
      </div>

      {/* Pre-refresh hash status table. Always rendered so the
          team can see which drafts are stale before deciding
          whether to refresh. */}
      <div
        data-testid="light-refresh-status-table"
        className="rounded border border-border bg-navy-900/50">
        <table className="w-full text-2xs">
          <thead className="text-muted">
            <tr className="border-b border-border">
              <th className="text-left px-2 py-1.5 font-medium">
                Document
              </th>
              <th className="text-left px-2 py-1.5 font-medium">
                Draft Hash
              </th>
              <th className="text-left px-2 py-1.5 font-medium">
                Live Hash
              </th>
              <th className="text-left px-2 py-1.5 font-medium">
                Status
              </th>
            </tr>
          </thead>
          <tbody>
            {hashRows.map((row) => (
              <tr
                key={row.documentType}
                data-testid={`light-refresh-row-${row.documentType}`}
                className="border-b border-border/30 last:border-b-0">
                <td className="px-2 py-1.5 text-slate-300">
                  {row.label}
                </td>
                <td className="px-2 py-1.5 font-mono text-slate-300">
                  {row.draftHash ? _shortHash(row.draftHash) : '—'}
                </td>
                <td className="px-2 py-1.5 font-mono text-slate-300">
                  {row.liveHash ? _shortHash(row.liveHash) : '—'}
                </td>
                <td className="px-2 py-1.5">
                  <StatusPill status={row.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <StaleSummaryCallout
        totalDrafts={totalDrafts}
        staleCount={staleCount}
        freezeActive={freezeActive}
        frozenCurrentCount={frozenCurrentCount} />

      <TeamGate
        block
        permission="generate_documents"
        tooltip="Light refresh is available to team members with document generation rights">
        <button
          type="button"
          onClick={() => { void handleRefresh() }}
          disabled={busy}
          data-testid="light-refresh-run"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded
                     text-xs font-semibold bg-electric text-white
                     hover:bg-blue-500 disabled:opacity-50
                     disabled:cursor-not-allowed">
          {busy
            ? <><Loader2 className="w-3 h-3 animate-spin" />
                Refreshing analytics…</>
            : <><RefreshCw className="w-3 h-3" />
                Run Light Refresh</>}
        </button>
      </TeamGate>

      {error && (
        <div
          data-testid="light-refresh-error"
          className="text-2xs text-danger flex items-start gap-1.5
                     rounded border border-danger/30 bg-danger/5
                     p-2.5">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      {result && (
        <div data-testid="light-refresh-result"
          className="space-y-2">
          {/* New post-refresh success line -- tallies drafts
              updated and surfaces the new hash. */}
          {result.ok && (
            <div
              data-testid="light-refresh-success"
              className="rounded border border-success/30
                         bg-success/5 p-2.5 text-2xs text-success
                         flex items-start gap-1.5">
              <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>
                {(() => {
                  const newHash = result.strategy_hash
                    ? _shortHash(result.strategy_hash) : null
                  if (draftsUpdated === 0) {
                    return 'All drafts already current — no '
                      + 'updates needed.'
                  }
                  const n = draftsUpdated ?? 0
                  return `Light refresh complete. ${n} draft(s) `
                    + `updated${newHash ? ` to ${newHash}` : ''}.`
                })()}
              </span>
            </div>
          )}
          {hashChanged && (
            <div
              data-testid="light-refresh-hash-changed-warning"
              className="rounded border border-warning/40
                         bg-warning/5 p-2.5 text-2xs text-warning
                         leading-relaxed flex items-start gap-1.5">
              <AlertTriangle
                className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>
                New data detected — hash updated. Consider
                regenerating documents to reflect the latest
                figures.
              </span>
            </div>
          )}
          {/* June 27 2026 -- post-refresh verification panel.
              triggerKey bumped on every successful light refresh
              (the result-id is good enough -- the panel only
              cares that the value changes). The verifier replaces
              the legacy "All drafts already current" callout as
              the post-refresh confirmation. */}
          <PostRefreshVerificationPanel
            triggerKey={result ? result.strategy_hash : null} />
          <ul className="text-2xs text-slate-300 space-y-0.5">
            {result.steps.map((s, i) => (
              <li key={i}
                className="flex items-start gap-1.5">
                {s.ok
                  ? <CheckCircle
                      className="w-3 h-3 text-success shrink-0
                                 mt-0.5" />
                  : <AlertTriangle
                      className="w-3 h-3 text-warning shrink-0
                                 mt-0.5" />}
                <span>
                  <span className="font-semibold">{s.step}</span>
                  {s.ok ? ' — ok' : ' — '}
                  {!s.ok && (
                    <span className="text-danger">{s.error}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* preRefreshHashes is set inside handleRefresh so the
          ESLint unused-var rule doesn't fire on the destructured
          state. Returned only for testability; not surfaced. */}
      <span data-testid="light-refresh-prerefresh-count"
        className="sr-only">
        {Object.keys(preRefreshHashes).length}
      </span>
    </section>
  )
}


function StatusPill(
  { status }: { status: HashRowStatus },
): React.ReactElement {
  if (status === 'current') {
    return (
      <span
        data-testid="hash-status-pill-current"
        className="inline-flex items-center gap-1 px-1.5 py-0.5
                   rounded text-2xs font-medium bg-success/15
                   border border-success/40 text-success">
        <CheckCircle className="w-3 h-3" />
        Current
      </span>
    )
  }
  // June 27 2026 (Task 2) -- under freeze, drafts on the freeze
  // hash get a distinct pill: green like 'current' but with the
  // 'freeze active' qualifier so the reader understands the
  // status reflects the freeze hash, not the live hash.
  if (status === 'current_frozen') {
    return (
      <span
        data-testid="hash-status-pill-current-frozen"
        className="inline-flex items-center gap-1 px-1.5 py-0.5
                   rounded text-2xs font-medium bg-success/15
                   border border-success/40 text-success">
        <CheckCircle className="w-3 h-3" />
        Current (freeze active)
      </span>
    )
  }
  if (status === 'stale') {
    return (
      <span
        data-testid="hash-status-pill-stale"
        className="inline-flex items-center gap-1 px-1.5 py-0.5
                   rounded text-2xs font-medium bg-warning/15
                   border border-warning/40 text-warning">
        <AlertTriangle className="w-3 h-3" />
        Stale
      </span>
    )
  }
  if (status === 'no_hash') {
    return (
      <span
        data-testid="hash-status-pill-no-hash"
        className="inline-flex items-center gap-1 px-1.5 py-0.5
                   rounded text-2xs font-medium bg-slate-600/30
                   border border-slate-500/40 text-slate-300">
        No hash
      </span>
    )
  }
  return (
    <span
      data-testid="hash-status-pill-no-draft"
      className="inline-flex items-center gap-1 px-1.5 py-0.5
                 rounded text-2xs font-medium bg-slate-600/30
                 border border-slate-500/40 text-slate-300">
      No draft
    </span>
  )
}


function StaleSummaryCallout(
  { totalDrafts, staleCount, freezeActive, frozenCurrentCount }: {
    totalDrafts:         number
    staleCount:          number
    freezeActive:        boolean
    frozenCurrentCount:  number
  },
): React.ReactElement {
  if (totalDrafts === 0) {
    return (
      <div
        data-testid="stale-summary-callout-empty"
        className="rounded border border-slate-500/40
                   bg-slate-700/20 p-2.5 text-2xs text-slate-300
                   flex items-start gap-1.5">
        <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          No drafts generated yet. Generate documents first.
        </span>
      </div>
    )
  }
  // June 27 2026 (Task 2) -- freeze-active branch. When all
  // drafts are on the freeze hash, the panel reports a neutral
  // locked state rather than the legacy "current" copy that
  // implied liveness. The "Refresh analytics cache" wording
  // omits the urgency framing -- Light Refresh remains
  // available but is now an explicit operator choice, not a
  // remediation for stale data.
  if (freezeActive && staleCount === 0 && frozenCurrentCount > 0) {
    return (
      <div
        data-testid="stale-summary-callout-frozen-current"
        className="rounded border border-success/30
                   bg-success/5 p-2.5 text-2xs text-success
                   flex items-start gap-1.5">
        <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          All drafts locked to submission freeze hash. Refresh
          analytics cache is optional.
        </span>
      </div>
    )
  }
  if (staleCount === 0) {
    return (
      <div
        data-testid="stale-summary-callout-current"
        className="rounded border border-success/30
                   bg-success/5 p-2.5 text-2xs text-success
                   flex items-start gap-1.5">
        <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
        <span>
          All drafts are current. Light Refresh is optional.
        </span>
      </div>
    )
  }
  // June 27 2026 (Task 2) -- copy adapts to freeze state. Under
  // freeze, "current hash" is the freeze hash and the stale
  // drafts genuinely need regen to match it. Without freeze
  // the legacy copy applies. Either way the user-facing
  // problem is the same: drafts don't match the comparison
  // hash and a regen-against-cache pass is needed.
  const summaryText = freezeActive
    ? `${staleCount} document(s) have drifted from the freeze `
      + 'hash. Regenerate to lock them to the freeze cache.'
    : `${staleCount} document(s) have stale data. Run Light `
      + 'Refresh to update all drafts to the current hash.'
  return (
    <div
      data-testid="stale-summary-callout-stale"
      className="rounded border border-warning/40
                 bg-warning/5 p-2.5 text-2xs text-warning
                 flex items-start gap-1.5">
      <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
      <span>
        ⚠ {summaryText}
      </span>
    </div>
  )
}
