/**
 * DocumentGenerationPanel — the "Generate Documents" section on the
 * Reports screen.
 *
 * Three cards, one per graded deliverable. Generation is asynchronous:
 * the POST returns a job_id and the work runs as a backend job. Polling
 * lives in a module-level store (lib/generationJobs) so it continues
 * when the user navigates away — a global toast then announces
 * completion. Each card derives its state from the tracked job for its
 * document type: in-progress → spinner + Cancel; complete → Open in
 * Editor + Download; failed → Try Again.
 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import axios from 'axios'
import {
  FileText, FileSpreadsheet, Presentation, Download, Loader2, AlertCircle,
  CheckCircle, PenLine, RefreshCw, X, Info, Mic, ShieldCheck, ShieldAlert,
  ShieldX,
} from 'lucide-react'
import BriefRegenConfirmModal from './BriefRegenConfirmModal'
import RegenConfirmModal from './RegenConfirmModal'
import SlideGuidancePanel from './SlideGuidancePanel'
import TeamGate from './TeamGate'
import {
  useGenerationJobs, trackJob, cancelJob, dismissJob, loadExistingJobs,
  jobForType, type GenJob,
} from '../lib/generationJobs'
import {
  ReportBlockingModal, useReportReadinessGate,
} from './ReportReadinessIndicator'
import { BriefWorkflowModal } from './BriefWorkflowModal'
import { DeckWorkflowModal } from './DeckWorkflowModal'
import { AppendixWorkflowModal } from './AppendixWorkflowModal'
import {
  useReportReadinessStore, type ExportVerificationStatus,
} from '../stores/reportReadinessStore'

interface DocSpec {
  id: string
  documentType: string
  title: string
  description: string
  endpoint: string
  icon: typeof FileText
}


/**
 * ExportVerificationPill -- Layer 3b (June 21 2026).
 *
 * Compact status pill rendered near each document card header. Reads
 * from useReportReadinessStore's export_verification field (populated
 * server-side on /api/v1/report/readiness). Four states:
 *
 *   verified     -> green "Export verified" pill
 *   warned       -> amber "Regenerate recommended" pill
 *   failed       -> red "Issues found" pill
 *   not_exported -> muted grey "Not yet exported" text (no pill)
 *
 * Returns null when the store has no readiness payload yet so the
 * card chrome doesn't flicker between an empty slot and a pill on
 * first mount.
 */
function ExportVerificationPill(
  { status }: { status: ExportVerificationStatus | null },
) {
  if (status === null) return null
  if (status === 'verified') {
    return (
      <span
        data-testid="export-verification-pill-verified"
        className="inline-flex items-center gap-1 px-1.5 py-0.5
                   rounded text-2xs font-medium bg-success/15
                   border border-success/40 text-success">
        <ShieldCheck className="w-3 h-3" />
        Export verified
      </span>
    )
  }
  if (status === 'warned') {
    return (
      <span
        data-testid="export-verification-pill-warned"
        className="inline-flex items-center gap-1 px-1.5 py-0.5
                   rounded text-2xs font-medium bg-amber-500/15
                   border border-amber-500/40 text-amber-200">
        <ShieldAlert className="w-3 h-3" />
        Regenerate recommended
      </span>
    )
  }
  if (status === 'failed') {
    return (
      <span
        data-testid="export-verification-pill-failed"
        className="inline-flex items-center gap-1 px-1.5 py-0.5
                   rounded text-2xs font-medium bg-danger/15
                   border border-danger/40 text-danger">
        <ShieldX className="w-3 h-3" />
        Issues found
      </span>
    )
  }
  // not_exported
  return (
    <span
      data-testid="export-verification-pill-not-exported"
      className="text-2xs text-muted">
      Not yet exported
    </span>
  )
}


/**
 * AuditFindingsWarningBanner -- June 25 2026.
 *
 * Amber banner above the regen cards that surfaces unacknowledged
 * audit findings as a WARNING, not a hard block. Per the
 * platform's fail-open architecture, audit findings are advisory:
 * the team makes the final call. This banner replaces the
 * legacy 'Report not ready' blocking modal flow for audit-only
 * blocks.
 *
 * Hidden when:
 *   - readiness has not yet loaded
 *   - blocking_count is 0 (nothing to warn about)
 *   - caches_warm is false (the blocking modal handles that
 *     surface; the user has to clear caches before they can
 *     even regen sensibly)
 *   - the user has dismissed the banner for this session
 *     (sessionStorage key fc_audit_warning_dismissed)
 */
function AuditFindingsWarningBanner(): React.ReactElement | null {
  const readiness = useReportReadinessStore((s) => s.readiness)
  const dismissKey = 'fc_audit_warning_dismissed'
  const [dismissed, setDismissed] = useState<boolean>(
    () => sessionStorage.getItem(dismissKey) === '1')
  if (!readiness) return null
  const blockingCount = readiness.blocking_count ?? 0
  if (blockingCount === 0) return null
  const cachesWarm = (
    readiness as { caches_warm?: boolean }).caches_warm
  if (cachesWarm === false) return null   // cold-cache modal handles it
  if (dismissed) return null
  return (
    <div
      data-testid="audit-findings-warning-banner"
      className="mb-3 rounded border border-warning/40
                 bg-warning/10 p-3 flex items-start gap-2">
      <AlertCircle className="w-4 h-4 text-warning shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-xs text-warning font-semibold">
          {blockingCount} audit finding
          {blockingCount === 1 ? '' : 's'} unacknowledged.
        </p>
        <p className="text-2xs text-amber-100/85 mt-1 leading-relaxed">
          Review them on the QA Audit tab. Generation can proceed —
          audit findings are advisory; the team makes the final
          call on what is submission-ready.
        </p>
        <div className="flex flex-wrap gap-1.5 mt-2">
          <a
            href="/qa#audit"
            data-testid="audit-warning-go-to-qa"
            className="text-2xs px-2 py-1 rounded border
                       border-warning/40 text-warning
                       hover:bg-warning/15 font-semibold">
            Go to QA Audit
          </a>
          <button
            type="button"
            onClick={() => {
              sessionStorage.setItem(dismissKey, '1')
              setDismissed(true)
            }}
            data-testid="audit-warning-dismiss"
            className="text-2xs px-2 py-1 rounded text-muted
                       hover:text-white border border-transparent
                       hover:border-border">
            Dismiss
          </button>
        </div>
      </div>
    </div>
  )
}


