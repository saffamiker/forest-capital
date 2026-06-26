/**
 * generationJobs — a module-level store + poller for async document
 * generation jobs.
 *
 * Document generation (midpoint paper, executive brief, presentation
 * deck) runs as a backend job: the POST returns a job_id, and the
 * client polls GET /api/v1/jobs/{id} every 3 seconds until the job is
 * terminal. The store lives at module scope — NOT in component state —
 * so polling continues when the user navigates away from the Reports
 * page; a global toast then announces completion.
 */
import { useSyncExternalStore } from 'react'
import axios from 'axios'

const POLL_MS = 3000

export type JobStatus =
  | 'pending' | 'running' | 'complete' | 'failed' | 'cancelled'

export interface GenJob {
  job_id: string
  document_type: string
  status: JobStatus
  draft_id: number | null
  download_url: string | null
  error: string | null
  // June 25 2026 -- ISO timestamp from the server (tools/
  // generation_jobs.create_job emits one per job; the GET
  // endpoint serialises it). Used by jobForType to sort by
  // recency so a NEWER complete job wins over an older failed
  // job for the same document_type. Optional on the type so a
  // legacy snapshot that pre-dates the field still parses.
  created_at?: string
}

const jobs = new Map<string, GenJob>()
const timers = new Map<string, ReturnType<typeof setTimeout>>()
// Terminal jobs the user has acted on / dismissed — the toast hides these.
const dismissed = new Set<string>()
const listeners = new Set<() => void>()

let snapshot: GenJob[] = []

function isTerminal(status: JobStatus): boolean {
  return status === 'complete' || status === 'failed'
    || status === 'cancelled'
}

function emit(): void {
  snapshot = [...jobs.values()]
  listeners.forEach((l) => l())
}

function schedulePoll(jobId: string): void {
  if (timers.has(jobId)) return
  timers.set(jobId, setTimeout(() => void poll(jobId), POLL_MS))
}

async function poll(jobId: string): Promise<void> {
  timers.delete(jobId)
  try {
    const res = await axios.get<GenJob>(`/api/v1/jobs/${jobId}`)
    const prev = jobs.get(jobId)
    jobs.set(jobId, res.data)
    // June 25 2026 -- when a poll observes the complete transition
    // (this job just succeeded), auto-dismiss any older FAILED
    // jobs for the same document_type so the failed-state branch
    // doesn't keep winning over the freshly-completed retry.
    if (res.data.status === 'complete'
        && prev?.status !== 'complete') {
      _autoDismissStaleFailed(res.data.document_type, jobId)
    }
    emit()
    if (!isTerminal(res.data.status)) schedulePoll(jobId)
  } catch {
    // The job is gone (expired / server restart) or the network failed.
    const cur = jobs.get(jobId)
    if (cur && !isTerminal(cur.status)) {
      jobs.set(jobId, {
        ...cur, status: 'failed',
        error: 'Generation job is no longer available.',
      })
      emit()
    }
  }
}

/** Registers a job and begins polling it (if not already terminal).
 *  June 25 2026 -- a newly-tracked NON-terminal job also auto-
 *  dismisses any STALE FAILED job for the same document_type. Without
 *  this, a one-off failure from May sits in the store and the failed-
 *  state branch wins over the new running/complete job until the user
 *  manually clears it. Auto-dismiss here covers the trackJob entry
 *  path; pollers landing a 'complete' transition also auto-dismiss
 *  via the same _autoDismissStaleFailed helper. */
export function trackJob(job: GenJob): void {
  jobs.set(job.job_id, job)
  dismissed.delete(job.job_id)
  if (!isTerminal(job.status)) {
    _autoDismissStaleFailed(job.document_type, job.job_id)
  }
  emit()
  if (!isTerminal(job.status)) schedulePoll(job.job_id)
}


/** June 25 2026 -- dismiss every FAILED job for a doc_type EXCEPT
 *  the keepJobId. Called when a new job for the same doc_type is
 *  tracked (the user clicked Try Again / Regenerate) OR when a poll
 *  observes a complete transition (the retry succeeded). Keeps the
 *  Zustand-style store clean so jobForType + the failed-state branch
 *  read the freshest signal, not the stalest. */
function _autoDismissStaleFailed(
  documentType: string, keepJobId: string,
): void {
  for (const j of jobs.values()) {
    if (j.document_type !== documentType) continue
    if (j.job_id === keepJobId) continue
    if (j.status !== 'failed') continue
    dismissed.add(j.job_id)
    // Hide from snapshot iteration by removing the entry. Once a
    // failed job is auto-dismissed we have no reason to keep it
    // -- the dismissed set is for "user acted on it"; this is the
    // store-housekeeping equivalent. Removing prevents jobForType
    // from finding it at all (defence in depth alongside the
    // recency sort below).
    jobs.delete(j.job_id)
    const t = timers.get(j.job_id)
    if (t) { clearTimeout(t); timers.delete(j.job_id) }
  }
}

/** Cancels a job — DELETEs it server-side and stops polling. */
export async function cancelJob(jobId: string): Promise<void> {
  const t = timers.get(jobId)
  if (t) { clearTimeout(t); timers.delete(jobId) }
  try {
    await axios.delete(`/api/v1/jobs/${jobId}`)
  } catch { /* the job may already be gone — fall through */ }
  const cur = jobs.get(jobId)
  if (cur) jobs.set(jobId, { ...cur, status: 'cancelled' })
  dismissed.add(jobId)
  emit()
}

/** Marks a terminal job as acted-on — the completion toast hides it. */
export function dismissJob(jobId: string): void {
  dismissed.add(jobId)
  emit()
}

export function isDismissed(jobId: string): boolean {
  return dismissed.has(jobId)
}

/** Fetches the user's recent jobs on page load — resumes polling any
 *  still running, and keeps recently completed ones visible. */
export async function loadExistingJobs(): Promise<void> {
  try {
    const res = await axios.get<{ jobs: GenJob[] }>('/api/v1/jobs')
    for (const j of res.data.jobs ?? []) {
      if (jobs.has(j.job_id)) continue
      jobs.set(j.job_id, j)
      if (!isTerminal(j.status)) schedulePoll(j.job_id)
    }
    emit()
  } catch { /* best-effort — a fresh panel simply shows no history */ }
}

/** The MOST RECENT job for a document type, or undefined.
 *  June 25 2026 -- sorted by created_at descending. The previous
 *  implementation relied on Map insertion order ('last write wins'
 *  in iteration), which let a historical FAILED job win over a newer
 *  COMPLETED retry for the same doc_type when loadExistingJobs
 *  hydrated the older job last. Sorting by created_at fixes the
 *  ordering deterministically; jobs without created_at (legacy
 *  snapshot) fall to the END so they don't shadow a fresh job. */
export function jobForType(documentType: string): GenJob | undefined {
  const candidates = snapshot.filter(
    (j) => j.document_type === documentType)
  if (candidates.length === 0) return undefined
  candidates.sort((a, b) => {
    const ta = a.created_at ?? ''
    const tb = b.created_at ?? ''
    if (ta === tb) return 0
    return ta > tb ? -1 : 1   // descending: newest first
  })
  return candidates[0]
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

/** React hook — the current list of tracked jobs. */
export function useGenerationJobs(): GenJob[] {
  return useSyncExternalStore(subscribe, () => snapshot, () => snapshot)
}

/** Test-only — clears every tracked job, timer and dismissal. */
export function __resetGenerationJobs(): void {
  for (const t of timers.values()) clearTimeout(t)
  timers.clear()
  jobs.clear()
  dismissed.clear()
  emit()
}
