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
    jobs.set(jobId, res.data)
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

/** Registers a job and begins polling it (if not already terminal). */
export function trackJob(job: GenJob): void {
  jobs.set(job.job_id, job)
  dismissed.delete(job.job_id)
  emit()
  if (!isTerminal(job.status)) schedulePoll(job.job_id)
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

/** The most recently tracked job for a document type, or undefined. */
export function jobForType(documentType: string): GenJob | undefined {
  let found: GenJob | undefined
  for (const j of snapshot) {
    if (j.document_type === documentType) found = j
  }
  return found
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