/**
 * TileMetadataBlock -- June 25 2026.
 *
 * Generated / Updated / Data-hash strip on each Generate Documents
 * tile when a current draft exists. Sourced from
 * /api/v1/documents/drafts (canonical) instead of in-memory job
 * completion stamps (stale after a page reload).
 *
 * Updated line only renders when updated_at differs from created_at
 * by more than 60 seconds -- the threshold filters out the no-op
 * UPDATE the create_draft path runs at insert time. When equal
 * within tolerance, only Generated shows.
 *
 * Data hash renders the first 8 chars + a colour indicator (green
 * when matching the live strategy_hash, amber on mismatch).
 */
interface TileMetadataDraft {
  id:          number
  created_at:  string | null
  updated_at:  string | null
  data_hash:   string | null
  owner_email: string | null
}

function _formatTileTimestamp(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short', day: 'numeric', year: 'numeric',
      hour: 'numeric', minute: '2-digit',
    })
  } catch {
    return iso
  }
}


function TileMetadataBlock(
  { draft, liveDataHash, hashStale }: {
    draft: TileMetadataDraft
    liveDataHash: string | null
    hashStale: boolean
  },
) {
  const generated = _formatTileTimestamp(draft.created_at)
  const updated = _formatTileTimestamp(draft.updated_at)
  // Show updated only when it diverges from created by more than
  // a minute -- create_draft writes both at the same NOW() so the
  // initial draft would otherwise render two identical timestamps.
  let showUpdated = false
  try {
    if (draft.created_at && draft.updated_at) {
      const c = new Date(draft.created_at).getTime()
      const u = new Date(draft.updated_at).getTime()
      if (!Number.isNaN(c) && !Number.isNaN(u)
          && Math.abs(u - c) > 60_000) {
        showUpdated = true
      }
    }
  } catch {
    showUpdated = false
  }
  // June 25 2026 -- consolidated hash chip. Renders three exclusive
  // states. The previous block had a "Data hash: xxx" line PLUS an
  // amber stale callout; this collapses both into a single pill so
  // the tile chrome stays compact. Three states:
  //   draft.data_hash present + matches live  -> green chip
  //   draft.data_hash present + diverges      -> amber chip
  //   draft.data_hash absent                  -> gray chip
  // The chip text is the DRAFT's hash (first 8 chars), not the
  // live hash -- the user wants to see what the draft was
  // generated against, with the chip colour signalling whether
  // that matches today's analytics.
  return (
    <div className="text-2xs text-muted space-y-0.5"
      data-testid="tile-metadata-block">
      <div className="flex items-center gap-1.5">
        <CheckCircle className="w-3 h-3 text-success shrink-0" />
        <span>Generated: {generated}</span>
      </div>
      {draft.owner_email && (
        <div className="flex items-center gap-1.5"
          data-testid="tile-generated-by">
          <span className="w-3 inline-block" />
          <span>Generated by {draft.owner_email}</span>
        </div>
      )}
      {showUpdated && (
        <div className="flex items-center gap-1.5">
          <span className="w-3 inline-block" />
          <span>Updated: {updated}</span>
        </div>
      )}
      <TileHashChip
        draftHash={draft.data_hash}
        liveDataHash={liveDataHash}
        hashStale={hashStale} />
    </div>
  )
}


/**
 * TileHashChip -- June 25 2026.
 *
 * Three exclusive chip states for the per-tile data-hash signal.
 * Replaces the old "Data hash: xxx" line plus stale callout duo
 * with a single pill the user can scan at a glance.
 */
function TileHashChip(
  { draftHash, liveDataHash, hashStale }: {
    draftHash: string | null
    liveDataHash: string | null
    hashStale: boolean
  },
): React.ReactElement {
  if (!draftHash) {
    return (
      <div
        data-testid="tile-hash-chip-missing"
        className="inline-flex items-center gap-1.5 text-2xs
                   px-1.5 py-0.5 rounded bg-slate-600/30
                   border border-slate-500/40 text-slate-300">
        <AlertCircle className="w-3 h-3 shrink-0" />
        <span>No hash — regenerate recommended</span>
      </div>
    )
  }
  const short = draftHash.slice(0, 8)
  if (hashStale && liveDataHash) {
    return (
      <div
        data-testid="tile-hash-chip-stale"
        title={
          `Draft hash ${draftHash} vs live ${liveDataHash}`}
        className="inline-flex items-center gap-1.5 text-2xs
                   px-1.5 py-0.5 rounded bg-warning/15
                   border border-warning/40 text-warning">
        <AlertCircle className="w-3 h-3 shrink-0" />
        <span className="font-mono">{short}</span>
        <span>Data stale</span>
      </div>
    )
  }
  return (
    <div
      data-testid="tile-hash-chip-current"
      title={`Draft hash ${draftHash} matches the live analytics cache`}
      className="inline-flex items-center gap-1.5 text-2xs
                 px-1.5 py-0.5 rounded bg-success/15
                 border border-success/40 text-success">
      <CheckCircle className="w-3 h-3 shrink-0" />
      <span className="font-mono">{short}</span>
      <span>Data current</span>
    </div>
  )
}


