/**
 * generation-jobs-housekeeping.test.ts -- June 27 2026.
 *
 * BUG 1 pin: loadExistingJobs MUST dismiss stale FAILED jobs for a
 * doc_type that ALSO has a non-terminal (pending/running) job in
 * the hydrated set. Without this, the server's /api/v1/jobs list
 * (last 10 jobs in any state) re-introduces old failed rows the
 * panel previously dismissed on a retry. _autoDismissStaleFailed
 * only fires at trackJob-time, so loadExistingJobs needs to
 * mirror the housekeeping.
 *
 * Also exercises jobForType's existing recency-sort which keeps
 * the new running job winning even if loadExistingJobs reordered
 * insertion.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import axios from 'axios'

import {
  loadExistingJobs, jobForType, __resetGenerationJobs,
  trackJob, isDismissed,
} from '../lib/generationJobs'

vi.mock('axios')


describe('generationJobs -- BUG 1 loadExistingJobs stale-failed housekeeping', () => {
  beforeEach(() => {
    __resetGenerationJobs()
    vi.mocked(axios.get).mockReset()
  })
  afterEach(() => {
    __resetGenerationJobs()
  })

  it('dismisses a stale FAILED job when a newer running job exists for the same doc_type', async () => {
    vi.mocked(axios.get).mockResolvedValueOnce({
      data: {
        jobs: [
          {
            job_id: 'old-failed',
            document_type: 'analytical_appendix',
            status: 'failed',
            draft_id: null,
            download_url: null,
            error: 'Generation job is no longer available.',
            created_at: '2026-06-27T14:00:00Z',
          },
          {
            job_id: 'new-running',
            document_type: 'analytical_appendix',
            status: 'running',
            draft_id: null,
            download_url: null,
            error: null,
            created_at: '2026-06-27T15:00:00Z',
          },
        ],
      },
    })
    await loadExistingJobs()
    // The newer running job wins the per-doc_type lookup; the old
    // failed job is no longer in the snapshot (was hard-removed by
    // _autoDismissStaleFailed via the housekeeping pass).
    const current = jobForType('analytical_appendix')
    expect(current?.job_id).toBe('new-running')
    expect(current?.status).toBe('running')
    // Old failed job is dismissed.
    expect(isDismissed('old-failed')).toBe(true)
  })

  it('preserves a stale FAILED job when no non-terminal job for that doc_type', async () => {
    // Without a running counterpart, the failed job is the only
    // signal for the user -- housekeeping must NOT dismiss it
    // (the user would lose the Try Again UX).
    vi.mocked(axios.get).mockResolvedValueOnce({
      data: {
        jobs: [
          {
            job_id: 'only-failed',
            document_type: 'analytical_appendix',
            status: 'failed',
            draft_id: null,
            download_url: null,
            error: 'Generation job is no longer available.',
            created_at: '2026-06-27T14:00:00Z',
          },
        ],
      },
    })
    await loadExistingJobs()
    const current = jobForType('analytical_appendix')
    expect(current?.job_id).toBe('only-failed')
    expect(current?.status).toBe('failed')
    expect(isDismissed('only-failed')).toBe(false)
  })

  it('housekeeping is per-doc_type -- a failed deck job is unaffected by a running brief job', async () => {
    vi.mocked(axios.get).mockResolvedValueOnce({
      data: {
        jobs: [
          {
            job_id: 'deck-failed',
            document_type: 'presentation_deck',
            status: 'failed',
            draft_id: null,
            download_url: null,
            error: 'Generation job is no longer available.',
            created_at: '2026-06-27T14:00:00Z',
          },
          {
            job_id: 'brief-running',
            document_type: 'executive_brief',
            status: 'running',
            draft_id: null,
            download_url: null,
            error: null,
            created_at: '2026-06-27T15:00:00Z',
          },
        ],
      },
    })
    await loadExistingJobs()
    expect(jobForType('presentation_deck')?.status).toBe('failed')
    expect(jobForType('executive_brief')?.status).toBe('running')
    expect(isDismissed('deck-failed')).toBe(false)
  })

  it('trackJob still triggers per-call _autoDismissStaleFailed', () => {
    // Manually seed an old failed brief job, then trackJob a new
    // running brief job -- old failed should be removed
    // immediately (pre-existing behaviour, pinned here so future
    // refactors keep the contract).
    trackJob({
      job_id: 'brief-failed',
      document_type: 'executive_brief',
      status: 'failed',
      draft_id: null,
      download_url: null,
      error: 'old failure',
      created_at: '2026-06-27T13:00:00Z',
    })
    trackJob({
      job_id: 'brief-running',
      document_type: 'executive_brief',
      status: 'pending',
      draft_id: null,
      download_url: null,
      error: null,
      created_at: '2026-06-27T15:00:00Z',
    })
    expect(jobForType('executive_brief')?.job_id).toBe('brief-running')
    expect(isDismissed('brief-failed')).toBe(true)
  })
})
