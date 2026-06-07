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
  CheckCircle, PenLine, RefreshCw, X,
} from 'lucide-react'
import TeamGate from './TeamGate'
import {
  useGenerationJobs, trackJob, cancelJob, dismissJob, loadExistingJobs,
  jobForType, type GenJob,
} from '../lib/generationJobs'
import {
  ReportBlockingModal, useReportReadinessGate,
} from './ReportReadinessIndicator'

interface DocSpec {
  id: string
  documentType: string
  title: string
  description: string
  endpoint: string
  icon: typeof FileText
}

const DOCS: DocSpec[] = [
  {
    id: 'midpoint',
    documentType: 'midpoint_paper',
    title: 'Midpoint Submission Paper',
    description:
      'Three-page academic paper formatted to midpoint requirements. '
      + 'Double-spaced, 12pt, ready to refine in the editor.',
    endpoint: '/api/v1/export/midpoint-paper',
    icon: FileText,
  },
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
      'Six-slide narrative deck (regime thesis, 2022 correlation '
      + 'break, OOS evidence, play-by-play, live blend, recommendation) '
      + 'with real data charts in light mode, ready to refine in the '
      + 'editor.',
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

export default function DocumentGenerationPanel() {
  const navigate = useNavigate()
  useGenerationJobs()   // re-render as tracked job states change
  // The POST that creates a job is brief; this guards the button until
  // the job appears in the store.
  const [postingId, setPostingId] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [generatedAt, setGeneratedAt] =
    useState<Record<string, string>>(readGeneratedAt)
  // Job ids whose completion has already been written to "last generated".
  const recorded = useRef<Set<string>>(new Set())
  // Workstream C report gate. The hook loads /api/v1/report/readiness
  // on mount; clicking Generate while is_ready is false opens the
  // blocking modal instead of firing the POST. The server-side gate
  // (_require_report_ready) is the source of truth — when it fires
  // a 422 the modal also opens from the response detail (defence in
  // depth against a stale `is_ready` value).
  const readinessGate = useReportReadinessGate()
  const [blockingModal, setBlockingModal] = useState<{
    open: boolean; blockers: string[]; message?: string
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
          }
        }
        if (data?.detail?.error === 'report_not_ready') {
          setBlockingModal({
            open: true,
            blockers: data.detail.blockers ?? [],
            ...(data.detail.message ? { message: data.detail.message } : {}),
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
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {DOCS.map((doc) => {
          const Icon = doc.icon
          const job = jobForType(doc.documentType)
          const inProgress = job?.status === 'pending'
            || job?.status === 'running' || postingId === doc.id
          const error = errors[doc.id]
          const ts = generatedAt[doc.id]
          return (
            <div key={doc.id} className="card p-4 flex flex-col gap-3">
              <div className="flex items-start gap-3">
                <div className="w-9 h-9 rounded flex items-center justify-center
                                shrink-0 bg-electric/10 text-electric">
                  <Icon className="w-4 h-4" />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-white font-semibold text-sm">
                    {doc.title}
                  </h3>
                  <p className="text-muted text-xs mt-1 leading-relaxed">
                    {doc.description}
                  </p>
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
      />
    </section>
  )
}