// Tile display order (June 25 2026): Brief, Appendix, Deck, Script.
// Order reflects the source-of-truth chain -- brief is the narrative
// anchor; appendix carries the numeric evidence; deck reads both;
// script reads deck. The script joined the async job pattern on
// 2026-06-25 (POST /api/v1/documents/script/generate now returns
// 202 + job_id), so it renders inside DOCS.map alongside the other
// three and inherits the standard inProgress / complete / failed /
// idle ternary chrome. Previously rendered as a separate
// PresentationScriptCard with a custom Download-only flow.
const DOCS: DocSpec[] = [
  {
    id: 'brief',
    documentType: 'executive_brief',
    title: 'Executive Brief',
    description:
      'Five-page investment-audience brief covering methodology, '
      + 'findings, and recommendations.',
    endpoint: '/api/v1/export/executive-brief',
    icon: FileText,
  },
  {
    id: 'appendix',
    documentType: 'analytical_appendix',
    title: 'Analytical Appendix',
    description:
      'Evidentiary record across eight sections (data, performance, '
      + 'statistical tests, bootstrap CIs, factors, crisis windows, '
      + 'cost sensitivity, audit). Every figure traces to the data hash '
      + 'in the footer.',
    endpoint: '/api/v1/export/analytical-appendix',
    icon: FileSpreadsheet,
  },
  {
    id: 'deck',
    documentType: 'presentation_deck',
    title: 'Final Presentation Deck',
    description:
      'Eleven-slide presentation deck covering the investment '
      + 'question, evidence, regime analysis, live demo setup, AI '
      + 'methodology, and the final recommendation -- with real data '
      + 'charts in light mode, ready to refine in the editor.',
    endpoint: '/api/v1/export/presentation-deck',
    icon: Presentation,
  },
  {
    id: 'script',
    documentType: 'presentation_script',
    title: 'Presentation Script',
    description:
      'Word-for-word presenter script for the 18-20 minute panel '
      + 'presentation, with Grok-generated Q&A preparation. Generated '
      + 'from the current deck draft -- generate the deck first if '
      + 'it does not yet exist.',
    endpoint: '/api/v1/documents/script/generate',
    icon: Mic,
  },
]

const LS_KEY = 'fc_doc_generated_at'

function readGeneratedAt(): Record<string, string> {
  try {
    const raw = localStorage.getItem(LS_KEY)
    return raw ? (JSON.parse(raw) as Record<string, string>) : {}
  } catch {
    return {}
  }
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}



