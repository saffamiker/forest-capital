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
import TeamGate from './TeamGate'
import {
  useGenerationJobs, trackJob, cancelJob, dismissJob, loadExistingJobs,
  jobForType, type GenJob,
} from '../lib/generationJobs'
import {
  ReportBlockingModal, useReportReadinessGate,
} from './ReportReadinessIndicator'
import { BriefWorkflowModal } from './BriefWorkflowModal'
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


/**
 * PresentationScriptCard — the only card on this panel that doesn't
 * use the async job pattern. The script is a pure cache read of
 * story_plans (Pass 2 full_script + Pass 3 anticipated_questions)
 * so the backend renders synchronously in ~200ms and returns the
 * .docx directly as a blob -- no job_id, no polling.
 *
 * The button state mirrors deck_story_plan_available from
 * /api/v1/report/readiness: true -> "Download Script" enabled;
 * false / null -> "Generate Deck First" disabled with a helper line.
 */
function PresentationScriptCard({
  deckPlanAvailable,
}: { deckPlanAvailable: boolean | null }) {
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const available = deckPlanAvailable === true

  const handleDownload = async () => {
    if (!available || downloading) return
    setDownloading(true)
    setError(null)
    try {
      const res = await axios.post(
        '/api/v1/export/presentation-script',
        {},
        { responseType: 'blob' })
      const dispo = String(res.headers['content-disposition'] ?? '')
      const filenameMatch = /filename="?([^";]+)"?/i.exec(dispo)
      const filename = filenameMatch?.[1]
        ?? 'forest-capital-presentation-script.docx'
      const contentType = String(
        res.headers['content-type'] ?? 'application/octet-stream')
      triggerDownload(new Blob([res.data], { type: contentType }), filename)
    } catch (err) {
      // The endpoint returns 404 when the deck story plan hasn't been
      // cached yet (defence in depth -- the readiness flag should
      // already gate the button, but a stale flag can slip through).
      let msg = 'Download failed.'
      if (axios.isAxiosError(err)) {
        const status = err.response?.status
        if (status === 404) {
          msg = 'Deck story plan not cached yet. '
            + 'Generate the Presentation Deck first.'
        } else {
          const detail = err.response?.data?.detail
          msg = typeof detail === 'string' ? detail : err.message
        }
      }
      setError(msg)
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="card p-4 flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded flex items-center justify-center
                        shrink-0 bg-electric/10 text-electric">
          <Mic className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-white font-semibold text-sm">
            Presentation Script
          </h3>
          <p className="text-muted text-xs mt-1 leading-relaxed">
            Word-for-word presenter script for the 18-20 minute panel
            presentation, with Grok-generated Q&A preparation. Reads
            from the cached deck story plan -- generate the
            Presentation Deck first.
          </p>
        </div>
      </div>

      <div className="text-2xs text-muted">
        Deadline: July 3 (presentation date)
      </div>

      {available ? (
        <TeamGate block permission="generate_documents"
          tooltip="Document generation is available to the project team">
          <button type="button"
            data-testid="download-presentation-script"
            onClick={() => void handleDownload()}
            disabled={downloading}
            className="w-full flex items-center justify-center gap-1.5
                       px-3 py-2 rounded text-xs font-semibold
                       transition-colors bg-electric text-white
                       hover:bg-blue-500 disabled:opacity-60">
            {downloading
              ? <><Loader2 className="w-3 h-3 animate-spin" />
                  Building script…</>
              : <><Download className="w-3 h-3" /> Download Script</>}
          </button>
        </TeamGate>
      ) : (
        <div className="flex flex-col gap-1.5">
          <button type="button"
            data-testid="download-presentation-script"
            disabled
            className="w-full flex items-center justify-center gap-1.5
                       px-3 py-2 rounded text-xs font-semibold
                       bg-navy-700 text-muted cursor-not-allowed">
            Generate Deck First
          </button>
          <p className="text-2xs text-muted leading-relaxed">
            Generate the Presentation Deck to produce the script.
          </p>
        </div>
      )}

      {error && (
        <div className="flex items-start gap-1.5 px-2 py-1.5 rounded
                        text-2xs border border-danger/30 bg-danger/5
                        text-danger">
          <AlertCircle className="w-3 h-3 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}
    </div>
  )
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

  // On mount, resume polling any in-progress jobs and surface recently
  // completed ones (e.g. generation finished while the user was away).
  useEffect(() => { void loadExistingJobs() }, [])

  // Stamp "last generated" the first time a job for a type completes.
  const jobs = useGenerationJobs()
  useEffect(() => {
    for (const job of jobs) {
      if (job.status !== 'complete' || recorded.current.has(job.job_id)) {
        continue
      }
      recorded.current.add(job.job_id)
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
  }, [jobs])

  const handleGenerate = async (doc: DocSpec, opts?: { regenerate?: boolean }) => {
    // Bridge #90: when the user clicks Regenerate on a card that
    // already has a complete generation, warn them the existing
    // editor draft will be superseded -- the backend creates a NEW
    // editor_drafts row and flips the previous one's is_current to
    // false, so any unsaved edits in the editor are no longer the
    // canonical "current" draft. The frontend has no explicit
    // dirty-flag for the editor, so always-confirm on Regenerate
    // is the safe default.
    if (opts?.regenerate) {
      const ok = window.confirm(
        `Regenerating will create a new draft and overwrite the `
        + `current "${doc.title}" version. Any unsaved edits in the `
        + `editor will no longer be the canonical draft. Continue?`)
      if (!ok) return
    }
    // Client-side gate — open the blocking modal without firing the
    // POST when readiness is known-blocked. is_ready === null means
    // "unknown" (endpoint failed or not loaded yet); fall through to
    // the POST and let the server gate decide.
    if (readinessGate.is_ready === false) {
      setBlockingModal({
        open: true, blockers: readinessGate.blockerLabels,
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
        // Bridge #91 — the gate now returns a distinct error type
        // for cold caches so the modal can render Warm Caches.
        if (data?.detail?.error === 'report_not_ready'
            || data?.detail?.error === 'caches_not_warm') {
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
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {DOCS.map((doc) => {
          const Icon = doc.icon
          const job = jobForType(doc.documentType)
          const inProgress = job?.status === 'pending'
            || job?.status === 'running' || postingId === doc.id
          const error = errors[doc.id]
          const ts = generatedAt[doc.id]
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

              <div className="text-2xs text-muted flex items-center gap-1">
                {ts ? (
                  <><CheckCircle className="w-3 h-3 text-success" />
                    Last generated: {new Date(ts).toLocaleString()}</>
                ) : <>Last generated: Never</>}
              </div>

              {inProgress ? (
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
              ) : (
                <div className="flex flex-col gap-1.5">
                  {job?.status === 'cancelled' && (
                    <div className="text-2xs text-muted">
                      Generation cancelled.
                    </div>
                  )}
                  <TeamGate block permission="generate_documents"
                    tooltip="Document generation is available to the project team">
                    <button type="button"
                      onClick={() => void handleGenerate(doc)}
                      className="w-full flex items-center justify-center gap-1.5
                                 px-3 py-2 rounded text-xs font-semibold
                                 transition-colors bg-electric text-white
                                 hover:bg-blue-500">
                      <Download className="w-3 h-3" />
                      {ts ? 'Regenerate' : 'Generate'}
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
            </div>
          )
        })}
        {/* Presentation Script -- pure cache read of the deck story
            plan's full_script + anticipated_questions. Sits in the
            same grid as the three generation cards because it's
            adjacent in the user's mental model (script for the deck),
            but uses its own direct-download flow because there is
            no LLM call to gate behind a job. */}
        <PresentationScriptCard
          deckPlanAvailable={readinessGate.deck_story_plan_available} />
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
    </section>
  )
}