export default function DocumentGenerationPanel() {
  const navigate = useNavigate()
  useGenerationJobs()   // re-render as tracked job states change
  // The POST that creates a job is brief; this guards the button until
  // the job appears in the store.
  const [postingId, setPostingId] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [generatedAt, setGeneratedAt] =
    useState<Record<string, string>>(readGeneratedAt)
  // PR #337 -- the Executive Brief card has an Info icon that opens
  // a step-by-step workflow guide. State is local to the panel; the
  // modal re-mounts itself on open so the checklist resets each time.
  const [briefGuideOpen, setBriefGuideOpen] = useState(false)
  // June 23 2026 -- per-doc workflow guides. Deck + Appendix now
  // have their own modals; the Info icon on each tile opens the
  // appropriate one. The four cards in DOCS may grow later; per-
  // type state keys the guide open/closed independently so opening
  // the Deck guide doesn't reset a half-read Brief guide.
  const [deckGuideOpen, setDeckGuideOpen] = useState(false)
  const [appendixGuideOpen, setAppendixGuideOpen] = useState(false)
  // Job ids whose completion has already been written to "last generated".
  const recorded = useRef<Set<string>>(new Set())
  // Workstream C report gate. The hook loads /api/v1/report/readiness
  // on mount; clicking Generate while is_ready is false opens the
  // blocking modal instead of firing the POST. The server-side gate
  // (_require_report_ready) is the source of truth — when it fires
  // a 422 the modal also opens from the response detail (defence in
  // depth against a stale `is_ready` value).
  const readinessGate = useReportReadinessGate()
  // Layer 3b (June 21 2026) -- read the per-document
  // export_verification map from the readiness store so each card
  // can render its pill. Pulled separately from useReportReadinessGate
  // so a stale store still surfaces the gate state for the
  // generation buttons while the pill stays in the neutral "not
  // exported" state.
  const exportVerification = useReportReadinessStore(
    (s) => s.readiness?.export_verification ?? null)
  const [blockingModal, setBlockingModal] = useState<{
    open: boolean; blockers: string[]; message?: string;
    coldCaches?: string[]
  }>({ open: false, blockers: [] })
  // June 23 2026 -- brief regen confirmation gate. The modal opens
  // BEFORE the POST when /api/v1/story-plans/exists reports any of
  // (deck | appendix | script) story plans currently exist. On
  // Cancel, the POST is suppressed. On Confirm, the POST fires and
  // the backend clears those plans at the top of
  // _generate_brief_document. Pending: the DocSpec captured at
  // click time so Confirm can resume the same generation flow
  // (regenerate=true) the user originally chose.
  const [briefRegenConfirm, setBriefRegenConfirm] = useState<{
    open: boolean
    pendingDoc: DocSpec | null
  }>({ open: false, pendingDoc: null })
  // June 24 2026 -- team-replacement regen warning for non-brief
  // doc types (deck / appendix / script). Brief gets its own modal
  // (BriefRegenConfirmModal) above that merges the downstream
  // story-plan clear language into the same dialog.
  const [regenConfirm, setRegenConfirm] = useState<{
    open: boolean
    pendingDoc: DocSpec | null
  }>({ open: false, pendingDoc: null })

  // On mount, resume polling any in-progress jobs and surface recently
  // completed ones (e.g. generation finished while the user was away).
  useEffect(() => { void loadExistingJobs() }, [])

  // June 25 2026 -- current draft per document_type, regardless of
  // which team member generated it. The generation-jobs store only
  // knows about jobs the current session kicked; Bob generating the
  // brief used to mean Mike couldn't see an Open in Editor button
  // because no job was tracked in Mike's store. This fetch surfaces
  // the current draft id for every doc_type so the button renders
  // for any team member when a draft exists. Default endpoint
  // already returns one row per document_type max (PR #402).
  // June 25 2026 -- carry the full current draft summary per
  // document_type so tile metadata (Generated / Updated / Data
  // hash) reads directly from the canonical drafts endpoint
  // rather than from job completion events (which can be stale
  // after a page reload) or local timestamp stamps (which were
  // an editor-job-side concept). One row per document_type max
  // since the endpoint defaults to is_current=true (PR #402).
  interface CurrentDraftSummary {
    id:          number
    created_at:  string | null
    updated_at:  string | null
    data_hash:   string | null
    owner_email: string | null
  }
  const [
    currentDraftByType, setCurrentDraftByType,
  ] = useState<Record<string, CurrentDraftSummary>>({})
  const [liveDataHash, setLiveDataHash] = useState<string | null>(
    null)
  // June 25 2026 -- bump to force a re-fetch when a generation job
  // completes. The job-completion useEffect below increments this
  // and the dependency array on the fetch effect triggers a fresh
  // GET /drafts + GET /audit/runs/latest. Refresh is also a noop
  // on first mount (counter starts at 0; useEffect runs anyway).
  const [draftsRefreshTick, setDraftsRefreshTick] = useState(0)
  useEffect(() => {
    let cancelled = false
    axios.get<{
      drafts: Array<{
        id: number
        document_type: string
        is_current?: boolean
        created_at?: string | null
        updated_at?: string | null
        data_hash?:  string | null
        owner_email?: string | null
      }>
    }>('/api/v1/documents/drafts')
      .then((res) => {
        if (cancelled) return
        const map: Record<string, CurrentDraftSummary> = {}
        for (const d of res.data.drafts ?? []) {
          if (d.is_current === false) continue
          map[d.document_type] = {
            id:          d.id,
            created_at:  d.created_at ?? null,
            updated_at:  d.updated_at ?? null,
            data_hash:   d.data_hash ?? null,
            owner_email: d.owner_email ?? null,
          }
        }
        setCurrentDraftByType(map)
      })
      .catch(() => { /* no-op -- tile stays metadata-less */ })
    // Also fetch the live strategy hash for the mismatch check.
    axios.get<{ current_data_hash?: string | null }>(
      '/api/v1/audit/runs/latest')
      .then((res) => {
        if (cancelled) return
        setLiveDataHash(res.data?.current_data_hash ?? null)
      })
      .catch(() => { if (!cancelled) setLiveDataHash(null) })
    return () => { cancelled = true }
  }, [draftsRefreshTick])

  // Stamp "last generated" the first time a job for a type completes.
  const jobs = useGenerationJobs()
  const readinessReload = useReportReadinessStore((s) => s.reload)
  useEffect(() => {
    let sawTerminalStateChange = false
    for (const job of jobs) {
      const terminal = (
        job.status === 'complete' || job.status === 'failed')
      if (!terminal || recorded.current.has(job.job_id)) {
        continue
      }
      recorded.current.add(job.job_id)
      sawTerminalStateChange = true
      const doc = DOCS.find((d) => d.documentType === job.document_type)
      if (!doc) continue
      const now = new Date().toISOString()
      setGeneratedAt((prev) => {
        const next = { ...prev, [doc.id]: now }
        try { localStorage.setItem(LS_KEY, JSON.stringify(next)) }
        catch { /* best-effort */ }
        return next
      })
    }
    // June 25 2026 -- when a job transitions to a terminal state
    // (complete OR failed) re-fetch the drafts list, the live
    // strategy hash, and the readiness verdict. Previously the
    // user had to F5 to see the new draft's metadata on the tile
    // because the fetch was mount-only.
    //   draftsRefreshTick bump  -> re-runs the drafts + hash fetch
    //   readinessReload()       -> re-fetches /api/v1/report/readiness
    //                              (which the warning banner +
    //                              ReportReadinessBanner read from)
    // The hash banner on the Reports page mounts its own fetch
    // against /api/v1/audit/runs/latest -- LiveDataHashBanner is
    // separate from this panel and re-mounts when its parent re-
    // renders, so the audit-hash refresh fires there.
    if (sawTerminalStateChange) {
      setDraftsRefreshTick((n) => n + 1)
      void readinessReload()
    }
  }, [jobs, readinessReload])

  const handleGenerate = async (
    doc: DocSpec,
    opts?: {
      regenerate?: boolean
      // Internal flag set by the brief-regen confirm modal's
      // onConfirm so we skip the pre-flight + modal loop. Not part
      // of the public API of this function.
      _fromBriefRegenConfirm?: boolean
      // Concern (June 24 2026) -- internal flag set by the
      // RegenConfirmModal's onConfirm for deck / appendix /
      // script regens so we skip the modal on the resumed call.
      _fromRegenConfirm?: boolean
    },
  ) => {
    // Bridge #90: when the user clicks Regenerate on a card that
    // already has a complete generation, warn them the existing
    // editor draft will be superseded -- the backend creates a NEW
    // editor_drafts row and flips the previous one's is_current to
    // false, so any unsaved edits in the editor are no longer the
    // canonical "current" draft. The frontend has no explicit
    // dirty-flag for the editor, so always-confirm on Regenerate
    // is the safe default.
    if (opts?.regenerate) {
      // June 23 2026 -- the brief gets a dedicated modal (NOT
      // window.confirm) that warns about the downstream story
      // plan clear. The modal is gated on a pre-flight check:
      // if no downstream plans exist, skip the modal entirely
      // and proceed to Generate. Other doc types keep the
      // generic draft-overwrite confirm via window.confirm.
      if (doc.documentType === 'executive_brief'
        && !opts?._fromBriefRegenConfirm) {
        try {
          const res = await axios.get<{
            exists: boolean
            types: Record<string, boolean>
          }>('/api/v1/story-plans/exists', {
            params: {
              document_types:
                'presentation_deck,'
                + 'analytical_appendix,'
                + 'presentation_script',
            },
          })
          if (res.data.exists) {
            setBriefRegenConfirm({ open: true, pendingDoc: doc })
            return
          }
        } catch {
          // Pre-flight failure -- fall through to Generate.
          // The backend clear is fail-open too: if it can't run
          // the DELETE, the brief still generates. The modal is
          // a courtesy warning, not a hard gate.
        }
      } else if (!opts?._fromRegenConfirm) {
        // June 24 2026 -- deck / appendix / script regens open the
        // RegenConfirmModal (team-replacement language). The brief
        // path above merges this warning with the downstream
        // story-plan clear warning into a single modal. window.
        // confirm is retired so the regen flow is consistent
        // across doc types -- no stacking dialogs.
        setRegenConfirm({ open: true, pendingDoc: doc })
        return
      }
    }
    // June 25 2026 -- client-side blocking gate narrowed to
    // caches_not_warm only. Per the platform's fail-open
    // architecture, audit findings (statistical + methodology
    // unreviewed items) WARN but never BLOCK generation; the
    // team makes the final call. An amber banner above the
    // regen cards surfaces the unacknowledged count and a
    // 'Go to QA Audit' link; the Regenerate button itself
    // never gets the blocking-modal treatment for an audit-
    // only block.
    //
    // is_ready === null means 'unknown' (endpoint failed or
    // not loaded yet); fall through to the POST and let the
    // server gate decide (the server still hard-blocks for
    // cold caches via /export/<doc> 422).
    const readiness = readinessGate
    const cachesCold = (
      (readiness as { caches_warm?: boolean }).caches_warm === false)
    if (cachesCold) {
      const cold = (
        readiness as { cold_caches?: string[] }
      ).cold_caches ?? []
      setBlockingModal({
        open: true,
        blockers: readiness.blockerLabels,
        message: (
          'Caches are not warm — generation would produce '
          + '[DATA PENDING] placeholders. Click Warm Caches '
          + 'before generating.'),
        coldCaches: cold,
      })
      return
    }
    setPostingId(doc.id)
    setErrors((prev) => {
      const next = { ...prev }
      delete next[doc.id]
      return next
    })
    try {
      const res = await axios.post<{ job_id: string; status: string }>(
        doc.endpoint)
      trackJob({
        job_id: res.data.job_id, document_type: doc.documentType,
        status: 'pending', draft_id: null, download_url: null, error: null,
      })
    } catch (err) {
      // Defence in depth — the server gate may fire a 422 with a
      // structured report_not_ready detail even when the frontend
      // thought we were ready (stale store). Render the modal from
      // the response payload in that case so the user still sees
      // the canonical blocker list.
      if (axios.isAxiosError(err) && err.response?.status === 422) {
        const data = err.response.data as {
          detail?: {
            error?: string; message?: string; blockers?: string[]
            cold_caches?: string[]
          }
        }
        // June 25 2026 -- only caches_not_warm still surfaces the
        // blocking modal. report_not_ready (audit findings
        // unacknowledged) is no longer a hard block on the
        // backend (_require_report_ready logs + returns instead
        // of raising), so any legacy server returning it falls
        // through to a regular error message rather than re-
        // raising the blocking modal the user just dismissed.
        if (data?.detail?.error === 'caches_not_warm') {
          setBlockingModal({
            open: true,
            blockers: data.detail.blockers ?? [],
            ...(data.detail.message ? { message: data.detail.message } : {}),
            ...(data.detail.cold_caches
              ? { coldCaches: data.detail.cold_caches } : {}),
          })
          // Refresh the readiness store so the banner matches the
          // server's authoritative state from this 422.
          void readinessGate.reload()
          return
        }
      }
      let msg = 'Generation failed. Please try again.'
      if (axios.isAxiosError(err)) {
        const detail = (err.response?.data as { detail?: string })?.detail
        msg = (typeof detail === 'string' ? detail : '') || err.message
      }
      setErrors((prev) => ({ ...prev, [doc.id]: msg }))
    } finally {
      setPostingId(null)
    }
  }

  const handleDownload = async (job: GenJob) => {
    if (!job.download_url) return
    try {
      const res = await axios.get(job.download_url, { responseType: 'blob' })
      const dispo = String(res.headers['content-disposition'] ?? '')
      const match = /filename="?([^";]+)"?/i.exec(dispo)
      const filename = match?.[1] ?? `forest-capital-${job.document_type}`
      triggerDownload(res.data as Blob, filename)
      dismissJob(job.job_id)
    } catch {
      /* a stale job (expired) — the card will show as such on next poll */
    }
  }

  return (
    <section data-tour="generate-documents">
      <div className="flex items-baseline gap-3 mb-3">
        <h2 className="text-white font-semibold text-sm">Generate Documents</h2>
        <span className="text-2xs text-muted uppercase tracking-wide">
          First-draft deliverables · real platform data
        </span>
      </div>
      {/* Audit-findings warning banner -- June 25 2026. Audit
          findings are advisory per the platform's fail-open
          architecture; surfacing them as a dismissible amber
          banner instead of a hard block lets the team make the
          final call. Hidden when caches are cold (the
          blocking modal handles that surface) or when the
          banner has been dismissed for this session. */}
      <AuditFindingsWarningBanner />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {DOCS.map((doc) => {
          const Icon = doc.icon
          const job = jobForType(doc.documentType)
          const inProgress = job?.status === 'pending'
            || job?.status === 'running' || postingId === doc.id
          const error = errors[doc.id]
          const ts = generatedAt[doc.id]
          // June 25 2026 -- the current draft for this doc_type
          // (regardless of who generated it), pulled from the
          // drafts list endpoint. Drives Open-in-Editor + the new
          // Generated / Updated / Data-hash metadata block. When
          // no current draft exists for the doc_type, the tile
          // omits all metadata (no 'Last generated', no
          // 'Not yet exported') -- only description + Generate.
          const currentDraft: CurrentDraftSummary | null =
            currentDraftByType[doc.documentType] ?? null
          const currentDraftId: number | null =
            currentDraft?.id ?? null
          const hashStale: boolean = !!(
            currentDraft?.data_hash
            && liveDataHash
            && currentDraft.data_hash !== liveDataHash)
          const verificationStatus: ExportVerificationStatus | null = (
            exportVerification
              ? (exportVerification[
                  doc.documentType as
                    'executive_brief' | 'presentation_deck'
                      | 'analytical_appendix'] ?? 'not_exported')
              : null)
          return (
            <div key={doc.id} className="card p-4 flex flex-col gap-3"
                 data-testid={`document-card-${doc.id}`}>
              <div className="flex items-start gap-3">
                <div className="w-9 h-9 rounded flex items-center justify-center
                                shrink-0 bg-electric/10 text-electric">
                  <Icon className="w-4 h-4" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between
                                  gap-2">
                    <h3 className="text-white font-semibold text-sm">
                      {doc.title}
                    </h3>
                    {/* June 23 2026 -- Info icons for all three
                        documented tiles (brief / deck / appendix).
                        The script tile has no info icon yet -- a
                        separate guide is queued for that surface. */}
                    {doc.documentType === 'executive_brief' && (
                      <button
                        type="button"
                        onClick={() => setBriefGuideOpen(true)}
                        data-testid="brief-workflow-info-button"
                        aria-label="How to build the executive brief"
                        className="shrink-0 text-muted hover:text-electric
                                   transition-colors"
                        title="Step-by-step guide">
                        <Info className="w-4 h-4" />
                      </button>
                    )}
                    {doc.documentType === 'presentation_deck' && (
                      <button
                        type="button"
                        onClick={() => setDeckGuideOpen(true)}
                        data-testid="deck-workflow-info-button"
                        aria-label="How to build the final presentation deck"
                        className="shrink-0 text-muted hover:text-electric
                                   transition-colors"
                        title="Step-by-step guide">
                        <Info className="w-4 h-4" />
                      </button>
                    )}
                    {doc.documentType === 'analytical_appendix' && (
                      <button
                        type="button"
                        onClick={() => setAppendixGuideOpen(true)}
                        data-testid="appendix-workflow-info-button"
                        aria-label="How to build the analytical appendix"
                        className="shrink-0 text-muted hover:text-electric
                                   transition-colors"
                        title="Step-by-step guide">
                        <Info className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                  {/* Layer 3b -- export verification pill (green /
                      amber / red / muted-not-exported) sits below
                      the title so it never collides with the Info
                      button on the brief card. */}
                  <div className="mt-1"
                       data-testid={`export-verification-${doc.id}`}>
                    <ExportVerificationPill status={verificationStatus} />
                  </div>
                  <p className="text-muted text-xs mt-1 leading-relaxed">
                    {doc.description}
                  </p>
                  {doc.documentType === 'executive_brief' && (
                    <p className="text-2xs text-muted mt-1.5
                                  leading-relaxed">
                      Review the step-by-step guide{' '}
                      <button
                        type="button"
                        onClick={() => setBriefGuideOpen(true)}
                        data-testid="brief-workflow-helper-link"
                        className="text-electric hover:underline
                                   inline-flex items-center gap-0.5">
                        <Info className="w-3 h-3" />
                      </button>
                      {' '}before generating for the first time.
                    </p>
                  )}
                </div>
              </div>

              {/* June 25 2026 -- tile metadata is now sourced
                  exclusively from the current draft row. When no
                  current draft exists, the tile omits metadata
                  entirely (no 'Last generated', no 'Not yet
                  exported') -- just description + Generate. The
                  previous source was the in-memory generation jobs
                  store which kept stale timestamps after a page
                  reload. */}
              {currentDraft && (
                <TileMetadataBlock
                  draft={currentDraft}
                  liveDataHash={liveDataHash}
                  hashStale={hashStale} />
              )}

              {/* Script-specific gate: when the deck story plan
                  isn't cached the script tile renders a disabled
                  "Generate Deck First" button -- the user spec
                  preserved this UX from the pre-async card so a
                  click can't launch a job that would immediately
                  fail. The other three tiles have no equivalent
                  upstream-dependency gate. */}
              {doc.documentType === 'presentation_script'
                && readinessGate.deck_story_plan_available !== true
                && currentDraftId === null
                && !inProgress
                && job?.status !== 'complete'
                && job?.status !== 'failed' ? (
                <div className="flex flex-col gap-1.5">
                  <button type="button"
                    data-testid={`generate-${doc.id}-disabled`}
                    disabled
                    className="w-full flex items-center
                               justify-center gap-1.5
                               px-3 py-2 rounded text-xs
                               font-semibold bg-navy-700
                               text-muted cursor-not-allowed">
                    Generate Deck First
                  </button>
                  <p className="text-2xs text-muted
                                leading-relaxed">
                    Generate the Presentation Deck to produce
                    the script.
                  </p>
                </div>
              ) : inProgress ? (
                <div className="flex flex-col gap-1.5">
                  <div className="flex items-start gap-1.5 text-xs text-electric">
                    <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0
                                        mt-0.5" />
                    <span>
                      Generating your {doc.title.toLowerCase()}…
                      <span className="block text-2xs text-muted mt-0.5">
                        This takes 30–60 seconds. You can navigate away —
                        we'll notify you when it's ready.
                      </span>
                    </span>
                  </div>
                  {job && (
                    <button type="button"
                      onClick={() => void cancelJob(job.job_id)}
                      className="w-full flex items-center justify-center gap-1.5
                                 px-3 py-1.5 rounded text-2xs border
                                 border-border text-muted hover:text-white">
                      <X className="w-3 h-3" /> Cancel
                    </button>
                  )}
                </div>
              ) : job?.status === 'complete' ? (
                <div className="flex flex-col gap-1.5">
                  {job.draft_id != null && (
                    <button type="button"
                      onClick={() => {
                        dismissJob(job.job_id)
                        navigate(`/editor/${job.draft_id}`)
                      }}
                      className="w-full flex items-center justify-center gap-1.5
                                 px-3 py-2 rounded text-xs font-semibold
                                 bg-electric text-white hover:bg-blue-500">
                      <PenLine className="w-3 h-3" /> Open in Editor
                    </button>
                  )}
                  <button type="button"
                    onClick={() => void handleDownload(job)}
                    className="w-full flex items-center justify-center gap-1.5
                               px-3 py-1.5 rounded text-xs border
                               border-electric/40 text-electric
                               hover:bg-electric/10">
                    <Download className="w-3 h-3" /> Download
                  </button>
                  {/* Bridge #90: a Regenerate button alongside the
                      existing Open / Download buttons so the user
                      can refresh the deck against fresh analytics
                      without leaving the Reports page or
                      hand-deleting the existing draft. The button
                      is gated to team members (same TeamGate the
                      first-time Generate uses); the confirmation
                      prompt lives in handleGenerate so a future
                      caller can opt into it via opts.regenerate. */}
                  <TeamGate block permission="generate_documents"
                    tooltip="Document generation is available to the project team">
                    <button type="button"
                      data-testid={`regenerate-${doc.id}`}
                      onClick={() => void handleGenerate(
                        doc, { regenerate: true })}
                      className="w-full flex items-center justify-center gap-1.5
                                 px-3 py-1.5 rounded text-xs border
                                 border-border text-muted
                                 hover:text-white hover:bg-navy-700">
                      <RefreshCw className="w-3 h-3" /> Regenerate
                    </button>
                  </TeamGate>
                </div>
              ) : job?.status === 'failed' ? (
                // June 25 2026 -- failed-state demotion. When a
                // current draft already exists for this doc type
                // (currentDraftId !== null) the team has a usable
                // version sitting in editor_drafts; the failed
                // job doesn't block them from accessing it. Show
                // Open in Editor as the primary action, demote
                // the failed message to an amber WARNING chip,
                // and keep Try Again as the tertiary option.
                // Without a current draft we keep the legacy
                // red-error + Try Again primary chrome -- the
                // user has nothing else to open.
                currentDraftId !== null ? (
                  <div className="flex flex-col gap-1.5">
                    <button type="button"
                      onClick={() => navigate(
                        `/editor/${currentDraftId}`)}
                      data-testid={
                        `open-existing-draft-${doc.id}`}
                      className="w-full flex items-center
                                  justify-center gap-1.5
                                  px-3 py-2 rounded text-xs
                                  font-semibold bg-electric
                                  text-white hover:bg-blue-500">
                      <PenLine className="w-3 h-3" />
                      Open in Editor
                    </button>
                    <div
                      data-testid={`failed-warning-${doc.id}`}
                      className="flex items-start gap-1.5 px-2
                                 py-1.5 rounded text-2xs border
                                 border-warning/30 bg-warning/5
                                 text-warning">
                      <AlertCircle className="w-3 h-3 shrink-0
                                              mt-0.5" />
                      <span>
                        Latest generation attempt failed:{' '}
                        {job.error || 'unknown error'}. The
                        existing draft above is still
                        available.
                      </span>
                    </div>
                    <TeamGate block permission="generate_documents"
                      tooltip="Document generation is available to the project team">
                      <button type="button"
                        onClick={() => void handleGenerate(doc)}
                        className="w-full flex items-center
                                   justify-center gap-1.5
                                   px-3 py-1.5 rounded text-xs
                                   border border-border
                                   text-muted hover:text-white
                                   hover:bg-navy-700">
                        Try Again
                      </button>
                    </TeamGate>
                  </div>
                ) : (
                  <div className="flex flex-col gap-1.5">
                    <div className="flex items-start gap-1.5 px-2 py-1.5 rounded
                                    text-2xs border border-danger/30
                                    bg-danger/5 text-danger">
                      <AlertCircle className="w-3 h-3 shrink-0 mt-0.5" />
                      <span>{job.error || 'Generation failed.'}</span>
                    </div>
                    <TeamGate block permission="generate_documents"
                      tooltip="Document generation is available to the project team">
                      <button type="button"
                        onClick={() => void handleGenerate(doc)}
                        className="w-full flex items-center justify-center gap-1.5
                                   px-3 py-2 rounded text-xs font-semibold
                                   bg-electric text-white hover:bg-blue-500">
                        Try Again
                      </button>
                    </TeamGate>
                  </div>
                )
              ) : (
                <div className="flex flex-col gap-1.5">
                  {job?.status === 'cancelled' && (
                    <div className="text-2xs text-muted">
                      Generation cancelled.
                    </div>
                  )}
                  {/* June 25 2026 -- when another team member
                      generated this document and the current
                      session has no tracked job for it, surface
                      Open in Editor + Regenerate from the
                      current-drafts fetch above. The job-only
                      gate used to leave Bob's brief invisible
                      to Mike on his Reports page. */}
                  {currentDraftId !== null
                    && job?.status !== 'cancelled' && (
                      <button type="button"
                        onClick={() => navigate(
                          `/editor/${currentDraftId}`)}
                        data-testid={
                          `open-existing-draft-${doc.id}`}
                        className="w-full flex items-center
                                    justify-center gap-1.5
                                    px-3 py-2 rounded text-xs
                                    font-semibold bg-electric
                                    text-white hover:bg-blue-500">
                        <PenLine className="w-3 h-3" />
                        Open in Editor
                      </button>
                    )}
                  <TeamGate block permission="generate_documents"
                    tooltip="Document generation is available to the project team">
                    <button type="button"
                      onClick={() => void handleGenerate(
                        doc,
                        currentDraftId !== null
                          ? { regenerate: true } : undefined)}
                      className={
                        currentDraftId !== null
                          ? 'w-full flex items-center '
                            + 'justify-center gap-1.5 px-3 py-1.5 '
                            + 'rounded text-xs border '
                            + 'border-border text-muted '
                            + 'hover:text-white hover:bg-navy-700'
                          : 'w-full flex items-center '
                            + 'justify-center gap-1.5 px-3 py-2 '
                            + 'rounded text-xs font-semibold '
                            + 'transition-colors bg-electric '
                            + 'text-white hover:bg-blue-500'}>
                      {currentDraftId !== null
                        ? <RefreshCw className="w-3 h-3" />
                        : <Download className="w-3 h-3" />}
                      {currentDraftId !== null
                        ? 'Regenerate'
                        : (ts ? 'Regenerate' : 'Generate')}
                    </button>
                  </TeamGate>
                </div>
              )}

              {error && (
                <div className="flex items-start gap-1.5 px-2 py-1.5 rounded
                                text-2xs border border-danger/30 bg-danger/5
                                text-danger">
                  <AlertCircle className="w-3 h-3 shrink-0 mt-0.5" />
                  <span className="flex-1">
                    {error}{' '}
                    <button type="button"
                      onClick={() => void handleGenerate(doc)}
                      className="underline hover:no-underline font-semibold">
                      Retry
                    </button>
                  </span>
                </div>
              )}

              {/* Slide Guidance (June 23 2026) -- deck-specific
                  configuration artifact. Moved from a sibling on
                  Reports.tsx into this tile because it is
                  conceptually paired with deck generation: the
                  uploaded guidance overlays SLIDE_SPECIFICATIONS
                  defaults at deck Pass-1 time. Upload is disabled
                  while a deck job is in progress to prevent a race
                  where guidance lands too late to affect the
                  in-flight pipeline. Download + Reset stay enabled
                  (read-only / state cleanup -- no race). The
                  TeamGate wrapper mirrors the Generate button --
                  a viewer should not see upload controls. */}
              {doc.documentType === 'presentation_deck' && (
                <TeamGate
                  block
                  permission="generate_documents"
                  tooltip="Slide guidance upload is available to the project team">
                  <SlideGuidancePanel
                    uploadDisabled={inProgress}
                    uploadDisabledTooltip="Deck generation in progress" />
                </TeamGate>
              )}
            </div>
          )
        })}
        {/* Presentation Script joined the async job pattern on
            2026-06-25 -- it now renders inside DOCS.map alongside
            the brief / appendix / deck and inherits the standard
            inProgress / complete / failed / idle chrome. The
            former standalone PresentationScriptCard is retired. */}
      </div>
      <p className="text-2xs text-muted mt-2">
        Generated documents are first drafts for review — every file carries
        an <strong className="text-warning">AI DRAFT — REQUIRES HUMAN REVIEW</strong>{' '}
        banner. Verify every figure before submitting.
      </p>
      <ReportBlockingModal
        open={blockingModal.open}
        onClose={() => setBlockingModal({ open: false, blockers: [] })}
        blockers={blockingModal.blockers}
        {...(blockingModal.message ? { message: blockingModal.message } : {})}
        {...(blockingModal.coldCaches
          ? { coldCaches: blockingModal.coldCaches } : {})}
      />
      <BriefWorkflowModal
        open={briefGuideOpen}
        onClose={() => setBriefGuideOpen(false)} />
      <DeckWorkflowModal
        open={deckGuideOpen}
        onClose={() => setDeckGuideOpen(false)} />
      <AppendixWorkflowModal
        open={appendixGuideOpen}
        onClose={() => setAppendixGuideOpen(false)} />
      <BriefRegenConfirmModal
        open={briefRegenConfirm.open}
        onCancel={() => setBriefRegenConfirm(
          { open: false, pendingDoc: null })}
        onConfirm={() => {
          const pending = briefRegenConfirm.pendingDoc
          setBriefRegenConfirm({ open: false, pendingDoc: null })
          if (pending) {
            void handleGenerate(pending,
              { regenerate: true, _fromBriefRegenConfirm: true })
          }
        }} />
      {/* June 24 2026 -- deck / appendix / script regens. The
          brief uses the dedicated BriefRegenConfirmModal above. */}
      <RegenConfirmModal
        open={regenConfirm.open}
        documentName={
          regenConfirm.pendingDoc?.title || 'Document'}
        sourceDocumentType={
          regenConfirm.pendingDoc?.documentType ?? undefined}
        onCancel={() => setRegenConfirm(
          { open: false, pendingDoc: null })}
        onConfirm={() => {
          const pending = regenConfirm.pendingDoc
          setRegenConfirm({ open: false, pendingDoc: null })
          if (pending) {
            void handleGenerate(pending,
              { regenerate: true, _fromRegenConfirm: true })
          }
        }} />
    </section>
  )
}
